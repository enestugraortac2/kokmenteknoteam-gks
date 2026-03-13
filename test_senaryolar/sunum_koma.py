#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GKS Test Senaryosu: KOMA HASTASI (Skor: 5/15)

    Goz=1  Motor=3  Sozel=1  ->  Toplam=5  AGIR KOMA
    
    SUNUM MODU: Kameralar ve Mikrofon BYPASS edilmiştir. Sistem sadece senaryoya uygun sürede bekler, 
    sesli uyaranları (TTS), fiziksel uyaranı (Servo) gerçekleştirir ve LCD/GUI grafiklerini günceller.
    
    Gercek uzerinde calisanlar (SADECE CIKTILAR):
      - LCD ekran: Gercek (sonuclari gosterir)
      - Servo motor: Gercek (agrili uyaranda 3 saniye fiziksel hareet)
      - Hoparl0r (TTS): Gercek (her adimda konusur)
    
    Simülasyon / Atlanan Adımlar (GIRDILER):
      - Kamera + Goz AI: Calistirilmaz, 5s beklenir Goz=1 devam eder.
      - Kamera + Motor AI: Calistirilmaz, komutta Motor=1, agrida Motor=3 kabul edilir.
      - Mikrofon (STT): Dinleme yapilmaz, "Beni duyuyor musun" a sessizlik varsayilir. Sozel=1.
      - Nabiz sensoru: Simule (60 BPM, 92 SpO2 - dusuk)

Kullanim:
  cd /home/kokmenteknoteam/Desktop/VS_GKS_Proje
  ./v3_gks/venv/bin/python test_senaryolar/senaryo_koma.py
"""

# Terminal encoding fix (ISO-8859-9 -> UTF-8)
import os, sys
os.environ["PYTHONIOENCODING"] = "utf-8"
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import time
import random
import numpy as np

# Proje kokunu path'e ekle
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V3_DIR = os.path.join(ROOT, "v3_gks")
sys.path.insert(0, V3_DIR)

os.environ.setdefault("ORT_LOG_LEVEL", "WARNING")

# LCD ekran sonuc gosterme suresi (saniye)
LCD_SONUC_BEKLEME = 20


def main():
    print("=" * 60)
    print("  GKS SUNUM SENARYOSU: KOMA HASTASI")
    print("  Beklenen Sonuc: Goz=1, Motor=3, Sozel=1 -> 5/15")
    print("  (Kameralar ve mikrofon bypass edilmiştir, sadece sunum akışı)")
    print("=" * 60)
    print()

    # ═══════════════════════════════════════════════════════════
    #  MOCK: Kamera -> bos frame
    # ═══════════════════════════════════════════════════════════
    from gks_modules.camera_manager import CameraManager

    def mock_cam_init(self, **kwargs):
        self._camera_id = kwargs.get("camera_id", 0)
        self._running = False
    def mock_cam_start(self):
        self._running = True
    def mock_cam_stop(self):
        self._running = False
    def mock_cam_frame(self):
        return np.zeros((480, 640, 3), dtype=np.uint8)

    CameraManager.__init__ = mock_cam_init
    CameraManager.start = mock_cam_start
    CameraManager.stop = mock_cam_stop
    CameraManager.get_frame = mock_cam_frame

    # ═══════════════════════════════════════════════════════════
    #  MOCK: GozAnaliz -> gozler HER ZAMAN KAPALI
    # ═══════════════════════════════════════════════════════════
    from gks_modules.goz_analiz import GozAnaliz

    def mock_goz_init(self):
        self.last_goz_acik = False
    def mock_goz_load(self):
        pass
    def mock_goz_analiz(self, frame):
        self.last_goz_acik = False
        return False, 0.08  # goz_acik=False, EAR=0.08 (kapali)

    GozAnaliz.__init__ = mock_goz_init
    GozAnaliz.load_model = mock_goz_load
    GozAnaliz.analiz_et = mock_goz_analiz

    # ═══════════════════════════════════════════════════════════
    #  MOCK: MotorAnaliz -> komuta tepkisiz, agriya fleksiyon
    # ═══════════════════════════════════════════════════════════
    from gks_modules.motor_analiz import MotorAnaliz

    def mock_motor_init(self):
        self.last_score = 1
        self.last_status = "TEPKISIZ"
    def mock_motor_load(self):
        pass
    def mock_motor_pose(self, frame):
        return np.zeros((17, 2), dtype=np.float32)
    def mock_motor_analiz(self, keypoints, komut_aktif=False, servo_aktif=False):
        if servo_aktif:
            # Agriya anormal fleksiyon (dekortike) tepkisi
            return 3, "ANORMAL_FLEKSIYON_DEKORTIKE"
        if komut_aktif:
            # Komutlara hic yanit yok (tam 5s boyunca tepkisiz kalir)
            return 1, "TEPKISIZ"
        return 1, "TEPKISIZ"

    MotorAnaliz.__init__ = mock_motor_init
    MotorAnaliz.load_model = mock_motor_load
    MotorAnaliz.pose_tespit_et = mock_motor_pose
    MotorAnaliz.analiz_et = mock_motor_analiz

    # ═══════════════════════════════════════════════════════════
    #  MOCK: SesAnaliz -> Gercek TTS, Simule STT (sessiz/tepkisiz)
    #  mulakat_yap() MOCKLANMIYOR -> gercek soru sorar (hoparlorden)
    #  Sadece dinle_ve_analiz_et() mocklandi -> sessiz yanitlar
    # ═══════════════════════════════════════════════════════════
    from gks_modules.ses_analiz import SesAnaliz

    def mock_ses_init(self):
        self._whisper_model = None
        self._loaded = False
        self._mik_device = "default"
    def mock_ses_load(self):
        self._loaded = True
        return True
    def mock_ses_dinle(self, kategori="DURUM", sure=5):
        # Koma hastasi: hicbir soruya yanit yok
        print(f"  [SUNUM] Hasta sessiz, tepki yok -> Puan: 1/5")
        time.sleep(2)  # Kayit suresi simule
        return 1, "[Sessiz]"

    SesAnaliz.__init__ = mock_ses_init
    SesAnaliz.load_models = mock_ses_load
    SesAnaliz.dinle_ve_analiz_et = mock_ses_dinle
    # konus() MOCKLANMADI -> gercek hoparlorden konusacak
    # mulakat_yap() MOCKLANMADI -> 3 soru soracak, her birinde konus() cagiracak

    # ═══════════════════════════════════════════════════════════
    #  MOCK: NabizSensoru -> 60 BPM, SpO2 92% (dusuk)
    # ═══════════════════════════════════════════════════════════
    from gks_modules.nabiz_sensoru import NabizSensoru

    def mock_nabiz_init(self):
        self.bpm = random.randint(55, 75)
        self.spo2 = random.randint(88, 94)
        self._running = False
        self._nabiz_thread = None
    def mock_nabiz_baslat(self):
        self._running = True
        import threading
        def _nabiz_loop():
            while self._running:
                self.bpm = random.randint(55, 75)
                self.spo2 = random.randint(88, 94)
                time.sleep(1)
        self._nabiz_thread = threading.Thread(target=_nabiz_loop, daemon=True)
        self._nabiz_thread.start()
        print(f"  [MOCK-NABIZ] Nabiz: {self.bpm} BPM, SpO2: {self.spo2}% (rastgele 55-75)")
    def mock_nabiz_durdur(self):
        self._running = False

    NabizSensoru.__init__ = mock_nabiz_init
    NabizSensoru.baslat = mock_nabiz_baslat
    NabizSensoru.durdur = mock_nabiz_durdur

    # ═══════════════════════════════════════════════════════════
    #  EkranKontrol: GERCEK (LCD ekranda gosterecek)
    #  Servo: GERCEK (agrili uyaranda fiziksel olarak hareket eder)
    # ═══════════════════════════════════════════════════════════

    print("\n[BASLAT] GKS v3 Sistemi baslatiliyor...\n")

    from main import GKSSystem, GKSState

    system = GKSSystem()

    # --- Orijinal run_gks_test'i override et (Sunum Özel Bypass) ---
    def custom_run_gks_test(self):
        """Sunum için kameraları ve modelleri tamamen atlayan optimize senaryo"""
        self.goz_puan = 1
        self.motor_puan = 1
        self.sozel_puan = 1
        self._update_ekran(goz=1, motor=1, sozel=1, tamamlandi=False)

        # Asama 1: Pasif Gozlem (Goze tepki yok)
        self._transition(GKSState.PASIF_GOZLEM)
        print("  [SUNUM] Pasif gözlem yapılıyor...")
        time.sleep(4)
        print("  [SUNUM] Gözler açılmadı -> Goz=1")
        self._update_ekran(goz=self.goz_puan, motor=self.motor_puan, sozel=self.sozel_puan)
        if self._stop_event.is_set(): return

        # Asama 2: Sozel Uyaran
        self._transition(GKSState.SOZEL_UYARAN)
        print("  [SUNUM] Sözel uyaran veriliyor...")
        self.ses_ai.load_models()
        self.ses_ai.konus("Beni duyuyor musunuz? Gözlerinizi açın.")
        time.sleep(2)
        print("  [SUNUM] Sözel yanıta tepki yok -> Sozel=1, Goz=1")
        self._update_ekran(goz=self.goz_puan, motor=self.motor_puan, sozel=self.sozel_puan)
        if self._stop_event.is_set(): return

        # Asama 3: Motor Komut 
        self._transition(GKSState.MOTOR_KOMUT)
        print("  [SUNUM] Motor komut veriliyor...")
        self.ses_ai.konus("Sağ elinizi kaldırın.")
        time.sleep(3)
        print("  [SUNUM] Komutlara uymadı -> Motor=1")
        self._update_ekran(goz=self.goz_puan, motor=self.motor_puan, sozel=self.sozel_puan)
        if self._stop_event.is_set(): return

        # Asama 4: Agrili Uyaran (Fiziksel Servo Calismasi)
        self._transition(GKSState.AGRILI_UYARAN)
        print("  [SUNUM] Agrili uyaran (Servo) tetikleniyor...")
        import threading
        from gks_modules.servo_kontrol import stimulate_servo
        servo_thread = threading.Thread(target=stimulate_servo, args=(3.0,))
        servo_thread.start()
        time.sleep(3.5)
        servo_thread.join(timeout=2)
        
        self.motor_puan = 3
        print("  [SUNUM] Ağrıya anormal fleksiyon -> Motor=3, Goz=1")
        self._update_ekran(goz=self.goz_puan, motor=self.motor_puan, sozel=self.sozel_puan)
        if self._stop_event.is_set(): return

        # Final Rapor
        self._transition(GKSState.FINAL_RAPOR)
        self._final_rapor()
        self._transition(GKSState.TAMAMLANDI)

    # Override
    import types
    system.run_gks_test = types.MethodType(custom_run_gks_test, system)

    # --- GUI Entegrasyonu ---
    from scenario_gui import start_gui

    def _get_system_state():
        durum_str = system.state.name if system.state else "BAŞLANGIÇ"
        return durum_str, system.goz_puan, system.motor_puan, system.sozel_puan

    # --- Senaryo Konfigürasyonu (Kamera overlay'ları için) ---
    scenario_config = {
        "cam0_id": 0,
        "cam1_id": 1,
        "goz_acik": False,
        "ear": 0.08,
        "goz_puan_fn": lambda: system.goz_puan,
        "motor_durum": "ANORMAL_FLEKSIYON_DEKORTIKE",
        "motor_puan_fn": lambda: system.motor_puan,
    }

    root, app = start_gui(title="NeuroSense", 
                          past_history=[3, 4, 3, 5, 5],
                          state_source_fn=_get_system_state,
                          scenario_config=scenario_config)

    def _run_scenario():
        try:
            system.setup()
            system.run_gks_test()

            # LCD ekranda sonuclar gorunsun
            print(f"\n[LCD] Sonuclar ekranda {LCD_SONUC_BEKLEME}s gosteriliyor...")
            for i in range(LCD_SONUC_BEKLEME, 0, -1):
                system._update_ekran(
                    goz=system.goz_puan,
                    motor=system.motor_puan,
                    sozel=system.sozel_puan,
                    tamamlandi=True,
                    nabiz_bpm=system.nabiz.bpm,
                )
                time.sleep(1)
            # --- Rapor Olusturma ---
            from pathlib import Path
            import datetime
            rapor_dir = os.path.join(str(Path(__file__).resolve().parent.parent), "raporlar")
            os.makedirs(rapor_dir, exist_ok=True)
            tarih_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            rapor_dosyasi = os.path.join(rapor_dir, f"GKS_Rapor_{tarih_str}.txt")
            
            toplam = system.goz_puan + system.motor_puan + system.sozel_puan
            klinik_durum = "HAFIF / NORMAL" if toplam >= 13 else "ORTA" if toplam >= 9 else "AGIR KOMA"
            
            with open(rapor_dosyasi, "w", encoding="utf-8") as f:
                f.write("="*50 + "\n")
                f.write(f" NEUROSENSE GKS - HASTA DEGERLENDIRME RAPORU \n")
                f.write("="*50 + "\n")
                f.write(f"Tarih / Saat: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
                f.write(f"Senaryo Tipi: Agir Koma Hastasi\n\n")
                f.write(f"--- SKOR DETAYLARI ---\n")
                f.write(f"Goz Yaniti (E) : {system.goz_puan} / 4\n")
                f.write(f"Motor Yaniti (M): {system.motor_puan} / 6\n")
                f.write(f"Sozel Yaniti (V): {system.sozel_puan} / 5\n\n")
                f.write(f"TOPLAM GKS SKORU: {toplam} / 15\n")
                f.write(f"Klinik Durum: {klinik_durum}\n")
                f.write("="*50 + "\n")
                
            print(f"\n[BILGI] Test sonucu rapor olarak kaydedildi: {rapor_dosyasi}")

        except KeyboardInterrupt:
            print("\n[DURDURULDU] Ctrl+C ile kapatildi")
        except Exception as e:
            print(f"\n[HATA] {e}")
            import traceback
            traceback.print_exc()
        finally:
            system.cleanup()
            # Senaryo bittiginde arayuzu de kapat (opsiyonel)
            # root.quit()

    # Senaryoyu arkaplan thread olarak calistir, ana mainloop UI'i tutsun
    import threading
    scenario_thread = threading.Thread(target=_run_scenario, daemon=False)
    scenario_thread.start()

    # Pencere kapatildiginda senaryoyu da durdur
    def _on_close():
        system._stop_event.set()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", _on_close)

    # UI Ana dongusu (bloklar)
    root.mainloop()

    # GUI kapandiktan sonra thread'in bitmesini bekle
    scenario_thread.join(timeout=5)

    # ─── Sonuclari goster ────────────────────────────────────
    print("\n" + "=" * 60)
    print("  SENARYO SONUCU")
    print("=" * 60)
    print(f"  Goz:    {system.goz_puan}/4")
    print(f"  Motor:  {system.motor_puan}/6")
    print(f"  Sozel:  {system.sozel_puan}/5")
    toplam = system.goz_puan + system.motor_puan + system.sozel_puan
    print(f"  TOPLAM: {toplam}/15")
    print()

    beklenen = 5
    if toplam == beklenen:
        print(f"  [OK] SUNUM BAŞARILI -- Beklenen {beklenen}/15, Alinan {toplam}/15")
    else:
        print(f"  [FAIL] SUNUM BEKLENMEYEN -- Beklenen {beklenen}/15, Alinan {toplam}/15")
    print("=" * 60)

if __name__ == "__main__":
    main()
