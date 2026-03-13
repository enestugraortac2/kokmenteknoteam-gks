#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — Ortak IPC Modülü
RAM disk üzerinden modüller arası veri paylaşımı.
Tüm modüller bu dosyayı kullanır — kod tekrarı sıfır.
"""

import os
import sys
import time
import json
import errno
import logging
from pathlib import Path

log = logging.getLogger("IPC")

# Platform-safe fcntl
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

# Varsayılan SHM yolu
SHM_PATH = Path(os.environ.get("GKS_SHM_PATH", "/dev/shm/gks_skor.json"))


class _NumpySafeEncoder(json.JSONEncoder):
    """numpy tiplerini native Python tiplerine donusturur."""
    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        return super().default(obj)


def read_shm() -> dict:
    """
    RAM disk'ten SHM verilerini oku (non-blocking shared lock).
    Hata durumunda boş dict döner — çağıran kodu kırmaz.
    """
    if not SHM_PATH.exists():
        return {}
    try:
        with open(SHM_PATH, "r", encoding="utf-8") as f:
            if _HAS_FCNTL:
                try:
                    fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except OSError as e:
                    if e.errno in (errno.EACCES, errno.EAGAIN):
                        return {}
                    return {}
            try:
                return json.load(f)
            except (json.JSONDecodeError, ValueError):
                return {}
            finally:
                if _HAS_FCNTL:
                    try:
                        fcntl.flock(f, fcntl.LOCK_UN)
                    except Exception:
                        pass
    except Exception:
        return {}


def write_shm(updates: dict) -> bool:
    """
    RAM disk'e fcntl exclusive lock ile atomik yazma.
    Mevcut verilerin üzerine merge eder (update).
    """
    try:
        SHM_PATH.parent.mkdir(parents=True, exist_ok=True)
        max_attempts = 5

        for attempt in range(max_attempts):
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
                            except (json.JSONDecodeError, ValueError):
                                data = {}
                            data.update(updates)
                            f.seek(0)
                            f.truncate()
                            json.dump(data, f, ensure_ascii=False, cls=_NumpySafeEncoder)
                            f.flush()
                            os.fsync(f.fileno())
                            return True
                        finally:
                            if _HAS_FCNTL:
                                try:
                                    fcntl.flock(f, fcntl.LOCK_UN)
                                except Exception:
                                    pass
                else:
                    with open(SHM_PATH, "w", encoding="utf-8") as f:
                        if _HAS_FCNTL:
                            fcntl.flock(f, fcntl.LOCK_EX)
                        try:
                            json.dump(updates, f, ensure_ascii=False, cls=_NumpySafeEncoder)
                            f.flush()
                            os.fsync(f.fileno())
                            return True
                        finally:
                            if _HAS_FCNTL:
                                try:
                                    fcntl.flock(f, fcntl.LOCK_UN)
                                except Exception:
                                    pass
            except Exception as e:
                log.warning("SHM yazma denemesi %d: %s", attempt + 1, e)
                time.sleep(0.05)
        return False
    except Exception as e:
        log.error("SHM kritik hata: %s", e)
        return False


def clear_shm():
    """SHM dosyasını sıfırla."""
    try:
        if SHM_PATH.exists():
            SHM_PATH.unlink()
        with open(SHM_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f, cls=_NumpySafeEncoder)
        return True
    except Exception as e:
        log.warning("SHM temizleme hatası: %s", e)
        return False
