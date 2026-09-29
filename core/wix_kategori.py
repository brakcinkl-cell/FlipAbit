"""Katalog detay 'grup' sekmesi -> Wix Stores kategori (koleksiyon) yapısı,
ÜÇ KATMANLI: MEGA (ana menü, 4 sade grup) -> ORTA (eski 15 grup, "Exclusive
Bedding Set" gibi) -> ALT (60 kategori, gerçek ürün koleksiyonları).

Kullanıcının 2026-09-29 istekleri:
- Kategoriler Wix Editor'de elle değil, SİSTEMDEN otomatik gelsin.
- Masaüstü menü çok kalabalık olmasın -> sade 4 ana grup (Bedroom/Bathroom/
  Baby/Others), ama altında "eskisi gibi" ayrıntılı kırılım (Exclusive/
  Ranforce/Pique/... altında Tek/Çift Kişilik) kaybolmasın -> 3. katman.
- ÖNEMLİ DÜZELTME (aynı gün): UST-GRUP-* sütunları BAŞKA BİR PROJEDE de
  kullanılıyor - onlara ASLA yazılmaz/üzerine yazılmaz, sadece OKUNUR. Yeni
  mega-grup sınıflandırması tamamen AYRI sütunlara (MEGA-GRUP-*) yazılır.

'grup' sekmesi sütunları:
  GRUP KODU | TR-UZUN | ENG-UZUN | RU-UZUN | AR-UZUN                (ALT - dokunulmaz, çok eski)
            | UST-GRUP-TR/EN/RU/AR                                  (ORTA - başka projede de kullanılıyor, SADECE OKU)
            | MEGA-GRUP-TR/EN/RU/AR                                 (MEGA - FlipaBit/Wix'e özel, 2026-09-29'da eklendi)
- Her GRUP KODU bir ALT kategoriye (ENG-UZUN), bir ORTA gruba (UST-GRUP-EN)
  ve bir MEGA gruba (MEGA-GRUP-EN) bağlıdır.
- Grup adları "1- DUVET COVER SETS ..." gibi sıra numarasıyla başlar; numara
  SIRALAMA için kullanılır, addan ayıklanır ("11A" gibi harfli de olur).
- Bir MEGA grubun altında yalnız TEK bir ORTA grubu varsa (ör. Baby, Others)
  o orta katman ATLANIR - alt kategoriler doğrudan mega'ya bağlanır (çift
  isimli tek satırlık gereksiz katman oluşmaz). Birden çok orta varsa
  (Bedroom: 9, Bathroom: 4) üç katman tam kurulur.

Wix tarafı (Katalog V1 + Multilingual Translation Content):
- Her katmandaki her kategori BİR Wix koleksiyonudur; eşleşme anahtarı
  koleksiyonun İngilizce adı (birincil dil). Yoksa oluşturulur.
- Ürün ALT + (varsa) ORTA + MEGA koleksiyonlarının hepsine eklenir; bizim
  yönettiğimiz başka koleksiyonlardan çıkarılır (Sheets'te grup değişirse
  Wix'te de taşınır; orta katman bir mega'da tekilleşip atlanırsa o orta
  koleksiyon otomatik gizlenir - eski/artık koleksiyon muamelesi görür).
- Yapıda olmayan (eski) koleksiyonlar SİLİNMEZ, gizlenir. Satılabilir ürünü
  kalmayan kategori de gizlenir, ürün gelince yeniden görünür.
- Çeviriler: Wix Stores'un GLOBAL 'collection' çeviri şeması, alan
  'collection-name'; site dillerinden sadece mevcut olanlar yazılır
  (2026-09-29: en birincil + tr; ru/ar sitede henüz yok - raporlanır).
- Sonuçta logs/wix_kategori_agaci.json yazılır: Wix sitesindeki Velo kodu
  menüyü ve ana sayfa kategori ızgarasını bu ağaçtan (uygulama sunucusunun
  /api/wix/kategoriler ucundan) doldurur - 3 katmanlı (ustler[].ortalar[].altlar[]),
  orta atlanan mega'larda altlar doğrudan mega düğümünde durur.
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
# UST-GRUP-* = ORTA katman. BAŞKA PROJE de kullanıyor - SADECE OKUNUR, hiçbir
# senaryoda buraya yazma kodu eklenmeyecek (core/wix_sync.py da yazmaz).
ORTA_AD_SUTUNU = {"en": "UST-GRUP-EN", "tr": "UST-GRUP-TR", "ru": "UST-GRUP-RU", "ar": "UST-GRUP-AR"}
# MEGA-GRUP-* = MEGA katman. Yalnızca FlipaBit/Wix senkronu kullanır, 2026-09-29'da
# core/wix_kategori.py dışında elle bakım gerektirmez (script ile bir kez dolduruldu).
MEGA_AD_SUTUNU = {"en": "MEGA-GRUP-EN", "tr": "MEGA-GRUP-TR", "ru": "MEGA-GRUP-RU", "ar": "MEGA-GRUP-AR"}
YONETILMEYEN_KOLEKSIYONLAR = {"All Products"}

# Kullanıcı kararı 2026-09-29: MEGA-GRUP-* boş bırakılmış (henüz sınıflandırılmamış)
# bir satır Wix'te GÖRÜNMEZ kalmasın - kullanıcı düzeltene kadar bu "toplama
# kutusu" ana grubun altında görünsün. Gerçek bir sınıflandırma değil, geçici;
# kullanıcı MEGA-GRUP-* sütununu doldurunca ürün normal ana gruba taşınır.
FALLBACK_MEGA_EN = "NEW CATEGORIES"
FALLBACK_MEGA_SIRA = "99"
FALLBACK_MEGA_ADLAR = {
    "en": "NEW CATEGORIES", "tr": "Yeni Eklenen Kategoriler",
    "ru": "НОВЫЕ КАТЕГОРИИ", "ar": "فئات جديدة",
}

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


def _sira_sayi_harf(sira: str | None) -> tuple[int, str]:
    if not sira:
        return (9999, "")
    m = re.match(r"(\d+)([A-Z]?)", sira)
    return (int(m.group(1)), m.group(2)) if m else (9999, sira)


@dataclass
class Kategori:
    anahtar: str                      # İngilizce ad = Wix koleksiyon adı (eşleşme anahtarı)
    adlar: dict[str, str]             # dil -> ad (sıra numarası ayıklanmış)
    sira: str | None = None           # kendi katmanı içindeki sırası ("1", "11A")
    ust_anahtar: str | None = None    # bağlı olduğu ÜST DÜĞÜMÜN anahtarı (mega/orta -> None; orta -> mega; alt -> orta/mega)
    grup_kodlari: set[str] = field(default_factory=set)
    uyarilar: list[str] = field(default_factory=list)


@dataclass
class Yapi:
    megalar: list[Kategori]                # sıralı, en üst katman (4)
    ortalar: list[Kategori]                # sıralı, orta katman (yalnız >1 orta'lı mega'larda var)
    altlar: list[Kategori]                 # sıralı, gerçek ürün koleksiyonları (60)
    kod_alt: dict[str, str]                # GRUP KODU -> alt anahtar
    uyarilar: list[str]

    @property
    def tum(self) -> list[Kategori]:
        return self.megalar + self.ortalar + self.altlar

    def hedef_koleksiyonlar(self, grup_kodu: str | None) -> list[str]:
        """Bir ürünün girmesi gereken koleksiyon adları: alt -> (varsa orta) -> mega."""
        alt = self.kod_alt.get(grup_kodu or "")
        if not alt:
            return []
        zincir = [alt]
        cur = self._ust_anahtar.get(alt)
        gorulen = {alt}
        while cur and cur not in gorulen:
            zincir.append(cur)
            gorulen.add(cur)
            cur = self._ust_anahtar.get(cur)
        return zincir

    def __post_init__(self):
        self._ust_anahtar = {k.anahtar: k.ust_anahtar for k in self.tum}


def _temiz(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _adlari_oku(row: dict, sutunlar: dict[str, str]) -> tuple[str | None, str, dict[str, str]]:
    """(sira, İngilizce_ad_ayıklanmış, {dil: ad_ayıklanmış})."""
    en_sira, en_ad = _sira_ayir(_temiz(row.get(sutunlar["en"])))
    adlar: dict[str, str] = {}
    for dil, sutun in sutunlar.items():
        _, ad = _sira_ayir(_temiz(row.get(sutun)))
        if ad:
            adlar[dil] = ad
    return en_sira, en_ad, adlar


def yapi_oku(rows: list[dict] | None = None) -> Yapi:
    rows = rows if rows is not None else cs._fetch_csv_rows(cs.GRUP_URL)
    megalar: dict[str, Kategori] = {}
    ortalar: dict[str, Kategori] = {}
    altlar: dict[str, Kategori] = {}
    kod_alt: dict[str, str] = {}
    tr_alt: dict[str, str] = {}   # TR-UZUN -> alt anahtar (kullanıcı kararı 2026-09-29: TR adı aynıysa tek grup)
    # sıralama için: her orta/alt'ın hangi mega/orta'ya bağlı olduğunu KOLLAPS
    # ÖNCESİ haliyle de tutuyoruz (kollaps sadece Wix koleksiyon yönetimini
    # etkiler, sıralamayı değil).
    orta_mega: dict[str, str] = {}   # orta anahtarı -> mega anahtarı (her zaman, kollaps olsa da)
    alt_orta: dict[str, str] = {}    # alt anahtarı -> orta anahtarı (her zaman)
    uyarilar: list[str] = []

    for row in rows:
        kod = _temiz(row.get("GRUP KODU"))
        if not kod:
            continue

        alt_en = _temiz(row.get(ALT_AD_SUTUNU["en"]))
        alt_tr = _temiz(row.get(ALT_AD_SUTUNU["tr"]))
        if not alt_en:
            uyarilar.append(f"{kod}: ENG-UZUN boş, atlandı")
            continue
        if alt_tr and alt_tr in tr_alt and tr_alt[alt_tr] != alt_en:
            uyarilar.append(f"{kod}: TR-UZUN '{alt_tr}' zaten '{tr_alt[alt_tr]}' altında, '{alt_en}' ayrı açılmadı")
            alt_en = tr_alt[alt_tr]
        elif alt_tr:
            tr_alt.setdefault(alt_tr, alt_en)

        orta_sira, orta_en, orta_adlar = _adlari_oku(row, ORTA_AD_SUTUNU)
        mega_sira, mega_en, mega_adlar = _adlari_oku(row, MEGA_AD_SUTUNU)
        if not orta_en:
            uyarilar.append(f"{kod}: UST-GRUP-EN boş, orta grupsuz kalır")
        if not mega_en:
            # Sınıflandırılmamış bırakılmasın - kullanıcı MEGA-GRUP-* dolduruncaya
            # kadar "Yeni Eklenen Kategoriler" toplama kutusuna düşer, Wix'te
            # görünür ve satılabilir kalır (bkz. FALLBACK_MEGA_EN yorumu).
            uyarilar.append(f"{kod}: MEGA-GRUP-EN boş, 'Yeni Eklenen Kategoriler' altına kondu")
            mega_sira, mega_en, mega_adlar = FALLBACK_MEGA_SIRA, FALLBACK_MEGA_EN, FALLBACK_MEGA_ADLAR

        if mega_en:
            m = megalar.get(mega_en)
            if m is None:
                m = Kategori(anahtar=mega_en, adlar=mega_adlar, sira=mega_sira)
                megalar[mega_en] = m
            m.grup_kodlari.add(kod)

        if orta_en:
            o = ortalar.get(orta_en)
            if o is None:
                o = Kategori(anahtar=orta_en, adlar=orta_adlar, sira=orta_sira, ust_anahtar=mega_en or None)
                ortalar[orta_en] = o
            elif mega_en and o.ust_anahtar and o.ust_anahtar != mega_en:
                uyarilar.append(f"{kod}: orta '{orta_en}' iki farklı mega grupta ({o.ust_anahtar} / {mega_en}), ilki kullanıldı")
            o.grup_kodlari.add(kod)
            if mega_en:
                orta_mega[orta_en] = o.ust_anahtar or mega_en

        a = altlar.get(alt_en)
        if a is None:
            _, _, alt_adlar = _adlari_oku(row, ALT_AD_SUTUNU)
            a = Kategori(anahtar=alt_en, adlar=alt_adlar, ust_anahtar=orta_en or mega_en or None)
            altlar[alt_en] = a
        elif orta_en and a.ust_anahtar and a.ust_anahtar not in (orta_en, mega_en):
            uyarilar.append(f"{kod}: alt '{alt_en}' iki farklı orta/mega grupta ({a.ust_anahtar} / {orta_en}), ilki kullanıldı")
        a.grup_kodlari.add(kod)
        if orta_en:
            alt_orta[alt_en] = orta_en
        kod_alt[kod] = alt_en

    # --- Kollaps: bir mega'nın TEK orta çocuğu varsa o orta katman atlanır ---
    mega_orta_sayisi: dict[str, int] = {}
    for o in ortalar.values():
        if o.ust_anahtar:
            mega_orta_sayisi[o.ust_anahtar] = mega_orta_sayisi.get(o.ust_anahtar, 0) + 1
    tekil_orta_mega: dict[str, str] = {  # orta_anahtar -> mega_anahtar (kollaps edilecekler)
        o.anahtar: o.ust_anahtar for o in ortalar.values()
        if o.ust_anahtar and mega_orta_sayisi.get(o.ust_anahtar) == 1
    }
    for orta_anahtar, mega_anahtar in tekil_orta_mega.items():
        del ortalar[orta_anahtar]
        for a in altlar.values():
            if a.ust_anahtar == orta_anahtar:
                a.ust_anahtar = mega_anahtar

    # --- Sıralama: mega -> orta -> alt, hepsi (kollaps öncesi) gerçek soy ağacına göre ---
    mega_sirali = sorted(megalar.values(), key=lambda k: _sira_sayi_harf(k.sira))
    mega_sira_no = {m.anahtar: _sira_sayi_harf(m.sira) for m in mega_sirali}
    orta_sirali = sorted(
        ortalar.values(),
        key=lambda o: (mega_sira_no.get(o.ust_anahtar or "", (9999, "")), _sira_sayi_harf(o.sira)),
    )
    orta_sira_no = {o.anahtar: _sira_sayi_harf(o.sira) for o in orta_sirali}

    def alt_anahtari(a: Kategori) -> tuple:
        orta_en = alt_orta.get(a.anahtar)
        mega_en = orta_mega.get(orta_en, orta_en) if orta_en else a.ust_anahtar
        return (
            mega_sira_no.get(mega_en or "", (9999, "")),
            orta_sira_no.get(orta_en or "", (-1, "")),  # kollaps edilmiş/orta'sız -> mega içinde en başta
            a.anahtar,
        )

    alt_sirali = sorted(altlar.values(), key=alt_anahtari)
    return Yapi(megalar=mega_sirali, ortalar=orta_sirali, altlar=alt_sirali, kod_alt=kod_alt, uyarilar=uyarilar)


# ------------------------------------------------------------ kategori ağacı JSON
def agac_yaz(yapi: Yapi, koleksiyon: dict[str, dict], urun_sayisi: dict[str, int],
             gorsel: dict[str, str | None], diller: list[str], path: Path = AGAC_PATH) -> dict:
    """Velo'nun okuyacağı ağacı yazar. `koleksiyon`: ad -> Wix kaydı (id/slug),
    `urun_sayisi`/`gorsel`: ad -> satılabilir ürün sayısı / kapak görseli.
    3 katmanlı: her mega düğümünde 'ortalar' (varsa) VE/VEYA 'altlar' (orta
    atlanan mega'larda doğrudan) bulunur; Velo tarafı hangisi doluysa onu
    ikinci seviye olarak kullanır (bkz. wix/masterPage.js)."""
    def dugum(k: Kategori) -> dict:
        w = koleksiyon.get(k.anahtar) or {}
        return {
            "ad": {d: k.adlar.get(d) or k.adlar.get(BIRINCIL_DIL) for d in diller},
            "slug": w.get("slug"),
            "urun": urun_sayisi.get(k.anahtar, 0),
            "gorsel": gorsel.get(k.anahtar),
        }

    def alt_dugumler(ust_anahtar: str) -> list[dict]:
        return [dugum(a) for a in yapi.altlar if a.ust_anahtar == ust_anahtar and urun_sayisi.get(a.anahtar, 0) > 0]

    def mega_dugumu(m: Kategori) -> dict:
        ortalar = [
            {**dugum(o), "altlar": alt_dugumler(o.anahtar)}
            for o in yapi.ortalar if o.ust_anahtar == m.anahtar and urun_sayisi.get(o.anahtar, 0) > 0
        ]
        return {**dugum(m), "sira": m.sira, "ortalar": ortalar, "altlar": alt_dugumler(m.anahtar)}

    agac = {
        "guncelleme": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "diller": diller,
        "birincil_dil": BIRINCIL_DIL,
        "ustler": [mega_dugumu(m) for m in yapi.megalar if urun_sayisi.get(m.anahtar, 0) > 0],
    }
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(agac, ensure_ascii=False, indent=1), encoding="utf-8")
    return agac


def agac_oku(path: Path = AGAC_PATH) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
