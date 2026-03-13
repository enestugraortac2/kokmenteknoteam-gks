#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS - Mikrofon Test Scripti

Bu script mikrofon donanimini test eder:
  1. Sistemdeki tum ses cihazlarini listeler
  2. Ilk mikrofonu otomatik bulur
  3. 5 saniye ses kaydi yapar
  4. Kaydin ses seviyesini (RMS) analiz eder
  5. Kaydi hoparlorden calar (dogrulama icin)
  6. (Opsiyonel) Whisper ile transkripsiyon test eder

Kullanim:
  cd /home/kokmenteknoteam/Desktop/VS_GKS_Proje
  python3 test_mikrofon.py
"""

import os
import sys
import time
import wave
import struct
import subprocess


def renkli(text, renk="reset"):
    renkler = {
        "kirmizi": "\033[91m", "yesil": "\033[92m",
        "sari": "\033[93m", "mavi": "\033[94m",
        "reset": "\033[0m",
    }
    return f"{renkler.get(renk, '')}{text}{renkler['reset']}"


def baslik(text):
    print("\n" + "=" * 60)
    print("  " + text)
    print("=" * 60)


def listele_ses_cihazlari():
    """Sistemdeki ses giris/cikis cihazlarini listeler."""
    baslik("1. SES CIHAZLARI")

    # Kayit cihazlari (mikrofonlar)
    print("\n[KAYIT] KAYIT CIHAZLARI (Mikrofonlar):")
    try:
        out = subprocess.check_output(["arecord", "-l"], stderr=subprocess.DEVNULL, text=True)
        if "card" not in out:
            print(renkli("  [HATA] Hic mikrofon cihazi bulunamadi!", "kirmizi"))
        else:
            for line in out.splitlines():
                if line.startswith("card"):
                    print(f"  {line.strip()}")
    except FileNotFoundError:
        print(renkli("  [HATA] arecord bulunamadi! ALSA araclari yuklu degil.", "kirmizi"))
        print("  Cozum: sudo apt install alsa-utils")
        return False
    except Exception as e:
        print(renkli(f"  [HATA] Hata: {e}", "kirmizi"))

    # Oynatma cihazları (hoparlörler)
    print("\n[OYNAT] OYNATMA CIHAZLARI (Hoparlorler):")
    try:
        out = subprocess.check_output(["aplay", "-l"], stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            if line.startswith("card"):
                print(f"  {line.strip()}")
    except Exception:
        pass

    return True


def bul_mikrofon():
    """Ilk calisan mikrofon cihazini bulur."""
    baslik("2. MIKROFON TESPITI")

    try:
        out = subprocess.check_output(["arecord", "-l"], stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            if line.startswith("card"):
                parts = line.split(":")
                card_no = parts[0].strip().replace("card ", "")
                device = f"plughw:{card_no},0"
                print(f"  [OK] Mikrofon bulundu: {renkli(device, 'yesil')}")
                print(f"       {line.strip()}")
                return device
    except Exception:
        pass

    print(renkli("  [UYARI] Otomatik mikrofon bulunamadi, 'default' kullanilacak", "sari"))
    return "default"


def kayit_yap(device, dosya="/tmp/gks_mikrofon_test.wav", sure=5):
    """Belirtilen cihazdan ses kaydı yapar."""
    baslik("3. SES KAYDI (" + str(sure) + " saniye)")

    print(f"  Cihaz: {device}")
    print(f"  Dosya: {dosya}")
    print(f"  Süre:  {sure} saniye")
    print()

    # Countdown
    print("  Kayit basliyor... ", end="", flush=True)
    for i in range(3, 0, -1):
        print(f"{i}...", end=" ", flush=True)
        time.sleep(1)
    print("KAYIT!")
    print(f"  {renkli('Simdi konusun!', 'yesil')}")

    try:
        result = subprocess.run(
            ['arecord', '-D', device, '-d', str(sure),
             '-f', 'S16_LE', '-r', '16000', '-c', '1', dosya],
            capture_output=True, timeout=sure + 10
        )

        if result.returncode != 0:
            stderr = result.stderr.decode(errors='ignore')
            print(renkli(f"\n  [HATA] Kayit basarisiz! (hata kodu: {result.returncode})", "kirmizi"))
            print(f"  Hata detayi: {stderr[:300]}")

            # Fallback dene
            if device != "default":
                print(f"\n  'default' cihazı ile tekrar deneniyor...")
                result = subprocess.run(
                    ['arecord', '-D', 'default', '-d', str(sure),
                     '-f', 'S16_LE', '-r', '16000', '-c', '1', dosya],
                    capture_output=True, timeout=sure + 10
                )
                if result.returncode == 0:
                    print(renkli("  [OK] 'default' cihazi ile kayit basarili!", "yesil"))
                    return True
            return False

        if not os.path.exists(dosya) or os.path.getsize(dosya) < 100:
            print(renkli("  [HATA] Kayit dosyasi bos veya cok kucuk!", "kirmizi"))
            return False

        boyut = os.path.getsize(dosya)
        print(f"\n  [OK] Kayit tamamlandi!")
        print(f"  Dosya boyutu: {boyut:,} byte ({boyut/1024:.1f} KB)")
        return True

    except subprocess.TimeoutExpired:
        print(renkli("  [HATA] Kayit zaman asimina ugradi!", "kirmizi"))
        return False
    except Exception as e:
        print(renkli(f"  [HATA] Kayit hatasi: {e}", "kirmizi"))
        return False


def analiz_yap(dosya="/tmp/gks_mikrofon_test.wav"):
    """Kayit dosyasinin ses seviyesini analiz eder."""
    baslik("4. SES ANALIZI")

    try:
        with wave.open(dosya, 'rb') as wf:
            n_frames = wf.getnframes()
            sample_rate = wf.getframerate()
            duration = n_frames / sample_rate

            if n_frames == 0:
                print(renkli("  [HATA] Kayit bos!", "kirmizi"))
                return False

            raw = wf.readframes(n_frames)
            samples = struct.unpack(f'<{n_frames}h', raw)

            # RMS hesapla
            rms = (sum(s * s for s in samples) / n_frames) ** 0.5
            max_amp = max(abs(s) for s in samples)
            db = 20 * (rms / 32768) if rms > 0 else -100

            print(f"  Süre:         {duration:.1f} saniye")
            print(f"  Sample Rate:  {sample_rate} Hz")
            print(f"  Frame Sayisi: {n_frames:,}")
            print(f"  RMS:          {rms:.1f}")
            print(f"  Max Amplitud: {max_amp}")
            print(f"  dB (yaklasik): {db:.1f} dB")
            print()

            # Seviye çubuğu
            seviye = min(int(rms / 100), 50)
            cubuk = "#" * seviye + "." * (50 - seviye)
            print(f"  Ses Seviyesi: [{cubuk}]")

            # Yorum
            if rms < 50:
                print(renkli("\n  [HATA] SES COK ZAYIF - Mikrofon calismiyor veya cok uzak!", "kirmizi"))
                print("  Oneriler:")
                print("     - Mikrofonu kontrol edin (takili mi?)")
                print("     - Mikrofon hassasiyetini artirin: alsamixer")
                print("     - Farkli USB port deneyin")
                return False
            elif rms < 150:
                print(renkli("\n  [UYARI] SES ZAYIF - Kayit var ama sessiz", "sari"))
                print("  Daha yakindan ve yuksek sesle konusmayi deneyin")
                print("  alsamixer ile capture seviyesini artirin")
                return True
            elif rms < 500:
                print(renkli("\n  [OK] SES SEVIYESI NORMAL - Mikrofon duzgun calisiyor!", "yesil"))
                return True
            else:
                print(renkli("\n  [OK] SES SEVIYESI IYI - Guclu kayit!", "yesil"))
                return True

    except Exception as e:
        print(renkli(f"  [HATA] Analiz hatasi: {e}", "kirmizi"))
        return False


def kaydi_cal(dosya="/tmp/gks_mikrofon_test.wav"):
    """Kaydi hoparlorden calar."""
    baslik("5. KAYDI CALMA")

    print("  Kaydinizi simdi dinliyorsunuz...")
    print()

    # Oynatıcı zinciri
    players = ["pw-play", "paplay", "aplay"]
    for player in players:
        try:
            result = subprocess.run(
                [player, dosya],
                capture_output=True, timeout=15
            )
            if result.returncode == 0:
                print(f"  [OK] Calma tamamlandi ({player})")
                return True
        except FileNotFoundError:
            continue
        except Exception:
            continue

    print(renkli("  [UYARI] Ses calinamadi - hoparlor sorunu olabilir", "sari"))
    return False


def whisper_test(dosya="/tmp/gks_mikrofon_test.wav"):
    """Opsiyonel: Kaydı Whisper ile transkript et."""
    baslik("6. WHISPER TRANSKRIPSIYON (Opsiyonel)")

    try:
        from faster_whisper import WhisperModel

        root = os.path.dirname(os.path.abspath(__file__))
        local_dir = os.path.join(root, "models", "whisper-base")

        if os.path.exists(local_dir) and os.listdir(local_dir):
            model_path = local_dir
        else:
            model_path = "base"

        print(f"  Whisper yükleniyor ({model_path})...")
        model = WhisperModel(model_path, device="cpu", compute_type="int8")

        print("  Transkripsiyon yapiliyor...")
        segments, info = model.transcribe(dosya, beam_size=5, language="tr")
        text = "".join([s.text for s in list(segments)]).strip()

        if text:
            print(f"\n  Transkript: \"{renkli(text, 'yesil')}\"")
            print(f"  [OK] Whisper calisiyor!")
        else:
            print(renkli("  [UYARI] Transkript bos - ses cok sessiz veya Turkce algilanamadi", "sari"))

        return True

    except ImportError:
        print(renkli("  [UYARI] faster-whisper yuklu degil, atlaniyor", "sari"))
        print("  Yuklemek icin: pip install faster-whisper")
        return False
    except Exception as e:
        print(renkli(f"  [HATA] Whisper hatasi: {e}", "kirmizi"))
        return False


def main():
    print()
    print("+" + "="*50 + "+")
    print("|  NeuroSense GKS - Mikrofon Test Scripti         |")
    print("+" + "="*50 + "+")

    dosya = "/tmp/gks_mikrofon_test.wav"

    # 1. Cihaz listele
    if not listele_ses_cihazlari():
        sys.exit(1)

    # 2. Mikrofon bul
    device = bul_mikrofon()

    # 3. Kayıt yap
    if not kayit_yap(device, dosya):
        print(renkli("\n[HATA] KAYIT BASARISIZ - Mikrofon sorunu var!", "kirmizi"))
        sys.exit(1)

    # 4. Analiz
    analiz_yap(dosya)

    # 5. Kayıt çal
    kaydi_cal(dosya)

    # 6. Whisper test
    whisper_test(dosya)

    # Temizlik
    try:
        os.remove(dosya)
    except Exception:
        pass

    baslik("SONUC")
    print(renkli("  Test tamamlandi!", "yesil"))
    print("  Eger ses kaydi basarili ve transkript dogruysa")
    print("  mikrofon GKS sistemi icin hazirdir.")
    print()


if __name__ == "__main__":
    main()
