# S2-05: Job-bound operatör cleanup recovery

Başlangıç kaynağı `491ea76f7bab6208cd117638ba7990891e5f97aa`, aynı checkout ve
`codex/development`. Bu çalışma normal finish transport recovery'sinden ayrıdır;
S2-04 yalnız pending canonical sonucu sınırlı/idempotent şekilde teslim eder.
Operatör recovery'si execution başlatmaz, finish ACK üretmez ve release yetkisi vermez.

## Kimlik ve güven sözleşmesi

Claim transaction'ı lease ile birlikte `execution_intent_v1` kaydı oluşturur.
Mevcut schema-v2 `recovery_receipts` tablosu kullanılır; DDL/migration gerekmez.
Sunucu HMAC'si job/project/runner, cache execution ID, lease hash, spec digest ve
her frozen case'in ID/revision/fingerprint/run ID'sini kapsar. Raw lease DB receipt'e
yazılmaz. Claim'deki ek `recovery_binding` alanı eski runner tarafından yok sayılabilir.

Yeni runner bu execution ID'yi private cache envelope'a, case run ID'lerini de
controller manifest'ine ve gerçek container/run yaşam döngüsüne taşır. Agent worker
sunucu signing key'ini veya recovery credential'ını almaz. Cache'in checksummed
envelope'u private yerel veridir; checksum hostile owner'a karşı authentication değildir.

Tam execution raporu cache'e yayımlandıktan sonra runner bir kez
`POST /api/runner/recovery-evidence` çağırır. Güncel runner yetkisi, issued lease hash,
job/project/execution/spec bağları, eksiksiz case seti, started run/container/image/
source/worker kimlikleri ve report digest doğrulanır. Sunucu bunların imzalı özetini
`execution_evidence_v1` olarak saklar. Aynı kayıt idempotent, replacement/conflict ve
başka job'a ait run/container replay'i reddedilir. Bu endpoint execution izni vermez;
lease'i uzatmaz veya resurrect etmez. Süresi dolmuş lease'in önceden issued hash'i
yalnız geçmiş cleanup kanıtının kaydı için kullanılabilir; güncel runner yetkisi gerekir.

Registration transport retry'sı yoktur. Registration doğrulanamazsa normal finish
akışı çalışabilir; fakat eksik server registration operatör recovery yetkisi sayılmaz.
Kayıp cevap kabul olup olmadığını kanıtlamaz. Yetkili operatör mevcut private envelope
ve kayıt durumunu incelemelidir; explicit aynı registration'ın idempotent tekrarını
uygulama API'sinden yapabilir. Bunun için yeni claim veya agent execution yapılmaz.

## Yetkili recovery

Yerel `management recover-job` güncel project-admin credential dosyasını ve private
`.entry.json` envelope'u ister. Ek HTTP yüzeyi
`POST /api/projects/{project}/runs/{job}/recover` aynı güncel admin doğrulamasını yapar.
HTTP gövdesi `report` (private envelope), `operator` ve `reason` içerir; audit actor'ı
caller'ın bildirdiği isimden değil doğrulanmış credential'dan alınır. Yetki validation
sonrasında/commit öncesinde tekrar kontrol edilir. Revoked/expired credential, yanlış
proje ve cross-tenant erişimi reddedilir. Yerel state erişimi uzaktan admin yetkisinin
yerine geçmez. Token CLI argümanı değildir; `--credential-file` kullanılır.

Mevcut `error` job'ın seal'i, issued binding, kayıtlı report/runtime hash'i ve private
envelope eşleşmelidir. Cleanup sonucu yalnız bu job/execution'a aittir; aynı suite'in
başka job'ına taşınamaz. Önceden kaydedilmiş geçerli proof, runner daha sonra revoke
edilmiş olsa da güncel admin tarafından historical cleanup için incelenebilir;
revoked runner yeni proof kaydedemez. Bu işlem runner yetkisini geri vermez.

Control DB transaction/coordination kilidi receipt, cleanup, seal ve audit'i atomik
tutar. Aynı proof'un duplicate/concurrent recovery'si tek cleanup receipt/audit
oluşturur. Conflict veya crash/rollback yarım kabul üretmez. Result hash'i olmayan
bilinmeyen execution'ın cleanup sonucu da seal'lenir; bu, bir finish acceptance
uydurmaz. Bozuk error cleanup seal'i API/UI'da verified olarak sunulmaz.
Operatör cleanup recovery'si yerel cache'i `acknowledged` yapmaz. Finish acceptance'ı
doğrulanmamış pending evidence ve cache blocker korunur; dosyayı elle düzenleyerek,
silerek veya yeni execution başlatarak belirsizlik giderilmiş sayılmaz. Bu pakette
otomatik cache kapatma veya unknown-usage uzlaştırma mekanizması eklenmez.

Job `error` ve karar `BLOCK` kalır; behavioral PASS/ALLOW üretilmez. Artifact yaratılmaz,
retention ile kaldırılan kanıt canlandırılmaz. Usage satırları ve unknown compute/model
rezervleri değiştirilmez. Receipt metadata gerçek logical storage kotasına dahildir;
kota aşımı fail-closed'dur. Mevcut S2-04 failure receipt'i meşru cleanup güncellemesi
sonrasında yeniden seal'lenerek aynı digest/lease için BLOCK olarak alınabilir.

## Uyumluluk ve sınırlar

Normal claim/heartbeat/finish yolları korunur; ek claim alanı ve endpoint additive'dir.
Eski server yeni binding göndermiyorsa yeni runner eski normal execution/delivery
yolunu kullanır. Yeni server eski runner'ın normal finish'ini destekler. Eski cache
v1/v2 kayıtları S2-03/04 kurallarıyla okunur veya fail-closed tutulur; eksik recovery
binding uydurulmaz. Yeni envelope'un eski binary ile downgrade kullanımı desteklenmez.

**Güvenlik sıkılaştırması:** eski `recover-job --report canonical.json` çağrısı ve
kimliksiz `recover_job()` artık cleanup yetkisi değildir. Yeni admin/project seçenekleri
ve server-bound envelope gerekir. Bu, canlı kayıtları migrate/repair etmez. Eski
incident'te gerekli binding yoksa job BLOCK/cleanup-unverified kalır; operator exact
resource cleanup ve ayrı usage/incident incelemesini sürdürür. Kanıt dosyasını elle
yeniden imzalama, binding ekleme veya credential kopyalama recovery prosedürü değildir.

Bu profil Python/container agent'ın controller-observed Docker manifest'ine yöneliktir.
HTTP agent'ın uzaktaki lifecycle'ı gözlenmiş sayılmaz; bu kanal ona cleanup attestation
üretemez. Customer runner'ın veya signing key sahibi operatörün bütün raporu bilerek
uydurmasına karşı bağımsız remote attestation yoktur. HMAC server record bütünlüğünü
korur; gerçek Docker daemon'ını uzaktan attest etmez. Signing key, host/OS admin ve
trusted runner sınırları geçerlidir. Registration replay kontrolü mevcut private-pilot
receipt hacminde tarama yapar; büyük ölçek için indekslenmiş resource registry gelecekte
değerlendirilebilir. Belirsiz execution otomatik tekrar edilmez.

## Yerel doğrulama kaydı

İlk iki regresyon eski kodda raw/başka job raporunun kabulünü yeniden üretti.
Storage metadata hesabı, exact cleanup üyeleri, unknown execution seal'i, doğrulama
sırasında auth expiry ve error-seal presentation için ayrı red testler kaydedildi.
Testler yalnız geçici private filesystem/SQLite/HTTP/process/Docker fixture'larını
kullanır. Son kaynakta **1032/1032 unit/integration** (398 + 634), **88/88 gerçek
Docker**, **57/57 hedefli S2-05 integration** ve **13/13 native Windows fixture ACL**
geçti; failure/error/skip yok. Önceki 975 unit/integration ve 84 Docker case kimliği
korunur. S2-05 57 yeni integration ve dört yeni gerçek Docker case'i ekler.
Windows production cache/credential/evidence-file izinleri ayrıca bu suite içindedir;
fixture ACL testleri onların yerine kanıt olarak kullanılmaz.

İlk geniş koşuda 1024 pass / bir failure vardı: error-seal kontrolünün açığa çıkardığı
S2-04 ilk/replay receipt farkı. Seal yazıldıktan sonra güncel satırın okunmasıyla
düzeltildi; dokuz ilgili case ve yukarıdaki son tam suite geçti. Bazı ilk fixture
hazırlıkları da düzeltildi; bunlar ürün açığı veya başarılı kabul kanıtı sayılmaz.

Son kaynaktan izole sdist ve sdist'ten wheel build başarılı. Wheel içindeki tüm Python
runtime byte'ları checkout ile eşleşir. Kurulu wheel'in ağsız/read-only POSIX grubunda
**203/203** geçti; ayrı gerçek UID'nin private cache okuması reddedildi. Checkout
dışındaki temiz runtime-only kurulum/CLI/verdict/parity ve Phase 0–9 profilleri
başarılı; gerçek LLM/API veya ödeme doğrulaması yapılmadı. Ayrıca temiz kurulumdan
`--docker --browser` smoke geçti: controlled Docker, private runner/authorization,
gateway/worker, generation/baseline/regression, gerçek Chromium/team, operations/
backup/billing profilleri true; `live_api_calls=0`, `external_pilot_verified=false`.
Bu paket smoke'u yeni operatör akışının ayrı 57 integration + dört Docker testinin
yerine sunulmaz; kurulu runtime'ın önceki profillerini koruduğunu gösterir.

Başlıca kayıtlar: `artifacts/s2-05-final-all.xml`, `s2-05-final-docker.xml`,
`s2-05-final-binding.xml`, `s2-05-native-acl.xml`, `s2-05-clean-wheel.json` ve
`s2-05-clean-docker-wheel.json`.
CI bu yerel sonuçların yerine gösterilmez. Commit/push/CI bu implementasyon onayının
kapsamında değildir; referans HEAD ve index korunur. Canlı state/credential/cache/
signing key/backup ve koruyucu gözlemciye işlem uygulanmadı. S2-06 başlatılmadı.

## Değişiklik manifest'i

Yönetim kaynakları:

- `src/agentguard/management/operator_recovery.py` (yeni)
- `src/agentguard/management/queue.py`
- `src/agentguard/management/cache.py`
- `src/agentguard/management/runner.py`
- `src/agentguard/management/results.py`
- `src/agentguard/management/operations.py`
- `src/agentguard/management/http.py`
- `src/agentguard/management/cli.py`
- `src/agentguard/management/limits.py`

Issued run ID'nin mevcut execution akışında taşınması:

- `src/agentguard/generation/execution.py`
- `src/agentguard/integrations/plan.py`
- `src/agentguard/integrations/sdk.py`
- `src/agentguard/orchestration/controller.py`

Testler ve belgeler:

- `tests/test_s2_operator_recovery.py` (yeni)
- `tests/test_s2_operator_recovery_docker.py` (yeni)
- `tests/test_phase9_docker.py` (mevcut test kimliği/sözleşmesi korunur)
- `tests/test_s2_delivery_docker.py` (mevcut test kimliği/sözleşmesi korunur)
- `docs/operations-runbook.md`
- `docs/s2-05-operator-recovery.md` (yeni)

Toplam 19 Git dosyası; test artefaktları, build çıktıları ve yerel yardımcılar
bu manifest'e dahil değildir. Önerilen commit: `fix: bind operator cleanup recovery to job execution evidence`.
