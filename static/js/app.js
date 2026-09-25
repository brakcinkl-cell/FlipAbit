// Ürün ızgarasındaki (kategori/arama sayfaları) "Sepete Ekle" formlarını
// AJAX ile gönderir - sayfa yenilenmesin, kaydırma konumu bozulmasın diye
// (kullanıcının 2026-09-25 isteği). Ürün detay sayfasındaki form BUNUN
// DIŞINDA - .urun-card içinde değil, kasıtlı olarak normal submit kalır.
document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches(".urun-card form")) return;
    e.preventDefault();

    var buton = form.querySelector("button[type=submit]");
    var orijinalMetin = buton.textContent;
    var formData = new FormData(form);

    fetch(form.action, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: formData,
    })
        .then(function (r) { return r.json(); })
        .then(function (veri) {
            if (!veri.ok) return;
            buton.textContent = "✓ " + veri.miktar + " Eklendi";
            buton.classList.add("btn-eklendi");
            buton.disabled = true;
            setTimeout(function () {
                buton.textContent = orijinalMetin;
                buton.classList.remove("btn-eklendi");
                buton.disabled = false;
            }, 1800);

            var sayiRozeti = document.querySelector(".sepet-rozet");
            var sepetIkonu = document.querySelector(".icon-sepet");
            if (veri.sepet_sayisi > 0 && sepetIkonu) {
                if (sayiRozeti) {
                    sayiRozeti.textContent = veri.sepet_sayisi;
                } else {
                    sayiRozeti = document.createElement("span");
                    sayiRozeti.className = "sepet-rozet";
                    sayiRozeti.textContent = veri.sepet_sayisi;
                    sepetIkonu.appendChild(sayiRozeti);
                }
            }
        })
        .catch(function () {
            // Ağ hatasında normal (sayfa yenileyen) forma düş.
            form.submit();
        });
});
