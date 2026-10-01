"""Özdilek Toptan katalog/sipariş uygulaması - Flask.

Kimlik doğrulama: Firma adı + Müşteri Temsilcisi Adı (serbest metin) +
sabit şifre "Ozd123" (core/catalog_source.py'deki 'tanımlı müşteriler'
sadece OTOFILL için, şifre kontrolü için DEĞİL - kullanıcının 2026-09-22
talimatı). Oturum Flask session'da tutulur, "beni hatırla" localStorage
üzerinden istemci tarafında yapılır (bkz. templates/giris.html).

Fiyat: core.catalog_source her ürün için ÇARPANSIZ vadeli fiyatı döner.
Sağ üstteki isimsiz toggle açıksa (session['fiyat_x2']) görüntülenen VE
sepete eklenen fiyat ×2'ye katlanır (kullanıcının 2026-09-22 talimatı).
"""
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify

from core import catalog_source, mailer, order_writer

# Sunucu pythonw.exe (konsolsuz) ile çalıştığı için stderr'e giden loglar
# kayboluyordu - arka plandaki Sheets/mail hataları görünmez kalıyordu.
# Artık logs/app.log'a dönen dosya logu da var (2 MB x 5).
_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_dosya_handler = RotatingFileHandler(_LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
_dosya_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_dosya_handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Sabit bir varsayılan anahtar YOK: kaynak koddaki bir anahtarla herkes sahte
# oturum çerezi üretebilirdi. Ortam değişkeni yoksa her açılışta rastgele
# üretilir (oturumlar yeniden başlatmada düşer) ve yüksek sesle loglanır.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("FLASK_SECRET_KEY"):
    logger.error("FLASK_SECRET_KEY ayarlı değil - geçici rastgele anahtar kullanılıyor, oturumlar yeniden başlatmada düşecek")
# Kullanıcının 2026-09-23 talimatı: şifreyi bir kez giren tekrar girmek
# zorunda kalmasın - oturum tarayıcı kapansa/PC yeniden açılsa da 1 yıl kalıcı.
app.permanent_session_lifetime = timedelta(days=365)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Canlı site Cloudflare üzerinden HTTPS; Secure çerez http://localhost veya
# LAN IP'sinden yapılan testlerde gönderilmez (giriş "tutmaz"). Öyle bir test
# için FLIPABIT_HTTP_TEST=1 ile geçici olarak kapatılabilir.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLIPABIT_HTTP_TEST") != "1"

LOGIN_PASSWORD = "Ozd123"
# Resminin Cloudinary yükleme tarihi bu kadar gün içindeyse "Yeni" rozeti
# gösterilir ve kategori içinde başa alınır (kullanıcının 2026-09-23 isteği).
YENI_URUN_GUN_SAYISI = 21

# Arayüz çok dilliliği - kullanıcı isteği 2026-10-01 ("ingilizce ve rusça
# yapalım"). Ürün adı/açıklaması/kategori adı çevirileri Sheets'ten gelir
# (core/catalog_source.py'nin Product.baslik()/aciklama()/kategori_adi()
# metodları, core/ceviri_bakim.py'nin doldurduğu GOOGLETRANSLATE
# sütunlarından); BURADAKİ METINLER sözlüğü sadece sabit arayüz metinleri
# (buton, menü, mesaj) içindir, elle çevrilmiştir.
DIL_VARSAYILAN = "tr"
DIL_ADLARI = {"tr": "Türkçe", "en": "English", "ru": "Русский"}
DIL_BAYRAK = {"tr": "🇹🇷", "en": "🇬🇧", "ru": "🇷🇺"}

METINLER: dict[str, dict[str, str]] = {
    # --- nav (base.html) ---
    "nav_kategoriler": {"tr": "Kategoriler", "en": "Categories", "ru": "Категории"},
    "nav_urun_arama": {"tr": "Ürün Arama", "en": "Search", "ru": "Поиск"},
    "nav_bekleyen_1": {"tr": "Bekleyen", "en": "Pending", "ru": "Ожидающие"},
    "nav_bekleyen_2": {"tr": "siparişlerim", "en": "orders", "ru": "заказы"},
    "nav_girise_git": {"tr": "Girişe Git", "en": "Log In", "ru": "Войти"},
    "nav_sepete_git": {"tr": "Sepete Git", "en": "Cart", "ru": "Корзина"},
    "nav_fiyat_x2_baslik": {"tr": "Fiyatları ×2 göster", "en": "Show prices ×2", "ru": "Показать цены ×2"},
    # --- giriş ---
    "giris_firma_placeholder": {"tr": "Firma adı (veya müşteri kodu)", "en": "Company name (or customer code)", "ru": "Название компании (или код клиента)"},
    "giris_temsilci_placeholder": {"tr": "Müşteri Temsilcisi Adı", "en": "Sales Representative Name", "ru": "Имя торгового представителя"},
    "giris_sifre_placeholder": {"tr": "Şifre", "en": "Password", "ru": "Пароль"},
    "giris_kvkk": {"tr": "Gizlilik politikasını kabul ediyorum", "en": "I accept the privacy policy", "ru": "Я принимаю политику конфиденциальности"},
    "giris_urunleri_goruntule": {"tr": "Ürünleri Görüntüle", "en": "View Products", "ru": "Просмотреть товары"},
    "giris_buton": {"tr": "▶ Giriş", "en": "▶ Log In", "ru": "▶ Войти"},
    "gizlilik_link": {"tr": "Gizlilik Politikasına bakın", "en": "View Privacy Policy", "ru": "Политика конфиденциальности"},
    "hata_kvkk": {"tr": "Gizlilik politikasını kabul etmelisiniz.", "en": "You must accept the privacy policy.", "ru": "Вы должны принять политику конфиденциальности."},
    "hata_zorunlu_alan": {"tr": "Firma adı ve Müşteri Temsilcisi Adı zorunludur.", "en": "Company name and Sales Representative Name are required.", "ru": "Название компании и имя представителя обязательны."},
    "hata_sifre": {"tr": "Şifre hatalı.", "en": "Incorrect password.", "ru": "Неверный пароль."},
    # --- kategoriler ---
    "kategoriler_baslik": {"tr": "Kategoriler", "en": "Categories", "ru": "Категории"},
    "katalog_bos": {"tr": "Katalogda ürün bulunamadı.", "en": "No products found in the catalog.", "ru": "Товары в каталоге не найдены."},
    "kategori_ara_placeholder": {"tr": "Kategori ara...", "en": "Search categories...", "ru": "Поиск категорий..."},
    "urun_sayisi_etiket": {"tr": "ürün", "en": "products", "ru": "товаров"},
    "kategori_eslesme_yok": {"tr": "Aramanızla eşleşen kategori bulunamadı.", "en": "No categories match your search.", "ru": "Категории по запросу не найдены."},
    "kategori_diger": {"tr": "Diğer", "en": "Other", "ru": "Другое"},
    # --- kategori / ürün listesi ---
    "geri_kategoriler": {"tr": "← Kategoriler", "en": "← Categories", "ru": "← Категории"},
    "bu_kategoride_ara_placeholder": {"tr": "Bu kategoride ara...", "en": "Search in this category...", "ru": "Поиск в этой категории..."},
    "siralama": {"tr": "Sıralama", "en": "Sort", "ru": "Сортировка"},
    "ara_buton": {"tr": "Ara", "en": "Search", "ru": "Искать"},
    "kategoride_urun_yok": {"tr": "Bu kategoride ürün bulunamadı.", "en": "No products found in this category.", "ru": "Товары в этой категории не найдены."},
    "yeni_rozet": {"tr": "Yeni", "en": "New", "ru": "Новинка"},
    "resim_hazirlaniyor": {"tr": "Ürün Görseli Hazırlanıyor", "en": "Product Image Coming Soon", "ru": "Изображение товара готовится"},
    "sepete_ekle": {"tr": "Sepete Ekle", "en": "Add to Cart", "ru": "В корзину"},
    "urun_eslesme_yok": {"tr": "Aramanızla eşleşen ürün bulunamadı.", "en": "No products match your search.", "ru": "Товары по запросу не найдены."},
    "sirala_fiyat_artan": {"tr": "Fiyat: Düşükten Yükseğe", "en": "Price: Low to High", "ru": "Цена: по возрастанию"},
    "sirala_fiyat_azalan": {"tr": "Fiyat: Yüksekten Düşüğe", "en": "Price: High to Low", "ru": "Цена: по убыванию"},
    "sirala_isim_az": {"tr": "İsim: A-Z", "en": "Name: A-Z", "ru": "Название: А-Я"},
    "sirala_isim_za": {"tr": "İsim: Z-A", "en": "Name: Z-A", "ru": "Название: Я-А"},
    # --- ürün detay ---
    "geri": {"tr": "← Geri", "en": "← Back", "ru": "← Назад"},
    "fiyat_icin_giris": {"tr": "Fiyat için giriş yapın", "en": "Log in to see price", "ru": "Войдите, чтобы увидеть цену"},
    "barkod_etiket": {"tr": "Barkod:", "en": "Barcode:", "ru": "Штрихкод:"},
    "siparis_icin_giris_on": {"tr": "Sipariş verebilmek için ", "en": "Please ", "ru": "Чтобы оформить заказ, "},
    "siparis_icin_giris_link": {"tr": "giriş yapın", "en": "log in", "ru": "войдите"},
    "siparis_icin_giris_son": {"tr": ".", "en": " to place an order.", "ru": "."},
    # --- sepet / bekleyen ---
    "sepetim": {"tr": "Sepetim", "en": "My Cart", "ru": "Моя корзина"},
    "sepet_bos": {"tr": "Sepetinizde ürün yok.", "en": "Your cart is empty.", "ru": "Ваша корзина пуста."},
    "th_urun": {"tr": "Ürün", "en": "Product", "ru": "Товар"},
    "th_miktar": {"tr": "Miktar", "en": "Qty", "ru": "Кол-во"},
    "th_birim_fiyat": {"tr": "Birim Fiyat", "en": "Unit Price", "ru": "Цена за ед."},
    "th_satir_toplami": {"tr": "Satır Toplamı", "en": "Line Total", "ru": "Сумма по строке"},
    "th_tarih": {"tr": "Tarih", "en": "Date", "ru": "Дата"},
    "miktar_guncelle_baslik": {"tr": "Miktarı güncelle", "en": "Update quantity", "ru": "Обновить количество"},
    "sepetten_kaldir_baslik": {"tr": "Sepetten kaldır", "en": "Remove from cart", "ru": "Удалить из корзины"},
    "toplam_etiket": {"tr": "Toplam:", "en": "Total:", "ru": "Итого:"},
    "siparisi_onayla": {"tr": "Siparişi Onayla", "en": "Confirm Order", "ru": "Подтвердить заказ"},
    "siparis_onay_confirm": {"tr": "Siparişi onaylıyor musunuz? Bu işlem geri alınamaz.", "en": "Confirm this order? This cannot be undone.", "ru": "Подтвердить заказ? Это действие необратимо."},
    "bekleyen_siparislerim_baslik": {"tr": "Bekleyen Siparişlerim", "en": "My Pending Orders", "ru": "Мои ожидающие заказы"},
    "bekleyen_aciklama": {"tr": "Onayladığınız, henüz tarafımızca işleme alınmamış siparişleriniz.", "en": "Orders you've confirmed that haven't been processed by us yet.", "ru": "Подтверждённые вами заказы, ещё не обработанные нами."},
    "bekleyen_siparis_yok": {"tr": "Bekleyen siparişiniz yok.", "en": "You have no pending orders.", "ru": "У вас нет ожидающих заказов."},
    # --- arama ---
    "urun_arama_baslik": {"tr": "Ürün Arama", "en": "Search Products", "ru": "Поиск товаров"},
    "urun_arama_placeholder": {"tr": "Ürün adı veya barkod...", "en": "Product name or barcode...", "ru": "Название товара или штрихкод..."},
    "sonuc_bulunamadi": {"tr": "Sonuç bulunamadı.", "en": "No results found.", "ru": "Результаты не найдены."},
    "giris_uyari_on": {"tr": "Toptan fiyatları görmek ve sipariş verebilmek için lütfen ", "en": "To see wholesale prices and place orders, please ", "ru": "Чтобы увидеть оптовые цены и оформить заказ, пожалуйста "},
    "giris_uyari_giris_link": {"tr": "giriş yapın", "en": "log in", "ru": "войдите"},
    "giris_uyari_orta": {"tr": " ya da satıcımız olmak için başvurun.", "en": " or apply to become our dealer.", "ru": " или подайте заявку, чтобы стать нашим дилером."},
    "basvuru_yap_buton": {"tr": "🏢 Başvuru Yap", "en": "🏢 Apply Now", "ru": "🏢 Подать заявку"},
    # --- başvuru ---
    "basvuru_baslik": {"tr": "Bayilik Başvurusu", "en": "Dealership Application", "ru": "Заявка на дилерство"},
    "basvuru_uyari_basvuru_sayfasi": {"tr": "Toptan fiyatları görmek ve sipariş verebilmek için lütfen ", "en": "To see wholesale prices and place orders, please ", "ru": "Чтобы увидеть оптовые цены и оформить заказ, пожалуйста "},
    "basvuru_uyari_orta": {"tr": " ya da aşağıdaki formla satıcımız olmak için başvurun.", "en": " or apply below to become our dealer.", "ru": " или подайте заявку ниже, чтобы стать нашим дилером."},
    "basvuru_basarili": {"tr": "Başvurunuz alındı, en kısa sürede sizinle iletişime geçeceğiz.", "en": "Your application has been received, we'll contact you soon.", "ru": "Ваша заявка получена, мы скоро свяжемся с вами."},
    "basvuru_firma_placeholder": {"tr": "Firma / Ad Soyad", "en": "Company / Full Name", "ru": "Компания / ФИО"},
    "basvuru_email_placeholder": {"tr": "E-posta", "en": "Email", "ru": "Эл. почта"},
    "basvuru_telefon_placeholder": {"tr": "Telefon", "en": "Phone", "ru": "Телефон"},
    "basvuru_adres_placeholder": {"tr": "Adres", "en": "Address", "ru": "Адрес"},
    "basvuru_mesaj_placeholder": {"tr": "Mesajınız (opsiyonel)", "en": "Your message (optional)", "ru": "Ваше сообщение (необязательно)"},
    "basvuru_iletisim_buton": {"tr": "🏢 İletişime Geçin", "en": "🏢 Contact Us", "ru": "🏢 Связаться с нами"},
    "basvuru_hata_zorunlu": {"tr": "Firma adı, e-posta ve telefon zorunludur.", "en": "Company name, email, and phone are required.", "ru": "Название компании, эл. почта и телефон обязательны."},
    # --- flash/hata ---
    "sepete_eklendi_son": {"tr": "sepete eklendi.", "en": "added to cart.", "ru": "добавлено в корзину."},
    "hata_urun_bulunamadi": {"tr": "Ürün bulunamadı.", "en": "Product not found.", "ru": "Товар не найден."},
    "hata_sayfa_bulunamadi": {"tr": "Sayfa bulunamadı.", "en": "Page not found.", "ru": "Страница не найдена."},
    "hata_katalog_yuklenemedi": {"tr": "Ürün kataloğu şu an yüklenemiyor, lütfen biraz sonra tekrar deneyin.", "en": "The product catalog can't be loaded right now, please try again shortly.", "ru": "Каталог товаров сейчас недоступен, попробуйте позже."},
    "hata_sunucu": {"tr": "Beklenmedik bir hata oluştu, lütfen tekrar deneyin.", "en": "An unexpected error occurred, please try again.", "ru": "Произошла непредвиденная ошибка, попробуйте снова."},
}


def t(key: str) -> str:
    dil = session.get("dil", DIL_VARSAYILAN)
    metinler = METINLER.get(key)
    if not metinler:
        return key
    return metinler.get(dil) or metinler.get(DIL_VARSAYILAN) or key


def _dil() -> str:
    return session.get("dil", DIL_VARSAYILAN)


def _urun_eslesiyor(urun, q: str) -> bool:
    """Arama sorgusu, ürünün HANGİ DİLDE olursa olsun adıyla (TR/EN/RU) veya
    barkoduyla eşleşirse True - İngilizce/Rusça arayüzde kullanıcı kendi
    dilinde yazsa da ürünü bulabilsin (kullanıcı isteği 2026-10-01)."""
    if q in urun.barcode:
        return True
    if q in urun.title.lower():
        return True
    return any(q in ad.lower() for ad in urun.title_i18n.values())


def _yeni_mi(urun) -> bool:
    if not urun.resim_tarihi:
        return False
    return (datetime.now(timezone.utc) - urun.resim_tarihi).days <= YENI_URUN_GUN_SAYISI


def _effective_price(base_price: float) -> float:
    return round(base_price * 2, 2) if session.get("fiyat_x2") else round(base_price, 2)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("kullanici_adi"):
            return redirect(url_for("giris"))
        return view(*args, **kwargs)
    return wrapped


def _asset_url(filename: str) -> str:
    """CSS/JS dosyasının değişiklik zamanına göre ?v= ekler - tarayıcı eski
    stili önbellekten göstermesin diye (2026-09-24, 'Yeni' rozeti stili hiç
    uygulanmamış görünüyordu, sebep buydu)."""
    path = os.path.join(app.static_folder, filename)
    try:
        v = int(os.path.getmtime(path))
    except OSError:
        v = 0
    return url_for("static", filename=filename) + f"?v={v}"


@app.context_processor
def inject_globals():
    kullanici_adi = session.get("kullanici_adi")
    sepet_sayisi = order_writer.get_cart_count(kullanici_adi) if kullanici_adi else 0
    return {
        "kullanici_adi": kullanici_adi,
        "fiyat_x2": session.get("fiyat_x2", False),
        "asset_url": _asset_url,
        "sepet_sayisi": sepet_sayisi,
        "t": t,
        "dil": _dil(),
        "DIL_ADLARI": DIL_ADLARI,
        "DIL_BAYRAK": DIL_BAYRAK,
    }


@app.route("/dil/<kod>")
def dil_degistir(kod):
    """Arayüz dilini değiştirir (kullanıcı isteği 2026-10-01). Ürün/kategori
    çevirileri Sheets'ten (core/catalog_source.py), sabit arayüz metinleri
    METINLER sözlüğünden gelir. Oturum olmadan da çalışır (giriş/başvuru
    sayfalarında da dil seçilebilsin)."""
    if kod in DIL_ADLARI:
        session["dil"] = kod
        session.permanent = True
    return redirect(request.referrer or url_for("index"))


@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("kategoriler") if session.get("kullanici_adi") else url_for("giris"))


@app.route("/giris", methods=["GET", "POST"])
def giris():
    if request.method == "GET":
        return render_template("giris.html")

    firma_adi = (request.form.get("firma_adi") or "").strip()
    temsilci = (request.form.get("temsilci") or "").strip()
    sifre = (request.form.get("sifre") or "").strip()
    kvkk = request.form.get("kvkk") == "on"

    hata = None
    if not kvkk:
        hata = t("hata_kvkk")
    elif not firma_adi or not temsilci:
        hata = t("hata_zorunlu_alan")
    elif sifre != LOGIN_PASSWORD:
        hata = t("hata_sifre")

    if hata:
        return render_template("giris.html", hata=hata, firma_adi=firma_adi, temsilci=temsilci), 400

    session.permanent = True
    session["kullanici_adi"] = f"{firma_adi} / {temsilci}"
    session["firma_adi"] = firma_adi
    session["temsilci"] = temsilci
    return redirect(url_for("kategoriler"))


@app.route("/basvuru", methods=["GET", "POST"])
def basvuru():
    """Şifresi olmayan yeni müşterilerin başvuru formu - giriş sayfasındaki
    'Ürünleri Görüntüle' butonu artık arama sayfası yerine buraya götürüyor
    (kullanıcının 2026-09-24 kararı). Girişsiz erişilebilir."""
    if request.method == "GET":
        return render_template("basvuru.html")

    adi = (request.form.get("adi") or "").strip()
    mail = (request.form.get("mail") or "").strip()
    telefon = (request.form.get("telefon") or "").strip()
    adres = (request.form.get("adres") or "").strip()
    mesaj = (request.form.get("mesaj") or "").strip()

    if not adi or not mail or not telefon:
        return render_template(
            "basvuru.html", hata=t("basvuru_hata_zorunlu"),
            adi=adi, mail=mail, telefon=telefon, adres=adres, mesaj=mesaj,
        ), 400

    # Sheets yazma + mail gönderme arka planda olur, kullanıcı beklemez
    # (kullanıcının 2026-09-25 "yavaş süreçleri bul" talebi - bu route daha
    # önce ikisinin de bitmesini senkron bekliyordu, ~2-5sn).
    def _arka_planda_isle():
        try:
            order_writer.append_basvuru(adi, mail, telefon, adres, mesaj)
        except Exception:
            logger.exception("basvuru: sheet'e yazma başarısız")
        mailer.send_basvuru_bildirimi(adi, mail, telefon, adres, mesaj)

    threading.Thread(target=_arka_planda_isle, daemon=True).start()
    return render_template("basvuru.html", basarili=True)


@app.route("/.well-known/assetlinks.json")
def assetlinks():
    """Android TWA (Trusted Web Activity) için Digital Asset Links doğrulaması
    - bu domain'in com.ozdilektotan.app tarafından sahiplenildiğini kanıtlar.
    İki fingerprint listeleniyor:
    - Upload key (credentials/android_release.keystore, 207.aab'yi imzalayan GERÇEK
      keystore, 2026-09-23'te doğrulandı): sideload/test için, cihazda Play Store
      kurulumu yoksa kullanılan imza.
    - Play App Signing anahtarı (Play Console > Uygulama imzalama'dan 2026-09-24'te
      indirilen deployment_cert.der ile doğrulandı): Google'ın Play Store üzerinden
      dağıtırken GERÇEKTEN imzaladığı sertifika - bu olmadan TWA, uygulama Play'den
      kurulduktan sonra doğrulanamaz ve tam ekran yerine adres çubuklu açılır."""
    return jsonify([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "com.ozdilektotan.app",
            "sha256_cert_fingerprints": [
                "92:CA:C3:32:1E:FB:1E:04:82:56:74:1A:90:BF:3F:A2:48:D3:32:E9:ED:95:26:0F:AE:D2:20:1B:36:40:38:3D",
                "3F:57:9E:A7:C0:4A:C7:7E:C2:A4:6B:34:70:76:13:3A:5E:5C:38:7A:92:D8:5D:B7:E6:9A:50:91:81:12:10:4F",
            ],
        },
    }])


@app.route("/gizlilik-politikasi")
def gizlilik_politikasi():
    return render_template("gizlilik.html")


@app.route("/cikis")
def cikis():
    session.clear()
    return redirect(url_for("giris"))


# /api/musteri-kodu girişsiz çalışmak zorunda (giriş formunu doldurmak için)
# ama kısa sayısal kodlar sırayla denenerek tüm müşteri listesi dökülebilirdi.
# Basit bir IP başına dakikalık hız sınırı: normal kullanımda (bir kod yazılır,
# 400ms debounce) asla dolmaz, otomatik tarama ise takılır.
_HIZ_SINIRI_ISTEK = 30
_HIZ_SINIRI_SANIYE = 60
_hiz_kayitlari: dict[str, list[float]] = {}
_hiz_kilidi = threading.Lock()


def _istemci_ip() -> str:
    # Cloudflare Tunnel arkasındayız: remote_addr hep 127.0.0.1, gerçek IP başlıkta.
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"


def _hiz_siniri_asildi(anahtar: str) -> bool:
    simdi = time.time()
    with _hiz_kilidi:
        zamanlar = [t for t in _hiz_kayitlari.get(anahtar, []) if simdi - t < _HIZ_SINIRI_SANIYE]
        if len(zamanlar) >= _HIZ_SINIRI_ISTEK:
            _hiz_kayitlari[anahtar] = zamanlar
            return True
        zamanlar.append(simdi)
        _hiz_kayitlari[anahtar] = zamanlar
        # Sözlük sınırsız büyümesin - eski/boş IP'leri ara sıra temizle.
        if len(_hiz_kayitlari) > 5000:
            for ip in [ip for ip, ts in _hiz_kayitlari.items() if not ts or simdi - ts[-1] > _HIZ_SINIRI_SANIYE]:
                _hiz_kayitlari.pop(ip, None)
    return False


@app.route("/api/musteri-kodu/<kodu>")
def api_musteri_kodu(kodu):
    """Giriş ekranında 'Firma adı' alanına bir müşteri kodu (örn. '11..')
    yazılınca Firma adı + Müşteri Temsilcisi Adı'nı otomatik doldurmak için."""
    if _hiz_siniri_asildi(_istemci_ip()):
        return jsonify({"found": False, "hata": "cok fazla istek"}), 429
    customers = catalog_source.fetch_customers()
    musteri = customers.get(kodu.strip())
    if not musteri:
        return jsonify({"found": False})
    return jsonify({"found": True, "musteriadi": musteri.musteriadi, "temsilci": musteri.temsilci})


@app.route("/api/wix/kategoriler")
def api_wix_kategoriler():
    """www.ozgunaydin.net (Wix) sitesindeki Velo kodunun menüyü ve ana sayfa
    kategori ızgarasını doldurmak için okuduğu kategori ağacı. Kaynak:
    core/wix_sync.py'nin her senkronda yazdığı logs/wix_kategori_agaci.json
    (Sheets 'grup' sekmesi -> Wix koleksiyonları). Herkese açık, salt-okunur,
    kişisel veri içermez; Wix alan adından tarayıcı çağrısı için CORS açık."""
    from core import wix_kategori
    agac = wix_kategori.agac_oku()
    if agac is None:
        yanit = jsonify({"hata": "kategori agaci henuz olusmadi"})
        yanit.status_code = 503
    else:
        yanit = jsonify(agac)
        yanit.headers["Cache-Control"] = "public, max-age=300"
    yanit.headers["Access-Control-Allow-Origin"] = "*"
    return yanit


@app.route("/ayarlar/fiyat-x2", methods=["POST"])
def ayarlar_fiyat_x2():
    session["fiyat_x2"] = not session.get("fiyat_x2", False)
    return redirect(request.referrer or url_for("giris"))


def _kategorilere_gore_grupla(products):
    # Gruplama DAİMA Türkçe category_name ile (URL/filtre anahtarı değişmez,
    # dil değişince link kırılmasın); görüntülenen ad ayrıca dil'e göre alınır.
    kategoriler = {}
    for p in products:
        ad = p.category_name or t("kategori_diger")
        kategoriler.setdefault(ad, []).append(p)
    return dict(sorted(kategoriler.items(), key=lambda kv: kv[0]))


@app.route("/kategoriler")
@login_required
def kategoriler():
    products = catalog_source.fetch_catalog()
    gruplu = _kategorilere_gore_grupla(products)
    dil = _dil()
    kategori_listesi = [
        {"ad": ad, "ad_goster": urunler[0].kategori_adi(dil) if urunler else ad, "urun_sayisi": len(urunler)}
        for ad, urunler in gruplu.items()
    ]
    return render_template("kategoriler.html", kategoriler=kategori_listesi)


SIRALAMA_SECENEKLERI = {
    "fiyat_artan": ("sirala_fiyat_artan", lambda p: p.price, False),
    "fiyat_azalan": ("sirala_fiyat_azalan", lambda p: p.price, True),
    "isim_az": ("sirala_isim_az", lambda p: p.title.lower(), False),
    "isim_za": ("sirala_isim_za", lambda p: p.title.lower(), True),
}


@app.route("/kategori/<path:kategori_adi>")
@login_required
def kategori_urunleri(kategori_adi):
    q = (request.args.get("q") or "").strip().lower()
    sirala = request.args.get("sirala") or ""
    dil = _dil()
    products = catalog_source.fetch_catalog()
    urunler = [p for p in products if (p.category_name or t("kategori_diger")) == kategori_adi]
    kategori_baslik = urunler[0].kategori_adi(dil) if urunler else kategori_adi
    if q:
        urunler = [p for p in urunler if _urun_eslesiyor(p, q)]
    if sirala in SIRALAMA_SECENEKLERI:
        _, anahtar, tersten = SIRALAMA_SECENEKLERI[sirala]
        urunler = sorted(urunler, key=anahtar, reverse=tersten)
    else:
        # Varsayılan: resmi son 3 hafta içinde eklenen ürünler başa gelsin
        # (en yeni resim en üstte), geri kalanı mevcut sırasında kalsın
        # (kullanıcının 2026-09-23 isteği - stabil sort ile sağlanıyor).
        urunler = sorted(
            urunler,
            key=lambda p: (0, -p.resim_tarihi.timestamp()) if _yeni_mi(p) else (1, 0),
        )

    sonuc = [
        {
            "barkod": p.barcode,
            "baslik": p.baslik(dil),
            "fiyat": _effective_price(p.price),
            "stok": p.stock,
            "resim": p.images[0] if p.images else None,
            "yeni": _yeni_mi(p),
        }
        for p in urunler
    ]
    return render_template(
        "kategori.html", kategori_adi=kategori_adi, kategori_baslik=kategori_baslik, urunler=sonuc, arama=q,
        sirala=sirala, siralama_secenekleri=SIRALAMA_SECENEKLERI,
    )


@app.route("/urun/<barkod>")
def urun_detay(barkod):
    # Girişsiz de erişilebilir (görüntüleme) - sepete ekleme formu
    # template'te kullanici_adi yoksa gizleniyor.
    products = catalog_source.fetch_catalog()
    urun = next((p for p in products if p.barcode == barkod), None)
    if not urun:
        return render_template("hata.html", mesaj=t("hata_urun_bulunamadi")), 404
    return render_template(
        "urun.html",
        urun=urun,
        fiyat=_effective_price(urun.price),
    )


@app.route("/sepete-ekle", methods=["POST"])
@login_required
def sepete_ekle():
    barkod = request.form.get("barkod")
    try:
        miktar = int(request.form.get("miktar") or 0)
    except ValueError:
        miktar = 0

    products = catalog_source.fetch_catalog()
    urun = next((p for p in products if p.barcode == barkod), None)

    # Açık yönlendirme koruması: sadece site içi göreli yollar ("/..."),
    # "//evil.com" veya "https://..." gibi dış adresler kategorilere düşer.
    donus_url = request.form.get("donus_url") or ""
    if not donus_url.startswith("/") or donus_url.startswith("//"):
        donus_url = url_for("kategoriler")
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not urun or miktar <= 0:
        if ajax:
            return jsonify({"ok": False}), 400
        return redirect(donus_url)

    # Güvenlik: sipariş miktarı STOK dosyasındaki gerçek adedi aşamaz.
    miktar = min(miktar, urun.stock)

    # Sheets yazma arka planda olur, kullanıcı beklemez - sepet sayacı hemen
    # (iyimser) güncellenir (kullanıcının 2026-09-25 isteği, bkz. order_writer
    # .append_cart_items_async docstring'i). Sheet'e DAİMA Türkçe/kanonik ad
    # yazılır (işletme sahibi oradan okuyor) - görüntülenen/flash mesajdaki ad
    # ayrıca dile göre alınır.
    dil = _dil()
    order_writer.append_cart_items_async(session["kullanici_adi"], [{
        "barcode": urun.barcode,
        "title": urun.title,
        "qty": miktar,
        "price": _effective_price(urun.price),
    }])

    if ajax:
        # Ürün ızgarasından (kategori/arama) AJAX ile gelen istek - sayfa
        # yenilenmesin diye JSON dönülür (bkz. static/js/app.js).
        return jsonify({
            "ok": True,
            "urun_adi": urun.baslik(dil),
            "miktar": miktar,
            "sepet_sayisi": order_writer.get_cart_count(session["kullanici_adi"]),
        })

    flash(f"“{urun.baslik(dil)}” {t('sepete_eklendi_son')}")
    return redirect(donus_url)


@app.route("/sepet")
@login_required
def sepet():
    """Şu anki, HENÜZ ONAYLANMAMIŞ sepet (FlipaBit_Sepet) - nav'daki
    'Sepete Git'. Onayla burada -> FlipaBit_Kesinlesmis'e taşınır."""
    satirlar = order_writer.list_pending(session["kullanici_adi"])
    # Miktar düzenleme inputundaki max için güncel stoğu satıra ekle.
    if satirlar:
        products = catalog_source.fetch_catalog()
        stok_by_barkod = {p.barcode: p.stock for p in products}
        for s in satirlar:
            s["guncel_stok"] = stok_by_barkod.get(s.get("barkod", "").lstrip("'"), s["miktar"])
    toplam = round(sum(s["satır toplamı"] for s in satirlar), 2) if satirlar else 0
    return render_template("sepet.html", satirlar=satirlar, toplam=toplam)


@app.route("/onayla", methods=["POST"])
@login_required
def onayla():
    kullanici_adi = session["kullanici_adi"]
    # E-posta içeriği için satırları taşımadan (confirm_order) ÖNCE al.
    satirlar = order_writer.list_pending(kullanici_adi)
    toplam = round(sum(s["satır toplamı"] for s in satirlar), 2) if satirlar else 0

    # confirm_order SENKRON kalıyor - hemen ardından /sepet güncel (artık
    # boş) durumu okuyor, arka plana alınırsa yarış durumu olur. Mail ise
    # yan etki, kullanıcıyı bekletmeden arka planda gönderilir.
    order_writer.confirm_order(kullanici_adi)
    if satirlar:
        threading.Thread(
            target=mailer.send_siparis_onay_bildirimi,
            args=(kullanici_adi, satirlar, toplam),
            daemon=True,
        ).start()
    return redirect(url_for("sepet"))


@app.route("/sepet/sil", methods=["POST"])
@login_required
def sepet_sil():
    no = request.form.get("no")
    if no:
        order_writer.remove_cart_item(session["kullanici_adi"], no)
    return redirect(url_for("sepet"))


@app.route("/sepet/guncelle", methods=["POST"])
@login_required
def sepet_guncelle():
    no = request.form.get("no")
    barkod = request.form.get("barkod")
    try:
        miktar = int(request.form.get("miktar") or 0)
    except ValueError:
        miktar = 0

    if no and miktar > 0:
        # Güvenlik: guncellenen miktar da STOK dosyasindaki gercek adedi asamaz.
        products = catalog_source.fetch_catalog()
        urun = next((p for p in products if p.barcode == barkod), None)
        if urun:
            miktar = min(miktar, urun.stock)
        order_writer.update_cart_item_qty(session["kullanici_adi"], no, miktar)
    return redirect(url_for("sepet"))


@app.route("/bekleyen-siparislerim")
@login_required
def bekleyen_siparislerim():
    """Kullanıcının DAHA ÖNCE onayladığı, FlipaBit_Kesinlesmis'teki geçmiş
    siparişleri - nav'daki 'Bekleyen siparişlerim' (teslim/işlem bekleyen,
    şu anki sepet DEĞİL)."""
    satirlar = order_writer.list_confirmed(session["kullanici_adi"])
    toplam = round(sum(s["satır toplamı"] for s in satirlar), 2) if satirlar else 0
    return render_template("bekleyen.html", satirlar=satirlar, toplam=toplam)


@app.route("/urun-arama")
def urun_arama():
    # Girişsiz de erişilebilir ("Ürünleri Görüntüle" - giriş sayfasındaki
    # ikinci buton) - sepete ekleme formu template'te kullanici_adi yoksa
    # zaten gizleniyor.
    q = (request.args.get("q") or "").strip().lower()
    dil = _dil()
    products = catalog_source.fetch_catalog()
    if q:
        products = [p for p in products if _urun_eslesiyor(p, q)]
    sonuc = [
        {
            "barkod": p.barcode,
            "baslik": p.baslik(dil),
            "fiyat": _effective_price(p.price),
            "stok": p.stock,
            "resim": p.images[0] if p.images else None,
            "kategori": p.kategori_adi(dil),
        }
        for p in products[:200]
    ]
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("partial"):
        # Canlı arama (bkz. templates/arama.html'deki JS) - sadece kart
        # ızgarasını döner, tüm sayfayı değil.
        return render_template("_urun_kartlari.html", urunler=sonuc, arama=q)
    return render_template("arama.html", urunler=sonuc, arama=q)


@app.errorhandler(404)
def sayfa_bulunamadi(_e):
    return render_template("hata.html", mesaj=t("hata_sayfa_bulunamadi")), 404


@app.errorhandler(catalog_source.CatalogUnavailable)
def katalog_yok(e):
    logger.error("katalog kullanılamıyor: %s", e)
    return render_template("hata.html", mesaj=t("hata_katalog_yuklenemedi")), 503


@app.errorhandler(500)
def sunucu_hatasi(_e):
    return render_template("hata.html", mesaj=t("hata_sunucu")), 500


if __name__ == "__main__":
    # Üretimde debug=False ve gerçek bir WSGI sunucusu (waitress, Windows'ta
    # çalışır). Flask'ın geliştirme sunucusu + debug=True canlı sitede
    # Werkzeug'ın etkileşimli hata ayıklayıcısını (kaynak kod, ortam
    # değişkenleri, Python konsolu) internete açıyordu - 2026-09-27
    # güvenlik gözden geçirmesinde kaldırıldı. Otomatik yeniden yükleme
    # artık yok: kod değişince sunucu (FlipaBit-Sunucu görevi) yeniden
    # başlatılmalı.
    from waitress import serve

    catalog_source.start_background_refresh()
    order_writer.ensure_worksheets()
    serve(app, host="0.0.0.0", port=5300, threads=8)
