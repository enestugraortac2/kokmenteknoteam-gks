#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GKS Test Senaryosu: BILINCLI HASTA (Skor: 15/15)

    Goz=4  Motor=6  Sozel=4  ->  Toplam=14  KONFUZE
    
    SUNUM MODU: Kameralar ve Mikrofon BYPASS edilmiştir. Sistem sadece senaryoya uygun sürede bekler, 
    sesli uyaranları gerçekleştirir (TTS) ve LCD/GUI grafiklerini günceller.
    
    TUM ASAMALAR SIRAYLA ÇALIŞIR:
      Asama 1: Pasif Gozlem (3s bekleme -> Goz=4)
      Asama 2: Sozel Uyaran (Soru sorulur, mikrofonsuz direkt Sozel=4 kabul edilir - Konfüze)
      Asama 3: Motor Komut (Komut verilir, kamerasız direkt Motor=6 kabul edilir)
      Asama 4: Agrili Uyaran (Gerekmez, atlanır)
      Final Rapor (sesli duyuru, 14 puan)

Gercek donanim:
  - LCD ekran, Servo motor, Hoparlor (TTS)
Simule:
  - Kamera, Goz AI, Motor AI, Mikrofon (STT), Nabiz

Kullanim:
  cd /home/kokmenteknoteam/Desktop/VS_GKS_Proje
  /bin/python test_senaryolar/senaryo_bilincli.py
"""

# Terminal encoding fix
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V3_DIR = os.path.join(ROOT, "v3_gks")
sys.path.insert(0, V3_DIR)

os.environ.setdefault("ORT_LOG_LEVEL", "WARNING")

LCD_SONUC_BEKLEME = 20


def main():
    print("=" * 60)
    print("  GKS SUNUM SENARYOSU: BİLİNÇLİ HASTA")
    print("  Hedef: Goz=4, Motor=6, Sozel=4 -> 14/15 (Oryante Değil/Konfüze)")
    print("  (Kameralar ve mikrofon bypass edilmiştir, sadece sunum akışı)")
    print("=" * 60)
    print()

    # ═══════════════════════════════════════════════════════════
    #  MOCK: Kamera
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
    #  MOCK: GozAnaliz -> gozler HER ZAMAN ACIK
    # ═══════════════════════════════════════════════════════════
    from gks_modules.goz_analiz import GozAnaliz

    def mock_goz_init(self):
        self.last_goz_acik = True
    def mock_goz_load(self):
        pass
    def mock_goz_analiz(self, frame):
        self.last_goz_acik = True
        return True, 0.32

    GozAnaliz.__init__ = mock_goz_init
    GozAnaliz.load_model = mock_goz_load
    GozAnaliz.analiz_et = mock_goz_analiz

    # ═══════════════════════════════════════════════════════════
    #  MOCK: MotorAnaliz -> 3s sonra komuta uyar
    # ═══════════════════════════════════════════════════════════
    from gks_modules.motor_analiz import MotorAnaliz

    _motor_komut_baslangic = [None]

    def mock_motor_init(self):
        self.last_score = 1
        self.last_status = "BEKLENIYOR"
    def mock_motor_load(self):
        pass
    def mock_motor_pose(self, frame):
        return np.zeros((17, 2), dtype=np.float32)
    def mock_motor_analiz(self, keypoints, komut_aktif=False, servo_aktif=False):
        if komut_aktif:
            if _motor_komut_baslangic[0] is None:
                _motor_komut_baslangic[0] = time.time()
            gecen = time.time() - _motor_komut_baslangic[0]
            if gecen >= 3.0:
                return 6, "KOMUTLARA_UYUYOR"
            else:
                return 4, "SPONTAN_HAREKET"
        if servo_aktif:
            # Agriya lokalizasyon (bilinclide normal tepki)
            return 5, "LOKALIZASYON"
        return 4, "SPONTAN_HAREKET"

    MotorAnaliz.__init__ = mock_motor_init
    MotorAnaliz.load_model = mock_motor_load
    MotorAnaliz.pose_tespit_et = mock_motor_pose
    MotorAnaliz.analiz_et = mock_motor_analiz

    # ═══════════════════════════════════════════════════════════
    #  MOCK: SesAnaliz -> Gercek TTS, Simule STT (oryante)
    # ═══════════════════════════════════════════════════════════
    from gks_modules.ses_analiz import SesAnaliz

    _soru_sayaci = [0]
    _oryante_yanitlar = [
        (4, "bilmiyorum"),
        (4, "hatırlamıyorum"),
        (4, "burası neresi"),
    ]

    def mock_ses_init(self):
        self._whisper_model = None
        self._loaded = False
        self._mik_device = "default"
    def mock_ses_load(self):
        self._loaded = True
        return True
    def mock_ses_dinle(self, kategori="DURUM", sure=5):
        idx = min(_soru_sayaci[0], len(_oryante_yanitlar) - 1)
        _soru_sayaci[0] += 1
        puan, metin = _oryante_yanitlar[idx]
        print(f"  [SUNUM] Hasta (Simüle Edilen) Yanıt: '{metin}' -> Puan: {puan}/5")
        time.sleep(1.5) # Gerçekçi bir bekleme süresi
        return puan, metin

    SesAnaliz.__init__ = mock_ses_init
    SesAnaliz.load_models = mock_ses_load
    SesAnaliz.dinle_ve_analiz_et = mock_ses_dinle

    # ═══════════════════════════════════════════════════════════
    #  MOCK: NabizSensoru -> rastgele 70-85 BPM
    # ═══════════════════════════════════════════════════════════
    from gks_modules.nabiz_sensoru import NabizSensoru

    def mock_nabiz_init(self):
        self.bpm = random.randint(70, 85)
        self.spo2 = random.randint(96, 99)
        self._running = False
        self._nabiz_thread = None
    def mock_nabiz_baslat(self):
        self._running = True
        import threading
        def _nabiz_loop():
            while self._running:
                self.bpm = random.randint(70, 85)
                self.spo2 = random.randint(96, 99)
                time.sleep(1)
        self._nabiz_thread = threading.Thread(target=_nabiz_loop, daemon=True)
        self._nabiz_thread.start()
        print(f"  [MOCK-NABIZ] Nabiz: {self.bpm} BPM, SpO2: {self.spo2}% (rastgele 70-85)")
    def mock_nabiz_durdur(self):
        self._running = False

    NabizSensoru.__init__ = mock_nabiz_init
    NabizSensoru.baslat = mock_nabiz_baslat
    NabizSensoru.durdur = mock_nabiz_durdur

    # ═══════════════════════════════════════════════════════════
    #  GKS Sistemini calistir — TUM ASAMALARI ZORLA
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

        # Asama 1: Pasif Gozlem (Hızlıca Goz=4)
        self._transition(GKSState.PASIF_GOZLEM)
        print("  [SUNUM] Pasif gözlem yapılıyor...")
        time.sleep(2)
        self.goz_puan = 4
        print("  [SUNUM] Gözler kendiliğinden açık -> Goz=4")
        self._update_ekran(goz=self.goz_puan, motor=self.motor_puan, sozel=self.sozel_puan)
        if self._stop_event.is_set(): return

        # Asama 2: Sozel Uyaran + Mulakat
        self._transition(GKSState.SOZEL_UYARAN)
        print("  [SUNUM] Sözel uyaran veriliyor...")
        self.ses_ai.load_models()
        self.ses_ai.konus("Beni duyuyor musunuz? Gözlerinizi açın.")
        time.sleep(2)
        print("  [SUNUM] Oryantasyon mülakatı başlıyor...")
        self.sozel_puan = self.ses_ai.mulakat_yap()
        self._update_ekran(goz=self.goz_puan, motor=self.motor_puan, sozel=self.sozel_puan)
        if self._stop_event.is_set(): return

        # Asama 3: Motor Komut (Hızlıca Motor=6)
        self._transition(GKSState.MOTOR_KOMUT)
        print("  [SUNUM] Motor komut veriliyor...")
        self.ses_ai.konus("Sağ elinizi kaldırın.")
        time.sleep(3)
        self.motor_puan = 6
        print("  [SUNUM] Komutlara uydu -> Motor=6")
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
        "goz_acik": True,
        "ear": 0.32,
        "goz_puan_fn": lambda: system.goz_puan,
        "motor_durum": "KOMUTLARA_UYUYOR",
        "motor_puan_fn": lambda: system.motor_puan,
    }

    root, app = start_gui(title="NeuroSense", 
                          past_history=[4, 7, 10, 13, 14],
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
                f.write(f"Senaryo Tipi: Bilincli / Konfuze Hasta\n\n")
                f.write(f"--- SKOR DETAYLARI ---\n")
                f.write(f"Goz Yaniti (E) : {system.goz_puan} / 4\n")
                f.write(f"Motor Yaniti (M): {system.motor_puan} / 6\n")
                f.write(f"Sozel Yaniti (V): {system.sozel_puan} / 5\n\n")
                f.write(f"TOPLAM GKS SKORU: {toplam} / 15\n")
                f.write(f"Klinik Durum: {klinik_durum}\n")
                f.write("="*50 + "\n")
                
            print(f"\n[BILGI] Test sonucu rapor olarak kaydedildi: {rapor_dosyasi}")

            # Test bitince eger hasta bilincliyse (Toplam >= 10) asistan panelini otomatik ac
            toplam = system.goz_puan + system.motor_puan + system.sozel_puan
            if toplam >= 10:
                print("\n[BILGI] Hasta bilinci acik algilandi. Hasta Ihtiyac Asistani otomatik baslatiliyor...")
                app.ihtiyac_panel.mock_mode = True  # Sunum simulasyonu icin gercek mikrofonu atla
                app.root.after(1000, lambda: app.ihtiyac_panel.toggle())
                
                print("[BILGI] Asistan paneli devrede. Kapatmak icin Ctrl+C basiniz.")
                while True:
                    time.sleep(1)

        except KeyboardInterrupt:
            print("\n[DURDURULDU] Ctrl+C ile kapatildi")
        except Exception as e:
            print(f"\n[HATA] {e}")
            import traceback
            traceback.print_exc()
        finally:
            system.cleanup()
            # Senaryo bittiginde arayuzu de kapat (opsiyonel, istersen yoruma al)
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

    beklenen = 14
    if toplam == beklenen:
        print(f"  [OK] SUNUM BAŞARILI -- Beklenen {beklenen}/15, Alinan {toplam}/15")
    else:
        print(f"  [FAIL] SUNUM BEKLENMEYEN -- Beklenen {beklenen}/15, Alinan {toplam}/15")
    print("=" * 60)


if __name__ == "__main__":
    main()
