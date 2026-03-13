#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — Kamera Yöneticisi
Tek noktadan kamera erişimi. Çakışmayı engeller.
Picamera2 → cv2.VideoCapture fallback zinciri.
Desteklenen özellikler: rotation (0/90/180/270), hflip, vflip.
"""

import cv2
import time
import logging
import threading
import numpy as np

log = logging.getLogger("Kamera")


class CameraManager:
    """
    Tek kamera kaynağı yöneticisi.
    start()/stop() ile yaşam döngüsü kontrol edilir.
    get_frame() ile BGR frame alınır.
    rotation/flip desteği ile eğik monte edilen kameralar desteklenir.
    """

    # cv2.rotate() için sabit harita
    _ROTATION_MAP = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }

    def __init__(self, camera_id: int = 0, width: int = 640, height: int = 480,
                 rotation: int = 0, hflip: bool = False, vflip: bool = False):
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.rotation = rotation if rotation in (0, 90, 180, 270) else 0
        self.hflip = hflip
        self.vflip = vflip
        self._camera = None
        self._use_picamera = False
        self._is_running = False
        self._latest_frame = None
        self._lock = threading.Lock()
        self._thread = None

        if self.rotation != 0 or self.hflip or self.vflip:
            log.info("Kamera transform: rotation=%d° hflip=%s vflip=%s",
                     self.rotation, self.hflip, self.vflip)

    @property
    def is_running(self) -> bool:
        return self._is_running

    def _apply_transform(self, frame):
        """Frame'e rotation ve flip uygula."""
        if frame is None:
            return frame

        # Flip işlemleri
        if self.hflip and self.vflip:
            frame = cv2.flip(frame, -1)  # Her iki eksen
        elif self.hflip:
            frame = cv2.flip(frame, 1)   # Yatay
        elif self.vflip:
            frame = cv2.flip(frame, 0)   # Dikey

        # Rotation (Picamera2 native transform kullanıyorsa 0/180 zaten halledilir)
        if self.rotation in self._ROTATION_MAP:
            frame = cv2.rotate(frame, self._ROTATION_MAP[self.rotation])

        return frame

    def _update_loop(self):
        """Sürekli arkaplanda frame okur ve en güncelini saklar (Buffer lag engeller)."""
        while self._is_running:
            if self._camera is None:
                time.sleep(0.01)
                continue
            
            try:
                if self._use_picamera:
                    raw = self._camera.capture_array()
                    frame = cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
                else:
                    ret, frame = self._camera.read()
                    if not ret:
                        frame = None

                # Transform uygula (rotation + flip)
                frame = self._apply_transform(frame)

                with self._lock:
                    self._latest_frame = frame
            except Exception as e:
                log.warning("Frame arkaplan okuma hatası: %s", e)
                
            time.sleep(0.01)  # CPU'yu çok boğmamak için kısa bekleme

    def start(self) -> bool:
        """Kamerayı başlat. Zaten açıksa True döner."""
        if self._is_running and self._camera is not None:
            return True

        log.info("Kamera %d başlatılıyor (%dx%d)...", self.camera_id, self.width, self.height)

        # Önce Picamera2 dene
        try:
            from picamera2 import Picamera2
            self._camera = Picamera2(self.camera_id)
            config = self._camera.create_preview_configuration(
                main={"format": "RGB888", "size": (self.width, self.height)}
            )
            self._camera.configure(config)
            self._camera.start()
            self._use_picamera = True
            self._is_running = True
            self._start_thread()
            log.info("Picamera2 kamera %d başlatıldı ✓", self.camera_id)
            return True
        except Exception as e:
            log.warning("Picamera2 başlatılamadı: %s — cv2 deneniyor...", e)

        # cv2 fallback
        try:
            self._camera = cv2.VideoCapture(self.camera_id)
            if not self._camera.isOpened():
                log.error("cv2 kamera %d açılamadı!", self.camera_id)
                self._camera = None
                return False

            self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self._use_picamera = False
            self._is_running = True
            self._start_thread()
            log.info("cv2 kamera %d başlatıldı ✓", self.camera_id)
            return True
        except Exception as e:
            log.error("Kamera tamamen başarısız: %s", e)
            self._camera = None
            return False

    def _start_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._update_loop, daemon=True)
            self._thread.start()

    def get_frame(self) -> np.ndarray | None:
        """En son okunan (latest) BGR frame'i döndürür."""
        if not self._is_running:
            return None
            
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def stop(self):
        """Kamerayı güvenli şekilde kapat ve kaynağı serbest bırak."""
        if self._camera is None:
            self._is_running = False
            return

        log.info("Kamera %d serbest bırakılıyor...", self.camera_id)
        self._is_running = False
        
        if hasattr(self, '_thread') and self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
            
        try:
            if self._use_picamera:
                self._camera.stop()
                self._camera.close()
            else:
                self._camera.release()
        except Exception as e:
            log.warning("Kamera kapatma hatası: %s", e)
        finally:
            self._camera = None
            log.info("Kamera serbest bırakıldı ✓")

    def __del__(self):
        self.stop()
