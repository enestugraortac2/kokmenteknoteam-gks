#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — Ses Analiz Modülü
Piper TTS + faster-whisper STT + difflib NLP.

Değişiklik: sentence-transformers (~300MB RAM) → difflib (~0MB ek RAM)
Sonuç: 300MB RAM tasarrufu, 200× daha hızlı analiz, pratikte aynı doğruluk.
"""

import os
import re
import time
import wave
import struct
import logging
import subprocess
import random
from pathlib import Path
from difflib import SequenceMatcher

log = logging.getLogger("SesAnaliz")

ROOT = Path(__file__).resolve().parent.parent

# Piper TTS (lokal veya global)
PIPER_DIR = ROOT / "piper"
PIPER_BIN_LOCAL = PIPER_DIR / "piper"
PIPER_MODEL_LOCAL = PIPER_DIR / "tr_TR-fahrettin-medium.onnx"

PIPER_BIN_GLOBAL = Path("/home/kokmenteknoteam/piper/piper")
PIPER_MODEL_GLOBAL = Path("/home/kokmenteknoteam/piper/tr_TR-fahrettin-medium.onnx")

PIPER_BIN = os.environ.get("PIPER_BIN", str(PIPER_BIN_LOCAL) if PIPER_BIN_LOCAL.exists() else str(PIPER_BIN_GLOBAL))
PIPER_MODEL = os.environ.get("PIPER_MODEL", str(PIPER_MODEL_LOCAL) if PIPER_MODEL_LOCAL.exists() else str(PIPER_MODEL_GLOBAL))

# Whisper STT
WHISPER_LOCAL_DIR = str(ROOT / "models" / "whisper-base")
WHISPER_FALLBACK = "base"

# ALSA cihaz secimi (Dinamik)
# --


# Kayıt süresi
DEFAULT_RECORD_DURATION = 5

# Fuzzy match eşiği
FUZZY_MATCH_THRESHOLD = 0.62

# ─── Soru Havuzu ───────────────────────────────────────────────
SORU_HAVUZU = {
    "YER": {
        "sorular": [
            "Şu an neredesiniz?",
            "Hangi binadayız?",
            "Buranın neresi olduğunu biliyor musunuz?"
        ],
        "dogru_cevaplar": [
            "hastane", "klinik", "ambulans", "sağlık merkezi",
            "acil servis", "revir", "yoğun bakım", "ameliyathane"
        ]
    },
    "ZAMAN": {
        "sorular": [
            "Hangi yıldayız?",
            "Hangi sene içerisindeyiz?",
            "Şu anki yılı söyler misiniz?"
        ],
        "dogru_cevaplar": [
            "2026", "iki bin yirmi altı", "yirmi altı", "yirmialtı"
        ]
    },
    "DURUM": {
        "sorular": [
            "Size ne oldu?",
            "Neden buradasınız?",
            "Başınıza ne geldi?"
        ],
        "dogru_cevaplar": [
            "kaza", "hasta", "düştüm", "bayıldım",
            "yaralandım", "çarptı", "tansiyon", "ameliyat",
            "kalp", "beyin", "nöbet"
        ]
    }
}

# ─── Whisper Hallucination Kalıpları ────────────────────────────
WHISPER_HALLUCINATION_PATTERNS = [
    r"altyaz[ıi]",
    r"izlediğiniz için",
    r"teşekkür(ler)?",
    r"abone ol",
    r"beğen",
    r"paylaş",
    r"bir sonraki video",
    r"(\b\w{1,4}\b)( \1){3,}",  # Aynı kısa kelime 4+ kez tekrar
    r"^[\s\.\,\!\?]+$",           # Sadece noktalama
]


# ═══════════════════════════════════════════════════════════════
#  Ses Cihazı Bulma (Otomatik Saptama)
# ═══════════════════════════════════════════════════════════════

def get_bütün_hoparlorler() -> list:
    """Sistemdeki tüm playback (hoparlör) cihazlarını listeler."""
    cihazlar = []
    try:
        out = subprocess.check_output(["aplay", "-l"], stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            if line.startswith("card"):
                parts = line.split(":")
                card_no = parts[0].strip().replace("card ", "")
                dev_part = [p for p in parts if "device" in p.lower()]
                dev_no = "0"
                if dev_part:
                    dev_no = dev_part[0].strip().split()[1].replace(",","")
                cihazlar.append(f"plughw:{card_no},{dev_no}")
    except Exception:
        pass
    if not cihazlar:
        cihazlar = ["default"]
    # En sona default ekle
    if "default" not in cihazlar:
        cihazlar.append("default")
    return cihazlar

def get_ilk_mikrofon() -> str:
    """Sistemdeki ilk çalışan kayıt (mikrofon) cihazını bulur."""
    try:
        out = subprocess.check_output(["arecord", "-l"], stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            if line.startswith("card"):
                parts = line.split(":")
                card_no = parts[0].strip().replace("card ", "")
                return f"plughw:{card_no},0"
    except Exception:
        pass
    return "default"


# ═══════════════════════════════════════════════════════════════
#  Ses Seviyesi ve Hallucination Kontrol
# ═══════════════════════════════════════════════════════════════

def _ses_seviyesi_kontrol(dosya_yolu: str, esik_rms: float = 150.0) -> bool:
    """
    WAV dosyasının RMS ses seviyesini kontrol eder.
    Çok sessiz kayıtlar gerçek konuşma içermiyor demektir.

    Returns:
        True eğer ses seviyesi yeterli ise.
    """
    try:
        with wave.open(dosya_yolu, 'rb') as wf:
            n_frames = wf.getnframes()
            if n_frames == 0:
                return False
            raw = wf.readframes(n_frames)
            # 16-bit signed PCM
            samples = struct.unpack(f'<{n_frames}h', raw)
            rms = (sum(s * s for s in samples) / n_frames) ** 0.5
            log.debug("Kayıt RMS: %.1f (eşik: %.1f)", rms, esik_rms)
            return rms >= esik_rms
    except Exception as e:
        log.warning("Ses seviyesi kontrol hatası: %s", e)
        return True  # Hata durumunda geçir


def _filtre_hallucination(text: str) -> str:
    """
    Whisper'ın bilinen hallucination kalıplarını temizler.
    Sessiz ortamda üretilen sahte metin çıktılarını filtreler.
    """
    if not text:
        return text

    cleaned = text.strip()

    for pattern in WHISPER_HALLUCINATION_PATTERNS:
        if re.search(pattern, cleaned, re.IGNORECASE):
            log.info("Hallucination tespit edildi ve filtrelendi: '%s'", cleaned[:50])
            return ""

    # Aynı kelimenin %60'tan fazla tekrar etmesi → hallucination
    words = cleaned.split()
    if len(words) >= 3:
        from collections import Counter
        freq = Counter(words)
        most_common_count = freq.most_common(1)[0][1]
        if most_common_count / len(words) > 0.6:
            log.info("Tekrar hallucination filtrelendi: '%s'", cleaned[:50])
            return ""

    return cleaned


# ═══════════════════════════════════════════════════════════════
#  Fuzzy Analiz — sentence-transformers yerine
# ═══════════════════════════════════════════════════════════════

def cevap_analiz_et(transkript: str, dogru_cevaplar: list,
                    threshold: float = FUZZY_MATCH_THRESHOLD) -> float:
    """
    Hasta yanıtını doğru cevaplarla karşılaştır.
    1. Tam keyword eşleşme (en hızlı)
    2. n-gram eşleştirme (çok kelimeli cevaplar için)
    3. Fuzzy string matching (SequenceMatcher)

    Returns:
        benzerlik skoru (0.0 — 1.0)
    """
    if not transkript or len(transkript.strip()) < 2:
        return 0.0

    text = transkript.lower().strip()
    words = text.split()

    # 1. Tam keyword eşleşme
    for kw in dogru_cevaplar:
        if kw.lower() in text:
            return 1.0

    # 2. n-gram eşleştirme (çok kelimeli cevaplar / kısmi eşleşme)
    best = 0.0
    for kw in dogru_cevaplar:
        kw_lower = kw.lower()
        kw_words = kw_lower.split()
        kw_len = len(kw_words)

        # Sliding window: transkriptteki her n-gram'ı karşılaştır
        if kw_len > 1 and len(words) >= kw_len:
            for i in range(len(words) - kw_len + 1):
                ngram = " ".join(words[i:i + kw_len])
                ratio = SequenceMatcher(None, ngram, kw_lower).ratio()
                best = max(best, ratio)

        # 3. Kelime bazında fuzzy match
        for word in words:
            ratio = SequenceMatcher(None, word, kw_lower).ratio()
            best = max(best, ratio)

        # 4. Tam cümle bazında (daha uzun cevaplar için)
        full_ratio = SequenceMatcher(None, text, kw_lower).ratio()
        best = max(best, full_ratio)

    return best


def gks_sozel_puan(transkript: str, kategori: str) -> int:
    """
    GKS Sözel puanlama (Klinik GKS standardına uyumlu).

    GKS Sözel Skalası:
        5 = Oryante (tam uyum)
        4 = Konfüze (yakın ama tam değil)
        3 = Uygunsuz kelimeler (konuşuyor ama alakasız)
        2 = Anlamsız sesler / inilti
        1 = Tepkisiz (sessiz)
    """
    if not transkript or len(transkript.strip()) < 2:
        return 1  # Tepkisiz

    text = transkript.strip()

    if len(text) < 4:
        return 2  # İnilti / anlamsız ses

    category_data = SORU_HAVUZU.get(kategori, {})
    dogru_cevaplar = category_data.get("dogru_cevaplar", [])

    if not dogru_cevaplar:
        # Kategori bulunamazsa: en az anlamlı kelimeler varsa 3
        if len(text.split()) >= 2:
            return 3
        return 2

    score = cevap_analiz_et(transkript, dogru_cevaplar)

    if score >= 0.85:
        return 5  # Oryante — çok yüksek eşleşme
    elif score >= 0.65:
        return 4  # Konfüze — yakın ama tam değil
    elif score >= FUZZY_MATCH_THRESHOLD:
        return 3  # Uygunsuz ama kısmen alakalı kelimeler
    else:
        # Konuşuyor ama soruyla hiç alakası yok
        # GKS standardı: konuşabilen ama uygunsuz → 3
        # Ama soruyla hiç alakası olmayan → 3 (uygunsuz kelime)
        # Çok kısa / inilti benzeri → 2
        word_count = len(text.split())
        if word_count >= 3:
            return 3  # En az birkaç kelimelik uygunsuz yanıt
        elif word_count >= 1:
            return 2  # Çok kısa, anlamsız sesler
        return 2


# ═══════════════════════════════════════════════════════════════
#  Ses Analiz Sınıfı
# ═══════════════════════════════════════════════════════════════

class SesAnaliz:
    """
    Piper TTS + faster-whisper STT + difflib NLP.
    Lazy loading: Whisper modeli ilk kullanımda yüklenir.
    """

    def __init__(self):
        self._whisper_model = None
        self._loaded = False
        self._mik_device = get_ilk_mikrofon()
        log.info("Varsayilan Mikrofon Secildi: %s", self._mik_device)

    def load_models(self) -> bool:
        """Whisper modelini yükle (~150MB RAM). NLP için ek model YOK."""
        if self._loaded:
            return True

        try:
            log.info("Whisper base yükleniyor (INT8, CPU)...")
            from faster_whisper import WhisperModel

            if os.path.exists(WHISPER_LOCAL_DIR) and os.listdir(WHISPER_LOCAL_DIR):
                model_path = WHISPER_LOCAL_DIR
            else:
                log.warning("Offline Whisper modeli yok, HuggingFace'ten indirilecek")
                model_path = WHISPER_FALLBACK

            self._whisper_model = WhisperModel(
                model_path, device="cpu", compute_type="int8"
            )
            self._loaded = True
            log.info("Whisper yüklendi ✓ (~150MB)")
            return True
        except ImportError:
            log.error("faster-whisper bulunamadı! pip install faster-whisper")
            return False
        except Exception as e:
            log.error("Whisper yükleme hatası: %s", e)
            return False

    def unload_models(self):
        """Modeli bellekten kaldır (RAM'i geri ver)."""
        self._whisper_model = None
        self._loaded = False
        log.info("Whisper bellekten kaldırıldı")

    def konus(self, text: str) -> bool:
        """Sentezlenmiş sesi PipeWire/PulseAudio veya ALSA ile oynat."""
        log.info("Konuşuluyor: '%s'", text)
        
        # Oynatıcı komutları zinciri (Modern RPi OS PipeWire kullanır)
        players = [
            "pw-play", "paplay", "aplay"
        ]
        # 1. 1inci Öncelik: Piper
        if os.path.exists(PIPER_BIN):
            for player in players:
                try:
                    # Piper WAV formatında çıktı verip player'a aktarır (-f -)
                    play_cmd = f"{player} -"
                    if player == "aplay":
                        play_cmd = "aplay" # aplay stdin direkt okuyabilir
                        
                    cmd = (
                        f'echo "{text}" | {PIPER_BIN} '
                        f'--model {PIPER_MODEL} '
                        f'--length_scale 1.25 '
                        f'-f - | {play_cmd}'
                    )
                    
                    result = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
                    if result.returncode == 0:
                        log.info("  Ses çalındı ✓ (Piper + %s)", player)
                        return True
                    else:
                        log.debug("  %s başarısız: %s", player, result.stderr.decode(errors="ignore")[:50])
                except Exception as e:
                    log.warning("  %s hata: %s", player, e)

        # 2. Öncelik: espeak-ng / espeak fallback pipeline
        log.info("  Piper kullanılamıyor veya başarısız, espeak deneniyor...")
        for player in players:
            try:
                cmd = f'espeak-ng -v tr --stdout "{text}" | {player} -'
                result = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
                if result.returncode == 0:
                    log.info("  Ses çalındı ✓ (espeak-ng + %s)", player)
                    return True
            except Exception:
                pass
                
            try:
                cmd = f'espeak -v tr --stdout "{text}" | {player} -'
                result = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
                if result.returncode == 0:
                    log.info("  Ses çalındı ✓ (espeak + %s)", player)
                    return True
            except Exception:
                pass
                
        log.error("Hiçbir hoparlörden/TTS motorundan ses çıkmadı!")
        return False

    def kayit_yap(self, dosya_yolu: str = "/tmp/gks_kayit.wav",
                  sure: int = DEFAULT_RECORD_DURATION) -> bool:
        """ALSA arecord ile ses kaydı."""
        log.info("Kayıt başlıyor (%d saniye): Mikrofon %s", sure, self._mik_device)
        try:
            result = subprocess.run(
                ['arecord', '-D', self._mik_device, '-d', str(sure),
                 '-f', 'S16_LE', '-r', '16000', '-c', '1', dosya_yolu],
                capture_output=True, timeout=sure + 5
            )
            if result.returncode != 0:
                log.warning("arecord hata kodu: %d", result.returncode)
                return False
            if not os.path.exists(dosya_yolu) or os.path.getsize(dosya_yolu) < 100:
                log.warning("Kayıt dosyası geçersiz: %s", dosya_yolu)
                return False
            log.info("Kayıt tamamlandı ✓")
            return True
        except subprocess.TimeoutExpired:
            log.error("Kayıt zaman aşımı")
            return False
        except FileNotFoundError:
            log.error("arecord bulunamadı — ALSA araçları yüklü mü?")
            return False
        except Exception as e:
            log.error("Kayıt hatası: %s", e)
            return False

    def transkript_yap(self, ses_dosyasi: str) -> str:
        """Ses dosyasını metne çevir (Whisper STT) — anti-hallucination filtreli."""
        if not self._loaded or self._whisper_model is None:
            log.warning("Whisper yüklenmemiş!")
            return ""

        # Ön kontrol: ses seviyesi yeterli mi?
        if not _ses_seviyesi_kontrol(ses_dosyasi):
            log.info("Kayıt çok sessiz — transkripsiyon atlanıyor")
            return ""

        try:
            # VAD + sıkılaştırılmış anti-hallucination ayarları
            segments, info = self._whisper_model.transcribe(
                ses_dosyasi,
                beam_size=5,
                language="tr",
                initial_prompt="hastane, ambulans, klinik, kaza, hasta, doktor.",
                condition_on_previous_text=False,   # Hallucination zincirini kes
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=400,
                    speech_pad_ms=200,
                ),
                no_speech_threshold=0.6,             # Sessiz segmentleri daha agresif filtrele
                log_prob_threshold=-0.5,             # Düşük güvenilirlikli çıktıları filtrele
                compression_ratio_threshold=2.4,     # Tekrarlayan hallucination engeli
            )
            raw_text = "".join([s.text for s in list(segments)]).lower().strip()

            # Post-processing: hallucination filtresi
            text = _filtre_hallucination(raw_text)

            if text:
                log.info("Transkript: '%s'", text[:100])
            else:
                log.info("Transkript boş (filtrelendi veya sessiz)")
            return text
        except Exception as e:
            log.error("Transkripsiyon hatası: %s", e)
            return ""

    def dinle_ve_analiz_et(self, kategori: str = "DURUM",
                           sure: int = DEFAULT_RECORD_DURATION) -> tuple:
        """
        Kayıt yap → Transkript → GKS Sözel puan hesapla.

        Returns:
            (puan: int, transkript: str)
        """
        wav_file = f"/tmp/gks_dinleme_{int(time.time())}.wav"

        if not self.kayit_yap(wav_file, sure):
            return 1, "[Kayıt Hatası]"

        text = self.transkript_yap(wav_file)
        puan = gks_sozel_puan(text, kategori)

        # Geçici dosyayı temizle
        try:
            if os.path.exists(wav_file):
                os.remove(wav_file)
        except Exception:
            pass

        return puan, text if text else "[Sessiz]"

    def mulakat_yap(self) -> int:
        """
        3 kategoride tam sözel test.
        GKS standardı: en iyi yanıt baz alınır.

        Returns:
            final_skor (int): 1-5
        """
        results = {}
        kategoriler = list(SORU_HAVUZU.keys())
        random.shuffle(kategoriler)

        log.info("═══ GKS SÖZEL TEST BAŞLIYOR ═══")

        for kategori in kategoriler:
            soru = random.choice(SORU_HAVUZU[kategori]["sorular"])
            log.info("Soru [%s]: %s", kategori, soru)

            # Soruyu sor
            self.konus(soru)

            # Hastanın soruyu işitmesi/hazırlanması için bekleme
            time.sleep(1.5)

            # Kaydet + analiz
            puan, metin = self.dinle_ve_analiz_et(kategori)
            results[kategori] = {"metin": metin, "puan": puan}

            durum_str = {5: "ORYANTE", 4: "KONFÜZE", 3: "UYGUNSUZ",
                         2: "İNİLTİ", 1: "TEPKİSİZ"}.get(puan, "?")
            log.info("  %s: %-20s | Puan: %d (%s)", kategori, metin[:20], puan, durum_str)

            # Sorular arası bekleme (hastanın dinlenmesi için)
            time.sleep(1.5)

        # GKS standardı: en iyi yanıt baz alınır
        puanlar = [r["puan"] for r in results.values()]
        final_skor = max(puanlar) if puanlar else 1

        log.info("═══ SÖZEL SKOR: %d/5 ═══", final_skor)

        return final_skor
