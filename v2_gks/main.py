import os
import sys
import time
import logging
import cv2
from enum import Enum, auto
from pathlib import Path

# --- Log Ayarları ---
log = logging.getLogger("MAIN")
log.setLevel(logging.DEBUG)
sh = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("[%(name)s %(asctime)s] %(message)s", datefmt="%H:%M:%S")
sh.setFormatter(formatter)
log.addHandler(sh)

# --- Yardımcı Modüllerin Yüklenmesi ---
try:
    from gks_modules.camera_manager import GKS_Camera
    from gks_modules.goz_analiz import GozAnaliz
    from gks_modules.motor_analiz import MotorAnaliz
    from gks_modules.ses_analiz import SesAnaliz, SORU_HAVUZU
    from gks_modules.ekran_analiz import GKSEkran
    from gks_modules.nabiz_sensoru import NabizSensor
except ImportError as e:
    log.error(f"Kritik bağımlılık yüklenemedi: {e}")
    sys.exit(1)

class GKSState(Enum):
    BASLANGIC = auto()
    PASIF_GOZLEM = auto()
    SOZEL_UYARAN = auto()
    MOTOR_KOMUT = auto()
    AGRILI_UYARAN = auto()
    FINAL_RAPOR = auto()
    TAMAMLANDI = auto()

class GKSSystem:
    def __init__(self):
        log.info("NeuroSense GKS (Monolitik Mimari) Başlatılıyor...")
        
        # Donanım ve Sensör Sınıfları
        self.ekran = GKSEkran()
        self.nabiz = NabizSensor()
        self.kamera = GKS_Camera(camera_id=0) # Sadece 1 kamera yorulacak
        
        # AI Analiz Sınıfları
        self.goz_ai = GozAnaliz()
        self.motor_ai = MotorAnaliz()
        self.ses_ai = SesAnaliz()
        
        self.state = GKSState.BASLANGIC
        self.is_running = True
        
        self.skorlar = {
            "goz": 1,
            "motor": 1,
            "sozel": 1
        }
        
    def setup(self):
        # Ekranı Başlat
        self.ekran.baslat()
        
        # Nabız Sensörünü Başlat
        self.nabiz.baslat()
        
        # OOM(Out of Memory) yememek için Modülleri Sırayla Yükle
        log.info("Yapay Zeka Modelleri RAM'e çekiliyor (Lütfen Bekleyin)...")
        # Goz modelini kalıcı al, küçük (100MB)
        self.goz_ai.load_model()
        # Ses modeli büyük (1-2GB), sadece gerektiğinde
        #self.ses_ai.load_models() 
        # Motor modeli büyük, sadece gerektiğinde
        #self.motor_ai.load_model()
        log.info("Sistem Hazır ✓")
        
    def goz_test_et(self, duration_sec, success_threshold_sec=3):
        start_t = time.time()
        open_time = 0
        
        self.kamera.start()
        log.info(f"Göz testi başladı (Süre: {duration_sec}s)...")
        
        while (time.time() - start_t) < duration_sec:
            frame = self.kamera.get_frame()
            is_open = self.goz_ai.analiz_et(frame)
            if is_open:
                open_time += 0.2
            else:
                open_time = 0 # Sıfırlandı
                
            try:
                cv2.imshow("Kamera", frame)
                cv2.waitKey(1)
            except Exception:
                pass
                
            if open_time >= success_threshold_sec:
                log.info("Gözler 3 saniye aralıksız açık kaldı ✓ (Puan: 4)")
                self.kamera.stop() # Cihazı hemen serbest bırak!
                return True
                
            # Ekrana ve nabza can ver
            self.ekran.update_data(
                hr=self.nabiz.heart_rate, 
                spo2=self.nabiz.spo2
            )
            self.ekran.guncelle()
            time.sleep(0.2)
            
        self.kamera.stop()
        log.info("Gözler yeterince uzun açık kalmadı ✗")
        return False

    def ses_test_et(self):
        log.info("Sözel Test Aşaması Başlıyor...")
        self.ses_ai.load_models() # Sadece buradayken RAM doldurur
        
        soru_sayisi = 0
        dogru_cevap_sayisi = 0
        
        # Örnek 2 Soru
        testler = [
            ("YER", SORU_HAVUZU["YER"]["sorular"][0], SORU_HAVUZU["YER"]["dogru_cevaplar"]),
            ("ZAMAN", SORU_HAVUZU["ZAMAN"]["sorular"][0], SORU_HAVUZU["ZAMAN"]["dogru_cevaplar"])
        ]
        
        for name, soru, dogrular in testler:
            self.ses_ai.konus(soru)
            transkript = self.ses_ai.dinle_ve_anla()
            
            puan = 1 # Tepkisiz
            if transkript:
                if self.ses_ai.cevap_uygun_mu(transkript, dogrular):
                    puan = 5 # Oryante (Sadece bu alanda, algoritma basitleştirildi)
                    dogru_cevap_sayisi += 1
                else:
                    puan = 3 # Uygunsuz Kelimeler
                    
            log.info(f"Soru: {soru} | Kayıt: {transkript} | Puan: {puan}")
            soru_sayisi += 1
            
        if dogru_cevap_sayisi == 2: return 5
        elif dogru_cevap_sayisi == 1: return 4
        else: return 3 # Tamamen tepkisiz veya uyumsuz
        
    def motor_test_et(self):
        log.info("Motor Test Aşaması Başlıyor...")
        self.motor_ai.load_model()
        self.kamera.start()
        
        self.ses_ai.konus("Lütfen elinizi kaldırın veya sıkın")
        
        start_t = time.time()
        prev_kpts = None
        has_movement = False
        
        while (time.time() - start_t) < 5.0:
            frame = self.kamera.get_frame()
            kpts = self.motor_ai.pose_tespit_et(frame)
            
            if prev_kpts is not None and kpts is not None:
                if self.motor_ai.hareket_var_mi(prev_kpts, kpts):
                    has_movement = True
                    break
                    
            try:
                cv2.imshow("Kamera", frame)
                cv2.waitKey(1)
            except Exception:
                pass
                
            prev_kpts = kpts
            time.sleep(0.2)
            
        self.kamera.stop()
        
        if has_movement:
            log.info("Motor hareket algılandı ✓ (Puan: 6)")
            return 6
        else:
            log.info("Motor hareket yok ✗")
            return None # Ağrıya geçilecek

    def run_gks_test(self):
        self.skorlar = {"goz": 1, "motor": 1, "sozel": 1}
        self.ekran.update_data(state="PASIF_GOZLEM", g=self.skorlar["goz"], m=self.skorlar["motor"], v=self.skorlar["sozel"])
        self.ekran.guncelle()
        
        # 1. Pasif Gözlem
        log.info("AŞAMA 1: Pasif Gözlem (Spontan)")
        if self.goz_test_et(duration_sec=7, success_threshold_sec=3):
            self.skorlar["goz"] = 4
        else:
            # 2. Sözel Uyaran (Sese Göz açma)
            self.ekran.update_data(state="SOZEL_UYARAN")
            self.ekran.guncelle()
            log.info("AŞAMA 2: Sözel Uyarana Yanıt")
            self.ses_ai.konus("Lütfen gözlerinizi açın")
            
            if self.goz_test_et(duration_sec=5, success_threshold_sec=2):
                self.skorlar["goz"] = 3
            else:
                self.skorlar["goz"] = 1 # Ağrılı uyaranda göz 2 puan, şimdilik 1
                
        # 3. Sözel Bilinç Mülakatı
        self.skorlar["sozel"] = self.ses_test_et()
        
        # 4. Motor Komutlar
        self.ekran.update_data(state="MOTOR_KOMUT")
        self.ekran.guncelle()
        motor_skoru = self.motor_test_et()
        
        if motor_skoru:
            self.skorlar["motor"] = motor_skoru
        else:
            # 5. Ağrılı Uyaran
            self.ekran.update_data(state="AGRILI_UYARAN")
            self.ekran.guncelle()
            log.info("AŞAMA 5: Ağrılı Uyaran (Servo)")
            # Burada normalde Pigpio ile motor 15 derece sallanıp tepki beklenir.
            # Şimdilik monolitik yapı hatası olmasın diye dummy ekliyorum.
            time.sleep(2)
            self.skorlar["motor"] = 1
            
        # 6. Final
        try:
            cv2.destroyAllWindows()
        except:
            pass
        self.ekran.update_data(state="FINAL_RAPOR", g=self.skorlar["goz"], m=self.skorlar["motor"], v=self.skorlar["sozel"])
        self.ekran.guncelle()
        log.info("GKS TESTİ BİTTİ:")
        log.info(self.skorlar)

    def run_continuous(self):
        self.setup()
        
        try:
            while self.is_running:
                log.info("-" * 40)
                log.info("YENİ GKS MUAYENESİ BAŞLATILIYOR")
                
                self.run_gks_test()
                
                log.info("Test tamamlandı. 1 dakika mola veriliyor...")
                # 1 dakika boyunca sadece nabız yansıt
                self.ekran.update_data(state="BEKLENIYOR")
                for _ in range(60):
                    if not self.is_running: break
                    self.ekran.update_data(hr=self.nabiz.heart_rate, spo2=self.nabiz.spo2)
                    self.ekran.guncelle()
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            log.info("Kullanıcı tarafından durduruldu.")
        finally:
            log.info("Donanım temizleniyor...")
            self.nabiz.durdur()
            self.kamera.stop()

if __name__ == "__main__":
    app = GKSSystem()
    app.run_continuous()
