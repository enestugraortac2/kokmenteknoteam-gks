#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS - Hasta İhtiyaç Sorusu
Bu modül hastaya bir şeye ihtiyacı olup olmadığını sorar, cevabını dinler 
ve anlaşılan cevabı fiziksel LCD ekrana yansıtır.
"""

import sys
import os
import time
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
        # 2. Hastaya soruyu sor (TTS metni turkce olabilir)
        soru_metni = "Beni duyabiliyor musunuz? Bir şeye ihtiyacınız var mı? Lütfen söyleyin."
        console.print("[bold blue]SISTEM:[/bold blue] Beni duyabiliyor musunuz? Bir seye ihtiyaciniz var mi? Lutfen soyleyin.")
        
        ekran.update(durum="IHTIYAC SORULUYOR", goz=0, motor=0, sozel=0)
        ses.konus(soru_metni)
        
        # 3. Cevabı dinle (kaydet)
        console.print("\n[bold red](Mikrofon acik, 5 saniye hasta dinleniyor...)[/bold red]")
        ekran.update(durum="HASTA DINLENIYOR", goz=0, motor=0, sozel=0)
        
        kayit_basarili = ses.kayit_yap(sure=5)
        
        if kayit_basarili:
            console.print("[yellow]Ses kaydedildi, yapay zeka tarafindan metne cevriliyor...[/yellow]")
            ekran.update(durum="SES ISLENIYOR", goz=0, motor=0, sozel=0)
            
            # SesAnaliz modülündeki transkript_yap çok sıkı GKS filtrelerine sahip, 
            # hastanın günlük konuşmaları için doğrudan ve daha esnek ayarla modeli çağırıyoruz.
            hasta_metni = ""
            if ses._whisper_model is not None:
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
                        no_speech_threshold=0.5, # Biraz daha gevşek filtre
                        log_prob_threshold=-0.8  # Daha esnek kabul
                    )
                    raw_text = "".join([s.text for s in list(segments)]).lower().strip()
                    
                    # Basit gürültü/halüsinasyon filtresi
                    if raw_text and not raw_text.startswith(("altt", "altyaz")):
                        # --- YAZILIMSAL DÜZELTME (Fuzzy Matching) ---
                        import difflib
                        
                        beklenen_ihtiyaclar = {
                            "su istiyorum": ["su", "susadım", "su ver", "suber", "foysturum", "suistiyorum", "şu", "so"],
                            "agrim var": ["ağrı", "ağrım", "ağrıyor", "acı", "acıyor", "ari", "agri", "ayrı", "yarım"],
                            "tuvalet": ["tuvalet", "wc", "lavabo", "ihtiyaç", "tuvale"],
                            "usudum": ["üşüdüm", "soğuk", "battaniye", "örtü", "üşüyorum"],
                            "sicak": ["sıcak", "yandım", "terledim", "bunaldım"],
                            "hemsire cagirin": ["hemşire", "doktor", "yardım", "biri baksın"],
                            "yastik duzelt": ["yastık", "yatak", "kaldır", "dikleştir"]
                        }
                        
                        en_iyi_eslesme = raw_text # Varsayılan olarak anladığı şeyi yazsın
                        en_yuksek_skor = 0.0
                        
                        # 1. Tam cümle veya kelime eşleşmesi kontrolü:
                        bulundu = False
                        for gercek_ihtiyac, varyasyonlar in beklenen_ihtiyaclar.items():
                            for varsayilan in varyasyonlar:
                                # difflib ile benzerlik oranı (0.0 - 1.0)
                                benzerlik = difflib.SequenceMatcher(None, raw_text, varsayilan).ratio()
                                
                                # Eğer kelime cümlenin içinde direkt geçiyorsa (örn: "ben su istiyorum" içinde "su")
                                if varsayilan in raw_text.split() or benzerlik > 0.65:
                                    if benzerlik > en_yuksek_skor:
                                        en_yuksek_skor = benzerlik
                                        en_iyi_eslesme = gercek_ihtiyac
                                        bulundu = True
                        
                        if bulundu:
                            hasta_metni = en_iyi_eslesme
                            console.print(f"[dim]Ham STT Ciktisi: '{raw_text}' -> Duzeltildi: '{hasta_metni}'[/dim]")
                        else:
                            hasta_metni = raw_text
                            
                except Exception as e:
                    console.print(f"[red]STT Hatasi: {e}[/red]")
            
            if hasta_metni:
                # Turkce karakterli degiskende sorun cikmamasi icin ASCII'ye yakin dondur (Console print icin)
                latin_hasta_metni = hasta_metni.replace('ı','i').replace('ş','s').replace('ç','c').replace('ğ','g').replace('ö','o').replace('ü','u')
                console.print(f"\n[bold green]HASTA TALEBI:[/bold green] {latin_hasta_metni}")
                
                # LCD ekrana yansıt (Ekran sınıfı durumu büyük harfle yazdırmak üzere tasarlandığı için)
                # Özel mesajı durum satırına gönderelim
                lcd_mesaj = f"TALEP: {latin_hasta_metni[:15]}" # LCD 20 karakter sınırına uyacak şekilde
                
                ekran.update(durum=lcd_mesaj, goz=0, motor=0, sozel=0)
                
                cevap_metni = "Anladım. Talebinizi hemen iletiyorum. Lütfen bekleyin."
                console.print("[bold blue]SISTEM:[/bold blue] Anladim. Talebinizi hemen iletiyorum. Lutfen bekleyin.")
                ses.konus(cevap_metni)
                
            else:
                console.print("\n[bold yellow]UYARI: Hasta cevap vermedi veya ses anlasilamadi.[/bold yellow]")
                ekran.update(durum="SES ANLASILAMADI", goz=0, motor=0, sozel=0)
                ses.konus("Sizi anlayamadım, ekiplerimiz durumu kontrol edecek.")
        else:
            console.print("\n[bold red]HATA: Mikrofon kaydi basarisiz oldu![/bold red]")
            ekran.update(durum="MIKROFON HATASI", goz=0, motor=0, sozel=0)
            
        console.print("\n[cyan]Talepler ekranda 5 saniye daha gosteriliyor...[/cyan]")
        time.sleep(5)

    except KeyboardInterrupt:
        console.print("\n[red]Islem kullanici tarafindan iptal edildi.[/red]")
    except Exception as e:
        console.print(f"\n[bold red]Beklenmeyen Hata: {e}[/bold red]")
    finally:
        console.print("\n[yellow]Sistem temizleniyor...[/yellow]")
        ekran.durdur()
        console.print("[green]Islem tamamlandi.[/green]")

if __name__ == "__main__":
    main()
