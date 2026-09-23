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
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for, jsonify

from core import catalog_source, order_writer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-degistir")

LOGIN_PASSWORD = "Ozd123"


def _effective_price(base_price: float) -> float:
    return round(base_price * 2, 2) if session.get("fiyat_x2") else round(base_price, 2)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("kullanici_adi"):
            return redirect(url_for("giris"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_globals():
    return {
        "kullanici_adi": session.get("kullanici_adi"),
        "fiyat_x2": session.get("fiyat_x2", False),
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

    session["kullanici_adi"] = f"{firma_adi} / {temsilci}"
    session["firma_adi"] = firma_adi
    session["temsilci"] = temsilci
    return redirect(url_for("kategoriler"))


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
        {"ad": ad, "urun_sayisi": len(urunler), "kapak_resim": (urunler[0].images[0] if urunler[0].images else None)}
        for ad, urunler in gruplu.items()
    ]
    return render_template("kategoriler.html", kategoriler=kategori_listesi)


@app.route("/kategori/<path:kategori_adi>")
@login_required
def kategori_urunleri(kategori_adi):
    q = (request.args.get("q") or "").strip().lower()
    products = catalog_source.fetch_catalog()
    urunler = [p for p in products if (p.category_name or "Diğer") == kategori_adi]
    if q:
        urunler = [p for p in urunler if q in p.title.lower() or q in p.barcode]

    sonuc = [
        {
            "barkod": p.barcode,
            "baslik": p.title,
            "fiyat": _effective_price(p.price),
            "stok": p.stock,
            "resim": p.images[0] if p.images else None,
        }
        for p in urunler
    ]
    return render_template("kategori.html", kategori_adi=kategori_adi, urunler=sonuc, arama=q)


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

    if not urun or miktar <= 0:
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
    return redirect(donus_url)


@app.route("/sepet")
@login_required
def sepet():
    """Şu anki, HENÜZ ONAYLANMAMIŞ sepet (FlipaBit_Sepet) - nav'daki
    'Sepete Git'. Onayla burada -> FlipaBit_Kesinlesmis'e taşınır."""
    order_writer.ensure_worksheets()
    satirlar = order_writer.list_pending(session["kullanici_adi"])
    toplam = round(sum(s["satır toplamı"] for s in satirlar), 2) if satirlar else 0
    return render_template("sepet.html", satirlar=satirlar, toplam=toplam)


@app.route("/onayla", methods=["POST"])
@login_required
def onayla():
    order_writer.confirm_order(session["kullanici_adi"])
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
    return render_template("arama.html", urunler=sonuc, arama=q)


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5300)
