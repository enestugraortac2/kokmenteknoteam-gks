#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║  NeuroSense GKS — Nabız ve Oksijen Sensörü (MAX30100/102)    ║
║  Raspberry Pi 5 — Robust DSP (Medyan + EMA Filtre)           ║
╚══════════════════════════════════════════════════════════════╝

Görev:
  - I2C (Bus 1, Adres 0x57) üzerinden nabız sensörünü okur.
  - Sinyal düşüş hızından yola çıkarak BPM hesaplar.
  - Hatalı ölçümleri ve zıplamaları önlemek için Medyan ve EMA
    (Üstel Hareketli Ortalama) filtreleri uygular.
  - IPC üzerinden /dev/shm/gks_skor.json hedefine yazar.
"""

import os
import sys
import time
import json
import errno
import atexit
import signal
import logging
from pathlib import Path
from collections import deque

# ─── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[NABIZ %(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nabiz")

# ─── Platform-safe IPC ──────────────────────────────────────────
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

# ─── SMBus2 ─────────────────────────────────────────────────────
try:
    import smbus2
    _HAS_SMBUS = True
except ImportError:
    _HAS_SMBUS = False

# ─── Konfigürasyon ─────────────────────────────────────────────
SHM_PATH = Path(os.environ.get("GKS_SHM_PATH", "/dev/shm/gks_skor.json"))
I2C_ADDR = 0x57
I2C_BUS = 1

# Algoritma parametreleri
MIN_IR_THRESHOLD = 15000     # Parmak varlık eşiği
PEAK_DELTA_THRESHOLD = -160  # Sinyal düşüş hızı eşiği
MIN_BPM = 45                 # Kabul edilebilir min nabız
MAX_BPM = 115                # Kabul edilebilir max nabız (dinlenik hasta)

# ─── Global State ──────────────────────────────────────────────
_running = True
_bus = None


# ═══════════════════════════════════════════════════════════════
#  IPC: RAM Disk'e Yazma
# ═══════════════════════════════════════════════════════════════

def write_shm(updates: dict) -> bool:
    try:
        SHM_PATH.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(5):
            try:
                if SHM_PATH.exists():
                    with open(SHM_PATH, "r+", encoding="utf-8") as f:
                        if _HAS_FCNTL:
                            try:
                                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except OSError as e:
                                if e.errno in (errno.EACCES, errno.EAGAIN):
                                    time.sleep(0.05)
                                    continue
                                raise
                        try:
                            f.seek(0)
                            try:
                                data = json.load(f)
                            except ValueError:
                                data = {}
                            data.update(updates)
                            f.seek(0)
                            f.truncate()
                            json.dump(data, f, ensure_ascii=False)
                            f.flush()
                            os.fsync(f.fileno())
                            return True
                        finally:
                            if _HAS_FCNTL:
                                try:
                                    fcntl.flock(f, fcntl.LOCK_UN)
                                except:
                                    pass
                else:
                    with open(SHM_PATH, "w", encoding="utf-8") as f:
                        if _HAS_FCNTL:
                            fcntl.flock(f, fcntl.LOCK_EX)
                        json.dump(updates, f, ensure_ascii=False)
                        if _HAS_FCNTL:
                            fcntl.flock(f, fcntl.LOCK_UN)
                        return True
            except Exception:
                time.sleep(0.05)
    except Exception as e:
        log.error("SHM hatası: %s", e)
    return False


# ═══════════════════════════════════════════════════════════════
#  I2C Sensör Kurulumu
# ═══════════════════════════════════════════════════════════════

def setup_sensor():
    """MAX30100 yapılandırması"""
    global _bus
    if not _HAS_SMBUS:
        return False
        
    try:
        if _bus is None:
            _bus = smbus2.SMBus(I2C_BUS)
            
        _bus.write_byte_data(I2C_ADDR, 0x06, 0x40) # Reset
        time.sleep(0.2)
        _bus.write_byte_data(I2C_ADDR, 0x06, 0x03) # Mode: HR & SpO2
        _bus.write_byte_data(I2C_ADDR, 0x09, 0x33) # Leds Power (50mA)
        log.info("Sensör başarıyla başlatıldı.")
        return True
    except Exception as e:
        log.warning("Sensör bağlanamadı: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════
#  Graceful Shutdown
# ═══════════════════════════════════════════════════════════════

def _signal_handler(signum, frame):
    global _running
    log.info("Sinyal alındı, kapatılıyor...")
    _running = False
    write_shm({"nabiz_durum": "KAPALI"})
    sys.exit(0)

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ═══════════════════════════════════════════════════════════════
#  Ana Döngü ve Filtreleme
# ═══════════════════════════════════════════════════════════════

def run():
    global _running, _bus
    
    log.info("Nabız Takip Modülü başlatılıyor...")
    
    if not _HAS_SMBUS:
        log.error("smbus2 paketi bulunamadı! Simülasyon moduna geçiliyor.")
    else:
        setup_sensor()

    # Değişken Tanimlamalari
    last_beat_time = time.time()
    raw_beats = deque(maxlen=5)        # Medyan filtre için son 5 ham BPM
    ema_beats = deque(maxlen=8)        # EMA (Hareketli ortalama) için son 8 stabil BPM
    
    old_ir = 0
    in_measurement = False
    
    # Başlangıç SHM yazısı
    write_shm({"nabiz_bpm": 0, "nabiz_durum": "BEKLENIYOR"})

    while _running:
        # 1. Sensörden Veri Okuma
        try:
            if _bus is not None:
                data = _bus.read_i2c_block_data(I2C_ADDR, 0x05, 4)
                ir = (data[0] << 8) | data[1]
            else:
                # Simülasyon verisi (75 BPM)
                ir = 25000 if int(time.time() * 10) % 8 == 0 else 24800
                time.sleep(0.01)
        except Exception:
            # I2C hatası → Yeniden bağlanmayı dene
            setup_sensor()
            time.sleep(0.1)
            continue

        # 2. Parmak Durum Kontrolü
        if ir < MIN_IR_THRESHOLD:
            if in_measurement:
                log.info("Parmak çekildi. GKS dinleme beklemede.")
                write_shm({"nabiz_bpm": 0, "nabiz_durum": "BEKLENIYOR"})
                
            in_measurement = False
            raw_beats.clear()
            ema_beats.clear()
            time.sleep(0.1)
            continue
            
        if not in_measurement:
            log.info("Okuma başladı, sensör stabilize ediliyor...")
            in_measurement = True
            old_ir = ir
            last_beat_time = time.time()
            write_shm({"nabiz_durum": "OLCULUYOR"})
            time.sleep(0.5) # Alışma süresi
            continue

        # 3. Sinyal Analizi
        delta = ir - old_ir
        now = time.time()
        
        # Tepe (Peak) Algılama — Keskin düşüşler kalbin attığı andır
        if delta < PEAK_DELTA_THRESHOLD: 
            time_diff = now - last_beat_time
            
            # Filtre Step 1: Zaman farkı gerçekçi bir kalbe mi ait? (45-115 BPM aralığı)
            min_diff = 60.0 / MAX_BPM
            max_diff = 60.0 / MIN_BPM
            
            if min_diff < time_diff < max_diff:
                bpm = 60.0 / time_diff
                raw_beats.append(bpm)
                
                # Filtre Step 2: Medyan Filtre — Veride zıplama varsa 5 verinin ortasındakini alır
                if len(raw_beats) >= 3:
                    sorted_beats = sorted(list(raw_beats))
                    median_bpm = sorted_beats[len(sorted_beats)//2]
                    
                    # Filtre Step 3: Üstel Hareketli Ortalama (EMA) — Akıcı grafik için
                    ema_beats.append(median_bpm)
                    avg_bpm = sum(ema_beats) / len(ema_beats)
                    
                    final_bpm = int(avg_bpm)
                    
                    write_shm({
                        "nabiz_bpm": final_bpm,
                        "nabiz_durum": "HAZIR"
                    })
                    log.info("♥ Nabız: %d BPM", final_bpm)
                
                # Hata önleme — Bir sonraki sinyale kadar sensör okumasını kilitle (Debounce)
                last_beat_time = now
                time.sleep(0.3) 

        old_ir = ir
        time.sleep(0.01)

if __name__ == "__main__":
    run()
