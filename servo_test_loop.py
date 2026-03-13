#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Servo Motor Surekli Test Scripti (Raspberry Pi 5 Uyumlu)
Sinyal (Sari/Turuncu) Kablo: Board Pin 12 (GPIO 18)
"""
import time
import sys
from gpiozero import AngularServo
from gpiozero.pins.lgpio import LGPIOFactory

# Raspberry Pi 5 (BCM2712) icin lgpio factory kullanimi zorunlu
from gpiozero import Device
Device.pin_factory = LGPIOFactory()

SERVO_PIN = 18

def baslat():
    print(f"[{time.strftime('%H:%M:%S')}] Servo Motor Testi Baslatiliyor (Pi 5 Uyumlu)...")
    print(f"Lutfen {SERVO_PIN}. GPIO Pinine (Board Pin 12) takili oldugundan emin olun.")
    print("Durdurmak icin: Ctrl + C\n")
    
    # Standart MG996R / SG90 servo pulse genislikleri (0.5ms ile 2.5ms arasi)
    servo = None
    try:
        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180, 
                             min_pulse_width=0.0005, max_pulse_width=0.0025)
        
        # Surekli hareket dongusu
        while True:
            print("Saga donuyor (0 derece)...")
            servo.angle = 0
            time.sleep(1)
            
            print("Ortaya donuyor (90 derece)...")
            servo.angle = 90
            time.sleep(1)

            print("Sola donuyor (180 derece)...")
            servo.angle = 180
            time.sleep(1)
            
            print("Ortaya donuyor (90 derece)...")
            servo.angle = 90
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nTest durduruldu.")
    except Exception as e:
        print(f"\nHATA: {e}")
    finally:
        if servo is not None:
            servo.close()
            print("GPIO pinleri temizlendi.")

if __name__ == "__main__":
    baslat()
