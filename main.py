#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║  NeuroSense GKS — Ana Orkestratör (Main Orchestrator)           ║
║  Raspberry Pi 5 — Endüstriyel Sınıf, Fault-Tolerant             ║
║                                                                  ║
║  Glasgow Koma Skalası (GKS) Otomatik Ölçüm Sistemi              ║
║  Toplam GKS = Göz (1-4) + Motor (1-6) + Sözel (1-5) = 3-15     ║
╚══════════════════════════════════════════════════════════════════╝

Durum Makinesi (State Machine):

  ┌─────────────────┐     10s gözler açık      ┌──────────────────┐
  │  PASIF_GOZLEM   │ ─────────────────────────>│  MOTOR_KOMUT     │
  │  (Aşama 1)      │   Göz=4                   │  (Aşama 3)       │
  └────────┬────────┘                           └────────┬─────────┘
           │ Gözler kapalı (10s)                         │
           ▼                                             │
  ┌─────────────────┐     Göz açıldı /          ┌────────▼─────────┐
  │  SOZEL_UYARAN   │     Sözel cevap           │  AGRILI_UYARAN   │
  │  (Aşama 2)      │ ────────────────────────> │  (Aşama 4)       │
  └─────────────────┘                           └────────┬─────────┘
                                                         │
                                                         ▼
                                                ┌──────────────────┐
                                                │  FINAL_RAPOR     │
                                                └──────────────────┘

Modul Yönetimi:
  - subprocess.Popen ile 3 bağımsız süreç
  - Watchdog: 2s aralıkla poll(), çökmüşü yeniden başlat
  - Graceful Shutdown: SIGTERM → terminate → kill → GPIO cleanup
  - IPC: /dev/shm/gks_skor.json (RAM disk)
"""

import os
import sys
import time
import json
import errno
import atexit
import signal
import threading
import subprocess
import logging
from pathlib import Path
from enum import Enum, auto

# ─── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[MAIN %(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

# ─── Platform-safe imports ──────────────────────────────────────
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

# ─── GPIO Setup ─────────────────────────────────────────────────
try:
    from gpiozero import Servo
    _HAS_GPIOZERO = True
except ImportError:
    _HAS_GPIOZERO = False

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False


# ═══════════════════════════════════════════════════════════════
#  Konfigürasyon
# ═══════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent
SHM_PATH = Path(os.environ.get("GKS_SHM_PATH", "/dev/shm/gks_skor.json"))

# Modül tanımları: (script_path, env_overrides)
MODULES = {
    "goz": (ROOT / "goz_takip.py", {"CAMERA_ID": "1"}),
    "motor": (ROOT / "motor_takip.py", {"CAMERA_ID": "0"}),
    "ekran": (ROOT / "ekran.py", {}),
}
SES_SCRIPT = ROOT / "ses_motoru.py"

# GPIO ayarları
SERVO_PIN = 18
SERVO_DURATION = 3.0  # Ağrılı uyaran süresi (saniye)

# Durum makinesi zamanlayıcıları
PASIF_GOZLEM_SURESI = 10.0    # Aşama 1: Gözlem süresi (saniye)
MOTOR_KOMUT_BEKLEME = 5.0     # Aşama 3: Motor komut bekleme süresi
WATCHDOG_INTERVAL = 2.0       # Watchdog kontrol aralığı (saniye)

# Logs
LOGS_DIR = ROOT / "logs"


# ═══════════════════════════════════════════════════════════════
#  Durum Makinesi Tanımı
# ═══════════════════════════════════════════════════════════════

class GKSState(Enum):
    """GKS muayene durum makinesi aşamaları."""
    BASLANGIC = auto()
    PASIF_GOZLEM = auto()      # Aşama 1
    SOZEL_UYARAN = auto()      # Aşama 2
    MOTOR_KOMUT = auto()       # Aşama 3
    AGRILI_UYARAN = auto()     # Aşama 4
    FINAL_RAPOR = auto()
    TAMAMLANDI = auto()


# ═══════════════════════════════════════════════════════════════
#  IPC: RAM Disk Okuma/Yazma
# ═══════════════════════════════════════════════════════════════

def read_shm() -> dict:
    """RAM disk'ten SHM verilerini oku (non-blocking shared lock)."""
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
    """RAM disk'e fcntl exclusive lock ile atomik yazma."""
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
                                    time.sleep(0.1)
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
                            json.dump(data, f, ensure_ascii=False)
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
                            json.dump(updates, f, ensure_ascii=False)
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
                time.sleep(0.1)
        return False
    except Exception as e:
        log.error("SHM kritik hata: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════
#  Servo Kontrolü (Ağrılı Uyaran)
# ═══════════════════════════════════════════════════════════════

def stimulate_servo(duration: float = SERVO_DURATION) -> bool:
    """
    GPIO 18 üzerinden servoyu tetikleyerek ağrılı uyaran uygula.
    RPi 5 üzerindeki PWM hatalarını aşmak için doğrudan OutputDevice ile Bit-Banging yapılır.
    """
    log.info("🔴 SERVO: Ağrılı uyaran başlatılıyor (%0.1fs)...", duration)
    write_shm({"servo_aktif": True, "servo_ts": time.time()})

    try:
        from gpiozero import OutputDevice
        
        period = 0.020        # 50Hz = 20ms
        pulse_center = 0.0015 # 1.5ms = 0 derece
        pulse_15deg = 0.0019  # 1.9ms = Sağa dönüş
        
        out = OutputDevice(SERVO_PIN)
        
        log.info("[SERVO] 15° rotasyon...")
        end_time = time.time() + (duration / 2)
        while time.time() < end_time:
            out.on()
            time.sleep(pulse_15deg)
            out.off()
            time.sleep(period - pulse_15deg)

        log.info("[SERVO] Merkeze dönüş...")
        end_time = time.time() + (duration / 2)
        while time.time() < end_time:
            out.on()
            time.sleep(pulse_center)
            out.off()
            time.sleep(period - pulse_center)
            
        out.close()
        log.info("[SERVO] Tamamlandı ✓")
        return True

    except Exception as e:
        log.error("[SERVO] Hata: %s", e)
        return False
    finally:
        write_shm({"servo_aktif": False})


# ═══════════════════════════════════════════════════════════════
#  Modül Yöneticisi (Process Manager)
# ═══════════════════════════════════════════════════════════════

class ModuleManager:
    """
    Alt süreçleri başlatma, izleme ve yeniden başlatma.
    Watchdog entegrasyonu ile fault-tolerant.
    """

    def __init__(self):
        self._procs = {}        # key → {"proc": Popen, "env": dict, ...}
        self._log_handles = []  # Açık log dosya handle'ları

    def start_module(self, key: str, script_path: Path, env_overrides: dict = None):
        """Modülü subprocess olarak başlat."""
        if not script_path.exists():
            log.error("Modül dosyası bulunamadı: %s", script_path)
            return False

        env = os.environ.copy()
        env["GKS_SHM_PATH"] = str(SHM_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        if env_overrides:
            env.update({k: str(v) for k, v in env_overrides.items()})

        # Log dosyaları
        LOGS_DIR.mkdir(exist_ok=True)
        out_log = LOGS_DIR / f"{key}.out.log"
        err_log = LOGS_DIR / f"{key}.err.log"

        out_f = open(out_log, "a+", buffering=1, encoding="utf-8")
        err_f = open(err_log, "a+", buffering=1, encoding="utf-8")
        self._log_handles.extend([out_f, err_f])

        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdout=out_f,
            stderr=err_f,
            env=env,
        )

        self._procs[key] = {
            "proc": proc,
            "script": script_path,
            "env_overrides": env_overrides or {},
            "out_f": out_f,
            "err_f": err_f,
            "restart_count": 0,
            "started_at": time.time(),
        }

        log.info("Modül '%s' başlatıldı (PID: %d)", key, proc.pid)
        return True

    def check_and_restart(self):
        """
        Watchdog: Çökmüş modülleri tespit edip yeniden başlat.
        """
        for key, info in list(self._procs.items()):
            proc = info["proc"]
            if proc.poll() is not None:
                # Süreç sonlanmış
                exit_code = proc.returncode
                log.warning("⚠️ Modül '%s' çöktü (exit: %d, restart #%d)",
                            key, exit_code, info["restart_count"] + 1)

                # Eski log handle'ları kapat
                for h in [info.get("out_f"), info.get("err_f")]:
                    try:
                        if h:
                            h.close()
                    except Exception:
                        pass

                # Kamerayı serbest bırak
                self._release_camera_resources(key)

                # Yeniden başlat
                info["restart_count"] += 1
                time.sleep(1)  # Kısa bekleme

                self.start_module(
                    key, info["script"], info["env_overrides"]
                )
                # restart_count'u koru
                self._procs[key]["restart_count"] = info["restart_count"]

    def _release_camera_resources(self, key: str):
        """Çökmüş modülün kamera kaynağını serbest bırak."""
        try:
            cam_id = self._procs[key]["env_overrides"].get("CAMERA_ID")
            if cam_id is not None:
                video_dev = f"/dev/video{cam_id}"
                subprocess.run(
                    f"fuser -k {video_dev}",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                log.info("Kamera %s serbest bırakıldı", video_dev)
        except Exception as e:
            log.warning("Kamera serbest bırakma hatası: %s", e)

    def stop_module(self, key: str):
        """Modülü düzgün şekilde durdur."""
        info = self._procs.get(key)
        if not info:
            return

        proc = info["proc"]
        if proc.poll() is None:
            log.info("Modül '%s' durduruluyor (PID: %d)...", key, proc.pid)
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    log.warning("Modül '%s' terminate'e yanıt vermedi, kill...", key)
                    proc.kill()
                    proc.wait(timeout=2)
            except Exception as e:
                log.warning("Modül '%s' durdurma hatası: %s", key, e)

        # Log handle'ları kapat
        for h in [info.get("out_f"), info.get("err_f")]:
            try:
                if h:
                    h.close()
            except Exception:
                pass

    def stop_all(self):
        """Tüm modülleri durdur."""
        log.info("Tüm modüller durduruluyor...")
        for key in list(self._procs.keys()):
            self.stop_module(key)
        self._procs.clear()
        log.info("Tüm modüller durduruldu ✓")

    def is_running(self, key: str) -> bool:
        info = self._procs.get(key)
        if not info:
            return False
        return info["proc"].poll() is None


# ═══════════════════════════════════════════════════════════════
#  Ses Komutu Çalıştırıcı
# ═══════════════════════════════════════════════════════════════

def ses_speak(text: str, timeout: int = 30) -> bool:
    """ses_motoru.py'yi speak modunda çalıştır."""
    try:
        cmd = [sys.executable, str(SES_SCRIPT), "--mode", "speak", "--text", text]
        env = os.environ.copy()
        env["GKS_SHM_PATH"] = str(SHM_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(cmd, env=env, timeout=timeout,
                                capture_output=True)
        return result.returncode == 0
    except Exception as e:
        log.warning("Ses komutu hatası: %s", e)
        return False


def ses_interview(timeout: int = 300) -> bool:
    """ses_motoru.py'yi interview modunda çalıştır."""
    try:
        cmd = [sys.executable, str(SES_SCRIPT), "--mode", "interview"]
        env = os.environ.copy()
        env["GKS_SHM_PATH"] = str(SHM_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(cmd, env=env, timeout=timeout)
        return result.returncode == 0
    except Exception as e:
        log.warning("Sözel test hatası: %s", e)
        return False


def ses_listen(duration: int = 5, category: str = "DURUM",
               timeout: int = 30) -> bool:
    """ses_motoru.py'yi listen modunda çalıştır."""
    try:
        cmd = [sys.executable, str(SES_SCRIPT),
               "--mode", "listen",
               "--duration", str(duration),
               "--category", category]
        env = os.environ.copy()
        env["GKS_SHM_PATH"] = str(SHM_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(cmd, env=env, timeout=timeout,
                                capture_output=True)
        return result.returncode == 0
    except Exception as e:
        log.warning("Dinleme hatası: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════
#  GKS Durum Makinesi (State Machine)
# ═══════════════════════════════════════════════════════════════

class GKSExamStateMachine:
    """
    Glasgow Koma Skalası muayene durum makinesi.

    Aşama 1 (Pasif Gözlem): EAR gözlem → Göz=4 mümkün mü?
    Aşama 2 (Sözel Uyaran): Sesli uyaran → Göz=3, Sözel=1-5
    Aşama 3 (Motor Komut):  Sözel komut → Motor=6 mümkün mü?
    Aşama 4 (Ağrılı Uyaran): Servo → Motor=1-5, Göz=1-2
    Final:   Toplam GKS = Göz + Motor + Sözel
    """

    def __init__(self, module_manager: ModuleManager):
        self.mgr = module_manager
        self.state = GKSState.BASLANGIC
        self._stop = threading.Event()

        # Nihai puanlar
        self.goz_puan = 1
        self.motor_puan = 1
        self.sozel_puan = 1

    def run(self):
        """Durum makinesini baştan sona çalıştır."""
        log.info("╔═══════════════════════════════════════╗")
        log.info("║  NeuroSense GKS MUAYENE BAŞLIYOR      ║")
        log.info("╚═══════════════════════════════════════╝")

        try:
            self._transition(GKSState.PASIF_GOZLEM)
            self._asama1_pasif_gozlem()

            if self._stop.is_set():
                return

            # Aşama 1 sonrası karar
            if self.goz_puan == 4:
                # Gözler spontan açık → Aşama 3'e geç
                log.info("Gözler spontan açık (Göz=4), Aşama 3'e geçiliyor...")
                self._transition(GKSState.MOTOR_KOMUT)
                self._asama3_motor_komut()
            else:
                # Gözler kapalı → Aşama 2 (Sözel Uyaran)
                self._transition(GKSState.SOZEL_UYARAN)
                self._asama2_sozel_uyaran()

                if self._stop.is_set():
                    return

                # Aşama 2 sonrası → Aşama 3 (Motor Komut)
                self._transition(GKSState.MOTOR_KOMUT)
                self._asama3_motor_komut()

            if self._stop.is_set():
                return

            # Aşama 4: Ağrılı uyaran gerekli mi?
            if self.motor_puan < 6 or self.goz_puan < 3:
                self._transition(GKSState.AGRILI_UYARAN)
                self._asama4_agrili_uyaran()

            if self._stop.is_set():
                return

            # Sözel test henüz yapılmadıysa (Aşama 1'den direkt 3'e geçilirse)
            if self.sozel_puan == 1 and self.goz_puan == 4:
                log.info("Sözel test başlatılıyor (tam mülakat)...")
                ses_interview()
                for attempt in range(3):
                    time.sleep(1)
                    shm = read_shm()
                    skor = int(shm.get("ses_skor", 0))
                    if skor > 0:
                        self.sozel_puan = skor
                        break
                else:
                    self.sozel_puan = 1
                log.info("Sözel skor: %d/5", self.sozel_puan)

            # Final rapor
            self._transition(GKSState.FINAL_RAPOR)
            self._final_rapor()
            self._transition(GKSState.TAMAMLANDI)

            # Ekranda sonuçları görebilmek için script kapanmadan önce bekle
            log.info("Muayene bitti. LCD ekranı için 25 saniye bekleniyor...")
            time.sleep(25)

        except KeyboardInterrupt:
            log.info("Muayene kullanıcı tarafından kesildi.")
        except Exception as e:
            log.error("Durum makinesi hatası: %s", e, exc_info=True)

    def stop(self):
        """Durum makinesini durdur."""
        self._stop.set()

    def _transition(self, new_state: GKSState):
        """Durum geçişi yap ve logla."""
        old = self.state
        self.state = new_state
        log.info("━━━ DURUM GEÇİŞİ: %s → %s ━━━", old.name, new_state.name)
        write_shm({"durum": new_state.name, "durum_ts": time.time()})

    # ─── Aşama 1: Pasif Gözlem ─────────────────────────────

    def _asama1_pasif_gozlem(self):
        """
        10 saniye boyunca göz EAR değerini izle.
        Gözler sürekli açıksa → Göz = 4
        """
        log.info("[AŞAMA 1] Pasif gözlem başlıyor (%0.0fs)...", PASIF_GOZLEM_SURESI)

        acik_baslangic = None
        _goz_acik_sayac = 0  # Ardışık "açık" okuma sayısı (hysteresis)
        deadline = time.time() + PASIF_GOZLEM_SURESI

        while time.time() < deadline and not self._stop.is_set():
            shm = read_shm()
            goz_acik = shm.get("goz_acik", False)
            ear = shm.get("goz_ear", 0.0)

            if goz_acik:
                _goz_acik_sayac += 1
                # 3 ardışık okumada açık => gerçekten açık kabul et
                if _goz_acik_sayac >= 3:
                    if acik_baslangic is None:
                        acik_baslangic = time.time()
                    else:
                        acik_sure = time.time() - acik_baslangic
                        if acik_sure >= 2.5:
                            self.goz_puan = 4
                            log.info("✅ Gözler spontan açık tespit edildi → Göz = 4")
                            return
            else:
                _goz_acik_sayac = 0
                # Sadece göz gerçekten KAPALI tespit edildiyse timer'ı resetle
                # ear > 0.0 demek yüz bulundu ama göz kapalı
                # ear == 0.0 demek goz_takip yüz bulamadı — timer'ı resetleme
                if ear > 0.0:
                    acik_baslangic = None  # Gerçek kapalı göz
                # else: yüz bulunamadı, son durumu koru

            # Watchdog kontrolü
            self.mgr.check_and_restart()
            time.sleep(0.5)

        if self.goz_puan < 4:
            log.info("Gözler 10 saniye boyunca sürekli açık kalmadı → Aşama 2'ye geçilecek")

    # ─── Aşama 2: Sözel Uyaran ─────────────────────────────

    def _asama2_sozel_uyaran(self):
        """
        Sesli uyaran ver: "Beni duyuyor musunuz?"
        - Göz açılırsa → Göz = 3
        - Sözel yanıt analizi → Sözel = 1-5
        """
        log.info("[AŞAMA 2] Sözel uyaran başlıyor...")

        # 1. Sesli uyaran ver
        ses_speak("Beni duyuyor musunuz? Gözlerinizi açın.")

        # Sesin hastaya ulaşması için bekleme
        time.sleep(1.5)

        # 2. 5 saniye bekle — göz açılıyor mu? (hysteresis ile)
        log.info("Göz tepkisi bekleniyor (5s)...")
        deadline = time.time() + 5.0
        goz_acildi = False
        _goz_acik_sayac = 0

        while time.time() < deadline and not self._stop.is_set():
            shm = read_shm()
            if shm.get("goz_acik", False):
                _goz_acik_sayac += 1
                if _goz_acik_sayac >= 3:  # 3 ardışık okumada açık
                    goz_acildi = True
                    self.goz_puan = 3
                    log.info("✅ Sesli uyaranla göz açıldı → Göz = 3")
                    break
            else:
                _goz_acik_sayac = 0
            time.sleep(0.25)

        if not goz_acildi:
            log.info("Sesli uyaranla göz açılmadı")

        # 3. Sözel test (tam mülakat)
        log.info("Sözel mülakat başlıyor...")
        ses_interview()

        # 4. SHM'den sonucu oku (hemen + 5 tekrar deneme)
        # ses_interview() senkron — bitince ses_skor SHM'de olmalı
        shm = read_shm()
        skor = 0
        try:
            skor = int(shm.get("ses_skor", 0))
        except (ValueError, TypeError):
            skor = 0

        if skor > 0:
            self.sozel_puan = skor
        else:
            # Yazılmamış olabilir — birkaç deneme daha
            for attempt in range(5):
                time.sleep(0.5)
                shm = read_shm()
                try:
                    skor = int(shm.get("ses_skor", 0))
                except (ValueError, TypeError):
                    skor = 0
                if skor > 0:
                    self.sozel_puan = skor
                    break
            else:
                self.sozel_puan = 1  # Hiç okunamazsa fallback
                log.warning("SHM'den ses_skor okunamadı, fallback: 1")

        log.info("Sözel skor: %d/5", self.sozel_puan)

    # ─── Aşama 3: Motor Komut ──────────────────────────────

    def _asama3_motor_komut(self):
        """
        Ses modülü "Sağ elinizi kaldırın" der.
        Motor modülü (YOLO) 5 saniye izler.
        El kalkarsa → Motor = 6
        """
        log.info("[AŞAMA 3] Motor komut testi başlıyor...")

        # Motor modülüne komut sinyali gönder (SHM üzerinden)
        write_shm({"motor_komut_aktif": True, "motor_komut_ts": time.time()})

        # Sesli komut ver
        ses_speak("Sağ elinizi kaldırın.")

        # 5 saniye izle
        log.info("Motor tepkisi bekleniyor (%0.0fs)...", MOTOR_KOMUT_BEKLEME)
        deadline = time.time() + MOTOR_KOMUT_BEKLEME
        motor_ok = False

        while time.time() < deadline and not self._stop.is_set():
            shm = read_shm()
            motor_skor = shm.get("motor_skor", 1)
            try:
                motor_skor = int(motor_skor)
            except (ValueError, TypeError):
                motor_skor = 1

            if motor_skor >= 6:
                motor_ok = True
                self.motor_puan = 6
                log.info("✅ Motor komut uyumu tespit edildi → Motor = 6")
                break

            self.mgr.check_and_restart()
            time.sleep(0.25)

        # Komut sinyalini kapat
        write_shm({"motor_komut_aktif": False})

        if not motor_ok:
            log.info("Motor komuta uymadı → Ağrılı uyaran gerekebilir")

    # ─── Aşama 4: Ağrılı Uyaran ───────────────────────────

    def _asama4_agrili_uyaran(self):
        """
        Servo tetikle, 3 saniye ağrı uygula.
        Tepkileri izle:
          - Göz açılırsa → Göz = 2
          - El ağrıya giderse → Motor = 5 (Lokalizasyon)
          - El kaçarsa → Motor = 4 (Normal fleksiyon)
          - Anormal kasılma → Motor = 3 veya 2
          - Hiç tepki yoksa → Motor = 1
        """
        log.info("[AŞAMA 4] Ağrılı uyaran başlıyor...")

        # Ağrı öncesi göz durumunu kaydet
        pre_shm = read_shm()
        pre_goz = pre_shm.get("goz_acik", False)

        # Servo tetikle (non-blocking thread ile)
        servo_thread = threading.Thread(target=stimulate_servo, args=(SERVO_DURATION,))
        servo_thread.start()

        # Ağrı süresince tepkileri izle
        log.info("Tepkiler izleniyor (%.0fs)...", SERVO_DURATION + 2)
        izleme_suresi = SERVO_DURATION + 2.0
        deadline = time.time() + izleme_suresi

        goz_tepki = False
        en_iyi_motor = 1

        while time.time() < deadline and not self._stop.is_set():
            shm = read_shm()

            # Göz tepkisi
            goz_acik_simdi = shm.get("goz_acik", False)
            if not pre_goz and goz_acik_simdi and not goz_tepki:
                goz_tepki = True
                if self.goz_puan < 2:
                    self.goz_puan = 2
                log.info("✅ Ağrıyla göz açıldı → Göz = 2")

            # Motor tepkisi
            motor_skor = shm.get("motor_skor", 1)
            try:
                motor_skor = int(motor_skor)
            except (ValueError, TypeError):
                motor_skor = 1

            if motor_skor > en_iyi_motor:
                en_iyi_motor = motor_skor

            self.mgr.check_and_restart()
            time.sleep(0.25)

        servo_thread.join(timeout=5)

        # Motor puanı güncelle
        if en_iyi_motor > self.motor_puan:
            self.motor_puan = min(en_iyi_motor, 5)  # Ağrıyla max 5 (lokalizasyon)

        # Göz açılmadıysa → Göz = 1
        if not goz_tepki and self.goz_puan < 2:
            self.goz_puan = 1
            log.info("Ağrıyla göz açılmadı → Göz = 1")

        # Motor tepkisi yoksa → Motor = 1
        if en_iyi_motor <= 1:
            self.motor_puan = 1
            log.info("Ağrıya motor tepki yok → Motor = 1")

        log.info("Ağrılı uyaran sonuçları: Göz=%d, Motor=%d",
                 self.goz_puan, self.motor_puan)

    # ─── Final Rapor ────────────────────────────────────────

    def _final_rapor(self):
        """GKS toplam puanı hesapla ve raporla."""
        toplam = self.goz_puan + self.motor_puan + self.sozel_puan

        # Severity classification
        if toplam <= 8:
            severity = "AGIR KOMA"
            emoji = "[!]"
        elif toplam <= 12:
            severity = "ORTA KOMA"
            emoji = "[~]"
        else:
            severity = "HAFIF / NORMAL"
            emoji = "[+]"

        log.info("")
        log.info("╔══════════════════════════════════════════════════╗")
        log.info("║           NeuroSense GKS FİNAL RAPORU           ║")
        log.info("╠══════════════════════════════════════════════════╣")
        log.info("║  Göz Yanıtı (E):     %d / 4                     ║", self.goz_puan)
        log.info("║  Motor Yanıt (M):    %d / 6                     ║", self.motor_puan)
        log.info("║  Sözel Yanıt (V):    %d / 5                     ║", self.sozel_puan)
        log.info("╠══════════════════════════════════════════════════╣")
        log.info("║  TOPLAM GKS:         %d / 15  %s %s", toplam, emoji, severity)
        log.info("╚══════════════════════════════════════════════════╝")
        # Geçmiş kayıtları SHM'den oku ve yenisini ekle
        shm = read_shm()
        gecmis = shm.get("gks_gecmis", [])
        yeni_kayit = {
            "toplam": toplam,
            "severity": severity,
            "ts": time.strftime("%H:%M")
        }
        gecmis.append(yeni_kayit)
        if len(gecmis) > 4:
            gecmis.pop(0) # Sadece son 4 ölçümü tut

        # SHM'ye final puanları yaz
        write_shm({
            "gks_goz": self.goz_puan,
            "gks_motor": self.motor_puan,
            "gks_sozel": self.sozel_puan,
            "gks_toplam": toplam,
            "gks_severity": severity,
            "gks_ts": time.time(),
            "gks_tamamlandi": True,
            "gks_gecmis": gecmis
        })

        # Sesli rapor
        ses_speak(
            f"GKS muayenesi tamamlandı. "
            f"Göz {self.goz_puan}, Motor {self.motor_puan}, "
            f"Sözel {self.sozel_puan}. "
            f"Toplam 15 üzerinden {toplam}. "
            f"Durum: {severity}."
        )

        # Rich tablo (opsiyonel) - encoding fix
        try:
            import io
            from rich.console import Console
            from rich.table import Table
            buf = io.StringIO()
            console = Console(file=buf, force_terminal=False, no_color=True)
            table = Table(title="NeuroSense GKS Raporu", show_lines=True)
            table.add_column("Parametre", width=20)
            table.add_column("Puan", justify="center")
            table.add_column("Max", justify="center")
            table.add_row("Goz (E)", str(self.goz_puan), "4")
            table.add_row("Motor (M)", str(self.motor_puan), "6")
            table.add_row("Sozel (V)", str(self.sozel_puan), "5")
            table.add_row(
                f"{emoji} TOPLAM",
                str(toplam),
                "15",
            )
            console.print(table)
            for line in buf.getvalue().splitlines():
                log.info(line)
        except ImportError:
            pass


# ═══════════════════════════════════════════════════════════════
#  Watchdog Thread
# ═══════════════════════════════════════════════════════════════

def watchdog_loop(mgr: ModuleManager, stop_event: threading.Event):
    """Modül sağlık kontrolü — çökmüş modülleri yeniden başlat."""
    while not stop_event.is_set():
        try:
            mgr.check_and_restart()
        except Exception as e:
            log.error("Watchdog hatası: %s", e)
        stop_event.wait(WATCHDOG_INTERVAL)


# ═══════════════════════════════════════════════════════════════
#  Graceful Shutdown
# ═══════════════════════════════════════════════════════════════

_module_manager = None
_stop_event = threading.Event()


def _cleanup():
    """Tüm kaynakları temizle."""
    global _module_manager
    _stop_event.set()

    if _module_manager is not None:
        _module_manager.stop_all()
        _module_manager = None

    # GPIO cleanup
    try:
        if _HAS_GPIO:
            GPIO.cleanup()
    except Exception:
        pass

    # SHM temizle
    try:
        if SHM_PATH.exists():
            SHM_PATH.unlink()
    except Exception:
        pass

    log.info("Tüm kaynaklar temizlendi ✓")


def _signal_handler(signum, frame):
    log.info("Sinyal alındı (%s), kapatılıyor...", signum)
    _cleanup()
    sys.exit(0)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)
atexit.register(_cleanup)


# ═══════════════════════════════════════════════════════════════
#  Ana Fonksiyon
# ═══════════════════════════════════════════════════════════════

def main():
    global _module_manager

    log.info("╔══════════════════════════════════════════════════╗")
    log.info("║  NeuroSense GKS Sistemi Başlatılıyor...         ║")
    log.info("║  Platform: Raspberry Pi 5                       ║")
    log.info("║  IPC: %s", str(SHM_PATH))
    log.info("╚══════════════════════════════════════════════════╝")

    # ─── SHM dosyasını temizle (eski verileri sil) ──────────
    try:
        if SHM_PATH.exists():
            try:
                SHM_PATH.unlink()
            except PermissionError:
                # root'a ait olabilir, sudo ile sil
                subprocess.run(["sudo", "rm", "-f", str(SHM_PATH)],
                               timeout=5, capture_output=True)
        with open(SHM_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)
        # Tüm alt süreçlerin yazabilmesi için izinleri aç
        os.chmod(str(SHM_PATH), 0o666)
        log.info("SHM dosyası hazırlandı (chmod 666): %s", SHM_PATH)
    except Exception as e:
        log.warning("SHM başlatma hatası: %s", e)

    # ─── Kamera kaynaklarını temizle ────────────────────────
    log.info("Eski kamera süreçleri temizleniyor...")
    try:
        subprocess.run(
            "fuser -k /dev/video* 2>/dev/null",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        pass

    # ─── Modül yöneticisini başlat ──────────────────────────
    _module_manager = ModuleManager()

    # Göz modülünü başlat
    for key, (script, env) in MODULES.items():
        _module_manager.start_module(key, script, env)
        time.sleep(1)  # Modüller arası kısa bekleme (kamera çakışmasını önle)

    # ─── Watchdog thread başlat ─────────────────────────────
    watchdog_thread = threading.Thread(
        target=watchdog_loop,
        args=(_module_manager, _stop_event),
        daemon=True,
    )
    watchdog_thread.start()
    log.info("Watchdog başlatıldı ✓")

    # ─── Modüllerin başlaması için bekle ────────────────────
    log.info("Modüllerin başlaması bekleniyor (3s)...")
    time.sleep(3)

    # ─── GKS Durum Makinesini Çalıştır ──────────────────────
    try:
        exam = GKSExamStateMachine(_module_manager)
        exam.run()
    except KeyboardInterrupt:
        log.info("Ctrl+C ile kapatılıyor...")
    except Exception as e:
        log.error("Kritik hata: %s", e, exc_info=True)
    finally:
        _cleanup()
        log.info("NeuroSense GKS Sistemi kapatıldı.")


if __name__ == "__main__":
    main()
