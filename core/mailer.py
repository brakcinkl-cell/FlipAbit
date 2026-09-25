"""Bildirim e-postalarını (yeni başvuru, kesinleşen sipariş) gönderir.

SMTP_USER/SMTP_PASSWORD ortam değişkenleri boşsa gönderim SESSİZCE atlanır
(loglanır) - form/sipariş akışı yine de sheet'e yazmaya devam eder, e-posta
göndericiyi sadece ek bir bildirim katmanı olarak görüyoruz, asıl işlemi
bloke etmemeli.

Gönderen VE alıcı: ozgunaydintekstil@gmail.com (kullanıcının 2026-09-25
kararı - tüm bildirimler bu adrese gitsin; daha önce alıcı ozgunaydin@
hotmail.com'du). Gmail SMTP'si normal hesap şifresiyle çalışmıyor -
SMTP_PASSWORD bir Gmail "Uygulama Şifresi" (App Password, 2 Adımlı
Doğrulama açıkken oluşturulur) olmalı.
"""
import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ALICI = "ozgunaydintekstil@gmail.com"


def _gonder(konu: str, govde: str) -> bool:
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("mailer: SMTP_USER/SMTP_PASSWORD ayarlı değil, e-posta atlanıyor (%s)", konu)
        return False

    msg = MIMEText(govde, _charset="utf-8")
    msg["Subject"] = konu
    msg["From"] = SMTP_USER
    msg["To"] = ALICI

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [ALICI], msg.as_string())
        logger.info("mailer: e-posta gonderildi (%s)", konu)
        return True
    except Exception:
        logger.exception("mailer: e-posta gonderilemedi (%s)", konu)
        return False


def send_basvuru_bildirimi(adi: str, mail: str, telefon: str, adres: str, mesaj: str) -> bool:
    govde = (
        f"Yeni bayi/müşteri başvurusu:\n\n"
        f"Firma/Ad: {adi}\n"
        f"E-posta: {mail}\n"
        f"Telefon: {telefon}\n"
        f"Adres: {adres}\n"
        f"Mesaj: {mesaj or '-'}\n"
    )
    return _gonder(f"Yeni Başvuru: {adi}", govde)


def send_siparis_onay_bildirimi(kullanici_adi: str, satirlar: list[dict], toplam: float) -> bool:
    """Bir müşteri sepetini onaylayıp siparişi kesinleştirdiğinde (core/
    order_writer.confirm_order ile birlikte, kullanıcının 2026-09-25
    isteği) gönderilir. `satirlar`, confirm_order'dan ÖNCE alınan
    list_pending() çıktısı olmalı (app.py'deki /onayla route'una bak)."""
    urun_satirlari = "\n".join(
        f"- {s.get('ürün adı')} | {s.get('miktar')} adet x {s.get('birim fiyat'):.2f} ₺ "
        f"= {s.get('satır toplamı'):.2f} ₺"
        for s in satirlar
    )
    govde = (
        f"Kesinleşen sipariş:\n\n"
        f"Müşteri: {kullanici_adi}\n\n"
        f"{urun_satirlari}\n\n"
        f"Toplam: {toplam:.2f} ₺\n"
    )
    return _gonder(f"Yeni Sipariş: {kullanici_adi}", govde)
