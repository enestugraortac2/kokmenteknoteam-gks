#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║  NeuroSense GKS — LCD Ekran Modülü (2.4" ILI9341)          ║
║  Raspberry Pi 5 — Premium Medikal Dashboard Arayüzü         ║
╚══════════════════════════════════════════════════════════════╝

Görev:
  - 2.4" ILI9341 SPI LCD üzerinde gerçek zamanlı GKS gösterimi
  - /dev/shm/gks_skor.json'dan verileri okuyarak ekrana yansıtma
  - Premium koyu tema, yüksek kontrast, tıbbi arayüz

Donanım Bağlantısı:
  - DC:  GPIO 25 (Fiziksel pin 22)
  - RST: GPIO 24 (Fiziksel pin 18)
  - CS:  Donanımsal SPI (None)
  - SPI: SCK, MOSI, MISO

Çözünürlük: 320x240 piksel (landscape)
"""

import os
import sys
import time
import json
import errno
import atexit
import signal
import logging
import threading
from pathlib import Path

# ─── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[EKRAN %(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ekran")

# ─── Platform-safe fcntl ────────────────────────────────────────
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

# ─── Konfigürasyon ─────────────────────────────────────────────
SHM_PATH = Path(os.environ.get("GKS_SHM_PATH", "/dev/shm/gks_skor.json"))

# Ekran boyutları
SCREEN_W = 320
SCREEN_H = 240

# Renk paleti (RGB)
class Colors:
    """Premium koyu tema renk paleti — tıbbi cihaz standardı."""
    # Arka plan
    BG_DARK = (10, 12, 28)          # Koyu lacivert
    BG_CARD = (18, 24, 48)          # Kart arka planı
    BG_HEADER = (14, 18, 38)        # Başlık çubuğu

    # Vurgu renkleri
    TEAL = (0, 210, 200)            # Ana vurgu — teal/turkuaz
    TEAL_DIM = (0, 140, 135)        # Soluk teal
    CYAN = (0, 195, 255)            # Siyan vurgu
    WHITE = (240, 245, 255)         # Beyaz metin
    GRAY = (130, 140, 160)          # İkincil metin
    DARK_GRAY = (60, 70, 90)        # Çizgiler

    # Durum renkleri — GKS seviye göstergeleri
    GREEN = (0, 230, 100)           # İyi (13-15)
    YELLOW = (255, 200, 0)          # Orta (9-12)
    ORANGE = (255, 140, 0)          # Endişe (6-8)
    RED = (255, 50, 50)             # Kritik (3-5)

    # Puan renkleri
    SCORE_EYE = (100, 180, 255)     # Göz — açık mavi
    SCORE_MOTOR = (150, 130, 255)   # Motor — mor
    SCORE_VERBAL = (255, 170, 100)  # Sözel — turuncu


# Referans boyutları — 320x240 ekran için optimize
HEADER_H = 32
SCORE_CARD_Y = 38
SCORE_CARD_H = 82
TOTAL_Y = 128
TOTAL_H = 60
STATUS_Y = 194
STATUS_H = 40

# Yenileme hızı
REFRESH_INTERVAL = 0.5  # saniye

# ─── Global State ──────────────────────────────────────────────
_running = True
_display = None


# ═══════════════════════════════════════════════════════════════
#  IPC: SHM Okuma
# ═══════════════════════════════════════════════════════════════

def read_shm() -> dict:
    """RAM disk'ten SHM verilerini oku."""
    if not SHM_PATH.exists():
        return {}
    try:
        with open(SHM_PATH, "r", encoding="utf-8") as f:
            if _HAS_FCNTL:
                try:
                    fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except OSError:
                    return {}
            try:
                return json.load(f)
            except (json.JSONDecodeError, ValueError):
                return {}
            finally:
                if _HAS_FCNTL:
                    try:
                        fcntl.flock(f, fcntl.LOCK_UN)
                    except Exception:
                        pass
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════
#  Yardımcı Çizim Fonksiyonları
# ═══════════════════════════════════════════════════════════════

def _severity_color(total: int) -> tuple:
    """GKS toplam puanına göre renk döndür."""
    if total >= 13:
        return Colors.GREEN
    elif total >= 9:
        return Colors.YELLOW
    elif total >= 6:
        return Colors.ORANGE
    else:
        return Colors.RED


def _severity_text(total: int) -> str:
    """GKS toplam puanına göre durum metni."""
    if total >= 13:
        return "NORMAL"
    elif total >= 9:
        return "ORTA"
    elif total >= 6:
        return "AĞIR"
    else:
        return "KRİTİK"


def _score_color(score: int, max_score: int) -> tuple:
    """Tek bir puan için renk (yüzdeye göre)."""
    ratio = score / max_score if max_score > 0 else 0
    if ratio >= 0.75:
        return Colors.GREEN
    elif ratio >= 0.5:
        return Colors.YELLOW
    elif ratio >= 0.25:
        return Colors.ORANGE
    else:
        return Colors.RED


def _state_text(state: str) -> str:
    """Durum makinesinin Türkçe karşılığı."""
    mapping = {
        "BASLANGIC": "Başlatılıyor",
        "PASIF_GOZLEM": "Pasif Gözlem",
        "SOZEL_UYARAN": "Sözel Uyaran",
        "MOTOR_KOMUT": "Motor Komut",
        "AGRILI_UYARAN": "Ağrılı Uyaran",
        "FINAL_RAPOR": "Rapor",
        "TAMAMLANDI": "Tamamlandı",
    }
    return mapping.get(state, state or "Bekleniyor")


def _draw_rounded_rect(draw, xy, radius, fill, outline=None):
    """Köşeleri yuvarlatılmış dikdörtgen çiz."""
    x0, y0, x1, y1 = xy
    r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)

    # Ana gövde
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)

    # Köşeler
    draw.pieslice([x0, y0, x0 + 2 * r, y0 + 2 * r], 180, 270, fill=fill)
    draw.pieslice([x1 - 2 * r, y0, x1, y0 + 2 * r], 270, 360, fill=fill)
    draw.pieslice([x0, y1 - 2 * r, x0 + 2 * r, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - 2 * r, y1 - 2 * r, x1, y1], 0, 90, fill=fill)

    # Çerçeve (opsiyonel)
    if outline:
        draw.arc([x0, y0, x0 + 2 * r, y0 + 2 * r], 180, 270, fill=outline)
        draw.arc([x1 - 2 * r, y0, x1, y0 + 2 * r], 270, 360, fill=outline)
        draw.arc([x0, y1 - 2 * r, x0 + 2 * r, y1], 90, 180, fill=outline)
        draw.arc([x1 - 2 * r, y1 - 2 * r, x1, y1], 0, 90, fill=outline)
        draw.line([x0 + r, y0, x1 - r, y0], fill=outline)
        draw.line([x0 + r, y1, x1 - r, y1], fill=outline)
        draw.line([x0, y0 + r, x0, y1 - r], fill=outline)
        draw.line([x1, y0 + r, x1, y1 - r], fill=outline)


def _draw_progress_bar(draw, x, y, w, h, value, max_val, color, bg_color):
    """Mini ilerleme çubuğu."""
    # Arka plan
    draw.rectangle([x, y, x + w, y + h], fill=bg_color)
    # Dolum
    if max_val > 0:
        fill_w = int((value / max_val) * w)
        if fill_w > 0:
            draw.rectangle([x, y, x + fill_w, y + h], fill=color)


# ═══════════════════════════════════════════════════════════════
#  Ana Ekran Renderlama
# ═══════════════════════════════════════════════════════════════

def render_frame(data: dict) -> "Image":
    """
    GKS dashboard frame'i oluştur (320x240 PIL Image).

    Düzen:
    ┌────────────────────────────────┐
    │  ◉ NeuroSense GKS    ●●●     │ ← Header (32px)
    ├────────┬────────┬─────────────┤
    │  GÖZ   │ MOTOR  │  SÖZEL     │ ← Score cards (82px)
    │  ●/4   │  ●/6   │   ●/5     │
    │ ■■■■□  │ ■■■□□  │  ■■■■□   │
    ├────────┴────────┴─────────────┤
    │  TOPLAM GKS:  ██ / 15        │ ← Total score (60px)
    │  ████████████░░░              │
    ├───────────────────────────────┤
    │  ● Aşama: Pasif Gözlem       │ ← Status bar (40px)
    │  EAR: 0.28 | 21:30:55       │
    └───────────────────────────────┘
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), Colors.BG_DARK)
    draw = ImageDraw.Draw(img)

    # Font yükleme (Pi'de mevcut fontlar)
    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        font_xs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
        font_xl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    except Exception:
        font_lg = ImageFont.load_default()
        font_md = ImageFont.load_default()
        font_sm = ImageFont.load_default()
        font_xs = ImageFont.load_default()
        font_xl = ImageFont.load_default()

    # ─── Puanlar ────────────────────────────────────────────
    goz = int(data.get("gks_goz", data.get("goz_puan", 0)))
    motor = int(data.get("gks_motor", data.get("motor_skor", 0)))
    sozel = int(data.get("gks_sozel", data.get("ses_skor", 0)))
    toplam = goz + motor + sozel
    durum = data.get("durum", "BASLANGIC")
    ear = data.get("goz_ear", 0.0)
    goz_acik = data.get("goz_acik", False)
    tamamlandi = data.get("gks_tamamlandi", False)

    # ═══ HEADER BAR ═════════════════════════════════════════
    draw.rectangle([0, 0, SCREEN_W, HEADER_H], fill=Colors.BG_HEADER)

    # Sol: Logo noktası + başlık
    # Teal dot
    draw.ellipse([8, 9, 22, 23], fill=Colors.TEAL)
    draw.text((26, 6), "NeuroSense GKS", fill=Colors.TEAL, font=font_md)

    # Orta/Sağ: Nabız Göstergesi (Yeni MAX30100 modülü)
    nabiz_bpm = int(data.get("nabiz_bpm", 0))
    nabiz_durum = data.get("nabiz_durum", "BEKLENIYOR")
    
    if nabiz_bpm > 0:
        # Nabız atma animasyonu
        blink = int(time.time() * 2) % 2 == 0
        h_color = Colors.RED if blink else Colors.GRAY
        draw.text((170, 6), "♥", fill=h_color, font=font_md)
        draw.text((190, 8), f"{nabiz_bpm} BPM", fill=Colors.WHITE, font=font_sm)
    elif nabiz_durum == "OLCULUYOR":
        draw.text((170, 8), "♥ Ölçülüyor...", fill=Colors.YELLOW, font=font_sm)

    # En Sağ: Durum gösterge LED'leri
    goz_led = Colors.GREEN if goz_acik else Colors.RED
    servo_led = Colors.RED if data.get("servo_aktif", False) else Colors.DARK_GRAY
    ses_led = Colors.GREEN if sozel > 0 else Colors.DARK_GRAY

    draw.ellipse([270, 11, 281, 22], fill=goz_led)      # Göz LED
    draw.ellipse([286, 11, 297, 22], fill=servo_led)     # Servo LED
    draw.ellipse([302, 11, 313, 22], fill=ses_led)       # Ses LED

    # İnce ayırıcı çizgi
    draw.line([0, HEADER_H, SCREEN_W, HEADER_H], fill=Colors.TEAL_DIM, width=1)

    # ═══ SKOR KARTLARI ═════════════════════════════════════
    card_w = 100
    card_gap = 5
    card_x_start = 5

    score_data = [
        ("GÖZ", "👁", goz, 4, Colors.SCORE_EYE),
        ("MOTOR", "✋", motor, 6, Colors.SCORE_MOTOR),
        ("SÖZEL", "🔊", sozel, 5, Colors.SCORE_VERBAL),
    ]

    for i, (label, icon, score, max_s, accent) in enumerate(score_data):
        cx = card_x_start + i * (card_w + card_gap)
        cy = SCORE_CARD_Y

        # Kart arka planı
        _draw_rounded_rect(draw, (cx, cy, cx + card_w, cy + SCORE_CARD_H),
                           radius=6, fill=Colors.BG_CARD, outline=Colors.DARK_GRAY)

        # Kategori etiketi
        draw.text((cx + 8, cy + 4), label, fill=Colors.GRAY, font=font_xs)

        # Büyük puan rakamı
        score_text = str(score)
        s_color = _score_color(score, max_s)
        draw.text((cx + 10, cy + 18), score_text, fill=s_color, font=font_lg)

        # Max değer
        draw.text((cx + 38, cy + 26), f"/{max_s}", fill=Colors.GRAY, font=font_sm)

        # İlerleme çubuğu
        _draw_progress_bar(draw,
                           cx + 8, cy + SCORE_CARD_H - 16,
                           card_w - 16, 8,
                           score, max_s, s_color, Colors.DARK_GRAY)

    # ═══ TOPLAM GKS BÖLÜMÜ ═════════════════════════════════
    ty = TOTAL_Y

    # Arka plan kartı
    _draw_rounded_rect(draw, (5, ty, SCREEN_W - 5, ty + TOTAL_H),
                       radius=8, fill=Colors.BG_CARD, outline=Colors.DARK_GRAY)

    # Sol: "TOPLAM GKS" etiketi
    draw.text((14, ty + 4), "TOPLAM GKS", fill=Colors.GRAY, font=font_xs)

    # Büyük toplam puan
    total_color = _severity_color(toplam)
    total_str = str(toplam)
    draw.text((14, ty + 16), total_str, fill=total_color, font=font_xl)

    # "/15" maksimum
    # offset_x for total text
    tw = len(total_str) * 20 + 14
    draw.text((tw + 4, ty + 28), "/15", fill=Colors.GRAY, font=font_md)

    # Sağ: Severity badge
    sev_text = _severity_text(toplam)
    sev_color = total_color

    badge_w = 90
    badge_x = SCREEN_W - badge_w - 14
    badge_y = ty + 10

    _draw_rounded_rect(draw,
                       (badge_x, badge_y, badge_x + badge_w, badge_y + 24),
                       radius=12, fill=sev_color)
    # Severity metin — koyu arka plan üzerine koyu metin
    draw.text((badge_x + 12, badge_y + 4), sev_text,
              fill=Colors.BG_DARK, font=font_sm)

    # Alt ilerleme çubuğu (tam genişlik)
    _draw_progress_bar(draw,
                       14, ty + TOTAL_H - 14,
                       SCREEN_W - 28, 6,
                       toplam, 15, total_color, Colors.DARK_GRAY)

    # ═══ DURUM ÇUBUĞU ══════════════════════════════════════
    sy = STATUS_Y

    draw.rectangle([0, sy, SCREEN_W, SCREEN_H], fill=Colors.BG_HEADER)
    draw.line([0, sy, SCREEN_W, sy], fill=Colors.DARK_GRAY, width=1)

    # Durum göstergesi (yanıp sönen nokta efekti)
    blink = int(time.time() * 2) % 2 == 0
    if tamamlandi:
        dot_color = Colors.GREEN
    elif blink:
        dot_color = Colors.TEAL
    else:
        dot_color = Colors.DARK_GRAY

    draw.ellipse([10, sy + 8, 20, sy + 18], fill=dot_color)

    # Aşama metni
    phase_text = _state_text(durum)
    draw.text((26, sy + 5), f"Aşama: {phase_text}", fill=Colors.WHITE, font=font_sm)

    # Alt satır: EAR + saat
    ear_str = f"EAR: {ear:.2f}" if ear > 0 else "EAR: —"
    time_str = time.strftime("%H:%M:%S")
    draw.text((10, sy + 22), ear_str, fill=Colors.GRAY, font=font_xs)
    draw.text((SCREEN_W - 70, sy + 22), time_str, fill=Colors.GRAY, font=font_xs)

    # Göz durumu ikonu
    eye_status = "●AÇIK" if goz_acik else "○KAPALI"
    eye_color = Colors.GREEN if goz_acik else Colors.RED
    draw.text((110, sy + 22), eye_status, fill=eye_color, font=font_xs)

    return img


# ═══════════════════════════════════════════════════════════════
#  Geçmiş Ölçümler Sayfası
# ═══════════════════════════════════════════════════════════════

def render_history(data: dict) -> "Image":
    """Geçmiş ölçümlerin gösterildiği ikinci ekran."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), Colors.BG_DARK)
    draw = ImageDraw.Draw(img)

    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_xs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except Exception:
        font_lg = ImageFont.load_default()
        font_md = ImageFont.load_default()
        font_sm = ImageFont.load_default()
        font_xs = ImageFont.load_default()

    # Header
    draw.rectangle([0, 0, SCREEN_W, 36], fill=Colors.BG_HEADER)
    draw.ellipse([8, 10, 24, 26], fill=Colors.TEAL)
    draw.text((32, 8), "Geçmiş Ölçümler", fill=Colors.WHITE, font=font_lg)
    draw.line([0, 36, SCREEN_W, 36], fill=Colors.TEAL_DIM, width=1)

    # Verileri çek
    gecmis = data.get("gks_gecmis", [])
    if not gecmis:
        # Eğer geçmiş ölçüm yoksa (ilk ölçüm tamamlanmadıysa)
        draw.text((60, 100), "Henüz geçmiş ölçüm yok.", fill=Colors.GRAY, font=font_sm)
        return img

    # En fazla son 4 ölçüm
    start_y = 50
    row_h = 40
    gap = 5

    for i, row in enumerate(gecmis):
        score = row.get("toplam", 0)
        sev = row.get("severity", "BİLİNMİYOR")
        ts = row.get("ts", "--:--")

        y = start_y + i * (row_h + gap)

        # Arka plan kartı
        _draw_rounded_rect(draw, (10, y, SCREEN_W - 10, y + row_h), radius=4, fill=Colors.BG_CARD)

        # Zaman
        draw.text((20, y + 12), ts, fill=Colors.GRAY, font=font_sm)

        # Skor Dairesi
        s_color = _severity_color(score)
        _draw_rounded_rect(draw, (80, y + 6, 120, y + 36), radius=4, fill=s_color)
        
        # Sayıyı ortalama 
        score_text = str(score)
        dx = 12 if score < 10 else 6
        draw.text((80 + dx, y + 10), score_text, fill=Colors.BG_DARK, font=font_md)

        # Durum Metni
        draw.text((140, y + 12), sev[:14], fill=s_color, font=font_sm)


    # Durum Çubuğu
    sy = STATUS_Y
    draw.rectangle([0, sy, SCREEN_W, SCREEN_H], fill=Colors.BG_HEADER)
    draw.line([0, sy, SCREEN_W, sy], fill=Colors.DARK_GRAY, width=1)
    
    time_str = time.strftime("%H:%M:%S")
    draw.text((10, sy + 5), "GKS Sonuç Ekranı", fill=Colors.WHITE, font=font_sm)
    draw.text((SCREEN_W - 70, sy + 22), time_str, fill=Colors.GRAY, font=font_xs)

    return img


# ═══════════════════════════════════════════════════════════════
#  Splash Ekranı
# ═══════════════════════════════════════════════════════════════

def render_splash() -> "Image":
    """Başlangıç splash ekranı."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), Colors.BG_DARK)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        font_ver = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_ver = ImageFont.load_default()

    # Teal çizgiler dekorasyon
    for i in range(5):
        y = 30 + i * 3
        alpha = max(0, 255 - i * 50)
        c = (0, int(210 * alpha / 255), int(200 * alpha / 255))
        draw.line([40, y, SCREEN_W - 40, y], fill=c, width=1)

    # Logo merkez
    draw.ellipse([140, 60, 180, 100], fill=Colors.TEAL, outline=Colors.CYAN)
    draw.text((153, 68), "N", fill=Colors.BG_DARK, font=font_title)

    # Başlık
    draw.text((85, 110), "NeuroSense", fill=Colors.TEAL, font=font_title)
    draw.text((88, 138), "Glasgow Koma Skalası", fill=Colors.GRAY, font=font_sub)

    # Alt çizgiler
    for i in range(5):
        y = SCREEN_H - 35 + i * 3
        alpha = max(0, 255 - i * 50)
        c = (0, int(210 * alpha / 255), int(200 * alpha / 255))
        draw.line([40, y, SCREEN_W - 40, y], fill=c, width=1)

    # Versiyon
    draw.text((105, SCREEN_H - 18), "v2.0 — Raspberry Pi 5", fill=Colors.DARK_GRAY, font=font_ver)

    # Yükleniyor animasyonu
    dots = "·" * (int(time.time()) % 4 + 1)
    draw.text((120, 165), f"Başlatılıyor{dots}", fill=Colors.TEAL_DIM, font=font_sub)

    return img


# ═══════════════════════════════════════════════════════════════
#  Hata Ekranı
# ═══════════════════════════════════════════════════════════════

def render_error(message: str) -> "Image":
    """Hata ekranı."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (30, 10, 10))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except Exception:
        font = ImageFont.load_default()
        font_sm = ImageFont.load_default()

    draw.text((20, 30), "⚠ HATA", fill=Colors.RED, font=font)
    draw.line([20, 52, SCREEN_W - 20, 52], fill=Colors.RED, width=1)

    # Mesajı satırlara böl
    words = message.split()
    lines = []
    current_line = ""
    for word in words:
        test = current_line + " " + word if current_line else word
        if len(test) > 35:
            lines.append(current_line)
            current_line = word
        else:
            current_line = test
    if current_line:
        lines.append(current_line)

    for i, line in enumerate(lines[:6]):
        draw.text((20, 60 + i * 16), line, fill=Colors.WHITE, font=font_sm)

    return img


# ═══════════════════════════════════════════════════════════════
#  Ekran Kontrolcüsü
# ═══════════════════════════════════════════════════════════════

class DisplayController:
    """ILI9341 ekran yöneticisi (spidev ve gpiozero tabanlı donanımsal sürücü)."""

    def __init__(self):
        self._spi = None
        self._initialized = False
        self._dc_pin = None
        self._rst_pin = None

    def _command(self, cmd, data=None):
        self._dc_pin.off()
        self._spi.xfer2([cmd])
        if data:
            self._dc_pin.on()
            if isinstance(data, int):
                self._spi.xfer2([data])
            else:
                self._spi.writebytes2(data)

    def initialize(self) -> bool:
        """Donanımı başlat."""
        try:
            import spidev
            from gpiozero import OutputDevice

            self._dc_pin = OutputDevice(25)
            self._rst_pin = OutputDevice(24)

            # Reset sequence
            self._rst_pin.on()
            time.sleep(0.01)
            self._rst_pin.off()
            time.sleep(0.01)
            self._rst_pin.on()
            time.sleep(0.12)

            # SPI setup
            self._spi = spidev.SpiDev()
            self._spi.open(0, 0)
            self._spi.max_speed_hz = 32000000
            self._spi.mode = 0

            # ILI9341 Init Sequence
            self._command(0xEF, [0x03, 0x80, 0x02])
            self._command(0xCF, [0x00, 0XC1, 0X30])
            self._command(0xED, [0x64, 0x03, 0X12, 0X81])
            self._command(0xE8, [0x85, 0x00, 0x78])
            self._command(0xCB, [0x39, 0x2C, 0x00, 0x34, 0x02])
            self._command(0xF7, [0x20])
            self._command(0xEA, [0x00, 0x00])

            self._command(0xC0, [0x23]) # Power control VRH[5:0]
            self._command(0xC1, [0x10]) # Power control SAP[2:0];BT[3:0]
            self._command(0xC5, [0x3e, 0x28]) # VCM control
            self._command(0xC7, [0x86]) # VCM control2
            
            # Memory Access Control - Landscape mode
            self._command(0x36, [0x28]) 

            self._command(0x3A, [0x55]) # Pixel Format
            self._command(0xB1, [0x00, 0x18]) # Frame Ratio Control, Standard RGB Color
            self._command(0xB6, [0x08, 0x82, 0x27]) # Display Function Control
            self._command(0xF2, [0x00]) # 3Gamma Function Disable
            self._command(0x26, [0x01]) # Gamma curve selected
            self._command(0xE0, [0x0F, 0x31, 0x2B, 0x0C, 0x0E, 0x08, 0x4E, 0xF1, 0x37, 0x07, 0x10, 0x03, 0x0E, 0x09, 0x00]) # Set Gamma
            self._command(0xE1, [0x00, 0x0E, 0x14, 0x03, 0x11, 0x07, 0x31, 0xC1, 0x48, 0x08, 0x0F, 0x0C, 0x31, 0x36, 0x0F]) # Set Gamma

            self._command(0x11) # Sleep out
            time.sleep(0.12)
            self._command(0x29) # Display on

            self._initialized = True
            log.info("ILI9341 ekran başlatıldı ✓ (spidev, %dx%d)", SCREEN_W, SCREEN_H)
            return True
        except Exception as e:
            log.error("Ekran başlatma hatası: %s", e)
            self._initialized = False
            return False

    def show(self, image) -> bool:
        """PIL Image'i ekrana gönder."""
        if not self._initialized or self._spi is None:
            return False
        try:
            import numpy as np
            
            # Convert PIL RGB image to numpy array
            img_array = np.array(image.convert('RGB'), dtype=np.uint16)
            
            # Extract R, G, B channels and shift to RGB565 format
            r = (img_array[:, :, 0] & 0xF8) << 8
            g = (img_array[:, :, 1] & 0xFC) << 3
            b = (img_array[:, :, 2] & 0xF8) >> 3
            
            # Combine channels
            rgb565 = r | g | b
            
            # ILI9341 expects Big Endian, so we byteswap
            data = rgb565.byteswap().tobytes()

            # Set column address
            self._command(0x2A, [0x00, 0x00, (SCREEN_W-1)>>8, (SCREEN_W-1)&0xFF])
            # Set page address
            self._command(0x2B, [0x00, 0x00, (SCREEN_H-1)>>8, (SCREEN_H-1)&0xFF])
            # Write memory
            self._command(0x2C)
            
            # Send data
            self._dc_pin.on()
            self._spi.writebytes2(data)

            return True
        except Exception as e:
            log.warning("Ekran güncelleme hatası: %s", e)
            return False

    @property
    def is_ready(self) -> bool:
        return self._initialized


# ═══════════════════════════════════════════════════════════════
#  Ana Döngü
# ═══════════════════════════════════════════════════════════════

def run():
    """Ekran döngüsü: SHM'den oku → render → ekrana gönder."""
    global _running

    log.info("NeuroSense LCD Ekran modülü başlatılıyor...")

    controller = DisplayController()

    if not controller.initialize():
        log.error("Ekran başlatılamadı! Çıkılıyor.")
        sys.exit(1)

    # Splash ekranı göster
    log.info("Splash ekranı gösteriliyor...")
    splash = render_splash()
    controller.show(splash)
    time.sleep(3)

    log.info("═══ Ana ekran döngüsü başlıyor ═══")

    try:
        while _running:
            loop_start = time.monotonic()

            try:
                data = read_shm()
                
                # Eğer tamamlandıysa 5 saniyede bir sayfaları değiştir
                if data.get("gks_tamamlandi", False) and len(data.get("gks_gecmis", [])) > 0:
                    page_switch = int(time.time() / 5) % 2
                    if page_switch == 0:
                        frame = render_frame(data)
                    else:
                        frame = render_history(data)
                else:
                    frame = render_frame(data)
                    
                controller.show(frame)
            except Exception as e:
                log.warning("Render hatası: %s", e)
                try:
                    err_img = render_error(str(e))
                    controller.show(err_img)
                except Exception:
                    pass

            # FPS kontrol
            elapsed = time.monotonic() - loop_start
            sleep_time = REFRESH_INTERVAL - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt alındı.")
    finally:
        _running = False
        log.info("Ekran modülü kapatıldı.")


# ═══════════════════════════════════════════════════════════════
#  Standalone Test (ekranı test etmek için)
# ═══════════════════════════════════════════════════════════════

def test_render():
    """Ekran bağlı olmadan test için PNG kaydet."""
    from PIL import Image

    # Örnek veri
    test_data = {
        "gks_goz": 3,
        "gks_motor": 5,
        "gks_sozel": 4,
        "goz_ear": 0.24,
        "goz_acik": True,
        "durum": "MOTOR_KOMUT",
        "servo_aktif": False,
        "gks_tamamlandi": False,
    }

    # Dashboard
    frame = render_frame(test_data)
    frame.save("/tmp/gks_dashboard_test.png")
    log.info("Test frame kaydedildi: /tmp/gks_dashboard_test.png")

    # Splash
    splash = render_splash()
    splash.save("/tmp/gks_splash_test.png")
    log.info("Splash kaydedildi: /tmp/gks_splash_test.png")

    # Tamamlanmış durum
    test_data["gks_tamamlandi"] = True
    test_data["durum"] = "TAMAMLANDI"
    test_data["gks_goz"] = 4
    test_data["gks_motor"] = 6
    test_data["gks_sozel"] = 5
    test_data["gks_gecmis"] = [
        {"toplam": 15, "severity": "NORMAL", "ts": "10:15"},
        {"toplam": 8, "severity": "AGIR KOMA", "ts": "10:45"},
        {"toplam": 13, "severity": "HAFIF KOMA", "ts": "11:20"}
    ]
    final = render_frame(test_data)
    final.save("/tmp/gks_final_test.png")
    log.info("Final frame kaydedildi: /tmp/gks_final_test.png")

    history = render_history(test_data)
    history.save("/tmp/gks_history_test.png")
    log.info("History frame kaydedildi: /tmp/gks_history_test.png")

    # Kritik durum
    test_data["gks_goz"] = 1
    test_data["gks_motor"] = 2
    test_data["gks_sozel"] = 1
    test_data["goz_acik"] = False
    test_data["servo_aktif"] = True
    test_data["durum"] = "AGRILI_UYARAN"
    test_data["gks_tamamlandi"] = False
    critical = render_frame(test_data)
    critical.save("/tmp/gks_critical_test.png")
    log.info("Kritik frame kaydedildi: /tmp/gks_critical_test.png")


# ═══════════════════════════════════════════════════════════════
#  Graceful Shutdown
# ═══════════════════════════════════════════════════════════════

def _signal_handler(signum, frame):
    global _running
    log.info("Sinyal alındı (%s), kapatılıyor...", signum)
    _running = False
    sys.exit(0)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ═══════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NeuroSense GKS LCD Ekran")
    parser.add_argument("--test", action="store_true",
                        help="Ekran bağlı olmadan test PNG oluştur")
    args = parser.parse_args()

    if args.test:
        test_render()
    else:
        run()
