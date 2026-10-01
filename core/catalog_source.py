"""Ürün katalogunu UrunEkleme projesindeki AYNI Google Sheets kaynaklarından
okur (STOK, grup, resimler, Katalog/Trad, adrev, içerik) — sheet'lerin
kendisi UrunEkleme ile ortak, ama okuma mantığı bu projeye kendi kopyası
olarak taşındı (iki proje arasında import yok, UrunEkleme'nin
core/stock_sheet.py + core/name_cleaner.py + core/content_source.py'sinin
"barkod -> alan" sözlükleri aynı desenle burada da üretiliyor).

FARKLAR (kullanıcının 2026-09-22 kararları):
- Fiyat: VADELİ FİYAT KDV DAHİL sütunu ÇARPANSIZ kullanılır (UrunEkleme'nin
  ×1.5 pazaryeri kâr payı burada YOK — bu direkt müşteri kataloğu, pazaryeri
  yeniden-satış değil).
- Filtre: STOK MİKTARI >= 10 VE fiyat > 0 (UrunEkleme'nin aynı
  satılabilirlik disiplini, min_stock burada da 10).

Salt okunur — hiçbir yere yazma yapmaz (yazma core/order_writer.py'de,
ayrı ve kimlik doğrulamalı).
"""
import csv
import io
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

STOK_SHEET_ID = "1o0qFFEVT-ZBlzcf2rFu-UL8-ZhbhdtWVpozFFulRtN8"
STOK_GID = "20088621"
KATALOG_SHEET_ID = "133n5tOR0BQhUwf6ItmqR11KGz353MY8P7MPDweHpVS4"

STOK_URL = f"https://docs.google.com/spreadsheets/d/{STOK_SHEET_ID}/export?format=csv&gid={STOK_GID}"
GRUP_URL = f"https://docs.google.com/spreadsheets/d/{KATALOG_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=grup"
RESIMLER_URL = f"https://docs.google.com/spreadsheets/d/{KATALOG_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=resimler"
KATALOG_URL = f"https://docs.google.com/spreadsheets/d/{KATALOG_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=Katalog"
ADREV_URL = f"https://docs.google.com/spreadsheets/d/{KATALOG_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=adrev"
ICERIK_URL = f"https://docs.google.com/spreadsheets/d/{KATALOG_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=içerik"
# core/ceviri_bakim.py'nin GOOGLETRANSLATE formülleriyle doldurduğu sekme -
# başlıkların İngilizce/Rusça çevirisi (kullanıcı isteği 2026-10-01).
CEVIRI_BASLIK_URL = f"https://docs.google.com/spreadsheets/d/{KATALOG_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=ceviri_baslik"
# 'grup' sekmesinde/override'larda çevirisi olmayan kategori adlarının otomatik
# (GOOGLETRANSLATE) çevirisi - core/ceviri_bakim.py doldurur.
CEVIRI_KATEGORI_URL = f"https://docs.google.com/spreadsheets/d/{KATALOG_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=ceviri_kategori"
# "SİPARİŞ DOSYASI" spreadsheet'inin (core/order_writer.py ile aynı dosya)
# 'tanımlı müşteriler' sekmesi: Kodu -> musteriadi/temsilci/sifre. Giriş
# ekranındaki müşteri kodu otofill'i için (bkz. app.py).
MUSTERILER_SHEET_ID = "1uN_5lvCCkT3dYMW4S45aP7DB4nFv7xb4JmvRHs9nrHA"
MUSTERILER_URL = (
    f"https://docs.google.com/spreadsheets/d/{MUSTERILER_SHEET_ID}"
    "/gviz/tq?tqx=out:csv&sheet=tan%C4%B1ml%C4%B1%20m%C3%BC%C5%9Fteriler"
)

MIN_STOCK = 10

# Basit bellek-içi önbellek - her sayfa yüklemesinde 6 ayrı Google Sheets
# isteği atmamak için (STOK+grup+resimler+Katalog+adrev+içerik). TTL süresi
# dolunca bir sonraki istek yeniden çeker.
_CACHE_TTL_SECONDS = 180
_catalog_cache: tuple[float, list] | None = None
_customers_cache: tuple[float, dict] | None = None

# UrunEkleme'nin core/stock_sheet.py'sinden BİREBİR taşındı — aynı GRUP
# KODU'ları aynı şekilde yanlış/genel isimde topluyor, düzeltme burada da
# geçerli.
CATEGORY_NAME_OVERRIDES = {
    "1540-KUTULU HAVLU": "KUTULU HAVLU",
    "5091-KUTULU HAVLU (NEV)": "KUTULU HAVLU",
    "1020-BATT.NEV.TAK CK": "BATTANİYELİ NEVRESİM TAKIMI ÇİFT KİŞİLİK",
    "1030-PIKELI NEV.TAK CK": "PİKELİ NEVRESİM TAKIMI ÇİFT KİŞİLİK",
    "1040-YAT.ORT.NEV.TAK CK": "YATAK ÖRTÜLÜ NEVRESİM TAKIMI ÇİFT KİŞİLİK",
    "1060-PIKELI NEV.TAK TK": "PİKELİ NEVRESİM TAKIMI TEK KİŞİLİK",
}
# Override adlarının İngilizce/Rusça karşılığı - 'grup' sekmesinde bunlara
# özel satır yok (orada genel isimle duruyorlar), elle çevrildi (kullanıcı
# isteği 2026-10-01). Anahtar: override'ın Türkçe adı.
CATEGORY_NAME_OVERRIDES_I18N = {
    "KUTULU HAVLU": {"en": "BOXED TOWELS", "ru": "ПОЛОТЕНЦА В ПОДАРОЧНОЙ КОРОБКЕ"},
    "BATTANİYELİ NEVRESİM TAKIMI ÇİFT KİŞİLİK": {
        "en": "DUVET COVER SET WITH BLANKET - DOUBLE", "ru": "КОМПЛЕКТ ПОСТЕЛЬНОГО БЕЛЬЯ С ОДЕЯЛОМ - ДВУСПАЛЬНЫЙ"},
    "PİKELİ NEVRESİM TAKIMI ÇİFT KİŞİLİK": {
        "en": "DUVET COVER SET WITH PIQUE - DOUBLE", "ru": "КОМПЛЕКТ ПОСТЕЛЬНОГО БЕЛЬЯ С ПИКЕ - ДВУСПАЛЬНЫЙ"},
    "YATAK ÖRTÜLÜ NEVRESİM TAKIMI ÇİFT KİŞİLİK": {
        "en": "DUVET COVER SET WITH BEDSPREAD - DOUBLE", "ru": "КОМПЛЕКТ ПОСТЕЛЬНОГО БЕЛЬЯ С ПОКРЫВАЛОМ - ДВУСПАЛЬНЫЙ"},
    "PİKELİ NEVRESİM TAKIMI TEK KİŞİLİK": {
        "en": "DUVET COVER SET WITH PIQUE - SINGLE", "ru": "КОМПЛЕКТ ПОСТЕЛЬНОГО БЕЛЬЯ С ПИКЕ - ОДНОСПАЛЬНЫЙ"},
}

_WORD_RE = re.compile(r"[^\s]+")
_TR_UPPER_I, _TR_LOWER_I = "İ", "i"
_TR_UPPER_DOTLESS_I, _TR_LOWER_DOTLESS_I = "I", "ı"

# STOK'taki adlar ASCII büyük harf ("DIAGONAL", "SILGI", "YASTIK") - noktalı/
# noktasız i bilgisi kaynakta YOK. Eskiden her "I" körlemesine "ı" yapılıyordu:
# YASTIK->yastık doğru ama DIAGONAL->dıagonal, SENSICE->sensıce, BATTANIYE->
# battanıye yanlış çıkıyordu (2026-09-29, Wix senkronu hazırlanırken fark
# edildi). Harfe bakarak karar verilemez, kelimeye bakılır: doğru yazılmış
# kaynaklardan (ürün açıklamaları 'içerik' + adrev uzun adları) kelime
# biçimleri ÖĞRENİLİR (_kelime_bicimlerini_ogren), üstüne sık Türkçe katalog
# kelimeleri için sabit bir çekirdek liste; hiç görülmemiş kelime ş/ç/ğ/ö/ü
# içeriyorsa Türkçe sayılıp ı korunur, yoksa (marka/İngilizce) "i" yapılır.
_ASCII_FOLD = str.maketrans("çğıiİöşüÇĞIÖŞÜ", "CGIIIOSUCGIOSU")
_HARF_RUN_RE = re.compile(r"[A-Za-zÇĞİIÖŞÜçğıiöşü]+")
_TURKCE_OZEL_HARFLER = set("şçğöüŞÇĞÖÜ")

_CEKIRDEK_KELIMELER = {
    "KILIF": "kılıf", "KILIFI": "kılıfı", "YASTIK": "yastık", "KISILIK": "kişilik", "CIFT": "çift",
    "BATTANIYE": "battaniye", "SILGI": "silgi", "PIKE": "pike", "PIKELI": "pikeli", "NEVRESIM": "nevresim",
    "TAKIMI": "takımı", "TAKIM": "takım", "HAVLUSU": "havlusu", "ORTUSU": "örtüsü", "ORTU": "örtü",
    "SIVI": "sıvı", "GECIRMEZ": "geçirmez", "CARSAF": "çarşaf", "KIRMIZI": "kırmızı", "SARI": "sarı",
    "ACIK": "açık", "YESIL": "yeşil", "GRI": "gri", "MAVI": "mavi", "MURDUM": "mürdüm", "FUSYA": "fuşya",
    "TAS": "taş", "CAY": "çay", "PESTEMAL": "peştemal", "COCUK": "çocuk", "KADIN": "kadın", "ONLUGU": "önlüğü",
    "CEYIZ": "çeyiz", "PECETE": "peçete", "IKILI": "ikili", "UCLU": "üçlü", "DORTLU": "dörtlü", "ALTILI": "altılı",
    "PARCA": "parça", "PARCALI": "parçalı", "DUZ": "düz", "CIZGILI": "çizgili", "CICEKLI": "çiçekli",
    "NAKISLI": "nakışlı", "ISLEMELI": "işlemeli", "JAKARLI": "jakarlı", "BASKILI": "baskılı", "KAPSONLU": "kapşonlu",
    "SALYAKA": "şalyaka", "KAPITONELI": "kapitoneli", "LASTIKLI": "lastikli", "MIKRO": "mikro", "MIKROFIBER": "mikrofiber",
    "ISIK": "ışık", "KIS": "kış", "YAZLIK": "yazlık", "KISLIK": "kışlık", "SICAK": "sıcak", "ILIK": "ılık",
    "LILA": "lila", "ANTRASIT": "antrasit", "INDIGO": "indigo", "LACIVERT": "lacivert", "VIZON": "vizon",
    "COLURIST": "colurist", "COLOURIST": "colourist", "YASTIKLI": "yastıklı", "CARSAFLI": "çarşaflı",
    "KILIFLI": "kılıflı", "GUMUS": "gümüş", "DUS": "duş", "KUL": "kül", "YUN": "yün", "YESILI": "yeşili",
    "KUMBEJI": "kumbeji", "SIM": "sim", "KENEVIR": "kenevir", "SETI": "seti", "BIYE": "biye",
}
_ogrenilen_kelimeler: dict[str, str] = dict(_CEKIRDEK_KELIMELER)


def _kelime_bicimlerini_ogren(adrev: dict[str, str], icerik_lookup: dict[str, str]) -> None:
    """İnsan eliyle yazılmış kaynaklardaki (ürün açıklamaları 'içerik' +
    adrev uzun adları) her kelimeyi ASCII-büyük anahtarıyla (YASTIK -> yastık)
    öğrenir. Katalog 'Trad' sütunu BİLEREK kullanılmıyor: 1546 ürünün başlığı
    oradan geliyor ve o sütunun kendisi aynı kör I->ı hatasını taşıyor
    ("Dıagonal", "Colurıst") - ondan öğrenmek hatayı pekiştirirdi. Çekirdek
    liste her zaman öncelikli."""
    # Çoğunluk oyu: açıklamalardaki tek tük yazım hatası ("antrasıt", "lıla")
    # sözlüğe sızmasın - her anahtar için en sık görülen biçim kazanır.
    sayac: dict[str, dict[str, int]] = {}
    for metin in list(icerik_lookup.values()) + list(adrev.values()):
        for kelime in _HARF_RUN_RE.findall(metin):
            if len(kelime) < 3:
                continue
            anahtar = kelime.translate(_ASCII_FOLD).upper()
            if anahtar in _CEKIRDEK_KELIMELER:
                continue
            if kelime == kelime.upper() and "I" in kelime and not (
                _TR_UPPER_I in kelime or _TURKCE_OZEL_HARFLER & set(kelime)
            ):
                # TAMAMEN BÜYÜK yazılmış ve içinde I var ama yazarın İ/I ayrımı
                # yaptığına dair iz yok ("FITTED" mi "FITTED" mı?) - belirsiz, atla.
                continue
            bicim = _turkce_kucult(kelime)
            sayac.setdefault(anahtar, {})
            sayac[anahtar][bicim] = sayac[anahtar].get(bicim, 0) + 1
    _ogrenilen_kelimeler.clear()
    for anahtar, bicimler in sayac.items():
        # Eşitlikte Türkçe harf taşıyan biçim kazanır ("gumus" 4 - "gümüş" 4 -> gümüş)
        _ogrenilen_kelimeler[anahtar] = max(
            bicimler.items(), key=lambda kv: (kv[1], sum(not c.isascii() for c in kv[0]))
        )[0]
    _ogrenilen_kelimeler.update(_CEKIRDEK_KELIMELER)


def _turkce_kucult(kelime: str) -> str:
    """Düzgün yazılmış Türkçe için küçültme: İ -> i, I -> ı."""
    return kelime.replace(_TR_UPPER_I, _TR_LOWER_I).replace("I", _TR_LOWER_DOTLESS_I).lower()


def _harf_harf_birlestir(giris: str, ogrenilen: str) -> str:
    """Girişin küçültülmüş hali ile öğrenilen biçimi harf harf birleştirir
    (ASCII katlama 1:1 olduğu için uzunluklar eşit). ı/i kararı öğrenilenden
    gelir; diğer harflerde ise Türkçe harf (ş/ü/…) taşıyan taraf kazanır -
    böylece sözlükteki tek tük düz-ASCII yazım ("gumus") girişin doğru
    "gümüş"ünü bozamaz, ama girişteki "Dus" sözlükten "duş"a yükselebilir."""
    if len(giris) != len(ogrenilen):
        return ogrenilen
    sonuc = []
    for g, o in zip(giris, ogrenilen):
        if o in (_TR_LOWER_I, _TR_LOWER_DOTLESS_I):
            sonuc.append(o)
        elif o.isascii() and not g.isascii():
            sonuc.append(g)
        else:
            sonuc.append(o)
    return "".join(sonuc)


def _kucuk_bicim(parca: str) -> str:
    """Bir kelimenin doğru küçük-harf biçimi: öğrenilmişse onunla harf harf
    birleştirilir; değilse ve kelime Türkçe'ye özgü harf (ş/ç/ğ/ö/ü)
    içeriyorsa zaten Türkçe'dir, ı'lar korunur; onun dışında (marka/
    İngilizce) I -> i."""
    bilinen = _ogrenilen_kelimeler.get(parca.translate(_ASCII_FOLD).upper())
    if bilinen:
        return _harf_harf_birlestir(_turkce_kucult(parca), bilinen)
    if _TURKCE_OZEL_HARFLER & set(parca):
        return _turkce_kucult(parca)
    return parca.replace(_TR_UPPER_I, _TR_LOWER_I).lower().replace(_TR_LOWER_DOTLESS_I, _TR_LOWER_I)


def _tr_lower(word: str) -> str:
    return _HARF_RUN_RE.sub(lambda m: _kucuk_bicim(m.group(0)), word)


def _baslik_onar(baslik: str) -> str:
    """Zaten büyük/küçük harfli bir başlıktaki (Katalog 'Trad') ı/i hatalarını
    kelime bazında onarır, kelimenin baş harfi büyükse büyük bırakır.
    Kaynak Sheet'e dokunmaz - sadece okurken uygulanır."""
    def onar(m: re.Match) -> str:
        parca = m.group(0)
        if len(parca) <= 2:  # "6 Lı", "M.", "Ck" gibi ekler/kısaltmalar - dokunma
            return parca
        yeni = _kucuk_bicim(parca)
        if parca[:1].isupper():
            yeni = _tr_buyuk_harf(yeni[:1]) + yeni[1:]
        return yeni
    return _ayrac_sonrasi_buyut(_HARF_RUN_RE.sub(onar, baslik))


def _tr_buyuk_harf(harf: str) -> str:
    if harf == _TR_LOWER_I:
        return _TR_UPPER_I
    if harf == _TR_LOWER_DOTLESS_I:
        return _TR_UPPER_DOTLESS_I
    return harf.upper()


# "Cotton Garden/gri", "Bisc-polar", "C.sarı" -> ayraçtan ("/", "-", ".", "(")
# sonra gelen harf de büyük: "Cotton Garden/Gri", "Bisc-Polar", "C.Sarı".
_AYRAC_SONRASI_HARF_RE = re.compile(r"(?<=[/\-.(])([a-zçğıiöşü])")


def _ayrac_sonrasi_buyut(text: str) -> str:
    return _AYRAC_SONRASI_HARF_RE.sub(lambda m: _tr_buyuk_harf(m.group(1)), text)


def _tr_title_word(word: str) -> str:
    if not word:
        return word
    lowered = _tr_lower(word)
    return _ayrac_sonrasi_buyut(_tr_buyuk_harf(lowered[0]) + lowered[1:])


def tr_title_case(text: str) -> str:
    return " ".join(_tr_title_word(w) for w in text.split(" "))


def _parse_float(value) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_int(value) -> int | None:
    f = _parse_float(value)
    return int(f) if f is not None else None


def _fetch_csv_rows(url: str) -> list[dict]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    return list(csv.DictReader(io.StringIO(response.text)))


def _fetch_grup_lookup() -> dict[str, dict[str, str]]:
    """GRUP KODU -> {"tr":.., "en":.., "ru":..} (ENG-UZUN/RU-UZUN boşsa o dil
    sözlükte yok - kullanıcı isteği 2026-10-01: kategori adları da İngilizce/
    Rusça gösterilsin, mevcut TR-UZUN'a dokunmadan)."""
    rows = _fetch_csv_rows(GRUP_URL)
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        kod = (row.get("GRUP KODU") or "").strip()
        if not kod:
            continue
        d = {}
        for dil, sutun in (("tr", "TR-UZUN"), ("en", "ENG-UZUN"), ("ru", "RU-UZUN")):
            deger = (row.get(sutun) or "").strip()
            if deger:
                d[dil] = deger
        result[kod] = d
    return result


def _clean_grup_kodu(grup_kodu: str) -> str:
    cleaned = re.sub(r"^\d+-\s*", "", grup_kodu)
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned)
    return cleaned.strip() or grup_kodu


def _fetch_resimler_lookup() -> dict[str, list[str]]:
    rows = _fetch_csv_rows(RESIMLER_URL)
    result: dict[str, list[str]] = {}
    for row in rows:
        barcode = (row.get("barkod") or "").strip()
        if not barcode:
            continue
        images = [
            (row.get(key) or "").strip()
            for key in row
            if key and key.startswith("resim")
        ]
        images = [url for url in images if url.lower().startswith("http")]
        if images:
            result[barcode] = images
    return result


def _fetch_katalog_trad() -> dict[str, str]:
    rows = _fetch_csv_rows(KATALOG_URL)
    result = {}
    for row in rows:
        barcode = (row.get("Barkod") or "").strip()
        trad = (row.get("Trad") or "").strip()
        if barcode and trad:
            result[barcode] = trad
    return result


def _fetch_adrev_dict() -> dict[str, str]:
    rows = _fetch_csv_rows(ADREV_URL)
    result = {}
    for row in rows:
        short = (row.get("KISA ADI") or "").strip().upper()
        long_ = (row.get("UZUN ADI") or "").strip()
        if short and long_:
            result[short] = long_
    return result


def _expand_with_adrev(raw_name: str, adrev: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        word = match.group(0)
        return adrev.get(word.upper().strip(".,"), word)

    expanded = _WORD_RE.sub(replace, raw_name)
    return tr_title_case(expanded)


# Kullanıcının 2026-09-22 talimatı: ürün sayfasında marka adı ve gramaj/tel
# sayısı bilgisi GÖSTERİLMESİN. icerik_tr serbest metninde bu alanlar
# "Etiket: değer" şeklinde geçiyor (örn. "...Marka: Özdilek Kalite:...",
# PDF kataloğunda "Gramaj (gr) / Tel Sayısı: 57 - 61"). DEĞERİ de silmek
# gerektiği için genel bir "bir sonraki etikete kadar" lookahead'i GÜVENSİZ
# (marka değeri "Özdilek" gibi kendisi de büyük harfle başlayıp bir sonraki
# etiket gibi görünebiliyor, canlı veriyle test edilirken yakalandı) --
# bunun yerine değerin kendi biçimini bilerek eşleşiyoruz: marka her zaman
# sabit "Özdilek", gramaj/tel sayısı her zaman sayısal.
_HIDDEN_DESCRIPTION_FIELDS = re.compile(
    r"Marka\s*:\s*Özdilek\s*"
    r"|(?:Gramaj\s*\(gr\)\s*/\s*Tel\s*Sayısı|Tel\s*Sayısı|Gramaj)\s*:\s*[\d][\d\s\-–,./]*\s*",
    re.UNICODE,
)


def _strip_hidden_fields(description: str) -> str:
    cleaned = _HIDDEN_DESCRIPTION_FIELDS.sub("", description)
    # icerik_tr metni tipik olarak "Özdilek <ürün adı tekrarı>..." ile
    # başlıyor (etiketsiz, serbest metin) -- sadece metnin EN BAŞINDAKİ
    # kelimeyse silinir, cümle içinde geçen başka "Özdilek" dokunulmaz.
    cleaned = re.sub(r"^Özdilek\s+", "", cleaned.strip())
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _fetch_icerik_lookup_tum() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """'içerik' sekmesini TEK seferde çeker (icerik_tr HER ZAMAN, icerik_en/
    icerik_ru'yu da - varsa - aynı istekten çıkarır; ayrı bir HTTP isteği
    açmaz). İkinci değer: barkod -> {"en":..,"ru":..} - core/ceviri_bakim.py'nin
    GOOGLETRANSLATE formülleriyle doldurduğu sütunlar, henüz hesaplanmamış/boş
    olanlar sözlükte yer almaz (çağıran taraf Türkçeye döner)."""
    rows = _fetch_csv_rows(ICERIK_URL)
    tr_sozluk: dict[str, str] = {}
    i18n_sozluk: dict[str, dict[str, str]] = {}
    for row in rows:
        barcode = (row.get("barkod") or "").strip()
        if not barcode:
            continue
        content = (row.get("icerik_tr") or "").strip()
        if content:
            tr_sozluk[barcode] = content
        en = (row.get("icerik_en") or "").strip()
        ru = (row.get("icerik_ru") or "").strip()
        if en or ru:
            d = {}
            if en:
                d["en"] = _strip_hidden_fields(en)
            if ru:
                d["ru"] = _strip_hidden_fields(ru)
            i18n_sozluk[barcode] = d
    return tr_sozluk, i18n_sozluk


def _fetch_kategori_ceviri_lookup() -> dict[str, dict[str, str]]:
    """Türkçe kategori adı -> {"en":..,"ru":..} ('ceviri_kategori' sekmesi,
    GOOGLETRANSLATE). Sekme henüz yoksa/erişilemezse boş döner."""
    try:
        rows = _fetch_csv_rows(CEVIRI_KATEGORI_URL)
    except requests.exceptions.RequestException:
        return {}
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        ad = (row.get("tr_ad") or "").strip()
        if not ad:
            continue
        d = {dil: (row.get(f"{dil}_ad") or "").strip() for dil in ("en", "ru")}
        d = {k: v for k, v in d.items() if v}
        if d:
            result[ad] = d
    return result


def _fetch_baslik_ceviri_lookup() -> dict[str, dict[str, str]]:
    """Ürün başlıklarının İngilizce/Rusça çevirisi - core/ceviri_bakim.py'nin
    yazdığı 'ceviri_baslik' sekmesinden (barkod + tr_baslik + GOOGLETRANSLATE
    formülleri). Henüz hesaplanmamış/boş olanlar sözlükte yer almaz."""
    try:
        rows = _fetch_csv_rows(CEVIRI_BASLIK_URL)
    except requests.exceptions.RequestException:
        return {}
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        barcode = (row.get("barkod") or "").strip()
        if not barcode:
            continue
        en = (row.get("en_baslik") or "").strip()
        ru = (row.get("ru_baslik") or "").strip()
        if en or ru:
            d = {}
            if en:
                d["en"] = en
            if ru:
                d["ru"] = ru
            result[barcode] = d
    return result


@dataclass
class Product:
    barcode: str
    title: str
    price: float
    stock: int
    category_code: str | None
    category_name: str | None
    images: list[str] = field(default_factory=list)
    description: str = ""
    resim_tarihi: datetime | None = None
    # İngilizce/Rusça çeviri (kullanıcı isteği 2026-10-01, core/ceviri_bakim.py
    # ile Sheets'in GOOGLETRANSLATE'i üzerinden dolduruluyor). Sadece "en"/"ru"
    # anahtarları olabilir, "tr" burada YOK - title/description zaten Türkçe.
    # Henüz çeviri hesaplanmamışsa boş kalır, baslik()/aciklama() Türkçeye döner.
    title_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)
    category_name_i18n: dict[str, str] = field(default_factory=dict)

    def baslik(self, dil: str) -> str:
        return self.title_i18n.get(dil) or self.title

    def aciklama(self, dil: str) -> str:
        return self.description_i18n.get(dil) or self.description

    def kategori_adi(self, dil: str) -> str | None:
        return self.category_name_i18n.get(dil) or self.category_name


# Cloudinary URL'lerindeki "/v1746104308/" gibi sürüm segmenti aslında o
# görselin Cloudinary'e YÜKLENDİĞİ an'ın Unix zaman damgası (Cloudinary'nin
# kendi otomatik versiyonlama davranışı) -- kullanıcının 2026-09-23 isteği
# "resmi yeni eklenen ürünleri ayırt edebilir miyiz" için gerçek bir tarih
# kaynağı olarak kullanılıyor (sheet'lerde ayrı bir "eklenme tarihi" sütunu
# yok). Sadece Cloudinary CDN'inden gelen resimlerde çalışır -- diğer CDN
# (örn. img-ozdilekteyim.mncdn.com, 'resimler' sekmesinden) bu deseni
# içermez, o durumda None döner ve ürün "yeni" sayılmaz (güvenli varsayılan).
_CLOUDINARY_VERSION_RE = re.compile(r"/v(\d{9,10})/")


def _resim_yukleme_tarihi(image_url: str | None) -> datetime | None:
    if not image_url:
        return None
    match = _CLOUDINARY_VERSION_RE.search(image_url)
    if not match:
        return None
    try:
        return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)
    except (ValueError, OSError):
        return None


class CatalogUnavailable(Exception):
    """Sheets'e ulaşılamadı VE elde hiç (bayat bile olsa) önbellek yok -
    app.py bunu dostane bir 503 sayfasına çeviriyor (2026-09-27 gözden
    geçirmesi: eskiden çıplak 500 dönüyordu)."""


def fetch_catalog(min_stock: int = MIN_STOCK, force_refresh: bool = False) -> list[Product]:
    """STOK sekmesini ceker, STOK MİKTARI >= min_stock VE fiyat > 0 olanları
    döndürür. Fiyat VADELİ FİYAT KDV DAHİL sütunundan ÇARPANSIZ okunur.
    _CACHE_TTL_SECONDS boyunca bellekte tutulur (sayfa başı 6 ayrı istek
    atmamak için). Yeniden çekme başarısız olursa bayat önbellek (varsa)
    sessizce kullanılmaya devam eder - Sheets'in kısa kesintisi siteyi
    düşürmesin."""
    global _catalog_cache
    if not force_refresh and _catalog_cache is not None:
        cached_at, cached_products = _catalog_cache
        if time.time() - cached_at < _CACHE_TTL_SECONDS:
            return cached_products

    try:
        products = _fetch_catalog_uncached(min_stock)
    except Exception as e:
        if _catalog_cache is not None:
            logger.warning("catalog_source: katalog yenilenemedi, bayat önbellek kullanılıyor (%s)", e)
            return _catalog_cache[1]
        raise CatalogUnavailable(str(e)) from e
    _catalog_cache = (time.time(), products)
    return products


def _fetch_catalog_uncached(min_stock: int) -> list[Product]:
    # 6 ayrı Google Sheets isteği - sırayla değil PARALEL (ThreadPoolExecutor).
    # Render.com'un free tier'ında sıralı çekim gunicorn'un worker timeout'unu
    # (varsayılan 30sn) aşıp worker'ın SIGKILL'lenmesine yol açtı (2026-09-23,
    # canlı ortamda yakalandı) - paralel çekim bunu ~6 kat hızlandırır.
    with ThreadPoolExecutor(max_workers=8) as executor:
        f_kategori_ceviri = executor.submit(_fetch_kategori_ceviri_lookup)
        f_stok = executor.submit(_fetch_csv_rows, STOK_URL)
        f_grup = executor.submit(_fetch_grup_lookup)
        f_resimler = executor.submit(_fetch_resimler_lookup)
        f_trad = executor.submit(_fetch_katalog_trad)
        f_adrev = executor.submit(_fetch_adrev_dict)
        f_icerik = executor.submit(_fetch_icerik_lookup_tum)
        f_baslik_ceviri = executor.submit(_fetch_baslik_ceviri_lookup)

        stok_rows = f_stok.result()
        grup_lookup = f_grup.result()
        resimler_lookup = f_resimler.result()
        trad_lookup = f_trad.result()
        adrev = f_adrev.result()
        icerik_lookup, icerik_ceviri_lookup = f_icerik.result()
        baslik_ceviri_lookup = f_baslik_ceviri.result()
        kategori_ceviri_lookup = f_kategori_ceviri.result()
    _kelime_bicimlerini_ogren(adrev, icerik_lookup)

    products: list[Product] = []
    for row in stok_rows:
        barcode = (row.get("BARKOD") or "").strip()
        if not barcode:
            continue

        stock = _parse_int(row.get("STOK MİKTARI"))
        if stock is None or stock < min_stock:
            continue

        price = _parse_float(row.get("VADELİ FİYAT KDV DAHİL"))
        if not price or price <= 0:
            continue

        raw_name = (row.get("ADI") or "").strip()
        trad = trad_lookup.get(barcode)
        title = _baslik_onar(trad) if trad else _expand_with_adrev(raw_name, adrev)

        grup_kodu = (row.get("ÜRÜN GRUBU") or "").strip()
        grup_adlari = grup_lookup.get(grup_kodu) or {}
        category_name = (
            CATEGORY_NAME_OVERRIDES.get(grup_kodu)
            or grup_adlari.get("tr")
            or (_clean_grup_kodu(grup_kodu) if grup_kodu else None)
        )
        # EN/RU kategori adı - sadece grup_lookup'tan (override/clean fallback
        # TR'dir, çevirisi yok); TR-UZUN ile aynıysa (override kullanıldıysa)
        # eklenmez, kategori_adi() zaten category_name'e düşer.
        # Öncelik: elle yazılmış (override sözlüğü / 'grup' ENG-UZUN,RU-UZUN) ->
        # eksik dil varsa 'ceviri_kategori' sekmesindeki otomatik çeviri.
        if grup_kodu in CATEGORY_NAME_OVERRIDES:
            category_name_i18n = dict(CATEGORY_NAME_OVERRIDES_I18N.get(category_name, {}))
        else:
            category_name_i18n = {
                dil: ad for dil, ad in grup_adlari.items()
                if dil != "tr" and ad and category_name == grup_adlari.get("tr")
            }
        for dil, ad in kategori_ceviri_lookup.get(category_name or "", {}).items():
            category_name_i18n.setdefault(dil, ad)

        images = resimler_lookup.get(barcode)
        if not images:
            image_url = (row.get("RESİMLER") or "").strip()
            valid = image_url.lower().startswith("http") and "/upload//" not in image_url
            images = [image_url] if valid else []

        products.append(Product(
            barcode=barcode,
            title=title,
            price=round(price, 2),
            stock=stock,
            category_code=grup_kodu or None,
            category_name=category_name,
            images=images,
            description=_strip_hidden_fields(icerik_lookup.get(barcode, "")),
            resim_tarihi=_resim_yukleme_tarihi(images[0] if images else None),
            title_i18n=baslik_ceviri_lookup.get(barcode, {}),
            description_i18n=icerik_ceviri_lookup.get(barcode, {}),
            category_name_i18n=category_name_i18n,
        ))

    logger.info("catalog_source: %d ürün (stok >= %d, fiyat > 0)", len(products), min_stock)
    return products


@dataclass
class Customer:
    kodu: str
    musteriadi: str
    temsilci: str


def fetch_customers(force_refresh: bool = False) -> dict[str, Customer]:
    """'tanımlı müşteriler' sekmesi: Kodu -> Customer. Giriş ekranında
    kullanıcı bir kodu (örn. '11..') yazınca Firma adı/Müşteri Temsilcisi
    Adı alanlarını otomatik doldurmak için (bkz. app.py '/api/musteri-kodu').
    ŞİFRE bu sözlükte YOK — kullanıcının 2026-09-22 talimatı gereği giriş
    şifresi sabit "Ozd123" (sheet'teki 'sifre' kolonu şu an kullanılmıyor)."""
    global _customers_cache
    if not force_refresh and _customers_cache is not None:
        cached_at, cached = _customers_cache
        if time.time() - cached_at < _CACHE_TTL_SECONDS:
            return cached

    try:
        rows = _fetch_csv_rows(MUSTERILER_URL)
    except Exception as e:
        # Otofill sadece bir kolaylık - Sheets erişilemezse giriş formunu
        # bozmak yerine bayat listeyi (ya da hiç eşleşme yok) döndür.
        logger.warning("catalog_source: müşteri listesi çekilemedi (%s)", e)
        return _customers_cache[1] if _customers_cache is not None else {}
    result: dict[str, Customer] = {}
    for row in rows:
        kodu = (row.get("Kodu") or "").strip()
        if not kodu:
            continue
        result[kodu] = Customer(
            kodu=kodu,
            musteriadi=(row.get("musteriadi") or "").strip(),
            temsilci=(row.get("temsilci") or "").strip(),
        )
    _customers_cache = (time.time(), result)
    logger.info("catalog_source: %d tanımlı müşteri okundu", len(result))
    return result


# TTL dolduğunda İLK isteği yapan ziyaretçi 6 Google Sheets isteğinin
# bitmesini (~2sn, bazen ağa göre daha da uzun) senkron beklemek zorunda
# kalıyordu ("Ürünleri Görüntüle" geçişinin yavaş hissettirmesi, kullanıcının
# 2026-09-24 şikayeti). Bunun yerine önbelleği hiçbir isteği bloklamadan
# arka planda, süresi dolmadan ÖNCE tazeleyen bir daemon thread - ziyaretçiler
# her zaman zaten-hazır önbellekten okur.
_BACKGROUND_REFRESH_INTERVAL = max(_CACHE_TTL_SECONDS - 30, 30)


def _background_refresh_loop() -> None:
    while True:
        try:
            fetch_catalog(force_refresh=True)
            fetch_customers(force_refresh=True)
        except Exception:
            # Bir turda Sheets erişilemezse eski (hâlâ geçerli) önbellek
            # korunur - fetch_catalog/fetch_customers exception fırlatınca
            # önbelleği güncellemiyor, sadece burada loglanıp bir sonraki
            # turda tekrar denenir.
            logger.exception("catalog_source: arka plan yenileme başarısız, mevcut önbellek korunuyor")
        time.sleep(_BACKGROUND_REFRESH_INTERVAL)


def start_background_refresh() -> None:
    """Sunucu başlarken bir kez çağrılır: önbelleği hemen doldurur, sonra
    periyodik olarak arka planda tazeler."""
    threading.Thread(target=_background_refresh_loop, daemon=True).start()
