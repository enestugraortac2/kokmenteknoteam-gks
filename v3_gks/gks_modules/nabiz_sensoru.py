#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — Nabız Sensörü (MAX30100/102)
I2C üzerinden nabız okuması + Medyan+EMA DSP filtresi.
v1'in sağlam sinyal işleme mantığı korunmuştur.
"""

import time
import logging
import threading
from collections import deque

log = logging.getLogger("Nabiz")

# Konfigürasyon
I2C_ADDR = 0x57
I2C_BUS = 1

# Algoritma parametreleri
MIN_IR_THRESHOLD = 15000   # Parmak varlık eşiği
PEAK_DELTA_THRESHOLD = -160  # Sinyal düşüş hızı eşiği
MIN_BPM = 45               # Min kabul edilebilir nabız
MAX_BPM = 115              # Max kabul edilebilir nabız

# SMBus2 kontrolü
try:
    import smbus2
    _HAS_SMBUS = True
except ImportError:
    _HAS_SMBUS = False


class NabizSensoru:
    """
    MAX30100 nabız sensörü okuyucu.
    Arka plan thread'inde çalışır, ana thread'i bloklamaz.
    Medyan + EMA filtre zinciri ile doğru BPM hesaplama.
    """

    def __init__(self):
        self._running = False
        self._thread = None
        self._bus = None

        # Dışarıdan okunabilir değerler
        self.bpm = 0
        self.durum = "BEKLENIYOR"  # BEKLENIYOR / OLCULUYOR / HAZIR / HATA

    def baslat(self) -> bool:
        """Sensörü başlat ve arka plan thread'ini başlat."""
        if self._running:
            return True

        if not _HAS_SMBUS:
            log.warning("smbus2 paketi yok — nabız simülasyon modunda")
            self.durum = "SIMULASYON"
            self._running = True
            self._thread = threading.Thread(target=self._sim_loop, daemon=True)
            self._thread.start()
            return True

        try:
            self._bus = smbus2.SMBus(I2C_BUS)
            # MAX30100 Setup
            self._bus.write_byte_data(I2C_ADDR, 0x06, 0x40)  # Reset
            time.sleep(0.2)
            self._bus.write_byte_data(I2C_ADDR, 0x06, 0x03)  # Mode: HR & SpO2
            self._bus.write_byte_data(I2C_ADDR, 0x09, 0x33)  # LED Power (50mA)
            log.info("MAX30100 sensörü başlatıldı ✓")

            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            log.warning("Sensör başlatılamadı: %s — simülasyona geçiliyor", e)
            self.durum = "SIMULASYON"
            self._running = True
            self._thread = threading.Thread(target=self._sim_loop, daemon=True)
            self._thread.start()
            return True

    def _read_loop(self):
        """Gerçek I2C okuma döngüsü + Medyan + EMA filtre."""
        last_beat_time = time.time()
        raw_beats = deque(maxlen=5)
        ema_beats = deque(maxlen=8)
        old_ir = 0
        in_measurement = False

        while self._running:
            try:
                data = self._bus.read_i2c_block_data(I2C_ADDR, 0x05, 4)
                ir = (data[0] << 8) | data[1]
            except Exception:
                self._setup_sensor()
                time.sleep(0.1)
                continue

            # Parmak kontrol
            if ir < MIN_IR_THRESHOLD:
                if in_measurement:
                    log.info("Parmak çekildi")
                    self.bpm = 0
                    self.durum = "BEKLENIYOR"
                in_measurement = False
                raw_beats.clear()
                ema_beats.clear()
                time.sleep(0.1)
                continue

            if not in_measurement:
                log.info("Parmak algılandı, stabilize ediliyor...")
                in_measurement = True
                old_ir = ir
                last_beat_time = time.time()
                self.durum = "OLCULUYOR"
                time.sleep(0.5)
                continue

            # Sinyal analizi — tepe algılama
            delta = ir - old_ir
            now = time.time()

            if delta < PEAK_DELTA_THRESHOLD:
                time_diff = now - last_beat_time
                min_diff = 60.0 / MAX_BPM
                max_diff = 60.0 / MIN_BPM

                if min_diff < time_diff < max_diff:
                    bpm_raw = 60.0 / time_diff
                    raw_beats.append(bpm_raw)

                    # Medyan filtre
                    if len(raw_beats) >= 3:
                        sorted_beats = sorted(list(raw_beats))
                        median_bpm = sorted_beats[len(sorted_beats) // 2]

                        # EMA filtre
                        ema_beats.append(median_bpm)
                        avg_bpm = sum(ema_beats) / len(ema_beats)
                        self.bpm = int(avg_bpm)
                        self.durum = "HAZIR"

                    last_beat_time = now
                    time.sleep(0.3)  # Debounce

            old_ir = ir
            time.sleep(0.01)

    def _sim_loop(self):
        """Simülasyon döngüsü (sensör yokken)."""
        import random
        while self._running:
            self.bpm = 75 + random.randint(-5, 5)
            self.durum = "SIMULASYON"
            time.sleep(2.0)

    def _setup_sensor(self):
        """Sensörü yeniden başlat."""
        try:
            if self._bus is None:
                self._bus = smbus2.SMBus(I2C_BUS)
            self._bus.write_byte_data(I2C_ADDR, 0x06, 0x40)
            time.sleep(0.2)
            self._bus.write_byte_data(I2C_ADDR, 0x06, 0x03)
            self._bus.write_byte_data(I2C_ADDR, 0x09, 0x33)
        except Exception:
            pass

    def durdur(self):
        """Sensörü durdur."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        log.info("Nabız sensörü durduruldu")
