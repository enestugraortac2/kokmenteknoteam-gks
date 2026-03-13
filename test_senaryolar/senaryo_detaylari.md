# NeuroSense GKS — Sistem Test Senaryoları Eğitim Raporu

Bu doküman, GKS (Glasgow Koma Skalası) Otonom Değerlendirme Sistemi'nin doğruluğunu fiziksel donanımlar (Kamera, Mikrofon, Hoparlör, LCD, Servo Motor, Nabız Sensörü) üzerinde kanıtlamak amacıyla hazırlanan **2 adet klinik senaryoyu** detaylandırmaktadır.

Aşağıdaki senaryolar, gerçek bir yoğun bakım veya acil servis ortamındaki hasta profillerine göre kurgulanmış olup, sistemin karar ağacını (State Machine) test etmek üzere tasarlanmıştır.

---

## SENARYO 1: Tam Bilinçli Hasta (Hafif Kafa Travması Gözlemi)
**Klinik Profil:** Hasta acil servise getirilmiş, koopere olabiliyor, bilinci tamamen açık.
**Beklenen Toplam Skor:** **15 / 15 (Normal)**

### Sistem İşleyiş Adımları ve Sensör Simülasyonu:

1. **Aşama 1: Pasif Gözlem (İlk 10 Saniye)**
   - **Klinik Durum:** Hasta etrafına bakınıyor, gözleri spontan olarak açık.
   - **Sistem İçi Karşılığı:** Kameradan alınan `goz_takip` verisinde (EAR değeri) gözler açık tespit edilir.
   - **Karar Algoritması:** Sistem 10 saniye sonunda gözlerin spontan açık olduğuna karar verir (**Göz = 4 Puan**). Sözel uyarana (Aşama 2) gerek kalmadan doğrudan motor komut testine (Aşama 3) geçer.

2. **Aşama 2: Sözel Uyaran**
   - **Atlanır.** (Gözler zaten spontan açık tespit edildiği için bu aşamaya girilmez).

3. **Aşama 3: Motor Komut Testi**
   - **Klinik Durum:** Sistem "Sağ elinizi kaldırın" der. Hasta komutu duyar ve sağ elini kaldırır.
   - **Sistem İçi Karşılığı:** Hoparlörden komut verilir. `motor_takip` kamerası YOLO ile bileğin omuz hizasını geçtiğini tespit eder.
   - **Karar Algoritması:** Komut başarıyla yerine getirildi (**Motor = 6 Puan**).

4. **Ek Aşama: Sözel Değerlendirme (Mülakat)**
   - **Klinik Durum:** Aşama 2 atlandığı için sistem eksik kalan Sözel puanı tamamlamak üzere hastaya sorular sorar ("Şu an neredesiniz?", "Hangi yıldayız?" vb.). Hasta anlamlı (Oryante) yanıtlar verir.
   - **Sistem İçi Karşılığı:** Mikrofon kaydeder, yapay zeka transkripti NLP ile analiz eder ve cevapların tutarlı olduğunu onaylar.
   - **Karar Algoritması:** Tam oryantasyon sağlandı (**Sözel = 5 Puan**).

5. **Aşama 4: Ağrılı Uyaran**
   - **Atlanır.** (Motor=6 ve Göz=4 alındığı için ağrı vermeye gerek yoktur).

6. **Final Raporu & Çıktılar:**
   - LCD Ekran: `E:4 M:6 V:5 | Toplam: 15 (HAFIF / NORMAL) | SpO2: 98, BPM: 75`
   - Hoparlör: "GKS muayenesi tamamlandı. Toplam 15 üzerinden 15. Durum: Hafif/Normal."

---

## SENARYO 2: Ağır Koma Hastası (Travmatik Asfiksi)
**Klinik Profil:** Hasta bilincini tamamen kaybetmiş, dış uyarılara kapalı ve sese yanıt vermiyor.
**Beklenen Toplam Skor:** **5 / 15 (Ağır Koma)**

### Sistem İşleyiş Adımları ve Sensör Simülasyonu:

1. **Aşama 1: Pasif Gözlem (İlk 10 Saniye)**
   - **Klinik Durum:** Hastanın gözleri kapalı, hiçbir spontan hareket yok.
   - **Sistem İçi Karşılığı:** Kameradan alınan `goz_takip` verisinde (EAR değeri) gözler kapalı tespit edilir. Spontan hareket görülmez.
   - **Karar Algoritması:** Gözler açılmadı. Sistem, hastanın sese tepki verip vermediğini anlamak için Aşama 2'ye geçer.

2. **Aşama 2: Sözel Uyaran**
   - **Klinik Durum:** Sistem "Beni duyuyor musunuz, gözlerinizi açın" der. Hastadan hiçbir tepki gelmez, göz kapakları kıpırdamaz.
   - **Sistem İçi Karşılığı:** Hoparlör uyarısı sonrası 5 saniye boyunca göz kamerası dinlenir. `goz_acik=False` kalmaya devam eder. NLP mülakatında sese yanıt alınamaz.
   - **Karar Algoritması:** Sese göz açılmadı, mülakata yanıt verilmedi (**Sözel = 1 Puan**). Sistem, hastanın komutlara veya ağrıya tepkisini ölçmek için doğrudan Aşama 4'e atlar (Göz açılmadığı için motor komut dinlenmez).

3. **Aşama 3: Motor Komut Testi**
   - **Atlanır.** (Hasta sese ve uyanmaya hiçbir tepki vermediği için mantıksal olarak komut alamaz, doğrudan ağrı uyarana geçilir).

4. **Aşama 4: Ağrılı Uyaran**
   - **Klinik Durum:** Hastaya 3 saniye boyunca mekanik ağrılı uyaran uygulanır. Hasta ağrıya uyanmaz (gözler kapalı kalır) ancak kollarında istemsiz, anormal bir katılaşma/bükülme (dekortike duruş) gözlemlenir.
   - **Sistem İçi Karşılığı:** Servo motor 3 saniye süreyle pin üzerinden fiziksel olarak tetiklenir (hastanın etine baskı). Motor kamerası bu esnada kolların <45 derece ile kapanarak anormal fleksiyon yaptığını tespit eder.
   - **Karar Algoritması:** Ağrıyla gözler açılmadı (**Göz = 1 Puan**). Ağrıya anormal fleksiyon yanıtı alındı (**Motor = 3 Puan**).

5. **Final Raporu & Çıktılar:**
   - LCD Ekran: `E:1 M:3 V:1 | Toplam: 5 (AGIR KOMA) | SpO2: 92, BPM: 60`
   - Hoparlör: "GKS muayenesi tamamlandı. Toplam 15 üzerinden 5. Durum: Ağır koma."
