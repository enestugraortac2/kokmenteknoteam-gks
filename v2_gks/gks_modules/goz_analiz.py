import dlib
import cv2
import numpy as np
import logging
from scipy.spatial import distance as dist
from pathlib import Path

log = logging.getLogger("GozAnaliz")

DLIB_MODEL = Path(__file__).parent.parent / "models" / "shape_predictor_68_face_landmarks.dat"

class GozAnaliz:
    def __init__(self):
        self.detector = None
        self.predictor = None
        self.EAR_THRESHOLD = 0.22
        
    def load_model(self):
        if self.detector is not None:
            return True
        log.info("Dlib göz modeli RGB RAM'e yükleniyor...")
        try:
            self.detector = dlib.get_frontal_face_detector()
            self.predictor = dlib.shape_predictor(str(DLIB_MODEL))
            log.info("Dlib modeli yüklendi ✓")
            return True
        except Exception as e:
            log.error(f"Dlib modeli yüklenemedi: {e}")
            return False

    def analiz_et(self, frame):
        if self.detector is None or frame is None:
            return False
            
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.detector(gray, 0)
            
            if not faces:
                return False # Yüz yok
                
            face = faces[0]
            landmarks = self.predictor(gray, face)
            
            def calculate_ear(eye_pts):
                A = dist.euclidean(eye_pts[1], eye_pts[5])
                B = dist.euclidean(eye_pts[2], eye_pts[4])
                C = dist.euclidean(eye_pts[0], eye_pts[3])
                return (A + B) / (2.0 * C)
                
            left_eye = np.array([(landmarks.part(n).x, landmarks.part(n).y) for n in range(36, 42)])
            right_eye = np.array([(landmarks.part(n).x, landmarks.part(n).y) for n in range(42, 48)])
            
            ear = (calculate_ear(left_eye) + calculate_ear(right_eye)) / 2.0
            return ear > self.EAR_THRESHOLD # True = Göz Açık
        except Exception as e:
            log.error(f"Göz analiz hatası: {e}")
            return False
