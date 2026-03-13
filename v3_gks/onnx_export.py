#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroSense GKS v3 — ONNX Model Export Scripti

YOLOv8n-pose.pt → YOLOv8n-pose.onnx dönüşümü yapar.
Bu script, PyTorch + ultralytics yüklü bir makinede BİR KERE çalıştırılır.
Sonuç ONNX dosyası Pi 5'e taşınır — Pi'de PyTorch gerekmez.

Kullanım:
    python3 onnx_export.py
"""

import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
PT_MODEL = MODELS_DIR / "yolov8n-pose.pt"
ONNX_MODEL = MODELS_DIR / "yolov8n-pose.onnx"

INPUT_SIZE = 320  # Pi 5 için optimize boyut


def main():
    print("=" * 50)
    print("  NeuroSense GKS v3 — ONNX Model Export")
    print("=" * 50)

    # Model dosyası kontrolü
    if not PT_MODEL.exists():
        # Ana proje dizinindeki modeli kontrol et
        alt_path = ROOT.parent / "models" / "yolov8n-pose.pt"
        if alt_path.exists():
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(alt_path, PT_MODEL)
            print(f"Model kopyalandı: {alt_path} → {PT_MODEL}")
        else:
            print(f"HATA: Model bulunamadı: {PT_MODEL}")
            print("Lütfen yolov8n-pose.pt dosyasını models/ klasörüne koyun")
            print("veya: python3 -c \"from ultralytics import YOLO; YOLO('yolov8n-pose.pt')\"")
            sys.exit(1)

    if ONNX_MODEL.exists():
        print(f"ONNX modeli zaten mevcut: {ONNX_MODEL}")
        resp = input("Üzerine yazmak istiyor musunuz? (e/h): ").strip().lower()
        if resp != "e":
            print("İptal edildi.")
            return

    print(f"\nKaynak: {PT_MODEL}")
    print(f"Hedef:  {ONNX_MODEL}")
    print(f"Boyut:  {INPUT_SIZE}x{INPUT_SIZE}")
    print()

    try:
        from ultralytics import YOLO
        model = YOLO(str(PT_MODEL))
        model.export(format="onnx", imgsz=INPUT_SIZE, simplify=True)

        # ultralytics .onnx'i aynı dizine yazar
        exported = PT_MODEL.with_suffix(".onnx")
        if exported.exists() and exported != ONNX_MODEL:
            exported.rename(ONNX_MODEL)

        if ONNX_MODEL.exists():
            size_mb = ONNX_MODEL.stat().st_size / (1024 * 1024)
            print(f"\n✓ ONNX export başarılı!")
            print(f"  Dosya: {ONNX_MODEL}")
            print(f"  Boyut: {size_mb:.1f} MB")
            print(f"\nBu dosyayı Pi 5'e taşıyabilirsiniz.")
            print("Pi 5'te sadece 'onnxruntime' gerekir, PyTorch gerekmez!")
        else:
            print(f"\nHATA: ONNX dosyası oluşturulamadı")
            sys.exit(1)

    except ImportError:
        print("HATA: ultralytics paketi bulunamadı!")
        print("Yükleme: pip install ultralytics")
        sys.exit(1)
    except Exception as e:
        print(f"HATA: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
