# Wix (www.ozgunaydin.net) — Sheets'ten otomatik ürün & kategori senkronu

Amaç: Wix mağazasındaki ürünler, kategoriler (koleksiyonlar), kategori
çevirileri, üst menü ve ana sayfa kategori ızgarası **Google Sheets'ten
otomatik** gelsin; Wix Editor'de tekrar tekrar "grup aç / düzenle" olmasın.

```
Google Sheets (Yansı STOK + Katalog detay/grup)
        │  core/wix_sync.py  (15 dk'da bir, Görev Zamanlayıcı)
        ├──► Wix Stores: ürün fiyat/ad/stok/görsel/açıklama, koleksiyonlar, üyelikler, çeviriler
        └──► logs/wix_kategori_agaci.json ──► https://toptan.ozgunaydin.com.tr/api/wix/kategoriler
                                                     ▲
Wix sitesi (Velo kodu, wix/ klasörü) ────────────────┘  sayfa açılınca menü + ana sayfa ızgarası
```

## 1. Veri kuralları (Sheets tarafı — tek yapılması gereken bakım)

`Katalog detay` → `grup` sekmesi, her GRUP KODU için:

| Sütun | Anlam |
|---|---|
| `ENG-UZUN` | **Alt kategori adı (sitenin ana dili, Wix koleksiyon adı)** |
| `TR-UZUN`, `RU-UZUN`, `AR-UZUN` | Alt kategorinin çevirileri |
| `UST-GRUP-EN` | **Üst grup** — `N- AD` biçiminde, `N` menü sırası (`11A` gibi harfli olabilir) |
| `UST-GRUP-TR/RU/AR` | Üst grubun çevirileri |

- Yeni bir grup kodu eklenince satırı bu sekmeye ekleyin → bir sonraki
  senkronda Wix'te koleksiyon(lar) oluşur, çeviriler yazılır, menüye girer.
- Aynı `ENG-UZUN` birden çok kodda olabilir (hepsi aynı koleksiyona düşer);
  ama **aynı alt kategori iki farklı üst grupta olamaz** — senkron raporunda
  `uyarilar` altında listelenir, ilki kullanılır.
- Bir dilin çevirisi boşsa o dilde İngilizce ad görünür.
- Sitede olmayan diller (2026-09-29: RU, AR yok; EN birincil + TR var)
  `eksik_dil` olarak raporlanır; Wix'e o dil eklenince otomatik yazılmaya başlar.

## 2. Senkron komutları

```bash
python -m core.wix_sync                 # KURU ÇALIŞMA: hiçbir şey yazmaz, planı raporlar
python -m core.wix_sync --uygula --azami 20   # canlı deneme: en fazla 20 yazma
python -m core.wix_sync --uygula        # tam senkron
```
Plan/rapor: `logs/wix_sync_plan.json` (özet + ağaç önizlemesi), ağaç: `logs/wix_kategori_agaci.json`.

Ne yapar (bkz. `core/wix_sync.py` ve `core/wix_kategori.py` docstring'leri):
ürünleri SKU=barkod ile eşler; stok ≥ 10 ve fiyat > 0 olanları günceller/oluşturur,
diğerlerini gizler (silmez); her ürünü hem alt hem üst koleksiyona ekler, eski
koleksiyondan çıkarır; yapıda olmayan koleksiyonları gizler; satılabilir ürünü
kalmayan kategoriyi gizler, ürün gelince açar; kategori çevirilerini yazar.

## 3. Wix Editor — BİR KEZ yapılacak kurulum (sonra dokunulmaz)

1. Wix Editor → **Dev Mode** (Velo) aç.
2. **Public** klasörüne `kategoriler.js` ekle, içeriği `wix/public/kategoriler.js`.
3. **masterPage.js** içeriğini `wix/masterPage.js` ile değiştir. Editor'de
   kategori çubuğu menüsünü ve mobil menüyü seçip id'lerini
   (`#kategoriMenu`, `#mobileMenu`) koddaki sabitlere yaz.
   - Kategori çubuğu Editor'de boş/tek maddeli kalabilir, içerik koddan gelir.
   - Mobil menüdeki sabit maddeler (Main page, Contact, Store) korunur,
     kategoriler arkasına eklenir.
4. **Ana sayfa** koduna `wix/Home.js` yapıştır. Ana sayfaya bir **Repeater**
   ekle (ızgara düzeni, mobilde 2 sütun) ve öğe içindeki elemanlara şu id'leri
   ver: `#kategoriRepeater`, `#kategoriKutu` (öğe kapsayıcısı), `#kategoriResim`
   (Image), `#kategoriAd` (Text), `#kategoriSayi` (Text).
5. **Publish**. Ondan sonra menü/ızgara içeriği her sayfa açılışında
   `https://toptan.ozgunaydin.com.tr/api/wix/kategoriler` adresinden gelir.

Sunucuya ulaşılamazsa: menü Editor'deki haliyle kalır, ızgara gizlenir
(site bozulmaz).

## 4. Zamanlanmış görev

`scripts/gorev_zamanlayici_kur.ps1` → `FlipaBit-WixSenkron`: 15 dakikada bir
`python -m core.wix_sync --uygula`. Log: `logs/wix_sync.log`.
