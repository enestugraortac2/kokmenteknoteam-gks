#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Göz Analiz Test Scripti (Entegre versiyon)
Kameradan yüz ve göz tespiti yapar. Göz açıklık (EAR) oranını ekrana basar.
Yeni özellikler: CLAHE, Multi-pass, Profil yüz, Eğik açı desteği.
"""

import os
import time
import sys
from gks_modules.camera_manager import CameraManager
from gks_modules.goz_analiz import GozAnaliz

def test_goz():
    print("="*50)
    print(" GÖZ ANALİZ (HAAR CASCADE + CLAHE + PROFİL) TESTİ")
    print("="*50)

    # Kamera konfigürasyonu (env variables)
    cam_id = int(os.environ.get("CAMERA_GOZ_ID", "0"))
    rotation = int(os.environ.get("CAMERA_GOZ_ROTATION", "0"))
    hflip = os.environ.get("CAMERA_GOZ_HFLIP", "0") == "1"
    vflip = os.environ.get("CAMERA_GOZ_VFLIP", "0") == "1"

    print(f"  Kamera ID: {cam_id}")
    print(f"  Rotation: {rotation}°, HFlip: {hflip}, VFlip: {vflip}")

    ai = GozAnaliz()
    print("Model yükleniyor...")
    if not ai.load_model():
        print("[HATA] Model yüklenemedi!")
        return

    cam = CameraManager(
        camera_id=cam_id,
        rotation=rotation,
        hflip=hflip,
        vflip=vflip,
    )
    if not cam.start():
        print("[HATA] Kamera başlatılamadı!")
        return

    print("\nKameradan yüz ve göz aranıyor (15 saniye boyunca)...")
    print("Lütfen kameraya bakın. Yakın, uzak ve yan açılardan deneyin.")
    print("-" * 50)

    start_time = time.time()
    yuz_bulma_sayisi = 0
    toplam_frame = 0

    try:
        while time.time() - start_time < 15.0:
            frame = cam.get_frame()
            if frame is not None:
                toplam_frame += 1
                acik_mi, ear = ai.analiz_et(frame)
                durum = "AÇIK" if acik_mi else "KAPALI"

                if ear > 0:
                    yuz_bulma_sayisi += 1
                    print(f"  EAR: {ear:.3f} -> Göz: {durum}")
                else:
                    print("  Yüz veya göz tespit edilemedi.")
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nTest iptal edildi.")
    finally:
        cam.stop()

        # Özet rapor
        oran = (yuz_bulma_sayisi / toplam_frame * 100) if toplam_frame > 0 else 0
        print("-" * 50)
        print(f"  Toplam frame: {toplam_frame}")
        print(f"  Yüz bulunan frame: {yuz_bulma_sayisi} ({oran:.0f}%)")
        print("Kamera kapatıldı. Test tamamlandı.")

if __name__ == "__main__":
    test_goz()
