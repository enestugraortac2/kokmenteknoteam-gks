#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor Analiz Test Scripti
ONNX Runtime ile pose analizi yapar, el hareketini tespit eder.
"""

import time
import os
import sys
from gks_modules.camera_manager import CameraManager
from gks_modules.motor_analiz import MotorAnaliz

# ONNX GPU uyarisini kapat
os.environ.setdefault("ORT_LOG_LEVEL", "WARNING")

def test_motor():
    print("="*40)
    print(" MOTOR ANALIZ (ONNX-YOLOv8) TESTI")
    print("="*40)
    
    ai = MotorAnaliz()
    print("Model yukleniyor...")
    if not ai.load_model():
        print("[HATA] Model yuklenemedi!")
        return
        
    cam_id = int(os.environ.get("CAMERA_MOTOR_ID", "1"))
    rotation = int(os.environ.get("CAMERA_MOTOR_ROTATION", "0"))
    hflip = os.environ.get("CAMERA_MOTOR_HFLIP", "0") == "1"
    vflip = os.environ.get("CAMERA_MOTOR_VFLIP", "0") == "1"

    print(f"  Kamera ID: {cam_id}")
    print(f"  Rotation: {rotation}°, HFlip: {hflip}, VFlip: {vflip}")

    cam = CameraManager(
        camera_id=cam_id,
        rotation=rotation,
        hflip=hflip,
        vflip=vflip,
    )
    if not cam.start():
        print("[HATA] Kamera baslatilamadi!")
        return

    print("Kameradan vucut iskeleti araniyor (10 saniye boyutunca)...")
    print("Lutfen kameraya dogru hareket edin (alinizi kaldirin).")
    
    start_time = time.time()
    max_skor = 1
    
    try:
        while time.time() - start_time < 10.0:
            frame = cam.get_frame()
            if frame is not None:
                kpts = ai.pose_tespit_et(frame)
                if kpts is not None:
                    skor, durum = ai.analiz_et(kpts, komut_aktif=True, servo_aktif=False)
                    print(f"  Kisi tespit edildi. GKS Motor Skor: {skor}/6 - Durum: {durum}")
                    if skor > max_skor:
                        max_skor = skor
                else:
                    print("  Kisi tespit edilemedi.")
            time.sleep(0.5)
            
        print("\nTest tamamlandi.")
        print(f"Elde edilen en yuksek motor skoru: {max_skor}/6")
            
    except KeyboardInterrupt:
        print("\nTest iptal edildi.")
    finally:
        cam.stop()
        print("Kamera kapatildi.")

if __name__ == "__main__":
    test_motor()
