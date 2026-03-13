#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  NeuroSense GKS — Raspberry Pi 5 Deployment Betiği
#  Bu betik Pi 5 üzerinde çalıştırılacaktır.
#
#  Kullanım:
#    chmod +x deploy.sh
#    ./deploy.sh
# ═══════════════════════════════════════════════════════════

# Renk kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  NeuroSense GKS — Deployment Başlıyor           ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"

# Proje dizinine git
cd "$(dirname "$0")"
PROJE_DIR="$(pwd)"
echo -e "${GREEN}[1/7]${NC} Proje dizini: ${PROJE_DIR}"

# ─── Senkronizasyon ──────────────────────────────────────────
echo -e "\n${GREEN}[2/7]${NC} Dosya taşıması tamamlandı (USB Flash mod)."

# ─── Zombi Süreçleri Temizle ──────────────────────────────
echo -e "\n${GREEN}[3/7]${NC} Eski süreçler temizleniyor..."
sudo pkill -9 -f "python3.*goz_takip" 2>/dev/null || true
sudo pkill -9 -f "python3.*motor_takip" 2>/dev/null || true
sudo pkill -9 -f "python3.*ses_motoru" 2>/dev/null || true
sudo pkill -9 -f "python3.*main.py" 2>/dev/null || true

# Kamera kaynakları serbest bırak
sudo fuser -k /dev/video0 2>/dev/null || true
sudo fuser -k /dev/video1 2>/dev/null || true
sudo fuser -k /dev/video2 2>/dev/null || true
sudo fuser -k /dev/video3 2>/dev/null || true

# libcamera süreçlerini temizle
sudo pkill -9 libcamera 2>/dev/null || true

# RAM disk temizle ve doğru izinlerle yeniden oluştur
sudo rm -f /dev/shm/gks_skor.json
echo '{}' > /dev/shm/gks_skor.json
chmod 666 /dev/shm/gks_skor.json

echo -e "${GREEN}  ✓ Eski süreçler temizlendi${NC}"

# ─── Sistem Bağımlılıkları ────────────────────────────────
echo -e "\n${GREEN}[4/7]${NC} Sistem bağımlılıkları kontrol ediliyor..."
sudo apt-get update -qq

# dlib, IPC, LCD ve I2C için gerekli
sudo apt-get install -y -qq \
    cmake \
    libboost-all-dev \
    libjpeg-dev \
    libpng-dev \
    build-essential \
    python3-dev \
    alsa-utils \
    libopenjp2-7 \
    libtiff5 \
    python3-spidev \
    i2c-tools \
    2>/dev/null

echo -e "${GREEN}  ✓ Sistem bağımlılıkları hazır${NC}"

# ─── Python Bağımlılıkları ────────────────────────────────
echo -e "\n${GREEN}[5/7]${NC} Python bağımlılıkları yükleniyor..."
./v3_gks/venv/bin/pip install --upgrade pip 2>/dev/null || true
./v3_gks/venv/bin/pip install -r requirements.txt

echo -e "${GREEN}  ✓ Python bağımlılıkları yüklendi${NC}"

# ─── Model Dosyaları Kontrol ──────────────────────────────
echo -e "\n${GREEN}[6/7]${NC} Model dosyaları kontrol ediliyor..."
mkdir -p models

# YOLOv8n-pose model kontrolü
if [ ! -f "models/yolov8n-pose.pt" ]; then
    echo -e "${YELLOW}  YOLOv8n-pose modeli bulunamadı, indiriliyor...${NC}"
    ./v3_gks/venv/bin/python -c "from ultralytics import YOLO; m = YOLO('yolov8n-pose.pt'); import shutil; shutil.move('yolov8n-pose.pt', 'models/yolov8n-pose.pt')" 2>/dev/null || {
        echo -e "${RED}  [HATA] YOLO model indirilemedi!${NC}"
    }
else
    echo -e "${GREEN}  ✓ YOLOv8n-pose modeli mevcut${NC}"
fi

# dlib shape predictor kontrolü
if [ ! -f "models/shape_predictor_68_face_landmarks.dat" ]; then
    echo -e "${YELLOW}  dlib shape predictor bulunamadı!${NC}"
    echo -e "${YELLOW}  Lütfen shape_predictor_68_face_landmarks.dat dosyasını models/ klasörüne kopyalayın${NC}"
    echo -e "${YELLOW}  İndirme: http://dlib.net/files/shape_predictor_68_face_landmarks.dat.bz2${NC}"
else
    echo -e "${GREEN}  ✓ dlib shape predictor mevcut${NC}"
fi

# Piper TTS kontrolü
if [ ! -f "./piper/piper" ]; then
    echo -e "${YELLOW}  Piper TTS bulunamadı!${NC}"
    echo -e "${YELLOW}  Piper'ı ./piper/ klasörüne kurmanız gerekiyor${NC}"
    echo -e "${YELLOW}  - https://github.com/rhasspy/piper/releases${NC}"
else
    echo -e "${GREEN}  ✓ Piper TTS mevcut${NC}"
fi

# ─── Log Klasörü ──────────────────────────────────────────
mkdir -p logs

# ─── Sistemi Başlat ───────────────────────────────────────
echo -e "\n${GREEN}[7/7]${NC} NeuroSense GKS sistemi başlatılıyor..."
echo -e "${CYAN}════════════════════════════════════════════════════${NC}"
echo ""

./v3_gks/venv/bin/python main.py
