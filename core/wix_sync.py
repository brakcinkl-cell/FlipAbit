"""Google Sheets kataloğunu (FlipaBit'in okuduğu AYNI kaynak) Wix Stores'a
(www.ozgunaydin.net, Katalog V1) aktarır - kullanıcının 2026-09-29 isteği:
"Wix'teki ürünler kısmını uygulamadaki aynı mantıkla revize edelim, canlı
senkron" + "kategoriler sistemden otomatik gelsin, ana dil İngilizce, diğer
diller 'grup' sekmesindeki çevirilerden".

VARSAYILAN KURU ÇALIŞMA (dry-run): Wix'e HİÇBİR ŞEY YAZMAZ, sadece neyin
değişeceğini hesaplayıp raporlar (logs/wix_sync_plan.json + özet). Gerçek
yazma yalnızca `--uygula` ile.

Ürün mantığı (uygulamayla aynı, bkz. core/catalog_source.py):
- Satılabilir = STOK MİKTARI >= 10 VE fiyat > 0. Satılabilir olanlar Wix'te
  görünür + güncel fiyat + güncel stok; olmayanlar GİZLENİR (silinmez -
  Google'da indeksli sayfalar ve sipariş geçmişi bozulmasın).
- Eşleştirme anahtarı: Wix SKU == STOK BARKOD (2026-09-29'da doğrulandı).
- Fiyat sütunu: VADELİ (kullanıcı kararı 2026-09-29). Ad: uygulamadaki başlık.
- Görsel: Wix'te hiç görsel yoksa ve Sheets'te varsa eklenir; mevcut görsel
  asla değiştirilmez. Açıklama: Sheets'te varsa yazılır, yoksa Wix'teki kalır.
- Çift SKU'lu Wix ürünlerinde ilk kayıt tutulur, fazlası gizlenir.

Kategori mantığı: core/wix_kategori.py (Sheets 'grup' sekmesi -> alt + üst
koleksiyonlar, çeviriler, ürün üyelikleri, eski koleksiyonların gizlenmesi,
Velo'nun okuduğu kategori ağacı JSON'u).

Wix REST (2026-09-29, deneyerek + dokümandan doğrulandı):
  ürün     POST /stores/v1/products/query (includeHiddenProducts:true ŞART) | PATCH /stores/v1/products/{id} | POST /stores/v1/products
           POST /stores/v1/products/{id}/media {media:[{url}]}
  stok     PATCH /stores/v2/inventoryItems/product/{id}  (trackQuantity HER çağrıda verilmeli)
  kolek.   POST /stores/v1/collections/query | POST /stores/v1/collections {collection:{name}}
           PATCH /stores/v1/collections/{id} {collection:{name,visible}}
           POST /stores/v1/collections/{id}/productIds {productIds} | .../productIds/delete
  diller   POST /locales/v2/locale/query
  çeviri   POST /translation-content/v1/contents/query {query:{filter:{schemaId,entityId}}}
           POST /translation-content/v1/contents {content:{schemaId,entityId,locale,fields}}
           POST /translation-content/v1/bulk/contents/update-by-key {contents:[{content:{...}}]}
Site V1 kullanıyor - V3 uç noktaları 428 CATALOG_V1_SITE_CALLING_CATALOG_V3_API
döner, bu kod V3'e taşınırsa baştan yazılmalı.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import catalog_source as cs  # noqa: E402
from core import wix_kategori as wk  # noqa: E402

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = ROOT / "credentials" / "wix_api.json"
PLAN_PATH = ROOT / "logs" / "wix_sync_plan.json"

BASE = "https://www.wixapis.com"
VARSAYILAN_VARYANT_ID = "00000000-0000-0000-0000-000000000000"
ISTEK_ARASI_SN = 0.15  # Wix rate limit'e nazik davran

AD_GUNCELLE = True          # Wix ürün adını Sheets'teki (uygulamadaki) başlıkla değiştir
ACIKLAMA_GUNCELLE = True    # Sheets'te açıklama varsa Wix'e yaz
GORSEL_EKLE = True          # Wix'te görsel yoksa Sheets'tekini ekle
KATEGORI_YONET = True       # koleksiyon yapısı + üyelikler + çeviriler


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

    def _istek(self, method: str, path: str, body: dict | None = None, yazma: bool = True,
               ag_tekrar: bool = True) -> dict:
        if yazma and not self.uygula:
            raise RuntimeError("kuru çalışmada yazma isteği çağrıldı - programlama hatası")
        for deneme in range(5):
            try:
                r = requests.request(method, BASE + path, headers=self.headers, json=body, timeout=60)
            except requests.exceptions.RequestException as e:
                # Ağ kesintisi / bağlantı sıfırlama (2026-09-29'daki tam senkron ~1600.
                # istekte 10054 ile düştü) - artan bekleme ile yeniden dene. Ürün
                # OLUŞTURMA gibi tekrarı güvenli olmayan isteklerde (ag_tekrar=False)
                # denenmez: istek Wix'e ulaşıp yanıt yolda kopmuş olabilir, körlemesine
                # tekrar çift kayıt üretir (2026-09-29'da 20 kadar ürün ikilendi).
                if not ag_tekrar:
                    raise
                logger.warning("Wix %s %s ağ hatası (%s), %d. deneme", method, path, e.__class__.__name__, deneme + 1)
                time.sleep(3 * (deneme + 1))
                continue
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

    # --- ürünler
    def tum_urunler(self) -> list[dict]:
        """GİZLİ ürünler dahil. Varsayılan sorgu yalnız görünür ürünleri döndürür
        (2026-09-29'da fark edildi: 1356 görünür / 4721 toplam) - gizliler
        görülmezse senkron onları yeniden oluşturmaya kalkar ("sku is not
        unique") ve stoğu gelen ürün asla yeniden görünür yapılamaz."""
        # sort ŞART: sırasız offset sayfalamada Wix aynı ürünü iki kez verip
        # başkasını atlıyor (2026-09-29: 4809 çekilen / 4746 benzersiz) - bu
        # yüzden gerçek ürünler "çift SKU" sanılıp gizlendi, atlananlar
        # yeniden oluşturulmaya çalışıldı. id'ye göre sıralı çekimde 4809/4809.
        urunler, offset = [], 0
        while True:
            d = self._istek("POST", "/stores/v1/products/query",
                            {"query": {"paging": {"limit": 100, "offset": offset}, "sort": json.dumps([{"id": "asc"}])},
                             "includeVariants": False, "includeHiddenProducts": True}, yazma=False)
            sayfa = d.get("products", [])
            urunler.extend(sayfa)
            offset += len(sayfa)
            if not sayfa or offset >= (d.get("totalResults") or 0):
                return urunler

    def urun_guncelle(self, urun_id: str, alanlar: dict) -> None:
        self._istek("PATCH", f"/stores/v1/products/{urun_id}", {"product": alanlar})

    def sku_ile_bul(self, sku: str) -> dict | None:
        d = self._istek("POST", "/stores/v1/products/query",
                        {"query": {"filter": json.dumps({"sku": sku})}, "includeHiddenProducts": True}, yazma=False)
        urunler = d.get("products", [])
        return urunler[0] if urunler else None

    def urun_olustur(self, alanlar: dict) -> str:
        """Ürünü oluşturur; ağ hatasında ya da 'sku is not unique' yanıtında
        ürün SKU ile aranır - varsa (istek aslında ulaşmışsa) onun id'si döner."""
        try:
            d = self._istek("POST", "/stores/v1/products", {"product": alanlar}, ag_tekrar=False)
            return d["product"]["id"]
        except (RuntimeError, requests.exceptions.RequestException) as e:
            mevcut = self.sku_ile_bul(alanlar.get("sku", ""))
            if mevcut:
                logger.warning("urun olusturma yaniti alinamadi ama SKU %s Wix'te var (%s): %s", alanlar.get("sku"), mevcut["id"], e)
                return mevcut["id"]
            raise RuntimeError(f"urun olusturulamadi {alanlar.get('sku')}: {e}") from e

    def stok_yaz(self, urun_id: str, miktar: int) -> None:
        self._istek("PATCH", f"/stores/v2/inventoryItems/product/{urun_id}", {"inventoryItem": {
            "productId": urun_id,
            "trackQuantity": True,
            "variants": [{"variantId": VARSAYILAN_VARYANT_ID, "quantity": max(0, int(miktar)), "inStock": miktar > 0}],
        }})

    def gorsel_ekle(self, urun_id: str, urls: list[str]) -> None:
        self._istek("POST", f"/stores/v1/products/{urun_id}/media", {"media": [{"url": u} for u in urls[:50]]})

    # --- koleksiyonlar
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

    def koleksiyon_olustur(self, ad: str) -> dict:
        d = self._istek("POST", "/stores/v1/collections", {"collection": {"name": ad, "visible": True}})
        return d["collection"]

    def koleksiyon_guncelle(self, koleksiyon_id: str, alanlar: dict) -> None:
        self._istek("PATCH", f"/stores/v1/collections/{koleksiyon_id}", {"collection": alanlar})

    def koleksiyona_ekle(self, koleksiyon_id: str, urun_idleri: list[str]) -> None:
        for i in range(0, len(urun_idleri), 1000):
            self._istek("POST", f"/stores/v1/collections/{koleksiyon_id}/productIds", {"productIds": urun_idleri[i:i + 1000]})

    def koleksiyondan_cikar(self, koleksiyon_id: str, urun_idleri: list[str]) -> None:
        for i in range(0, len(urun_idleri), 1000):
            self._istek("POST", f"/stores/v1/collections/{koleksiyon_id}/productIds/delete", {"productIds": urun_idleri[i:i + 1000]})

    # --- diller / çeviriler
    def diller(self) -> list[dict]:
        return self._istek("POST", "/locales/v2/locale/query", {"query": {}}, yazma=False).get("locales", [])

    def koleksiyon_cevirileri(self, koleksiyon_id: str) -> dict[str, str]:
        """dil -> mevcut çeviri metni (Wix'te kayıtlı olanlar)."""
        d = self._istek("POST", "/translation-content/v1/contents/query",
                        {"query": {"filter": {"schemaId": wk.CEVIRI_SEMA_ID, "entityId": koleksiyon_id}}}, yazma=False)
        return {c["locale"]: ((c.get("fields") or {}).get(wk.CEVIRI_ALAN) or {}).get("textValue", "")
                for c in d.get("contents", [])}

    def ceviri_yaz(self, koleksiyon_id: str, dil: str, metin: str, mevcut: bool) -> None:
        icerik = {"schemaId": wk.CEVIRI_SEMA_ID, "entityId": koleksiyon_id, "locale": dil,
                  "fields": {wk.CEVIRI_ALAN: {"textValue": metin, "published": True}}}
        if mevcut:
            self._istek("POST", "/translation-content/v1/bulk/contents/update-by-key", {"contents": [{"content": icerik}]})
        else:
            self._istek("POST", "/translation-content/v1/contents", {"content": icerik})


# ------------------------------------------------------------------- Plan
@dataclass
class Plan:
    guncelle: list[dict] = field(default_factory=list)      # mevcut, satılabilir: fiyat/ad/görünürlük/açıklama farkları
    gizle: list[dict] = field(default_factory=list)         # mevcut ama satılamaz -> visible False
    olustur: list[dict] = field(default_factory=list)       # Sheets'te satılabilir, Wix'te yok
    stok_yaz: list[dict] = field(default_factory=list)      # miktar farklı olanlar
    gorsel_ekle: list[dict] = field(default_factory=list)   # Wix görselsiz, Sheets görselli
    cift_sku_gizle: list[dict] = field(default_factory=list)
    # kategori
    kol_olustur: list[str] = field(default_factory=list)                    # yapıda var, Wix'te yok (ad)
    kol_gorunurluk: list[dict] = field(default_factory=list)                # {id, ad, visible}
    kol_gizle_eski: list[dict] = field(default_factory=list)                # yapıda olmayan görünür koleksiyonlar
    ceviri: list[dict] = field(default_factory=list)                        # {ad, id|None, dil, metin, mevcut}
    uyelik_ekle: dict[str, list[str]] = field(default_factory=dict)         # koleksiyon adı -> [urun id]
    uyelik_cikar: dict[str, list[str]] = field(default_factory=dict)        # koleksiyon adı -> [urun id]
    eksik_dil: list[str] = field(default_factory=list)                      # Sheets'te çevirisi var, sitede dil yok
    kategorisiz_kod: dict[str, int] = field(default_factory=dict)           # grup kodu -> ürün sayısı ('grup' sekmesinde yok)
    uyarilar: list[str] = field(default_factory=list)
    # ağaç için
    urun_sayisi: dict[str, int] = field(default_factory=dict)               # koleksiyon adı -> satılabilir ürün
    kapak: dict[str, str | None] = field(default_factory=dict)              # koleksiyon adı -> görsel

    def ozet(self) -> dict:
        return {
            "urun_guncelle": len(self.guncelle),
            "  fiyat_degisen": sum(1 for x in self.guncelle if "priceData" in x["alanlar"]),
            "  ad_degisen": sum(1 for x in self.guncelle if "name" in x["alanlar"]),
            "  gorunur_yapilan": sum(1 for x in self.guncelle if x["alanlar"].get("visible") is True),
            "  aciklama_yazilan": sum(1 for x in self.guncelle if "description" in x["alanlar"]),
            "urun_gizle": len(self.gizle),
            "urun_olustur": len(self.olustur),
            "stok_yaz": len(self.stok_yaz),
            "gorsel_ekle": len(self.gorsel_ekle),
            "cift_sku_gizle": len(self.cift_sku_gizle),
            "koleksiyon_olustur": self.kol_olustur,
            "koleksiyon_gorunurluk_degisen": len(self.kol_gorunurluk),
            "eski_koleksiyon_gizle": [x["ad"] for x in self.kol_gizle_eski],
            "ceviri_yaz": len(self.ceviri),
            "uyelik_ekle": sum(len(v) for v in self.uyelik_ekle.values()),
            "uyelik_cikar": sum(len(v) for v in self.uyelik_cikar.values()),
            "eksik_dil": self.eksik_dil,
            "kategorisiz_kod": self.kategorisiz_kod,
            "uyarilar": self.uyarilar,
        }


def _duz_metin(s: str) -> str:
    """HTML kaçışlarını kaç kat olursa olsun çözer ("&amp;amp;" -> "&")."""
    onceki = None
    while s != onceki:
        onceki, s = s, html.unescape(s)
    return s.strip()


def _ham_stoklar() -> dict[str, int]:
    return {
        (row.get("BARKOD") or "").strip(): (cs._parse_int(row.get("STOK MİKTARI")) or 0)
        for row in cs._fetch_csv_rows(cs.STOK_URL)
        if (row.get("BARKOD") or "").strip()
    }


def plan_olustur(wix: WixClient) -> tuple[Plan, wk.Yapi, dict[str, dict], list[str]]:
    """Döner: plan, kategori yapısı, Wix koleksiyonları (ad -> kayıt), site dilleri."""
    plan = Plan()
    satilabilir = {p.barcode: p for p in cs.fetch_catalog()}
    ham_stok = _ham_stoklar()
    yapi = wk.yapi_oku()
    plan.uyarilar.extend(yapi.uyarilar)

    koleksiyon = {k["name"]: k for k in wix.tum_koleksiyonlar()}
    diller = [l["id"] for l in wix.diller() if l.get("visibility") != "HIDDEN"] or [wk.BIRINCIL_DIL]
    yapi_adlari = {k.anahtar for k in yapi.tum}

    # --- koleksiyon yapısı
    for k in yapi.tum:
        if k.anahtar not in koleksiyon:
            plan.kol_olustur.append(k.anahtar)
    for ad, k in koleksiyon.items():
        if ad not in yapi_adlari and ad not in wk.YONETILMEYEN_KOLEKSIYONLAR and k.get("visible"):
            plan.kol_gizle_eski.append({"id": k["id"], "ad": ad})
    sheets_dilleri = {d for k in yapi.tum for d in k.adlar if d != wk.BIRINCIL_DIL}
    plan.eksik_dil = sorted(sheets_dilleri - set(diller))

    # --- ürünler
    urunler = wix.tum_urunler()
    id_ad = {k["id"]: ad for ad, k in koleksiyon.items()}
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
            hf = round(p.price, 2)
            if abs(mevcut_fiyat - hf) > 0.005:
                alanlar["priceData"] = {"price": hf}
            if AD_GUNCELLE and p.title and p.title != (u.get("name") or "") and len(p.title) <= 80:
                alanlar["name"] = p.title
            if not u.get("visible"):
                alanlar["visible"] = True
            # Wix açıklamayı HTML olarak saklar ("&" -> "&amp;"), Sheets'te de yer yer
            # "&amp;"/"&amp;amp;" hazır kaçışlı metin var. İki taraf da tamamen
            # çözülüp karşılaştırılır ve Wix'e çözülmüş düz metin yazılır (Wix
            # kendi kaçışını yapar) - yoksa aynı ürünler her koşuda yeniden yazılır.
            if ACIKLAMA_GUNCELLE and p.description and _duz_metin(p.description) != _duz_metin(u.get("description") or ""):
                alanlar["description"] = _duz_metin(p.description)[:8000]
            if alanlar:
                plan.guncelle.append({"id": uid, "sku": sku, "name": u.get("name"), "alanlar": alanlar,
                                      "eski_fiyat": mevcut_fiyat})
            if mevcut_stok != p.stock:
                plan.stok_yaz.append({"id": uid, "sku": sku, "eski": mevcut_stok, "yeni": p.stock})
            if GORSEL_EKLE and not gorselli and p.images:
                plan.gorsel_ekle.append({"id": uid, "sku": sku, "urls": p.images[:1]})
            if KATEGORI_YONET:
                hedef = yapi.hedef_koleksiyonlar(p.category_code)
                if not hedef and p.category_code:
                    plan.kategorisiz_kod[p.category_code] = plan.kategorisiz_kod.get(p.category_code, 0) + 1
                mevcut_adlar = {id_ad.get(cid) for cid in (u.get("collectionIds") or [])}
                for ad in hedef:
                    if ad not in mevcut_adlar:
                        plan.uyelik_ekle.setdefault(ad, []).append(uid)
                for ad in mevcut_adlar:
                    if ad and ad in yapi_adlari and ad not in hedef:
                        plan.uyelik_cikar.setdefault(ad, []).append(uid)
        else:
            if u.get("visible"):
                plan.gizle.append({"id": uid, "sku": sku, "name": u.get("name"), "stok": ham_stok.get(sku, 0)})
            gercek = max(0, ham_stok.get(sku, 0))  # Sheets'te eksi stok olabiliyor, Wix'e 0 yazılır
            if mevcut_stok != gercek:
                plan.stok_yaz.append({"id": uid, "sku": sku, "eski": mevcut_stok, "yeni": gercek})

    for sku, p in satilabilir.items():
        if sku in sku_gorulen:
            continue
        hedef = yapi.hedef_koleksiyonlar(p.category_code) if KATEGORI_YONET else []
        if KATEGORI_YONET and not hedef and p.category_code:
            plan.kategorisiz_kod[p.category_code] = plan.kategorisiz_kod.get(p.category_code, 0) + 1
        plan.olustur.append({
            "sku": sku, "name": p.title[:80], "price": round(p.price, 2), "stok": p.stock,
            "description": _duz_metin(p.description or "")[:8000], "urls": p.images[:1], "koleksiyonlar": hedef,
        })

    # --- kategori başına ürün sayısı, kapak görseli, görünürlük, çeviriler
    for p in satilabilir.values():
        for ad in yapi.hedef_koleksiyonlar(p.category_code):
            plan.urun_sayisi[ad] = plan.urun_sayisi.get(ad, 0) + 1
            if p.images and not plan.kapak.get(ad):
                plan.kapak[ad] = p.images[0]
    for k in yapi.tum:
        w = koleksiyon.get(k.anahtar)
        gorunur_olmali = plan.urun_sayisi.get(k.anahtar, 0) > 0
        if w is not None and bool(w.get("visible")) != gorunur_olmali:
            plan.kol_gorunurluk.append({"id": w["id"], "ad": k.anahtar, "visible": gorunur_olmali})
        mevcut_ceviri = wix.koleksiyon_cevirileri(w["id"]) if w is not None else {}
        for dil in diller:
            if dil == wk.BIRINCIL_DIL:
                continue
            metin = k.adlar.get(dil)
            if metin and mevcut_ceviri.get(dil) != metin:
                plan.ceviri.append({"ad": k.anahtar, "id": w["id"] if w else None, "dil": dil,
                                    "metin": metin, "mevcut": dil in mevcut_ceviri})
    return plan, yapi, koleksiyon, diller


# ----------------------------------------------------------------- Uygulama
def plani_uygula(wix: WixClient, plan: Plan, yapi: wk.Yapi, koleksiyon: dict[str, dict],
                 diller: list[str], azami: int | None = None, sadece_kategori: bool = False) -> None:
    """Planı Wix'e yazar. `azami` verilirse toplam yazma isteği o sayıda
    durur (ilk canlı denemede küçük bir dilimle doğrulamak için);
    `sadece_kategori` ürün adımlarını atlar (koleksiyon/üyelik/çeviri/ağaç).
    Sıra: koleksiyonlar -> ürünler -> üyelikler -> çeviriler -> ağaç JSON."""
    def limit_doldu() -> bool:
        return azami is not None and wix.yazma_sayisi >= azami

    koleksiyon = dict(koleksiyon)  # ad -> kayıt; yeni oluşturulanlar eklenir

    for ad in plan.kol_olustur:
        if limit_doldu(): break
        koleksiyon[ad] = wix.koleksiyon_olustur(ad)
        logger.info("koleksiyon olusturuldu: %s (%s)", ad, koleksiyon[ad]["id"])
    for x in plan.kol_gorunurluk + [{**x, "visible": False} for x in plan.kol_gizle_eski]:
        if limit_doldu(): break
        wix.koleksiyon_guncelle(x["id"], {"visible": x["visible"]})
        logger.info("koleksiyon %s -> visible=%s", x["ad"], x["visible"])

    for x in ([] if sadece_kategori else plan.guncelle):
        if limit_doldu(): break
        wix.urun_guncelle(x["id"], x["alanlar"])
        logger.info("guncellendi %s %s -> %s", x["sku"], x["name"], x["alanlar"])
    for x in ([] if sadece_kategori else plan.gizle + plan.cift_sku_gizle):
        if limit_doldu(): break
        wix.urun_guncelle(x["id"], {"visible": False})
        logger.info("gizlendi %s %s", x["sku"], x["name"])
    for x in ([] if sadece_kategori else plan.stok_yaz):
        if limit_doldu(): break
        wix.stok_yaz(x["id"], x["yeni"])
        logger.info("stok %s: %s -> %s", x["sku"], x["eski"], x["yeni"])
    for x in ([] if sadece_kategori else plan.gorsel_ekle):
        if limit_doldu(): break
        try:
            wix.gorsel_ekle(x["id"], x["urls"])
            logger.info("gorsel eklendi %s", x["sku"])
        except RuntimeError as e:
            logger.warning("gorsel eklenemedi %s: %s", x["sku"], e)
    for x in ([] if sadece_kategori else plan.olustur):
        if limit_doldu(): break
        try:
            uid = wix.urun_olustur({
                "name": x["name"], "productType": "physical", "sku": x["sku"], "visible": True,
                "priceData": {"price": x["price"]}, "costAndProfitData": {"itemCost": 0},
                "description": x["description"],
            })
        except RuntimeError as e:
            # Örn. "product.sku is not unique": SKU Wix'te bir VARYANTTA kayıtlı
            # olabilir; tek ürün yüzünden koca senkron durmasın, raporla ve geç.
            logger.warning("urun olusturulamadi %s %s: %s", x["sku"], x["name"], e)
            continue
        wix.stok_yaz(uid, x["stok"])
        if x["urls"]:
            try:
                wix.gorsel_ekle(uid, x["urls"])
            except RuntimeError as e:
                logger.warning("yeni urun gorseli eklenemedi %s: %s", x["sku"], e)
        for ad in x["koleksiyonlar"]:
            plan.uyelik_ekle.setdefault(ad, []).append(uid)
        logger.info("olusturuldu %s %s (%s)", x["sku"], x["name"], uid)

    for ad, idler in plan.uyelik_ekle.items():
        if limit_doldu(): break
        if ad not in koleksiyon:
            logger.warning("koleksiyon yok, uyelik atlandi: %s", ad)
            continue
        wix.koleksiyona_ekle(koleksiyon[ad]["id"], idler)
        logger.info("koleksiyon %s: %d urun eklendi", ad, len(idler))
    for ad, idler in plan.uyelik_cikar.items():
        if limit_doldu(): break
        wix.koleksiyondan_cikar(koleksiyon[ad]["id"], idler)
        logger.info("koleksiyon %s: %d urun cikarildi", ad, len(idler))

    for x in plan.ceviri:
        if limit_doldu(): break
        kid = x["id"] or (koleksiyon.get(x["ad"]) or {}).get("id")
        if not kid:
            continue
        try:
            wix.ceviri_yaz(kid, x["dil"], x["metin"], x["mevcut"])
            logger.info("ceviri %s [%s] = %s", x["ad"], x["dil"], x["metin"])
        except RuntimeError as e:
            logger.warning("ceviri yazilamadi %s [%s]: %s", x["ad"], x["dil"], e)

    if not limit_doldu():
        wk.agac_yaz(yapi, koleksiyon, plan.urun_sayisi, plan.kapak, diller)
        logger.info("kategori agaci yazildi: %s", wk.AGAC_PATH)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sheets -> Wix Stores senkronu (varsayılan: kuru çalışma)")
    ap.add_argument("--uygula", action="store_true", help="Wix'e GERÇEKTEN yaz (yoksa sadece plan)")
    ap.add_argument("--azami", type=int, default=None, help="--uygula ile: en fazla bu kadar yazma isteği (deneme dilimi)")
    ap.add_argument("--sadece-kategori", action="store_true", help="--uygula ile: ürün adımlarını atla, yalnız koleksiyon/üyelik/çeviri")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    wix = WixClient(uygula=args.uygula)
    plan, yapi, koleksiyon, diller = plan_olustur(wix)
    PLAN_PATH.parent.mkdir(exist_ok=True)
    PLAN_PATH.write_text(json.dumps({
        "ozet": plan.ozet(), "guncelle": plan.guncelle[:200], "gizle": plan.gizle[:200],
        "olustur": plan.olustur[:200], "gorsel_ekle": plan.gorsel_ekle[:50], "cift_sku_gizle": plan.cift_sku_gizle,
        "kol_gorunurluk": plan.kol_gorunurluk, "ceviri": plan.ceviri,
        "uyelik_ekle": {k: len(v) for k, v in plan.uyelik_ekle.items()},
        "uyelik_cikar": {k: len(v) for k, v in plan.uyelik_cikar.items()},
        "agac_onizleme": [
            {"sira": m.sira, "en": m.anahtar, "tr": m.adlar.get("tr"), "urun": plan.urun_sayisi.get(m.anahtar, 0),
             "ortalar": [
                 {"sira": o.sira, "en": o.anahtar, "tr": o.adlar.get("tr"), "urun": plan.urun_sayisi.get(o.anahtar, 0),
                  "altlar": [{"en": a.anahtar, "tr": a.adlar.get("tr"), "urun": plan.urun_sayisi.get(a.anahtar, 0)}
                             for a in yapi.altlar if a.ust_anahtar == o.anahtar]}
                 for o in yapi.ortalar if o.ust_anahtar == m.anahtar
             ],
             "altlar_dogrudan": [{"en": a.anahtar, "tr": a.adlar.get("tr"), "urun": plan.urun_sayisi.get(a.anahtar, 0)}
                                 for a in yapi.altlar if a.ust_anahtar == m.anahtar]}
            for m in yapi.megalar
        ],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(plan.ozet(), ensure_ascii=False, indent=1))
    if not args.uygula:
        print(f"KURU ÇALIŞMA - hiçbir şey yazılmadı. Ayrıntı: {PLAN_PATH}")
        return
    plani_uygula(wix, plan, yapi, koleksiyon, diller, azami=args.azami, sadece_kategori=args.sadece_kategori)
    print(f"UYGULANDI - toplam yazma isteği: {wix.yazma_sayisi}")


if __name__ == "__main__":
    main()
