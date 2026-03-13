import logging
import cv2
import numpy as np
from pathlib import Path

log = logging.getLogger("MotorAnaliz")
YOLO_MODEL_PATH = Path(__file__).parent.parent / "models" / "yolov8n-pose.pt"

class MotorAnaliz:
    def __init__(self):
        self.model = None
        self.MOVEMENT_TH = 15.0 # Piksel hareket eşiği

    def load_model(self):
        if self.model is not None:
            return True
        log.info("YOLOv8n-pose modeli yükleniyor...")
        try:
            from ultralytics import YOLO
            self.model = YOLO(str(YOLO_MODEL_PATH))
            log.info("YOLOv8n-pose başarıyla yüklendi ✓")
            return True
        except Exception as e:
            log.error(f"YOLOv8n-pose yüklenemedi: {e}")
            return False
            
    def pose_tespit_et(self, frame):
        if self.model is None or frame is None:
            return None
            
        try:
            results = self.model(frame, verbose=False, max_det=1, classes=[0], conf=0.5)
            if not results or not results[0].boxes:
                return None
                
            keypoints = results[0].keypoints.data[0].cpu().numpy()
            return keypoints
        except Exception as e:
            log.error(f"YOLO çıkarım hatası: {e}")
            return None
            
    def hareket_var_mi(self, old_kpts, new_kpts, joints=[9, 10]):
        # 9, 10: El bilekleri
        if old_kpts is None or new_kpts is None:
            return False
            
        try:
            total_mov = 0
            count = 0
            for j in joints:
                if len(old_kpts) > j and len(new_kpts) > j:
                    if old_kpts[j][2] > 0.3 and new_kpts[j][2] > 0.3: # Güven skoru
                        dx = new_kpts[j][0] - old_kpts[j][0]
                        dy = new_kpts[j][1] - old_kpts[j][1]
                        dist = (dx**2 + dy**2)**0.5
                        total_mov += dist
                        count += 1
            
            if count > 0:
                avg_mov = total_mov / count
                return avg_mov > self.MOVEMENT_TH
            return False
        except Exception as e:
            log.error(f"Hareket analizi hatası: {e}")
            return False
