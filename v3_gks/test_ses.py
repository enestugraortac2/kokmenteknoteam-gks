#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ses Analiz Test Scripti (Entegre versiyon)
STT (Whisper), TTS (Piper) ve NLP (difflib) modüllerini tek tek test eder.
Anti-hallucination filtresi ve objektif puanlama dahil.
"""

import time
import sys
from gks_modules.ses_analiz import (
    SesAnaliz, gks_sozel_puan, cevap_analiz_et,
    SORU_HAVUZU, _filtre_hallucination
)

def test_puanlama():
    """Offline puanlama testi — mikrofon gerektirmez."""
    print("\n[OFFLINE] Sözel puanlama objektivite testi:")
    print("-" * 50)

    test_cases = [
        ("YER",    "hastanedeyiz",      "Tam doğru cevap"),
        ("YER",    "sağlık merkezindeyiz", "Alternatif doğru cevap"),
        ("YER",    "evdeyim",           "Yanlış ama anlamlı cevap"),
        ("YER",    "merhaba",           "Alakasız tek kelime"),
        ("YER",    "",                  "Sessiz / boş"),
        ("ZAMAN",  "2026",             "Doğru yıl"),
        ("ZAMAN",  "iki bin yirmi altı", "Doğru yıl (yazıyla)"),
        ("ZAMAN",  "1990",             "Yanlış yıl"),
        ("DURUM",  "kaza geçirdim",    "Doğru durum"),
        ("DURUM",  "hava güzel bugün", "Tamamen alakasız"),
    ]

    for kategori, cevap, aciklama in test_cases:
        puan = gks_sozel_puan(cevap, kategori)
        dogru_cevaplar = SORU_HAVUZU[kategori]["dogru_cevaplar"]
        skor = cevap_analiz_et(cevap, dogru_cevaplar) if cevap else 0.0
        print(f"  [{kategori:6s}] '{cevap:25s}' -> Puan: {puan}/5  (skor: {skor:.2f})  | {aciklama}")

    print()

def test_hallucination_filtre():
    """Hallucination filtresi testi."""
    print("[OFFLINE] Hallucination filtre testi:")
    print("-" * 50)

    test_texts = [
        ("altyazı çeviri", True),
        ("izlediğiniz için teşekkürler", True),
        ("bir bir bir bir bir", True),
        ("hastanedeyim", False),
        ("kaza geçirdim düştüm", False),
    ]

    for text, should_filter in test_texts:
        result = _filtre_hallucination(text)
        filtered = result == ""
        status = "[OK]" if filtered == should_filter else "[X] YANLIS"
        print(f"  '{text:35s}' -> {'FİLTRELENDİ' if filtered else 'GEÇTİ':12s} {status}")

    print()

def test_ses():
    print("="*50)
    print(" SES UYARAN VE NLP TESTİ (Entegre)")
    print("="*50)

    # Önce offline testler (mikrofon gerektirmez)
    test_puanlama()
    test_hallucination_filtre()

    # Online testler (donanım gerektirir)
    ai = SesAnaliz()
    print("Modeller yükleniyor (Whisper modeli ilk başta uzun sürebilir)...")
    if not ai.load_models():
        print("[HATA] Whisper modeli yüklenemedi! Online test atlanıyor.")
        print("Test tamamlandı (sadece offline testler).")
        return

    print("Modeller hazır.\n")

    # 1. TTS Test
    print("[1/4] TTS (Text-to-Speech) Testi...")
    ai.konus("Merhaba, bu bir ses testidir. Eğer beni duyuyorsanız sistem çalışıyordur.")
    print("Ses çıkışı verildi.\n")

    # 2. STT Test — Sessizlik (hallucination kontrolü)
    print("[2/4] STT Sessizlik Testi (5 saniye sessiz kalın)...")
    wav_tmp = "/tmp/test_stt_sessiz.wav"
    if ai.kayit_yap(wav_tmp, sure=5):
        metin = ai.transkript_yap(wav_tmp)
        if not metin:
            print("  [OK] Sessiz ortamda hallucination yok - temiz cikti")
        else:
            print(f"  [!] Sessiz ortamda metin algilandi: '{metin}'")
    else:
        print("[HATA] Kayıt başarısız oldu.")
    print()

    # 3. STT Test — Konuşma
    print("[3/4] STT Konuşma Testi...")
    print("Lütfen mikrofona bir şeyler söyleyin (5 saniye dinleniyor)...")
    wav_tmp = "/tmp/test_stt.wav"
    if ai.kayit_yap(wav_tmp, sure=5):
        metin = ai.transkript_yap(wav_tmp)
        print(f"  Duyulan metin: '{metin}'")
        if not metin:
            print("  Uyarı: Mikrofondan hiçbir şey algılanmadı.")
    else:
        print("[HATA] Kayıt başarısız oldu.")
    print()

    # 4. Sözel GKS Testi
    print("[4/4] Sözel GKS Puanlama Testi...")
    ai.konus("Hangi binadayız?")
    time.sleep(1)
    print("Lütfen cevap verin (örneğin 'hastanedeyiz' diyin) (5s)...")
    puan, metin = ai.dinle_ve_analiz_et(kategori="YER", sure=5)
    print(f"  Cevabınız: '{metin}'")
    print(f"  Sözel (V) puanı: {puan}/5")

    print("\nTest tamamlandı.")

if __name__ == "__main__":
    test_ses()
