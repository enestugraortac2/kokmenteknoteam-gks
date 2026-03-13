#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║  NeuroSense GKS — Göz Takip Modülü (Eye Tracking Module)   ║
║  Raspberry Pi 5 — Endüstriyel Sınıf, Fault-Tolerant        ║
╚══════════════════════════════════════════════════════════════╝

Görev:
  - Picamera2 üzerinden yüz tespiti + dlib 68-landmark ile EAR hesaplama
  - Göz açıklık durumu ve EAR değerini /dev/shm/gks_skor.json'a yazma
  - Graceful shutdown (atexit + SIGTERM handler)

IPC:
  - /dev/shm/gks_skor.json (fcntl exclusive lock ile atomik yazma)

Kamera:
  - CAMERA_ID ortam değişkeninden okunur (varsayılan: 1)
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

import cv2
import numpy as np
from collections import deque

# ─── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[GÖZ %(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("goz_takip")

# ─── Platform-safe fcntl ────────────────────────────────────────
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

# ─── Konfigürasyon ─────────────────────────────────────────────
CAMERA_ID = int(os.environ.get("CAMERA_ID", "1"))
SHM_PATH = Path(os.environ.get("GKS_SHM_PATH", "/dev/shm/gks_skor.json"))
MODEL_DIR = Path(__file__).resolve().parent / "models"
DLIB_MODEL = MODEL_DIR / "shape_predictor_68_face_landmarks.dat"

# EAR eşik değerleri (kalibre edilmiş)
EAR_OPEN_THRESHOLD = 0.18       # Bu değerin üstü = göz açık
EAR_CLOSED_THRESHOLD = 0.15     # Bu değerin altı = göz kapalı
FRAME_RATE_TARGET = 15          # FPS hedefi (Pi 5 için uygun)

# dlib yüz landmark indeksleri
LEFT_EYE_IDX = [36, 37, 38, 39, 40, 41]
RIGHT_EYE_IDX = [42, 43, 44, 45, 46, 47]

# ─── Global State ──────────────────────────────────────────────────────
_camera = None
_running = True

# Sliding window: son N frame'in göz açıklık sonuçları
_HISTORY_SIZE = 5
_MAJORITY_THRESHOLD = 3
_goz_history = deque(maxlen=_HISTORY_SIZE)
_last_face_ts = 0.0
_STALE_TIMEOUT = 1.0  # Yüz bulunamazsa son durumu koruma süresi
_last_goz_acik = False
_last_ear = 0.0


# ═══════════════════════════════════════════════════════════════
#  IPC: RAM Disk'e Güvenli Yazma
# ═══════════════════════════════════════════════════════════════

def write_shm(updates: dict) -> bool:
    """
    /dev/shm/gks_skor.json dosyasına fcntl exclusive lock ile atomik yazma.
    Aynı anda birden fazla modülün yazmasını engeller.
    """
    try:
        SHM_PATH.parent.mkdir(parents=True, exist_ok=True)
        max_attempts = 5

        for attempt in range(max_attempts):
            try:
                # Dosya varsa oku + güncelle, yoksa oluştur
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


# ═══════════════════════════════════════════════════════════════
#  EAR Hesaplama
# ═══════════════════════════════════════════════════════════════

def calculate_ear(eye_points: np.ndarray) -> float:
    """
    Eye Aspect Ratio (EAR) hesapla.
    Solov'yev & al. (2016) formülü:
      EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)
    """
    v1 = np.linalg.norm(eye_points[1] - eye_points[5])
    v2 = np.linalg.norm(eye_points[2] - eye_points[4])
    h = np.linalg.norm(eye_points[0] - eye_points[3])
    if h < 1e-6:
        return 0.0
    return (v1 + v2) / (2.0 * h)


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
    """SIGTERM/SIGINT yakalanınca temiz kapanış."""
    log.info("Sinyal alındı (%s), kapatılıyor...", signum)
    _cleanup()
    sys.exit(0)


# SIGTERM ve SIGINT handler'larını kaydet
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)
atexit.register(_cleanup)


# ═══════════════════════════════════════════════════════════════
#  Ana Döngü
# ═══════════════════════════════════════════════════════════════

def run():
    """Göz takip ana döngüsü."""
    global _camera, _running
    import dlib

    # ─── Model yükleme ──────────────────────────────────────
    if not DLIB_MODEL.exists():
        log.error("dlib model bulunamadı: %s", DLIB_MODEL)
        sys.exit(1)

    log.info("dlib yüz dedektörü yükleniyor...")
    detector = dlib.get_frontal_face_detector()
    predictor = dlib.shape_predictor(str(DLIB_MODEL))
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
        log.warning("Picamera2 başlatılamadı: %s — cv2.VideoCapture deneniyor", e)
        _camera = cv2.VideoCapture(CAMERA_ID)
        if not _camera.isOpened():
            log.error("Kamera %d açılamadı!", CAMERA_ID)
            sys.exit(1)
        use_picamera = False
        log.info("cv2 kamera %d başlatıldı ✓", CAMERA_ID)

    # Headless mi kontrol et (DISPLAY yoksa imshow çağırma)
    has_display = os.environ.get("DISPLAY") is not None

    frame_delay = 1.0 / FRAME_RATE_TARGET
    consecutive_errors = 0
    max_consecutive_errors = 30  # 30 frame üst üste hata → çık

    log.info("═══ Göz takip döngüsü başlıyor ═══")

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
                            log.error("Çok fazla ardışık frame hatası, çıkılıyor.")
                            break
                        time.sleep(0.05)
                        continue
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                log.warning("Frame yakalama hatası (%d/%d): %s",
                            consecutive_errors, max_consecutive_errors, e)
                if consecutive_errors >= max_consecutive_errors:
                    log.error("Kamera kalıcı olarak başarısız, çıkılıyor.")
                    break
                time.sleep(0.1)
                continue

            # ─── Yüz tespiti ────────────────────────────────
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector(gray, 0)  # upsample=0 → hız

            ear_value = 0.0
            raw_goz_acik = False

            if len(faces) > 0:
                # İlk (en büyük) yüzü al
                face = max(faces, key=lambda f: (f.right() - f.left()) * (f.bottom() - f.top()))
                landmarks = predictor(gray, face)
                pts = np.array([[landmarks.part(i).x, landmarks.part(i).y]
                                for i in range(68)])

                left_eye = pts[LEFT_EYE_IDX]
                right_eye = pts[RIGHT_EYE_IDX]

                left_ear = calculate_ear(left_eye)
                right_ear = calculate_ear(right_eye)
                ear_value = (left_ear + right_ear) / 2.0

                raw_goz_acik = ear_value > EAR_OPEN_THRESHOLD
                _last_face_ts = time.time()

                # Sliding window'a ekle
                _goz_history.append(raw_goz_acik)

                # Görselleştirme (sadece DISPLAY varsa)
                if has_display:
                    color = (0, 255, 0) if raw_goz_acik else (0, 0, 255)
                    for (x, y) in np.concatenate((left_eye, right_eye)):
                        cv2.circle(frame, (int(x), int(y)), 2, (0, 255, 0), -1)
                    cv2.polylines(frame, [left_eye.astype(int)], True, (255, 255, 0), 1)
                    cv2.polylines(frame, [right_eye.astype(int)], True, (255, 255, 0), 1)
                    cv2.rectangle(frame,
                                  (face.left(), face.top()),
                                  (face.right(), face.bottom()),
                                  (200, 200, 200), 1)
                    cv2.putText(frame,
                                f"EAR: {ear_value:.3f} | Acik: {raw_goz_acik}",
                                (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            else:
                # Yüz bulunamadı: stale timeout içindeyse son durumu koru
                elapsed = time.time() - _last_face_ts
                if elapsed < _STALE_TIMEOUT and _last_face_ts > 0:
                    # Son bilinen durumu koru (anlık kayıp vb.)
                    ear_value = _last_ear
                else:
                    # Timeout aşıldı → gerçekten yüz yok
                    _goz_history.clear()

            # ─── Majority vote ile stabilize edilmiş karar ───
            acik_sayisi = sum(1 for x in _goz_history if x)
            goz_acik = acik_sayisi >= _MAJORITY_THRESHOLD

            _last_goz_acik = goz_acik
            _last_ear = ear_value

            # ─── IPC: RAM disk'e yaz ───────────────────────
            ts = time.time()
            write_shm({
                "goz_ear": round(ear_value, 4),
                "goz_acik": goz_acik,
                "goz_ts": ts,
            })

            # ─── Görüntüleme ────────────────────────────────
            if has_display:
                cv2.imshow("NeuroSense Goz Takip", frame)
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
