# S2-03: Crash-safe private cache ve teslim durumu

Bu özellik controller kuyruğunu veya worker protokolünü yeniden yazmaz. Otomatik
transport retry, replay, redispatch veya yeniden agent execution eklemez. S2-04
bu paketin dışında kalır. Gerçek kullanıcı cache'leri otomatik dönüştürülmez.

## Kayıt ve güven sınırı

`management/cache.py:EvidenceCache` her job için tek `project/job.entry.json`
envelope yayımlar. Payload report, report checksum, project/job, execution UUID,
spec digest, runner credential fingerprint + server-origin bağı, özel lease ve
teslim metadata'sını birlikte taşır. Envelope checksum bütün payload'a bağlıdır.
Runner credential'ın kendisi saklanmaz. Lease sonraki güvenli teslim için yalnız
owner-only envelope içindedir; terminale veya canonical kullanıcı raporuna eklenmez.
Bu dosya report indirme/upload dosyası değildir. **Envelope'u paylaşmayın.**

Hash, accidental corruption kontrolüdür; imza veya güvenilmeyen OS owner'a karşı
authentication değildir. Server'ın gerçek runner kimliği credential doğrulamasıyla
belirlenir; cache bağı credential fingerprint'idir. Credential/origin değiştirilmiş
bir runner eski cache'i devralamaz. Yeni credential için ayrı cache gerekir.

`project/job.json` önceki canonical report biçimini koruyan türetilmiş export'tur.
Teslim durumu bu dosyadan veya eski `.meta.json` dosyasından çıkarılmaz. Crash tam
envelope publication ile export arasında olursa report envelope içinde korunur;
export henüz bulunmayabilir. Export eksikliği tekrar execution izni değildir.

## Kalıcı durumlar

| Durum | Anlam ve davranış |
| --- | --- |
| `incomplete` | Execution öncesi intent yayımlandı veya özel staging artığı var. Execution/cleanup gerçekleşip gerçekleşmediği belirsizdir; yeniden çalıştırılmaz. |
| `pending_upload` | Bütün report ve binding checksum'ları doğrulandı; sunucu kabulü yerelde henüz doğrulanmadı. Sunucu gerçekten kabul etmiş fakat yanıt kaybolmuş olabilir. |
| `acknowledged` | Aynı job/project ve report digest için mevcut finish yanıtında açık sunucu kabulü alındı ve bu yerelde atomik kaydedildi. Davranış PASS'i anlamına gelmez. |
| `corrupt` | JSON/checksum/binding/izin veya tanınmayan kayıt doğrulanamadı. Teslim kanıtı olarak kullanılamaz. |
| `legacy_unverified` | V1 marker'lı cache. Teslim durumu bilinmez; otomatik import, purge veya upload yapılmaz. |

`PrivateRunner.run_once()` exclusive cache session'ını claim'den önce alır ve
execution, publication, finish ve ack boyunca tutar. Aynı cache'i eşzamanlı başka
process/thread kullanamaz. Process death OS kilidini bırakır. Aynı credential ile
restart mümkündür; pending/incomplete/corrupt/legacy kayıt yeni claim'i fail-closed
olarak `CACHE_RECOVERY_REQUIRED` ile durdurur. Başka job'ın çalıştırılması da böyle
bir unresolved cache varken otomatik devam etmez. Bu konservatif operatör sınırıdır.

Mevcut S2-02 senkron heartbeat ve cancellation/deadline kontrolleri korunur; intent
publication'dan sonra execution başlamadan önce yerel cancel/deadline tekrar kontrol
edilir. Start ACK ile fiziksel execution arasındaki distributed yarış devam eder.

## Publication ve izinler

Yeni kayıt no-overwrite rename ile yayımlanır. Güncelleme aynı protected dizinde
exclusive session altında staged replacement kullanır. Unbuffered write döngüsü,
file fsync ve read-back kontrolü tamamlanmadan rename yapılmaz. Windows'ta native
MoveFileEx write-through, Linux'ta renameat2 no-replace / replace ve directory fsync
kullanılır. Bu yerel filesystem garantisidir; distributed transaction değildir.
Host güç kaybı, bozuk disk/controller veya network filesystem dayanıklılığı vaat edilmez.
Desteklenen publication profili Windows ve Linux yerel filesystem'leridir.
Production cache kalıcı yerel volume'da, OneDrive/senkronize klasörlerin dışında
tutulmalıdır; tmpfs veya ephemeral container layer host/container yeniden yaratımında
kanıtı korumaz. Testlerdeki geçici storage production retention garantisi değildir.

Windows cache güvenlik kontrolü native token User SID, protected DACL, tek allow ACE,
aynı User SID için FullControl ve doğru directory inheritance flag'lerini doğrular.
Root ve oluşturulan nesneler açıkça user-owned yapılır; token default-owner grubunun
aynı olduğu varsayılmaz. POSIX'te owner UID ve group/other bitlerinin kapalı olması
aranır. Symlink/reparse/hardlink kayıtları reddedilir. Mevcut güvenliği hatalı dosya
ve dizinler otomatik onarılmaz. Cache parent'ı da trusted-writer kontrolünden geçer.
Windows admin/SYSTEM ve aynı OS hesabı güvenilen host sınırındadır; ayrı güvenilen
runner OS hesabı önerisi korunur. Cache şifreli değildir.

Credential publication'ın path, no-replace, fsync ve write/read-back primitive'leri
yeniden kullanılır. Credential'ın token-specific `private_file()` callback akışı
cache için kullanılmaz; `credentials.py` ve mevcut enrollment sözleşmesi değiştirilmez.

## Finish kabulü

Mevcut finish endpoint'i kabul edilmiş rapor için geriye uyumlu iki response alanı
ekler: `delivery_accepted=true`, `report_digest`. Yeni endpoint, request zorunluluğu,
DB schema veya migration yoktur. Fields yalnız mevcut doğrulamalardan geçmiş report
ve tamamlanmış aynı report'un idempotent cevabı için döner. Stale/expired/rejected
finish cevabı bu kabul kanıtını üretmez. Açık failure report kabulü de BLOCK kalır.
Alanları vermeyen eski server'ın cevabı acknowledged sayılmaz; rapor pending kalır.

Acknowledgement report hash, job, project, terminal state ve güvenli karar alanlarını
doğrular. Cache yeni PASS hesaplamaz; core evaluator/policy/cleanup kuralları korunur.
ALLOW için completed execution ve verified cleanup gerekir. Ack state write/sync
başarısızsa runner başarı bildirmez; görünür kayıt pending veya tam acknowledged
olabilir. Operatör checksum ve durum kontrolü yapmadan yeniden gönderim varsaymaz.

## Retention, kota ve operatör recovery

TTL yalnız doğrulanmış `acknowledged` kayıtları silebilir. Export önce, envelope sonra
silinir; crash arada olursa kalan acknowledged kayıt sonraki purge için yeterlidir.
Pending/incomplete/corrupt/legacy ve orphan staging TTL yüzünden sessizce silinmez.
Default kota 64 MiB / 1000 kayıt; yeni iş öncesi yaklaşık 8 MiB publication rezervi
ve disk boşluğu aranır. Kota veya disk hatası mevcut geçerli kanıtı overwrite etmez.
Bu project-wide durable quota değil, cache başına admission sınırıdır.

Operatör recovery sırası:

1. Aynı cache için yeni runner/process çalıştırmayın; job ve cleanup durumunu sunucuda
   doğrulayın. Raporun bulunmasını veya terminal job durumunu kabul receipt'i saymayın.
2. `EvidenceCache.scan()` yalnız secret-free kimlik/durum özeti verir.
   `delivery(project, job)` yalnız doğrulanmış pending kayıt için `PendingDelivery`
   döndürür; repr lease/report göstermez. `request_body()` hassas transport içeriğidir;
   loglamayın veya kullanıcı raporuna yazmayın. Bu API kendiliğinden network çağrısı yapmaz.
   `acknowledge_delivery(delivery, receipt)` restart sonrası özgün claim spec'i olmadan
   aynı execution UUID/lease/report bağını doğrulayıp açık kabulü kaydeder. Receipt'i
   kendisi almaz veya icat etmez; retry implementasyonu değildir.
3. Yarım/bozuk kayıtları tamamlanmış rapor gibi birleştirmeyin. Incomplete execution için
   otomatik rerun yapmayın. Eski server ack'i veya kayıp yanıtı manuel durum değiştirerek
   acknowledged'a çevirmeyin. Stale lease cleanup recovery için mevcut operatör akışı
   kullanılabilir; geçmiş ALLOW yeniden üretilmez.
4. `.stage-*` artıkları report/lease içerebilir. Bunları paylaşmayın. Aktif cache session
   olmadığı ve kayıt/server durumu incelendiği doğrulanmadan kaldırmayın. Yalnız doğrulanmış
   orphan'ın tam yoluna yönelik, operatör onaylı işlem uygulayın; wildcard cleanup yoktur.
5. Pending evidence saklama süresi ile veri silme talebi çelişirse güvenli operatör
   kararı gerekir. Bu paket manuel deletion, quarantine veya retry CLI'ı eklemez.

## Doğrulama

Testler geçici filesystem, gerçek subprocess death, native ACL/access denial,
gerçek HTTP ve Docker execution kullanır. Crash sonrası doğrulanmış pending bulunması
execution sayısını artırmaz. Eski Sprint 1 ve S2-01/S2-02 sözleşmeleri korunmalıdır.
Kesin test sayıları sonuç raporunda belirtilir; CI doğrulaması bu yerel paketin
tamamlanmasıyla otomatik yapılmış sayılmaz. Commit/push bu çalışma kapsamında yoktur.

## Yerel kabul kaydı — 26 Kasım 2026

Son kaynakla **894 unit/integration**, **80 gerçek Docker E2E/security** ve
**191 birleşik Sprint 1/S2-01/S2-02/S2-03 regresyonu** başarılıdır; JUnit kayıtlarında
failure/error/skip yoktur. Son grupta 13 native Windows ACL testi vardır. Gruplar
örtüşür. Yeni S2-03 kapsamı 46 integration ve 2 gerçek Docker case içerir.
JUnit dosyaları `artifacts/s2-03-verified-{all,docker,regressions}.xml` içindedir.

Güncel wheel ayrı Linux container'ında 64 cache/credential-publication testiyle
doğrulandı. Testin dış ağı kapalıdır. Ayrı gerçek UID'nin okuma erişimi reddedildi.
Windows production cache ACL'leri bağımsız credential ACL kontrolü ve Authz erişim
reddiyle de doğrulandı; sentetik fixture ACL'si tek başına kanıt sayılmadı.

İzole sdist/wheel build ve checkout dışında runtime-only kurulum başarılıdır.
`artifacts/s2-03-verified-wheel-serial.json` Docker/Chromium/önceki faz profillerini
passed, live_api_calls=0 olarak kaydeder. Wheel'deki dört değişen uygulama modülünün
byte'ları son kaynakla karşılaştırıldı. Gerçek LLM veya gerçek ödeme doğrulaması
iddia edilmez; AG-075 dış pilot kanıtı oluşturulmadı.

Bir önceki eşzamanlı wheel koşusunda BrowserEnvironment startup ve cleanup
unverified oldu; bu sonuç kabul kanıtına dahil edilmedi. Host ve geçici runtime
Playwright sürümleri 1.65.2 ile eşleşiyordu; kesin hata nedeni kayıttan çıkarılamadı.
Yoğun Docker koşuları bittikten sonraki seri tekrar mevcut timeout/cleanup
beklentileri değiştirilmeden geçti. Ayrıca ilk geniş koşuda mevcut strict-marker
testinin beklediği hata metni bulunamadı; tekil ve son tam koşular geçti. Assertion
korundu, yalnız teşhis mesajı geliştirildi; kesin transient neden iddia edilmez.

Regresyonlar gerçek baseline purge kaybını, kayıp/geçersiz ACK'i, bozuk dosya adının
durum çıktısına sızmasını, eksik restart ACK arayüzünü ve zorlanmış staging isim
çakışmasında eski orphan'ın silinmesini yeniden üretti. Process crash gerçek ayrı
process'lerde; disk/fsync/rename hataları gerçek dosyalar üzerinde fault injection
ile doğrulandı. Host diski doldurulmadı ve güç kesintisi simüle edilmedi.

Kaynak/test/doc kapsamı 14 dosyadır. Secret taraması ve diff kontrolü temizdir.
Canlı state/credential/cache/backup/observer üzerinde işlem, migration, commit,
push veya yeni CI koşusu yapılmadı. S2-04 başlatılmadı.
