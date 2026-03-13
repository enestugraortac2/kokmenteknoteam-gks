#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 - TAM SÜRÜM (Monolitik Tek Dosya)
=====================================================
Tüm modüller tek dosyada. Bilinen tüm hatalar giderilmiştir:
 - LCD: Kullanıcının çalışan kodu (D25/D24, 24MHz) birebir kullanıldı
 - SES: Otomatik ses cihazı bulma (aplay -l ile tarama)
 - KAMERA: AI işlenmiş görüntü SSH masaüstüne yansıtılıyor
 - SERVO: SG5010 pulse width tam ayarlanmış
"""

import os, sys, time, logging, threading, subprocess, random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ============================================================================
# LOG & YOLLAR
# ============================================================================
log = logging.getLogger("GKS")
log.setLevel(logging.INFO)
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
log.addHandler(_h)

# HDMI ile monitöre bağlı - DISPLAY her zaman :0
os.environ["DISPLAY"] = ":0"

# X11 yetkilendirme
try:
    subprocess.run(["xhost", "+local:"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
except:
    pass

ROOT  = Path("/home/kokmenteknoteam/Desktop/VS_GKS_Proje/v2_gks")
MODELS = ROOT / "models"

# Models klasörü v2_gks içinde yoksa üst klasöre bak
if not MODELS.exists():
    MODELS = ROOT.parent / "models"
    log.info(f"Models üst klasörden alınıyor: {MODELS}")

# OpenCV GUI desteği kontrolü
def cv2_gui_var_mi():
    """opencv-python-headless mı yoksa tam sürüm mü kurulu?"""
    try:
        test_img = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.imshow("_test_", test_img)
        cv2.waitKey(1)
        cv2.destroyWindow("_test_")
        return True
    except:
        return False

GUI_DESTEGI = cv2_gui_var_mi()
if GUI_DESTEGI:
    log.info("OpenCV GUI desteği VAR - Kamera pencereleri açılacak ✓")
else:
    log.warning("="*60)
    log.warning("OpenCV GUI desteği YOK! (opencv-python-headless kurulu)")
    log.warning("Kamera pencereleri açılamaz. Düzeltmek için:")
    log.warning("  pip3 uninstall opencv-python-headless")
    log.warning("  pip3 install opencv-python")
    log.warning("="*60)

# Models klasoru v2_gks içinde yoksa ust klasore bak
if not MODELS.exists():
    MODELS = ROOT.parent / "models"
    log.info(f"Models dizini v2_gks'te bulunamadı, üst klasör kullanılıyor: {MODELS}")

# ============================================================================
# YARDIMCI: Ses cihazı otomatik bulma
# ============================================================================
def bul_ses_cihazi():
    """aplay -l çıktısını tarayarak ilk çalışan ses kartını bulur."""
    cihazlar = []
    try:
        out = subprocess.check_output(["aplay", "-l"], stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            if line.startswith("card"):
                # "card 0: ..." -> card_no=0, device=0
                parts = line.split(":")
                card_no = parts[0].strip().replace("card ", "")
                # device satırı "device 0:" gibi
                dev_part = [p for p in parts if "device" in p.lower()]
                dev_no = "0"
                if dev_part:
                    dev_no = dev_part[0].strip().split()[1].replace(",","")
                cihazlar.append(f"plughw:{card_no},{dev_no}")
    except:
        pass
    if not cihazlar:
        cihazlar = ["default"]
    return cihazlar

def bul_mikrofon():
    """arecord -l çıktısını tarayarak ilk çalışan mikrofonu bulur."""
    try:
        out = subprocess.check_output(["arecord", "-l"], stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            if line.startswith("card"):
                parts = line.split(":")
                card_no = parts[0].strip().replace("card ", "")
                return f"plughw:{card_no},0"
    except:
        pass
    return "default"


# ============================================================================
# 1. KAMERA YÖNETİCİSİ
# ============================================================================
class Kamera:
    def __init__(self, cam_id=0):
        self.cam_id = cam_id
        self._cam = None
        self._is_picam = False

    def ac(self):
        if self._cam is not None:
            return True
        try:
            from picamera2 import Picamera2
            self._cam = Picamera2(self.cam_id)
            cfg = self._cam.create_preview_configuration(
                main={"format": "RGB888", "size": (640, 480)})
            self._cam.configure(cfg)
            self._cam.start()
            self._is_picam = True
            time.sleep(0.5)  # Kameranın ısınması için
            log.info(f"Kamera {self.cam_id} (Picamera2) HAZIR ✓")
            return True
        except Exception as e:
            log.warning(f"Picamera2[{self.cam_id}] başarısız: {e}")
            try:
                self._cam = cv2.VideoCapture(self.cam_id)
                if self._cam.isOpened():
                    self._is_picam = False
                    log.info(f"Kamera {self.cam_id} (CV2) HAZIR ✓")
                    return True
            except:
                pass
            self._cam = None
            return False

    def oku(self):
        if self._cam is None:
            return None
        try:
            if self._is_picam:
                return cv2.cvtColor(self._cam.capture_array(), cv2.COLOR_RGB2BGR)
            else:
                ok, f = self._cam.read()
                return f if ok else None
        except:
            return None

    def kapat(self):
        if self._cam is None:
            return
        log.info(f"Kamera {self.cam_id} kapatılıyor...")
        try:
            if self._is_picam:
                self._cam.stop()
                self._cam.close()
            else:
                self._cam.release()
        except:
            pass
        self._cam = None


# ============================================================================
# 2. GÖZ ANALİZİ (Dlib) - Yüze landmark çizer, sonucu frame'e yansıtır
# ============================================================================
class GozAnaliz:
    EAR_ESIK = 0.22

    def __init__(self):
        self.detector = None
        self.predictor = None

    def yukle(self):
        if self.predictor is not None:
            return True
        try:
            import dlib
            # Birden fazla konuma bak
            for p in [MODELS / "shape_predictor_68_face_landmarks.dat",
                       ROOT.parent / "models" / "shape_predictor_68_face_landmarks.dat"]:
                if p.exists():
                    self.detector = dlib.get_frontal_face_detector()
                    self.predictor = dlib.shape_predictor(str(p))
                    log.info(f"Dlib yüklendi ✓ ({p.name})")
                    return True
            log.error("Dlib .dat dosyası bulunamadı! models/ klasörüne koyun.")
            return False
        except Exception as e:
            log.error(f"Dlib yükleme hatası: {e}")
            return False

    def analiz_et(self, frame):
        """Göz açık mı kontrol eder. Frame üzerine landmark çizer. (ear, acik_mi) döner."""
        if self.predictor is None or frame is None:
            return 0.0, False

        try:
            from scipy.spatial import distance as dist
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.detector(gray, 0)
            if not faces:
                # Yüz bulunamadı - frame üzerine yaz
                cv2.putText(frame, "YUZ YOK", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                return 0.0, False

            face = faces[0]
            lm = self.predictor(gray, face)

            # Yüz çerçevesi çiz
            x1, y1, x2, y2 = face.left(), face.top(), face.right(), face.bottom()
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

            # 68 landmark noktasını çiz
            for i in range(68):
                px, py = lm.part(i).x, lm.part(i).y
                color = (255, 255, 0) if 36 <= i < 48 else (0, 200, 200)  # Göz noktaları sarı
                cv2.circle(frame, (px, py), 2, color, -1)

            # EAR hesapla
            def ear(idx):
                pts = [(lm.part(i).x, lm.part(i).y) for i in idx]
                A = dist.euclidean(pts[1], pts[5])
                B = dist.euclidean(pts[2], pts[4])
                C = dist.euclidean(pts[0], pts[3])
                return (A + B) / (2.0 * C)

            left_ear = ear(range(36, 42))
            right_ear = ear(range(42, 48))
            avg_ear = (left_ear + right_ear) / 2.0
            acik = avg_ear > self.EAR_ESIK

            # Sonucu frame'e yaz
            renk = (0, 255, 0) if acik else (0, 0, 255)
            durum = "GOZ ACIK" if acik else "GOZ KAPALI"
            cv2.putText(frame, f"EAR: {avg_ear:.2f} | {durum}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, renk, 2)

            return avg_ear, acik
        except Exception as e:
            cv2.putText(frame, f"HATA: {e}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            return 0.0, False


# ============================================================================
# 3. MOTOR ANALİZİ (YOLOv8-pose) - İskelet çizer, hareketi frame'e yansıtır
# ============================================================================
class MotorAnaliz:
    HAREKET_ESIK = 15.0

    def __init__(self):
        self.model = None

    def yukle(self):
        if self.model is not None:
            return True
        try:
            from ultralytics import YOLO
            yolo_p = str(MODELS / "yolov8n-pose.pt")
            self.model = YOLO(yolo_p)
            log.info("YOLO-pose yüklendi ✓")
            return True
        except Exception as e:
            log.error(f"YOLO yükleme hatası: {e}")
            return False

    def analiz_et(self, frame):
        """YOLO ile iskelet çıkart, frame üzerine çiz, keypoints döndür."""
        if self.model is None or frame is None:
            return None
        try:
            results = self.model(frame, verbose=False, max_det=1, conf=0.4)
            if not results or len(results[0].boxes) == 0:
                cv2.putText(frame, "KISI YOK", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                return None

            # YOLO annotated frame'i al (iskelet otomatik çizilir)
            ann = results[0].plot()
            # Orijinal frame'i annotated ile değiştir
            frame[:] = ann

            kpts = results[0].keypoints.data[0].cpu().numpy()
            return kpts
        except:
            return None

    def hareket_var_mi(self, eski, yeni):
        if eski is None or yeni is None:
            return False
        try:
            toplam, sayac = 0, 0
            for j in [9, 10]:  # Bilekler
                if len(eski) > j and len(yeni) > j:
                    if eski[j][2] > 0.3 and yeni[j][2] > 0.3:
                        dx = yeni[j][0] - eski[j][0]
                        dy = yeni[j][1] - eski[j][1]
                        toplam += (dx**2 + dy**2)**0.5
                        sayac += 1
            return (toplam / sayac) > self.HAREKET_ESIK if sayac > 0 else False
        except:
            return False


# ============================================================================
# 4. SES MODÜLLERİ (Piper TTS + Whisper STT + Sentence NLP)
# ============================================================================
SORULAR = {
    "YER":   {"q": "Hangi binadayız?",  "a": ["hastane","ambulans","ev","oda","klinik"]},
    "ZAMAN": {"q": "Hangi yıldayız?",   "a": ["iki bin","yirmi dört","yirmi beş","sabah"]},
}

class SesMotoru:
    def __init__(self):
        self.whisper = None
        self.nlp = None
        self.piper_bin = None
        self.piper_model = None
        self.hoparlor = "default"
        self.mikrofon = "default"

        # Piper binary'sini birden fazla konumda ara
        olasi_piper = [
            ROOT / "piper" / "piper",
            ROOT.parent / "piper" / "piper",
            Path("/home/kokmenteknoteam/piper/piper"),
            Path("/usr/local/bin/piper"),
            Path("/opt/piper/piper"),
        ]
        olasi_model = [
            ROOT / "piper" / "tr_TR-fahrettin-medium.onnx",
            ROOT.parent / "piper" / "tr_TR-fahrettin-medium.onnx",
            Path("/home/kokmenteknoteam/piper/tr_TR-fahrettin-medium.onnx"),
        ]
        for p in olasi_piper:
            if p.exists():
                self.piper_bin = p
                log.info(f"Piper bulundu: {p}")
                break
        for p in olasi_model:
            if p.exists():
                self.piper_model = p
                log.info(f"Piper model bulundu: {p}")
                break

        if self.piper_bin is None:
            # which komutu ile de dene
            try:
                result = subprocess.run(["which", "piper"], capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    self.piper_bin = Path(result.stdout.strip())
                    log.info(f"Piper (which ile) bulundu: {self.piper_bin}")
            except:
                pass

        if self.piper_bin is None:
            log.warning("PIPER BULUNAMADI! espeak-ng fallback kullanılacak.")

    def yukle(self):
        # 1) Ses cihazlarını bul
        cihazlar = bul_ses_cihazi()
        self.hoparlor = cihazlar[0] if cihazlar else "default"
        self.mikrofon = bul_mikrofon()
        log.info(f"Ses Çıkış: {self.hoparlor} | Mikrofon: {self.mikrofon}")

        # 2) Whisper
        try:
            from faster_whisper import WhisperModel
            w_dir = MODELS / "whisper-base"
            if w_dir.exists():
                self.whisper = WhisperModel(str(w_dir), device="cpu", compute_type="int8")
            else:
                self.whisper = WhisperModel("base", device="cpu", compute_type="int8")
            log.info("Whisper yüklendi ✓")
        except Exception as e:
            log.error(f"Whisper hatası: {e}")

        # 3) NLP
        try:
            from sentence_transformers import SentenceTransformer
            nlp_dir = MODELS / "sentence-transformers" / "paraphrase-multilingual-MiniLM-L12-v2"
            if nlp_dir.exists():
                self.nlp = SentenceTransformer(str(nlp_dir))
            else:
                self.nlp = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            log.info("NLP yüklendi ✓")
        except Exception as e:
            log.error(f"NLP hatası: {e}")

    def konus(self, metin):
        """Piper TTS ile konuşma. Bulamazsa espeak-ng fallback."""
        log.info(f"🔊 ROBOT: '{metin}'")

        # Piper varsa onu kullan
        if self.piper_bin and self.piper_bin.exists() and self.piper_model and self.piper_model.exists():
            cihazlar = bul_ses_cihazi()
            for cihaz in cihazlar:
                try:
                    cmd = (f'echo "{metin}" | {self.piper_bin} --model {self.piper_model} '
                           f'--output_raw | aplay -D {cihaz} -r 22050 -f S16_LE -t raw')
                    r = subprocess.run(cmd, shell=True, timeout=15,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    if r.returncode == 0:
                        log.info(f"  Ses çıktı (Piper + {cihaz}) ✓")
                        return
                    else:
                        log.warning(f"  {cihaz} başarısız: {r.stderr.decode(errors='ignore')[:80]}")
                except subprocess.TimeoutExpired:
                    log.warning(f"  {cihaz} zaman aşımı")
                except Exception as e:
                    log.warning(f"  {cihaz} hata: {e}")

        # Piper yoksa espeak-ng kullan (Pi'de genellikle yüklüdür)
        log.info("  Piper kullanılamıyor, espeak-ng deneniyor...")
        try:
            subprocess.run(["espeak-ng", "-v", "tr", metin], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            log.info("  Ses çıktı (espeak-ng) ✓")
            return
        except:
            pass

        # espeak-ng de yoksa espeak dene
        try:
            subprocess.run(["espeak", "-v", "tr", metin], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            log.info("  Ses çıktı (espeak) ✓")
            return
        except:
            pass

        log.error("HİÇBİR TTS MOTORUNDAN SES ÇIKMADI!")

    def dinle(self, sure=5):
        """Mikrofondan kayıt yap, metne çevir."""
        wav = "/tmp/gks_mic.wav"
        try:
            subprocess.run(["arecord", "-D", self.mikrofon,
                            "-f", "S16_LE", "-c", "1", "-r", "16000",
                            "-d", str(sure), wav],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=sure+5)
        except:
            pass

        if not os.path.exists(wav) or self.whisper is None:
            return ""
        try:
            segs, _ = self.whisper.transcribe(wav, language="tr", beam_size=5)
            metin = " ".join([s.text for s in list(segs)]).strip()
            log.info(f"🗣️ DUYULAN: '{metin}'")
            return metin
        except:
            return ""

    def test_et(self, tip):
        soru = SORULAR[tip]["q"]
        dogrular = SORULAR[tip]["a"]

        self.konus(soru)
        metin = self.dinle(5)

        if not metin or self.nlp is None:
            return 1

        try:
            from sentence_transformers import util
            e_m = self.nlp.encode(metin.lower(), normalize_embeddings=True)
            e_a = self.nlp.encode([a.lower() for a in dogrular], normalize_embeddings=True)
            skor = max(util.cos_sim(e_m, e_a)[0]).item()
            return 5 if skor > 0.45 else 3
        except:
            return 1


# ============================================================================
# 5. LCD EKRAN (ILI9341 SPI) - Kullanıcının çalışan kodu temel alındı
# ============================================================================
class LCDEkran:
    def __init__(self):
        self.disp = None
        self.w = 320
        self.h = 240
        self.font = None
        # Skorlar
        self.g = 1; self.m = 1; self.s = 1
        self.hr = 75; self.spo2 = 98
        self.durum = "BASLATILIYOR"

    def baslat(self):
        """Kullanıcının birebir çalışan kodu ile aynı init."""
        try:
            import board, busio, digitalio
            from adafruit_rgb_display import ili9341 as ili

            # Kullanıcının çalışan pin tanımları:
            dc_pin = digitalio.DigitalInOut(board.D25)
            reset_pin = digitalio.DigitalInOut(board.D24)
            spi = busio.SPI(board.SCK, board.MOSI, board.MISO)

            self.disp = ili.ILI9341(spi, cs=None, dc=dc_pin, rst=reset_pin,
                                     baudrate=24000000)
            self.w = self.disp.width
            self.h = self.disp.height
            log.info(f"LCD HAZIR ({self.w}x{self.h}) ✓")

            # İlk test - Mavi ekran bas (çalıştığı kesin)
            test_img = Image.new("RGB", (self.w, self.h), (0, 0, 128))
            self.disp.image(test_img)
            time.sleep(0.3)
        except Exception as e:
            log.error(f"LCD başlatma hatası: {e}")
            self.disp = None

    def ciz(self):
        if self.disp is None:
            return
        try:
            W, H = self.w, self.h
            img = Image.new("RGB", (W, H), (10, 10, 20))
            d = ImageDraw.Draw(img)

            # Font - Raspberry Pi'de mevcut olanı dene
            try:
                fn = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
                fn_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
                fn_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            except:
                fn = fn_b = fn_s = ImageFont.load_default()

            # ── Başlık Çubuğu ──
            d.rectangle((0, 0, W, 28), fill=(25, 25, 60))
            d.text((8, 4), "NEUROSENSE GKS", font=fn, fill=(255, 255, 255))

            # ── Nabız Kutusu ──
            d.rectangle((5, 33, W//2-3, 70), outline=(80, 80, 200), width=1)
            hr_clr = (255, 60, 60) if (self.hr > 100 or self.hr < 55) else (80, 255, 80)
            d.text((10, 36), "HR", font=fn_s, fill=(180, 180, 255))
            d.text((10, 50), f"{self.hr} bpm", font=fn, fill=hr_clr)

            d.rectangle((W//2+3, 33, W-5, 70), outline=(80, 200, 200), width=1)
            d.text((W//2+8, 36), "SpO2", font=fn_s, fill=(180, 255, 255))
            d.text((W//2+8, 50), f"{self.spo2}%", font=fn, fill=(100, 220, 255))

            # ── Kategorik Skor Kutuları ──
            col_w = (W - 20) // 3

            # Göz (E)
            x0 = 5
            d.rectangle((x0, 76, x0+col_w, 130), outline=(120, 120, 255), width=1,
                         fill=(20, 20, 50))
            d.text((x0+4, 78), "GOZ (E)", font=fn_s, fill=(150, 150, 255))
            d.text((x0+4, 95), f" {self.g}/4", font=fn_b, fill=(255, 255, 255))

            # Motor (M)
            x1 = x0 + col_w + 5
            d.rectangle((x1, 76, x1+col_w, 130), outline=(120, 255, 120), width=1,
                         fill=(20, 50, 20))
            d.text((x1+4, 78), "MOTOR(M)", font=fn_s, fill=(150, 255, 150))
            d.text((x1+4, 95), f" {self.m}/6", font=fn_b, fill=(255, 255, 255))

            # Sözel (V)
            x2 = x1 + col_w + 5
            d.rectangle((x2, 76, x2+col_w, 130), outline=(255, 120, 120), width=1,
                         fill=(50, 20, 20))
            d.text((x2+4, 78), "SOZEL(V)", font=fn_s, fill=(255, 150, 150))
            d.text((x2+4, 95), f" {self.s}/5", font=fn_b, fill=(255, 255, 255))

            # ── TOPLAM SKOR ──
            total = self.g + self.m + self.s
            if total <= 8:
                tc = (255, 40, 40);  tl = "AGIR KOMA"
            elif total <= 12:
                tc = (255, 180, 0);  tl = "ORTA"
            else:
                tc = (0, 230, 0);    tl = "NORMAL"

            d.rectangle((5, 136, W-5, 185), fill=(tc[0]//8, tc[1]//8, tc[2]//8),
                         outline=tc, width=2)
            d.text((12, 140), f"TOPLAM: {total}/15", font=fn_b, fill=tc)
            d.text((12, 163), tl, font=fn, fill=tc)

            # ── Alt Durum Çubuğu ──
            d.rectangle((0, H-24, W, H), fill=(15, 15, 15))
            durum_kisa = self.durum[:30]  # Ekrana sığsın
            d.text((8, H-20), f"> {durum_kisa}", font=fn_s, fill=(160, 160, 160))

            self.disp.image(img)
        except Exception as e:
            log.error(f"LCD çizim hatası: {e}")


# ============================================================================
# 6. NABIZ SİMÜLASYONU
# ============================================================================
class Nabiz:
    def __init__(self):
        self.hr = 75
        self.spo2 = 98
        def _sim():
            while True:
                self.hr = 72 + random.randint(-3, 5)
                self.spo2 = 97 + random.randint(-1, 1)
                time.sleep(2)
        threading.Thread(target=_sim, daemon=True).start()


# ============================================================================
# 7. ANA UYGULAMA - ORCHESTRATOR
# ============================================================================
class GKSApp:
    def __init__(self):
        self.cam_goz   = Kamera(1)
        self.cam_motor = Kamera(0)
        self.goz   = GozAnaliz()
        self.motor = MotorAnaliz()
        self.ses   = SesMotoru()
        self.lcd   = LCDEkran()
        self.nabiz = Nabiz()

    # ── Kamera görüntüsünü monitörde göster ──
    def goster(self, pencere, frame):
        """Frame'i HDMI monitörde pencere olarak gösterir."""
        if frame is None or not GUI_DESTEGI:
            return
        try:
            cv2.namedWindow(pencere, cv2.WINDOW_AUTOSIZE)
            cv2.imshow(pencere, frame)
            cv2.waitKey(1)
        except Exception as e:
            if not hasattr(self, '_imshow_warned'):
                log.warning(f"cv2.imshow hatası: {e}")
                self._imshow_warned = True

    # ── Ağrı Testi (Servo SG5010) ──
    def agri_testi(self):
        self.lcd.durum = "AGRI TESTI"
        self.lcd.ciz()
        log.info("Ağrı testi: Servo Pin 18 (SG5010)")
        try:
            from gpiozero import Servo
            s = Servo(18, min_pulse_width=0.5/1000, max_pulse_width=2.5/1000)
            s.min();  time.sleep(1.5)
            s.max();  time.sleep(1.5)
            s.mid();  time.sleep(1.0)
            s.value = None
            log.info("Servo testi tamamlandı ✓")
        except Exception as e:
            log.warning(f"Servo hatası: {e}")

    # ── LCD'yi güncelle ──
    def lcd_guncelle(self):
        self.lcd.hr   = self.nabiz.hr
        self.lcd.spo2 = self.nabiz.spo2
        self.lcd.ciz()

    # ── ANA DÖNGÜ ──
    def basla(self):
        log.info("=" * 50)
        log.info(" NeuroSense GKS v3 - Monolitik Sistem Başlıyor")
        log.info("=" * 50)

        # LCD Başlat
        self.lcd.baslat()

        # Modelleri sırayla yükle (RAM koruması)
        self.lcd.durum = "MODELLER YUKLENIYOR"
        self.lcd.ciz()

        self.goz.yukle()
        self.ses.yukle()
        self.motor.yukle()

        self.lcd.durum = "HAZIR"
        self.lcd.ciz()
        log.info("Tüm modeller yüklendi. Sistem HAZIR. ✓")

        # ── SONSUZ MUAYENE DÖNGÜSÜ ──
        while True:
            log.info("\n" + "=" * 50)
            log.info(" YENİ GKS MUAYENESİ BAŞLIYOR")
            log.info("=" * 50)

            self.lcd.g = 1; self.lcd.m = 1; self.lcd.s = 1

            # ═══════════ AŞAMA 1: GÖZ TESTİ (Kamera 1) ═══════════
            self.lcd.durum = "GOZ: SPONTAN GOZLEM"
            self.lcd_guncelle()
            self.cam_goz.ac()

            acik_birikim = 0
            t0 = time.time()
            while time.time() - t0 < 7:
                frame = self.cam_goz.oku()
                if frame is not None:
                    _, acik = self.goz.analiz_et(frame)  # Frame'e landmark çizilir
                    self.goster("Goz Kamerasi", frame)
                    if acik:
                        acik_birikim += 0.25
                    else:
                        acik_birikim = 0
                    if acik_birikim >= 2.0:
                        self.lcd.g = 4
                        break
                self.lcd_guncelle()
                time.sleep(0.25)

            # Spontan açılmadıysa sesli uyar
            if self.lcd.g == 1:
                self.lcd.durum = "GOZ: SESLI UYARI"
                self.lcd_guncelle()
                self.ses.konus("Lütfen gözlerinizi açın.")

                t0 = time.time()
                while time.time() - t0 < 5:
                    frame = self.cam_goz.oku()
                    if frame is not None:
                        _, acik = self.goz.analiz_et(frame)
                        self.goster("Goz Kamerasi", frame)
                        if acik:
                            self.lcd.g = 3
                            break
                    time.sleep(0.25)

            self.cam_goz.kapat()
            try: cv2.destroyWindow("Goz Kamerasi")
            except: pass
            log.info(f"Göz (E) Sonucu: {self.lcd.g}/4")

            # ═══════════ AŞAMA 2: MOTOR TESTİ (Kamera 0) ═══════════
            self.lcd.durum = "MOTOR: KOMUT BEKLENIYOR"
            self.lcd_guncelle()
            self.cam_motor.ac()
            self.ses.konus("Lütfen elinizi kaldırın.")

            eski_kpt = None
            t0 = time.time()
            while time.time() - t0 < 6:
                frame = self.cam_motor.oku()
                if frame is not None:
                    kpt = self.motor.analiz_et(frame)  # Frame'e iskelet çizilir
                    self.goster("Motor Kamerasi", frame)
                    if self.motor.hareket_var_mi(eski_kpt, kpt):
                        self.lcd.m = 6
                        break
                    eski_kpt = kpt
                time.sleep(0.25)

            self.cam_motor.kapat()
            try: cv2.destroyWindow("Motor Kamerasi")
            except: pass

            if self.lcd.m == 1:
                self.agri_testi()

            log.info(f"Motor (M) Sonucu: {self.lcd.m}/6")

            # ═══════════ AŞAMA 3: SÖZEL TEST ═══════════
            self.lcd.durum = "SOZEL MULAKAT"
            self.lcd_guncelle()

            s1 = self.ses.test_et("YER")
            s2 = self.ses.test_et("ZAMAN")

            if s1 == 5 and s2 == 5:
                self.lcd.s = 5
            elif s1 >= 3 or s2 >= 3:
                self.lcd.s = 3
            else:
                self.lcd.s = 1

            log.info(f"Sözel (V) Sonucu: {self.lcd.s}/5")

            # ═══════════ SONUÇ ═══════════
            toplam = self.lcd.g + self.lcd.m + self.lcd.s
            log.info(f"════ TOPLAM GKS: {toplam}/15 ════")
            self.lcd.durum = f"SONUC: {toplam}/15"
            self.lcd_guncelle()
            time.sleep(3)

            # Mola
            self.lcd.durum = "MOLA (30s)"
            for i in range(30, 0, -1):
                self.lcd.durum = f"MOLA ({i}s)"
                self.lcd_guncelle()
                time.sleep(1)


# ============================================================================
if __name__ == "__main__":
    try:
        GKSApp().basla()
    except KeyboardInterrupt:
        log.info("Kullanıcı durdurdu (Ctrl+C)")
        cv2.destroyAllWindows()
