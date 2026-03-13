#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS — Premium Sunum Arayüzü
Canvas-tabanlı dairesel göstergeler, adım ilerlemesi ve profesyonel grafik.
Hasta İhtiyaç Sorgulama paneli entegre edilmiştir.
"""

import tkinter as tk
import math
import threading
import time
import sys
import difflib
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
#  Renk Paleti
# ═══════════════════════════════════════════════════════════════
class C:
    BG          = "#0d1117"
    CARD        = "#161b22"
    CARD_HOVER  = "#1c2129"
    BORDER      = "#21262d"
    BORDER_LT   = "#30363d"

    WHITE       = "#e6edf3"
    TEXT        = "#c9d1d9"
    TEXT2       = "#8b949e"
    DIM         = "#484f58"

    CYAN        = "#2dd4bf"
    PURPLE      = "#a78bfa"
    AMBER       = "#fbbf24"
    BLUE        = "#58a6ff"
    BLUE_DIM    = "#1f3a5f"
    GREEN       = "#3fb950"
    RED         = "#f85149"
    ORANGE      = "#d29922"

    GRAPH_LINE  = "#58a6ff"
    GRAPH_FILL  = "#0d2240"
    GRAPH_GRID  = "#161b22"
    GRAPH_DOT   = "#79c0ff"


# ═══════════════════════════════════════════════════════════════
#  Yardımcılar
# ═══════════════════════════════════════════════════════════════
STAGES = [
    ("PASIF_GOZLEM",  "Pasif Gözlem"),
    ("SOZEL_UYARAN",  "Sözel Uyaran"),
    ("MOTOR_KOMUT",   "Motor Komut"),
    ("AGRILI_UYARAN", "Ağrılı Uyaran"),
]

STAGE_KEYS = [s[0] for s in STAGES]


def severity_text(t):
    if t <= 8:  return "AĞIR KOMA"
    if t <= 12: return "ORTA KOMA"
    return "HAFİF / NORMAL"


def severity_color(t):
    if t <= 8:  return C.RED
    if t <= 12: return C.ORANGE
    return C.GREEN


# ═══════════════════════════════════════════════════════════════
#  Dairesel Gösterge (Arc Gauge) Widget
# ═══════════════════════════════════════════════════════════════
class ArcGauge:
    """Canvas üzerinde 270° yay gösterge çizer."""

    def __init__(self, parent, size, label, max_val, color, bg=C.CARD):
        self.size = size
        self.max_val = max_val
        self.color = color
        self.bg = bg
        self.label = label
        self.value = 1

        self.canvas = tk.Canvas(parent, width=size, height=size + 28,
                                bg=bg, highlightthickness=0)

        self._draw()

    def _draw(self):
        self.canvas.delete("all")
        s = self.size
        cx, cy = s // 2, s // 2
        pad = 14
        r = (s // 2) - pad
        lw = 8

        # Arka plan yayı (270°, -225 → 45)
        self.canvas.create_arc(
            cx - r, cy - r, cx + r, cy + r,
            start=-225, extent=270,
            style=tk.ARC, outline=C.BORDER_LT, width=lw
        )

        # Değer yayı
        ratio = min(self.value / self.max_val, 1.0)
        extent = ratio * 270
        if extent > 0:
            self.canvas.create_arc(
                cx - r, cy - r, cx + r, cy + r,
                start=-225, extent=extent,
                style=tk.ARC, outline=self.color, width=lw + 1
            )

        # Ortadaki skor
        self.canvas.create_text(
            cx, cy - 4, text=str(self.value),
            fill=C.WHITE, font=("Helvetica", int(s * 0.22), "bold")
        )
        self.canvas.create_text(
            cx, cy + int(s * 0.16), text=f"/ {self.max_val}",
            fill=C.DIM, font=("Helvetica", int(s * 0.09))
        )

        # Alt etiket
        self.canvas.create_text(
            cx, s + 14, text=self.label,
            fill=C.TEXT2, font=("Helvetica", 11)
        )

    def set_value(self, v):
        if v != self.value:
            self.value = v
            self._draw()

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def grid(self, **kw):
        self.canvas.grid(**kw)


# ═══════════════════════════════════════════════════════════════
#  Adım İlerlemesi (Step Indicator) Widget
# ═══════════════════════════════════════════════════════════════
class StepIndicator:
    """Yatay adım göstergesi."""

    def __init__(self, parent, bg=C.BG):
        self.bg = bg
        self.current_idx = -1

        self.canvas = tk.Canvas(parent, height=52, bg=bg, highlightthickness=0)

    def _draw(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        if w < 100:
            return

        n = len(STAGES)
        pad = 60
        total_w = w - pad * 2
        step_w = total_w / max(n - 1, 1)
        cy = 18

        for i in range(n):
            x = pad + i * step_w

            # Bağlantı çizgisi
            if i < n - 1:
                x2 = pad + (i + 1) * step_w
                color = C.BLUE if i < self.current_idx else C.BORDER_LT
                self.canvas.create_line(x, cy, x2, cy, fill=color, width=2)

            # Daire
            r = 8
            if i < self.current_idx:
                # Tamamlanmış
                self.canvas.create_oval(x - r, cy - r, x + r, cy + r,
                                        fill=C.BLUE, outline="")
                self.canvas.create_text(x, cy, text="✓", fill=C.WHITE,
                                        font=("Helvetica", 9, "bold"))
            elif i == self.current_idx:
                # Aktif
                self.canvas.create_oval(x - r - 3, cy - r - 3, x + r + 3, cy + r + 3,
                                        fill="", outline=C.BLUE, width=2)
                self.canvas.create_oval(x - 4, cy - 4, x + 4, cy + 4,
                                        fill=C.BLUE, outline="")
            else:
                # Bekliyor
                self.canvas.create_oval(x - r, cy - r, x + r, cy + r,
                                        fill="", outline=C.BORDER_LT, width=2)

            # Etiket
            color_t = C.BLUE if i == self.current_idx else (C.TEXT2 if i < self.current_idx else C.DIM)
            weight = "bold" if i == self.current_idx else "normal"
            self.canvas.create_text(x, cy + 22, text=STAGES[i][1],
                                    fill=color_t, font=("Helvetica", 9, weight))

    def set_stage(self, stage_key):
        if stage_key in STAGE_KEYS:
            self.current_idx = STAGE_KEYS.index(stage_key)
        elif stage_key in ("FINAL_RAPOR", "TAMAMLANDI"):
            self.current_idx = len(STAGES)  # tümü tamamlanmış
        else:
            self.current_idx = -1
        self._draw()

    def pack(self, **kw):
        self.canvas.pack(**kw)
        self.canvas.bind("<Configure>", lambda e: self._draw())




# ═══════════════════════════════════════════════════════════════
#  Kamera Görüntüleyici (Sadece Düz Kamera)
# ═══════════════════════════════════════════════════════════════

class CameraViewer:
    """
    İki kamerayı yan yana gösteren Toplevel penceresi.
    Sadece düz kamera görüntülerini gösterir (AI overlay yok).
    """

    def __init__(self, parent, scenario_config=None):
        self.parent = parent
        self.win = None
        self.running = False
        self.scenario_config = scenario_config or {}

        # Kamera nesneleri
        self.cam0 = None
        self.cam1 = None

    def toggle(self):
        """Ana butona basıldığında aç/kapat."""
        if self.win is not None and self.running:
            self._close()
        else:
            self._open()

    def _open(self):
        import cv2
        try:
            from PIL import Image, ImageTk
        except ImportError:
            print("[HATA] Pillow bulunamadı: pip install Pillow")
            return

        self.running = True

        self.win = tk.Toplevel(self.parent)
        self.win.title("NeuroSense — Kamera Görüntüleri")
        self.win.geometry("900x520")
        self.win.configure(bg=C.BG)
        self.win.protocol("WM_DELETE_WINDOW", self._close)

        # Üst bar
        bar = tk.Frame(self.win, bg=C.BG, height=44)
        bar.pack(fill=tk.X, padx=16, pady=(10, 6))
        bar.pack_propagate(False)

        tk.Label(bar, text="◆  Kamera İzleme", font=("Helvetica", 14, "bold"),
                 bg=C.BG, fg=C.WHITE).pack(side=tk.LEFT)

        tk.Frame(self.win, bg=C.BORDER, height=1).pack(fill=tk.X, padx=16)

        # Kamera frame'leri
        cam_frame = tk.Frame(self.win, bg=C.BG)
        cam_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=10)
        cam_frame.columnconfigure(0, weight=1)
        cam_frame.columnconfigure(1, weight=1)
        cam_frame.rowconfigure(1, weight=1)

        tk.Label(cam_frame, text="Kamera 0 — Göz Takibi", font=("Helvetica", 10),
                 bg=C.BG, fg=C.TEXT2).grid(row=0, column=0, pady=(0, 4))
        tk.Label(cam_frame, text="Kamera 1 — Motor Takibi", font=("Helvetica", 10),
                 bg=C.BG, fg=C.TEXT2).grid(row=0, column=1, pady=(0, 4))

        self.lbl_cam0 = tk.Label(cam_frame, bg=C.CARD, text="Kamera bağlanıyor…",
                                  fg=C.DIM, font=("Helvetica", 11))
        self.lbl_cam0.grid(row=1, column=0, sticky="nsew", padx=(0, 5))

        self.lbl_cam1 = tk.Label(cam_frame, bg=C.CARD, text="Kamera bağlanıyor…",
                                  fg=C.DIM, font=("Helvetica", 11))
        self.lbl_cam1.grid(row=1, column=1, sticky="nsew", padx=(5, 0))

        self._start_cameras()
        self._update_frames()

    def _start_cameras(self):
        """Gerçek kameraları başlat (Picamera2 veya cv2)."""
        import cv2
        cam0_id = int(self.scenario_config.get("cam0_id", 0))
        cam1_id = int(self.scenario_config.get("cam1_id", 1))

        # Kamera 0
        try:
            from picamera2 import Picamera2
            self.cam0 = Picamera2(cam0_id)
            cfg = self.cam0.create_preview_configuration(
                main={"format": "RGB888", "size": (640, 480)})
            self.cam0.configure(cfg)
            self.cam0.start()
            self._cam0_type = "picam"
        except Exception:
            self.cam0 = cv2.VideoCapture(cam0_id)
            self._cam0_type = "cv2"

        # Kamera 1
        try:
            from picamera2 import Picamera2
            self.cam1 = Picamera2(cam1_id)
            cfg = self.cam1.create_preview_configuration(
                main={"format": "RGB888", "size": (640, 480)})
            self.cam1.configure(cfg)
            self.cam1.start()
            self._cam1_type = "picam"
        except Exception:
            self.cam1 = cv2.VideoCapture(cam1_id)
            self._cam1_type = "cv2"

    def _grab_frame(self, cam, cam_type):
        """Tek kameradan frame al."""
        import cv2
        try:
            if cam is None:
                return None
            if cam_type == "picam":
                raw = cam.capture_array()
                return cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
            else:
                ret, frame = cam.read()
                return frame if ret else None
        except Exception:
            return None

    def _update_frames(self):
        """Periyodik olarak kamera frame'lerini güncelle (saf görüntü)."""
        if not self.running or self.win is None:
            return

        import cv2
        from PIL import Image, ImageTk

        for cam, cam_type, lbl in [
            (self.cam0, getattr(self, "_cam0_type", "cv2"), self.lbl_cam0),
            (self.cam1, getattr(self, "_cam1_type", "cv2"), self.lbl_cam1),
        ]:
            frame = self._grab_frame(cam, cam_type)
            if frame is not None:
                # Boyutlandır (Overlay olmadığı için doğrudan)
                frame = cv2.resize(frame, (420, 320))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                imgtk = ImageTk.PhotoImage(image=img)
                lbl.imgtk = imgtk  # Referansı tut
                lbl.configure(image=imgtk, text="")

        if self.running:
            self.win.after(33, self._update_frames)  # ~30 FPS

    def _close(self):
        """Pencereyi kapat ve kameraları serbest bırak."""
        import cv2
        self.running = False

        if self.cam0 is not None:
            try:
                if getattr(self, "_cam0_type", "") == "picam":
                    self.cam0.stop()
                    self.cam0.close()
                else:
                    self.cam0.release()
            except Exception:
                pass
            self.cam0 = None

        if self.cam1 is not None:
            try:
                if getattr(self, "_cam1_type", "") == "picam":
                    self.cam1.stop()
                    self.cam1.close()
                else:
                    self.cam1.release()
            except Exception:
                pass
            self.cam1 = None

        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None


# ═══════════════════════════════════════════════════════════════
#  Hasta İhtiyaç Sorgulama Paneli
# ═══════════════════════════════════════════════════════════════

# NLP Sabitleri
_STOPWORDS = ["merhaba", "ben", "bir", "şey", "sey", "ııı", "eee", "evet",
              "tamam", "olur", "var", "yani", "iste", "işte", "biliyor", "musunuz", "lütfen"]

_NEGATIF = ["hayır", "hayir", "yok", "değil", "degil", "istemiyorum", "iyiyim", "gerek yok"]

_KATEGORILER = {
    "su istiyorum": {
        "nesneler": ["su", "suistiyorum", "suber", "foysturum", "şu", "so"],
        "eylemler": ["susadım", "içmek", "ver", "istiyorum"]
    },
    "agrim var": {
        "nesneler": ["ağrı", "agri", "acı", "aci", "ari", "ayrı"],
        "eylemler": ["ağrıyor", "acıyor", "sızlıyor", "yarım"]
    },
    "tuvalet": {
        "nesneler": ["tuvalet", "wc", "lavabo", "lazımlık", "ördek", "altım"],
        "eylemler": ["geldi", "gitmek", "yapmam"]
    },
    "usudum": {
        "nesneler": ["soğuk", "battaniye", "örtü"],
        "eylemler": ["üşüdüm", "usudum", "üşüyorum", "titriyorum"]
    },
    "sicak": {
        "nesneler": ["sıcak", "ter", "havalandırma"],
        "eylemler": ["yandım", "terledim", "bunaldım"]
    },
    "hemsire cagirin": {
        "nesneler": ["hemşire", "doktor", "hekim", "yardım"],
        "eylemler": ["çağırın", "baksın", "gelin", "yardımcı"]
    },
    "yastik duzelt": {
        "nesneler": ["yastık", "yatak", "belim", "sırtım"],
        "eylemler": ["düzelt", "kaldır", "dikleştir", "indir"]
    }
}

KAYIT_SURESI = 5


def _turkce_temizle(metin):
    return metin.replace('ı','i').replace('ş','s').replace('ç','c').replace('ğ','g').replace('ö','o').replace('ü','u')


def _niyet_analiz(raw_text):
    """Ham STT metnini analiz ederek niyet döndürür."""
    if not raw_text or raw_text.startswith(("altt", "altyaz")):
        return ("BELIRSIZ", None, "")

    # Negatif kontrol
    if any(neg in raw_text for neg in _NEGATIF):
        return ("TALEP_YOK", None, raw_text)

    # Temizle
    temiz = [k for k in raw_text.split() if k not in _STOPWORDS and len(k) > 2]
    temiz_cumle = " ".join(temiz)

    en_iyi = None
    en_puan = 0

    for ihtiyac, params in _KATEGORILER.items():
        puan = 0
        for nesne in params["nesneler"]:
            if nesne in temiz_cumle:
                puan += 1.5; break
            for k in temiz:
                if difflib.SequenceMatcher(None, k, nesne).ratio() > 0.70:
                    puan += 1.2; break

        for eylem in params["eylemler"]:
            if eylem in temiz_cumle:
                puan += 1.0; break
            for k in temiz:
                if difflib.SequenceMatcher(None, k, eylem).ratio() > 0.70:
                    puan += 0.8; break

        if puan > en_puan:
            en_puan = puan
            en_iyi = ihtiyac

    if en_puan >= 1.0:
        return ("TALEP_VAR", en_iyi, raw_text)
    return ("BELIRSIZ", None, raw_text)


class HastaIhtiyacPanel:
    """
    Hasta İhtiyaç Sorgulama — Toplevel penceresi.
    Arka plan thread'inde mikrofon dinler, NLP ile niyet analiz eder,
    sonuçları canlı log panelinde gösterir.
    """

    def __init__(self, parent):
        self.parent = parent
        self.win = None
        self.running = False
        self._thread = None
        self._ses = None
        self._ekran = None

    def toggle(self):
        if self.win is not None and self.running:
            self._close()
        else:
            self._open()

    def _open(self):
        self.running = True

        self.win = tk.Toplevel(self.parent)
        self.win.title("NeuroSense — Hasta Ihtiyac Sorgulama")
        self.win.geometry("620x480")
        self.win.configure(bg=C.BG)
        self.win.protocol("WM_DELETE_WINDOW", self._close)

        # Üst bar
        bar = tk.Frame(self.win, bg=C.BG, height=44)
        bar.pack(fill=tk.X, padx=16, pady=(10, 6))
        bar.pack_propagate(False)

        tk.Label(bar, text="🎤  Hasta Ihtiyac Sorgulama", font=("Helvetica", 14, "bold"),
                 bg=C.BG, fg=C.WHITE).pack(side=tk.LEFT)

        # Durum etiketi
        self.lbl_status = tk.Label(bar, text="  BASLATILIYOR...  ", font=("Helvetica", 9, "bold"),
                                    bg=C.AMBER, fg="#000", padx=8, pady=2)
        self.lbl_status.pack(side=tk.RIGHT)

        tk.Frame(self.win, bg=C.BORDER, height=1).pack(fill=tk.X, padx=16)

        # Log paneli
        log_frame = tk.Frame(self.win, bg=C.CARD)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=10)

        self.log_text = tk.Text(log_frame, bg=C.CARD, fg=C.TEXT,
                                 font=("Courier", 10), wrap=tk.WORD,
                                 insertbackground=C.TEXT, bd=0,
                                 highlightthickness=0, padx=12, pady=12,
                                 state=tk.DISABLED)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Tag renkleri
        self.log_text.tag_configure("info", foreground=C.CYAN)
        self.log_text.tag_configure("talep", foreground=C.GREEN, font=("Courier", 11, "bold"))
        self.log_text.tag_configure("red", foreground=C.RED)
        self.log_text.tag_configure("dim", foreground=C.DIM)
        self.log_text.tag_configure("yellow", foreground=C.AMBER)

        # Alt bar — son talep göstergesi
        bottom = tk.Frame(self.win, bg=C.BG, height=40)
        bottom.pack(fill=tk.X, padx=16, pady=(0, 10))
        bottom.pack_propagate(False)

        tk.Label(bottom, text="Son Talep:", font=("Helvetica", 10),
                 bg=C.BG, fg=C.TEXT2).pack(side=tk.LEFT)

        self.lbl_last_talep = tk.Label(bottom, text="--", font=("Helvetica", 12, "bold"),
                                        bg=C.BG, fg=C.GREEN)
        self.lbl_last_talep.pack(side=tk.LEFT, padx=(8, 0))

        # Arka plan thread başlat
        self._thread = threading.Thread(target=self._dinleme_dongusu, daemon=True)
        self._thread.start()

    def _log(self, msg, tag="info"):
        """Log paneline mesaj yaz (thread-safe)."""
        if self.win is None or not self.running:
            return
        try:
            self.win.after(0, self._log_insert, msg, tag)
        except Exception:
            pass

    def _log_insert(self, msg, tag):
        if self.log_text is None:
            return
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_status(self, text, bg_color):
        if self.win is None or not self.running:
            return
        try:
            self.win.after(0, lambda: self.lbl_status.configure(text=f"  {text}  ", bg=bg_color))
        except Exception:
            pass

    def _set_talep(self, text):
        if self.win is None or not self.running:
            return
        try:
            self.win.after(0, lambda: self.lbl_last_talep.configure(text=text))
        except Exception:
            pass

    def _dinleme_dongusu(self):
        """Arka plan thread'i: modülleri yükle ve sürekli dinle."""
        # Eger mock mode (sunum simulasyonu) aciksa sadece ekrana animasyon yansit (Model ve mikrofon by-pass)
        if getattr(self, "mock_mode", False):
            self._set_status("AI YUKLENIYOR", C.AMBER)
            self._log("Asistan Modeli Yükleniyor...", "yellow")
            time.sleep(2)
            self._set_status("DINLENIYOR", C.CYAN)
            self._log("Dil Modeli Hazır. Hasta Dinleniyor.", "info")
            self._log("\n>>> YARDIM SERVISI AKTIF", "info")
            
            while self.running:
                self._set_status("DINLENIYOR", C.CYAN)
                self._log("\n[Mikrofon] Dinleniyor...", "dim")
                time.sleep(3)
                
                self._set_status("ANALIZ", C.AMBER)
                time.sleep(2)
                
                self._log("Hastanın söylediği: 'bana biraz su verir misiniz'", "dim")
                self._log("Hasta Talebi: SU", "talep")
                self._set_status("TALEP: SU", C.GREEN)
                self._set_talep("SU")
                
                time.sleep(5)
                self._set_status("PASIF DINLEME", C.BLUE)
                
                time.sleep(4)
            return

        # v3_gks path
        gks_dir = str(Path(__file__).resolve().parent.parent / "v3_gks")
        if gks_dir not in sys.path:
            sys.path.append(gks_dir)

        # Ana modüllerle (EkranKontrol, SesAnaliz) çakışmamak için
        # mikrofon ve STT işlemlerini burada izole ve bağımsız yapıyoruz.
        import subprocess
        import os

        # 1. Whisper modelini direkt yükle
        self._log("Dil Modeli (AI) Yükleniyor...", "yellow")
        self._set_status("AI YUKLENIYOR", C.AMBER)

        try:
            from faster_whisper import WhisperModel
            # Stabiliteyi artırmak için en büyük ve isabetli Whisper modelini kullanıyoruz
            model = WhisperModel("Systran/faster-whisper-large-v3", device="cpu", compute_type="int8")
            self._log("Büyük Dil Modeli (Large-V3) Hazır.", "info")
        except Exception as e:
            self._log(f"Sistem Hatası: {e}", "red")
            self._set_status("HATA", C.RED)
            return

        # Sadece arayüzde göster (LCD çakışmasını önlemek için EkranKontrol KULLANILMAZ)
        self._log("\n>>> YARDIM SERVISI AKTIF", "info")
        self._set_status("DINLENIYOR", C.CYAN)


        # Sonsuz dinleme döngüsü
        while self.running:
            try:
                # ==========================================
                # 1. Ses Kaydı (ALSA arecord ile izole)
                # ==========================================
                self._set_status("DINLENIYOR", C.CYAN)
                
                audio_path = "/tmp/hasta_ihtiyac_panel.wav"
                cmd = ['arecord', '-D', 'hw:3,0', '-d', str(KAYIT_SURESI), '-f', 'S16_LE', '-r', '16000', '-c', '1', audio_path]
                
                result = subprocess.run(cmd, capture_output=True, timeout=KAYIT_SURESI + 5)
                
                if result.returncode != 0 or not os.path.exists(audio_path) or os.path.getsize(audio_path) < 100:
                    self._set_status("MIKROFON HATASI", C.RED)
                    time.sleep(2)
                    continue

                self._set_status("ANALIZ", C.AMBER)

                # ==========================================
                # 2. STT (Whisper ile metne çevir)
                # ==========================================
                try:
                    segments, info = model.transcribe(
                        audio_path, 
                        beam_size=7, # Doğruluğu artırmak için aramayı genişlettik
                        language="tr",
                        initial_prompt="su, ilaç, ağrı, tuvalet, doktor, bulantı, üşüyorum.",
                        vad_filter=True,
                        no_speech_threshold=0.4, 
                        log_prob_threshold=-1.0
                    )
                    raw_text = "".join([s.text for s in list(segments)]).lower().strip()
                except Exception:
                    time.sleep(2)
                    continue

                # ==========================================
                # 3. Niyet Analizi ve Sonuçlar
                # ==========================================
                sonuc, niyet, ham = _niyet_analiz(raw_text)

                if sonuc == "TALEP_YOK":
                    self._log(f"Hastanın söylediği: '{ham}'", "dim")
                    self._log("Hasta Talebi: YOK", "yellow")
                    self._set_status("TALEP YOK", C.AMBER)
                    self._set_talep("Talep Yok")
                    
                    self._set_status("PASIF DINLEME", C.BLUE)

                elif sonuc == "TALEP_VAR" and niyet:
                    latin = _turkce_temizle(niyet).upper()
                    
                    self._log(f"Hastanın söylediği: '{ham}'", "dim")
                    self._log(f"Hasta Talebi: {latin}", "talep")
                    self._set_status(f"TALEP: {latin[:12]}", C.GREEN)
                    self._set_talep(latin)
                    
                    time.sleep(3)
                    self._set_status("PASIF DINLEME", C.BLUE)

                elif sonuc == "BELIRSIZ":
                    if ham:
                        self._log(f"Hastanın söylediği: '{ham}'", "dim")
                        self._log("Durum: Anlaşılamadı / Gürültü", "dim")
                    self._set_status("PASIF DINLEME", C.BLUE)

                time.sleep(1)

            except Exception as e:
                self._log(f"Beklenmeyen hata: {e}", "red")
                time.sleep(3)

        self._log("Yardım Servisi kapatıldı.", "dim")

    def _close(self):
        self.running = False
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None


# ═══════════════════════════════════════════════════════════════
#  Ana Arayüz Sınıfı
# ═══════════════════════════════════════════════════════════════
class ScenarioGUI:

    def __init__(self, root, title="NeuroSense GKS", past_history=None,
                 scenario_config=None):
        self.root = root
        self.root.title(title)
        self.root.geometry("1060x640")
        self.root.minsize(900, 560)
        self.root.configure(bg=C.BG)

        self.past_history = list(past_history) if past_history else []
        self.state_source_fn = None
        self.scenario_config = scenario_config or {}
        self.camera_viewer = CameraViewer(root, scenario_config=self.scenario_config)
        self.ihtiyac_panel = HastaIhtiyacPanel(root)

        self._build()

    def _build(self):
        # ─── Üst Bar ─────────────────────────────────────────
        top = tk.Frame(self.root, bg=C.BG, height=48)
        top.pack(fill=tk.X, padx=32, pady=(18, 0))
        top.pack_propagate(False)

        # Logo
        tk.Label(top, text="◆", font=("Helvetica", 18), bg=C.BG,
                 fg=C.BLUE).pack(side=tk.LEFT)
        tk.Label(top, text="  NeuroSense GKS", font=("Helvetica", 17, "bold"),
                 bg=C.BG, fg=C.WHITE).pack(side=tk.LEFT)

        # Toplam Skor (sağ üst)
        self.total_frame = tk.Frame(top, bg=C.BG)
        self.total_frame.pack(side=tk.RIGHT)

        self.lbl_severity = tk.Label(self.total_frame, text="  HAFİF  ",
                                     font=("Helvetica", 9, "bold"),
                                     bg=C.GREEN, fg="#000000", padx=8, pady=2)
        self.lbl_severity.pack(side=tk.RIGHT, padx=(8, 0))

        self.lbl_total_max = tk.Label(self.total_frame, text="/15",
                                      font=("Helvetica", 14), bg=C.BG,
                                      fg=C.DIM)
        self.lbl_total_max.pack(side=tk.RIGHT)

        self.lbl_total_val = tk.Label(self.total_frame, text="3",
                                      font=("Helvetica", 32, "bold"),
                                      bg=C.BG, fg=C.WHITE)
        self.lbl_total_val.pack(side=tk.RIGHT)

        # Kamera Butonu
        self.btn_cam = tk.Button(
            top, text="  📷 Kameralar  ", font=("Helvetica", 10, "bold"),
            bg=C.CARD, fg=C.TEXT, activebackground=C.CARD_HOVER,
            activeforeground=C.WHITE, bd=0, padx=14, pady=4,
            highlightbackground=C.BORDER, highlightthickness=1,
            command=self.camera_viewer.toggle,
        )
        self.btn_cam.pack(side=tk.RIGHT, padx=(0, 16))

        # Hasta İhtiyaç Butonu
        self.btn_ihtiyac = tk.Button(
            top, text="  \U0001f3a4 Hasta Ihtiyac  ", font=("Helvetica", 10, "bold"),
            bg="#1a2744", fg=C.CYAN, activebackground=C.CARD_HOVER,
            activeforeground=C.WHITE, bd=0, padx=14, pady=4,
            highlightbackground=C.BORDER, highlightthickness=1,
            command=self.ihtiyac_panel.toggle,
        )
        self.btn_ihtiyac.pack(side=tk.RIGHT, padx=(0, 8))

        # ─── Adım Göstergesi ──────────────────────────────────
        self.steps = StepIndicator(self.root, bg=C.BG)
        self.steps.pack(fill=tk.X, padx=32, pady=(12, 6))

        # ─── İnce çizgi ──────────────────────────────────────
        tk.Frame(self.root, bg=C.BORDER, height=1).pack(fill=tk.X, padx=32)

        # ─── İçerik Alanı ────────────────────────────────────
        body = tk.Frame(self.root, bg=C.BG)
        body.pack(fill=tk.BOTH, expand=True, padx=32, pady=16)
        body.columnconfigure(0, weight=0, minsize=180)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ─── Sol: Gauge Kartları ──────────────────────────────
        left = tk.Frame(body, bg=C.BG)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 16))

        gauge_size = 130

        self.gauge_goz = ArcGauge(left, gauge_size, "Göz (E)", 4, C.CYAN)
        self.gauge_goz.pack(pady=(0, 6))

        self.gauge_motor = ArcGauge(left, gauge_size, "Motor (M)", 6, C.PURPLE)
        self.gauge_motor.pack(pady=(6, 6))

        self.gauge_sozel = ArcGauge(left, gauge_size, "Sözel (V)", 5, C.AMBER)
        self.gauge_sozel.pack(pady=(6, 0))

        # ─── Sağ: Grafik Kartı ────────────────────────────────
        right = tk.Frame(body, bg=C.CARD, highlightbackground=C.BORDER,
                         highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew")

        # Grafik başlık
        gh = tk.Frame(right, bg=C.CARD, padx=24, pady=14)
        gh.pack(fill=tk.X)
        tk.Label(gh, text="Test Geçmişi", font=("Helvetica", 14, "bold"),
                 bg=C.CARD, fg=C.WHITE).pack(side=tk.LEFT)
        tk.Label(gh, text="Skor trendi", font=("Helvetica", 10),
                 bg=C.CARD, fg=C.DIM).pack(side=tk.RIGHT)

        tk.Frame(right, bg=C.BORDER, height=1).pack(fill=tk.X, padx=20)

        self.graph_canvas = tk.Canvas(right, bg=C.CARD, highlightthickness=0)
        self.graph_canvas.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
        self.graph_canvas.bind("<Configure>", lambda e: self._draw_graph())

    # ──────────────────────────────────────────────────────────
    #  Grafik
    # ──────────────────────────────────────────────────────────
    def _draw_graph(self):
        cv = self.graph_canvas
        cv.delete("all")

        cw = cv.winfo_width()
        ch = cv.winfo_height()
        if cw < 80 or ch < 80:
            return

        pl, pr, pt, pb = 52, 28, 20, 38
        gw = cw - pl - pr
        gh_ = ch - pt - pb

        # Bölge bantları (arka plan)
        zones = [
            (0,  8,  "#120f10"),
            (8,  12, "#14130e"),
            (12, 15, "#0e1510"),
        ]
        for lo, hi, zc in zones:
            yt = pt + gh_ - (hi / 15.0) * gh_
            yb = pt + gh_ - (lo / 15.0) * gh_
            cv.create_rectangle(pl, yt, pl + gw, yb, fill=zc, outline="")

        # Grid çizgileri ve Y etiketleri
        for v in [0, 3, 5, 8, 10, 12, 15]:
            y = pt + gh_ - (v / 15.0) * gh_
            cv.create_line(pl, y, pl + gw, y, fill=C.BORDER, dash=(1, 4))
            cv.create_text(pl - 14, y, text=str(v), fill=C.DIM,
                           font=("Helvetica", 9), anchor="e")

        # Eksenler
        cv.create_line(pl, pt, pl, pt + gh_, fill=C.BORDER_LT, width=1)
        cv.create_line(pl, pt + gh_, pl + gw, pt + gh_, fill=C.BORDER_LT, width=1)

        data = self.past_history
        if not data:
            cv.create_text(pl + gw // 2, pt + gh_ // 2,
                           text="Henüz geçmiş veri yok",
                           fill=C.DIM, font=("Helvetica", 12))
            return

        n = len(data)
        if n == 1:
            xs = [pl + gw / 2]
        else:
            xs = [pl + (i / (n - 1)) * gw for i in range(n)]

        pts = []
        for i, val in enumerate(data):
            x = xs[i]
            y = pt + gh_ - (val / 15.0) * gh_
            pts.append((x, y))

        # Dolgu alanı
        if len(pts) >= 2:
            fc = [(pts[0][0], pt + gh_)]
            fc.extend(pts)
            fc.append((pts[-1][0], pt + gh_))
            flat = []
            for c in fc:
                flat.extend(c)
            cv.create_polygon(flat, fill=C.GRAPH_FILL, outline="", smooth=True)

        # Çizgi
        if len(pts) >= 2:
            flat = []
            for p in pts:
                flat.extend(p)
            cv.create_line(flat, fill=C.GRAPH_LINE, width=3, smooth=True,
                           capstyle=tk.ROUND, joinstyle=tk.ROUND)

        # Noktalar
        for i, ((x, y), val) in enumerate(zip(pts, data)):
            # Halo
            cv.create_oval(x - 8, y - 8, x + 8, y + 8,
                           fill="", outline=C.BLUE_DIM, width=2)
            # İç daire
            cv.create_oval(x - 4, y - 4, x + 4, y + 4,
                           fill=C.GRAPH_DOT, outline="")
            # Değer
            cv.create_text(x, y - 16, text=str(val), fill=C.WHITE,
                           font=("Helvetica", 10, "bold"))
            # X etiketi
            cv.create_text(x, pt + gh_ + 18, text=f"T{i+1}",
                           fill=C.DIM, font=("Helvetica", 9))

    # ──────────────────────────────────────────────────────────
    #  Güncelleme
    # ──────────────────────────────────────────────────────────
    def update_state(self, durum, goz, motor, sozel):
        toplam = goz + motor + sozel

        # Adım göstergesi
        self.steps.set_stage(durum)

        # Gauge'lar
        self.gauge_goz.set_value(goz)
        self.gauge_motor.set_value(motor)
        self.gauge_sozel.set_value(sozel)

        # Toplam skor
        self.lbl_total_val.config(text=str(toplam), fg=severity_color(toplam))

        # Severity badge
        sv = severity_text(toplam)
        sc = severity_color(toplam)
        self.lbl_severity.config(text=f"  {sv}  ", bg=sc,
                                  fg="#000000" if sc != C.RED else C.WHITE)

    def add_history(self, score):
        self.past_history.append(score)
        if len(self.past_history) > 10:
            self.past_history.pop(0)
        self._draw_graph()

    def _poll_state(self):
        if self.state_source_fn:
            try:
                durum, goz, motor, sozel = self.state_source_fn()
                self.update_state(durum, goz, motor, sozel)
            except Exception:
                pass
        self.root.after(150, self._poll_state)


# ═══════════════════════════════════════════════════════════════
#  Başlatma
# ═══════════════════════════════════════════════════════════════
def start_gui(title="NeuroSense GKS", past_history=None, state_source_fn=None,
              scenario_config=None):
    root = tk.Tk()
    app = ScenarioGUI(root, title=title, past_history=past_history,
                      scenario_config=scenario_config)
    app.state_source_fn = state_source_fn
    app._poll_state()
    return root, app

