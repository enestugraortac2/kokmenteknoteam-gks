import logging
import busio
import board
import digitalio
import json
import time
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.ili9341 as ili9341
from pathlib import Path
import os

log = logging.getLogger("EkranAnaliz")

SCREEN_W, SCREEN_H = 240, 320
ROOT = Path(__file__).parent.parent
RES_DIR = ROOT / "resources"

class GKSEkran:
    def __init__(self):
        self._display = None
        self._font_s = None
        self._font_m = None
        self._font_l = None
        self._font_xl = None
        
        self.koma_durumu = "NORMAL"
        self.koma_renk = (0, 255, 0)
        
        self.current_state_str = "BASLANGIC"
        self.heart_rate = 0
        self.spo2 = 0
        
        self.goz_skoru = 1
        self.motor_skor = 1
        self.sozel_skor = 1

    def baslat(self):
        log.info("Ekran başlatılıyor...")
        try:
            # Fontları yükle
            tr_font = str(RES_DIR / "tr_font.ttf")
            from PIL import ImageFont
            try:
                self._font_s = ImageFont.truetype(tr_font, 14)
                self._font_m = ImageFont.truetype(tr_font, 18)
                self._font_l = ImageFont.truetype(tr_font, 24)
                self._font_xl = ImageFont.truetype(tr_font, 36)
            except Exception as e:
                log.warning(f"tr_font.ttf bulunamadı, varsayılan font kullanılıyor. Hata: {e}")
                def_f = ImageFont.load_default()
                self._font_s = def_f
                self._font_m = def_f
                self._font_l = def_f
                self._font_xl = def_f

            # SPI Başlat
            from busio import SPI
            from board import SCK, MOSI, MISO, D25, D24
            from digitalio import DigitalInOut, Direction
            
            dc_pin = DigitalInOut(D25)
            reset_pin = DigitalInOut(D24)
            
            reset_pin.direction = Direction.OUTPUT
            reset_pin.value = False
            time.sleep(0.1)
            reset_pin.value = True
            time.sleep(0.1)

            spi = SPI(SCK, MOSI, MISO)
            # Jumper kablolarındaki parazitlenmeyi önlemek için baudrate DÜŞÜRÜLDÜ (2MHz)
            self._display = ili9341.ILI9341(
                spi, cs=None, dc=dc_pin, rst=reset_pin,
                baudrate=2000000, 
                width=SCREEN_W, height=SCREEN_H,
                rotation=0 # Rotation kütüphaneyi bozuyor, PIL üzerinden çevireceğiz
            )
            log.info("ILS9341 Ekran Başarıyla Başlatıldı ✓")
            self.guncelle()
            return True
        except Exception as e:
            log.error(f"Ekran başlatma hatası: {e}")
            return False

    def update_data(self, state=None, hr=None, spo2=None, g=None, m=None, v=None):
        if state is not None: self.current_state_str = state
        if hr is not None: self.heart_rate = hr
        if spo2 is not None: self.spo2 = spo2
        if g is not None: self.goz_skoru = g
        if m is not None: self.motor_skor = m
        if v is not None: self.sozel_skor = v
        self._calc_koma()

    def _calc_koma(self):
        t = self.goz_skoru + self.motor_skor + self.sozel_skor
        if t <= 8:
            self.koma_durumu = "AGIR KOMA"
            self.koma_renk = (255, 0, 0)
        elif t <= 12:
            self.koma_durumu = "ORTA KOMA"
            self.koma_renk = (255, 165, 0)
        elif t <= 14:
            self.koma_durumu = "HAFIF KOMA"
            self.koma_renk = (255, 255, 0)
        else:
            self.koma_durumu = "NORMAL"
            self.koma_renk = (0, 255, 0)

    def guncelle(self):
        if not self._display:
            return
            
        try:
            image = Image.new("RGB", (SCREEN_W, SCREEN_H), (0, 0, 0))
            draw = ImageDraw.Draw(image)
            
            # Üst Bar
            draw.rectangle((0, 0, SCREEN_W, 35), fill=(30, 30, 30))
            draw.text((10, 8), "NEUROSENSE GKS", font=self._font_m, fill=(255, 255, 255))
            
            # Nabız ve SpO2 (Pil gibi)
            hr_txt = f"{int(self.heart_rate)}" if self.heart_rate > 0 else "--"
            sp_txt = f"{int(self.spo2)}" if self.spo2 > 0 else "--"
            draw.text((SCREEN_W - 100, 8), f"❤{hr_txt}  O2:{sp_txt}", font=self._font_m, fill=(255, 100, 100))

            # SKOR EKRANI
            if self.current_state_str == "FINAL_RAPOR":
                self._draw_rapor(draw)
            else:
                self._draw_normal(draw)

            # Alt Bar (Durum)
            draw.rectangle((0, SCREEN_H - 30, SCREEN_W, SCREEN_H), fill=(40, 40, 40))
            draw.text((10, SCREEN_H - 25), f"DURUM: {self.current_state_str}", font=self._font_s, fill=(200, 200, 200))
            
            # Ekran kütüphanesi rotation bug'ı yüzünden görüntüyü manuel 90 derece çevirip basıyoruz
            rotated_image = image.transpose(Image.ROTATE_90)
            self._display.image(rotated_image)
        except Exception as e:
            log.error(f"Ekran çizim hatası: {e}")

    def _draw_normal(self, draw):
        # Koma Durumu Ortada
        bbox = draw.textbbox((0, 0), self.koma_durumu, font=self._font_xl)
        w = bbox[2] - bbox[0]
        draw.text(((SCREEN_W - w) // 2, 60), self.koma_durumu, font=self._font_xl, fill=self.koma_renk)
        
        # Skorlar
        box_y = 130
        draw.text((30, box_y), f"Goz: {self.goz_skoru}/4", font=self._font_l, fill=(200, 200, 255))
        draw.text((30, box_y + 35), f"Motor: {self.motor_skor}/6", font=self._font_l, fill=(200, 255, 200))
        draw.text((180, box_y + 15), f"Sozel: {self.sozel_skor}/5", font=self._font_l, fill=(255, 200, 200))

    def _draw_rapor(self, draw):
        draw.text((10, 50), "--- FINAL GKS RAPORU ---", font=self._font_m, fill=(255, 255, 255))
        draw.text((20, 80), f"Goz Yaniti:   {self.goz_skoru} / 4", font=self._font_m, fill=(200, 200, 255))
        draw.text((20, 110), f"Motor Yanit:  {self.motor_skor} / 6", font=self._font_m, fill=(200, 255, 200))
        draw.text((20, 140), f"Sozel Yanit:  {self.sozel_skor} / 5", font=self._font_m, fill=(255, 200, 200))
        
        t = self.goz_skoru + self.motor_skor + self.sozel_skor
        draw.text((20, 180), f"TOPLAM SKOR: {t}/15", font=self._font_l, fill=self.koma_renk)
