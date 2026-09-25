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
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify

from core import catalog_source, mailer, order_writer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-degistir")
# Kullanıcının 2026-09-23 talimatı: şifreyi bir kez giren tekrar girmek
# zorunda kalmasın - oturum tarayıcı kapansa/PC yeniden açılsa da 1 yıl kalıcı.
app.permanent_session_lifetime = timedelta(days=365)

LOGIN_PASSWORD = "Ozd123"
# Resminin Cloudinary yükleme tarihi bu kadar gün içindeyse "Yeni" rozeti
# gösterilir ve kategori içinde başa alınır (kullanıcının 2026-09-23 isteği).
YENI_URUN_GUN_SAYISI = 21


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
    }


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
        hata = "Gizlilik politikasını kabul etmelisiniz."
    elif not firma_adi or not temsilci:
        hata = "Firma adı ve Müşteri Temsilcisi Adı zorunludur."
    elif sifre != LOGIN_PASSWORD:
        hata = "Şifre hatalı."

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
            "basvuru.html", hata="Firma adı, e-posta ve telefon zorunludur.",
            adi=adi, mail=mail, telefon=telefon, adres=adres, mesaj=mesaj,
        ), 400

    order_writer.append_basvuru(adi, mail, telefon, adres, mesaj)
    mailer.send_basvuru_bildirimi(adi, mail, telefon, adres, mesaj)
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


@app.route("/api/musteri-kodu/<kodu>")
def api_musteri_kodu(kodu):
    """Giriş ekranında 'Firma adı' alanına bir müşteri kodu (örn. '11..')
    yazılınca Firma adı + Müşteri Temsilcisi Adı'nı otomatik doldurmak için."""
    customers = catalog_source.fetch_customers()
    musteri = customers.get(kodu.strip())
    if not musteri:
        return jsonify({"found": False})
    return jsonify({"found": True, "musteriadi": musteri.musteriadi, "temsilci": musteri.temsilci})


@app.route("/ayarlar/fiyat-x2", methods=["POST"])
def ayarlar_fiyat_x2():
    session["fiyat_x2"] = not session.get("fiyat_x2", False)
    return redirect(request.referrer or url_for("giris"))


def _kategorilere_gore_grupla(products):
    kategoriler = {}
    for p in products:
        ad = p.category_name or "Diğer"
        kategoriler.setdefault(ad, []).append(p)
    return dict(sorted(kategoriler.items(), key=lambda kv: kv[0]))


@app.route("/kategoriler")
@login_required
def kategoriler():
    products = catalog_source.fetch_catalog()
    gruplu = _kategorilere_gore_grupla(products)
    kategori_listesi = [
        {"ad": ad, "urun_sayisi": len(urunler)}
        for ad, urunler in gruplu.items()
    ]
    return render_template("kategoriler.html", kategoriler=kategori_listesi)


SIRALAMA_SECENEKLERI = {
    "fiyat_artan": ("Fiyat: Düşükten Yükseğe", lambda p: p.price, False),
    "fiyat_azalan": ("Fiyat: Yüksekten Düşüğe", lambda p: p.price, True),
    "isim_az": ("İsim: A-Z", lambda p: p.title.lower(), False),
    "isim_za": ("İsim: Z-A", lambda p: p.title.lower(), True),
}


@app.route("/kategori/<path:kategori_adi>")
@login_required
def kategori_urunleri(kategori_adi):
    q = (request.args.get("q") or "").strip().lower()
    sirala = request.args.get("sirala") or ""
    products = catalog_source.fetch_catalog()
    urunler = [p for p in products if (p.category_name or "Diğer") == kategori_adi]
    if q:
        urunler = [p for p in urunler if q in p.title.lower() or q in p.barcode]
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
            "baslik": p.title,
            "fiyat": _effective_price(p.price),
            "stok": p.stock,
            "resim": p.images[0] if p.images else None,
            "yeni": _yeni_mi(p),
        }
        for p in urunler
    ]
    return render_template(
        "kategori.html", kategori_adi=kategori_adi, urunler=sonuc, arama=q,
        sirala=sirala, siralama_secenekleri=SIRALAMA_SECENEKLERI,
    )


@app.route("/urun/<barkod>")
def urun_detay(barkod):
    # Girişsiz de erişilebilir (görüntüleme) - sepete ekleme formu
    # template'te kullanici_adi yoksa gizleniyor.
    products = catalog_source.fetch_catalog()
    urun = next((p for p in products if p.barcode == barkod), None)
    if not urun:
        return render_template("hata.html", mesaj="Ürün bulunamadı."), 404
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

    donus_url = request.form.get("donus_url") or url_for("kategoriler")
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not urun or miktar <= 0:
        if ajax:
            return jsonify({"ok": False}), 400
        return redirect(donus_url)

    # Güvenlik: sipariş miktarı STOK dosyasındaki gerçek adedi aşamaz.
    miktar = min(miktar, urun.stock)

    order_writer.ensure_worksheets()
    order_writer.append_cart_items(session["kullanici_adi"], [{
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
            "urun_adi": urun.title,
            "miktar": miktar,
            "sepet_sayisi": order_writer.get_cart_count(session["kullanici_adi"]),
        })

    flash(f"“{urun.title}” sepete eklendi.")
    return redirect(donus_url)


@app.route("/sepet")
@login_required
def sepet():
    """Şu anki, HENÜZ ONAYLANMAMIŞ sepet (FlipaBit_Sepet) - nav'daki
    'Sepete Git'. Onayla burada -> FlipaBit_Kesinlesmis'e taşınır."""
    order_writer.ensure_worksheets()
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
    order_writer.confirm_order(session["kullanici_adi"])
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
    order_writer.ensure_worksheets()
    satirlar = order_writer.list_confirmed(session["kullanici_adi"])
    toplam = round(sum(s["satır toplamı"] for s in satirlar), 2) if satirlar else 0
    return render_template("bekleyen.html", satirlar=satirlar, toplam=toplam)


@app.route("/urun-arama")
def urun_arama():
    # Girişsiz de erişilebilir ("Ürünleri Görüntüle" - giriş sayfasındaki
    # ikinci buton) - sepete ekleme formu template'te kullanici_adi yoksa
    # zaten gizleniyor.
    q = (request.args.get("q") or "").strip().lower()
    products = catalog_source.fetch_catalog()
    if q:
        products = [p for p in products if q in p.title.lower() or q in p.barcode]
    sonuc = [
        {
            "barkod": p.barcode,
            "baslik": p.title,
            "fiyat": _effective_price(p.price),
            "stok": p.stock,
            "resim": p.images[0] if p.images else None,
            "kategori": p.category_name,
        }
        for p in products[:200]
    ]
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("partial"):
        # Canlı arama (bkz. templates/arama.html'deki JS) - sadece kart
        # ızgarasını döner, tüm sayfayı değil.
        return render_template("_urun_kartlari.html", urunler=sonuc, arama=q)
    return render_template("arama.html", urunler=sonuc, arama=q)


if __name__ == "__main__":
    # debug=True reloader iki süreç açar (izleyici + gerçek işçi) - arka plan
    # tazelemeyi sadece gerçek işçide başlat, izleyicide değil.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        catalog_source.start_background_refresh()
    app.run(host="0.0.0.0", debug=True, port=5300)
