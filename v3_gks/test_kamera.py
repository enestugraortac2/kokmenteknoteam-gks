#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kamera Test Scripti (Entegre versiyon)
Kamerayı açar, frame okur, ilk ve son frame'i kaydeder.
Eğik açı (rotation/flip) desteği dahil.
"""

import os
import time
import cv2
import sys
from gks_modules.camera_manager import CameraManager

def test_single_camera(name, cam_id, rotation, hflip, vflip):
    print(f"\n--- {name} KAMERASI TEST EDİLİYOR ---")
    print(f"  Kamera ID: {cam_id}")
    print(f"  Rotation: {rotation}°")
    print(f"  HFlip: {hflip}, VFlip: {vflip}")

    cam = CameraManager(
        camera_id=cam_id,
        rotation=rotation,
        hflip=hflip,
        vflip=vflip,
    )
    if not cam.start():
        print(f"[HATA] {name} Kamerası başlatılamadı!")
        return

    frames_read = 0
    start_time = time.time()

    gui_support = False
    try:
        cv2.namedWindow(f"Test_{name}", cv2.WINDOW_AUTOSIZE)
        gui_support = True
    except:
        pass

    try:
        print(f"Kameradan görüntü alınıyor (3 saniye)...")
        while time.time() - start_time < 3.0:
            frame = cam.get_frame()
            if frame is not None:
                if frames_read == 0:
                    cv2.imwrite(f"/tmp/test_kamera_{name}_ilk.jpg", frame)
                    print(f"  [OK] İlk frame kaydedildi: {frame.shape}")

                frames_read += 1

                if gui_support:
                    try:
                        cv2.imshow(f"Test_{name}", frame)
                        cv2.waitKey(1)
                    except:
                        pass
            time.sleep(0.05)

        print(f"  [OK] Toplam {frames_read} frame okundu.")
        if frame is not None:
            cv2.imwrite(f"/tmp/test_kamera_{name}_son.jpg", frame)
            print(f"  [OK] Son frame /tmp/test_kamera_{name}_son.jpg olarak kaydedildi.")

    except KeyboardInterrupt:
        print("\nTest iptal edildi.")
    finally:
        if gui_support:
            try: cv2.destroyWindow(f"Test_{name}")
            except: pass
        cam.stop()
        print(f"  [OK] {name} Kamerası kapatıldı.")

def test_kamera():
    print("="*50)
    print(" KAMERA TESTİ (Çift Kamera: Göz & Motor)")
    print("="*50)

    # Göz Kamerası
    test_single_camera(
        name="GÖZ",
        cam_id=int(os.environ.get("CAMERA_GOZ_ID", "0")),
        rotation=int(os.environ.get("CAMERA_GOZ_ROTATION", "0")),
        hflip=os.environ.get("CAMERA_GOZ_HFLIP", "0") == "1",
        vflip=os.environ.get("CAMERA_GOZ_VFLIP", "0") == "1"
    )

    # Motor Kamerası
    test_single_camera(
        name="MOTOR",
        cam_id=int(os.environ.get("CAMERA_MOTOR_ID", "1")),
        rotation=int(os.environ.get("CAMERA_MOTOR_ROTATION", "0")),
        hflip=os.environ.get("CAMERA_MOTOR_HFLIP", "0") == "1",
        vflip=os.environ.get("CAMERA_MOTOR_VFLIP", "0") == "1"
    )

if __name__ == "__main__":
    test_kamera()
