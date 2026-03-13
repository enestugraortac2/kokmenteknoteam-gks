#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

NeuroSense GKS v3 — Göz Analiz Modülü
OpenCV Haar Cascade + Contour tabanlı göz açıklık analizi.

MediaPipe ARM64'te pip ile yüklenemiyor.
Bu modül sadece OpenCV kullanır — ek paket gerekmez.

Yöntem:
1. Haar Cascade ile yüz tespiti
2. Yüz ROI'sinde göz bölgesini çıkar
3. Göz bölgesinde adaptif threshold + contour analizi
4. Kontur yükseklik/genişlik oranıyla göz açıklık tespit
"""

import time
import logging
from collections import deque
import numpy as np

log = logging.getLogger("GozAnaliz")

# Göz açıklık analizi parametreleri
EYE_OPEN_RATIO = 0.25     # Açık göz minimum yükseklik/genişlik oranı
EYE_OPEN_AREA_MIN = 80    # Minimum kontur alanı (piksel²)
FACE_SCALE = 1.08         # Haar cascade scale factor (düşük = hassas, yavaş)
FACE_MIN_NEIGHBORS = 3    # Haar cascade min neighbors (düşük = daha toleranslı)
FACE_MIN_SIZE = (50, 50)  # Min yüz boyutu (uzak hastalar için küçültüldü)


class GozAnaliz:
    """
    OpenCV Haar Cascade tabanlı göz açıklık analizi.
    Sadece OpenCV kullanır — ek bağımlılık yok.
    """

    # Sliding window parametreleri
    HISTORY_MAXLEN = 5         # Son N frame sonuçları
    MAJORITY_THRESHOLD = 3     # N frame'den kaç tanesi "açık" olmalı
    STALE_TIMEOUT = 1.0        # Yüz bulunamazsa son durumu koruma süresi (saniye)

    def __init__(self):
        self._face_cascade = None
        self._profile_cascade = None
        self._eye_cascade = None
        self._clahe = None
        self._loaded = False
        self.last_ear = 0.0
        self.last_goz_acik = False

        # Sliding window buffer (stabilite için)
        self._history = deque(maxlen=self.HISTORY_MAXLEN)
        self._last_face_ts = 0.0   # Son yüz tespit zamanı

    def load_model(self) -> bool:
        """Haar Cascade'leri yükle (frontal + profil + göz). RAM: ~2MB."""
        if self._loaded:
            return True

        try:
            import cv2

            face_cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            profile_cascade_path = cv2.data.haarcascades + "haarcascade_profileface.xml"
            eye_cascade_path = cv2.data.haarcascades + "haarcascade_eye.xml"

            self._face_cascade = cv2.CascadeClassifier(face_cascade_path)
            self._eye_cascade = cv2.CascadeClassifier(eye_cascade_path)

            # Profil yüz cascade (yan bakış desteği)
            self._profile_cascade = cv2.CascadeClassifier(profile_cascade_path)
            if self._profile_cascade.empty():
                log.warning("Profil cascade yüklenemedi — sadece frontal kullanılacak")
                self._profile_cascade = None

            if self._face_cascade.empty() or self._eye_cascade.empty():
                log.error("Haar cascade dosyaları yüklenemedi!")
                return False

            # CLAHE: Adaptif histogram eşitleme (düşük ışıkta çok daha iyi)
            self._clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

            self._loaded = True
            log.info("Haar Cascade göz modeli yüklendi ✓ (frontal + profil + CLAHE)")
            return True

        except Exception as e:
            log.error("Cascade yükleme hatası: %s", e)
            return False

    def unload_model(self):
        """Modeli bellekten kaldır."""
        self._face_cascade = None
        self._profile_cascade = None
        self._eye_cascade = None
        self._clahe = None
        self._loaded = False

    def _detect_eyes_open(self, gray_frame, face_rect) -> tuple:
        """
        Yüz ROI'sinde göz açıklığını analiz et.

        Yöntem: Haar cascade ile göz tespiti.
        Her iki göz de tespit ediliyorsa → göz açık.

        Returns:
            (goz_acik: bool, skor: float)
        """
        import cv2

        fx, fy, fw, fh = face_rect

        # Göz sadece yüzün üst yarısında aranır
        eye_region_y = fy + int(fh * 0.15)
        eye_region_h = int(fh * 0.40)
        face_roi = gray_frame[eye_region_y:eye_region_y + eye_region_h, fx:fx + fw]

        if face_roi.size == 0:
            return False, 0.0

        # Göz tespiti
        raw_eyes = self._eye_cascade.detectMultiScale(
            face_roi,
            scaleFactor=1.1,
            minNeighbors=6,  # Artırıldı: False positive'leri (yanlış tespitler) azaltır
            minSize=(20, 20),
            maxSize=(fw // 2, eye_region_h),
        )

        # Geometrik Doğrulama: Göz bölgeleri yatay dikdörtgen veya kareye yakın olmalıdır
        # Dikey uzun dikdörtgenler genelde sahte pozitif (burun kenarı, yanak gölgesi vb.) olur
        eyes = []
        for (ex, ey, ew, eh) in raw_eyes:
            if ew >= eh * 0.75:  # Genişlik, yüksekliğin %75'inden büyük olmalı
                eyes.append((ex, ey, ew, eh))

        if len(eyes) >= 2:
            # İki göz tespit edildi → göz açık
            # Skor hesaplama: göz boyutu / yüz boyutu oranı
            avg_eye_h = np.mean([e[3] for e in eyes[:2]])
            ratio = avg_eye_h / fh
            skor = min(ratio / EYE_OPEN_RATIO, 1.0)
            return True, round(float(skor), 4)

        elif len(eyes) == 1:
            # Tek göz tespit edildi (yarı açık veya yan bakış)
            ratio = eyes[0][3] / fh
            skor = min(ratio / EYE_OPEN_RATIO, 1.0) * 0.6
            return skor > 0.4, round(float(skor), 4)

        # Hiç göz tespit edilemedi → göz kapalı veya yüz çok küçük
        return False, 0.0

    def _find_faces_multipass(self, gray):
        """
        Multi-pass yüz tespiti: frontal → profil → relaxed parametreler.
        İlk geçişte bulunamazsa parametreleri gevşeterek tekrar dener.
        """
        import cv2

        # 1. Geçiş: Normal parametreler ile frontal yüz
        faces = self._face_cascade.detectMultiScale(
            gray,
            scaleFactor=FACE_SCALE,
            minNeighbors=FACE_MIN_NEIGHBORS,
            minSize=FACE_MIN_SIZE,
        )
        if len(faces) > 0:
            return faces

        # 2. Geçiş: Profil yüz cascade (yan bakış)
        if self._profile_cascade is not None:
            # Sol profil
            profile_faces = self._profile_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=FACE_MIN_SIZE,
            )
            if len(profile_faces) > 0:
                return profile_faces

            # Sağ profil (aynalama ile)
            flipped = cv2.flip(gray, 1)
            profile_faces_r = self._profile_cascade.detectMultiScale(
                flipped,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=FACE_MIN_SIZE,
            )
            if len(profile_faces_r) > 0:
                # x koordinatını aynala
                w = gray.shape[1]
                for i in range(len(profile_faces_r)):
                    profile_faces_r[i][0] = w - profile_faces_r[i][0] - profile_faces_r[i][2]
                return profile_faces_r

        # 3. Geçiş: Gevşetilmiş parametreler (son çare)
        faces = self._face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=2,
            minSize=(40, 40),
        )
        return faces

    def analiz_et(self, frame) -> tuple:
        """
        Frame üzerinde göz açıklık analizi yap.
        Sliding window + majority vote ile stabilize edilmiş.

        Args:
            frame: BGR formatında OpenCV frame

        Returns:
            (goz_acik: bool, ear_value: float)
        """
        if not self._loaded or frame is None:
            return self.last_goz_acik, self.last_ear

        try:
            import cv2

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Gürültü azaltma
            gray = cv2.GaussianBlur(gray, (3, 3), 0)

            # CLAHE: Adaptif histogram eşitleme (düşük ışık performansı)
            if self._clahe is not None:
                gray = self._clahe.apply(gray)
            else:
                gray = cv2.equalizeHist(gray)

            # Multi-pass yüz tespiti
            faces = self._find_faces_multipass(gray)

            if len(faces) == 0:
                # Yüz bulunamazsa: stale timeout içindeyse son durumu koru
                elapsed = time.time() - self._last_face_ts
                if elapsed < self.STALE_TIMEOUT and self._last_face_ts > 0:
                    # Son bilinen durumu koru (kamera titremesi, anlık kayıp vb.)
                    return self.last_goz_acik, self.last_ear
                # Timeout aşıldı → gerçekten yüz yok
                self._history.clear()
                self.last_ear = 0.0
                self.last_goz_acik = False
                return False, 0.0

            # Yüz bulundu → ts güncelle
            self._last_face_ts = time.time()

            # En büyük yüzü seç
            face = max(faces, key=lambda f: f[2] * f[3])

            raw_acik, skor = self._detect_eyes_open(gray, face)

            # Sliding window'a ekle
            self._history.append(raw_acik)

            # Majority vote: son N frame'in çoğunluğuna göre karar ver
            acik_sayisi = sum(1 for x in self._history if x)
            goz_acik = acik_sayisi >= self.MAJORITY_THRESHOLD

            self.last_ear = skor
            self.last_goz_acik = goz_acik

            return goz_acik, skor

        except Exception as e:
            log.warning("Göz analiz hatası: %s", e)
            return self.last_goz_acik, self.last_ear
