#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS - Hasta İhtiyaç Sorgulama Sistemi
Bu modül hastaya bir şeye ihtiyacı olup olmadığını sorar, cevabını dinler,
anlaşılan cevabı fiziksel LCD ekrana yansıtır.
Hasta "hayır/yok" derse sesli geri bildirim verir ve pasif dinlemeye geçer.
Hasta bir talep bildirirse talebi iletir ve tekrar pasif dinlemeye döner.
Ctrl+C ile kapatılır.
"""

import sys
import os
import time
import difflib
from pathlib import Path

# v3_gks modüllerine erişim sağlamak için path ekle
GKS_DIR = Path(__file__).resolve().parent.parent / "v3_gks"
if str(GKS_DIR) not in sys.path:
    sys.path.append(str(GKS_DIR))

# Encode hatasini onlemek
sys.stdout.reconfigure(encoding='utf-8')

try:
    from gks_modules.ses_analiz import SesAnaliz
    from gks_modules.ekran import EkranKontrol
    from rich.console import Console
except ImportError as e:
    print(f"Modul yukleme hatasi: {e}. GKS dizini bulunamadi veya bagimliliklar eksik.")
    sys.exit(1)

console = Console()

# ============================================================
#  SABITLER
# ============================================================

STOPWORDS = ["merhaba", "ben", "bir", "şey", "sey", "ııı", "eee", "evet",
             "tamam", "olur", "var", "yani", "iste", "işte", "biliyor", "musunuz", "lütfen"]

NEGATIF_KELIMELER = ["hayır", "hayir", "yok", "değil", "degil", "istemiyorum", "iyiyim", "gerek yok"]

IHTIYAC_KATEGORILERI = {
    "su istiyorum": {
        "nesneler": ["su", "suistiyorum", "suber", "foysturum", "şu", "so"],
        "eylemler": ["susadım", "içmek", "ver", "istiyorum"]
    },
    "agrim var": {
        "nesneler": ["ağrı", "agri", "acı", "aci", "ari", "ayrı"],
        "eylemler": ["ağrıyor", "acıyor", "sızlıyor", "yarım"]
    },
    "tuvalet": {
        "nesneler": ["tuvalet", "wc", "lavabo", "lazımlık", "ördek", "altım"],
        "eylemler": ["geldi", "gitmek", "yapmam"]
    },
    "usudum": {
        "nesneler": ["soğuk", "battaniye", "örtü"],
        "eylemler": ["üşüdüm", "usudum", "üşüyorum", "titriyorum"]
    },
    "sicak": {
        "nesneler": ["sıcak", "ter", "havalandırma"],
        "eylemler": ["yandım", "terledim", "bunaldım"]
    },
    "hemsire cagirin": {
        "nesneler": ["hemşire", "doktor", "hekim", "yardım"],
        "eylemler": ["çağırın", "baksın", "gelin", "yardımcı"]
    },
    "yastik duzelt": {
        "nesneler": ["yastık", "yatak", "belim", "sırtım"],
        "eylemler": ["düzelt", "kaldır", "dikleştir", "indir"]
    }
}

KAYIT_SURESI = 5  # Mikrofon dinleme suresi (saniye)


# ============================================================
#  YARDIMCI FONKSIYONLAR
# ============================================================

def turkce_temizle(metin):
    """Turkce karakterleri Latin eşdeğerine çevirir (konsol yazdırma için)."""
    return metin.replace('ı','i').replace('ş','s').replace('ç','c').replace('ğ','g').replace('ö','o').replace('ü','u')


def niyet_analiz(raw_text):
    """
    Ham STT metnini analiz ederek hastanin niyetini belirler.
    
    Donuş:
        ("TALEP_YOK", None)   -> Hasta bir sey istemiyor
        ("TALEP_VAR", niyet)  -> Hasta bir sey istiyor (niyet = "su istiyorum" vs.)
        ("BELIRSIZ", None)    -> Anlamsiz veya yetersiz puan
    """
    if not raw_text or raw_text.startswith(("altt", "altyaz")):
        return ("BELIRSIZ", None)
    
    # 1. Negatif Niyet Kontrolü
    negatif_var = any(neg in raw_text for neg in NEGATIF_KELIMELER)
    if negatif_var:
        console.print(f"[dim]STT: '{raw_text}' | Negatif niyet tespit edildi.[/dim]")
        return ("TALEP_YOK", None)
    
    # 2. Stopwords temizle
    temiz_kelimeler = [k for k in raw_text.split() if k not in STOPWORDS and len(k) > 2]
    temiz_cumle = " ".join(temiz_kelimeler)
    
    # 3. Niyet Puanlaması
    en_iyi_niyet = None
    en_yuksek_puan = 0
    
    for gercek_ihtiyac, parametreler in IHTIYAC_KATEGORILERI.items():
        puan = 0
        
        # Nesne kelimelerini ara
        for nesne in parametreler["nesneler"]:
            if nesne in temiz_cumle:
                puan += 1.5
                break
            for kelime in temiz_kelimeler:
                if difflib.SequenceMatcher(None, kelime, nesne).ratio() > 0.70:
                    puan += 1.2
                    break
        
        # Eylem kelimelerini ara
        for eylem in parametreler["eylemler"]:
            if eylem in temiz_cumle:
                puan += 1.0
                break
            for kelime in temiz_kelimeler:
                if difflib.SequenceMatcher(None, kelime, eylem).ratio() > 0.70:
                    puan += 0.8
                    break
        
        if puan > en_yuksek_puan:
            en_yuksek_puan = puan
            en_iyi_niyet = gercek_ihtiyac
    
    # 4. Karar
    if en_yuksek_puan >= 1.0:
        console.print(f"[dim]STT: '{raw_text}' | Puan: {en_yuksek_puan:.1f} | Niyet: '{en_iyi_niyet}'[/dim]")
        return ("TALEP_VAR", en_iyi_niyet)
    else:
        console.print(f"[dim]STT: '{raw_text}' | Yetersiz niyet puani ({en_yuksek_puan:.1f}), iptal edildi.[/dim]")
        return ("BELIRSIZ", None)


def ses_dinle_ve_analiz_et(ses):
    """Mikrofonu aç, kayıt yap, Whisper ile metne çevir ve niyet analizi döndür."""
    kayit_basarili = ses.kayit_yap(sure=KAYIT_SURESI)
    
    if not kayit_basarili:
        return ("HATA", None)
    
    console.print("[yellow]Ses kaydedildi, yapay zeka tarafindan metne cevriliyor...[/yellow]")
    
    if ses._whisper_model is None:
        return ("HATA", None)
    
    try:
        segments, info = ses._whisper_model.transcribe(
            "/tmp/gks_kayit.wav",
            beam_size=5,
            language="tr",
            initial_prompt="su, ilaç, ağrı, tuvalet, hemşire, doktor, lütfen, yardım, üşüdüm, sıcak, yastık.",
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=400,
                speech_pad_ms=200,
            ),
            no_speech_threshold=0.5,
            log_prob_threshold=-0.8
        )
        raw_text = "".join([s.text for s in list(segments)]).lower().strip()
        return niyet_analiz(raw_text)
    except Exception as e:
        console.print(f"[red]STT Hatasi: {e}[/red]")
        return ("HATA", None)


# ============================================================
#  ANA PROGRAM
# ============================================================

def main():
    console.print("\n[bold cyan]============================================================[/bold cyan]")
    console.print("[bold cyan]  HASTA IHTIYAC SORGULAMA SISTEMI[/bold cyan]")
    console.print("[bold cyan]============================================================[/bold cyan]\n")

    # 1. Modülleri başlat
    console.print("[yellow]Moduller yukleniyor, lutfen bekleyin...[/yellow]")

    ekran = EkranKontrol()
    ekran.baslat()
    ekran.update(durum="SISTEM HAZIR")

    ses = SesAnaliz()
    ses.load_models()

    console.print("[green]Moduller hazir.[/green]\n")

    try:
        # ---- İLK SORGULAMA ----
        soru_metni = "Beni duyabiliyor musunuz? Bir şeye ihtiyacınız var mı? Lütfen söyleyin."
        console.print("[bold blue]SISTEM:[/bold blue] Beni duyabiliyor musunuz? Bir seye ihtiyaciniz var mi? Lutfen soyleyin.")
        ekran.update(durum="IHTIYAC SORULUYOR", goz=0, motor=0, sozel=0)
        ses.konus(soru_metni)

        # ---- SONSUZ DINLEME DONGUSU ----
        while True:
            console.print(f"\n[bold red](Mikrofon acik, {KAYIT_SURESI} saniye hasta dinleniyor...)[/bold red]")
            ekran.update(durum="HASTA DINLENIYOR", goz=0, motor=0, sozel=0)

            sonuc, niyet = ses_dinle_ve_analiz_et(ses)

            # --- DURUM: Hasta "hayir/yok" dedi ---
            if sonuc == "TALEP_YOK":
                console.print("\n[bold yellow]SISTEM: Hastanin herhangi bir talebi yok.[/bold yellow]")
                ekran.update(durum="TALEP YOK", goz=0, motor=0, sozel=0)
                ses.konus("Anladım. Hastanın herhangi bir talebi yok. Ne zaman bir şeye ihtiyacınız olursa buradayım.")
                console.print("[cyan]Pasif dinleme moduna geciliyor...[/cyan]")
                ekran.update(durum="PASIF DINLEME", goz=0, motor=0, sozel=0)
                # Pasif dinlemeye devam (döngü başa döner)

            # --- DURUM: Hasta bir istek bildirdi ---
            elif sonuc == "TALEP_VAR" and niyet:
                latin_niyet = turkce_temizle(niyet)
                console.print(f"\n[bold green]HASTA TALEBI:[/bold green] {latin_niyet}")

                lcd_mesaj = f"TALEP: {latin_niyet[:15]}"
                ekran.update(durum=lcd_mesaj, goz=0, motor=0, sozel=0)

                cevap = f"Anladım. Hastanın talebi: {niyet}. Talebinizi hemen iletiyorum."
                console.print(f"[bold blue]SISTEM:[/bold blue] {turkce_temizle(cevap)}")
                ses.konus(cevap)

                # Talep ekranda 5 saniye kalsın
                time.sleep(5)

                # Tekrar pasif dinlemeye dön
                ses.konus("Başka bir şeye ihtiyacınız olursa söylemeniz yeterli, buradayım.")
                console.print("[cyan]Pasif dinleme moduna geciliyor...[/cyan]")
                ekran.update(durum="PASIF DINLEME", goz=0, motor=0, sozel=0)

            # --- DURUM: Ses anlaşılamadı / Belirsiz ---
            elif sonuc == "BELIRSIZ":
                console.print("\n[dim]Ses anlasilamadi veya anlamsiz, dinlemeye devam...[/dim]")
                ekran.update(durum="PASIF DINLEME", goz=0, motor=0, sozel=0)

            # --- DURUM: Mikrofon veya STT hatası ---
            elif sonuc == "HATA":
                console.print("\n[bold red]HATA: Mikrofon veya STT kaydi basarisiz![/bold red]")
                ekran.update(durum="MIKROFON HATASI", goz=0, motor=0, sozel=0)
                time.sleep(3)
                ekran.update(durum="PASIF DINLEME", goz=0, motor=0, sozel=0)

            # Her döngüde kısa bir bekleme (CPU yormamak için)
            time.sleep(1)

    except KeyboardInterrupt:
        console.print("\n[red]Islem kullanici tarafindan iptal edildi (Ctrl+C).[/red]")
    except Exception as e:
        console.print(f"\n[bold red]Beklenmeyen Hata: {e}[/bold red]")
    finally:
        console.print("\n[yellow]Sistem temizleniyor...[/yellow]")
        ekran.durdur()
        console.print("[green]Islem tamamlandi.[/green]")


if __name__ == "__main__":
    main()
