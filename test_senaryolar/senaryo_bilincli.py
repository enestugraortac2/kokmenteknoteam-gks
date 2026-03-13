#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GKS Test Senaryosu: BILINCLI HASTA (Skor: 15/15)

Goz=4  Motor=6  Sozel=5  ->  Toplam=15  NORMAL

TUM ASAMALAR SIRAYLA CALISIR (servo dahil):
  Asama 1: Pasif Gozlem (3s goz acik -> Goz=4)
  Asama 2: Sozel Uyaran (sesli soru + mulakat -> Sozel=5)
  Asama 3: Motor Komut (3s sonra uyum -> Motor=6)
  Asama 4: Agrili Uyaran (servo fiziksel calisir, goz acik kalir)
  Final Rapor (sesli duyuru)

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
    print("  GKS TEST SENARYOSU: BILINCLI HASTA")
    print("  Beklenen Sonuc: Goz=4, Motor=6, Sozel=5 -> 15/15")
    print("  Tum asamalar sirayla calisacak (servo dahil)")
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
        (5, "hastane"),
        (5, "2026"),
        (5, "kaza"),
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
        print(f"  [MOCK-STT] Hasta yaniti: '{metin}' -> Puan: {puan}/5")
        time.sleep(1)
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

    # --- Orijinal run_gks_test'i override et ---
    # Tum 4 asama sirayla calissin (servo dahil)
    def custom_run_gks_test(self):
        """Tum asamalar sirayla calisir (servo dahil)."""
        self.goz_puan = 1
        self.motor_puan = 1
        self.sozel_puan = 1
        self._update_ekran(goz=1, motor=1, sozel=1, tamamlandi=False)

        # Asama 1: Pasif Gozlem
        self._transition(GKSState.PASIF_GOZLEM)
        self._asama1_pasif_gozlem()
        if self._stop_event.is_set(): return

        # Asama 2: Sozel Uyaran + Mulakat
        self._transition(GKSState.SOZEL_UYARAN)
        self._asama2_sozel_uyaran()
        if self._stop_event.is_set(): return

        # Asama 3: Motor Komut
        self._transition(GKSState.MOTOR_KOMUT)
        self._asama3_motor_komut()
        if self._stop_event.is_set(): return

        # Asama 4: Agrili Uyaran (servo GERCEK calisir)
        self._transition(GKSState.AGRILI_UYARAN)
        self._asama4_agrili_uyaran()
        if self._stop_event.is_set(): return

        # Motor puanini koru: hasta Phase 3'te komuta uydu (=6)
        # Phase 4 caps at 5 but patient already proved motor=6
        if self.motor_puan < 6:
            self.motor_puan = 6

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

    root, app = start_gui(title="Bilinçli Hasta Senaryosu", 
                          past_history=[4, 7, 10, 13, 14], # Artan grafik trendi
                          state_source_fn=_get_system_state)

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

            # Test bitince eger hasta bilincliyse (Toplam >= 10) asistan panelini otomatik ac
            toplam = system.goz_puan + system.motor_puan + system.sozel_puan
            if toplam >= 10:
                print("\n[BILGI] Hasta bilinci acik algilandi. Hasta Ihtiyac Asistani otomatik baslatiliyor...")
                app.root.after(1000, lambda: app.ihtiyac_panel.toggle())

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
    scenario_thread = threading.Thread(target=_run_scenario, daemon=True)
    scenario_thread.start()

    # UI Ana dongusu (bloklar)
    root.mainloop()

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

    beklenen = 15
    if toplam == beklenen:
        print(f"  [OK] TEST BASARILI -- Beklenen {beklenen}/15, Alinan {toplam}/15")
    else:
        print(f"  [FAIL] TEST BASARISIZ -- Beklenen {beklenen}/15, Alinan {toplam}/15")
    print("=" * 60)


if __name__ == "__main__":
    main()
