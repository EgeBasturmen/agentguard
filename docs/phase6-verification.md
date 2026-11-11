# Phase 6: regression ve CI

Başlangıç: aynı checkout, `codex/development`, doğrulanmış Phase 5 HEAD
`64dce93c7f841c31010f143bf97c6dd105084e74`. Phase 0–5'in 631 testi korunur.
Ana referans ROADMAP §9, Phase 6 AG-060–064 ve faz kapısıdır. Phase 6 tamamlanmadı.

## Mevcut mimariyle karşılaştırma ve sıra

| Görev | Mevcut temel | Eksik iş ve doğrulama |
| --- | --- | --- |
| AG-060 | ScenarioRepository review/approve/freeze; Snapshot; SuiteExecution; ComparisonManifest ve controller RunResult | Baseline için ayrı immutable kayıt, yaşam döngüsü, actor/reason audit, promote CAS, artifact/policy/dataset/evaluator bağları. Yeni baseline modülü mevcut suite motorunu kullanır. |
| AG-061 | Her SuiteRunner attempt'inde yeni environment; deterministic replay adapter'ı; tekil RegressionComparator | Paired repeats, tüm attempt geçmişi, pass rate/belirsizlik, açık tolerans ve owner/expiry içeren flaky quarantine. Başarı gelene kadar FAIL silme ve hard gate'i istatistikle geçersiz kılma test edilecek. |
| AG-062 | Unit/integration/Docker CI; JSON/HTML run/comparison raporu | PR/scheduled/release suite seçimi, zorunlu critical case kapsamı, ürün suite JUnit export'u. Gerçek good→bad değişiklik CI'da gerekçeli BLOCK üretmeli. Pytest JUnit tek başına ürün suite export'u değildir. |
| AG-063 | Strict dataset import/export; approved replay entries; bounded HTTP/FS adapters | Sürümlü repro paketi, hash/compatibility kontrolü, varsayılan hassas veri minimizasyonu, explicit export policy. Traversal/symlink/secret/PII sızıntısı ve sessiz live fallback engellenecek. |
| AG-064 | effective_pass, ComparisonResult, strict v1/v2 RunReport loader; CLI/HTML parity | Suite/baseline/repeat/profile kararlarını kapsayan tek public gate API ve belgelenmiş exit-code/version sözleşmesi. SDK/CLI/JSON/HTML/JUnit aynı kanıtta aynı kararı vermeli. |

Uygulama sırası AG-060 → AG-061 → AG-062 → AG-063 → AG-064. AG-061 baseline
kimliğine; AG-062 repeat/gate politikasına; AG-063 frozen kimlikler ve mevcut
replay sözleşmesine; son bütünleştirme bütün bu sözleşmelere bağlıdır.
Yeni bir execution/comparison motoru veya senaryo review sistemi yazılmayacak.

Güvenlik öncelikleri: eksik/silinmiş case veya assertion; değiştirilmiş manifest;
güncelliğini yitirmiş approval; yarışan baseline promotion; başarılı attempt seçme;
policy/cleanup/unknown usage'nın toleransla PASS yapılması; export'ta sır/veri
sızıntısı; güvenilmeyen rapor sürümünün kabul edilmesi. Ücretli API çağrısı gerekmez.

## AG-060 uygulaması

- `regression/baseline.py`: FrozenBaseline, checked_execution ve ayrı yerel
  BaselineRepository. Phase 4 ScenarioRepository tabloları değiştirilmez.
- Frozen içerik: approved suite snapshot/case revision/review fingerprint'leri,
  runtime/tool/environment/policy konfigürasyonları, comparison/evaluator
  manifest'leri, tam suite execution ve case başına SHA256 artifact kimliği.
  8 MiB kayıt sınırı; gelecekteki schema version, eksik/failed suite ve bozuk
  verdict/fingerprint reddedilir.
- Controlled Python source veya container image digest'i controller manifest'iyle
  karşılaştırılır. Observed HTTP agent'ın binary artifact'ı gözlenemez; bu profilde
  operatörün verdiği sürüm digest'i yalnız declared identity'dir, binary doğrulaması
  veya uzak deployment attestation değildir.
- `create → approve → promote → retire`; promote eski aktif baseline'ı supersede
  eder, eski içeriği silmez/değiştirmez. Retired baseline okunabilir ama active olmaz.
- Approve/promote sırasında scenario repository yeniden kontrol edilir; retired,
  superseded veya stale review/suite aktive edilemez. Historical baseline get'i
  eski senaryonun aktif kalmasını gerektirmez.
- Aktör ve gerekçe her mutasyonda zorunlu. UTC timestamp, sıralı ve hash zincirli
  API üzerinden append-only audit (en fazla 1000 event), SQL transaction ve expected_active
  compare-and-set ile eski bilgili writer aktif pointer'ı sessizce değiştiremez.
- Hash'ler authentication/imza değildir. Bu tek güvenilen yerel operatör/OS dosya
  erişimi profilidir; actor beyanı doğrulanmış kullanıcı kimliği veya SaaS RBAC
  sayılmaz. Workspace/project authorization Phase 7 kapsamındadır.
- CLI `baseline create/approve/promote/retire/show`, mevcut Typer app'ine eklendi.
  CLI payload/exception metni yazdırmaz; hatalı işlem exit=2 verir. Anahtar/model
  çağrısı veya otomatik promote yoktur. Veritabanı private yerel evidence içerir;
  dışa aktarım/redaction AG-063 tamamlanmadan güvenli repro paketi iddia edilmez.

Örnek akış (ücretli servis çağırmaz; mevcut approved suite execution kullanılır):

```text
agentguard baseline create release suite-run.json --artifacts artifact-pins.json --db baselines.sqlite --scenario-db scenarios.sqlite --actor operator --reason "Verified initial baseline"
agentguard baseline approve BASELINE_SHA256 --db baselines.sqlite --scenario-db scenarios.sqlite --actor reviewer --reason "Reviewed immutable evidence"
agentguard baseline promote BASELINE_SHA256 --db baselines.sqlite --scenario-db scenarios.sqlite --actor operator --reason "Release baseline"
agentguard baseline show release --db baselines.sqlite
```

`artifact-pins.json`: exact case ID → SHA256 mapping. Controlled çalıştırmada
digest suite report'taki source_digest/image_id'den alınır; source dosyasının basit
tek-file hash'i ile aynı olduğu varsayılmaz. Sonraki promote işleminde
`--expected-active OLD_BASELINE_SHA256` zorunludur; default None yalnız henüz aktif
baseline bulunmadığında kabul edilir. Baseline onayı scenario review yerine geçmez.

## İlk AG-060 test kaydı (tarihsel)

Önce missing baseline module ve ardından stale approval, audit sequence, UTC audit
timestamp ve public CLI regression'ları RED çalıştırıldı. İlk hedefli 80 test geçti
(baseline, approved execution, comparison integrity ve eski CLI parity).
Güncel tüm suite **640/640 başarılı** (373 unit, 221 integration, 46 gerçek Docker;
failure/error/skip yok), 278,71 saniye. Phase 0–5'in 631 testi korunur; 9 yeni
baseline integration case'i gerçek yerel HTTP gözlemiyle test edilir.
JUnit: `artifacts/phase6-ag060-tests.xml`.

İzole sdist ve sdist'ten wheel oluşturuldu. `artifacts/phase6-ag060-wheel.json`
checkout dışında runtime-only temiz kurulumda passed: önceki CLI/verdict parity,
Phase 3/4/5 kontrolleri ve `phase6_baseline=true`. Probe gerçek kurulu paketin
ürettiği approved Docker suite'inden baseline yaratır; yanlış artifact hash'ini,
approve edilmeden promote'u ve retired active lookup'u reddeder. Geçerli
create → approve → promote → show → retire akışı ve audit sırası da doğrulandı.
Canlı LLM/API çağrısı yok; real_llm_verified=false offline probe kapsamını belirtir.

Değişen dosyalar: `regression/baseline.py`, `regression/cli.py`, mevcut CLI main,
`tests/test_phase6_baseline.py`, `scripts/check_wheel.py`, ROADMAP ve bu rapor.
Dal `codex/development` ve HEAD `64dce93` korunur; commit/push/yeni CI yapılmadı.
İlk baseline kilometre taşı yerelde doğrulandı; Phase 6'nın tamamı için bu sonuç
tek başına CI kabul kanıtı değildir.

Bu kayıt ilk AG-060 kilometre taşına aittir; sonraki uygulama aşağıdadır.

## AG-061: repeats, belirsizlik ve flaky policy

`regression/repeats.py` mevcut SuiteRunner ile her attempt için yeni environment/worker
oluşturur. RepeatBatch bütün sıralı attempt'leri, frozen snapshot, policy ve profile
kimliğini mühürler. Kopyalanmış execution veya tekrar kullanılan controlled run ID
bağımsız örnek sayılmaz. Paired comparison, pass rate, flaky işareti ve Wilson %95
aralıkları raporlanır. Bu aralıklar bağımsız Bernoulli gözlemleri varsayar; seed veya
küçük örnek sayısı genel doğruluk garantisi değildir. Stochastic modda asgari örnek
sayısı 3–20 arasında açık politikadır; yetersizlik INCONCLUSIVE/BLOCK üretir.

Tolerans yalnız açıkça belirtilen noncritical tool count farkını kapsar; policy,
cleanup, assertion, kaynak veya state hatasını örtemez. Varsayılan bütün case'ler
critical'dır. Quarantine owner, gerekçe ve UTC expiry ister; coverage kaybı görünür,
quarantine gate'i geçirmez. Süresi geçmiş veya 30 günden uzun quarantine ayrıca bloklanır.
Retry varsayılan sıfırdır; en fazla iki, yalnız bütün üyelerin worker başlamadan
temiz provision hatası verdiği suite için yapılabilir. Tamamlanmış bir üyeyi yeniden
çalıştırmak, timeout/behavior/model hatasını tekrar etmek yasaktır. Önceki hata
silinmez, retry örnek sayısını artırmaz ve gate hatayı korur.

HTTP veya endpoint içeren repeat ayrı `--allow-external`, canlı model ayrı
`--allow-live-models` ister. Flag operatörün etki/bütçe sorumluluğudur; sistem dış
servisin kendi etkilerini geri alma garantisi vermez. Phase 6 doğrulaması bu
flag'lerle ücretli servis çağırmaz; yerel HTTP fixture gerçek LLM değildir.

## AG-062: profiller ve ürün raporları

`regression/profiles.py`: PR/scheduled/release seçimi parent frozen snapshot'a bağlıdır.
Critical üyeler çıkarılamaz; release tüm frozen üyeleri zorunlu tutar. PR kapsamı
daraltılırsa hariç tutulan ID'ler raporda görünür; seçili olmayan coverage için PASS
iddia edilmez. JSON/escaped HTML/JUnit aynı GateDecision'dan üretilir. Ham task,
output, trace, state veya provider yanıtı ürün özetine alınmaz. Regresyon nedenleri
typed finding kodlarıyla görünür; JUnit global gate'i ayrıca failure/error olarak taşır.
Mevcut raporlar üzerine yazılmaz.

`scripts/check_phase6.py`, genel dosya agent'ının gerçek source'unu golden frozen
baseline ile karşılaştırır; aynı bağımsız oracle altında değiştirilmiş source
varyantını BLOCK etmek zorundadır. Mevcut aday BLOCK ise script de başarısız olur.
PR 1 critical case × 1 örnek; scheduled 2 case × 3; release 2 case × 5 örnek kullanır.
Bu sonlu entegrasyon fixture'ı genel agent güvenliği kanıtı sayılmaz.
CI push/PR için smoke, workflow_dispatch ile seçilen profil, schedule için scheduled,
published release için release kullanır. GitHub schedule yalnız varsayılan daldaki
workflow için otomatik işler; development dalındaki doğrulama bunu main'e deploy
edilmiş gibi göstermez. Main'e merge yapılmaz.

## AG-063: güvenli repro

`regression/repro.py`: schema-versioned data-only JSON, source/evidence/behavior
hash'leri ve bounded içerik. Varsayılan yalnız metadata'dır. Runnable export için
case verisi ve her alan açıkça onaylanır. Bilinen secret desenleri, hassas alan
adları ve explicit JSON pointer'lar filtrelenir; yanlış redaction path reddedilir.
Redaction olursa bundle runnable değildir; değiştirilmiş oracle otomatik onaylanmaz.
Bu regex filtresi genel PII/DLP garantisi değildir; serbest metin operatör incelemesi
ister. Yerel baseline/repeat evidence private veri içerir ve public özet değildir.

Desteklenen repro profili mevcut Python worker + local environment/pinned replay
tool'larıdır. Model, HTTP, dış endpoint, host filesystem/environment/network izni
reddedilir. Agent source'u bundle'a gömülmez; operatör eşleşen yerel source sağlar.
Source hash'i execution öncesi doğrulanır, kontrol edilen byte'lar izole staging'e
alınır ve mevcut Docker runtime kullanılır. Davranış imzası assertion/output digest,
mutation, audit status/effect, policy ve cleanup'ı karşılaştırır. Nondeterministik
veya farklı sonuç `reproduced=false` olur. Repro hiçbir zaman release approval
vermez. HTTP/container binary ve canlı modelin genel offline yeniden üretimi bu
profilde desteklenmez; otomatik live fallback yoktur.

CLI: `agentguard release export-repro PLAN REPORT --output BUNDLE` metadata üretir;
runnable profil ayrıca `--export-policy POLICY` ister. `agentguard release reproduce
BUNDLE --source-dir SOURCE --output RESULT` yalnız uygun bundle'ı çalıştırır.

## AG-064: ortak gate sözleşmesi

`regression/gate.py` public `GateAPI.evaluate` mevcut effective_pass ve
RegressionComparator'ı yeniden kullanır; saklanmış findings/verdict'e güvenmez.
RunReport v1/v2 loader korunur; Phase 6 schema'ları yalnız sürüm 1 kabul eder.
Gelecek sürüm, bozuk fingerprint, summary/repro'nun authority diye verilmesi,
eksik/stale suite ve retired baseline BLOCK olur. Release gate güncel scenario
repository ile aktif approved baseline'ı yeniden kontrol eder. Controlled candidate
source pin'leri bütün attempt'lerde aynı olmalıdır; observed HTTP agent için açık
declared version digest gerekir. Bu uzaktaki binary'yi atteste etmez.

SDK, `release gate`, JSON/HTML/JUnit aynı GateDecision kullanır. Exit 0 ALLOW,
exit 1 doğrulanmış evidence içinde bloklayan sonuç (davranış/altyapı/coverage),
exit 2 geçersiz/uyumsuz evidence veya operasyon hatasıdır. Outcome ayrıca
PASS/FAIL/ERROR/INCONCLUSIVE ayrımını taşır; hiçbir ERROR başarı sayılmaz.
Standalone run/compare teşhisi aktif baseline release onayı vermez;
`authorizes_release=true` yalnız güncel aktif baseline ile geçen ve parent
snapshot'tan hiçbir case çıkarmayan repeat gate'inde verilir. Daraltılmış PR
scope'u ALLOW olsa bile tam release onayı vermez. Legacy CLI/rapor sözleşmeleri
korunur; public SDK yeni API'yi export eder.

Gerçek Docker profil koşusunda Windows geçici dizin cleanup hatası, SQLite
context manager'ın connection'ı kapatmamasını ortaya çıkardı. Önce tracking-connection
regression testi eklendi; ScenarioRepository ve yeni BaselineRepository artık
connection'ı finally ile kapatır. Cleanup hatası bastırılmadı.

## Güncel toplu doğrulama

AG-060–064 kodu uygulandı. Son tam suite **661/661 başarılı**: 379 unit,
235 integration, 47 gerçek Docker; failure/error/skip yok, 293,00 saniye.
Phase 0–5'in 631 case'i korunur; 30 yeni regression case'i vardır.
JUnit: `artifacts/phase6-final-tests.xml`. Yeni testler önce eksik modüller,
stale lifecycle, kopya attempt, eksik profile/repro/gate, cleanup sınıflandırması,
connection leak ve completed suite member retry sorunlarını RED üretti.
Scheduled gerçek Docker kontrolü 2 case × 3 örnek; release 2 × 5 örnekte golden,
mevcut ve hatalı source'u çalıştırdı. Mevcut ALLOW, hatalı source assertion/state
mutation gerekçeleriyle BLOCK oldu. `artifacts/phase6-profile-scheduled` ve
`artifacts/phase6-profile-release` metadata ve ürün JSON/HTML/JUnit raporlarını tutar.
Son koddan izole sdist ve sdist'ten wheel build başarılı. Checkout dışındaki
runtime-only temiz kurulum `artifacts/phase6-final-wheel.json` içinde passed:
CLI/verdict parity, Phase 3/4/5 özellikleri, gerçek Docker worker ve
`phase6_baseline=true`, `phase6_regression=true`. Kurulu wheel'den active baseline
ve current/known-bad gate raporları da doğrulandı. `real_llm_verified=false`
offline kontrolün sınırıdır; Phase 5'in kullanıcı canlı kanıtı korunur.

Uygulama commit'i `ab3721aa2be7f28756cca847cba6a99e3ba77033`, dal `codex/development`.
[Kaynak CI 43072800532](https://github.com/EgeBasturmen/agentguard/actions/runs/43072800532)
13/13 başarılı oldu. Sonraki redaction düzeltmesini bu eski CI koşusu kapsamaz.
Son kodun sonuçları doğrulanmadan Phase 6 tamamlandı olarak işaretlenmez.
Yeni canlı LLM/API çağrısı, IAM değişikliği veya kullanıcı dosyası taşıma yapılmadı.

### Son export güvenlik düzeltmesi

İlk CI beklenirken yapılan ikinci export incelemesinde JSON pointer doğrulamasının
`/rows/-1`, `/rows/00` ve geçersiz `~2` escape'ini kabul edip maskeleme walk'ıyla
aynı alanı eşlemeyebildiği görüldü. Üç regression önce RED çalıştı. Canonical liste
indeksi ve standart `~0`/`~1` escape zorunlu kılındı; geçerli pointer'ların gerçek
maskelemesi de doğrulandı. Negatif/alias indeksle sessiz redaction bypass yapılamaz.

Son kodun tam suite'i **664/664 başarılı**: 382 unit, 235 integration, 47 gerçek
Docker, failure/error/skip yok; 318,12 saniye. Phase 0–5'in 631 testi korunur,
Phase 6 toplam 33 yeni case içerir. JUnit: `artifacts/phase6-redaction-final-tests.xml`.
Yeni izole sdist/wheel build başarılı; son temiz kurulum ve yeni CI kontrolü bekleniyor.

Redaction kodunun temiz wheel kontrolü `artifacts/phase6-redaction-wheel.json`
içinde geçti; aynı Phase 3–6 ve gerçek Docker feature flag'leri doğrulandı.
Son preflight incelemesinde bilinmeyen critical/quarantine case ID'sinin ancak
attempt'ten sonra reddedildiği görüldü. Önce execution counter ile RED üretildi;
policy scope artık herhangi bir agent execution'ından önce doğrulanır. Hedefli
21 test geçti; 665 case'lik son tam koşu ve yeni source CI doğrulaması sürüyor.

Preflight düzeltmesinden sonra 665/665 test ve temiz wheel geçti. Son scope
kontrolü, daraltılmış PR ALLOW'unun tam release approval sayılmaması gerektiğini
gerçek HTTP entegrasyon ve Docker PR kontrolünde önce RED üretti. Gate artık
excluded case varsa `authorizes_release=false` verir; seçilen scope'un ALLOW/BLOCK
kararı değişmez. Tam release scope'u bütün üyeleri zorunlu tutmaya devam eder.
Bu son ayrım için 666 case'lik tam suite, temiz wheel ve CI kontrolü bekleniyor.
