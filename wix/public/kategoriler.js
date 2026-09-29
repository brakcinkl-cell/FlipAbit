// www.ozgunaydin.net - Velo ortak kodu (Wix Editor > Dev Mode > Public > kategoriler.js)
//
// Kategori ağacını uygulama sunucusundan okur; menü (masterPage.js) ve ana
// sayfa ızgarası (Home.js) buradan beslenir. Ağaç, Google Sheets 'grup'
// sekmesinden core/wix_sync.py tarafından her senkronda üretilir - Wix
// Editor'de kategori/menü elle düzenlenmez.
import { fetch } from 'wix-fetch';
import wixWindowFrontend from 'wix-window-frontend';

export const KATEGORI_URL = 'https://toptan.ozgunaydin.com.tr/api/wix/kategoriler';

const URUN_ETIKETI = { en: 'products', tr: 'ürün', ru: 'товаров', ar: 'منتج' };

let _agac = null;

export function dil() {
    try {
        return (wixWindowFrontend.multilingual.currentLanguage || 'en').toLowerCase().slice(0, 2);
    } catch (e) {
        return 'en';
    }
}

export function kategoriLink(slug) {
    const d = dil();
    const onek = d === 'en' ? '' : `/${d}`;
    return `${onek}/category/${slug}`;
}

export function urunEtiketi(sayi) {
    return `${sayi} ${URUN_ETIKETI[dil()] || URUN_ETIKETI.en}`;
}

export async function kategoriAgaci() {
    if (_agac) return _agac;
    try {
        const r = await fetch(KATEGORI_URL, { method: 'get' });
        if (!r.ok) return null;
        _agac = await r.json();
        return _agac;
    } catch (e) {
        console.error('kategori agaci alinamadi', e);
        return null;
    }
}

// Mega grupları, seçili dildeki adla/linkle, 3 KATMANLI olarak düzleştirir:
// mega.altlar = orta gruplar VARSA onlar (her biri kendi .altlar'ıyla,
// gerçek ürün kategorileri), yoksa (Baby/Others gibi tek katmanlı mega'larda)
// doğrudan ürün kategorileri - ikinci durumda .altlar boş kalır (3. seviye yok).
function dugumOku(dugum, d, birincilDil) {
    return {
        ad: dugum.ad[d] || dugum.ad[birincilDil] || '',
        urun: dugum.urun,
        gorsel: dugum.gorsel || '',
        link: kategoriLink(dugum.slug),
    };
}

export async function kategoriListesi() {
    const agac = await kategoriAgaci();
    if (!agac) return [];
    const d = dil();
    const oku = (dugum) => dugumOku(dugum, d, agac.birincil_dil);
    return agac.ustler.map((mega, i) => ({
        _id: `mega-${i}`,
        ...oku(mega),
        urunEtiketi: urunEtiketi(mega.urun),
        altlar: (mega.ortalar && mega.ortalar.length ? mega.ortalar : mega.altlar || []).map((orta, j) => ({
            _id: `orta-${i}-${j}`,
            ...oku(orta),
            altlar: (orta.altlar || []).map((alt, k) => ({ _id: `alt-${i}-${j}-${k}`, ...oku(alt) })),
        })),
    }));
}
