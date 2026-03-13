#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║  NeuroSense GKS — Ses Motoru (Speech Engine)                ║
║  Raspberry Pi 5 — Endüstriyel Sınıf, Fault-Tolerant        ║
╚══════════════════════════════════════════════════════════════╝

Görev:
  - TTS: Piper ile Türkçe konuşma sentezi
  - STT: faster-whisper (base model, int8) ile konuşma tanıma
  - NLP: sentence-transformers ile anlamsal benzerlik analizi
  - GKS Sözel puanlama (1-5)

Modlar:
  --mode speak --text "..."     → Yalnızca konuşma
  --mode interview              → Tam 3 kategorili sözel test
  --mode listen --duration 5    → Kayıt + analiz, sonucu SHM'ye yaz

IPC:
  - /dev/shm/gks_skor.json (fcntl exclusive lock ile atomik yazma)
"""

import os
import sys
import time
import json
import errno
import atexit
import signal
import argparse
import logging
import subprocess
import threading
import random
from pathlib import Path
from difflib import SequenceMatcher

import numpy as np

# ─── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[SES %(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ses_motoru")

# ─── Platform-safe fcntl ────────────────────────────────────────
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

# ─── Konfigürasyon ─────────────────────────────────────────────
ROOT = Path(__file__).parent # Kök dizin
SHM_PATH = Path(os.environ.get("GKS_SHM_PATH", "/dev/shm/gks_skor.json"))

# Piper TTS
PIPER_DIR = ROOT / "piper"
PIPER_BIN = os.environ.get("PIPER_BIN", str(PIPER_DIR / "piper"))
PIPER_MODEL = os.environ.get("PIPER_MODEL", str(PIPER_DIR / "tr_TR-fahrettin-medium.onnx"))

# Lokal Model Yolları (İnternet bağlantısı gerektirmemesi için)
WHISPER_HUGGINGFACE_MODEL = "base" # Fallback için HuggingFace adı
WHISPER_LOCAL_DIR = str(ROOT / "models" / "whisper-base")

SENTENCE_TRANSFORMERS_NAME = "paraphrase-multilingual-MiniLM-L12-v2" # Fallback için HuggingFace adı
SENTENCE_TRANSFORMERS_DIR = str(ROOT / "models" / "sentence-transformers" / SENTENCE_TRANSFORMERS_NAME)

# Whisper model: "base" daha iyi Türkçe tanıma sağlar (Pi 5'te ~74MB RAM)
# "tiny" yerine "base" → %40 daha iyi WER (Word Error Rate) Türkçe'de
WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL", "base") # This line is now redundant if WHISPER_HUGGINGFACE_MODEL is used as fallback. Keeping for now.

# ALSA cihaz adresleri
# Otomatik saptama: ilk calışan mikrofon cihazını bul
def _bul_ilk_mikrofon() -> str:
    """Sistemdeki ilk çalışan kayıt (mikrofon) cihazını bulur."""
    try:
        out = subprocess.check_output(
            ["arecord", "-l"], stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            if line.startswith("card"):
                parts = line.split(":")
                card_no = parts[0].strip().replace("card ", "")
                return f"plughw:{card_no},0"
    except Exception:
        pass
    return "default"

MIK_DEVICE = os.environ.get("MIK_DEVICE", _bul_ilk_mikrofon())
HOP_DEVICE = os.environ.get("HOP_DEVICE", "default")
log.info("Mikrofon cihazı: %s", MIK_DEVICE)

# NLP benzerlik eşiği
SIMILARITY_THRESHOLD = 0.45

# Kayıt süresi (saniye)
DEFAULT_RECORD_DURATION = 5

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


# ═══════════════════════════════════════════════════════════════
#  IPC: RAM Disk'e Güvenli Yazma
# ═══════════════════════════════════════════════════════════════

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
#  Piper TTS — Konuşma Sentezi
# ═══════════════════════════════════════════════════════════════

def speak(text: str) -> bool:
    """Piper TTS ile Türkçe konuşma sentezi."""
    log.info("Konuşuluyor: '%s'", text)
    try:
        wav_path = "/tmp/gks_tts_out.wav"
        
        # 1. Piper ile WAV oluştur
        cmd_piper = (
            f'echo "{text}" | {PIPER_BIN} '
            f'--model {PIPER_MODEL} '
            f'--length_scale 1.25 '
            f'--output_file {wav_path}'
        )
        res_piper = subprocess.run(cmd_piper, shell=True, capture_output=True, timeout=30)
        
        if res_piper.returncode != 0:
            stderr = res_piper.stderr.decode(errors="ignore")
            log.warning("Piper hata kodu %d: %s", res_piper.returncode, stderr[:200])
            return False

        # 2. PipeWire (pw-play) ile WAV oynat
        if os.path.exists(wav_path):
            cmd_play = f"pw-play {wav_path}"
            res_play = subprocess.run(cmd_play, shell=True, capture_output=True, timeout=30)
            if res_play.returncode != 0:
                log.warning("pw-play hata verdi, ALSA veya aygıt sorunu olabilir.")
                return False
            return True
        else:
            log.error("Piper WAV dosyası oluşturamadı.")
            return False

    except subprocess.TimeoutExpired:
        log.error("TTS zaman aşımı (30s)")
        return False
    except Exception as e:
        log.error("Piper hatası: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════
#  Ses Kaydı
# ═══════════════════════════════════════════════════════════════

def record_audio(filename: str, duration: int = DEFAULT_RECORD_DURATION) -> bool:
    """ALSA arecord ile ses kaydı. Otomatik cihaz deneme ile."""
    devices_to_try = [MIK_DEVICE]
    if MIK_DEVICE != "default":
        devices_to_try.append("default")

    for device in devices_to_try:
        log.info("Kayıt başlıyor (%d saniye, cihaz: %s): %s", duration, device, filename)
        try:
            result = subprocess.run(
                ['arecord', '-D', device, '-d', str(duration),
                 '-f', 'S16_LE', '-r', '16000', '-c', '1', filename],
                capture_output=True, timeout=duration + 10
            )
            if result.returncode != 0:
                stderr = result.stderr.decode(errors='ignore')[:200]
                log.warning("arecord hata (cihaz: %s, kod: %d): %s", device, result.returncode, stderr)
                continue
            if not os.path.exists(filename) or os.path.getsize(filename) < 100:
                log.warning("Kayıt dosyası geçersiz: %s", filename)
                continue
            log.info("Kayıt tamamlandı ✓ (cihaz: %s)", device)
            return True
        except subprocess.TimeoutExpired:
            log.error("Kayıt zaman aşımı (cihaz: %s)", device)
        except FileNotFoundError:
            log.error("arecord bulunamadı — ALSA araçları yüklü mü?")
            return False
        except Exception as e:
            log.error("Kayıt hatası (cihaz: %s): %s", device, e)

    log.error("Hiçbir mikrofon cihazından kayıt yapılamadı!")
    return False


# ═══════════════════════════════════════════════════════════════
#  Whisper STT + NLP Analiz
# ═══════════════════════════════════════════════════════════════

class SpeechAnalyzer:
    """
    faster-whisper ile STT + sentence-transformers ile semantik analiz.
    Lazy initialization: modeller ilk kullanımda yüklenir.
    """

    def __init__(self):
        self._whisper_model = None
        self._nlp_model = None
        self._initialized = False
        self.device = "cpu" # Raspberry Pi 5 için
        self.compute = "int8" # Raspberry Pi 5 için

    def _ensure_initialized(self):
        """Modelleri lazy olarak yükle (RAM tasarrufu)."""
        if self._initialized:
            return

        log.info("🧠 Whisper yükleniyor (LOKAL KLASÖR)...")
        from faster_whisper import WhisperModel
        
        # Eğer model indirilmişse lokalden yükle, yoksa hata fırlat ki offline çalışmadığı anlaşılsın
        if os.path.exists(WHISPER_LOCAL_DIR) and os.listdir(WHISPER_LOCAL_DIR):
            model_path = WHISPER_LOCAL_DIR
        else:
            log.warning("Offline Whisper modeli bulunamadı! Lütfen offline_modelleri_indir.py scriptini çalıştırın.")
            model_path = WHISPER_HUGGINGFACE_MODEL # Fallback

        self._whisper_model = WhisperModel(model_path, device=self.device, compute_type=self.compute)
        log.info("Whisper başarıyla yüklendi (%s)", model_path)
        
        log.info("🧠 NLP Modeli yükleniyor (LOKAL KLASÖR)...")
        from sentence_transformers import SentenceTransformer
        
        if os.path.exists(SENTENCE_TRANSFORMERS_DIR) and os.listdir(SENTENCE_TRANSFORMERS_DIR):
            st_path = SENTENCE_TRANSFORMERS_DIR
        else:
            log.warning("Offline Sentence-Transformers modeli bulunamadı! Lütfen offline_modelleri_indir.py scriptini çalıştırın.")
            st_path = SENTENCE_TRANSFORMERS_NAME # Fallback
            
        self._nlp_model = SentenceTransformer(st_path)
        log.info("NLP başarıyla yüklendi (%s)", st_path)
        self._initialized = True

    def transcribe(self, audio_file: str) -> str:
        """Ses dosyasını metne çevir."""
        self._ensure_initialized()
        try:
            segments, _ = self._whisper_model.transcribe(
                audio_file, beam_size=1, language="tr"
            )
            text = "".join([s.text for s in segments]).lower().strip()
            log.info("Transkript: '%s'", text[:100])
            return text
        except Exception as e:
            log.error("Transkripsiyon hatası: %s", e)
            return ""

    def analyze_response(self, text: str, category: str) -> int:
        """
        Hasta yanıtını analiz et ve GKS Sözel puanı hesapla.
        difflib tabanlı hızlı analiz (sentence-transformers gerektirmez).

        GKS Sözel Skalası:
            5 = Oryante (tam uyum)
            4 = Konfüze (kafa karışık ama anlamlı)
            3 = Uygunsuz kelimeler (anlamsız ama kelime var)
            2 = Anlamsız sesler / inilti
            1 = Tepkisiz (sessiz)

        Returns:
            puan (int): 1-5
        """
        # Sessiz / boş
        if not text or len(text.strip()) < 2:
            return 1  # Tepkisiz

        # Çok kısa, anlamsız ses
        if len(text.strip()) < 4:
            return 2  # İnilti / anlamsız ses

        category_data = SORU_HAVUZU.get(category, {})
        correct_answers = category_data.get("dogru_cevaplar", [])

        if not correct_answers:
            if len(text.split()) >= 2:
                return 3
            return 2

        text_lower = text.lower().strip()
        words = text_lower.split()

        # 1. Tam keyword eşleşme (en hızlı)
        for kw in correct_answers:
            if kw.lower() in text_lower:
                return 5  # Oryante — tam eşleşme

        # 2. Fuzzy matching (difflib SequenceMatcher)
        best_score = 0.0
        for kw in correct_answers:
            kw_lower = kw.lower()
            # Kelime bazlı fuzzy
            for word in words:
                ratio = SequenceMatcher(None, word, kw_lower).ratio()
                best_score = max(best_score, ratio)
            # Tam cümle bazlı
            full_ratio = SequenceMatcher(None, text_lower, kw_lower).ratio()
            best_score = max(best_score, full_ratio)

        if best_score >= 0.85:
            return 5  # Oryante
        elif best_score >= 0.65:
            return 4  # Konfüze
        elif best_score >= 0.50:
            return 3  # Uygunsuz ama kısmen alakalı
        elif len(words) >= 3:
            return 3  # Konuşuyor ama soruyla ilgisiz
        elif len(words) >= 1:
            return 2  # Çok kısa
        return 2


# ═══════════════════════════════════════════════════════════════
#  GKS Sözel Test — Tam Mülakatls
# ═══════════════════════════════════════════════════════════════

def run_interview() -> int:
    """
    3 kategoride sözel test yap ve GKS sözel puanını hesapla.
    En iyi yanıtı baz alır (GKS standardına uygun).

    Returns:
        final_skor (int): 1-5
    """
    analyzer = SpeechAnalyzer()
    results = {}

    kategoriler = list(SORU_HAVUZU.keys())
    random.shuffle(kategoriler)  # Çeşitlilik için sırayı karıştır

    log.info("═══ GKS SÖZEL TEST BAŞLIYOR ═══")

    for kategori in kategoriler:
        soru = random.choice(SORU_HAVUZU[kategori]["sorular"])
        log.info("Soru [%s]: %s", kategori, soru)

        # Soruyu sor
        speak(soru)

        # Konuştuktan sonra hastanın işitmesi/hazırlanması için bekleme
        time.sleep(1.5)

        # Kaydet
        wav_file = f"/tmp/gks_kayit_{kategori}.wav"
        recording_ok = record_audio(wav_file, duration=DEFAULT_RECORD_DURATION)

        if not recording_ok:
            log.warning("[%s] Kayıt başarısız — puan: 1", kategori)
            results[kategori] = {"metin": "[Kayıt Hatası]", "puan": 1}
            continue

        # Analiz (transkript + NLP)
        text = analyzer.transcribe(wav_file)
        puan = analyzer.analyze_response(text, kategori)

        results[kategori] = {"metin": text if text else "[Sessiz]", "puan": puan}

        # Geçici dosyayı temizle
        try:
            if os.path.exists(wav_file):
                os.remove(wav_file)
        except Exception:
            pass

        # Sorular arası bekleme (hastanın dinlenmesi için)
        time.sleep(1.5)

    # ─── Raporlama ──────────────────────────────────────────
    log.info("")
    log.info("=" * 40)
    log.info("      GKS SÖZEL RAPORU")
    log.info("=" * 40)

    puanlar = []
    for kat in ["YER", "ZAMAN", "DURUM"]:
        data = results.get(kat, {"metin": "[Sessiz]", "puan": 1})
        puanlar.append(data["puan"])
        durum_str = {5: "ORYANTE", 4: "KONFÜZE", 3: "UYGUNSUZ",
                     2: "INILTI", 1: "TEPKISIZ"}.get(data["puan"], "?")
        log.info("  %s: %-20s | Puan: %d (%s)",
                 kat, data["metin"][:20], data["puan"], durum_str)

    # GKS standardı: en iyi yanıt baz alınır
    final_skor = max(puanlar) if puanlar else 1

    log.info("=" * 40)
    log.info("  SÖZEL SKOR: %d/5", final_skor)
    log.info("=" * 40)

    # SHM'ye yaz
    write_shm({
        "ses_skor": final_skor,
        "ses_durum": {5: "ORYANTE", 4: "KONFUZE", 3: "UYGUNSUZ",
                      2: "INILTI", 1: "TEPKISIZ"}.get(final_skor, "BILINMIYOR"),
        "ses_ts": time.time(),
        "ses_detay": results,
    })

    # Sonucu sesli bildir
    speak(f"Test tamamlandı. Sözel bilinciniz 5 üzerinden {final_skor} olarak değerlendirildi.")

    return final_skor


# ═══════════════════════════════════════════════════════════════
#  Tek Kayıt + Analiz Modu (main.py'den çağrılır)
# ═══════════════════════════════════════════════════════════════

def listen_and_analyze(duration: int = DEFAULT_RECORD_DURATION,
                       category: str = "DURUM") -> dict:
    """
    Belirtilen süre kadar kayıt yap, analiz et, sonucu döndür.
    main.py Aşama 2'de kullanılır.
    """
    analyzer = SpeechAnalyzer()
    wav_file = f"/tmp/gks_dinleme_{int(time.time())}.wav"

    recording_ok = record_audio(wav_file, duration=duration)
    if not recording_ok:
        return {"metin": "[Kayıt Hatası]", "puan": 1}

    text = analyzer.transcribe(wav_file)
    puan = analyzer.analyze_response(text, category)

    try:
        if os.path.exists(wav_file):
            os.remove(wav_file)
    except Exception:
        pass

    result = {"metin": text if text else "[Sessiz]", "puan": puan}
    write_shm({
        "ses_skor": puan,
        "ses_durum": {5: "ORYANTE", 4: "KONFUZE", 3: "UYGUNSUZ",
                      2: "INILTI", 1: "TEPKISIZ"}.get(puan, "BILINMIYOR"),
        "ses_ts": time.time(),
    })
    return result


# ═══════════════════════════════════════════════════════════════
#  Graceful Shutdown
# ═══════════════════════════════════════════════════════════════

def _signal_handler(signum, frame):
    log.info("Sinyal alındı (%s), kapatılıyor...", signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ═══════════════════════════════════════════════════════════════
#  CLI Entry Point
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="NeuroSense GKS Ses Motoru"
    )
    parser.add_argument(
        "--mode", choices=["speak", "interview", "listen"],
        default="interview",
        help="Çalışma modu: speak (konuş), interview (tam test), listen (kayıt+analiz)"
    )
    parser.add_argument(
        "--text", type=str, default=None,
        help="speak modunda söylenecek metin"
    )
    parser.add_argument(
        "--duration", type=int, default=DEFAULT_RECORD_DURATION,
        help="listen modunda kayıt süresi (saniye)"
    )
    parser.add_argument(
        "--category", type=str, default="DURUM",
        choices=["YER", "ZAMAN", "DURUM"],
        help="listen modunda analiz kategorisi"
    )

    # Eski uyumluluk: --say argümanı
    parser.add_argument("--say", type=str, default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()

    # Eski --say uyumluluğu
    if args.say:
        speak(args.say)
        return

    if args.mode == "speak":
        if not args.text:
            log.error("speak modunda --text gerekli")
            sys.exit(1)
        speak(args.text)

    elif args.mode == "interview":
        score = run_interview()
        log.info("Final sözel skor: %d/5", score)

    elif args.mode == "listen":
        result = listen_and_analyze(
            duration=args.duration,
            category=args.category
        )
        log.info("Dinleme sonucu: %s", result)


if __name__ == "__main__":
    main()
