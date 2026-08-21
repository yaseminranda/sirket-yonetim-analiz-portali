# Şirket Yönetim & Analiz Portalı

Araç kiralama ve gayrimenkul (ev/daire) kiralama işlerini tek bir sistemden yöneten, ödeme takibi, müşteri yönetimi, analiz panelleri ve yapay zekâ destekli tahmin araçları içeren bir şirket içi web uygulaması.

## İçindekiler

- [Genel Bakış](#genel-bakış)
- [Klasör Yapısı](#klasör-yapısı)
- [Kullanılan Teknolojiler](#kullanılan-teknolojiler)
- [Kurulum ve Çalıştırma](#kurulum-ve-çalıştırma)
- [Ortam Değişkenleri (.env)](#ortam-değişkenleri-env)
- [Özellikler / Modüller](#özellikler--modüller)
- [Veritabanı Şeması](#veritabanı-şeması)
- [Testler](#testler)

## Genel Bakış

Uygulama iki ayrı Docker servisinden oluşur:

- **backend** — FastAPI ile yazılmış REST API. Tüm iş mantığı (kiralama, ödeme, kimlik doğrulama, raporlama) burada çalışır ve PostgreSQL (Supabase) veritabanına bağlanır.
- **frontend** — Streamlit ile yazılmış çok sayfalı web arayüzü. Kullanıcı burada giriş yapar, sözleşme oluşturur, ödeme alır, raporları görüntüler. Kendi başına veritabanına bağlanmaz, her şeyi backend API'si üzerinden yapar.

## Klasör Yapısı

```
proje_final_tum_asamalar/
├── backend/
│   ├── main.py                 # FastAPI uygulama girişi, scheduler (otomatik hatırlatma) kurulumu
│   ├── config.py                # .env'den okunan ayarlar (Settings)
│   ├── database.py              # SQLAlchemy engine, run_query / execute_query yardımcıları
│   ├── dependencies.py          # JWT doğrulama, yetki/departman kontrolü
│   ├── auth_utils.py            # Şifre hash'leme, JWT oluşturma/okuma
│   ├── schemas.py               # Pydantic request/response modelleri
│   ├── routers/                 # API uç noktaları (auth, vehicles, housing, finance, contracts, ...)
│   ├── services/                 # İş mantığı (her routers/ dosyasının arkasındaki gerçek işlemler)
│   ├── scripts/                  # Tek seferlik yardımcı scriptler (Excel veri aktarımı vb.)
│   └── tests/                    # pytest ile yazılmış birim testleri (bkz. Testler bölümü)
└── frontend/
    ├── Home.py                   # Giriş/2FA, oturum (remember-me) yönetimi, sayfa yönlendirme
    ├── api_client.py             # Backend API'sine istek atan yardımcı fonksiyonlar
    ├── validators.py             # Form doğrulama yardımcıları (telefon, e-posta, TC formatı vb.)
    ├── config.py                 # Backend API adresi (BACKEND_URL)
    └── app_pages/                # Streamlit sayfaları (kiralama, ödeme, analiz, müşteri, vb.)
```

## Kullanılan Teknolojiler

**Backend:** FastAPI, SQLAlchemy, PostgreSQL (Supabase), Pydantic, python-jose (JWT), bcrypt, APScheduler (otomatik günlük hatırlatmalar), scikit-learn (tahmin modelleri), pandas, openpyxl, python-docx.

**Frontend:** Streamlit, requests, pandas, plotly, extra-streamlit-components (çerez/cookie yönetimi — "Beni Hatırla" özelliği için).

**Altyapı:** Docker + Docker Compose (iki ayrı konteyner: backend ve frontend).

## Kurulum ve Çalıştırma

1. Proje kök dizininde bir `.env` dosyası oluştur (bkz. [Ortam Değişkenleri](#ortam-değişkenleri-env)).
2. Aşağıdaki komutla her iki servisi de build edip başlat:

   ```
   docker-compose up --build -d
   ```

3. Backend varsayılan olarak `http://localhost:8000`, frontend ise `http://localhost:8501` üzerinden erişilebilir olur.
4. Kod tarafında değişiklik yaptıktan sonra (bu proje volume mount kullanmadığından) her zaman `--build` ile yeniden başlatmak gerekir.

## Ortam Değişkenleri (.env)

Backend (`backend/config.py` üzerinden okunur):

| Değişken | Zorunlu mu | Açıklama |
|---|---|---|
| `DATABASE_URL` | Evet | PostgreSQL bağlantı adresi (Supabase connection string) |
| `SECRET_KEY` | Evet | JWT imzalama anahtarı |
| `ALGORITHM` | Hayır (vars. `HS256`) | JWT algoritması |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Hayır (vars. `120`) | Normal oturum süresi (dakika) |
| `REMEMBER_ME_EXPIRE_DAYS` | Hayır (vars. `30`) | "Beni Hatırla" ile açılan oturumun süresi (gün) |
| `MAX_FAILED_ATTEMPTS` | Hayır (vars. `5`) | Hesabın kilitlenmesi için gereken art arda hatalı giriş sayısı |
| `LOCK_DURATION_MINUTES` | Hayır (vars. `15`) | Hesap kilit süresi (dakika) |
| `SMTP_HOST` / `SMTP_PORT` | Hayır (vars. Gmail) | E-posta gönderimi için SMTP sunucusu |
| `SMTP_USER` / `SMTP_PASSWORD` | E-posta göndermek için gerekli | SMTP hesap bilgileri |
| `LOGIN_VERIFICATION_CODE_EXPIRE_MINUTES` | Hayır (vars. `5`) | 2FA / doğrulama kodlarının geçerlilik süresi |
| `SMS_API_URL` / `SMS_API_KEY` / `SMS_SENDER_ID` | Hayır | Gerçek bir SMS sağlayıcısı bağlanana kadar boş kalabilir |

Frontend (`frontend/config.py` üzerinden okunur):

| Değişken | Zorunlu mu | Açıklama |
|---|---|---|
| `BACKEND_URL` | Hayır (vars. `http://localhost:8000`) | Docker Compose içinde genelde `http://backend:8000` olarak ayarlanır |

## Özellikler / Modüller

- **Giriş & Güvenlik** — Kullanıcı adı/şifre + e-posta ile gönderilen tek kullanımlık kod (2FA), "Beni Hatırla" (çerez tabanlı kalıcı oturum), hatalı girişte hesap kilitleme, güvenlik sorusu ile şifre sıfırlama (e-posta koduyla onaylı).
- **Araç Kiralama** — Filo yönetimi, sözleşme oluşturma, sözleşme uzatma, araç değişimi, ek şoför ekleme.
- **Ev/Daire Kiralama** — Portföy yönetimi, sözleşme oluşturma/uzatma, tahliye onayı, depozito iade/kesinti işlemleri, taksitli ödeme planı.
- **Ödeme Yönetimi** — Kısmi ödeme, döviz cinsinden ödeme (TL karşılığı otomatik hesaplanır), iptal/iade kayıtları, borç takibi.
- **Ödeme Hatırlatmaları** — Vadesi yaklaşan/geçen ödemeler için manuel veya her gün otomatik (zamanlanmış görev ile) e-posta/SMS hatırlatması.
- **Analiz Panelleri** — Araç ve ev için ayrı ayrı: günlük gelir dağılımı, doluluk oranı, ödeme yöntemi/döviz dağılımı.
- **Genel Karşılaştırma** — Aylık ciro/net kâr karşılaştırması, departman bazlı gider takibi.
- **Yapay Zekâ & Tahmin** — İptal riski tahmini ve yatırım önerisi (scikit-learn tabanlı modeller).
- **Müşteri Yönetimi** — Müşteri arama, düzenleme, sözleşme geçmişi görüntüleme.

## Veritabanı Şeması

> Not: Bu proje için doğrudan bir veritabanı bağlantım olmadığından, aşağıdaki şema doğrudan Supabase'den değil, backend kodundaki SQL sorgularından (routers/services) çıkarılmıştır. Sütun tipleri çoğunlukla kodun kullanım şekline bakılarak **tahmin edilmiştir** — kesin tip/kısıt bilgisi için Supabase Table Editor'ü referans al. `giris_dogrulama_kodlari` ve `islem_loglari` tabloları kodda `CREATE TABLE IF NOT EXISTS` ile tanımlandığı için bu ikisinin tipleri kesindir.

### calisan (çalışanlar)
Sisteme giriş yapan personel; departman ve yetki seviyesine göre hangi sayfaları/verileri görebileceği belirlenir.

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `calisan_id` | text (PK) | Çalışan kimliği (örn. "E01" gibi kısa kod) |
| `ad_soyad` | text | Ad soyad |
| `departman_id` | text | Departman kodu (D1 = Ev/Kiralama, D2 = Araç, D3 = Genel Müdürlük vb.) |
| `yetki` | text | Rol/yetki seviyesi (GENEL MÜDÜR, YÖNETİCİ, ÇALIŞAN vb.) |
| `sifre_hash` | text | bcrypt ile hash'lenmiş şifre |
| `email` | text | Bildirim/doğrulama kodu maillerinin gönderildiği adres |
| `aylik_maas` | numeric | Aylık maaş (gider karşılaştırma raporlarında kullanılır) |
| `hatali_giris_sayisi` | integer | Art arda hatalı giriş sayacı |
| `kilitli_mi` | boolean | Hesap kilitli mi |
| `kilit_bitis_zamani` | timestamp, null olabilir | Kilit ne zaman kalkacak |
| `guvenlik_sorusu` | text, null olabilir | Şifre sıfırlama için güvenlik sorusu |
| `guvenlik_cevabi_hash` | text, null olabilir | Güvenlik sorusu cevabının hash'i |

### giris_dogrulama_kodlari (2FA / doğrulama kodları)
Giriş, güvenlik sorusu değişikliği ve şifre sıfırlama akışlarının hepsinde ortak kullanılan, e-posta ile gönderilen tek kullanımlık kod tablosu. *(Kod içinde `CREATE TABLE IF NOT EXISTS` ile tanımlı, tipler kesin.)*

| Sütun | Tip | Açıklama |
|---|---|---|
| `id` | SERIAL (PK) | Otomatik artan kimlik |
| `calisan_id` | VARCHAR(50) NOT NULL | Kodun ait olduğu çalışan |
| `kod` | VARCHAR(10) NOT NULL | 6 haneli doğrulama kodu |
| `olusturulma_zamani` | TIMESTAMP (vars. şimdi) | Oluşturulma zamanı |
| `son_kullanma_zamani` | TIMESTAMP NOT NULL | Son geçerlilik zamanı |
| `kullanildi` | BOOLEAN (vars. FALSE) | Kod daha önce kullanıldı mı |

### giris_loglari (giriş logları)
Her giriş denemesinin (başarılı/başarısız) kaydedildiği log tablosu.

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `log_id` | integer (PK) | Kayıt kimliği |
| `calisan_id` | text | Deneme yapan çalışan (bulunamazsa "UNKNOWN") |
| `departman_id` | text | Departman kodu |
| `basarili_mi` | boolean | Giriş başarılı oldu mu |
| `hata_nedeni` | text | Hata nedeni / not (ör. "Başarılı Giriş", hesap kilidi notu) |
| `tarih` | timestamp | Deneme zamanı |

### islem_loglari (işlem logları)
Genel Müdür seviyesindeki kullanıcıların güvenlik/işlem geçmişi ekranında gösterilen genel işlem kayıtları. *(Kod içinde `CREATE TABLE IF NOT EXISTS` ile tanımlı, tipler kesin.)*

| Sütun | Tip | Açıklama |
|---|---|---|
| `log_id` | SERIAL (PK) | Otomatik artan kimlik |
| `calisan_id` | VARCHAR(50) | İşlemi yapan çalışan |
| `departman_id` | VARCHAR(10) | Departman kodu |
| `islem_tipi` | VARCHAR(100) | İşlem türü etiketi |
| `detay` | TEXT | Serbest metin açıklama |
| `tarih` | TIMESTAMP (vars. şimdi) | İşlem zamanı |

### musteriler (müşteriler)
Araç veya ev kiralayan müşterilerin ana kaydı.

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `musteri_id` | integer (PK) | Müşteri kimliği |
| `isim` | text | Ad soyad |
| `telefon` | text | Telefon numarası |
| `email` | text | E-posta adresi |
| `tc_kimlik_no` | text | TC kimlik / pasaport numarası |
| `kayit_tarihi` | date | Kayıt tarihi |

### soforler (ek şoförler)
Bir araç sözleşmesine ek olarak tanımlanabilen (asıl müşteri dışındaki) şoförler.

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `sofor_id` | integer (PK) | Şoför kimliği |
| `ad_soyad` | text | Ad soyad |
| `telefon` | text | Telefon numarası |
| `email` | text | E-posta adresi |
| `tc_kimlik_no` | text | TC kimlik / pasaport numarası |

### araba_markalari (araç markaları)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `marka_id` | text (PK) | Marka kimliği (örn. "M1") |
| `marka_adi` | text | Marka adı |

### araba_modelleri (araç modelleri)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `model_id` | text (PK) | Model kimliği (örn. "MO1") |
| `marka_id` | text | → `araba_markalari.marka_id` |
| `model_adi` | text | Model adı |

### arabalar (araç filosu)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `arac_id` | text (PK) | Araç kimliği (örn. "A1") |
| `model_id` | text | → `araba_modelleri.model_id` |
| `plaka` | text | Plaka (büyük harfle saklanır) |
| `gunluk_ucret` | numeric | Günlük kiralama ücreti |
| `musaitlik_durumu` | text | "Müsait" / "Kirada" / "Pasif" |
| `sisteme_ekleme_tarihi` | date | Filoya eklenme tarihi |
| `pasif_tarihi` | date, null olabilir | Pasife alınma tarihi (NULL = hâlâ aktif) |

### araba_kiralama_sozlesmeleri (araç kiralama sözleşmeleri)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `sozlesme_no` | text (PK) | Sözleşme no (örn. "AKS-0001") |
| `musteri_id` | integer | → `musteriler.musteri_id` |
| `arac_id` | text | → `arabalar.arac_id` |
| `islemi_yapan_calisan_id` | text | → `calisan.calisan_id` |
| `baslangic_tarihi` / `bitis_tarihi` | date | Sözleşme başlangıç/bitiş tarihi |
| `total_kira` | numeric | Toplam sözleşme tutarı |
| `odenen_toplam_tutar` | numeric | Toplam ödenen tutar |
| `kalan_borc` | numeric | Kalan borç |
| `sozlesme_durumu` | text | BEKLEMEDE / DEVAM EDİYOR / TAMAMLANDI / İPTAL EDİLDİ |
| `odeme_durumu` | text | ÖDENDİ / KISMİ ÖDENDİ / ÖDENMEDİ |
| `bitis_hatirlatma_gonderildi` | boolean (vars. FALSE) | Bitiş hatırlatma maili gönderildi mi |
| `onceki_sozlesme_no` | text, null olabilir | Araç değişimi ile oluşmuşsa → önceki sözleşmenin no'su (kendine referans) |
| `degisim_nedeni` | text, null olabilir | Araç değişim nedeni |

### arac_sozlesme_soforler (sözleşme-şoför bağlantı tablosu)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `sozlesme_no` | text | → `araba_kiralama_sozlesmeleri.sozlesme_no` |
| `sofor_id` | integer | → `soforler.sofor_id` |
| `sira` | integer | Sözleşmedeki şoför sırası (1 veya 2) |

### apartmanlar (binalar)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `apartman_id` | text (PK) | Bina kimliği (örn. "AP1") |
| `apartman_adi` | text | Bina adı |
| `il` / `ilce` | text | İl / ilçe |

### daireler (kiralanabilir daireler)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `daire_id` | text (PK) | Daire kimliği (örn. "D12") |
| `apartman_id` | text | → `apartmanlar.apartman_id` |
| `daire_no` | text | Daire numarası/etiketi |
| `oda_sayisi` | text | Oda sayısı (örn. "2+1") |
| `aylik_kira` | numeric | Aylık kira tutarı |
| `musaitlik_durumu` | text | "Müsait" / "Kirada" / "Pasif" |
| `sisteme_ekleme_tarihi` | date | Portföye eklenme tarihi |
| `pasif_tarihi` | date, null olabilir | Pasife alınma tarihi |

### ev_kiralama_sozlesmeleri (ev/daire kiralama sözleşmeleri)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `sozlesme_no` | text (PK) | Sözleşme no (örn. "KS-0001") |
| `musteri_id` | integer | → `musteriler.musteri_id` |
| `daire_id` | text | → `daireler.daire_id` |
| `islemi_yapan_calisan_id` | text | → `calisan.calisan_id` |
| `baslangic_tarihi` / `bitis_tarihi` | date | Sözleşme başlangıç/bitiş tarihi |
| `aylik_kira_yrd` | numeric | Sözleşmeye kilitlenen aylık kira |
| `depozito` | numeric | Depozito tutarı |
| `total_kira` | numeric | Toplam sözleşme tutarı |
| `odenen_toplam_tutar` | numeric | Toplam ödenen tutar |
| `kalan_borc` | numeric | Kalan borç |
| `sozlesme_durumu` | text | BEKLEMEDE / DEVAM EDİYOR / TAMAMLANDI / İPTAL EDİLDİ |
| `odeme_durumu` | text | ÖDENMEDİ / KISMİ ÖDENDİ / ÖDENDİ |
| `tahliye_onayi` | boolean | Kiracı tahliyeyi onayladı mı |
| `bitis_hatirlatma_gonderildi` | boolean (vars. FALSE) | Bitiş hatırlatma maili gönderildi mi |
| `hasar_kesintisi` | numeric | Depozitodan yapılan hasar kesintisi |
| `iade_edilen_depozito` | numeric | İade edilen depozito tutarı |

### ev_odeme_plani (taksitli ödeme planı)

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `id` | integer (PK) | Kayıt kimliği |
| `sozlesme_no` | text | → `ev_kiralama_sozlesmeleri.sozlesme_no` |
| `taksit_no` | integer | Taksit sıra numarası |
| `planlanan_tarih` | date | Taksidin planlanan vade tarihi |
| `planlanan_tutar` | numeric | Planlanan taksit tutarı |
| `odenen_tutar` | numeric | Bu taksit için o ana kadar ödenen tutar |
| `durum` | text | BEKLİYOR / PARÇALI ÖDENDİ / ÖDENDİ |

### odemeler (ödemeler)
Hem araç hem ev sözleşmeleri için ortak ödeme/iade defteri.

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `odeme_id` | integer (PK) | Ödeme kimliği |
| `sozlesme_no` | text | İlgili sözleşme no (araç veya ev) |
| `kategori` | text | "ARAC" veya "EV" |
| `musteri_id` | integer | → `musteriler.musteri_id` |
| `odenen_tutar` | numeric | TL karşılığı ödeme tutarı (iade/iptal için negatif olabilir) |
| `odeme_tipi` | text | KİRA_ODEMESI / DEPOZITO_TAHSILATI / DEPOZITO_IADE / HASAR_KESINTISI / İPTAL_İADESİ vb. |
| `aciklama` | text | Serbest metin açıklama |
| `doviz_cinsi` | text | TRY / USD / EUR / GBP |
| `odenen_tutar_doviz` | numeric | Orijinal döviz cinsinden tutar |
| `kur` | numeric | Uygulanan kur |
| `odeme_yontemi` | text | Ödeme yöntemi (ör. "NAKİT") |
| `taksit_id` | integer, null olabilir | İlişkili taksit (varsa) |
| `odeme_tarihi` | date | Ödeme tarihi |

### doviz_kurlari (döviz kuru önbelleği)
TCMB'den çekilen günlük kurların yerel önbelleği.

| Sütun | Tip (tahmini) | Açıklama |
|---|---|---|
| `tarih` | date | Kur tarihi (benzersiz anahtarın parçası) |
| `doviz_cinsi` | text | USD / EUR / GBP (benzersiz anahtarın parçası) |
| `alis` | numeric | Alış kuru |
| `satis` | numeric | Satış kuru (dönüşümlerde kullanılan asıl kur) |
| `efektif_satis` | numeric | Efektif (banknot) satış kuru |

### İlişki Özeti (Foreign Key'ler)

```
calisan ── (islemi_yapan_calisan_id) ──> araba_kiralama_sozlesmeleri / ev_kiralama_sozlesmeleri
calisan ── (calisan_id) ──> giris_loglari, giris_dogrulama_kodlari, islem_loglari

araba_markalari ──> araba_modelleri ──> arabalar ──> araba_kiralama_sozlesmeleri
musteriler ──> araba_kiralama_sozlesmeleri, ev_kiralama_sozlesmeleri, odemeler

araba_kiralama_sozlesmeleri ──> arac_sozlesme_soforler <── soforler
araba_kiralama_sozlesmeleri.onceki_sozlesme_no ──> araba_kiralama_sozlesmeleri.sozlesme_no  (araç değişimi, kendine referans)

apartmanlar ──> daireler ──> ev_kiralama_sozlesmeleri ──> ev_odeme_plani

odemeler.sozlesme_no ──> araba_kiralama_sozlesmeleri.sozlesme_no  VEYA  ev_kiralama_sozlesmeleri.sozlesme_no  (kategori sütununa göre)
```

## Testler

`backend/tests/` altında pytest ile yazılmış birim testleri var (şifre hash'leme, JWT, sözleşme uzatma kuralları, taksit planı hesaplama, döviz kuru çekme, fatura üretimi gibi konularda toplam ~26 test). Bu testler **hiçbir otomatik süreçle (CI, docker-compose) tetiklenmiyor** — çalıştırmak için backend konteynerine girip elle çalıştırman gerekir:

```
docker-compose exec backend pytest
```
