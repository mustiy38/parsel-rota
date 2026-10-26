# Kayseri Parsel Rota

Kayseri İl Tarım ve Orman Müdürlüğü saha ekipleri için parsel yol tarifi aracı.

- İlçe, mahalle, ada ve parsel bilgisiyle parselin konumu önce ekip verisinden, yoksa TKGM Parsel Sorgu servisinden alınır.
- Parseller harita üzerinde gösterilir, en kısa ziyaret sırasına dizilir, Google Maps ile sıralı yol tarifi başlatılır.
- Excel listesi yüklenebilir, konumlar toplu bulunur, sonuç Excel'e aktarılır.

## Ekip verisi (şifreli)

Parsel koordinatları `veri/` klasöründe ilçe ilçe, AES-256-GCM ile şifrelenmiş paketler halinde durur; parola olmadan okunamaz.
Uygulama ilk açılışta ekip parolasını sorar, doğrulanan anahtar telefonda saklanır. Paketler telefonda çevrimdışı kopya olarak da tutulur.

Paket üretmek için (bilgisayarda, Python 3 ile):

    pip install pandas openpyxl cryptography
    python3 arac/paketle.py --girdi parseller.xlsx --parola "EKİP PAROLASI" --cikti veri

Girdi dosyasında ilk satır başlık olmalı: İlçe, Mahalle, Ada, Parsel, Enlem, Boylam (isteğe bağlı Alan, Nitelik, Mevkii).
Malik, TC kimlik gibi kişisel sütunlar paketlenmez. Yeni paket üretildiğinde parola değişmişse telefonlarda yeniden sorulur.

Sunulan bilgiler TKGM'nin uyarısı gereği resmi ve ticari amaçla kullanılamaz; konumlar yaklaşıktır.
