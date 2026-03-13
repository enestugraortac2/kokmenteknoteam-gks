#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LCD Ekran Test Scripti
ILI9341 ekranin calisip calismadigini test eder.
"""

import time
import sys

print("LCD Ekran Testi")
print("=" * 40)

try:
    from gks_modules.ekran import EkranKontrol, render_frame, render_splash

    # 1. Render testi (PIL)
    print("[1/3] PIL render testi...")
    data = {
        "goz": 4, "motor": 6, "sozel": 5,
        "durum": "TAMAMLANDI", "ear": 0.28,
        "goz_acik": True, "tamamlandi": True,
        "nabiz_bpm": 72,
    }
    img = render_frame(data)
    assert img.size == (320, 240), f"Boyut hatasi: {img.size}"
    img.save("/tmp/gks_lcd_test.png")
    print(f"  [OK] Frame: {img.size[0]}x{img.size[1]}")
    print(f"  [OK] Kaydedildi: /tmp/gks_lcd_test.png")

    # 2. Splash testi
    print("[2/3] Splash ekrani testi...")
    splash = render_splash()
    assert splash.size == (320, 240)
    splash.save("/tmp/gks_splash_test.png")
    print(f"  [OK] Splash: {splash.size[0]}x{splash.size[1]}")

    # 3. Donanim testi
    print("[3/3] ILI9341 donanim testi...")
    ekran = EkranKontrol()
    result = ekran.baslat()

    if result:
        print("  [OK] LCD ekran calisiyor!")
        print("  Splash -> Dashboard gosteriliyor...")
        ekran.update(**data)
        time.sleep(5)
        ekran.durdur()
        print("  [OK] LCD test tamamlandi")
    else:
        print("  [UYARI] LCD baglanti yok veya hata olustu")
        print("  Kontrol edin:")
        print("    1. SPI aktif mi? -> sudo raspi-config -> Interfaces -> SPI")
        print("    2. Kablo baglantilari: DC=GPIO25, RST=GPIO24")
        print("    3. sudo apt install python3-board python3-adafruit-blinka")
        print("")
        print("  PIL render basarili - LCD olmadan da calisir")

except Exception as e:
    print(f"[HATA] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("Test tamamlandi.")
