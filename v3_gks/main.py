#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 - Ana Orkestrator
Raspberry Pi 5 (8GB) - Optimize Monolitik Mimari
GKS = Goz (1-4) + Motor (1-6) + Sozel (1-5) = 3-15
"""

import os
import sys
import time
import signal
import atexit
import logging
import threading
import subprocess
from pathlib import Path
from enum import Enum, auto

# --- ONNX Runtime GPU uyarisini sustur (Pi 5'te GPU yok) ---
os.environ.setdefault("ORT_LOG_LEVEL", "WARNING")

# --- Logging (temiz ASCII cikti) ---
from rich.logging import RichHandler
from rich.console import Console

# Rich Console
console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)]
)
log = logging.getLogger("GKS")

# ─── Modül imports ──────────────────────────────────────────────
from gks_modules.ipc import read_shm, write_shm, clear_shm
from gks_modules.camera_manager import CameraManager
from gks_modules.goz_analiz import GozAnaliz
from gks_modules.motor_analiz import MotorAnaliz
from gks_modules.ses_analiz import SesAnaliz
from gks_modules.nabiz_sensoru import NabizSensoru
from gks_modules.ekran import EkranKontrol
from gks_modules.servo_kontrol import stimulate_servo, cleanup_gpio

# ─── Konfigürasyon ──────────────────────────────────────────────
CAMERA_GOZ_ID = int(os.environ.get("CAMERA_GOZ_ID", "0"))
CAMERA_GOZ_ROTATION = int(os.environ.get("CAMERA_GOZ_ROTATION", "0"))
CAMERA_GOZ_HFLIP = os.environ.get("CAMERA_GOZ_HFLIP", "0") == "1"
CAMERA_GOZ_VFLIP = os.environ.get("CAMERA_GOZ_VFLIP", "0") == "1"

CAMERA_MOTOR_ID = int(os.environ.get("CAMERA_MOTOR_ID", "1"))
CAMERA_MOTOR_ROTATION = int(os.environ.get("CAMERA_MOTOR_ROTATION", "0"))
CAMERA_MOTOR_HFLIP = os.environ.get("CAMERA_MOTOR_HFLIP", "0") == "1"
CAMERA_MOTOR_VFLIP = os.environ.get("CAMERA_MOTOR_VFLIP", "0") == "1"

# Zamanlayıcılar
PASIF_GOZLEM_SURESI = 10.0     # Aşama 1 gözlem süresi (saniye)
GOZ_ACIK_ESIK_SURE = 3.0       # Gözlerin sürekli açık kalması gereken minimum süre
SOZEL_GOZ_BEKLEME = 5.0        # Aşama 2 göz açılma bekleme
MOTOR_KOMUT_BEKLEME = 5.0      # Aşama 3 motor komut bekleme
SERVO_SURESI = 3.0             # Aşama 4 ağrılı uyaran süresi
BEKLEME_SURESI_DAKIKA = 1      # Testler arası bekleme (dakika)

# Frame hızı
FRAME_DELAY = 1.0 / 15  # ~15 FPS hedef


class GKSState(Enum):
    BASLANGIC = auto()
    PASIF_GOZLEM = auto()
    SOZEL_UYARAN = auto()
    MOTOR_KOMUT = auto()
    AGRILI_UYARAN = auto()
    FINAL_RAPOR = auto()
    TAMAMLANDI = auto()


# ═══════════════════════════════════════════════════════════════
#  GKS Sistemi
# ═══════════════════════════════════════════════════════════════

class GKSSystem:
    """
    NeuroSense GKS v3 Monolitik Sistem.
    Tek process, threading ile paralel donanım erişimi.
    """

    def __init__(self):
        console.print("")
        console.rule("[bold cyan]NeuroSense GKS v3 Başlatılıyor[/bold cyan]")
        log.info("  Platform: Raspberry Pi 5 - 8GB RAM")

        # Donanım bileşenleri
        self.kamera_goz = CameraManager(
            camera_id=CAMERA_GOZ_ID,
            rotation=CAMERA_GOZ_ROTATION,
            hflip=CAMERA_GOZ_HFLIP,
            vflip=CAMERA_GOZ_VFLIP,
        )
        self.kamera_motor = CameraManager(
            camera_id=CAMERA_MOTOR_ID,
            rotation=CAMERA_MOTOR_ROTATION,
            hflip=CAMERA_MOTOR_HFLIP,
            vflip=CAMERA_MOTOR_VFLIP,
        )
        self.ekran = EkranKontrol()
        self.nabiz = NabizSensoru()

        # AI analiz modülleri
        self.goz_ai = GozAnaliz()
        self.motor_ai = MotorAnaliz()
        self.ses_ai = SesAnaliz()

        # Durum
        self.state = GKSState.BASLANGIC
        self._stop_event = threading.Event()

        # GKS puanları
        self.goz_puan = 1
        self.motor_puan = 1
        self.sozel_puan = 1
        self.gks_gecmisi = []  # Zamana bağlı grafik için

    def setup(self):
        """Tüm bileşenleri başlat."""
        # SHM temizle
        clear_shm()

        # Eski kamera süreçlerini temizle
        try:
            subprocess.run(
                "fuser -k /dev/video* 2>/dev/null",
                shell=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5,
            )
        except Exception:
            pass

        # Ekranı başlat (arka plan thread)
        self.ekran.baslat()

        # Nabız sensörünü başlat (arka plan thread)
        self.nabiz.baslat()

        # AI modellerini yükle
        log.info("AI modelleri yukleniyor...")
        self.goz_ai.load_model()
        # Motor ve Ses modelleri lazy - gerektiginde yuklenir
        
        # Kameraları sürekli izleme için baştan başlat
        log.info("Kameralar baslatiliyor (Surekli izleme)...")
        self.kamera_goz.start()
        self.kamera_motor.start()
        
        log.info("Sistem hazir.")

    def _transition(self, new_state: GKSState):
        """Durum geçişi yap."""
        old = self.state
        self.state = new_state
        log.info("--- DURUM: %s -> %s ---", old.name, new_state.name)
        self._update_ekran(durum=new_state.name)
        write_shm({"durum": new_state.name, "durum_ts": time.time()})

    def _update_ekran(self, **kwargs):
        """Ekranı güncelle + nabız verisini ekle."""
        kwargs.setdefault("nabiz_bpm", self.nabiz.bpm)
        kwargs.setdefault("goz", self.goz_puan)
        kwargs.setdefault("motor", self.motor_puan)
        kwargs.setdefault("sozel", self.sozel_puan)
        kwargs.setdefault("history", self.gks_gecmisi)
        self.ekran.update(**kwargs)

    # ─── Aşama 1: Pasif Gözlem ─────────────────────────────

    def _asama1_pasif_gozlem(self):
        """10s boyunca göz EAR'ı izle. Sürekli açıksa Göz=4."""
        console.rule("[bold magenta]AŞAMA 1: Pasif Gözlem[/bold magenta]")
        log.info("[ASAMA 1] Pasif gozlem basliyor (%.0fs)...", PASIF_GOZLEM_SURESI)

        acik_baslangic = None
        deadline = time.time() + PASIF_GOZLEM_SURESI

        while time.time() < deadline and not self._stop_event.is_set():
            loop_start = time.monotonic()

            frame = self.kamera_goz.get_frame()
            if frame is not None:
                goz_acik, ear = self.goz_ai.analiz_et(frame)
                self._update_ekran(ear=ear, goz_acik=goz_acik)
                write_shm({"goz_ear": ear, "goz_acik": goz_acik, "goz_ts": time.time()})

                if goz_acik:
                    if acik_baslangic is None:
                        acik_baslangic = time.time()
                    elif time.time() - acik_baslangic >= GOZ_ACIK_ESIK_SURE:
                        self.goz_puan = 4
                        log.info("[OK] Gozler %.1fs boyunca acik -> Goz = 4",
                                 time.time() - acik_baslangic)
                        return
                else:
                    # Sadece göz gerçekten KAPALI tespit edildiyse timer'ı resetle
                    # ear > 0 demek yüz bulundu ama göz kapalı
                    # ear == 0 demek yüz bulunamadı — bu durumda son durumu koru
                    if ear > 0.0:
                        acik_baslangic = None  # Gerçek kapalı göz, timer reset
                    # else: yüz bulunamadı, timer'ı RESETLEME

            # FPS kontrol
            elapsed = time.monotonic() - loop_start
            sleep_time = FRAME_DELAY - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        if self.goz_puan < 4:
            log.info("Gozler surekli acik kalmadi -> Asama 2'ye geciliyor")

    # ─── Aşama 2: Sözel Uyaran ─────────────────────────────

    def _asama2_sozel_uyaran(self):
        """Sesli uyaran ver, göz açılma ve sözel yanıt izle."""
        console.rule("[bold cyan]AŞAMA 2: Sözel Uyaran & Mülakat[/bold cyan]")
        log.info("[ASAMA 2] Sozel uyaran basliyor...")

        # Ses motorunu yükle (lazy loading)
        self.ses_ai.load_models()

        # Sesli uyaran ver
        self.ses_ai.konus("Beni duyuyor musunuz? Gözlerinizi açın.")

        # Sesin hastaya ulaşması için kısa bekleme
        time.sleep(1.5)

        # Göz tepkisi kontrol (5s) — hysteresis ile (tek frame = karar değil)
        log.info("Goz tepkisi bekleniyor (%.0fs)...", SOZEL_GOZ_BEKLEME)
        deadline = time.time() + SOZEL_GOZ_BEKLEME
        goz_acildi = False
        _goz_acik_sayac = 0

        while time.time() < deadline and not self._stop_event.is_set():
            frame = self.kamera_goz.get_frame()
            if frame is not None:
                goz_acik, ear = self.goz_ai.analiz_et(frame)
                self._update_ekran(ear=ear, goz_acik=goz_acik)

                if goz_acik:
                    _goz_acik_sayac += 1
                    if _goz_acik_sayac >= 3:  # 3 ardışık frame açık
                        goz_acildi = True
                        self.goz_puan = 3
                        log.info("[OK] Sesli uyaranla goz acildi -> Goz = 3")
                        break
                else:
                    _goz_acik_sayac = 0
            time.sleep(0.25)

        if not goz_acildi:
            log.info("Sesli uyaranla goz acilmadi")

        # Sozel mulakat
        log.info("Sozel mulakat basliyor...")
        self.sozel_puan = self.ses_ai.mulakat_yap()
        log.info("Sozel skor: %d/5", self.sozel_puan)
        self._update_ekran(sozel=self.sozel_puan)

    # ─── Aşama 3: Motor Komut ──────────────────────────────

    def _asama3_motor_komut(self):
        """Sözel komut ver, motor tepkisini izle."""
        console.rule("[bold blue]AŞAMA 3: Motor Komut (Lokalizasyon/Uyum)[/bold blue]")
        log.info("[ASAMA 3] Motor komut testi basliyor...")

        # Motor AI yükle (lazy loading)
        self.motor_ai.load_model()

        # Ses motorunun yüklü olduğundan emin ol
        self.ses_ai.load_models()

        write_shm({"motor_komut_aktif": True, "motor_komut_ts": time.time()})
        self.ses_ai.konus("Sağ elinizi kaldırın.")

        deadline = time.time() + MOTOR_KOMUT_BEKLEME
        motor_ok = False

        while time.time() < deadline and not self._stop_event.is_set():
            loop_start = time.monotonic()
            frame = self.kamera_motor.get_frame()

            if frame is not None:
                # Pose tespiti
                keypoints = self.motor_ai.pose_tespit_et(frame)
                if keypoints is not None:
                    skor, durum = self.motor_ai.analiz_et(
                        keypoints, komut_aktif=True, servo_aktif=False
                    )
                    write_shm({"motor_skor": skor, "motor_durum": durum,
                               "motor_ts": time.time()})

                    if skor >= 6:
                        motor_ok = True
                        self.motor_puan = 6
                        log.info("[OK] Motor komut uyumu -> Motor = 6")
                        break

            elapsed = time.monotonic() - loop_start
            if FRAME_DELAY - elapsed > 0:
                time.sleep(FRAME_DELAY - elapsed)

        write_shm({"motor_komut_aktif": False})
        self._update_ekran(motor=self.motor_puan)

        if not motor_ok:
            log.info("Motor komuta uymadi -> Agrili uyaran gerekebilir")

    # ─── Aşama 4: Ağrılı Uyaran ───────────────────────────

    def _asama4_agrili_uyaran(self):
        """Servo tetikle, göz ve motor tepkilerini izle."""
        console.rule("[bold red]AŞAMA 4: Ağrılı Uyaran (Omuz Bastırma)[/bold red]")
        log.info("[ASAMA 4] Agrili uyaran basliyor...")

        # Motor AI yüklü olduğundan emin ol
        self.motor_ai.load_model()

        # Ağrı öncesi göz durumu
        pre_goz = self.goz_ai.last_goz_acik
        write_shm({"servo_aktif": True, "servo_ts": time.time()})

        # Servo tetikle (ayrı thread)
        servo_thread = threading.Thread(target=stimulate_servo, args=(SERVO_SURESI,))
        servo_thread.start()

        # Tepkileri izle
        izleme_suresi = SERVO_SURESI + 2.0
        deadline = time.time() + izleme_suresi
        goz_tepki = False
        en_iyi_motor = 1

        while time.time() < deadline and not self._stop_event.is_set():
            loop_start = time.monotonic()
            
            # Göz ve Motor için aynı anda veri al
            frame_goz = self.kamera_goz.get_frame()
            frame_motor = self.kamera_motor.get_frame()

            if frame_goz is not None:
                # Göz tepkisi
                goz_acik, ear = self.goz_ai.analiz_et(frame_goz)
                self._update_ekran(ear=ear, goz_acik=goz_acik)

                if not pre_goz and goz_acik and not goz_tepki:
                    goz_tepki = True
                    if self.goz_puan < 2:
                        self.goz_puan = 2
                    log.info("[OK] Agriyla goz acildi -> Goz = 2")

            if frame_motor is not None:
                # Motor tepkisi
                keypoints = self.motor_ai.pose_tespit_et(frame_motor)
                if keypoints is not None:
                    skor, durum = self.motor_ai.analiz_et(
                        keypoints, komut_aktif=False, servo_aktif=True
                    )
                    if skor > en_iyi_motor:
                        en_iyi_motor = skor

            elapsed = time.monotonic() - loop_start
            if FRAME_DELAY - elapsed > 0:
                time.sleep(FRAME_DELAY - elapsed)

        servo_thread.join(timeout=5)
        write_shm({"servo_aktif": False})

        # Motor puanı güncelle
        if en_iyi_motor > self.motor_puan:
            self.motor_puan = min(en_iyi_motor, 5)

        # Göz açılmadıysa
        if not goz_tepki and self.goz_puan < 2:
            self.goz_puan = 1
            log.info("Agriyla goz acilmadi -> Goz = 1")

        if en_iyi_motor <= 1:
            self.motor_puan = 1
            log.info("Agriya motor tepki yok -> Motor = 1")

        log.info("Agrili uyaran sonuclari: Goz=%d, Motor=%d",
                 self.goz_puan, self.motor_puan)
        self._update_ekran(goz=self.goz_puan, motor=self.motor_puan)

    # ─── Final Rapor ────────────────────────────────────────

    def _final_rapor(self):
        """GKS toplam puanı hesapla ve raporla."""
        toplam = self.goz_puan + self.motor_puan + self.sozel_puan

        if toplam <= 8:
            severity = "AGIR KOMA"
        elif toplam <= 12:
            severity = "ORTA KOMA"
        else:
            severity = "HAFIF / NORMAL"

        # Geçmişe ekle (grafik için, maksimum 20 veri tut)
        self.gks_gecmisi.append(toplam)
        if len(self.gks_gecmisi) > 20:
            self.gks_gecmisi.pop(0)

        console.print("")
        console.rule(f"[bold green]NeuroSense GKS FİNAL RAPORU[/bold green]")
        
        # Rich tablo
        from rich.table import Table
        
        table = Table(title="Hasta GKS Sonucu", show_lines=True)
        table.add_column("Parametre", style="cyan", width=20)
        table.add_column("Puan", style="bold green", justify="center")
        table.add_column("Max", style="dim", justify="center")
        table.add_row("Göz Yanıtı (E)", str(self.goz_puan), "4")
        table.add_row("Motor Yanıt (M)", str(self.motor_puan), "6")
        table.add_row("Sözel Yanıt (V)", str(self.sozel_puan), "5")
        table.add_row("TOPLAM SKOR", f"[bold]{toplam}[/bold]", "15", style="bold white on blue")
        table.add_row("KLİNİK DURUM", f"[bold]{severity}[/bold]", "", style="bold black on yellow")
        
        console.print(table)

        # SHM güncelle
        write_shm({
            "gks_goz": self.goz_puan,
            "gks_motor": self.motor_puan,
            "gks_sozel": self.sozel_puan,
            "gks_toplam": toplam,
            "gks_severity": severity,
            "gks_ts": time.time(),
            "gks_tamamlandi": True,
        })

        # Ekranı güncelle
        self._update_ekran(
            goz=self.goz_puan, motor=self.motor_puan,
            sozel=self.sozel_puan, tamamlandi=True,
        )

        # Sonucu sesli olarak duyur
        try:
            self.ses_ai.load_models()
            self.ses_ai.konus(
                f"GKS muayenesi tamamlandi. "
                f"Goz {self.goz_puan}, Motor {self.motor_puan}, "
                f"Sozel {self.sozel_puan}. "
                f"Toplam 15 uzerinden {toplam}. "
                f"Durum: {severity}."
            )
        except Exception as e:
            log.warning("Sesli duyuru yapilamadi: %s", e)

    # ─── Tek GKS Testi ──────────────────────────────────────

    def run_gks_test(self):
        """Tek bir tam GKS muayenesi çalıştır."""
        self.goz_puan = 1
        self.motor_puan = 1
        self.sozel_puan = 1
        self._update_ekran(goz=1, motor=1, sozel=1, tamamlandi=False)

        # Aşama 1: Pasif Gözlem
        self._transition(GKSState.PASIF_GOZLEM)
        self._asama1_pasif_gozlem()

        if self._stop_event.is_set():
            return

        # Aşama 1 sonrası karar
        if self.goz_puan == 4:
            # Gözler spontan açık → Aşama 3
            log.info("Gozler spontan acik (Goz=4), Asama 3'e geciliyor...")
            self._transition(GKSState.MOTOR_KOMUT)
            self._asama3_motor_komut()
        else:
            # Gözler kapalı → Aşama 2
            self._transition(GKSState.SOZEL_UYARAN)
            self._asama2_sozel_uyaran()

            if self._stop_event.is_set():
                return

            # Aşama 2 sonrası → Aşama 3
            self._transition(GKSState.MOTOR_KOMUT)
            self._asama3_motor_komut()

        if self._stop_event.is_set():
            return

        # Aşama 4: Ağrılı uyaran gerekli mi?
        if self.motor_puan < 6 or self.goz_puan < 3:
            self._transition(GKSState.AGRILI_UYARAN)
            self._asama4_agrili_uyaran()

        if self._stop_event.is_set():
            return

        # Sözel test henüz yapılmadıysa (Aşama 1 → 3 direkt geçiş)
        if self.sozel_puan == 1 and self.goz_puan == 4:
            log.info("Sozel test baslatiliyor (tam mulakat)...")
            self.ses_ai.load_models()
            self.sozel_puan = self.ses_ai.mulakat_yap()
            log.info("Sozel skor: %d/5", self.sozel_puan)

        # Final
        self._transition(GKSState.FINAL_RAPOR)
        self._final_rapor()
        self._transition(GKSState.TAMAMLANDI)

    # ─── 7/24 Döngü ────────────────────────────────────────

    def run_continuous(self):
        """7/24 GKS izleme döngüsü."""
        self.setup()

        try:
            while not self._stop_event.is_set():
                console.print("")
                console.rule("[bold yellow]YENİ TEST DÖNGÜSÜ BAŞLIYOR[/bold yellow]")

                self.run_gks_test()

                log.info("")
                log.info("Test bitti. %d dk sonra yeni test.",
                         BEKLEME_SURESI_DAKIKA)

                self._update_ekran(durum="BEKLENIYOR")

                for _ in range(BEKLEME_SURESI_DAKIKA * 60):
                    if self._stop_event.is_set():
                        break
                    # Bekleme sırasında nabız ekranda akmaya devam eder
                    self._update_ekran(nabiz_bpm=self.nabiz.bpm)
                    time.sleep(1)

        except KeyboardInterrupt:
            log.info("Cikiliyor...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Tüm kaynakları temizle."""
        log.info("Sistem kapatiliyor...")
        self._stop_event.set()

        self.kamera_goz.stop()
        self.kamera_motor.stop()
        self.nabiz.durdur()
        self.ekran.durdur()
        cleanup_gpio()

        try:
            clear_shm()
        except Exception:
            pass

        log.info("Tum kaynaklar temizlendi.")

    def stop(self):
        self._stop_event.set()


# ═══════════════════════════════════════════════════════════════
#  Graceful Shutdown
# ═══════════════════════════════════════════════════════════════

_system = None

def _signal_handler(signum, frame):
    log.info("Sinyal alindi (%s), kapatiliyor...", signum)
    if _system:
        _system.stop()

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ═══════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════

def main():
    global _system
    _system = GKSSystem()
    atexit.register(_system.cleanup)
    _system.run_continuous()


if __name__ == "__main__":
    main()
