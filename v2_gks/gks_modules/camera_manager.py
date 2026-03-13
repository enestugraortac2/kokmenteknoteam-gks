import cv2
import time
import logging
import numpy as np

log = logging.getLogger("CameraManager")

class GKS_Camera:
    """Tek noktadan kamera yönetimi. Çakışmayı engeller."""
    def __init__(self, camera_id=0):
        self.camera_id = camera_id
        self._camera = None
        self.use_picamera = False

    def start(self):
        if self._camera is not None:
            return True # Zaten açık
            
        log.info(f"Kamera {self.camera_id} başlatılıyor (Önce Picamera2 deneniyor)...")
        try:
            from picamera2 import Picamera2
            self._camera = Picamera2(self.camera_id)
            config = self._camera.create_preview_configuration(
                main={"format": "RGB888", "size": (640, 480)}
            )
            self._camera.configure(config)
            self._camera.start()
            self.use_picamera = True
            log.info("Picamera2 başarıyla başlatıldı ✓")
            return True
        except Exception as e:
            log.warning(f"Picamera2 başlatılamadı: {e}. cv2 deneniyor...")
            
        try:
            self._camera = cv2.VideoCapture(self.camera_id)
            if self._camera.isOpened():
                self.use_picamera = False
                log.info("cv2 başarıyla başlatıldı ✓")
                return True
            else:
                self._camera = None
                log.error("cv2 kamera açılamadı!")
                return False
        except Exception as e:
            log.error(f"Kamera tamamen çöktü: {e}")
            self._camera = None
            return False

    def get_frame(self):
        if self._camera is None:
            return None
            
        try:
            if self.use_picamera:
                raw_frame = self._camera.capture_array()
                frame = cv2.cvtColor(raw_frame, cv2.COLOR_RGB2BGR)
                return frame
            else:
                ret, frame = self._camera.read()
                return frame if ret else None
        except Exception as e:
            log.error(f"Frame okuma hatası: {e}")
            return None

    def stop(self):
        if self._camera:
            log.info("Kamera serbest bırakılıyor...")
            try:
                if self.use_picamera:
                    self._camera.stop()
                    self._camera.close() # Kamerayı tamamen OS seviyesinde kapat
                else:
                    self._camera.release()
            except Exception as e:
                log.error(f"Kamera kapatma hatası: {e}")
            finally:
                self._camera = None
