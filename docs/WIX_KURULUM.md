# Wix (www.ozgunaydin.net) — Sheets'ten otomatik ürün & kategori senkronu

Amaç: Wix mağazasındaki ürünler, kategoriler (koleksiyonlar), kategori
çevirileri, üst menü ve ana sayfa kategori ızgarası **Google Sheets'ten
otomatik** gelsin; Wix Editor'de tekrar tekrar "grup aç / düzenle" olmasın.

```
Google Sheets (Yansı STOK + Katalog detay/grup)
        │  core/wix_sync.py  (her gün 06:00, Görev Zamanlayıcı: FlipaBit-WixSenkron)
        ├──► Wix Stores: ürün fiyat/ad/stok/görsel/açıklama, koleksiyonlar, üyelikler, çeviriler
        └──► logs/wix_kategori_agaci.json ──► https://toptan.ozgunaydin.com.tr/api/wix/kategoriler
                                                     ▲
Wix sitesi (Velo kodu, wix/ klasörü) ────────────────┘  sayfa açılınca menü + ana sayfa ızgarası
```

## 1. Veri kuralları (Sheets tarafı — tek yapılması gereken bakım)

`Katalog detay` → `grup` sekmesi, her GRUP KODU için **3 katmanlı** yapı:

| Katman | Sütunlar | Anlam |
|---|---|---|
| **ALT** (60, gerçek ürün koleksiyonu) | `ENG-UZUN` + `TR/RU/AR-UZUN` | Ürünün gireceği en alt kategori |
| **ORTA** (15, ör. "Exclusive Bedding Sets") | `UST-GRUP-TR/EN/RU/AR` | **⚠️ BAŞKA BİR PROJEDE de kullanılıyor — SADECE OKUNUR, buraya asla yazma kodu eklenmez.** `N- AD` biçiminde, `N` sıra (`11A` gibi harfli olabilir). |
| **MEGA** (4, sade üst menü: Bedroom/Bathroom/Baby/Others) | `MEGA-GRUP-TR/EN/RU/AR` | FlipaBit'e özel, yeni sütunlar (J:M). Aynı kurallarla `N- AD`. |

- Yeni bir grup kodu eklenince satırın **hem ENG-UZUN'unu hem MEGA-GRUP-EN'ini**
  doldurun → bir sonraki senkronda Wix'te koleksiyon(lar) oluşur, çeviriler
  yazılır, menüye girer.
- **`MEGA-GRUP-EN` boş bırakılırsa** ürün kaybolmaz — otomatik olarak **"Yeni
  Eklenen Kategoriler / NEW CATEGORIES"** adında bir toplama ana grubuna
  düşer (menünün en sonunda), satılabilir ve görünür kalır. Siz `MEGA-GRUP-*`
  sütununu doldurunca bir sonraki senkronda doğru ana gruba taşınır.
- Bir mega grubun altında **tek bir orta grup** varsa (şu an Baby, Others) o
  ara katman otomatik atlanır, alt kategoriler doğrudan mega'ya bağlanır —
  gereksiz tek satırlık katman açılmaz.
- Aynı `ENG-UZUN` birden çok kodda olabilir (hepsi aynı koleksiyona düşer);
  ama **aynı alt kategori iki farklı orta/mega grupta olamaz**, aynı şekilde
  bir orta grup iki farklı mega'da olamaz — senkron raporunda `uyarilar`
  altında listelenir, ilki kullanılır.
- Bir dilin çevirisi boşsa o dilde İngilizce ad görünür.
- Sitede olmayan diller (2026-09-29: RU, AR yok; EN birincil + TR var)
  `eksik_dil` olarak raporlanır; Wix'e o dil eklenince otomatik yazılmaya başlar.

## 2. Senkron komutları

```bash
python -m core.wix_sync                       # KURU ÇALIŞMA: hiçbir şey yazmaz, planı raporlar
python -m core.wix_sync --uygula --azami 20    # canlı deneme: en fazla 20 yazma
python -m core.wix_sync --uygula               # tam senkron
python -m core.wix_sync --uygula --sadece-kategori   # sadece koleksiyon/üyelik/çeviri, ürün adımları atlanır
```
Plan/rapor: `logs/wix_sync_plan.json` (özet + 3 katmanlı ağaç önizlemesi), ağaç: `logs/wix_kategori_agaci.json`.

Ne yapar (bkz. `core/wix_sync.py` ve `core/wix_kategori.py` docstring'leri):
ürünleri SKU=barkod ile eşler; stok ≥ 10 ve fiyat > 0 olanları günceller/oluşturur,
diğerlerini gizler (silmez); her ürünü alt + (varsa) orta + mega koleksiyonuna
ekler, eski koleksiyondan çıkarır; yapıda olmayan koleksiyonları gizler;
satılabilir ürünü kalmayan kategoriyi gizler, ürün gelince açar; kategori
çevirilerini yazar.

## 3. Wix Editor — BİR KEZ yapılan kurulum (2026-09-29'da tamamlandı)

1. Wix Editor → **Dev Mode** (Velo) açık.
2. **Public** klasöründe `kategoriler.js` — içeriği `wix/public/kategoriler.js`
   ile aynı tutulur (3 katmanı okur, dil öneki eklemez — bkz. dosyadaki not).
3. **masterPage.js** içeriği `wix/masterPage.js` ile aynı tutulur. Gerçek
   element ID'leri (Editor'de doğrulandı): kategori çubuğu **`#horizontalMenu5`**,
   mobil menü **`#expandableMenu1`**. Masaüstü menü 3 katmanlı (mega → orta
   varsa → alt) nested `menuItems` kurar; mobil menü sadece 4 mega grubu
   gösterir (liste kısa kalsın diye, alt kategoriler kategori sayfasında).
4. **Ana sayfa** koduna `wix/Home.js` — henüz repeater kurulmadıysa: ana
   sayfaya bir **Repeater** ekleyip öğe içindeki elemanlara şu id'leri verin:
   `#kategoriRepeater`, `#kategoriKutu` (öğe kapsayıcısı), `#kategoriResim`
   (Image), `#kategoriAd` (Text), `#kategoriSayi` (Text). *(Not: bu dosya
   hâlâ eski 2 katmanlı `ustler[]` şeklini okuyor — mega listesini doğru
   gösterir ama orta/alt'a inmez; sorun çıkarsa `wix/public/kategoriler.js`'in
   güncel `kategoriListesi()` çıktısına göre elden geçirilmeli.)*
5. Her kod değişikliğinden sonra **Kaydet + Yayınla** gerekir.

Sunucuya ulaşılamazsa: menü Editor'deki haliyle kalır, ızgara gizlenir
(site bozulmaz).

## 4. Zamanlanmış görev

`scripts/gorev_zamanlayici_kur.bat` (yönetici olarak, bir kez) → `FlipaBit-WixSenkron`:
**her gün 06:00'da** `python -m core.wix_sync --uygula` (kullanıcı kararı 2026-09-29:
günde bir yeterli, 15 dk değil). Bilgisayar o saatte kapalıysa açıldığında
çalışır. Log: `logs/wix_sync.log`. Ara güncelleme gerekirse elle:
`python -m core.wix_sync --uygula`.

## 5. Bilinen kısıtlar

- Kategori sayfaları (`/category/<slug>`) sitede genel olarak şifre korumalı
  ("Misafir Alanı") — kullanıcı kararı, kaldırılmadı (2026-09-29).
- Wix menü etiketi (label) en fazla 40 karakter — uzun kategori adları
  menüde "…" ile kısaltılır (`masterPage.js`'teki `kisaltEtiket`), tam ad
  ana sayfa ızgarasında ve kategori sayfasında değişmeden kalır.
