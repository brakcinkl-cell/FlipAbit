"""Katalog detay 'grup' sekmesi -> Wix Stores kategori (koleksiyon) yapısı.

Kullanıcının 2026-09-29 isteği: kategoriler Wix Editor'de elle değil,
SİSTEMDEN otomatik gelsin; yeni grup Sheets'in 'grup' sekmesine eklenince
Wix'te de oluşsun; sitenin ana dili İngilizce (ENG-UZUN / UST-GRUP-EN),
diğer diller (TR/RU/AR) aynı sekmedeki çeviri sütunlarından alınsın.

'grup' sekmesi sütunları:
  GRUP KODU | TR-UZUN | ENG-UZUN | RU-UZUN | AR-UZUN
            | UST-GRUP-TR | UST-GRUP-EN | UST-GRUP-RU | UST-GRUP-AR
- Her GRUP KODU bir ALT kategoriye (ENG-UZUN) ve bir ÜST gruba (UST-GRUP-EN)
  bağlıdır. Birden çok kod aynı alt kategoriye düşebilir (1300/1310/1320
  "PIKE TAKIMI ...").
- Üst grup adları "1- DUVET COVER SETS ..." gibi sıra numarasıyla başlar;
  numara SIRALAMA için kullanılır, addan ayıklanır ("11A" gibi harfli de olur).

Wix tarafı (Katalog V1 + Multilingual Translation Content):
- Alt ve üst kategorilerin her biri BİR Wix koleksiyonudur; eşleşme anahtarı
  koleksiyonun İngilizce adı (birincil dil). Yoksa oluşturulur.
- Ürün hem alt hem üst koleksiyona eklenir; bizim yönettiğimiz başka
  koleksiyonlardan çıkarılır (Sheets'te grup değişirse Wix'te de taşınır).
- Yapıda olmayan (eski) koleksiyonlar SİLİNMEZ, gizlenir. Satılabilir ürünü
  kalmayan kategori de gizlenir, ürün gelince yeniden görünür.
- Çeviriler: Wix Stores'un GLOBAL 'collection' çeviri şeması, alan
  'collection-name'; site dillerinden sadece mevcut olanlar yazılır
  (2026-09-29: en birincil + tr; ru/ar sitede henüz yok - raporlanır).
- Sonuçta logs/wix_kategori_agaci.json yazılır: Wix sitesindeki Velo kodu
  menüyü ve ana sayfa kategori ızgarasını bu ağaçtan (uygulama sunucusunun
  /api/wix/kategoriler ucundan) doldurur.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from core import catalog_source as cs

ROOT = Path(__file__).resolve().parent.parent
AGAC_PATH = ROOT / "logs" / "wix_kategori_agaci.json"

# Wix Stores'un site-bağımsız (GLOBAL) koleksiyon çeviri şeması - 2026-09-29'da
# /translation-schema/v1/schemas/site ile doğrulandı (appId 1380b703-..., entityType 'collection').
CEVIRI_SEMA_ID = "5b35dfe1-da21-4071-aab5-2cec870459c0"
CEVIRI_ALAN = "collection-name"
BIRINCIL_DIL = "en"
ALT_AD_SUTUNU = {"en": "ENG-UZUN", "tr": "TR-UZUN", "ru": "RU-UZUN", "ar": "AR-UZUN"}
UST_AD_SUTUNU = {"en": "UST-GRUP-EN", "tr": "UST-GRUP-TR", "ru": "UST-GRUP-RU", "ar": "UST-GRUP-AR"}
YONETILMEYEN_KOLEKSIYONLAR = {"All Products"}

# "1- DUVET ...", "11A - TOWEL ...", "10-BATH ...", "1. КОМПЛЕКТЫ", "١٠ أطقم", "11أ - مجموعة"
_SIRA_RE = re.compile(r"^\s*([0-9٠-٩]+)\s*([A-Za-zء-ي])?\s*[-.–]?\s*")
_ARAP_RAKAM = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _sira_ayir(ad: str) -> tuple[str | None, str]:
    """'11A - TOWEL COLLECTION' -> ('11A', 'TOWEL COLLECTION'); sıra yoksa (None, ad)."""
    m = _SIRA_RE.match(ad or "")
    if not m:
        return None, (ad or "").strip()
    sayi = m.group(1).translate(_ARAP_RAKAM)
    ek = (m.group(2) or "").upper()
    # Arapça harf ekini ('أ') Latin karşılığına indirgemeye çalışmıyoruz; sıra
    # anahtarı olarak İngilizce sütun esas alınır, diğer diller sadece ad verir.
    return sayi + (ek if ek.isascii() else ""), (ad or "")[m.end():].strip()


def _sira_anahtari(sira: str | None) -> tuple[int, str]:
    if not sira:
        return (9999, "")
    m = re.match(r"(\d+)([A-Z]?)", sira)
    return (int(m.group(1)), m.group(2)) if m else (9999, sira)


@dataclass
class Kategori:
    anahtar: str                      # İngilizce ad = Wix koleksiyon adı (eşleşme anahtarı)
    adlar: dict[str, str]             # dil -> ad (sıra numarası ayıklanmış)
    ust: bool
    sira: str | None = None           # üst gruplar için "1", "11A"; altlar üstünün sırasını taşır
    ust_anahtar: str | None = None    # altlar için bağlı olduğu üst grubun anahtarı
    grup_kodlari: set[str] = field(default_factory=set)
    uyarilar: list[str] = field(default_factory=list)


@dataclass
class Yapi:
    ustler: list[Kategori]                 # sıralı
    altlar: list[Kategori]                 # üst sırası + ad sırası
    kod_alt: dict[str, str]                # GRUP KODU -> alt anahtar
    uyarilar: list[str]

    @property
    def tum(self) -> list[Kategori]:
        return self.ustler + self.altlar

    def hedef_koleksiyonlar(self, grup_kodu: str | None) -> list[str]:
        """Bir ürünün girmesi gereken koleksiyon adları (alt + üst)."""
        alt = self.kod_alt.get(grup_kodu or "")
        if not alt:
            return []
        ust = next((a.ust_anahtar for a in self.altlar if a.anahtar == alt), None)
        return [alt] + ([ust] if ust else [])


def _temiz(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def yapi_oku(rows: list[dict] | None = None) -> Yapi:
    rows = rows if rows is not None else cs._fetch_csv_rows(cs.GRUP_URL)
    ustler: dict[str, Kategori] = {}
    altlar: dict[str, Kategori] = {}
    kod_alt: dict[str, str] = {}
    tr_alt: dict[str, str] = {}   # TR-UZUN -> alt anahtar (kullanıcı kararı 2026-09-29: TR adı aynıysa tek grup)
    uyarilar: list[str] = []

    for row in rows:
        kod = _temiz(row.get("GRUP KODU"))
        if not kod:
            continue
        alt_en = _temiz(row.get(ALT_AD_SUTUNU["en"]))
        alt_tr = _temiz(row.get(ALT_AD_SUTUNU["tr"]))
        ust_en_ham = _temiz(row.get(UST_AD_SUTUNU["en"]))
        if not alt_en:
            uyarilar.append(f"{kod}: ENG-UZUN boş, atlandı")
            continue
        if alt_tr and alt_tr in tr_alt and tr_alt[alt_tr] != alt_en:
            # Aynı Türkçe ad, farklı İngilizce ad: ürünler ilk görülen koleksiyonda toplanır,
            # ikinci bir alt grup OLUŞTURULMAZ.
            uyarilar.append(f"{kod}: TR-UZUN '{alt_tr}' zaten '{tr_alt[alt_tr]}' altında, '{alt_en}' ayrı açılmadı")
            alt_en = tr_alt[alt_tr]
        elif alt_tr:
            tr_alt.setdefault(alt_tr, alt_en)
        ust_sira, ust_en = _sira_ayir(ust_en_ham)
        if not ust_en:
            uyarilar.append(f"{kod}: UST-GRUP-EN boş, alt kategori üst grupsuz kalır")

        if ust_en:
            u = ustler.get(ust_en)
            if u is None:
                adlar = {}
                for dil, sutun in UST_AD_SUTUNU.items():
                    _, ad = _sira_ayir(_temiz(row.get(sutun)))
                    if ad:
                        adlar[dil] = ad
                u = Kategori(anahtar=ust_en, adlar=adlar, ust=True, sira=ust_sira)
                if ust_sira is None:
                    u.uyarilar.append("sıra numarası yok (UST-GRUP-EN 'N-' ile başlamalı)")
                ustler[ust_en] = u
            u.grup_kodlari.add(kod)

        a = altlar.get(alt_en)
        if a is None:
            adlar = {dil: _temiz(row.get(sutun)) for dil, sutun in ALT_AD_SUTUNU.items() if _temiz(row.get(sutun))}
            a = Kategori(anahtar=alt_en, adlar=adlar, ust=False, ust_anahtar=ust_en or None,
                         sira=ust_sira)
            altlar[alt_en] = a
        elif ust_en and a.ust_anahtar and a.ust_anahtar != ust_en:
            uyarilar.append(f"{kod}: '{alt_en}' iki farklı üst grupta ({a.ust_anahtar} / {ust_en}), ilki kullanıldı")
        a.grup_kodlari.add(kod)
        kod_alt[kod] = alt_en

    ust_sirali = sorted(ustler.values(), key=lambda k: _sira_anahtari(k.sira))
    ust_konum = {u.anahtar: i for i, u in enumerate(ust_sirali)}
    alt_sirali = sorted(altlar.values(), key=lambda a: (ust_konum.get(a.ust_anahtar or "", 9999), a.anahtar))
    return Yapi(ustler=ust_sirali, altlar=alt_sirali, kod_alt=kod_alt, uyarilar=uyarilar)


# ------------------------------------------------------------ kategori ağacı JSON
def agac_yaz(yapi: Yapi, koleksiyon: dict[str, dict], urun_sayisi: dict[str, int],
             gorsel: dict[str, str | None], diller: list[str], path: Path = AGAC_PATH) -> dict:
    """Velo'nun okuyacağı ağacı yazar. `koleksiyon`: ad -> Wix kaydı (id/slug),
    `urun_sayisi`/`gorsel`: ad -> satılabilir ürün sayısı / kapak görseli."""
    def dugum(k: Kategori) -> dict:
        w = koleksiyon.get(k.anahtar) or {}
        return {
            "ad": {d: k.adlar.get(d) or k.adlar.get(BIRINCIL_DIL) for d in diller},
            "slug": w.get("slug"),
            "urun": urun_sayisi.get(k.anahtar, 0),
            "gorsel": gorsel.get(k.anahtar),
        }
    agac = {
        "guncelleme": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "diller": diller,
        "birincil_dil": BIRINCIL_DIL,
        "ustler": [
            {**dugum(u), "sira": u.sira,
             "altlar": [dugum(a) for a in yapi.altlar if a.ust_anahtar == u.anahtar and urun_sayisi.get(a.anahtar, 0) > 0]}
            for u in yapi.ustler if urun_sayisi.get(u.anahtar, 0) > 0
        ],
    }
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(agac, ensure_ascii=False, indent=1), encoding="utf-8")
    return agac


def agac_oku(path: Path = AGAC_PATH) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
