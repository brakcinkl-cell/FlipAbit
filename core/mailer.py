"""Yeni müşteri başvurusu bildirimini ozgunaydin@hotmail.com'a e-posta olarak
gönderir (core/order_writer.py'nin 'basvuru' sekmesine yazmasıyla birlikte,
2026-09-24 kullanıcı isteği).

SMTP_USER/SMTP_PASSWORD ortam değişkenleri boşsa gönderim SESSİZCE atlanır
(loglanır) - form yine de sheet'e yazmaya devam eder, e-posta göndericiyi
sadece ek bir bildirim katmanı olarak görüyoruz, formun kendisini bloke
etmemeli.

Gönderen: ozgunaydintekstil@gmail.com (kullanıcının 2026-09-24 kararı,
başlangıçta ozgunaydin@hotmail.com düşünülmüştü). Gmail SMTP'si de normal
hesap şifresiyle çalışmıyor - SMTP_PASSWORD bir Gmail "Uygulama Şifresi"
(App Password, 2 Adımlı Doğrulama açıkken oluşturulur) olmalı.
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
BASVURU_ALICI = "ozgunaydin@hotmail.com"


def send_basvuru_bildirimi(adi: str, mail: str, telefon: str, adres: str, mesaj: str) -> bool:
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("mailer: SMTP_USER/SMTP_PASSWORD ayarlı değil, başvuru e-postası atlanıyor")
        return False

    govde = (
        f"Yeni bayi/müşteri başvurusu:\n\n"
        f"Firma/Ad: {adi}\n"
        f"E-posta: {mail}\n"
        f"Telefon: {telefon}\n"
        f"Adres: {adres}\n"
        f"Mesaj: {mesaj or '-'}\n"
    )
    msg = MIMEText(govde, _charset="utf-8")
    msg["Subject"] = f"Yeni Başvuru: {adi}"
    msg["From"] = SMTP_USER
    msg["To"] = BASVURU_ALICI

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [BASVURU_ALICI], msg.as_string())
        logger.info("mailer: basvuru bildirimi gonderildi (%s)", adi)
        return True
    except Exception:
        logger.exception("mailer: basvuru e-postasi gonderilemedi")
        return False
