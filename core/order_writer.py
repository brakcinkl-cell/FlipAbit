"""SİPARİŞ DOSYASI Google Sheet'ine yazar (tek yazma noktası - okuma tarafı
core/catalog_source.py'de, tamamen ayrı ve kimlik doğrulamasız).

KENDİ SEKMELERİMİZ kullanılıyor (FlipaBit_Sepet / FlipaBit_Kesinlesmis),
eski Siparişler/Kesinlesmis DEĞİL -- o sekmelere Flipabit'in eski (tam
olarak anlaşılamayan) bir otomasyonu bağlı olabilir, canlı testte bir
deneme satırı sessizce kayboldu (2026-09-22, kullanıcı onayıyla ayrı
sekmelere geçildi). Aynı spreadsheet içinde ama bağımsız, temiz şema.

Şema (FlipaBit_Sepet VE FlipaBit_Kesinlesmis, ikisi de aynı kolonlar):
NO, kullanıcı adı, barkod, ürün adı, miktar, birim fiyat, satır toplamı, tarih

Akış: sepete eklenen her ürün "FlipaBit_Sepet"e bir satır olarak yazılır (NO
otomatik artan). "Onayla" o kullanıcıya ait tüm satırları "FlipaBit_
Kesinlesmis"e taşır (ekler + kaynaktan siler).

Servis hesabı JSON'ı credentials/ altında, git'e HİÇ girmez (.gitignore).
"""
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import gspread

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1uN_5lvCCkT3dYMW4S45aP7DB4nFv7xb4JmvRHs9nrHA"
SIPARISLER_SHEET = "FlipaBit_Sepet"
KESINLESMIS_SHEET = "FlipaBit_Kesinlesmis"
HEADER = ["NO", "kullanıcı adı", "barkod", "ürün adı", "miktar", "birim fiyat", "satır toplamı", "tarih"]

# Render.com'un "Secret File" özelliği dosya adında '/' (alt klasör) kabul
# etmiyor - orada dosya proje köküne düz "excel-to-sheet.json" olarak
# yazılıyor. GOOGLE_CREDENTIALS_PATH env var'ı varsa onu kullan (Render),
# yoksa yerel geliştirmedeki credentials/ klasörüne düş.
CREDENTIALS_PATH = Path(os.environ.get("GOOGLE_CREDENTIALS_PATH") or (
    Path(__file__).resolve().parent.parent / "credentials" / "excel-to-sheet.json"
))

_client = None

# Nav'daki sepet rozeti için kullanıcı başına küçük önbellek (2026-09-24
# isteği) - her sayfa yüklemesinde Sheets'e ayrı bir istek atmamak için.
# Kullanıcının KENDİ sepet/ekle/sil/onayla aksiyonlarında anında doğru
# kalsın diye o an ayrıca güncelleniyor, TTL sadece diğer durumlar
# (başka sekme/cihaz, sheet elle düzenlenmiş) için güvenlik payı.
#
# ÖNCEDEN invalidate (cache'i tamamen silip bir sonraki okumada Sheets'ten
# yeniden çekmek) yapılıyordu - AJAX sepete-ekle akışında bu, kullanıcıya
# yanıt dönmeden önce EK bir Sheets isteği (list_pending, ~1-2sn) demekti
# ("sepete ekle çok uzun sürüyor" şikayeti, 2026-09-25). Artık sayı zaten
# bildiğimiz delta kadar yerinde (in-place) güncelleniyor, ekstra istek yok.
_CART_COUNT_TTL_SECONDS = 20
_cart_count_cache: dict[str, tuple[float, int]] = {}


def _bump_cart_count(username: str, delta: int) -> None:
    cached = _cart_count_cache.get(username)
    if cached:
        _cart_count_cache[username] = (cached[0], max(0, cached[1] + delta))
    # Önbellekte hiç yoksa dokunma - bir sonraki get_cart_count zaten taze çeker.


def _set_cart_count(username: str, count: int) -> None:
    _cart_count_cache[username] = (time.time(), count)


def get_cart_count(username: str) -> int:
    cached = _cart_count_cache.get(username)
    if cached and time.time() - cached[0] < _CART_COUNT_TTL_SECONDS:
        return cached[1]
    count = len(list_pending(username))
    _cart_count_cache[username] = (time.time(), count)
    return count


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        _client = gspread.service_account(filename=str(CREDENTIALS_PATH))
    return _client


def _worksheet(name: str):
    sh = _get_client().open_by_key(SPREADSHEET_ID)
    return sh.worksheet(name)


def ensure_worksheets() -> None:
    """FlipaBit_Sepet / FlipaBit_Kesinlesmis yoksa oluşturur, başlığı yazar.
    Var olan bir sekmeye dokunmaz (başlığı zaten doğruysa hiçbir şey yapmaz)."""
    sh = _get_client().open_by_key(SPREADSHEET_ID)
    existing = {ws.title for ws in sh.worksheets()}
    for name in (SIPARISLER_SHEET, KESINLESMIS_SHEET):
        if name not in existing:
            ws = sh.add_worksheet(title=name, rows=1000, cols=len(HEADER))
            ws.append_row(HEADER, value_input_option="USER_ENTERED")
            logger.info("order_writer: '%s' sekmesi oluşturuldu", name)


def _next_no(ws) -> int:
    """İlk kolondaki (NO) mevcut en büyük değerin bir fazlası. Boşsa 1."""
    col = ws.col_values(1)[1:]  # başlığı atla
    numbers = [int(v) for v in col if v.strip().isdigit()]
    return max(numbers, default=0) + 1


def _append_cart_items_sync(username: str, items: list[dict]) -> int:
    """items: [{"barcode": str, "title": str, "qty": int, "price": float}, ...].
    Her ürün için 'FlipaBit_Sepet'e bir satır ekler (satır toplamı = miktar × birim fiyat).
    Gerçek Sheets yazma kısmı - sepet sayacına DOKUNMAZ, onu çağıran (senkron
    veya arka plan) sürüm yönetir."""
    if not items:
        return 0
    ws = _worksheet(SIPARISLER_SHEET)
    now_str = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")
    next_no = _next_no(ws)

    rows = []
    for i, item in enumerate(items):
        line_total = round(item["qty"] * item["price"], 2)
        rows.append([
            next_no + i,
            username,
            f"'{item['barcode']}",  # basiyle zorla metin - onculu sifirlar (00..) sayi yuvarlamasinda kaybolmasin
            item["title"],
            item["qty"],
            item["price"],
            line_total,
            now_str,
        ])
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info("order_writer: %s icin %d satir Sepet'e eklendi", username, len(rows))
    return len(rows)


def append_cart_items(username: str, items: list[dict]) -> int:
    """Senkron sürüm - Sheets yazma bitene kadar bekler. /sepet gibi Sheets
    durumunu hemen bilmesi gereken yerler için."""
    sayi = _append_cart_items_sync(username, items)
    if sayi:
        _bump_cart_count(username, sayi)
    return sayi


def append_cart_items_async(username: str, items: list[dict]) -> None:
    """AJAX sepete-ekle için: sepet sayacı HEMEN (iyimser) güncellenir, gerçek
    Sheets yazma arka plan thread'ine bırakılır - çağıran taraf (app.py)
    kullanıcıyı Sheets'in cevap vermesini beklemeden yanıtlayabilir
    (kullanıcının 2026-09-25 isteği: 'sepete ekleme Sheets'e yine yazsın
    ama kullanıcıyı bekletmeden'). Arka planda yazma gerçekten başarısız
    olursa (nadiren) sayaç geri alınır ve loglanır; /sepet sayfası her
    zaman Sheets'in kendisini okuduğu için veri kaybı olmaz, sadece nav
    rozeti kısa süreliğine (birkaç saniye) yanlış olabilir."""
    if not items:
        return
    _bump_cart_count(username, len(items))

    def _arka_planda_yaz():
        try:
            _append_cart_items_sync(username, items)
        except Exception:
            logger.exception("order_writer: arka plan sepete ekleme başarısız (%s)", username)
            _bump_cart_count(username, -len(items))

    threading.Thread(target=_arka_planda_yaz, daemon=True).start()


def _parse_tr_float(value: str) -> float:
    """Sheet Türkçe yerel ayarlı (virgül ondalık) - gspread'in get_all_records()
    numericise'i bunu binlik ayırıcı sanıp '809,16' -> 80916 gibi YANLIŞ
    çeviriyor (canlı testte yakalandı, 2026-09-22). Bu yüzden list_pending
    get_all_values() (her zaman ham metin) kullanıp burada KENDİMİZ çeviriyoruz."""
    value = (value or "0").strip().replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return 0.0


BASVURU_SHEET = "basvuru"


def append_basvuru(adi: str, mail: str, telefon: str, adres: str, mesaj: str) -> None:
    """Yeni müşteri başvurusunu (şifresi olmayan, 'İletişime Geçin' formundan)
    orijinal Flipabit uygulamasının da kullandığı 'basvuru' sekmesine yazar
    (No, Adi, Mail, Telefon, Adres, Mesaj) - 2026-09-24'te zararsız bir test
    satırıyla bu sekmenin (Siparişler/Kesinlesmis'in aksine) güvenli
    olduğu doğrulandı, ayrı bir FlipaBit_ sekmesine gerek yok."""
    ws = _worksheet(BASVURU_SHEET)
    next_no = _next_no(ws)
    ws.append_row(
        [next_no, adi, mail, f"'{telefon}", adres, mesaj],
        value_input_option="USER_ENTERED",
    )
    logger.info("order_writer: yeni basvuru eklendi (No=%s, %s)", next_no, adi)


def list_pending(username: str) -> list[dict]:
    """Kullanıcının 'FlipaBit_Sepet'teki (henüz onaylanmamış) satırlarını
    döner - '/bekleyen-siparislerim' sayfası için. get_all_values() (ham
    metin) kullanır, get_all_records() DEĞİL - bkz. _parse_tr_float."""
    ws = _worksheet(SIPARISLER_SHEET)
    all_values = ws.get_all_values()
    if not all_values:
        return []
    header, data_rows = all_values[0], all_values[1:]

    result = []
    for row in data_rows:
        record = dict(zip(header, row))
        if record.get("kullanıcı adı", "").strip() != username.strip():
            continue
        record["miktar"] = int(_parse_tr_float(record.get("miktar")))
        record["birim fiyat"] = _parse_tr_float(record.get("birim fiyat"))
        record["satır toplamı"] = _parse_tr_float(record.get("satır toplamı"))
        result.append(record)
    return result


def _find_sepet_row(ws, username: str, no: str) -> tuple[int, dict] | None:
    """'FlipaBit_Sepet'te NO'su ve kullanıcı adı eşleşen satırı bulur, sheet
    satır numarası (1-indexed) + satır sözlüğünü döner. Kullanıcı adı da
    kontrol edilir ki biri başka bir kullanıcının sepet satırını
    değiştiremesin/silemesin."""
    all_values = ws.get_all_values()
    if not all_values:
        return None
    header, data_rows = all_values[0], all_values[1:]
    for offset, row in enumerate(data_rows, start=2):
        record = dict(zip(header, row))
        if record.get("NO", "").strip() == str(no).strip() and record.get("kullanıcı adı", "").strip() == username.strip():
            return offset, record
    return None


def remove_cart_item(username: str, no: str) -> bool:
    """Sepetteki (FlipaBit_Sepet) tek bir satırı siler. Kullanıcının
    2026-09-24 isteği: sepette ürün silme."""
    ws = _worksheet(SIPARISLER_SHEET)
    found = _find_sepet_row(ws, username, no)
    if not found:
        return False
    row_number, _ = found
    ws.delete_rows(row_number)
    _bump_cart_count(username, -1)
    logger.info("order_writer: %s icin NO=%s sepetten silindi", username, no)
    return True


def update_cart_item_qty(username: str, no: str, yeni_miktar: int) -> bool:
    """Sepetteki bir satırın miktarını (ve satır toplamını) günceller.
    Kullanıcının 2026-09-24 isteği: sepette adet düzeltme. Miktar çağıran
    tarafından (app.py) stok üst sınırına göre zaten kırpılmış olmalı."""
    ws = _worksheet(SIPARISLER_SHEET)
    found = _find_sepet_row(ws, username, no)
    if not found:
        return False
    row_number, record = found
    birim_fiyat = _parse_tr_float(record.get("birim fiyat"))
    yeni_toplam = round(yeni_miktar * birim_fiyat, 2)
    header = ws.row_values(1)
    miktar_col = header.index("miktar") + 1
    toplam_col = header.index("satır toplamı") + 1
    ws.update_cell(row_number, miktar_col, yeni_miktar)
    ws.update_cell(row_number, toplam_col, yeni_toplam)
    logger.info("order_writer: %s icin NO=%s miktar %s yapildi", username, no, yeni_miktar)
    return True


def list_confirmed(username: str) -> list[dict]:
    """Kullanıcının 'FlipaBit_Kesinlesmis'teki (daha önce onayladığı, teslim/
    işlem bekleyen) geçmiş siparişlerini döner - '/bekleyen-siparislerim'
    sayfası için (kullanıcının 2026-09-22 düzeltmesi: bu sayfa ŞU ANKİ
    sepeti değil, GEÇMİŞ onaylanmış siparişleri gösterir)."""
    ws = _worksheet(KESINLESMIS_SHEET)
    all_values = ws.get_all_values()
    if not all_values:
        return []
    header, data_rows = all_values[0], all_values[1:]

    result = []
    for row in data_rows:
        record = dict(zip(header, row))
        if record.get("kullanıcı adı", "").strip() != username.strip():
            continue
        record["miktar"] = int(_parse_tr_float(record.get("miktar")))
        record["birim fiyat"] = _parse_tr_float(record.get("birim fiyat"))
        record["satır toplamı"] = _parse_tr_float(record.get("satır toplamı"))
        result.append(record)
    return result


def confirm_order(username: str) -> int:
    """Kullanıcının 'Siparişler' sekmesindeki TÜM satırlarını 'Kesinlesmis'e
    taşır (ekler + kaynaktan siler). Taşınan satır sayısını döner."""
    ws_source = _worksheet(SIPARISLER_SHEET)
    ws_target = _worksheet(KESINLESMIS_SHEET)

    all_values = ws_source.get_all_values()
    if not all_values:
        return 0
    header, data_rows = all_values[0], all_values[1:]
    try:
        user_col = header.index("kullanıcı adı")
    except ValueError:
        user_col = 1

    matching_row_numbers = []  # 1-indexed sheet row numbers
    matching_rows = []
    for offset, row in enumerate(data_rows, start=2):
        if len(row) > user_col and row[user_col].strip() == username.strip():
            matching_row_numbers.append(offset)
            matching_rows.append(row)

    if not matching_rows:
        return 0

    ws_target.append_rows(matching_rows, value_input_option="USER_ENTERED")

    # Yukarıdan aşağı silersek satır numaraları kayar - en alttan yukarı sil.
    for row_number in sorted(matching_row_numbers, reverse=True):
        ws_source.delete_rows(row_number)

    _set_cart_count(username, 0)  # tum bekleyen satirlar tasindi, sepet artik bos
    logger.info("order_writer: %s icin %d satir Kesinlesmis'e tasindi", username, len(matching_rows))
    return len(matching_rows)
