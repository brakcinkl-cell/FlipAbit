"""FlipaBit arayüzüne İngilizce/Rusça ekleme işinin VERİ katmanı
(kullanıcı isteği 2026-10-01: "ingilizce ve rusça yapalım", "sheets üzerinden
çeviri kullanıp göndersek, ek maliyet çıkmasın").

Ücretli bir çeviri API'si entegre ETMİYORUZ - Google Sheets'in kendi ÜCRETSİZ
`GOOGLETRANSLATE()` fonksiyonunu kullanıyoruz. Bu fonksiyon sadece gerçek bir
Sheets hücresinde çalışır (Python'dan doğrudan çağrılamaz); bu yüzden akış:

  1. Bu script, mevcut SATILABİLİR katalogdaki (core.catalog_source ile aynı
     filtre: stok>=10, fiyat>0) her ürünün TÜRKÇE başlığını (zaten var olan
     karmaşık Trad/adrev mantığıyla) hesaplar ve YENİ BİR SEKME olan
     'ceviri_baslik'e (barkod + tr_baslik) yazar - SADECE yeni/değişen
     barkodlar için (var olan satırlara dokunmaz, GOOGLETRANSLATE kotasını
     gereksiz tüketmesin).
  2. O sekmede en_baslik/ru_baslik sütunları `=GOOGLETRANSLATE(...)` formülü
     olarak durur - Sheets bunları KENDİSİ hesaplar, biz sadece formülü
     bir kere yazarız.
  3. 'içerik' sekmesine de aynı mantıkla icerik_en/icerik_ru FORMÜL
     sütunları eklenir (icerik_tr'yi referans alır) - sadece satılabilir
     barkodlar için (hacim/kota kontrolü).
  4. core/catalog_source.py bir sonraki senkronda bu sütunları (artık Sheets
     tarafından hesaplanmış düz metin olarak) okur, title_en/title_ru/
     description_en/description_ru alanlarını doldurur.

Çalıştırma: `python -m core.ceviri_bakim` (idempotent, tekrar tekrar
çalıştırılabilir; sadece eksik/yeni satırları tamamlar).
"""
from __future__ import annotations

import sys
from pathlib import Path

import gspread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import catalog_source as cs  # noqa: E402

CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "credentials" / "excel-to-sheet.json"
BASLIK_SEKME_ADI = "ceviri_baslik"
BASLIK_BASLIKLAR = ["barkod", "tr_baslik", "en_baslik", "ru_baslik"]
KATEGORI_SEKME_ADI = "ceviri_kategori"
KATEGORI_BASLIKLAR = ["tr_ad", "en_ad", "ru_ad"]


def _client() -> gspread.Client:
    return gspread.service_account(filename=str(CREDENTIALS_PATH))


def _formul(hucre: str, hedef_dil: str) -> str:
    # IFERROR: GOOGLETRANSLATE ara sırada geçici "Loading..."/#ERROR! döner;
    # boş string'e düşürülür, Python tarafı bunu "henüz yok -> TR'ye dön" sayar.
    # NOKTALI VİRGÜL ŞART: bu Sheets Türkçe yerel ayarda - fonksiyon
    # argümanları virgülle değil ";" ile ayrılıyor (virgül ondalık ayracı).
    # Virgülle yazılan formül "Formula parse error." ile #ERROR! veriyor
    # (2026-10-01'de canlıda doğrulandı).
    return f'=IFERROR(GOOGLETRANSLATE({hucre};"tr";"{hedef_dil}");"")'


def baslik_sekmesini_guncelle(wix_degil_sheets_client: gspread.Client | None = None) -> dict:
    """'ceviri_baslik' sekmesini satılabilir katalogdaki her barkod için
    günceller: yoksa satır EKLER (tr_baslik + EN/RU formülleri), varsa VE
    tr_baslik DEĞİŞMİŞSE (ör. ı/i düzeltmesi, yeni ürün adı) günceller.
    Değişmeyen satırlara dokunulmaz (GOOGLETRANSLATE kotası boşa harcanmaz)."""
    client = wix_degil_sheets_client or _client()
    sh = client.open_by_key(cs.KATALOG_SHEET_ID)
    try:
        ws = sh.worksheet(BASLIK_SEKME_ADI)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=BASLIK_SEKME_ADI, rows=2000, cols=4)
        ws.update(range_name="A1:D1", values=[BASLIK_BASLIKLAR])

    mevcut = ws.get_all_values()
    mevcut_satir_no = {row[0].strip(): i for i, row in enumerate(mevcut[1:], start=2) if row and row[0].strip()}
    mevcut_tr = {row[0].strip(): (row[1] if len(row) > 1 else "") for row in mevcut[1:] if row and row[0].strip()}

    urunler = cs.fetch_catalog()
    yeni_satirlar: list[list[str]] = []
    guncellenecek: list[dict] = []
    for p in urunler:
        if p.barcode in mevcut_satir_no:
            if mevcut_tr.get(p.barcode, "") != p.title:
                satir_no = mevcut_satir_no[p.barcode]
                guncellenecek.append({"range": f"B{satir_no}", "values": [[p.title]]})
        else:
            satir_no = len(mevcut) + len(yeni_satirlar) + 1
            yeni_satirlar.append([
                p.barcode, p.title,
                _formul(f"B{satir_no}", "en"), _formul(f"B{satir_no}", "ru"),
            ])

    if yeni_satirlar:
        ws.append_rows(yeni_satirlar, value_input_option="USER_ENTERED")
    if guncellenecek:
        ws.batch_update(guncellenecek, value_input_option="USER_ENTERED")
    return {"eklenen": len(yeni_satirlar), "guncellenen_tr_baslik": len(guncellenecek), "toplam_satilabilir": len(urunler)}


def icerik_cevirilerini_ekle(wix_degil_sheets_client: gspread.Client | None = None) -> dict:
    """'içerik' sekmesine icerik_en/icerik_ru FORMÜL sütunlarını (I, J) ekler
    - sadece satılabilir barkod + icerik_tr dolu VE henüz formül yazılmamış
    satırlar için (hacim kontrolü, var olanı tekrar yazıp kotayı tüketmez)."""
    client = wix_degil_sheets_client or _client()
    sh = client.open_by_key(cs.KATALOG_SHEET_ID)
    ws = sh.worksheet("içerik")

    header = ws.row_values(1)
    if len(header) < 10:
        ws.update(range_name="I1:J1", values=[["icerik_en", "icerik_ru"]])

    tum_satirlar = ws.get_all_values()
    satilabilir = {p.barcode for p in cs.fetch_catalog()}

    guncellenecek = []
    for i, row in enumerate(tum_satirlar[1:], start=2):
        row = row + [""] * max(0, 10 - len(row))
        barkod, icerik_tr, mevcut_en = row[0].strip(), row[5].strip(), row[8].strip()
        if barkod not in satilabilir or not icerik_tr or mevcut_en:
            continue
        guncellenecek.append({"range": f"I{i}:J{i}", "values": [[_formul(f"F{i}", "en"), _formul(f"F{i}", "ru")]]})

    if guncellenecek:
        for parca_basi in range(0, len(guncellenecek), 500):
            ws.batch_update(guncellenecek[parca_basi:parca_basi + 500], value_input_option="USER_ENTERED")
    return {"icerik_formulu_eklenen": len(guncellenecek)}


def kategori_sekmesini_guncelle(wix_degil_sheets_client: gspread.Client | None = None) -> dict:
    """Kullanıcı isteği 2026-10-01: yeni kategori gelince çevirisi de otomatik
    gelsin. 'grup' sekmesinin ENG-UZUN/RU-UZUN'undan ya da kodun
    CATEGORY_NAME_OVERRIDES_I18N'inden çevirisi GELMEYEN her kategori adı
    'ceviri_kategori' sekmesine (tr_ad + GOOGLETRANSLATE formülleri) eklenir.
    Sekmede zaten olan ad tekrar yazılmaz (formül henüz hesaplanmamış olsa da)."""
    client = wix_degil_sheets_client or _client()
    sh = client.open_by_key(cs.KATALOG_SHEET_ID)
    try:
        ws = sh.worksheet(KATEGORI_SEKME_ADI)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=KATEGORI_SEKME_ADI, rows=500, cols=3)
        ws.update(range_name="A1:C1", values=[KATEGORI_BASLIKLAR])

    mevcut = ws.get_all_values()
    sekmedekiler = {row[0].strip() for row in mevcut[1:] if row and row[0].strip()}

    eksikler: list[str] = []
    for p in cs.fetch_catalog():
        ad = p.category_name
        if not ad or ad in sekmedekiler or ad in eksikler:
            continue
        if "en" not in p.category_name_i18n or "ru" not in p.category_name_i18n:
            eksikler.append(ad)

    yeni_satirlar = []
    for ad in eksikler:
        satir_no = len(mevcut) + len(yeni_satirlar) + 1
        yeni_satirlar.append([ad, _formul(f"A{satir_no}", "en"), _formul(f"A{satir_no}", "ru")])
    if yeni_satirlar:
        ws.append_rows(yeni_satirlar, value_input_option="USER_ENTERED")
    return {"kategori_eklenen": len(yeni_satirlar), "eklenenler": eksikler}


def main() -> None:
    client = _client()
    r1 = baslik_sekmesini_guncelle(client)
    print("ceviri_baslik:", r1)
    r2 = icerik_cevirilerini_ekle(client)
    print("içerik EN/RU:", r2)
    r3 = kategori_sekmesini_guncelle(client)
    print("ceviri_kategori:", r3)


if __name__ == "__main__":
    main()
