// www.ozgunaydin.net - Velo site kodu (Wix Editor > Dev Mode > masterPage.js)
//
// Her sayfa açılışında menüleri kategori ağacından doldurur:
//  - KATEGORI_MENU_ID : ikinci sıradaki kategori çubuğu -> 3 KATMANLI ağaçtan
//    (4 sade mega grup: Bedroom/Bathroom/Baby/Others -> varsa orta gruplar,
//    ör. "Exclusive Bedding Sets" -> Tek/Çift Kişilik gibi gerçek kategoriler).
//    ÖNEMLİ: Wix Editor'de bu menü "Gelişmiş Menü / Advanced Menu" olarak
//    ayarlanmalı (menüyü seçip Ayarlar/dişli simgesi -> "Set as Advanced"),
//    yoksa 2. ek alt menü seviyesi (3. katman) desteklenmez.
//  - MOBIL_MENU_ID    : mobil menü -> Editor'deki sabit maddeler (Main page,
//    Contact, Store...) KORUNUR, arkasına sadece 4 MEGA GRUP eklenir
//    (kullanıcı kararı 2026-09-29: mobilde liste kısa kalsın, alt kategoriler
//    kategori sayfasının kendisinde filtre olarak görünüyor).
// Element id'leri Wix Editor'de seçilen elemanın üzerindeki etikette görünür.
import { kategoriListesi } from 'public/kategoriler.js';

const KATEGORI_MENU_ID = '#horizontalMenu5';   // Wix Editor'de doğrulanan gerçek ID (2026-09-29)
const MOBIL_MENU_ID = '#expandableMenu1';      // Wix Editor'de doğrulanan gerçek ID (2026-09-29)

// Wix menuItems.label EN FAZLA 40 KARAKTER kabul ediyor (2026-09-29'da canlıda
// SDK hatasıyla bulundu: uzun etiketle TÜM atama sessizce reddediliyor, menü
// hiç güncellenmiyordu). Kategori adlarımız (özellikle İngilizce üst gruplar,
// "DUVET COVER SETS WITH BEDSPREADS AND BLANKETS" gibi) bunu aşıyor - menüde
// kısaltılır, ana sayfa ızgarasındaki tam ad buna dokunmaz.
const ETIKET_AZAMI = 40;
function kisaltEtiket(metin) {
    if (!metin || metin.length <= ETIKET_AZAMI) return metin;
    return metin.slice(0, ETIKET_AZAMI - 1).trimEnd() + '…';
}

// 3 katmanlı: mega -> orta (varsa) -> alt (gerçek kategori). Baby/Others gibi
// orta katmanı olmayan mega'larda ikinci seviye zaten doğrudan alt kategoriler
// olduğu için o.altlar boş gelir, 3. seviye (menuItems) otomatik oluşmaz.
function menuMaddeleri(liste) {
    return liste.map((u, i) => ({
        id: `mega-${i}`,
        label: kisaltEtiket(u.ad),
        link: u.link,
        menuItems: u.altlar.map((o, j) => ({
            id: `orta-${i}-${j}`,
            label: kisaltEtiket(o.ad),
            link: o.link,
            menuItems: (o.altlar || []).map((a, k) => ({ id: `alt-${i}-${j}-${k}`, label: kisaltEtiket(a.ad), link: a.link })),
        })),
    }));
}

// Mobil menü için: sadece 4 mega grup, alt kategori YOK (masaüstünden farklı).
function mobilMenuMaddeleri(liste) {
    return liste.map((u, i) => ({ id: `mega-${i}`, label: kisaltEtiket(u.ad), link: u.link }));
}

$w.onReady(async function () {
    const liste = await kategoriListesi();
    if (!liste.length) return;   // sunucuya ulaşılamazsa Editor'deki menü olduğu gibi kalır

    try {
        $w(KATEGORI_MENU_ID).menuItems = menuMaddeleri(liste);
    } catch (e) {
        console.warn('kategori menusu bulunamadi', KATEGORI_MENU_ID, e);
    }
    try {
        const sabit = ($w(MOBIL_MENU_ID).menuItems || []).filter((m) => !(m.id || '').startsWith('mega-'));
        $w(MOBIL_MENU_ID).menuItems = [...sabit, ...mobilMenuMaddeleri(liste)];
    } catch (e) {
        console.warn('mobil menu bulunamadi', MOBIL_MENU_ID, e);
    }
});
