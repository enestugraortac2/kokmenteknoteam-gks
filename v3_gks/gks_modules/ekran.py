#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — LCD Ekran Modülü (2.4" ILI9341)
Donanım: DC=GPIO25, RST=GPIO24, CS=None, SPI (SCK,MOSI,MISO)
Çözünürlük: 320x240 (landscape, rotation=90)
"""

import time
import logging
import threading
import traceback

log = logging.getLogger("Ekran")

SCREEN_W = 320
SCREEN_H = 240
REFRESH_INTERVAL = 1.0


class Colors:
    BG_DARK = (10, 12, 28)
    BG_CARD = (18, 24, 48)
    BG_HEADER = (14, 18, 38)
    TEAL = (0, 210, 200)
    TEAL_DIM = (0, 140, 135)
    CYAN = (0, 195, 255)
    WHITE = (240, 245, 255)
    GRAY = (130, 140, 160)
    DARK_GRAY = (60, 70, 90)
    GREEN = (0, 230, 100)
    YELLOW = (255, 200, 0)
    ORANGE = (255, 140, 0)
    RED = (255, 50, 50)
    SCORE_EYE = (100, 180, 255)
    SCORE_MOTOR = (150, 130, 255)
    SCORE_VERBAL = (255, 170, 100)


def _severity_color(total):
    if total >= 13: return Colors.GREEN
    if total >= 9:  return Colors.YELLOW
    if total >= 6:  return Colors.ORANGE
    return Colors.RED

def _severity_text(total):
    if total >= 13: return "NORMAL"
    if total >= 9:  return "ORTA"
    if total >= 6:  return "AGIR"
    return "KRITIK"

def _score_color(score, mx):
    r = score / mx if mx > 0 else 0
    if r >= 0.75: return Colors.GREEN
    if r >= 0.5:  return Colors.YELLOW
    if r >= 0.25: return Colors.ORANGE
    return Colors.RED

_STATE_MAP = {
    "BASLANGIC":    "Baslatiliyor",
    "PASIF_GOZLEM": "Pasif Gozlem",
    "SOZEL_UYARAN": "Sozel Uyaran",
    "MOTOR_KOMUT":  "Motor Komut",
    "AGRILI_UYARAN":"Agrili Uyaran",
    "FINAL_RAPOR":  "Rapor",
    "TAMAMLANDI":   "Tamamlandi",
    "BEKLENIYOR":   "Bekleniyor",
}

def _draw_rounded_rect(draw, xy, radius, fill, outline=None):
    x0, y0, x1, y1 = xy
    r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.pieslice([x0, y0, x0 + 2*r, y0 + 2*r], 180, 270, fill=fill)
    draw.pieslice([x1 - 2*r, y0, x1, y0 + 2*r], 270, 360, fill=fill)
    draw.pieslice([x0, y1 - 2*r, x0 + 2*r, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - 2*r, y1 - 2*r, x1, y1], 0, 90, fill=fill)

def _draw_bar(draw, x, y, w, h, val, mx, color, bg):
    draw.rectangle([x, y, x + w, y + h], fill=bg)
    if mx > 0:
        fw = int((val / mx) * w)
        if fw > 0:
            draw.rectangle([x, y, x + fw, y + h], fill=color)


def render_frame(data: dict):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), Colors.BG_DARK)
    draw = ImageDraw.Draw(img)

    # Font
    try:
        fl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        fm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        fx = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
        fxl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    except Exception:
        fl = fm = fs = fx = fxl = ImageFont.load_default()

    goz   = int(data.get("goz", 0))
    motor = int(data.get("motor", 0))
    sozel = int(data.get("sozel", 0))
    toplam = goz + motor + sozel
    durum = data.get("durum", "BASLANGIC")
    ear   = data.get("ear", 0.0)
    goz_a = data.get("goz_acik", False)
    done  = data.get("tamamlandi", False)
    bpm   = int(data.get("nabiz_bpm", 0))

    # --- HEADER ---
    draw.rectangle([0, 0, SCREEN_W, 32], fill=Colors.BG_HEADER)
    draw.ellipse([8, 9, 22, 23], fill=Colors.TEAL)
    draw.text((26, 6), "NeuroSense GKS", fill=Colors.TEAL, font=fm)
    
    # Nabız Göstergesi (Daha belirgin)
    if bpm > 0:
        blink = int(time.time() * 2) % 2 == 0
        hb_color = Colors.RED if blink else Colors.DARK_GRAY
        _draw_rounded_rect(draw, (200, 4, 280, 28), 6, Colors.BG_CARD)
        draw.text((205, 10), "x", fill=hb_color, font=fx) # Kalp ritmi atış efekti sembolü
        draw.text((215, 8), f"{bpm} BPM", fill=Colors.WHITE, font=fm)
    else:
        _draw_rounded_rect(draw, (200, 4, 280, 28), 6, Colors.BG_CARD)
        draw.text((212, 10), "HR: --", fill=Colors.GRAY, font=fs)

    led_c = Colors.GREEN if goz_a else Colors.RED
    draw.ellipse([290, 11, 305, 22], fill=led_c)
    draw.line([0, 32, SCREEN_W, 32], fill=Colors.TEAL_DIM, width=1)

    # --- SKOR KARTLARI ---
    cw, cg, cx0 = 100, 5, 5
    for i, (lbl, sc, mx, _) in enumerate([
        ("GOZ", goz, 4, Colors.SCORE_EYE),
        ("MOTOR", motor, 6, Colors.SCORE_MOTOR),
        ("SOZEL", sozel, 5, Colors.SCORE_VERBAL),
    ]):
        cx = cx0 + i * (cw + cg)
        cy = 38
        _draw_rounded_rect(draw, (cx, cy, cx+cw, cy+82), 6, Colors.BG_CARD, Colors.DARK_GRAY)
        draw.text((cx+8, cy+4), lbl, fill=Colors.GRAY, font=fx)
        sc_c = _score_color(sc, mx)
        draw.text((cx+10, cy+18), str(sc), fill=sc_c, font=fl)
        draw.text((cx+38, cy+26), "/"+str(mx), fill=Colors.GRAY, font=fs)
        _draw_bar(draw, cx+8, cy+66, cw-16, 8, sc, mx, sc_c, Colors.DARK_GRAY)

    # --- TOPLAM ---
    ty = 128
    _draw_rounded_rect(draw, (5, ty, 145, ty+60), 8, Colors.BG_CARD, Colors.DARK_GRAY)
    draw.text((14, ty+4), "TOPLAM GKS", fill=Colors.GRAY, font=fx)
    tc = _severity_color(toplam)
    draw.text((14, ty+16), str(toplam), fill=tc, font=fxl)
    
    st = _severity_text(toplam)
    # Durum ("NORMAL", "AGIR" vb.) rozeti
    bx = 75
    _draw_rounded_rect(draw, (bx, ty+16, bx+60, ty+36), 10, tc)
    draw.text((bx+6, ty+20), st, fill=Colors.BG_DARK, font=fs)
    
    _draw_bar(draw, 14, ty+46, 122, 6, toplam, 15, tc, Colors.DARK_GRAY)

    # --- GRAFIK (Geçmiş Toplamlar) ---
    _draw_rounded_rect(draw, (150, ty, SCREEN_W-5, ty+60), 8, Colors.BG_CARD, Colors.DARK_GRAY)
    draw.text((158, ty+4), "GKS GECMISI", fill=Colors.GRAY, font=fx)
    
    history = data.get("history", [])
    if len(history) > 0:
        gx, gy = 158, ty + 20
        gw, gh = 148, 32
        
        # Max 15, Min 3 
        y_min, y_max = 3, 15
        val_range = max(1, y_max - y_min)
        
        # Noktaları hesapla
        pts = []
        n = len(history)
        for idx, val in enumerate(history):
            px = gx + int((idx / max(1, n - 1)) * gw) if n > 1 else gx + gw//2
            py = gy + gh - int(((val - y_min) / val_range) * gh)
            pts.append((px, py))
            
        # Çizgi çiz
        if n > 1:
            draw.line(pts, fill=Colors.CYAN, width=2)
            
        # Noktaları çiz
        for p in pts:
            draw.ellipse([p[0]-2, p[1]-2, p[0]+2, p[1]+2], fill=Colors.TEAL)
            
        # Son değeri yaz
        last_val = history[-1]
        last_p = pts[-1]
        draw.text((last_p[0]-8, last_p[1]-14), str(last_val), fill=Colors.WHITE, font=fx)
    else:
        draw.text((170, ty+25), "Veri yok", fill=Colors.DARK_GRAY, font=fs)

    # --- DURUM ---
    sy = 194
    draw.rectangle([0, sy, SCREEN_W, SCREEN_H], fill=Colors.BG_HEADER)
    draw.line([0, sy, SCREEN_W, sy], fill=Colors.DARK_GRAY, width=1)
    blink = int(time.time()*2) % 2 == 0
    dot_c = Colors.GREEN if done else (Colors.TEAL if blink else Colors.DARK_GRAY)
    draw.ellipse([10, sy+8, 20, sy+18], fill=dot_c)
    draw.text((26, sy+5), "Asama: " + _STATE_MAP.get(durum, durum or "?"), fill=Colors.WHITE, font=fs)

    ear_s = "EAR:" + (f"{ear:.2f}" if ear > 0 else "--")
    draw.text((10, sy+22), ear_s, fill=Colors.GRAY, font=fx)
    eye_s = "ACIK" if goz_a else "KAPALI"
    eye_c = Colors.GREEN if goz_a else Colors.RED
    draw.text((100, sy+22), eye_s, fill=eye_c, font=fx)
    draw.text((SCREEN_W-70, sy+22), time.strftime("%H:%M:%S"), fill=Colors.GRAY, font=fx)

    return img


def render_splash():
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), Colors.BG_DARK)
    draw = ImageDraw.Draw(img)
    try:
        ft = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        fv = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except Exception:
        ft = fs = fv = ImageFont.load_default()

    for i in range(5):
        a = max(0, 255 - i * 50)
        c = (0, int(210*a/255), int(200*a/255))
        draw.line([40, 30+i*3, SCREEN_W-40, 30+i*3], fill=c)

    draw.ellipse([140, 60, 180, 100], fill=Colors.TEAL, outline=Colors.CYAN)
    draw.text((153, 68), "N", fill=Colors.BG_DARK, font=ft)
    draw.text((85, 110), "NeuroSense", fill=Colors.TEAL, font=ft)
    draw.text((78, 138), "Glasgow Koma Skalasi", fill=Colors.GRAY, font=fs)

    for i in range(5):
        a = max(0, 255 - i * 50)
        c = (0, int(210*a/255), int(200*a/255))
        draw.line([40, SCREEN_H-35+i*3, SCREEN_W-40, SCREEN_H-35+i*3], fill=c)

    draw.text((100, SCREEN_H-18), "v3.0 - Raspberry Pi 5", fill=Colors.DARK_GRAY, font=fv)
    draw.text((115, 165), "Baslatiliyor...", fill=Colors.TEAL_DIM, font=fs)
    return img


class ILI9341_SPI:
    """Natively accelerated SPI driver for ILI9341 (Bypasses Adafruit/Python static bugs)"""
    def __init__(self, bus=0, device=0, dc_pin=25, rst_pin=24, speed_hz=24000000):
        import spidev
        from gpiozero import OutputDevice

        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = speed_hz
        self.spi.mode = 0

        try:
            from gpiozero import OutputDevice
            self.dc = OutputDevice(dc_pin)
            self.rst = OutputDevice(rst_pin)
        except Exception as e:
            # GPIO mesgul hatasi verirse uyarip geciyoruz (Donanim reset islemi atlanacak ancak SPI yazmaya devam edecek)
            import logging
            logging.getLogger().warning(f"SPI Pinleri onceden acik kalmis: {e}")
            self.dc = None
            self.rst = None
        
        # Hardware reset
        if self.rst:
            self.rst.on()
            time.sleep(0.05)
            self.rst.off()
            time.sleep(0.05)
            self.rst.on()
            time.sleep(0.05)

        # ILI9341 Init Sequence
        self.command(0x01) # SWRESET
        time.sleep(0.120)
        self.command(0x11) # SLPOUT
        time.sleep(0.120)
        self.command(0x3A, [0x55]) # PIXFMT 16bit
        # MADCTL (Orientation) -> 0x28 is Landscape (Matches original orientation)
        self.command(0x36, [0x28]) 
        self.command(0x29) # DISPON
        time.sleep(0.05)

    def command(self, cmd, data=None):
        if self.dc: self.dc.off()
        self.spi.xfer3([cmd])
        if data:
            if self.dc: self.dc.on()
            self.spi.xfer3(data)

    def set_window(self, x0, y0, x1, y1):
        self.command(0x2A, [x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self.command(0x2B, [y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
        self.command(0x2C)

    def image(self, img):
        import numpy as np
        import RPi.GPIO as GPIO
        # Convert PIL to fast numpy array RGB565 byte stream
        img_arr = np.array(img.convert('RGB'))
        r = (img_arr[:,:,0] >> 3).astype(np.uint16) << 11
        g = (img_arr[:,:,1] >> 2).astype(np.uint16) << 5
        b = (img_arr[:,:,2] >> 3).astype(np.uint16)
        
        rgb565 = r | g | b
        
        data = np.empty((240, 320, 2), dtype=np.uint8)
        data[:,:,0] = (rgb565 >> 8) & 0xFF  # Big endian High byte
        data[:,:,1] = rgb565 & 0xFF         # Low byte
        
        # Send everything in ONE giant C-level DMA transfer (Zeros out ANY python timing noise!)
        self.set_window(0, 0, 319, 239)
        if self.dc: self.dc.on()
        self.spi.xfer3(data.tobytes())

    def close(self):
        try:
            if hasattr(self, 'dc') and self.dc is not None:
                self.dc.close()
            if hasattr(self, 'rst') and self.rst is not None:
                self.rst.close()
            if hasattr(self, 'spi') and self.spi is not None:
                self.spi.close()
        except Exception:
            pass


class EkranKontrol:
    def __init__(self):
        self._display = None
        self._initialized = False
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self.data = {
            "goz": 0, "motor": 0, "sozel": 0,
            "durum": "BASLANGIC", "ear": 0.0,
            "goz_acik": False, "tamamlandi": False,
            "nabiz_bpm": 0,
        }

    def baslat(self) -> bool:
        log.info("LCD ekran baslatiliyor (SPIDev Natively Accelerated)...")

        try:
            # Adafruit CircuitPython kütüphanesinden kaynaklı yavaşlık 
            # ve parazitleri/titremeleri engellemek için SPIDev ile direkt C tabanlı donanım erişimi kullanıyoruz.
            self._display = ILI9341_SPI(
                bus=0, device=0, 
                dc_pin=25, rst_pin=24, 
                speed_hz=24000000 
            )
            self._initialized = True
            log.info("  ILI9341 baslatildi (Native SPIDev, 320x240, 24MHz, SIFIR PARAZİT)")
        except Exception as e:
            log.error("HATA: ILI9341 SPIDev driver olusturulamadi: %s", e)
            traceback.print_exc()
            return False

        # --- Splash ekrani ---
        try:
            splash = render_splash()
            self._display.image(splash)
            log.info("  Splash ekrani gosterildi")
            time.sleep(2)
        except Exception as e:
            log.error("HATA: Splash ekrani gonderilemedi: %s", e)
            traceback.print_exc()
            return False

        # --- Arka plan thread ---
        self._running = True
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()
        log.info("  Ekran arka plan thread baslatildi")
        return True

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if k in self.data:
                    self.data[k] = v

    def _render_loop(self):
        while self._running:
            try:
                with self._lock:
                    d = dict(self.data)
                frame = render_frame(d)
                if self._display is not None:
                    self._display.image(frame)
            except Exception as e:
                log.warning("Render hatasi: %s", e)
            time.sleep(REFRESH_INTERVAL)

    def durdur(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            
        if self._display:
            try:
                self._display.close()
            except Exception:
                pass
                
        log.info("Ekran durduruldu")
