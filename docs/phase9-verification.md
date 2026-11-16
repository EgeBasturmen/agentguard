# Phase 9: operasyon ve private-pilot güvenilirliği

Güncel durum (17 Kasım 2026): uygulama ve kabul doğrulaması sürüyor; henüz Phase 9
kapanışı ilan edilmedi. Başlangıç `cfb22f00c00f8b437e9497eb3c07ccaedd0153fa`, aynı
`artifacts/phase2/repo` checkout’u ve `codex/development`. Mevcut 754 test korunur.
AG-075 **BLOCKED / PENDING EXTERNAL VALIDATION**, Phase 7 genel kapısı açık kalır.

## Mevcut mimariyle karşılaştırma

| Görev | Önceki durum | Uygulama ve kabul kanıtı |
| --- | --- | --- |
| AG-090 | Transactional queue/lease ve normal cancellation vardı; gerçek owner-crash/load/recovery boşlukları vardı | Processler arası reentrant state lock, gerçek process ölümü/rollback, 4 process × 24 aynı admission, restart’lı 40 cycle; ayrıca gerçek HTTP burst/bounded soak. Browser parent-death cleanup; exact owner/run/container recovery; kayıp lease PASS’e dönmez. |
| AG-091 | Version 1 shape kontrolü, primary artifact TTL vardı; backup/restore, migration ve full-project silme yoktu | Transactional v1→v2 migration/index/history; migration failure rollback; v1’i yükseltmeden backup; ayrı anahtarla HMAC imzalı SQLite snapshot bundle; yeni hedefe restore, credential/runner revoke, TTL purge ve silme intent journal. |
| AG-092 | Per-run model rezervi ve manual pilot planı vardı; reconciliation/idempotent payment recovery boşlukları vardı | Eksik/negatif/orphan ledger admission’ı BLOCK; harici reference’ın birden çok planda kullanılması reddedilir; manual reconciliation ve cancellation idempotent; geri alınan bütçe unstarted live işi durdurur, unknown kullanım sıfırlanmaz. |
| AG-093 | Scope/tenant/CSP vardı; başarısız işlem rate rollback’i, rotation bypass ve TLS accept DoS vardı | Ayrı kalıcı rate counter, principal+key limiti, key/enrollment kapasitesi, bounded TLS handshake; backup traversal/HMAC/journal regression testleri. Threat model ve incident runbook. |
| AG-094 | Bağla→review→run→rapor UI vardı; operasyon funnel ve primary proje silme yoktu | Yetkili operasyon/funnel endpoint’i, güvenli durum mesajları, operatör hesap provisioning CLI; gerçek Chromium/Docker connect→test→report→manual payment/cancel→delete teknik yolculuğu; release/support dokümanı. |

Bağımlılık: state lock/migration → consistent backup/delete intent → credential
quarantine/recovery → quota/payment reconciliation → UI/status → release kabulü.
RunController, broker, evaluator, generation ve release-gate motorları korunur.

## Önce başarısız testlerle doğrulanan sorunlar

Başarısız authenticated işlem rate sayacını rollback ediyordu; rotation yeni sayaçla
limiti aşabiliyordu. Eksik/negatif ledger yeni admission’ı engellemiyordu. Kullanılmış
receipt/idempotent activation ve pending model-budget revoke sınırları eksikti.
Gelecek scenario schema’sı reddedilirken dosyaya önce DDL yazılıyordu. Eksik/eski
silme journal’ı sessizce kabul edilebiliyordu. Pending enrollment kapasitesi yoktu.
TLS handshake accept thread’inde bekleyerek bütün servisi durdurabiliyordu.
Browser helper parent ölünce kaynaklarını açık bırakıyordu. Bu sorunlar RED testinden
sonra düzeltildi; eski testler gevşetilmedi veya skip edilmedi.

## Operasyon profili ve güven sınırı

Tek aktif controller, private state dizini ve yerel/private runner desteklenir.
Thread/process işlemleri aynı state lock’ı kullanır. Eski binary veya doğrudan DB
yazan custom kodun aynı anda çalıştırılması desteklenmez. Bu, HA/distributed lock
veya hostile shared tenancy değildir. Backup/migration/restore bakım penceresinde
controller/runner drain edilerek yapılır; queued/active iş varken backup reddedilir.

Archive HMAC ile doğrulanır; signing key archive’a dahil edilmez ve ayrıca güvenli
aktarılır. İmza encryption değildir. Backup özel/şifreli disk ve erişim kontrollü
offsite storage’da saklanmalıdır; otomatik cloud/KMS veya ücretli servis açılmaz.
Profil sınırı 256 entry, entry başına 64 MiB ve toplam 256 MiB’dir; aşım hata verir.
Restore mevcut hedefe yazmaz; private staging’de hash/integrity/schema doğrular,
sonra yeni hedefe geçirir. Eski API key/enrollment/runner’lar iptal edilir; active
işler otomatik redispatch edilmez. Yeni owner key açık operator recovery ister.

Primary proje silme doğru isim, mevcut admin authority, iptal edilmiş plan ve
quiescent/verified cleanup gerektirir. Signed deletion intent, DB silmeden önce
yazılır; restart kalan silmeyi tamamlar. Minimal UUID receipt tutulur; case DB,
object/job/artifact/member/usage/commercial/audit proje payload’ları primary’den
kaldırılır. Güncel journal backup’tan ayrı korunmalı ve restore’a verilmelidir;
aksi halde geçmiş backup’tan silinmiş veriyi geri getirmeme garantisi yoktur.
Mevcut DB, journal regression/missing journal durumunda fail-closed olur.
Operatörün eski DB ile eski signed journal’ı birlikte seçtiğini platform dışından
tespit etmek mümkün değildir; authoritative latest journal offsite saklama önkoşuludur.

Workspace owner kendi hesabının primary workspace/member/key/audit ve bütün proje
verisini UI/API/CLI ile silebilir; başka workspace korunur. Workspace intent’i de
restore’a uygulanır. Müşterinin private cache’i, indirilmiş raporları, agent image/build
cache’i ve eski backup kopyaları uzaktan silinmiş sayılmaz. SSD fiziksel shredding
ve tüm harici kopyaların otomatik erase’i iddia edilmez.
Artifact TTL restore sonrasında yeniden uygulanır; süresi dolan kanıt ALLOW olmaz.

Ödeme profili hâlâ **operator-recorded manual external payment**’tır. Reconciliation
reference/status tutarlılığını doğrular; banka/processor faturası veya ödeme gerçekleşmesi
bağımsız doğrulanmaz. Sentetik teknik reference gerçek ödeme/pilot kanıtı değildir.
Token/maliyet ledger’ı provider-reported/configured estimate; unknown rezerv tutulur.
Privileged operator’ın DB/key’i birlikte değiştirmesine karşı attestation veya
matematiksel invoice üst sınırı yoktur. Remote Docker daemon orphan reaping kapalıdır;
yalnız yerel daemon/exact label/dead owner doğrulamasıyla resource cleanup yapılır.
Bu receipt tek başına kayıp run’ı veya görünmeyen dış etkiyi PASS yapmaz.

## Load/soak ve pilot hazır oluş sınırı

24 duplicate HTTP isteği/8 thread ve 4 process’in aynı admission yarışları; restart’lı
40 cycle ve parametrik 1–80 cycle controller soak profili vardır; pause 0–60 saniyedir.
Gerçek HTTP probe
8 sentetik principal kullanır, varsayılan 120/min key/principal ve 600/min IP limitleri
gevşetilmez. Queue/cancel yük testi gerçek agent throughput’u değildir; gerçek
Docker/Chromium execution/crash testleri ayrı kanıttır. CI p95 bir ölçümdür, müşteri
donanımı için SLO değildir. Saatler/günler süren production soak veya bağımsız pentest
tamamlandı sayılmaz. Yerel 30 dakikalık controller soak ayrıca çalıştırılır; aynı
ölçüm worker throughput veya günler süren production qualification değildir.
Pilot başlangıcında düşük concurrency ve operator gözetimi gerekir.

Public signup/email/SSO ve tamamen self-service onboarding yoktur. Hesap oluşturma
operator CLI, bağlantı/fixture/rule/oracle açık kullanıcı girdisidir. Yeni funnel
configuration kaydını verified run’a veya self-service müşteri başarısına çevirmez.
“Agent’ını Bağla → Test Et → Rapor Al” çalışır; otomatik framework keşfi iddiası yoktur.
AG-075 olmadan dış müşteri tekrar kullanım/pilot başarısı ve genel GA hazır oluşu
ilan edilmez. [Runbook](operations-runbook.md), [threat model](security-threat-model.md),
[release/support](phase9-release.md).

## Test ve CI kayıtları

Targeted migration, backup/delete/retention, payment/quota, process crash ve gerçek
Docker/TLS/Chromium testleri geçti. Gerçek browser yolculuğu
`artifacts/phase9-ui-journey.json` başarılı; ödeme sentetik/manuel teknik fixture’dır.
İlk HTTP burst `artifacts/phase9-controller-soak.json` ölçümü test rate override’ı
kullanıyordu; final kabulün varsayılan limit kanıtı değildir. Yerine 8 principal ile
varsayılan limitlerde `phase9-default-limits-soak.json` çalıştırılır.
Son tüm suite, temiz wheel ve CI sayıları doğrulandıktan sonra eklenecektir.

Ara geniş suite **788/788** geçti (`phase9-final-tests.xml`, 642,43 saniye).
Yeni account/owner guard testleriyle sonraki **791** koşusunda iki eski controlled
execution testi tool/Docker launch deadline’ından başarısız oldu; **789 geçti**
(`phase9-release-tests.xml`, 1138,30 saniye). Timeout/assertion değiştirilmedi.
Aynı iki test izole koşuda **2/2 geçti** (54,60 saniye); salt okunur docker info
ölçümü ~5,22 saniyeydi. Yerel Docker gecikmesi olası etken; kesin kök neden olarak
sunulmaz. Başarısız JUnit korunur. Son unit katmanı **387/387** başarılıdır
(`phase9-unit-release.xml`); mevcut 754 test hiçbir şekilde kaldırılmadı/gevşetilmedi.

Private File.text await’i oturum değişiminden sonra yeni key ile eski canonical
kanıtı upload edebiliyordu. Gerçek browser testi önce AssertionError verdi; backend
tenant guard’ının reddi korundu. Dosya okumaları sonrası epoch kontrolüyle
`phase9-file-read-fenced.json` gerçek Chromium/Docker/account-delete yolculuğu geçti.
`private_file_read_fenced=true`; sentetik manual payment, dış pilot/gerçek ödeme değil.
Son 792 test katmanı, yeni clean wheel ve CI kabulü ayrıca doğrulanacaktır.
