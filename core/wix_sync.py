"""Google Sheets kataloğunu (FlipaBit'in okuduğu AYNI kaynak) Wix Stores'a
(www.ozgunaydin.net, Katalog V1) aktarır - kullanıcının 2026-09-29 isteği:
"Wix'teki ürünler kısmını uygulamadaki aynı mantıkla revize edelim, canlı
senkron".

VARSAYILAN KURU ÇALIŞMA (dry-run): Wix'e HİÇBİR ŞEY YAZMAZ, sadece neyin
değişeceğini hesaplayıp raporlar (logs/wix_sync_plan.json + özet). Gerçek
yazma yalnızca `--uygula` ile.

Mantık (uygulamayla aynı, bkz. core/catalog_source.py):
- Satılabilir = STOK MİKTARI >= 10 VE fiyat > 0. Satılabilir olanlar Wix'te
  görünür + güncel fiyat + güncel stok; olmayanlar GİZLENİR (silinmez -
  Google'da indeksli sayfalar ve sipariş geçmişi bozulmasın).
- Eşleştirme anahtarı: Wix SKU == STOK BARKOD (2026-09-29'da doğrulandı,
  2184 Wix ürününün hepsinde SKU var, STOK'ta olmayan yok).
- Fiyat sütunu FIYAT_SUTUNU ile seçilir. Wix'e zamanında PEŞİN fiyat
  yüklenmiş (Wix fiyatı ≈ vadeli × 0.91, iki üründe peşin sütununa birebir
  eşit) - hangisiyle devam edileceği kullanıcı kararı.
- Görsel: Wix'te hiç görsel yoksa ve Sheets'te varsa eklenir; mevcut görsel
  asla değiştirilmez. Açıklama: Sheets'te varsa yazılır, yoksa Wix'teki kalır.
- Kategori: Sheets kategorisi -> Wix koleksiyonu (KOLEKSIYON_ESLEME);
  ürün ilgili koleksiyona EKLENİR, başka koleksiyonlardan çıkarılmaz.
  Eşleşmeyen kategoriler raporlanır, koleksiyon otomatik OLUŞTURULMAZ.
- Çift SKU'lu Wix ürünlerinde ilk kayıt tutulur, fazlası gizlenir.

Wix Katalog V1 REST (2026-09-29, dokümandan):
  query   POST  /stores/v1/products/query
  update  PATCH /stores/v1/products/{id}            product.{name,priceData.price,visible,description}
  create  POST  /stores/v1/products                 product.{name,productType,priceData.price,costAndProfitData.itemCost,sku,visible,description}
  media   POST  /stores/v1/products/{id}/media      media[{url}]
  stok    PATCH /stores/v2/inventoryItems/product/{productId}
          inventoryItem.{productId,trackQuantity,variants[{variantId,quantity,inStock}]}
          (trackQuantity HER çağrıda açıkça verilmeli, yoksa false sayılıp
          ürünün stok takibi kapanır - doküman uyarısı)
  kolek.  POST  /stores/v1/collections/{id}/productIds   productIds[] (<=1000)
Site V1 kullanıyor - V3 uç noktaları 428 CATALOG_V1_SITE_CALLING_CATALOG_V3_API
döner, bu kod V3'e taşınırsa baştan yazılmalı.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import catalog_source as cs  # noqa: E402

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = ROOT / "credentials" / "wix_api.json"
PLAN_PATH = ROOT / "logs" / "wix_sync_plan.json"

BASE = "https://www.wixapis.com"
VARSAYILAN_VARYANT_ID = "00000000-0000-0000-0000-000000000000"
ISTEK_ARASI_SN = 0.15  # Wix rate limit'e nazik davran

# "vadeli" -> VADELİ FİYAT KDV DAHİL (uygulamayla aynı), "pesin" -> PEŞİN FİYAT KDV DAHİL
FIYAT_SUTUNU = "vadeli"
AD_GUNCELLE = True          # Wix ürün adını Sheets'teki (uygulamadaki) başlıkla değiştir
ACIKLAMA_GUNCELLE = True    # Sheets'te açıklama varsa Wix'e yaz
GORSEL_EKLE = True          # Wix'te görsel yoksa Sheets'tekini ekle
KOLEKSIYONA_EKLE = True     # Ürünü eşleşen koleksiyona ekle

# Sheets kategorisi -> Wix koleksiyon adı (2026-09-29'da Wix'te var olan 80
# koleksiyondan seçildi). None = emin olunamadı / karşılığı yok -> raporlanır.
KOLEKSIYON_ESLEME: dict[str, str | None] = {
    "ALEZ": "BED UNDERCOVER",
    "BATTANIYE VE SETLERİ CIFT KISILIK": "BLANKET AND SETS DOUBLE SIZE",
    "BATTANIYE VE SETLERİ TEK KISILIK": "BLANKET AND SETS SINGLE SIZE",
    "BATTANİYELİ NEVRESİM TAKIMI ÇİFT KİŞİLİK": "DUVET COVER SETS (WITH BEDSPREADS,BLANKETS ETC.)",
    "BEBEK BANYO SETI": "BABY BATH SET",
    "BEBEK BATTANIYESI": "BABY BLANKET",
    "BEBEK NEVRESIM TAKIMI": "BABY PREMIUM DUVET COVER SET",
    "BEBEK PELERIN": "BABY TOWEL HOOD",
    "BEBEK YASTIK": "BABY PILLOW",
    "BEBEK YORGAN": "BABY DUVET",
    "CEYIZ SETI": "WEDDING SET",
    "FITTED CARSAF SETI 100*200": "FITTED SHEET SET 100*200",
    "FITTED CARSAF SETI 120*200": "FITTED SHEET SET 120*200",
    "FITTED CARSAF SETI 160*200": "FITTED SHEET SET 160*200",
    "FITTED CARSAF SETI 180*200": "FITTED SHEET SET 180*200",
    "HAVLU - AILE SETI (BORNOZ TAKIMI)": "BATHROBE SET FOR FAMILY",
    "HAVLU - AYAK HAVLUSU": "BATH FLOOR FOOT TOWEL",
    "HAVLU - BANYO": "TOWEL (BATH SIZE)",
    "HAVLU - BEST SET (HAMAM TAKIMI)": "BATH AND HEAD TOWEL SET FOR FAMILY",
    "HAVLU - BORNOZ VE SETLERİ": "BATHROBES AND SETS",
    "HAVLU - COCUK BORNOZ VE HAVLU SETLERİ": "BATHROPES AND HEAD TOWEL SETS FOR KIDS",
    "HAVLU - HAVLULAR": "TOWELS",
    "HAVLU - PECETE": "TOWEL (SMALL SIZE)",
    "HAVLU - PLAJ": "TOWEL (BEACH SIZE)",
    "KESE": "SHOWER GLOVE",
    "KLOZET TAKIMI": "BATH RUG SET",
    "KOLTUK ORTUSU": "SOFA COVER",
    "KUTULU HAVLU": "TOWELS BOXED",
    "MASA ORTUSU": "TABLE CLOTH",
    "MUTFAK ONLUGU": "KITCHEN APRON",
    "NEVRESIM TAKIMI - EXCLUSIVE CIFT KISILIK (SATEN VB.)": "DUVET COVER SET-EXCLUSIVE DOUBLE SIZE (SATIN ETC.)",
    "NEVRESIM TAKIMI - EXCLUSIVE TEK KISILIK (SATEN VB.)": "DUVET COVER SET-EXCLUSIVE SINGLE SIZE (SATIN ETC.)",
    "NEVRESIM TAKIMI - KAPITONELI CIFT KISILIK": None,   # Wix'te "kapitoneli" karşılığı belirsiz (DELUXE?)
    "NEVRESIM TAKIMI - KAPITONELI TEK KISILIK": None,
    "NEVRESIM TAKIMI - POLYCOTTON TEK KİŞİLİK (NEV)": "DUVET COVER SET - POLYCOTTON SINGLE SIZE",
    "NEVRESIM TAKIMI - POLYCOTTON ÇİFT KİŞİLİK (NEV)": "DUVET COVER SET - POLYCOTTON DOUBLE SIZE",
    "NEVRESIM TAKIMI - RANFORCE BATTAL BOY": "DUVET COVER SET - RANFORCE KING SIZE",
    "NEVRESIM TAKIMI - RANFORCE CIFT KISILIK": "DUVET COVER SET - RANFORCE DOUBLE SIZE",
    "NEVRESIM TAKIMI - RANFORCE TEK KISILIK": "DUVET COVER SET - RANFORCE SINGLE SIZE",
    "PENYE LASTİKLİ ÇARŞAF": "JERSEY FABRIC FITTED SHEET",
    "PIKE TAKIMI CIFT KISILIK": "PIQUE SET DOUBLE SIZE",
    "PIKE TAKIMI TEK KISILIK": "PIQUE SET SINGLE SIZE",
    "PİKELİ NEVRESİM TAKIMI ÇİFT KİŞİLİK": "DUVET COVER SETS (WITH BEDSPREADS,BLANKETS ETC.)",
    "TEK PIKE CIFT KISILIK": "ONE PIECE PIQUE DOUBLE SIZE",
    "TEK PIKE TEK KISILIK": "ONE PIECE PIQUE SINGLE SIZE",
    "TEK YASTIK KILIFI": "ONE PIECE PILLOW CASE",
    "TERLIK": "TOWEL SLIPPER",
    "UYKU SETI - POLYCOTTON CIFT KISILIK (NEV)": "COMFORTER & SHEET SET- POLYCOTTON DOUBLE SIZE",
    "UYKU SETI - POLYCOTTON TEK KISILIK (NEV)": "COMFORTER & SHEET SET - POLYCOTTON SINGLE SIZE",
    "UYKU SETI - RANFORCE CIFT KISILIK": "COMFORTER & SHEET SET- RANFORCE DOUBLE SIZE",
    "UYKU SETI - RANFORCE TEK KISILIK": "COMFORTER & SHEET SET- RANFORCE SİNGLE SIZE",
    "YASTIK": "PILLOW GROUP",
    "YATAK ORTUSU CIFT KISILIK": "BEDSPREAD DOUBLE SIZE",
    "YATAK ORTUSU TEK KISILIK": "BEDSPREAD SINGLE SIZE",
    "YATAK SETI - RANFORCE CIFT KISILIK": "COMPLETE BEDDİNG SET - RANFORCE DOUBLE SIZE",
    "YATAK SETI - RANFORCE TEK KISILIK": "COMPLETE BEDDİNG SET - RANFORCE SINGLE SIZE",
    "YATAK ÖRTÜLÜ NEVRESİM TAKIMI ÇİFT KİŞİLİK": "DUVET COVER SETS (WITH BEDSPREADS,BLANKETS ETC.)",
    "YORGAN CIFT KISILIK": "DUVET DOUBLE SIZE",
    "YORGAN TEK KISILIK": "DUVET SINGLE SIZE",
}


# ---------------------------------------------------------------- Wix istemcisi
class WixClient:
    def __init__(self, uygula: bool):
        c = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
        self.headers = {
            "Authorization": c["api_key"],
            "wix-site-id": c["site_id"],
            "Content-Type": "application/json",
        }
        self.uygula = uygula
        self.yazma_sayisi = 0

    def _istek(self, method: str, path: str, body: dict | None = None, yazma: bool = True) -> dict:
        if yazma and not self.uygula:
            raise RuntimeError("kuru çalışmada yazma isteği çağrıldı - programlama hatası")
        for deneme in range(3):
            r = requests.request(method, BASE + path, headers=self.headers, json=body, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (deneme + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Wix {method} {path} -> {r.status_code}: {r.text[:300]}")
            if yazma:
                self.yazma_sayisi += 1
                time.sleep(ISTEK_ARASI_SN)
            return r.json() if r.text else {}
        raise RuntimeError(f"Wix {method} {path} -> {r.status_code} (3 deneme): {r.text[:300]}")

    def tum_urunler(self) -> list[dict]:
        urunler, offset = [], 0
        while True:
            d = self._istek("POST", "/stores/v1/products/query",
                            {"query": {"paging": {"limit": 100, "offset": offset}}, "includeVariants": False}, yazma=False)
            sayfa = d.get("products", [])
            urunler.extend(sayfa)
            offset += len(sayfa)
            if not sayfa or offset >= (d.get("totalResults") or 0):
                return urunler

    def tum_koleksiyonlar(self) -> list[dict]:
        kols, offset = [], 0
        while True:
            d = self._istek("POST", "/stores/v1/collections/query",
                            {"query": {"paging": {"limit": 100, "offset": offset}}}, yazma=False)
            sayfa = d.get("collections", [])
            kols.extend(sayfa)
            offset += len(sayfa)
            if not sayfa or offset >= (d.get("totalResults") or 0):
                return kols

    def urun_guncelle(self, urun_id: str, alanlar: dict) -> None:
        self._istek("PATCH", f"/stores/v1/products/{urun_id}", {"product": alanlar})

    def urun_olustur(self, alanlar: dict) -> str:
        d = self._istek("POST", "/stores/v1/products", {"product": alanlar})
        return d["product"]["id"]

    def stok_yaz(self, urun_id: str, miktar: int) -> None:
        self._istek("PATCH", f"/stores/v2/inventoryItems/product/{urun_id}", {"inventoryItem": {
            "productId": urun_id,
            "trackQuantity": True,
            "variants": [{"variantId": VARSAYILAN_VARYANT_ID, "quantity": max(0, int(miktar)), "inStock": miktar > 0}],
        }})

    def gorsel_ekle(self, urun_id: str, urls: list[str]) -> None:
        self._istek("POST", f"/stores/v1/products/{urun_id}/media", {"media": [{"url": u} for u in urls[:50]]})

    def koleksiyona_ekle(self, koleksiyon_id: str, urun_idleri: list[str]) -> None:
        for i in range(0, len(urun_idleri), 1000):
            self._istek("POST", f"/stores/v1/collections/{koleksiyon_id}/productIds", {"productIds": urun_idleri[i:i + 1000]})


# ------------------------------------------------------------------- Plan
@dataclass
class Plan:
    guncelle: list[dict] = field(default_factory=list)      # mevcut, satılabilir: fiyat/ad/görünürlük/açıklama farkları
    gizle: list[dict] = field(default_factory=list)         # mevcut ama satılamaz -> visible False
    olustur: list[dict] = field(default_factory=list)       # Sheets'te satılabilir, Wix'te yok
    stok_yaz: list[dict] = field(default_factory=list)      # miktar farklı olanlar
    gorsel_ekle: list[dict] = field(default_factory=list)   # Wix görselsiz, Sheets görselli
    koleksiyon_ekle: dict[str, list[str]] = field(default_factory=dict)  # koleksiyon_id -> [urun_id]
    cift_sku_gizle: list[dict] = field(default_factory=list)
    eslesmeyen_kategori: dict[str, int] = field(default_factory=dict)
    fiyat_sutunu: str = FIYAT_SUTUNU

    def ozet(self) -> dict:
        return {
            "fiyat_sutunu": self.fiyat_sutunu,
            "guncelle": len(self.guncelle),
            "  fiyat_degisen": sum(1 for x in self.guncelle if "priceData" in x["alanlar"]),
            "  ad_degisen": sum(1 for x in self.guncelle if "name" in x["alanlar"]),
            "  gorunur_yapilan": sum(1 for x in self.guncelle if x["alanlar"].get("visible") is True),
            "  aciklama_yazilan": sum(1 for x in self.guncelle if "description" in x["alanlar"]),
            "gizle": len(self.gizle),
            "olustur": len(self.olustur),
            "stok_yaz": len(self.stok_yaz),
            "gorsel_ekle": len(self.gorsel_ekle),
            "koleksiyona_eklenecek_urun": sum(len(v) for v in self.koleksiyon_ekle.values()),
            "cift_sku_gizle": len(self.cift_sku_gizle),
            "eslesmeyen_kategori": self.eslesmeyen_kategori,
        }


def _pesin_fiyatlar() -> dict[str, float]:
    sonuc = {}
    for row in cs._fetch_csv_rows(cs.STOK_URL):
        b = (row.get("BARKOD") or "").strip()
        f = cs._parse_float(row.get("PEŞİN FİYAT KDV DAHİL"))
        if b and f:
            sonuc[b] = round(f, 2)
    return sonuc


def _ham_stoklar() -> dict[str, int]:
    return {
        (row.get("BARKOD") or "").strip(): (cs._parse_int(row.get("STOK MİKTARI")) or 0)
        for row in cs._fetch_csv_rows(cs.STOK_URL)
        if (row.get("BARKOD") or "").strip()
    }


def plan_olustur(wix: WixClient) -> Plan:
    plan = Plan()
    satilabilir = {p.barcode: p for p in cs.fetch_catalog()}
    ham_stok = _ham_stoklar()
    pesin = _pesin_fiyatlar() if FIYAT_SUTUNU == "pesin" else {}

    def hedef_fiyat(barkod: str) -> float | None:
        if FIYAT_SUTUNU == "pesin":
            return pesin.get(barkod)
        return round(satilabilir[barkod].price, 2) if barkod in satilabilir else None

    koleksiyon_id = {k["name"]: k["id"] for k in wix.tum_koleksiyonlar()}
    urunler = wix.tum_urunler()

    sku_gorulen: set[str] = set()
    for u in urunler:
        sku = (u.get("sku") or "").strip()
        uid = u["id"]
        if not sku:
            continue
        if sku in sku_gorulen:
            if u.get("visible"):
                plan.cift_sku_gizle.append({"id": uid, "sku": sku, "name": u.get("name")})
            continue
        sku_gorulen.add(sku)

        mevcut_fiyat = float((u.get("priceData") or {}).get("price") or 0)
        mevcut_stok = int((u.get("stock") or {}).get("quantity") or 0)
        gorselli = bool((u.get("media") or {}).get("items"))

        if sku in satilabilir:
            p = satilabilir[sku]
            alanlar: dict = {}
            hf = hedef_fiyat(sku)
            if hf is not None and abs(mevcut_fiyat - hf) > 0.005:
                alanlar["priceData"] = {"price": hf}
            if AD_GUNCELLE and p.title and p.title != (u.get("name") or "") and len(p.title) <= 80:
                alanlar["name"] = p.title
            if not u.get("visible"):
                alanlar["visible"] = True
            if ACIKLAMA_GUNCELLE and p.description and p.description != (u.get("description") or ""):
                alanlar["description"] = p.description[:8000]
            if alanlar:
                plan.guncelle.append({"id": uid, "sku": sku, "name": u.get("name"), "alanlar": alanlar,
                                      "eski_fiyat": mevcut_fiyat})
            if mevcut_stok != p.stock:
                plan.stok_yaz.append({"id": uid, "sku": sku, "eski": mevcut_stok, "yeni": p.stock})
            if GORSEL_EKLE and not gorselli and p.images:
                plan.gorsel_ekle.append({"id": uid, "sku": sku, "urls": p.images[:1]})
            if KOLEKSIYONA_EKLE:
                kol_adi = KOLEKSIYON_ESLEME.get(p.category_name or "")
                if kol_adi and kol_adi in koleksiyon_id:
                    kid = koleksiyon_id[kol_adi]
                    if kid not in (u.get("collectionIds") or []):
                        plan.koleksiyon_ekle.setdefault(kid, []).append(uid)
                else:
                    plan.eslesmeyen_kategori[p.category_name or "?"] = plan.eslesmeyen_kategori.get(p.category_name or "?", 0) + 1
        else:
            if u.get("visible"):
                plan.gizle.append({"id": uid, "sku": sku, "name": u.get("name"), "stok": ham_stok.get(sku, 0)})
            gercek = ham_stok.get(sku, 0)
            if mevcut_stok != gercek:
                plan.stok_yaz.append({"id": uid, "sku": sku, "eski": mevcut_stok, "yeni": gercek})

    for sku, p in satilabilir.items():
        if sku in sku_gorulen:
            continue
        hf = hedef_fiyat(sku)
        if hf is None:
            continue
        kol_adi = KOLEKSIYON_ESLEME.get(p.category_name or "")
        plan.olustur.append({
            "sku": sku, "name": p.title[:80], "price": hf, "stok": p.stock,
            "description": (p.description or "")[:8000], "urls": p.images[:1],
            "koleksiyon_id": koleksiyon_id.get(kol_adi) if kol_adi else None,
        })
        if not kol_adi or kol_adi not in koleksiyon_id:
            plan.eslesmeyen_kategori[p.category_name or "?"] = plan.eslesmeyen_kategori.get(p.category_name or "?", 0) + 1
    return plan


# ----------------------------------------------------------------- Uygulama
def plani_uygula(wix: WixClient, plan: Plan, azami: int | None = None) -> None:
    """Planı Wix'e yazar. `azami` verilirse toplam yazma isteği o sayıda
    durur (ilk canlı denemede küçük bir dilimle doğrulamak için)."""
    def limit_doldu() -> bool:
        return azami is not None and wix.yazma_sayisi >= azami

    for x in plan.guncelle:
        if limit_doldu(): return
        wix.urun_guncelle(x["id"], x["alanlar"])
        logger.info("guncellendi %s %s -> %s", x["sku"], x["name"], x["alanlar"])
    for x in plan.gizle + plan.cift_sku_gizle:
        if limit_doldu(): return
        wix.urun_guncelle(x["id"], {"visible": False})
        logger.info("gizlendi %s %s", x["sku"], x["name"])
    for x in plan.stok_yaz:
        if limit_doldu(): return
        wix.stok_yaz(x["id"], x["yeni"])
        logger.info("stok %s: %s -> %s", x["sku"], x["eski"], x["yeni"])
    for x in plan.gorsel_ekle:
        if limit_doldu(): return
        try:
            wix.gorsel_ekle(x["id"], x["urls"])
            logger.info("gorsel eklendi %s", x["sku"])
        except RuntimeError as e:
            logger.warning("gorsel eklenemedi %s: %s", x["sku"], e)
    for x in plan.olustur:
        if limit_doldu(): return
        uid = wix.urun_olustur({
            "name": x["name"], "productType": "physical", "sku": x["sku"], "visible": True,
            "priceData": {"price": x["price"]}, "costAndProfitData": {"itemCost": 0},
            "description": x["description"],
        })
        wix.stok_yaz(uid, x["stok"])
        if x["urls"]:
            try:
                wix.gorsel_ekle(uid, x["urls"])
            except RuntimeError as e:
                logger.warning("yeni urun gorseli eklenemedi %s: %s", x["sku"], e)
        if x["koleksiyon_id"]:
            plan.koleksiyon_ekle.setdefault(x["koleksiyon_id"], []).append(uid)
        logger.info("olusturuldu %s %s (%s)", x["sku"], x["name"], uid)
    for kid, idler in plan.koleksiyon_ekle.items():
        if limit_doldu(): return
        wix.koleksiyona_ekle(kid, idler)
        logger.info("koleksiyon %s: %d urun eklendi", kid, len(idler))


def main() -> None:
    ap = argparse.ArgumentParser(description="Sheets -> Wix Stores senkronu (varsayılan: kuru çalışma)")
    ap.add_argument("--uygula", action="store_true", help="Wix'e GERÇEKTEN yaz (yoksa sadece plan)")
    ap.add_argument("--azami", type=int, default=None, help="--uygula ile: en fazla bu kadar yazma isteği (deneme dilimi)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    wix = WixClient(uygula=args.uygula)
    plan = plan_olustur(wix)
    PLAN_PATH.parent.mkdir(exist_ok=True)
    PLAN_PATH.write_text(json.dumps({"ozet": plan.ozet(), "guncelle": plan.guncelle[:200], "gizle": plan.gizle[:200],
                                     "olustur": plan.olustur[:200], "gorsel_ekle": plan.gorsel_ekle[:50],
                                     "cift_sku_gizle": plan.cift_sku_gizle}, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(json.dumps(plan.ozet(), ensure_ascii=False, indent=1))
    if not args.uygula:
        print(f"KURU ÇALIŞMA - hiçbir şey yazılmadı. Ayrıntı: {PLAN_PATH}")
        return
    plani_uygula(wix, plan, azami=args.azami)
    print(f"UYGULANDI - toplam yazma isteği: {wix.yazma_sayisi}")


if __name__ == "__main__":
    main()
