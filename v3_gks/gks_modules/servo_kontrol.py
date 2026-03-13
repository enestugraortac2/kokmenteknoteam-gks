#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — Servo Kontrol (Ağrılı Uyaran)
GPIO üzerinden servo tetikleme.
gpiozero → RPi.GPIO → Simülasyon fallback zinciri.
"""

import time
import logging

log = logging.getLogger("Servo")

# GPIO ayarları
SERVO_PIN = 18
SERVO_DURATION = 3.0  # Ağrılı uyaran süresi (saniye)

# GPIO kütüphane kontrolü
try:
    from gpiozero import Servo
    from gpiozero import Device
    from gpiozero.pins.lgpio import LGPIOFactory
    Device.pin_factory = LGPIOFactory()
    _HAS_GPIOZERO = True
except ImportError:
    _HAS_GPIOZERO = False
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False


def stimulate_servo(duration: float = SERVO_DURATION) -> bool:
    """
    GPIO 18 üzerinden servoyu tetikleyerek ağrılı uyaran uygula.
    15° rotasyon → belirtilen süre → merkeze dönüş.

    Returns:
        True: Başarılı, False: Hata
    """
    log.info("🔴 SERVO: Ağrılı uyaran başlatılıyor (%.1fs)...", duration)

    try:
        if _HAS_GPIOZERO:
            servo = Servo(
                SERVO_PIN,
                min_pulse_width=0.5 / 1000,
                max_pulse_width=2.5 / 1000,
            )
            try:
                # Nötr konum
                servo.value = -1.0
                time.sleep(1)

                # 15° rotasyon
                log.info("[SERVO] Uyaran başlatıldı (15°)")
                servo.value = -0.7
                time.sleep(duration)

                # Merkeze dönüş
                log.info("[SERVO] Merkeze dönüş (0°)")
                servo.value = -1.0
                time.sleep(1.0)

                log.info("[SERVO] Tamamlandı ✓")
            finally:
                servo.value = None  # PWM serbest bırak
            return True

        elif _HAS_GPIO:
            log.info("[SERVO] gpiozero yok, RPi.GPIO kullanılıyor")
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(SERVO_PIN, GPIO.OUT)
            pwm = GPIO.PWM(SERVO_PIN, 50)
            pwm.start(7.5)  # Merkez

            pwm.ChangeDutyCycle(8.5)  # ~+15°
            time.sleep(duration / 2)
            pwm.ChangeDutyCycle(7.5)  # Merkez
            time.sleep(duration / 2)

            pwm.stop()
            GPIO.cleanup(SERVO_PIN)
            log.info("[SERVO] Tamamlandı ✓")
            return True

        else:
            log.warning("[SERVO] GPIO kütüphanesi yok — simülasyon")
            log.info("[SERVO-SIM] 15° rotasyon simülasyonu...")
            time.sleep(duration)
            log.info("[SERVO-SIM] Tamamlandı")
            return True

    except Exception as e:
        log.error("[SERVO] Hata: %s", e)
        return False


def cleanup_gpio():
    """GPIO kaynaklarını temizle."""
    try:
        if _HAS_GPIO:
            GPIO.cleanup()
    except Exception:
        pass
