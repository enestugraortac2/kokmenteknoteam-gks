#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — Motor Analiz Modülü
ONNX Runtime ile YOLOv8n-pose iskelet tespiti + 6-kademeli GKS Motor puanlama.

Değişiklik: PyTorch/ultralytics (~350MB RAM) → ONNX Runtime (~80MB RAM)
Sonuç: %77 daha az RAM, aynı doğruluk, daha hızlı inferans.
"""

import logging
import time
import numpy as np
from collections import deque
from pathlib import Path

log = logging.getLogger("MotorAnaliz")

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
ONNX_MODEL_PATH = MODEL_DIR / "yolov8n-pose.onnx"

# COCO keypoint indeksleri
NOSE = 0
L_SHOULDER = 5
R_SHOULDER = 6
L_ELBOW = 7
R_ELBOW = 8
L_WRIST = 9
R_WRIST = 10

# Hareket eşik değerleri (piksel/frame)
VELOCITY_COMMAND_THRESHOLD = 5.0      # Belirgin hareket
VELOCITY_WITHDRAWAL_THRESHOLD = 10.0  # Hızlı kaçış hareketi
HEAD_PROXIMITY_THRESHOLD = 85.0       # Lokalizasyon: el → baş mesafesi

# Anormal postür tespiti
FLEXION_ANGLE_THRESHOLD = 45.0        # Fleksiyon açısı
EXTENSION_ANGLE_THRESHOLD = 160.0     # Ekstansiyon açısı

VELOCITY_WINDOW = 5                   # Hız hesaplama penceresi (frame)

# ONNX input boyutu
INPUT_SIZE = 320


class MotorAnaliz:
    """
    ONNX Runtime tabanlı YOLOv8n-pose motor analizi.
    6-kademeli GKS Motor puanlama (v1'in tam mantığı korunmuştur).
    """

    def __init__(self):
        self._session = None
        self._loaded = False
        self._input_name = None
        self._output_names = None

        # Bilek hız geçmişi
        self.wrist_history = {
            L_WRIST: deque(maxlen=VELOCITY_WINDOW),
            R_WRIST: deque(maxlen=VELOCITY_WINDOW),
        }
        self.last_score = 1
        self.last_status = "TEPKISIZ"

    def load_model(self) -> bool:
        """ONNX Runtime ile YOLOv8n-pose modelini yükle (~80MB RAM)."""
        if self._loaded:
            return True

        if not ONNX_MODEL_PATH.exists():
            # Fallback: ultralytics ile PyTorch modeli dene
            pt_path = MODEL_DIR / "yolov8n-pose.pt"
            if pt_path.exists():
                log.warning("ONNX modeli yok, PyTorch ile export deneniyor...")
                try:
                    self._export_to_onnx(pt_path)
                except Exception as e:
                    log.warning("ONNX export başarısız: %s — ultralytics fallback", e)
                    return self._load_ultralytics_fallback(pt_path)

            if not ONNX_MODEL_PATH.exists():
                log.error("Model bulunamadı: %s", ONNX_MODEL_PATH)
                return False

        try:
            import onnxruntime as ort
            # CPU execution provider — Pi 5 için en verimli
            self._session = ort.InferenceSession(
                str(ONNX_MODEL_PATH),
                providers=["CPUExecutionProvider"],
            )
            self._input_name = self._session.get_inputs()[0].name
            self._output_names = [o.name for o in self._session.get_outputs()]
            self._loaded = True
            log.info("YOLOv8n-pose ONNX yüklendi ✓ (~80MB)")
            return True
        except ImportError:
            log.warning("onnxruntime bulunamadı, ultralytics fallback deneniyor...")
            pt_path = MODEL_DIR / "yolov8n-pose.pt"
            if pt_path.exists():
                return self._load_ultralytics_fallback(pt_path)
            return False
        except Exception as e:
            log.error("ONNX yükleme hatası: %s", e)
            return False

    def _export_to_onnx(self, pt_path: Path):
        """PyTorch modelini ONNX'e otomatik export et."""
        from ultralytics import YOLO
        model = YOLO(str(pt_path))
        model.export(format="onnx", imgsz=INPUT_SIZE, simplify=True)
        # ultralytics export aynı dizine yazar
        exported = pt_path.with_suffix(".onnx")
        if exported.exists() and exported != ONNX_MODEL_PATH:
            exported.rename(ONNX_MODEL_PATH)
        log.info("ONNX export tamamlandı: %s", ONNX_MODEL_PATH)

    def _load_ultralytics_fallback(self, pt_path: Path) -> bool:
        """ONNX yoksa ultralytics/PyTorch ile çalış (ağır ama çalışır)."""
        try:
            from ultralytics import YOLO
            self._session = YOLO(str(pt_path))
            self._loaded = True
            self._input_name = "__ultralytics__"  # Fallback marker
            log.warning("YOLOv8n-pose PyTorch fallback yüklendi (RAM: ~350MB)")
            return True
        except Exception as e:
            log.error("Ultralytics fallback da başarısız: %s", e)
            return False

    def unload_model(self):
        """Modeli bellekten kaldır."""
        self._session = None
        self._loaded = False
        self._input_name = None
        self._output_names = None
        self.wrist_history[L_WRIST].clear()
        self.wrist_history[R_WRIST].clear()
        log.info("Motor modeli bellekten kaldırıldı")

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        """Frame'i ONNX model girdisine hazırla."""
        import cv2
        img = cv2.resize(frame, (INPUT_SIZE, INPUT_SIZE))
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))  # HWC → CHW
        img = np.expand_dims(img, 0)          # Batch dim
        return img

    def _postprocess_keypoints(self, output: np.ndarray, orig_shape: tuple) -> np.ndarray | None:
        """
        YOLOv8-pose ONNX çıktısından keypoint'leri çıkar.
        output shape: (1, 56, N) — 56 = 4(bbox) + 1(conf) + 17*3(kpts)
        """
        if output is None or len(output) == 0:
            return None

        predictions = output[0]  # (56, N)
        if predictions.ndim == 3:
            predictions = predictions[0]

        if predictions.shape[0] == 56:
            predictions = predictions.T  # (N, 56)

        if len(predictions) == 0:
            return None

        # En yüksek confidence tespit
        confidences = predictions[:, 4]
        best_idx = np.argmax(confidences)

        if confidences[best_idx] < 0.5:
            return None

        det = predictions[best_idx]

        # Keypoints: indeks 5'ten itibaren 17×3
        kpts_raw = det[5:5 + 51].reshape(17, 3)

        # Koordinatları orijinal boyuta ölçekle
        h_orig, w_orig = orig_shape[:2]
        scale_x = w_orig / INPUT_SIZE
        scale_y = h_orig / INPUT_SIZE

        keypoints = np.zeros((17, 3), dtype=np.float32)
        keypoints[:, 0] = kpts_raw[:, 0] * scale_x
        keypoints[:, 1] = kpts_raw[:, 1] * scale_y
        keypoints[:, 2] = kpts_raw[:, 2]  # confidence

        return keypoints

    def pose_tespit_et(self, frame) -> np.ndarray | None:
        """
        Frame'den iskelet keypoint'lerini çıkar.

        Returns:
            keypoints: (17, 3) array [x, y, confidence] veya None
        """
        if not self._loaded or frame is None:
            return None

        try:
            # Ultralytics fallback modu
            if self._input_name == "__ultralytics__":
                results = self._session(frame, verbose=False, conf=0.5, imgsz=INPUT_SIZE)
                for r in results:
                    if r.keypoints is not None and len(r.keypoints.data) > 0:
                        return r.keypoints.data[0].cpu().numpy()
                return None

            # ONNX inferans
            input_tensor = self._preprocess(frame)
            outputs = self._session.run(self._output_names, {self._input_name: input_tensor})
            return self._postprocess_keypoints(outputs[0], frame.shape)

        except Exception as e:
            log.warning("Pose tespiti hatası: %s", e)
            return None

    # ─── Hareket Analiz Yardımcıları ─────────────────────────

    def _compute_velocity(self, wrist_idx: int, current_pos: np.ndarray) -> float:
        """Son N frame üzerinden ortalama hız (piksel/frame)."""
        history = self.wrist_history[wrist_idx]
        history.append(current_pos[:2].copy())
        if len(history) < 2:
            return 0.0
        velocities = []
        for i in range(1, len(history)):
            v = np.linalg.norm(history[i] - history[i - 1])
            velocities.append(v)
        return float(np.mean(velocities))

    @staticmethod
    def _compute_arm_angle(shoulder: np.ndarray, elbow: np.ndarray,
                           wrist: np.ndarray) -> float:
        """Omuz-dirsek-bilek açısını hesapla (derece)."""
        v1 = shoulder[:2] - elbow[:2]
        v2 = wrist[:2] - elbow[:2]
        cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
        cos_angle = np.clip(cos_angle, -1.0, 1.0)
        return float(np.degrees(np.arccos(cos_angle)))

    def analiz_et(self, keypoints: np.ndarray,
                  komut_aktif: bool = False,
                  servo_aktif: bool = False) -> tuple:
        """
        Keypoints'ten GKS Motor puanı hesapla.

        GKS Motor Skalası:
            6 = Emirlere uyuyor
            5 = Ağrıyı lokalize ediyor
            4 = Normal fleksiyon (kaçış)
            3 = Anormal fleksiyon (dekortike)
            2 = Ekstansiyon (deserebre)
            1 = Tepkisiz

        Returns:
            (skor: int, durum: str)
        """
        if keypoints is None or len(keypoints) < 11:
            return 1, "YETERSIZ_VERI"

        nose = keypoints[NOSE]
        l_wrist = keypoints[L_WRIST]
        r_wrist = keypoints[R_WRIST]
        l_shoulder = keypoints[L_SHOULDER]
        r_shoulder = keypoints[R_SHOULDER]
        l_elbow = keypoints[L_ELBOW]
        r_elbow = keypoints[R_ELBOW]

        # Bilek hızları
        l_vel = self._compute_velocity(L_WRIST, l_wrist)
        r_vel = self._compute_velocity(R_WRIST, r_wrist)
        max_vel = max(l_vel, r_vel)

        # Baş-el mesafeleri
        l_head_dist = float(np.linalg.norm(l_wrist[:2] - nose[:2]))
        r_head_dist = float(np.linalg.norm(r_wrist[:2] - nose[:2]))
        min_head_dist = min(l_head_dist, r_head_dist)

        # Kol açıları
        l_arm_angle = self._compute_arm_angle(l_shoulder, l_elbow, l_wrist)
        r_arm_angle = self._compute_arm_angle(r_shoulder, r_elbow, r_wrist)

        # ─── Dinamik Ölçekleme (Norm) ─────────────────────
        shoulder_dist = float(np.linalg.norm(l_shoulder[:2] - r_shoulder[:2]))
        # 320x320 giriş boyutunda ortalama bir omuz genişliği referans alınır (Örn: 100px)
        if shoulder_dist < 20.0:
            shoulder_dist = 100.0  # Fallback

        scale = shoulder_dist / 100.0
        
        dyn_vel_cmd = VELOCITY_COMMAND_THRESHOLD * scale
        dyn_vel_with = VELOCITY_WITHDRAWAL_THRESHOLD * scale
        dyn_head_prox = HEAD_PROXIMITY_THRESHOLD * scale

        # ─── GKS Karar Mantığı ─────────────────────────────
        skor = 1
        durum = "TEPKISIZ"

        # 1. SKOR 6: Emirlere uyma
        if komut_aktif and max_vel > dyn_vel_cmd:
            r_lifted = r_wrist[1] < r_shoulder[1]  # y ekseni ters
            l_lifted = l_wrist[1] < l_shoulder[1]
            if (r_vel > dyn_vel_cmd and r_lifted) or \
               (l_vel > dyn_vel_cmd and l_lifted):
                skor = 6
                durum = "KOMUTLARA_UYUYOR"
                self.last_score = skor
                self.last_status = durum
                return skor, durum

        # 2. SKOR 5: Ağrıyı lokalize etme
        if servo_aktif and min_head_dist < dyn_head_prox:
            skor = 5
            durum = "AGRIYI_LOKALIZE"

        # 3. SKOR 4: Normal fleksiyon / kaçış
        elif servo_aktif and max_vel > dyn_vel_with:
            skor = 4
            durum = "NORMAL_FLEKSIYON"

        # 4. SKOR 3: Anormal fleksiyon (dekortike)
        elif servo_aktif and (l_arm_angle < FLEXION_ANGLE_THRESHOLD
                             and r_arm_angle < FLEXION_ANGLE_THRESHOLD):
            skor = 3
            durum = "ANORMAL_FLEKSIYON_DEKORTIKE"

        # 5. SKOR 2: Ekstansiyon (deserebre)
        elif servo_aktif and (l_arm_angle > EXTENSION_ANGLE_THRESHOLD
                             and r_arm_angle > EXTENSION_ANGLE_THRESHOLD):
            skor = 2
            durum = "EKSTANSIYON_DESEREBRE"

        # 6. Spontan hareket (komut/servo olmadan)
        elif max_vel > dyn_vel_cmd:
            if min_head_dist < dyn_head_prox:
                skor = 5
                durum = "SPONTAN_LOKALIZASYON"
            else:
                skor = 4
                durum = "SPONTAN_HAREKET"

        self.last_score = skor
        self.last_status = durum
        return skor, durum

    def hareket_var_mi(self, old_kpts: np.ndarray, new_kpts: np.ndarray,
                       joints: list = None) -> bool:
        """Basit hareket tespiti (iki frame arasında)."""
        if joints is None:
            joints = [L_WRIST, R_WRIST]
        if old_kpts is None or new_kpts is None:
            return False
        try:
            total_mov = 0
            count = 0
            for j in joints:
                if len(old_kpts) > j and len(new_kpts) > j:
                    if old_kpts[j][2] > 0.3 and new_kpts[j][2] > 0.3:
                        dx = new_kpts[j][0] - old_kpts[j][0]
                        dy = new_kpts[j][1] - old_kpts[j][1]
                        dist = (dx ** 2 + dy ** 2) ** 0.5
                        total_mov += dist
                        count += 1
            if count > 0:
                return (total_mov / count) > VELOCITY_COMMAND_THRESHOLD
            return False
        except Exception:
            return False
