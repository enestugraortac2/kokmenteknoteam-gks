#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  NeuroSense GKS v3 — Raspberry Pi 5 Deployment Betiği
#
#  Kullanım:
#    chmod +x deploy.sh
#    ./deploy.sh
# ═══════════════════════════════════════════════════════════

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

export PYTHONIOENCODING=utf-8

echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  NeuroSense GKS v3 — Deployment                ║${NC}"
echo -e "${CYAN}║  Optimize Edilmiş (OpenCV + ONNX + difflib)     ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"

cd "$(dirname "$0")"
PROJE_DIR="$(pwd)"
VENV_DIR="${PROJE_DIR}/venv"
PYTHON="${VENV_DIR}/bin/python3"
PIP="${VENV_DIR}/bin/pip"

echo -e "${GREEN}[1/7]${NC} Proje dizini: ${PROJE_DIR}"

# ─── Eski Süreçleri Temizle ──────────────────────────────
echo -e "\n${GREEN}[2/7]${NC} Eski süreçler temizleniyor..."
sudo pkill -9 -f "python3.*main.py" 2>/dev/null || true
sudo pkill -9 -f "python3.*goz_takip" 2>/dev/null || true
sudo pkill -9 -f "python3.*motor_takip" 2>/dev/null || true
sudo pkill -9 -f "python3.*ses_motoru" 2>/dev/null || true
sudo fuser -k /dev/video0 2>/dev/null || true
sudo fuser -k /dev/video1 2>/dev/null || true
sudo pkill -9 libcamera 2>/dev/null || true
rm -f /dev/shm/gks_skor.json
echo -e "${GREEN}  ✓ Temizlendi${NC}"

# ─── Sistem Bağımlılıkları ────────────────────────────────
echo -e "\n${GREEN}[3/7]${NC} Sistem bağımlılıkları..."
sudo apt-get update -qq 2>/dev/null || true
sudo apt-get install -y -qq \
    python3-dev \
    python3-venv \
    python3-full \
    alsa-utils \
    libopenjp2-7 \
    libtiff5 \
    python3-spidev \
    i2c-tools \
    2>/dev/null || echo -e "${YELLOW}  apt-get atlandı${NC}"
echo -e "${GREEN}  ✓ Sistem bağımlılıkları hazır${NC}"

# ─── Virtual Environment ─────────────────────────────────
echo -e "\n${GREEN}[4/7]${NC} Python sanal ortamı hazırlanıyor..."
if [ ! -d "${VENV_DIR}" ]; then
    echo -e "  Venv oluşturuluyor..."
    python3 -m venv "${VENV_DIR}" --system-site-packages
    echo -e "${GREEN}  ✓ Venv oluşturuldu${NC}"
else
    echo -e "${GREEN}  ✓ Venv zaten mevcut${NC}"
fi

# ─── Python Bağımlılıkları ────────────────────────────────
echo -e "\n${GREEN}[5/7]${NC} Python bağımlılıkları yükleniyor..."
${PIP} install --upgrade pip 2>/dev/null || true
${PIP} install -r requirements.txt 2>&1 | tail -3
echo -e "${GREEN}  ✓ Python bağımlılıkları yüklendi${NC}"

# ─── Model Dosyaları ──────────────────────────────────────
echo -e "\n${GREEN}[6/7]${NC} Model dosyaları kontrol ediliyor..."
mkdir -p models

# ONNX model kontrolü
if [ -f "models/yolov8n-pose.onnx" ]; then
    echo -e "${GREEN}  ✓ YOLOv8n-pose ONNX modeli mevcut${NC}"
elif [ -f "../models/yolov8n-pose.pt" ]; then
    echo -e "${YELLOW}  ONNX modeli yok, export deneniyor...${NC}"
    cp ../models/yolov8n-pose.pt models/ 2>/dev/null || true
    ${PYTHON} onnx_export.py 2>/dev/null || echo -e "${YELLOW}  Export başarısız — PyTorch fallback kullanılacak${NC}"
elif [ -f "../models/yolov8n-pose.onnx" ]; then
    cp ../models/yolov8n-pose.onnx models/
    echo -e "${GREEN}  ✓ ONNX modeli kopyalandı${NC}"
else
    echo -e "${YELLOW}  YOLO modeli bulunamadı!${NC}"
    echo -e "${YELLOW}  Lütfen yolov8n-pose.onnx dosyasını models/ klasörüne koyun${NC}"
fi

# Piper TTS kontrolü
if [ -d "../piper" ]; then
    if [ ! -L "piper" ] && [ ! -d "piper" ]; then
        ln -s ../piper piper
        echo -e "${GREEN}  ✓ Piper TTS symlink oluşturuldu${NC}"
    else
        echo -e "${GREEN}  ✓ Piper TTS mevcut${NC}"
    fi
else
    echo -e "${YELLOW}  Piper TTS bulunamadı — TTS çalışmayacak${NC}"
fi

# Whisper model
if [ -d "../models/whisper-base" ]; then
    if [ ! -L "models/whisper-base" ] && [ ! -d "models/whisper-base" ]; then
        ln -s ../../models/whisper-base models/whisper-base
        echo -e "${GREEN}  ✓ Whisper base model symlink oluşturuldu${NC}"
    else
        echo -e "${GREEN}  ✓ Whisper base model mevcut${NC}"
    fi
fi

# ─── Sistemi Başlat ───────────────────────────────────────
echo -e "\n${GREEN}[7/7]${NC} NeuroSense GKS v3 başlatılıyor..."
echo -e "${CYAN}════════════════════════════════════════════════════${NC}"

# Kamera konfigürasyonu (ortam değişkenleri)
export CAMERA_GOZ_ID="${CAMERA_GOZ_ID:-0}"
export CAMERA_GOZ_ROTATION="${CAMERA_GOZ_ROTATION:-0}"
export CAMERA_GOZ_HFLIP="${CAMERA_GOZ_HFLIP:-0}"
export CAMERA_GOZ_VFLIP="${CAMERA_GOZ_VFLIP:-0}"

export CAMERA_MOTOR_ID="${CAMERA_MOTOR_ID:-1}"
export CAMERA_MOTOR_ROTATION="${CAMERA_MOTOR_ROTATION:-0}"
export CAMERA_MOTOR_HFLIP="${CAMERA_MOTOR_HFLIP:-0}"
export CAMERA_MOTOR_VFLIP="${CAMERA_MOTOR_VFLIP:-0}"

echo -e "  Göz Kamerası:   ID=${CAMERA_GOZ_ID}, Rotation=${CAMERA_GOZ_ROTATION}°, HFlip=${CAMERA_GOZ_HFLIP}, VFlip=${CAMERA_GOZ_VFLIP}"
echo -e "  Motor Kamerası: ID=${CAMERA_MOTOR_ID}, Rotation=${CAMERA_MOTOR_ROTATION}°, HFlip=${CAMERA_MOTOR_HFLIP}, VFlip=${CAMERA_MOTOR_VFLIP}"
echo ""

${PYTHON} main.py
