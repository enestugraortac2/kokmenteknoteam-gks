#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║  NeuroSense GKS — Motor Takip Modülü (Motor Tracking)      ║
║  Raspberry Pi 5 — Endüstriyel Sınıf, Fault-Tolerant        ║
╚══════════════════════════════════════════════════════════════╝

Görev:
  - YOLOv8n-pose ile iskelet tespiti
  - El bilek hız analizi → GKS Motor puanı (1-6)
  - Hareket sınıflandırma: komut uyma, lokalizasyon, kaçış, fleksiyon, tepkisiz

IPC:
  - /dev/shm/gks_skor.json (fcntl exclusive lock ile atomik yazma)

Kamera:
  - CAMERA_ID ortam değişkeninden okunur (varsayılan: 0)

Keypoint İndeksleri (COCO format):
   0: burun,  5: sol_omuz, 6: sağ_omuz,
   7: sol_dirsek, 8: sağ_dirsek,
   9: sol_bilek, 10: sağ_bilek,
  11: sol_kalça, 12: sağ_kalça
"""

import os
import sys
import time
import json
import atexit
import signal
import errno
import logging
from pathlib import Path
from collections import deque

import cv2
import numpy as np

# ─── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[MOTOR %(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("motor_takip")

# ─── Platform-safe fcntl ────────────────────────────────────────
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

# ─── Konfigürasyon ─────────────────────────────────────────────
CAMERA_ID = int(os.environ.get("CAMERA_ID", "0"))
SHM_PATH = Path(os.environ.get("GKS_SHM_PATH", "/dev/shm/gks_skor.json"))
MODEL_DIR = Path(__file__).resolve().parent / "models"
YOLO_MODEL_PATH = MODEL_DIR / "yolov8n-pose.pt"

# Hareket eşik değerleri (piksel/frame)
VELOCITY_COMMAND_THRESHOLD = 5.0     # Belirgin hareket = komuta uyma
VELOCITY_WITHDRAWAL_THRESHOLD = 10.0  # Hızlı kaçış hareketi
HEAD_PROXIMITY_THRESHOLD = 85.0       # Lokalizasyon: el → baş mesafesi

# Anormal postür tespiti eşikleri
FLEXION_ANGLE_THRESHOLD = 45.0        # Fleksiyon açısı
EXTENSION_ANGLE_THRESHOLD = 160.0     # Ekstansiyon açısı

FRAME_RATE_TARGET = 10                # FPS hedefi
VELOCITY_WINDOW = 5                   # Hız hesaplama penceresi (frame)

# ─── Global State ──────────────────────────────────────────────
_camera = None
_running = True

# Komut durumu (main.py'den SHM üzerinden okunur)
# main.py, komut verdiğinde SHM'ye "motor_komut_aktif": true yazar


# ═══════════════════════════════════════════════════════════════
#  IPC: RAM Disk'e Güvenli Yazma
# ═══════════════════════════════════════════════════════════════

def write_shm(updates: dict) -> bool:
    """RAM disk'e fcntl exclusive lock ile atomik yazma."""
    try:
        SHM_PATH.parent.mkdir(parents=True, exist_ok=True)
        max_attempts = 5

        for attempt in range(max_attempts):
            try:
                if SHM_PATH.exists():
                    with open(SHM_PATH, "r+", encoding="utf-8") as f:
                        if _HAS_FCNTL:
                            try:
                                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except OSError as e:
                                if e.errno in (errno.EACCES, errno.EAGAIN):
                                    time.sleep(0.1)
                                    continue
                                raise
                        try:
                            f.seek(0)
                            try:
                                data = json.load(f)
                            except (json.JSONDecodeError, ValueError):
                                data = {}
                            data.update(updates)
                            f.seek(0)
                            f.truncate()
                            json.dump(data, f, ensure_ascii=False)
                            f.flush()
                            os.fsync(f.fileno())
                            return True
                        finally:
                            if _HAS_FCNTL:
                                try:
                                    fcntl.flock(f, fcntl.LOCK_UN)
                                except Exception:
                                    pass
                else:
                    with open(SHM_PATH, "w", encoding="utf-8") as f:
                        if _HAS_FCNTL:
                            fcntl.flock(f, fcntl.LOCK_EX)
                        try:
                            json.dump(updates, f, ensure_ascii=False)
                            f.flush()
                            os.fsync(f.fileno())
                            return True
                        finally:
                            if _HAS_FCNTL:
                                try:
                                    fcntl.flock(f, fcntl.LOCK_UN)
                                except Exception:
                                    pass
            except Exception as e:
                log.warning("SHM yazma denemesi %d başarısız: %s", attempt + 1, e)
                time.sleep(0.1)
        return False
    except Exception as e:
        log.error("SHM yazma kritik hata: %s", e)
        return False


def read_shm() -> dict:
    """RAM disk'ten SHM verilerini oku (non-blocking shared lock)."""
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
#  Motor Analiz Motoru
# ═══════════════════════════════════════════════════════════════

class MotorAnalyzer:
    """Hareket analizi ve GKS motor puanlama."""

    # COCO keypoint indeksleri
    NOSE = 0
    L_SHOULDER = 5
    R_SHOULDER = 6
    L_ELBOW = 7
    R_ELBOW = 8
    L_WRIST = 9
    R_WRIST = 10

    def __init__(self):
        # Her bilek için pozisyon geçmişi (hız hesaplama için)
        self.wrist_history = {
            self.L_WRIST: deque(maxlen=VELOCITY_WINDOW),
            self.R_WRIST: deque(maxlen=VELOCITY_WINDOW),
        }
        self.last_score = 1
        self.last_status = "TEPKISIZ"

    def _compute_velocity(self, idx: int, current_pos: np.ndarray) -> float:
        """Son N frame üzerinden ortalama hız hesapla (piksel/frame)."""
        history = self.wrist_history[idx]
        history.append(current_pos.copy())
        if len(history) < 2:
            return 0.0
        velocities = []
        for i in range(1, len(history)):
            v = np.linalg.norm(history[i] - history[i - 1])
            velocities.append(v)
        return float(np.mean(velocities))

    def _compute_arm_angle(self, shoulder: np.ndarray,
                           elbow: np.ndarray, wrist: np.ndarray) -> float:
        """Omuz-dirsek-bilek açısını hesapla (derece)."""
        v1 = shoulder - elbow
        v2 = wrist - elbow
        cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
        cos_angle = np.clip(cos_angle, -1.0, 1.0)
        return float(np.degrees(np.arccos(cos_angle)))

    def analyze(self, keypoints: np.ndarray, komut_aktif: bool = False,
                servo_aktif: bool = False) -> tuple:
        """
        YOLO keypoints'ten GKS Motor puanı hesapla.

        Returns:
            (skor: int, durum: str)

        GKS Motor Skalası:
            6 = Emirlere uyuyor
            5 = Ağrıyı lokalize ediyor
            4 = Normal fleksiyon (kaçış)
            3 = Anormal fleksiyon (dekortike)
            2 = Ekstansiyon (deserebre)
            1 = Tepkisiz
        """
        if len(keypoints) < 11:
            return 1, "YETERSIZ_VERI"

        nose = keypoints[self.NOSE]
        l_wrist = keypoints[self.L_WRIST]
        r_wrist = keypoints[self.R_WRIST]
        l_shoulder = keypoints[self.L_SHOULDER]
        r_shoulder = keypoints[self.R_SHOULDER]
        l_elbow = keypoints[self.L_ELBOW]
        r_elbow = keypoints[self.R_ELBOW]

        # Bilek hızları
        l_vel = self._compute_velocity(self.L_WRIST, l_wrist)
        r_vel = self._compute_velocity(self.R_WRIST, r_wrist)
        max_vel = max(l_vel, r_vel)

        # Baş-el mesafeleri
        l_head_dist = float(np.linalg.norm(l_wrist - nose))
        r_head_dist = float(np.linalg.norm(r_wrist - nose))
        min_head_dist = min(l_head_dist, r_head_dist)

        # Kol açıları
        l_arm_angle = self._compute_arm_angle(l_shoulder, l_elbow, l_wrist)
        r_arm_angle = self._compute_arm_angle(r_shoulder, r_elbow, r_wrist)

        # ─── GKS Karar Mantığı ─────────────────────────────
        skor = 1
        durum = "TEPKISIZ"

        # 1. SKOR 6: Emirlere uyma (komut verildi + belirgin hareket)
        if komut_aktif and max_vel > VELOCITY_COMMAND_THRESHOLD:
            # Sağ el kaldırma komutu kontrol — sağ bilek omuz üstüne çıktı mı?
            r_lifted = r_wrist[1] < r_shoulder[1]  # y ekseni aşağı olduğu için
            l_lifted = l_wrist[1] < l_shoulder[1]
            if r_vel > VELOCITY_COMMAND_THRESHOLD and r_lifted:
                skor = 6
                durum = "KOMUTLARA_UYUYOR"
                self.last_score = skor
                self.last_status = durum
                write_shm({"motor_skor": skor, "motor_durum": durum,
                            "motor_ts": time.time()})
                return skor, durum
            elif l_vel > VELOCITY_COMMAND_THRESHOLD and l_lifted:
                skor = 6
                durum = "KOMUTLARA_UYUYOR"
                self.last_score = skor
                self.last_status = durum
                write_shm({"motor_skor": skor, "motor_durum": durum,
                            "motor_ts": time.time()})
                return skor, durum

        # 2. SKOR 5: Ağrıyı lokalize etme (el → ağrı kaynağına gidiyor)
        if servo_aktif and min_head_dist < HEAD_PROXIMITY_THRESHOLD:
            skor = 5
            durum = "AGRIYI_LOKALIZE"

        # 3. SKOR 4: Normal fleksiyon / kaçış
        elif servo_aktif and max_vel > VELOCITY_WITHDRAWAL_THRESHOLD:
            skor = 4
            durum = "NORMAL_FLEKSIYON"

        # 4. SKOR 3: Anormal fleksiyon (dekortike)
        #    İki taraflı fleksiyon — dirsek açısı < 45°
        elif servo_aktif and (l_arm_angle < FLEXION_ANGLE_THRESHOLD
                             and r_arm_angle < FLEXION_ANGLE_THRESHOLD):
            skor = 3
            durum = "ANORMAL_FLEKSIYON_DEKORTIKE"

        # 5. SKOR 2: Ekstansiyon (deserebre)
        #    İki taraflı ekstansiyon — dirsek açısı > 160°
        elif servo_aktif and (l_arm_angle > EXTENSION_ANGLE_THRESHOLD
                             and r_arm_angle > EXTENSION_ANGLE_THRESHOLD):
            skor = 2
            durum = "EKSTANSIYON_DESEREBRE"

        # 6. Herhangi bir hareket var mı? (komut/servo olmadan)
        elif max_vel > VELOCITY_COMMAND_THRESHOLD:
            # Spontan hareket — en az 1'den yüksek
            if min_head_dist < HEAD_PROXIMITY_THRESHOLD:
                skor = 5
                durum = "SPONTAN_LOKALIZASYON"
            else:
                skor = 4
                durum = "SPONTAN_HAREKET"

        self.last_score = skor
        self.last_status = durum

        write_shm({
            "motor_skor": skor,
            "motor_durum": durum,
            "motor_ts": time.time(),
        })

        return skor, durum


# ═══════════════════════════════════════════════════════════════
#  Graceful Shutdown
# ═══════════════════════════════════════════════════════════════

def _cleanup():
    """Kamerayı ve pencereyi güvenli şekilde kapat."""
    global _camera, _running
    _running = False
    log.info("Cleanup başlatılıyor...")

    if _camera is not None:
        try:
            _camera.stop()
            log.info("Kamera durduruldu.")
        except Exception as e:
            log.warning("Kamera durdurma hatası: %s", e)
        _camera = None

    try:
        cv2.destroyAllWindows()
    except Exception:
        pass
    log.info("Cleanup tamamlandı.")


def _signal_handler(signum, frame):
    log.info("Sinyal alındı (%s), kapatılıyor...", signum)
    _cleanup()
    sys.exit(0)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)
atexit.register(_cleanup)


# ═══════════════════════════════════════════════════════════════
#  Ana Döngü
# ═══════════════════════════════════════════════════════════════

def run():
    """Motor takip ana döngüsü."""
    global _camera, _running

    # ─── YOLO model yükleme ─────────────────────────────────
    if not YOLO_MODEL_PATH.exists():
        log.error("YOLO model bulunamadı: %s", YOLO_MODEL_PATH)
        sys.exit(1)

    log.info("YOLOv8n-pose yükleniyor...")
    from ultralytics import YOLO
    model = YOLO(str(YOLO_MODEL_PATH))
    log.info("Model yüklendi ✓")

    # ─── Kamera başlatma ────────────────────────────────────
    log.info("Kamera %d başlatılıyor (Picamera2)...", CAMERA_ID)
    try:
        from picamera2 import Picamera2
        _camera = Picamera2(CAMERA_ID)
        config = _camera.create_preview_configuration(
            main={"format": "YUV420", "size": (640, 480)}
        )
        _camera.configure(config)
        _camera.start()
        use_picamera = True
        log.info("Picamera2 kamera %d başarıyla başlatıldı ✓", CAMERA_ID)
    except Exception as e:
        log.warning("Picamera2 başlatılamadı: %s — cv2 deneniyor", e)
        _camera = cv2.VideoCapture(CAMERA_ID)
        if not _camera.isOpened():
            log.error("Kamera %d açılamadı!", CAMERA_ID)
            sys.exit(1)
        use_picamera = False

    has_display = os.environ.get("DISPLAY") is not None

    analyzer = MotorAnalyzer()
    frame_delay = 1.0 / FRAME_RATE_TARGET
    consecutive_errors = 0
    max_consecutive_errors = 30

    log.info("═══ Motor takip döngüsü başlıyor ═══")

    try:
        while _running:
            loop_start = time.monotonic()

            # ─── Frame yakala ───────────────────────────────
            try:
                if use_picamera:
                    raw_frame = _camera.capture_array()
                    frame = cv2.cvtColor(raw_frame, cv2.COLOR_YUV420p2BGR)
                else:
                    ret, frame = _camera.read()
                    if not ret:
                        consecutive_errors += 1
                        if consecutive_errors >= max_consecutive_errors:
                            break
                        time.sleep(0.05)
                        continue
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                log.warning("Frame hatası (%d/%d): %s",
                            consecutive_errors, max_consecutive_errors, e)
                if consecutive_errors >= max_consecutive_errors:
                    break
                time.sleep(0.1)
                continue

            # ─── YOLO Pose Tespiti ──────────────────────────
            results = model(frame, verbose=False, conf=0.5, imgsz=320)

            # SHM'den komut durumunu oku
            shm_data = read_shm()
            komut_aktif = shm_data.get("motor_komut_aktif", False)
            servo_aktif = shm_data.get("servo_aktif", False)

            canvas = frame.copy()
            score = 1
            status = "TEPKISIZ"

            for r in results:
                if r.keypoints is not None and len(r.keypoints.data) > 0:
                    points = r.keypoints.xy[0].cpu().numpy()
                    if len(points) > 10:
                        score, status = analyzer.analyze(
                            points,
                            komut_aktif=komut_aktif,
                            servo_aktif=servo_aktif
                        )

                        if has_display:
                            canvas = r.plot()

            # ─── Görüntüleme ────────────────────────────────
            if has_display:
                # Bilgi paneli
                cv2.rectangle(canvas, (10, 10), (500, 75), (0, 0, 0), -1)
                color = (0, 255, 0) if score >= 5 else (
                    (0, 255, 255) if score >= 3 else (0, 0, 255))
                cv2.putText(canvas, f"MOTOR SKOR: {score}/6",
                            (20, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.putText(canvas, status,
                            (20, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (255, 255, 255), 1)

                if komut_aktif:
                    cv2.putText(canvas, "KOMUT BEKLENIYOR...",
                                (350, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (0, 255, 255), 2)
                if servo_aktif:
                    cv2.putText(canvas, "SERVO AKTIF",
                                (350, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (0, 0, 255), 2)

                cv2.imshow("NeuroSense Motor Takip", canvas)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    log.info("'q' tuşuna basıldı, çıkılıyor.")
                    break

            # ─── FPS kontrol ────────────────────────────────
            elapsed = time.monotonic() - loop_start
            sleep_time = frame_delay - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt alındı.")
    finally:
        _cleanup()


# ═══════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run()
