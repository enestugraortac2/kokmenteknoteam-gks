import os
import sys
import psutil
import time
import subprocess
import warnings
warnings.filterwarnings("ignore")

def print_mem():
    vm = psutil.virtual_memory()
    print(f"\n[BELLEK_DURUMU] Toplam: {vm.total/1024**3:.2f} GB | Kullanilabilir: {vm.available/1024**3:.2f} GB | Kullanim: %{vm.percent}")

def record_audio(duration=5, hw_id="hw:3,0", filename="/tmp/uvox_test.wav"):
    print(f"\n[MIKROFON] {duration} saniye boyunca konusun... ({hw_id})")
    cmd = ['arecord', '-D', hw_id, '-d', str(duration), '-f', 'S16_LE', '-r', '16000', '-c', '1', filename]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0 and os.path.getsize(filename) > 100:
        print("[MIKROFON] Ses basariyla kaydedildi.")
        return filename
    else:
        print("[MIKROFON_HATA] Ses kaydedilemedi!")
        return None

def test_ultravox_1b():
    print("="*60)
    print(" ULTRAVOX 1B (LLAMA 3.2 1B) AUDIO-LLM STRES TESTI (RPi 5)")
    print("="*60)
    print_mem()
    
    # 1B olan modeli deniyoruz
    model_id = "fixie-ai/ultravox-v0_5-llama-3_2-1b"
    
    start_time = time.time()
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        import librosa
        
        print("\n[AI_HAZIRLIK] Kutuphaneler import edildi.")
        
        # Processor'i yukle
        print(f"[AI_YUKLEME] Processor (Onyukleyici) indiriliyor/yukleniyor: {model_id} ...")
        processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        print("[AI_YUKLEME] Processor hazir.")
        
        print_mem()
        
        # Modeli yukle
        print(f"\n[AI_YUKLEME] LLM Modeli RAM'e aliniyor (Bu islem RPi'de bikac dakika surebilir, Cihaz KASILABILIR)...")
        # RPi 5 limitleri icin float16/bfloat16 deniyoruz
        model = AutoModelForCausalLM.from_pretrained(
            model_id,
            trust_remote_code=True,
            # RPi CPU icin memory asimi olmamasi adina
            low_cpu_mem_usage=True,
            torch_dtype=torch.float32, 
            device_map="cpu"
        )
        print("[AI_YUKLEME] Model basariyla RAM'e yerlesti!")
        print_mem()
        
        # Sesi Kaydet
        audio_path = record_audio(duration=4)
        if not audio_path:
            return
            
        print("\n[AI_ANALIZ] Ses okunuyor...")
        # librosa ile sesi oku
        audio_array, sr = librosa.load(audio_path, sr=16000)
        
        print("[AI_ANALIZ] Yapay Zeka dusunmeye basladi... (Islemci %100 kullanilacak, Lutfen bekleyin)")
        # Ultravox formatina (prompt) gore:
        # User mesaji olarak magic tag'i veriyoruz
        turns = [
            {
                "role": "system",
                "content": "You are a helpful medical assistant for a patient. The user speaks Turkish. Listen and reply very shortly."
            },
            {
                "role": "user",
                "content": "<|audio|>\nHastanin ne istedigini veya ne dedigini tek kelimeyle soyle."
            }
        ]
        
        # Modeli besle
        t0 = time.time()
        inputs = processor(
            text=processor.apply_chat_template(turns, add_generation_prompt=True, tokenize=False),
            audio=audio_array,
            sampling_rate=sr,
            return_tensors="pt"
        )
        # Cpu'ya gonder
        inputs = {k: v.to("cpu") for k, v in inputs.items() if hasattr(v, 'to')}
        
        print(f"  > Input isleme saniyesi: {time.time()-t0:.2f} sn")
        
        # Ciktilar uret
        print("[AI_URETIM] Yazi uretiliyor...")
        t1 = time.time()
        # Sadece 30 token ile sinirli uret ki test hizli bitsin
        with torch.no_grad():
            output_ids = model.generate(**inputs, max_new_tokens=30, do_sample=False)
            
        # Girdi kismini kes, sadece asistanin urettigini al
        input_len = inputs["input_ids"].shape[-1]
        generated_ids = output_ids[0][input_len:]
        
        response = processor.decode(generated_ids, skip_special_tokens=True)
        t_final = time.time() - t1
        
        print("\n" + "="*50)
        print(f" [ ULTRAVOX CEVABI ]: {response.strip()}")
        print("="*50)
        
        print(f" > LLM Uretim Suresi (Gecikme): {t_final:.2f} Saniye")
        print_mem()

    except Exception as e:
        print("\n[KRITIK_HATA] Model calisirken coktu:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    current_uid = os.geteuid()
    if current_uid == 0:
        print("Lutfen bu scripti root olmadan cagiriniz.")
    test_ultravox_1b()
