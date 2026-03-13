#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sensor ve Servo Test Scripti
Nabiz sensoru (MAX30100) ve Servo Motoru (SG5010) test eder.
"""

import time
import sys
from gks_modules.nabiz_sensoru import NabizSensoru
from gks_modules.servo_kontrol import stimulate_servo, cleanup_gpio

def test_sensorler():
    print("="*40)
    print(" SENSOR VE SERVO TESTI")
    print("="*40)
    
    # 1. Nabiz Sensoru Testi
    print("\n[1/2] Nabiz Sensoru (MAX30100) Testi...")
    nabiz = NabizSensoru()
    nabiz.baslat()
    
    print("Nabiz okunuyor (10 saniye boyunca)... Parmaginizi sensore koyun.")
    try:
        for i in range(10):
            bpm = nabiz.bpm
            if bpm > 0:
                print(f"  [{i+1}/10] Okunan Nabiz: {bpm} BPM")
            else:
                print(f"  [{i+1}/10] Okunuyor... (Parmak bekleniyor)")
            time.sleep(1.0)
    except Exception as e:
        print(f"[HATA] Nabiz okuma hatasi: {e}")
    finally:
        nabiz.durdur()
        print("Nabiz sensoru kapatildi.")
        
    # 2. Servo Motor Testi
    print("\n[2/2] Servo Motor (Agrili Uyaran) Testi...")
    print("Servo hareket edecek. Lutfen pin baglantilarini kontrol edin (Orn: GPIO 18).")
    try:
        print("Servo calistiriliyor (3 saniyelik hareket)...")
        stimulate_servo(duration=3.0)
        print("  [OK] Servo hareketi basariyla gonderildi.")
    except Exception as e:
        print(f"[HATA] Servo calistirma hatasi: {e}")
    finally:
        cleanup_gpio()
        
    print("\nTest tamamlandi.")

if __name__ == "__main__":
    test_sensorler()
