#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — Entegre Sistem Testi
Tüm bileşenleri tek seferde test eder:
  1. Kamera (rotation/flip dahil)
  2. Göz Analiz (CLAHE + profil + multi-pass)
  3. Motor Analiz (ONNX pose)
  4. Ses Analiz (anti-hallucination + objektif puanlama)
  5. Nabız Sensörü
  6. Servo Motor
  7. LCD Ekran
"""

import os
import sys
import time
import traceback

from rich.console import Console
from rich.panel import Panel

console = Console()

# --- Sonuclar ---
results = {}

def test_section(name, func):
    """Bir test bölümünü çalıştırır ve sonucu kaydeder."""
    console.print("")
    console.rule(f"[bold cyan]{name}[/bold cyan]")
    try:
        ok = func()
        if ok:
            results[name] = "[bold green][OK] BAŞARILI[/bold green]"
        else:
            results[name] = "[bold red][X] BAŞARISIZ[/bold red]"
        return ok
    except Exception as e:
        console.print(f"  [bold red][HATA] {e}[/bold red]")
        traceback.print_exc()
        results[name] = f"[bold red][X] HATA: {e}[/bold red]"
        return False


# =========== 1. KAMERA TESTI ===========

def test_kamera():
    from gks_modules.camera_manager import CameraManager

    cam_goz_id = int(os.environ.get("CAMERA_GOZ_ID", "0"))
    cam_motor_id = int(os.environ.get("CAMERA_MOTOR_ID", "1"))
    
    print(f"  Göz Kamera ID={cam_goz_id}, Motor Kamera ID={cam_motor_id}")

    # Göz Testi
    cam_goz = CameraManager(camera_id=cam_goz_id)
    if not cam_goz.start():
        print("  [HATA] Göz Kamerası başlatılamadı!")
        return False
    time.sleep(0.5)
    frame = cam_goz.get_frame()
    cam_goz.stop()
    if frame is None:
        print("  [HATA] Göz Kamerasından Frame alınamadı!")
        return False
    print("  [OK] Göz Kamerası OK")

    # Motor Testi
    cam_motor = CameraManager(camera_id=cam_motor_id)
    if not cam_motor.start():
        print("  [HATA] Motor Kamerası başlatılamadı!")
        return False
    time.sleep(0.5)
    frame = cam_motor.get_frame()
    cam_motor.stop()
    if frame is None:
        print("  [HATA] Motor Kamerasından Frame alınamadı!")
        return False
    print("  [OK] Motor Kamerası OK")
    
    return True


# =========== 2. GOZ ANALIZ TESTI ===========

def test_goz():
    from gks_modules.camera_manager import CameraManager
    from gks_modules.goz_analiz import GozAnaliz

    ai = GozAnaliz()
    if not ai.load_model():
        print("  [HATA] Haar Cascade yüklenemedi!")
        return False

    cam_id = int(os.environ.get("CAMERA_GOZ_ID", "0"))
    rotation = int(os.environ.get("CAMERA_GOZ_ROTATION", "0"))
    hflip = os.environ.get("CAMERA_GOZ_HFLIP", "0") == "1"
    vflip = os.environ.get("CAMERA_GOZ_VFLIP", "0") == "1"

    cam = CameraManager(camera_id=cam_id, rotation=rotation, hflip=hflip, vflip=vflip)
    if not cam.start():
        print("  [HATA] Kamera başlatılamadı!")
        return False

    print("  Yüz aranıyor (5 saniye)...")
    yuz_bulundu = False
    start = time.time()
    while time.time() - start < 5.0:
        frame = cam.get_frame()
        if frame is not None:
            acik, ear = ai.analiz_et(frame)
            if ear > 0:
                yuz_bulundu = True
                print(f"  [OK] Yuz bulundu! EAR: {ear:.3f}, Goz: {'ACIK' if acik else 'KAPALI'}")
                break
        time.sleep(0.3)

    cam.stop()

    if not yuz_bulundu:
        print("  [UYARI] 5 saniye içinde yüz bulunamadı")
    return yuz_bulundu


# =========== 3. MOTOR ANALIZ TESTI ===========

def test_motor():
    from gks_modules.motor_analiz import MotorAnaliz

    ai = MotorAnaliz()
    if not ai.load_model():
        print("  [HATA] YOLOv8n-pose modeli yüklenemedi!")
        print("  (models/yolov8n-pose.onnx dosyası gerekli)")
        return False

    from gks_modules.camera_manager import CameraManager
    cam_id = int(os.environ.get("CAMERA_MOTOR_ID", "1"))
    rotation = int(os.environ.get("CAMERA_MOTOR_ROTATION", "0"))
    hflip = os.environ.get("CAMERA_MOTOR_HFLIP", "0") == "1"
    vflip = os.environ.get("CAMERA_MOTOR_VFLIP", "0") == "1"

    cam = CameraManager(camera_id=cam_id, rotation=rotation, hflip=hflip, vflip=vflip)
    if not cam.start():
        print("  [HATA] Kamera başlatılamadı!")
        return False

    print("  İskelet aranıyor (5 saniye)...")
    iskelet_bulundu = False
    start = time.time()
    while time.time() - start < 5.0:
        frame = cam.get_frame()
        if frame is not None:
            keypoints = ai.pose_tespit_et(frame)
            if keypoints is not None:
                iskelet_bulundu = True
                skor, durum = ai.analiz_et(keypoints)
                print(f"  [OK] İskelet bulundu! Motor skor: {skor}, Durum: {durum}")
                break
        time.sleep(0.3)

    cam.stop()
    ai.unload_model()

    if not iskelet_bulundu:
        print("  [UYARI] 5 saniye içinde iskelet bulunamadı")
    return iskelet_bulundu


# =========== 4. SES ANALIZ TESTI ===========

def test_ses():
    from gks_modules.ses_analiz import (
        SesAnaliz, gks_sozel_puan, cevap_analiz_et,
        SORU_HAVUZU, _filtre_hallucination
    )

    # Offline puanlama doğrulaması
    print("  [Offline] Puanlama testi:")
    test_ok = True

    # "hastane" → 5 puan olmalı
    puan = gks_sozel_puan("hastanedeyiz", "YER")
    print(f"    'hastanedeyiz' -> {puan}/5 (beklenen: 5)")
    if puan != 5:
        test_ok = False

    # Boş → 1 puan olmalı
    puan = gks_sozel_puan("", "YER")
    print(f"    '' (sessiz) -> {puan}/5 (beklenen: 1)")
    if puan != 1:
        test_ok = False

    # Hallucination filtresi
    result = _filtre_hallucination("altyazı çeviri")
    print(f"    Hallucination filtre 'altyazi ceviri': {'FILTRELENDI [OK]' if result == '' else 'GECTI [X]'}")
    if result != "":
        test_ok = False

    result = _filtre_hallucination("hastanedeyim")
    print(f"    Normal metin 'hastanedeyim': {'GECTI [OK]' if result != '' else 'FILTRELENDI [X]'}")
    if result == "":
        test_ok = False

    # Online test (Whisper yükleme)
    ai = SesAnaliz()
    if ai.load_models():
        print("  [Online] Whisper modeli yuklendi [OK]")

        # TTS testi
        ai.konus("Test.")
        print("  [Online] TTS calisti [OK]")
    else:
        print("  [UYARI] Whisper yuklenemedi - online test atlaniyor")

    return test_ok


# =========== 5. NABIZ SENSORU TESTI ===========

def test_nabiz():
    from gks_modules.nabiz_sensoru import NabizSensoru

    nabiz = NabizSensoru()
    nabiz.baslat()
    time.sleep(3)

    bpm = nabiz.bpm
    durum = nabiz.durum
    nabiz.durdur()

    print(f"  BPM: {bpm}, Durum: {durum}")
    return bpm > 0 or durum == "SIMULASYON"


# =========== 6. SERVO TESTI ===========

def test_servo():
    from gks_modules.servo_kontrol import stimulate_servo, cleanup_gpio

    ok = stimulate_servo(duration=1.0)
    cleanup_gpio()

    print(f"  Servo {'calisti [OK]' if ok else 'basarisiz [X]'}")
    return ok


# =========== 7. LCD EKRAN TESTI ===========

def test_ekran():
    from gks_modules.ekran import EkranKontrol

    ekran = EkranKontrol()
    ok = ekran.baslat()
    if ok:
        ekran.update(goz=3, motor=5, sozel=4, durum="TEST", nabiz_bpm=72)
        time.sleep(2)
        ekran.durdur()
        print("  Ekran test frame gosterildi [OK]")
    else:
        print("  [HATA] Ekran başlatılamadı!")
    return ok


# =========== ANA FONKSIYON ===========

def main():
    console.print(Panel.fit(
        "[bold white]NeuroSense GKS v3 - Entegre Sistem Testi[/bold white]",
        border_style="cyan"
    ))

    # Sıralı test (kaynakları paylaştıkları için paralel değil)
    test_section("1. Kamera",        test_kamera)
    test_section("2. Göz Analiz",    test_goz)
    test_section("3. Motor Analiz",  test_motor)
    test_section("4. Ses Analiz",    test_ses)
    test_section("5. Nabız Sensörü", test_nabiz)
    test_section("6. Servo Motor",   test_servo)
    test_section("7. LCD Ekran",     test_ekran)

    # ─── Sonuç Raporu ──────────────────────────────────────
    console.print("\n")
    console.rule("[bold yellow]ENTEGRE TEST RAPORU[/bold yellow]")
    
    from rich.table import Table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Modül", style="dim", width=25)
    table.add_column("Durum", justify="left")
    
    basarili = 0
    toplam = len(results)
    for name, status in results.items():
        table.add_row(name, status)
        if "BAŞARILI" in status:
            basarili += 1

    console.print(table)
    console.print(f"\n  [bold]Özet:[/bold] {basarili}/{toplam} bileşen başarılı")
    
    if basarili == toplam:
        console.print(Panel("[bold green]TÜM SİSTEM KUSURSUZ ÇALIŞIYOR [OK][/bold green]", border_style="green"))
    else:
        console.print(Panel(f"[bold red]DİKKAT: Bazı bileşenler hatalı![/bold red]", border_style="red"))


if __name__ == "__main__":
    main()
