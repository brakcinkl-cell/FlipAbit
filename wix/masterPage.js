// www.ozgunaydin.net - Velo site kodu (Wix Editor > Dev Mode > masterPage.js)
//
// Her sayfa açılışında menüleri kategori ağacından doldurur:
//  - KATEGORI_MENU_ID : ikinci sıradaki kategori çubuğu -> TAMAMEN ağaçtan
//    (üst grup = ana madde, alt kategoriler = açılır alt menü)
//  - MOBIL_MENU_ID    : mobil menü -> Editor'deki sabit maddeler (Main page,
//    Contact, Store...) KORUNUR, kategoriler arkasına eklenir
// Element id'lerini Editor'de seçip (sağ üst köşedeki id) buraya yazın.
import { kategoriListesi } from 'public/kategoriler.js';

const KATEGORI_MENU_ID = '#kategoriMenu';
const MOBIL_MENU_ID = '#mobileMenu';

function menuMaddeleri(liste) {
    return liste.map((u, i) => ({
        id: `ust-${i}`,
        label: u.ad,
        link: u.link,
        menuItems: u.altlar.map((a, j) => ({ id: `alt-${i}-${j}`, label: a.ad, link: a.link })),
    }));
}

$w.onReady(async function () {
    const liste = await kategoriListesi();
    if (!liste.length) return;   // sunucuya ulaşılamazsa Editor'deki menü olduğu gibi kalır
    const maddeler = menuMaddeleri(liste);

    try {
        $w(KATEGORI_MENU_ID).menuItems = maddeler;
    } catch (e) {
        console.warn('kategori menusu bulunamadi', KATEGORI_MENU_ID, e);
    }
    try {
        const sabit = ($w(MOBIL_MENU_ID).menuItems || []).filter((m) => !(m.id || '').startsWith('ust-'));
        $w(MOBIL_MENU_ID).menuItems = [...sabit, ...maddeler];
    } catch (e) {
        console.warn('mobil menu bulunamadi', MOBIL_MENU_ID, e);
    }
});
