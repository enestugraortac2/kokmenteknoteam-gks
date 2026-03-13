import logging
import os
import time
import subprocess
from pathlib import Path
import wave
import struct

log = logging.getLogger("SesAnaliz")

ROOT = Path(__file__).parent.parent
PIPER_BIN = ROOT / "piper" / "piper"
PIPER_MODEL = ROOT / "piper" / "tr_TR-fahrettin-medium.onnx"
WHISPER_DIR = ROOT / "models" / "whisper-base"
SENTENCE_TRANSFORMERS_DIR = ROOT / "models" / "sentence-transformers" / "paraphrase-multilingual-MiniLM-L12-v2"

MIK_DEVICE = "plughw:0,0" # Kamera/USB mikrofon
HOP_DEVICE = "plughw:1,0" # USB HoparlÃ¶r

# NLP Benzerlik eÅŸiÄŸi
SIMILARITY_TH = 0.45

class SesAnaliz:
    def __init__(self):
        self.whisper_model = None
        self.nlp_model = None

    def load_models(self):
        if self.whisper_model is None:
            try:
                log.info("Whisper yÃ¼kleniyor...")
                from faster_whisper import WhisperModel
                if WHISPER_DIR.exists():
                    self.whisper_model = WhisperModel(str(WHISPER_DIR), device="cpu", compute_type="int8")
                else:
                    self.whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
                log.info("Whisper baÅŸarÄ±yla yÃ¼klendi âœ“")
            except Exception as e:
                log.error(f"Whisper yÃ¼kleme hatasÄ±: {e}")

        if self.nlp_model is None:
            try:
                log.info("NLP modeli yÃ¼kleniyor...")
                from sentence_transformers import SentenceTransformer
                if SENTENCE_TRANSFORMERS_DIR.exists():
                    self.nlp_model = SentenceTransformer(str(SENTENCE_TRANSFORMERS_DIR))
                else:
                    self.nlp_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
                log.info("NLP baÅŸarÄ±yla yÃ¼klendi âœ“")
            except Exception as e:
                log.error(f"NLP yÃ¼kleme hatasÄ±: {e}")

        return self.whisper_model is not None and self.nlp_model is not None

    def konus(self, text: str):
        """Piper ile cihazdan ses Ã§Ä±karÄ±r"""
        log.info(f"KonuÅŸuluyor: '{text}'")
        try:
            cmd = (
                f'echo "{text}" | {PIPER_BIN} '
                f'--model {PIPER_MODEL} '
                f'--length_scale 1.25 '
                f'--output_raw | '
                f'aplay -r 22050 -f S16_LE -t raw'
            )
            subprocess.run(cmd, shell=True, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.error(f"KonuÅŸma hatasÄ±: {e}")

    def dinle_ve_anla(self, wav_path="/tmp/gks_kayit.wav", duration=5):
        """arecord ile ses kaydeder ve dÃ¶kÃ¼mÃ¼nÃ¼ yapar"""
        log.info("KayÄ±t baÅŸlÄ±yor...")
        try:
            cmd = [
                "arecord", "-D", MIK_DEVICE,
                "-f", "S16_LE", "-c", "1", "-r", "16000",
                "-d", str(duration), wav_path
            ]
            subprocess.run(cmd, stderr=subprocess.DEVNULL, timeout=duration + 2)
            
            if not os.path.exists(wav_path):
                return ""
                
            log.info("Ses dÃ¶kÃ¼mÃ¼ (STT) yapÄ±lÄ±yor...")
            segments, _ = self.whisper_model.transcribe(
                wav_path,
                language="tr",
                beam_size=5,
                vad_filter=True
            )
            
            transcript = " ".join([s.text for s in list(segments)]).strip()
            log.info(f"Duyulan: '{transcript}'")
            return transcript
        except Exception as e:
            log.error(f"Dinleme hatasÄ±: {e}")
            return ""

    def cevap_uygun_mu(self, transkript: str, dogru_cevaplar: list):
        if not transkript or not self.nlp_model:
            return False
            
        transkript = transkript.lower()
        emb_transkript = self.nlp_model.encode(transkript, normalize_embeddings=True)
        emb_cevaplar = self.nlp_model.encode([c.lower() for c in dogru_cevaplar], normalize_embeddings=True)
        
        from sentence_transformers import util
        benzerlikler = util.cos_sim(emb_transkript, emb_cevaplar)[0]
        en_iyi_skor = max(benzerlikler).item()
        
        return en_iyi_skor >= SIMILARITY_TH
SORU_HAVUZU = {
    'YER': {
        'sorular': ['Hangi binadayız?', 'Şu an neresi?'],
        'dogru_cevaplar': ['hastane', 'ev', 'oda', 'sağlık', 'klinik']
    },
    'ZAMAN': {
        'sorular': ['Hangi senedeyiz?', 'Aylardan ne?'],
        'dogru_cevaplar': ['iki bin', 'yirmi', 'ilkbahar', 'yaz', 'sonbahar', 'kış', 'ocak', 'şubat', 'mart']
    }
}
