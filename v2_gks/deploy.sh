#!/bin/bash

echo "======================================"
echo " NeuroSense GKS (v2 - Tekli Çekirdek) "
echo "======================================"

cd "$(dirname "$0")"

if [ ! -d "gks_modules" ]; then
    echo "HATA: gks_modules/ klasörü bulunamadı. Yanlış dizindesiniz."
    exit 1
fi

if [ ! -d "models" ]; then
    echo "Modeller ../ klasöründen onarılıyor..."
    cp -R ../models . || true
    echo "Modeller başarıyla v2_gks içine taşındı."
fi

if [ ! -f "piper/piper" ]; then
    echo "Piper ../ klasöründen onarılıyor..."
    cp -R ../piper . || true
fi

chmod +x piper/piper 2>/dev/null

echo "Sistem Başlatılıyor..."
python3 main.py
