// www.ozgunaydin.net - Ana sayfa kodu (Wix Editor > Dev Mode > Home page code)
//
// Ana sayfadaki kategori ızgarasını (repeater) ağaçtan doldurur. Editor'de
// bir kez şu elemanlar eklenir, sonra içerik hep sistemden gelir:
//   #kategoriRepeater  Repeater (ızgara düzeni; mobilde 2 sütun)
//     #kategoriKutu    repeater öğesinin kapsayıcısı (tıklanınca kategoriye gider)
//     #kategoriResim   Image  (kapak görseli - kategorinin ilk ürünü)
//     #kategoriAd      Text   (kategori adı, seçili dilde)
//     #kategoriSayi    Text   (ürün sayısı, ör. "163 ürün")
import wixLocationFrontend from 'wix-location-frontend';
import { kategoriListesi } from 'public/kategoriler.js';

$w.onReady(async function () {
    const rep = $w('#kategoriRepeater');
    rep.onItemReady(($item, veri) => {
        $item('#kategoriAd').text = veri.ad;
        $item('#kategoriSayi').text = veri.urunEtiketi;
        if (veri.gorsel) {
            $item('#kategoriResim').src = veri.gorsel;
            $item('#kategoriResim').alt = veri.ad;
        }
        $item('#kategoriKutu').onClick(() => wixLocationFrontend.to(veri.link));
    });
    const liste = await kategoriListesi();
    if (liste.length) {
        rep.data = liste;
        rep.expand();
    } else {
        rep.collapse();   // sunucu yoksa boş ızgara görünmesin
    }
});
