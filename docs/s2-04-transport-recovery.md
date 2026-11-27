# S2-04: Sınırlı sonuç teslim recovery profili

`management runner --recover-pending` açık seçenektir. Varsayılan runner davranışı
değişmez: pending sonuç yeni execution'ı engeller, otomatik retry yapılmaz.
Seçenek aynı runner credential'ı, server origin'i ve özel cache ile kullanılmalıdır.
Mevcut runner komutuna bu bayrağı ekleyin; `--once` tek delivery/claim geçişinden sonra
çıkar. Token veya lease CLI argümanı değildir; credential-file kullanılmaya devam eder.

Recovery önce bütünlüğü doğrulanmış `pending_upload` kaydını teslim eder; yeni claim
ve agent execution yapmaz. incomplete, corrupt ve legacy_unverified kayıtlar
yüklenmez veya otomatik silinmez. S2-03 operatör inceleme prosedürü geçerlidir.
Job/project/runner/origin, execution ID, lease ve report digest bağları korunur.
Cache session kilidi aynı cache'in eşzamanlı publisher/recovery kullanımını engeller.

## Sınırlar

Varsayılan politika en fazla 3 attempt, 15 saniye delivery bütçesi, istek başına
3 saniye timeout, 0,25 saniye başlangıç ve 2 saniye maksimum exponential backoff
kullanır; jitter çarpanı 0,5–1'dir. SDK sınırları 5 attempt, 60 saniye toplam,
10 saniye request ve 5 saniye backoff'tur. Bu bütçe açık delivery geçişi içindir;
kalıcı yaşam boyu kota değildir. Harici supervisor'ın tekrar başlatma politikası
bu garantinin dışındadır. Cache I/O ve işletim sistemi scheduling süresi network
deadline'ının kesin gerçek zamanlı üst sınırı olarak sunulmaz.

Yalnız finish transport reset/timeout ve izin verilen geçici 500/502/503/504
yanıtları yeniden denenir. Claim, enrollment, heartbeat ve execution yeniden
denenmez. Authentication, TLS doğrulama, bozuk/protokol dışı cevap, integrity,
revocation ve kabul edilmemiş stale lease kalıcı hatadır. Retry tükenirse güvenli
CLI hata özeti `pending_upload` bildirir ve evidence korunur; başarı uydurulmaz.
Default transport DNS bekleme, connect, TLS, header ve body için deadline uygular.
OS DNS görevi iptal edilemez; daemon resolver'ın sonucu deadline sonrasında yeni
bağlantı kurmaz. Güvenilen özel opener/client uygulamaları kendi transport sınırlarından
sorumludur. Hiçbir hata gövdesi veya credential teşhis çıktısına taşınmaz.

## İdempotency ve kabul

Sunucunun önceden kabul edip seal'lediği aynı digest ve lease, mevcut runner yetkisi
doğrulanarak tarihsel receipt alabilir; lease/deadline süresinin sonradan dolması
yeni bir raporun kabulü değildir. Farklı digest/lease veya bozuk seal reddedilir.
Aynı kabul ikinci artifact, accounting veya execution oluşturmaz. Kabul edilmiş
failure sinyali de BLOCK receipt olarak korunur. Eski error kayıtları geriye dönük
kabul edilmiş sayılmaz. Eski server doğrulanabilir ACK vermiyorsa cache pending
kalır. Yeni server eski runner finish gövdesini destekler; şema migration'ı yoktur.

Yerel acknowledge yalnız doğrulanmış eşleşen server receipt sonrasında yayımlanır.
Tarihsel kabul yeni admission/release onayı değildir; `authorizes_release=false`
korunur. Sonradan değişen approval geçmiş execution'ı yeniden çalıştırmaz.
Receipt'in kaybı network üzerinden commit noktasını kesinleştirmez. Bu tasarım
server DB idempotency sağlar; dağıtık transaction veya dış yan etkilerde exactly-once
execution garantisi değildir. İlk kabulden önce admission geri çekilmişse cleanup kanıtı korunabilir,
ancak ALLOW üretilmez. Retention ile silinen artifact yeniden canlandırılmaz.
Mevcut operatör cleanup proof sözleşmesi genişletilmez; ilgili sealed failure
receipt yalnız meşru cleanup güncellemesinden sonra yeniden seal'lenir.

Ücretli API, gerçek kullanıcı state'i ve gerçek credential kullanılmadan yerel
HTTP, subprocess crash/restart ve Docker fixture'larıyla doğrulanır.

## Regresyon kanıtlarının kapsamı

Önce failure receipt'in restart/expired lease sonrasında kaybolması ve bozuk seal'in
teslim kabulüne dönüşmesi testlerle yeniden üretildi. Yavaş header akışı eski
inactivity timeout'ının toplam deadline olmadığını gösterdi. Son olarak gerçek
özel cache okunurken bütçe tükenmesini modelleyen test, HTTP çağrısından hemen
önce de budget kontrolü gerektiğini kanıtladı. Düzeltmelerden sonra bu testler geçti.

HTTP fixture'ları reset, commit öncesi transaction rollback, commit sonrası ACK
kaybı, 503, auth/revoke, cancellation/withdrawal, expiry, bozuk yanıt ve origin
değişimini kapsar. Gerçek server process'i commit sonrası cevap vermeden ölür;
aynı origin'de farklı process ve yeni SQLite instance kabulü yeniden doğrular. Docker
fixture'larında agent invocation=1, finish attempt=2, artifact=1 ve ilgili usage
kaydı=1 koşulları doğrulanır. Restart delivery'si ek execution yapmaz. Negatif
durumlarda pending veri korunur; admission iptali BLOCK ve verified cleanup ile
birlikte görünür. Mevcut operator cleanup yolu yalnız seal tutarlılığı için uyarlanır.

Üretim cache/credential Windows izin testleri ve ayrı native fixture ACL testleri
birbirinden ayrıdır. Linux wheel kurulumunda farklı UID okuma girişimi reddedilir.
Native testler geçici dizinler kullanır; canlı kullanıcı credential/cache'i okunmaz.

Değişen kaynaklar: `management/cli.py`, `client.py`, `delivery.py`, `operations.py`,
`results.py`, `rpc_deadline.py`, `runner.py`. Testler: `test_s2_delivery.py`,
`test_s2_delivery_server.py`, `test_s2_delivery_transport.py`,
`test_s2_delivery_recovery.py`, `test_s2_delivery_process.py`,
`test_s2_delivery_docker.py`. Bu profil ve `phase7-onboarding.md` ile toplam 15
dosya vardır. Queue şeması, credential publication, mevcut test beklentileri ve
workflow değiştirilmez. Yeni integration/Docker marker'ları mevcut CI keşfine dahildir.

## Yerel doğrulama (27 Kasım 2026)

Son kaynakla unit/integration: **951 passed**, gerçek Docker: **84 passed**;
failure/error/skip yok. Önceki doğrulanmış JUnit case kimlikleriyle karşılaştırmada
894 eski unit/integration ve 80 eski Docker case'inin tamamı korunur. Yeni kapsam
57 integration ve 4 Docker case'idir. Son kaynakla hedefli HTTP/process/Docker
koşusu ayrıca **61/61** geçti. Bunlar geniş suite ile örtüşür; ayrı toplam değildir.

Yerel JUnit kayıtları: `artifacts/s2-04-current-source-all.xml`,
`artifacts/s2-04-current-source-docker.xml`,
`artifacts/s2-04-last-source-regressions.xml`.
Native Windows fixture ACL: **13/13**; production cache/credential native izin
testleri geniş suite içinde ayrıca çalışır. Güncel wheel'in temiz Linux kurulumunda
cache/publication/recovery: **121/121**, farklı UID ile okuma reddi başarılı.
Wheel içindeki değişen kaynak byte'ları çalışma ağacıyla eşleşir.
Güncel wheel'in checkout dışındaki runtime-only temiz kurulumunda CLI/verdict
parity, önceki execution/generation/evaluation/operations profilleri, gerçek
Docker ve Chromium probu geçti: `artifacts/s2-04-current-wheel.json`.
`real_llm_verified=false`, `external_pilot_verified=false` ve `live_api_calls=0`
korunur; offline fixture gerçek LLM veya bağımsız dış pilot sayılmaz.

Sentetik restore schema/integrity/review/source/frozen pin kontrolleri ve gerçek
Chromium current/legacy review hydration, reload, repeat-save, approval ve frozen
invalidation profilleri geçti. Restore gerçek kullanıcı backup'ı değildir.
Native ACL, UI ve restore kayıtları sırasıyla `artifacts/s2-04-native-acl.xml`,
`artifacts/s2-04-review-ui.json`, `artifacts/s2-04-synthetic-restore.json` dosyalarındadır.

Bu yerel doğrulama kaydı hazırlanırken commit/push veya yeni CI koşusu yapılmamıştı.
Yerel sonuçlar Windows
Server 2022 / Python 3.10–3.14 CI matrix doğrulaması yerine sunulmaz. Canlı state,
credential, cache, signing key, snapshot/backup ve koruyucu gözlemci değiştirilmedi;
ücretli API çağrısı yapılmadı. S2-05 başlatılmadı.
