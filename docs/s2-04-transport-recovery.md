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
Bağlantı native socket timeout'u ile sınırlıdır; bağlı TCP/TLS I/O nonblocking
çalışır ve kalan deadline'ı en çok 25 ms'lik select bekleyişlerinde tekrar kontrol
eder. Windows'ta başka handle'dan shutdown timed read'i hemen uyandırmayabilir;
deadline garantisi bu davranışa bağlı değildir. TLS handshake aynı deadline içindedir;
sertifika zinciri ve hostname doğrulaması kapatılmaz.
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

## İlk yerel doğrulama (27 Kasım 2026; a3a3f2b öncesi)

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

## İlk CI ve transport düzeltmesi

`a3a3f2b9f7a8edd5e00289c0cd585d0e48361386` için
[CI 45926523434](https://github.com/EgeBasturmen/agentguard/actions/runs/45926523434)
9 başarılı / 4 başarısız job ile bitti. Windows 3.12'de 15, 3.13'te 5, 3.14'te
3 integration failure vardı; Phase 1 gate doğru şekilde başarısız oldu. Bunlar
S2-04 kapanış kanıtı sayılmaz. Ubuntu 3.10–3.14, Windows 3.10/3.11 ve Docker geçti.

23 failure'ın 20'si `elapsed < 2 AND server_received == expected` birleşik
assert'inde, üçü non-finish RPC için `server_received == 1` assert'indeydi.
Eski log birleşik koşulun hangi tarafının bozulduğunu kaydetmez. JUnit süresi setup
ve teardown içerir; bu süre RPC elapsed yerine kullanılamaz. CI'daki her failure'ı
yalnız scheduling, Python sürümü veya DNS olarak etiketlemek için kanıt yoktur.

| İlk CI ortamı | Birleşik süre/receive assert'i (case mode'ları) | Non-finish receive assert'i |
| --- | --- | --- |
| Windows 3.12 | reset, timeout, trickle, trickle_headers, truncated, server, auth, forbidden, integrity, json, bad_error, legacy (12) | claim, exchange, heartbeat (3) |
| Windows 3.13 | reset, timeout, server, auth, integrity (5) | yok |
| Windows 3.14 | timeout, truncated, json (3) | yok |

Kontrollü DNS gecikmesi, normal iki client attempt'in sıfır server receive ile
sonlanabileceğini gerçek yerel HTTP ile yeniden üretti. Non-finish çağrılarda da
tek client attempt / sıfır receive geçerliydi; pending evidence korundu.
Attempt, receive ve kabul aynı olay değildir. Düzeltilen testler bunları ve RPC
elapsed/deadline/hata kategorisini ayrı kaydeder; secret, lease, body, exception
metni veya tam credential path'i assertion mesajına eklenmez.

Yanıt sınıflandırma testleri üretimin mevcut 3s request / 15s geçiş profilinde
gerçek HTTP cevabının gelmesini ve beklenen kategoriyi zorunlu tutar. Stall,
partial header ve partial body fixture'ları event ile durur; kısa sleep'in
timeout'tan önce/sonra bitmesine güvenilmez. Önceki kısa sınırlar kaldırılmaz:
ayrı DNS/connect/TLS/header/body testleri 50 ms ve 3s mantıksal deadline'ı faz
bariyerinden sonra tetikler. Gerçek timer ile DNS ve TLS stall testleri ayrıca
çalışır. Connect fazındaki kernel timeout kontrollü socket alt sınıfıyla enjekte
edilir; bu tek başına her ağ/OS için fiziksel connect süresi garantisi değildir.
Diğer fazlarda gerçek TCP, TLS ve HTTP I/O kullanılır; gerçek HTTP testleri mock
yanıtlarla değiştirilmez. Geçerli TLS'de 1 MiB POST/response tam olarak doğrulanır;
güvenilmeyen sertifika ve hostname uyumsuzluğu bir attempt ile kalıcı ret verir.

Ayrı üretim kusuru: Windows socketpair deneyinde duplicate shutdown, original
shutdown ve close timed read'i hemen kesmedi. Eski deadline kodunun TLS/header/body
fazlarında native 3s socket timeout'unu bekleyebildiği, explicit expiry sonrası
1s içinde dönme testlerinin üçünde failure ile kanıtlandı. Nonblocking deadline
polling düzeltmesi sonrası bu testler geçti. Bu bulgu tarihsel 23 failure'ın
tamamının nedeni olarak sunulmaz. Production request/total/retry/backoff değerleri
artırılmadı; admission, receipt, cache publication ve queue şeması değiştirilmedi.

Yeni test yardımcısı `tests/s2_transport_support.py`, sdist'ten test çalıştırmanın
bozulmaması için MANIFEST'e açıkça eklenir. OpenSSL yalnız geçici test sertifikası
üretir; anahtar testin owner-only geçici dizininde kalır, commit veya log'a yazılmaz.
Windows'ta Git'in OpenSSL'i, Linux'ta sistem OpenSSL'i kullanılır; bulunamazsa
skip/xfail yerine test fail-closed olur.

### Düzeltmenin yerel kabul kanıtları

Son kaynakla **970 unit/integration + 84 gerçek Docker** testi geçti; JUnit
failure/error/skip sıfırdır. İlk S2-04 kaydındaki 951 ve 84 case kimliği korunur;
19 yeni integration case eklenir. S2-04 integration profili artık 76 case'tir,
ilgili Docker profili dört case olarak korunur. Son kayıtlar
`artifacts/s2-04-revision-all.xml` ve `artifacts/s2-04-revision-docker.xml`.

Transport dosyasının 40 case'i normal, bir ve iki düşük öncelikli test CPU worker'ı
altında ayrı ayrı geçti: **120/120**. Yük worker'ları yalnız testin kendi process'leridir
ve stop event'iyle sonlandırılmıştır. Native Windows fixture ACL **13/13**;
production cache/credential izin testleri geniş suite içinde ayrıca geçti.
Yerelde desteklenen Python 3.11 ile çalışıldı; yerel sonuçlar 3.10–3.14 matrix
kanıtı yerine kullanılmaz. Yeni CI sonucu commit sonrasında ayrı doğrulanmalıdır.

İzole sdist'ten wheel build başarılı; sdist yardımcısı ve wheel üretim kaynakları
kontrol edildi. Kurulu wheel üzerinde Linux/POSIX cache/publication/recovery/TLS
profili **140/140** geçti; farklı UID okuması reddedildi. Sentetik restore integrity,
schema, review/source fingerprint ve frozen suite pin kontrolleri geçti. Bu kabul
canlı kullanıcı backup'ı, ücretli LLM veya bağımsız dış pilot kanıtı değildir.
Güncel wheel'in checkout dışındaki runtime-only Docker/Chromium probu ve
current/legacy review UI de geçti: `artifacts/s2-04-revision-current-wheel.json`,
`artifacts/s2-04-revision-review-ui.json`. Model/pilot kanıt bayrakları false ve
ücretli API çağrısı sıfır olarak korunur.

Düzeltmenin Git kapsamı beş dosyadır: `rpc_deadline.py`,
`tests/test_s2_delivery_transport.py`, `tests/s2_transport_support.py`,
`MANIFEST.in` ve bu belge. Production retry/deadline değerleri, finish protokolü,
credential publication, admission ve queue şeması değiştirilmez. S2-05 kapsamına
geçilmez. Custom transport ve OS DNS sınırlamaları önceki profille aynıdır.

### Claim'e bağlı withdrawal fixture'ı

`bea4159f412c9c1b10f7e2ebc6b0d27056e0d68c` için
[CI 45997402514](https://github.com/EgeBasturmen/agentguard/actions/runs/45997402514)
10 başarılı / 3 başarısız job ile bitti. Tüm 40 transport case'i on matrix
ortamında geçti; Windows 3.10 ve 3.12'de yalnız recovery `[withdraw]` case'i
başarısız oldu ve gate kapalı kaldı. Diğer ortamlar ve Docker geçti. Bu koşu da
tam kapanış sayılmaz.

Bu fixture `objects(...)[0]` nesnesini retire ediyordu. Üretim liste sözleşmesi
`ORDER BY created,id` kullanır; liste başının frozen suite üyesi olduğu garanti
edilmez. Özellikle eşit timestamp'ler sıralamayı etkileyebilir; tarihsel CI'daki
timestamp değerleri kaydedilmediğinden bunun her koşudaki nedeni olduğu iddia
edilmez. Gerçek HTTP/job/repository ile nonmember-first sıralaması kurulduğunda
aynı assertion failure yeniden üretildi: ilgisiz case retire edilirken mevcut
job'ın ALLOW kalması, withdrawn member'ın yanlış kabulü olarak yorumlanamaz.

Fixture artık yönetim nesnesini claim'deki scenario ID, revision ve fingerprint
üçlüsüne bağlar. Eksik, yanlış revision veya çift eşleme test başlamadan reddedilir.
Gerçekten pinned case withdraw edildiğinde BLOCK, verified cleanup ve tek
artifact/usage koşulları korunur. Dört ek regression, eski case kimliklerini ve
üretim approval/finish kodunu değiştirmeden bu varsayımı doğrular. Bu ek düzeltme
yalnız `tests/test_s2_delivery_recovery.py` ve bu belgeyi değiştirir; runtime wheel
kodu önceki doğrulanan üretim koduyla aynıdır.

Geniş yerel tekrar mevcut Phase 9 rate-limit testinde ayrıca bir failure gösterdi:
beklenen RATE_LIMITED oluşmadı. Yalnız başarısız sentetik testin abuse DB'si salt
okunur incelendi; komşu iki 60s pencerede ikişer key satırı ve toplam beşer attempt
vardı. Bu, test işlemleri sırasında window rollover'ını doğrudan doğrular; kimlik
ve key değerleri seçilmedi. Aynı pencere varsayımı duvar saatiyle kurulamaz.
`test_failed_authenticated_operations_consume_rate_budget` artık kendi servisinin
saatini sabitler; mevcut RATE_LIMITED beklentisi korunur. Saat 60s ilerletildikten
sonra yeni pencerenin geçerli isteğe izin verdiği de doğrulanır. Üretim rate-limit
kodu veya limiti değiştirilmez. Bu küçük fixture düzeltmesi
`tests/test_phase9_operations.py` dosyasına eklenir; eski test kimliği korunur.

Son fixture kaynağıyla **974/974 unit/integration** geçti;
`artifacts/s2-04-final-closure-all.xml` failure/error/skip içermez. Önceki 970 case
korunur; yeni dört case yalnız claim eşlemesini doğrular. S2-04 integration profili
80 case'e yükselir, eski dört Docker case'i değişmez. İlgili gerçek HTTP/repository
ve Docker tekrarında **24/24**, clock/recovery odaklı tekrarda **21/21** geçti.
Üretim byte'ları değişmeyen kurulu wheel ile, yalnız güncel iki test dosyası
readonly bağlanarak yapılan Linux kontrolü **145/145** geçti. Önceki tam Docker
84/84, native ACL 13/13, temiz wheel ve Chromium kanıtlarının üretim kaynağı
aynıdır; son commit'in matrix ve paket kanıtı ayrıca CI'da doğrulanmalıdır.
