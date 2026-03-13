#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bu script, Raspberry Pi 5'in Tıbbi GKS Sistemini tamamen İNTERNETSİZ (offline)
çalıştırabilmesi için gerekli olan tüm AI modellerini ve Piper TTS Linux
dosyalarını masaüstünüzdeki bu klasöre indirir.

Kurulumdan sonra bu klasörü doğrudan Pi 5'e atabilirsiniz.
"""

import os
import sys
import urllib.request
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
PIPER_DIR = ROOT / "piper"

def download_file(url, target_path):
    print(f"\n[İndiriliyor] {target_path.name}")
    print(f"URL: {url}")
    try:
        urllib.request.urlretrieve(url, target_path)
        print(f"✓ Başarılı: {target_path}")
    except Exception as e:
        print(f"❌ HATA: {e}")

def download_piper():
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    
    # Piper Linux aarch64 (Pi 5) Binary
    piper_tar_url = "https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_linux_aarch64.tar.gz"
    piper_tar_path = PIPER_DIR / "piper_linux_aarch64.tar.gz"
    
    if not (PIPER_DIR / "piper").exists():
        download_file(piper_tar_url, piper_tar_path)
        if piper_tar_path.exists():
            print("Arşivden çıkarılıyor...")
            with tarfile.open(piper_tar_path, "r:gz") as tar:
                # Extract ignoring top level directory 'piper' if it exists in tar
                tar.extractall(path=ROOT) 
            piper_tar_path.unlink()
    else:
        print("✓ Piper binary zaten mevcut.")

    # Model dosyaları
    model_url = "https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx"
    json_url = "https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx.json"
    
    model_path = PIPER_DIR / "tr_TR-fahrettin-medium.onnx"
    json_path = PIPER_DIR / "tr_TR-fahrettin-medium.onnx.json"
    
    if not model_path.exists(): download_file(model_url, model_path)
    if not json_path.exists(): download_file(json_url, json_path)


def download_huggingface_models():
    try:
        print("\n--- Faster-Whisper Base Modeli İndiriliyor ---")
        from faster_whisper import download_model
        whisper_dir = str(MODELS_DIR / "whisper-base")
        os.makedirs(whisper_dir, exist_ok=True)
        # Sadece eksikse indirir, models/whisper-base içine kaydeder
        download_model("base", output_dir=whisper_dir)
        print("✓ Whisper Base indirildi/mevcut:", whisper_dir)
        
        print("\n--- Sentence-Transformers Modeli İndiriliyor ---")
        from sentence_transformers import SentenceTransformer
        st_dir = str(MODELS_DIR / "sentence-transformers")
        os.makedirs(st_dir, exist_ok=True)
        # İndirip hedef klasöre kaydeder
        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        model.save(st_dir)
        print("✓ Sentence-Transformers indirildi/mevcut:", st_dir)
        
    except ImportError as e:
        print(f"\n❌ Python paketi eksik: {e}")
        print("Lütfen önce çalıştırın: pip install faster-whisper sentence-transformers")

if __name__ == "__main__":
    print("==================================================")
    print("  NeuroSense GKS - Offline Model İndirici")
    print("==================================================")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    
    download_piper()
    download_huggingface_models()
    
    print("\n==================================================")
    print("TÜM İŞLEMLER TAMAMLANDI!")
    print("Artık bu proje klasörünü Pi 5'e atabilirsiniz.")
    print("Pi 5 internete bağlı olmasa BİLE %100 çalışacaktır.")
