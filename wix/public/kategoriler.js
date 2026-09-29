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

// Üst grupları (ve altlarını) seçili dildeki adla, linkle düzleştirir.
export async function kategoriListesi() {
    const agac = await kategoriAgaci();
    if (!agac) return [];
    const d = dil();
    const ad = (dugum) => dugum.ad[d] || dugum.ad[agac.birincil_dil] || '';
    return agac.ustler.map((u, i) => ({
        _id: `ust-${i}`,
        ad: ad(u),
        urun: u.urun,
        urunEtiketi: urunEtiketi(u.urun),
        gorsel: u.gorsel || '',
        link: kategoriLink(u.slug),
        altlar: u.altlar.map((a, j) => ({
            _id: `alt-${i}-${j}`,
            ad: ad(a),
            urun: a.urun,
            gorsel: a.gorsel || '',
            link: kategoriLink(a.slug),
        })),
    }));
}
