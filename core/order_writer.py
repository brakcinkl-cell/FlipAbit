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


def append_cart_items(username: str, items: list[dict]) -> int:
    """items: [{"barcode": str, "title": str, "qty": int, "price": float}, ...].
    Her ürün için 'FlipaBit_Sepet'e bir satır ekler (satır toplamı = miktar × birim fiyat)."""
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

    logger.info("order_writer: %s icin %d satir Kesinlesmis'e tasindi", username, len(matching_rows))
    return len(matching_rows)
