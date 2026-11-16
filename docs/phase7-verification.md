# Phase 7: yönetim, private runner ve pilot hazırlığı

Güncel durum (16 Kasım 2026): AG-070–074 teknik uygulaması/doğrulaması sürüyor.
**AG-075 BLOCKED / PENDING EXTERNAL VALIDATION. Phase 7 bütünü tamamlanmadı.**
Kullanıcı, doğrulanmış 3–5 bağımsız dış ekip veya gerçek pilot kabul kanıtı
bulunmadığını açıkça bildirdi. Teknik fixture kimlikleri, fixture geri bildirimleri,
Chromium/Docker smoke ve CI sonuçları dış müşteri/pilot başarısı sayılmayacak.

Başlangıç: aynı `artifacts/phase2/repo` checkout'u, `codex/development`, Phase 6 HEAD
`354ec131f7d6320cf63bbe520cd7bc2ce362cc8e`. Phase 0–6'nın 667 testi referanstır.
Main, özgün index/değişiklikler ve kullanıcı canlı sonuçları korunur. Yeni repo yoktur.

## Roadmap karşılaştırması ve uygulama sırası

| Görev | Mevcut temel / eksik | Eklenen teknik kapsam / kabul kanıtı |
| --- | --- | --- |
| AG-070 | CLI/SDK yerel güvenilen operatör; web auth, tenant ve scopes yok | Opaque high-entropy API key, hash-only DB, workspace/project membership, rol+scope kesişimi, expiry/rotate/revoke, operator-assisted recovery. Cross-tenant, role escalation, iptal/expiry ve issuer offboarding testleri. |
| AG-071 | ScenarioRepository, SuiteRunner, GateAPI ve escaped raporlar var; yönetim UI/API yok | Aynı motorlara bağlı runtime kaydı, generation/review/approve/freeze/version binding, queued preflight/run, case detail, redacted artifact ve canonical upload ile gerçek comparator UI/API. Gerçek Chromium → HTTP → Docker akışı. |
| AG-072 | RunController container lifecycle; kalıcı yönetim kuyruğu/runner enrollment yok | SQLite transactional idempotent admission, tek-dispatch lease/heartbeat, cancellation, restart/fencing, one-use enrollment/revoke. Lost worker otomatik redispatch edilmez; unknown cleanup BLOCK. |
| AG-073 | Yerel export/redaction profili; tenant query/artifact authority yok | Her sorguda tenant/role/scope, bounded JSON, origin/host/version kontrolleri, authenticated kısa ömürlü artifact grant, redaction, integrity seal ve retention/delete. Eksik/expired kanıt güncel ALLOW taşımaz. |
| AG-074 | Gateway per-run bütçe var; proje planı/kalıcı quota/support yok | Server-side run/user concurrency, logical storage/compute reservations, operator-gated model token/tahmini maliyet rezervi, idempotent ledger, manuel fatura/plan/iptal ve destek/bug-retest akışı. Gerçek tahsilat veya invoice reconciliation iddiası yok. |
| AG-075 | Bağımsız dış ekip/pilot kanıtı yok | Onboarding/pilot planı, teknik eventler, feedback ve bug revalidation mekanizması hazırlanır. Gerçek 3–5 ekip, assisted/self-service ayrımı, bug düzeltme ve tekrar kullanım kanıtı olmadan kapatılamaz. |

Bağımlılık: auth/proje authority → review ve immutable suite → durable job/private
runner → typed evidence/redacted artifact → usage/quota/plan/support → dış pilot.
Yeni test/evaluation/comparison motoru yazılmadı. `management` servis katmanı;
generation repository, IntegrationPlan/SuiteRunner, mevcut Docker/ToolBroker,
GatewayEvidence ve Phase 6 GateAPI/comparator'ı kullanır.

## “Agent’ını Bağla → Test Et → Rapor Al” gerçek kapsamı

**Bağla:** Operatör control plane'i ve workspace/proje anahtarını hazırlar. Kullanıcı
Python source / uyumlu HTTP endpoint / pinned container runtime'ını, tool/environment
mapping ve source/image/declared version digest'ini girer. Private runner kendi
makinesinde/CI'da enrollment yapar ve proje source workspace'ine bağlanır.
Yapılandırma kaydı `verified=false`, `automatic_discovery=false` olur. Adapter'ın
ilan ettiği capability'ler bağlantının gerçekten doğrulandığı iddiası değildir.

**Test et:** Kullanıcı task, fixture ve başarı/yasak kurallarını tanımlar. Mevcut
generator taslak üretir. Oracle ID/beklenen değer, fixture ve sınıf insan tarafından
ayrı incelenir; unknown/rejected beklenti approve edilemez. Approved üyeler frozen
suite'e alınır. Runner preflight configuration/source-pin/capability readiness'dir;
agent invoke veya davranış PASS'i değildir. Asıl test mevcut controller/broker ve
Docker worker'da yürür. UI için YAML şart değildir; runtime/fixture JSON ve kurallar
halen manuel girdidir. Her case'i elle yazmak gerekmez; oracle doğruluğunu insanın
tanımlama/inceleme sorumluluğu ortadan kalkmaz.

**Rapor al:** UI execution, assertion/evaluation, audit/adım, policy, cleanup ve
kapsamı gösterir; redacted JSON indirilebilir. Artifact grant 60 saniyeliktir ve
hala geçerli API key/membership gerektirir. Retention sonrası geçmiş karar ayrıca
görülebilir; güncel gate BLOCK olur. Sürüm comparison'ı için private runner cache'indeki
iki canonical JSON kullanıcı tarafından açıkça yüklenir. Sunum artifact'i yeniden
değerlendirme authority'si değildir. Core comparator kullanılır; başarısız eski run,
geçerli passing release baseline yerine konmaz. Bug revalidation ayrı olarak aynı
oracle + değişen source + eski BLOCK/yeni ALLOW kanıtı ister.

Otomatik agent/framework keşfi, yalnız URL ile iç tool/model telemetry, zero-config
onboarding veya bütün framework'lere native adapter yoktur. OpenAPI/MCP tool
discovery mevcut explicit endpoint/protokol ve effect onayıyla runner'da çalışır;
bu agent'ın iş semantiğini veya oracle'ını otomatik çıkarmak değildir.
LangChain/LangGraph/CrewAI/AutoGen gibi framework'ler native yönetim adapter'ı olarak
iddia edilmez: uyumlu Python `run(task, sandbox)` wrapper, AgentGuard HTTP protokolü
veya container worker protokolü gerekir. Direct SDK network/tool kullanımı controlled
worker'da broker'a yönlendirilmelidir; sıradan bir URL veya Docker image yeterli değildir.
Browser/multi-agent/delayed-effect yüzeyleri Phase 8, kapsamlı self-service/operasyon
Phase 9, SSO/managed-hostile execution/ileri framework governance Phase 10'dur.

## Güven sınırı ve teknik profil

- Tek controller process, SQLite metadata/lease DB, proje başına mevcut scenario DB.
  Worker/customer code controller'da import/build/invoke edilmez. Agent source ve
  provider anahtarı customer runner'da kalır. Managed/shared-hostile execution yoktur.
- Kimlik yöntemi opaque API key'dir; public signup, email recovery, OIDC/MFA/SSO
  sunulmaz. Private pilot operatörü hesap/anahtar provisioning ve owner recovery
  yapar. Caller reviewer/actor/plan/usage değerini kendi adına seçemez.
- API key DB'de yalnız hash; plaintext credential dosyası yalnız açık CLI hedefinde
  exclusive ve POSIX owner-only oluşturulur; Windows'ta private directory ACL'i
  operatörün deployment önkoşuludur. Key terminale/loga/Git'e yazdırılmaz.
  Model/provider key dosyası okunmaz; .env veya başka proje credential taraması yoktur.
- Runner token proje/sadece runner RPC kapsamındadır. Enrollment one-use ve 10 dk;
  issuer key/üyelik yetkisi exchange'de tekrar kontrol edilir. Runner çağrıları da
  güncel issuer yetkisini kontrol eder. Revoke/üyelik kaldırma/downgrade aktif işi
  cleanup unverified ile bloklar. Normal key rotation, mevcut runner bağını yeni
  key'e taşır; eski pending enrollment geçerli olmaz. Expiry/owner recovery sonrası
  gerekli runner re-enrollment operatör tarafından yapılır.
- Customer runner bildirimi bağımsız platform attestation'ı değildir. Sunucu typed
  evidence, mevcut oracle/suite/source pin, cancellation ve GateAPI sonucunu denetler;
  `authorizes_release=false` ve customer-reported assurance korunur. Kötü niyetli
  customer host bütün gözlemi uydurabilir; managed attestation iddiası yoktur.
- Claim/complete atomik/fenced; duplicate idempotency payload değişirse conflict.
  Lease kaybı otomatik retry veya exactly-once external effect garantisi değildir.
  Unknown execution/cleanup BLOCK ve compute/model rezervi tutar. Offline/öldürülmüş
  runner'ın container cleanup'ı uzaktan doğrulanamaz; yerel inceleme gerekir.
- Sonuç canonical olarak geçici bellekte değerlendirilir. Sunucuda raw canonical
  rapor yerine redacted presentation, original digest ve server HMAC seal tutulur.
  Bu seal customer execution imzası değildir. Canonical cache customer runner'da
  açıkça seçilmiş private dizindedir; karşılaştırma için kullanıcı bunu yükler.
  Cache kendi TTL'inde, runner çalışırken temizlenir. Offline kopya/backup/download
  silinmesi uzaktan garanti edilmez. Şifreli object store/backup restore Phase 9/10
  kapsamında ayrıca doğrulanmalıdır.
- Redaction bilinen secret desenleri, hassas alanlar ve proje alan listesiyle çalışır;
  işaretli alanın metne yankılanan değerleri de maskelenir. Genel PII/DLP garantisi
  yoktur. Fixture/oracle sessiz redakte edilmez; credential içeren definition reddedilir.
  Retention primary presentation artifact'lerine uygulanır; metadata/audit/definition
  ve operator backup retention'ı ayrı sorumluluktur. Account/project hard-delete
  yolculuğu bu fazda tam GA olarak iddia edilmez.
- HTTP pilot transport loopback default; remote bind TLS ister. TLS reverse proxy,
  private storage ACL, 2 MiB body / 16 thread / timeout / rate limit ve tek-process
  deployment sınırları vardır. Public internet/load/HA/soak güvence kapısı Phase 9'dur.
  CSP eval/inline script'i açmaz; userdata yalnız text olarak render edilir. Key
  browser storage/cookie'ye yazılmaz; credential/project değişiminde gecikmiş yanıtlar
  generation fence ile atılır, eski DOM/form/download URL'leri temizlenir.

## Kullanım ve maliyet modeli

Run admission idempotent ledger'a bir kez yazılır; cancelled/failed admission run
kotası tüketir. Project/user concurrency ve toplam reserved/spent lease wall seconds
sunucuda uygulanır. Wall time CPU veya cloud invoice ölçümü değildir. Storage limiti
definition/spec/artifact logical byte kapsamındadır; fiziksel SQLite sayfa/backup/disk
quota garantisi değildir. Plan period'u UTC epoch tabanlı sabit 30 günlük bucket'tır;
takvim ayı veya gerçek fatura döngüsü değildir.

Model proje bütçesi default sıfırdır. Sadece local operator CLI bütçe/pricing floor
tanımlar; tenant kendi fiyatını sıfırlayıp sınırı geçemez. Runner'da ayrıca açık live
ve external grant gerekir. Her case'in GatewayConfig token/tahmini maliyet cap'i
önceden ayrılır. Eksik/HTTP-error usage rezervi tutulur, sıfır ücret uydurulmaz.
Bilinen provider-reported usage ve operator rates tahmini ayrı ledger'dır; provider
faturası veya matematiksel invoice üst sınırı değildir. Key sadece runner env'dedir;
Gemini yalnız GEMINI_API_KEY kullanır. Bu geliştirmede gerçek ücretli çağrı yoktur;
yerel JSON HTTP model fixture'ı gerçek LLM doğrulaması sayılmaz.

Commercial profil manuel pilot fatura/ödeme kaydıdır. Talep planı değiştirmez;
operator external reference'ı hash olarak kaydedip planı aktive eder. API kullanıcısı
ödendi diyerek yetki alamaz. İptal talebi ve external billing teyidi ayrıdır; otomatik
charge/webhook/refund/reconciliation yoktur. Aktif run varken ani downgrade reddedilir.
Gerçek ticari ödeme kanıtı teknik fixture'la oluşturulmaz.

## Test ve doğrulama kaydı (devam ediyor)

Yeni görevler önce missing-module/metod ve security RED testleriyle açıldı.
Gerçek Chromium ilk oracle liste-indeks eşleme hatasını buldu; sunucu yanlış ID'yi
onaylamadı. UI mapping düzeltildi. Test harness'inin eval tabanlı beklemesi CSP'ye
takıldı; CSP gevşetilmeden locator assertion kullanıldı. Geciken önceki tenant yanıtı
gerçek HTTP response ertelenerek RED üretildi; request-generation fence ile düzeltildi.
Pending enrollment/removed admin runner, non-completed stored ALLOW, retention sonrası
ALLOW, pricing/scope/concurrency ve future-DB migration sorunları da önce RED üretildi.

Yerel targeted auth/workflow/result/queue/billing testleri ve gerçek Docker iyi/kötü
agent, core comparator ve bug retest akışı geçti. Gerçek Chromium akışı geçti:
`artifacts/phase7-ui-epoch-fixed.json`. Bunlar synthetic technical fixture'lardır;
`external_pilot_verified=false`. Tam suite, son temiz wheel ve CI sonuçları
henüz bu kayıtta tamamlandı diye sunulmaz. AG-075 için [pilot planı](phase7-pilot-plan.md)
izlenir; dış ekiplere mesaj gönderilmedi ve dış pilot verisi üretilmedi.

### Son yerel kabul kontrolleri

Tam suite **716/716 başarılı**: 382 unit, 282 integration, 52 gerçek Docker;
failure/error/skip yok, 304,09 saniye. Phase 0–6'nın 667 case'i korunur, 49 yeni
Phase 7 case'i vardır. JUnit: `artifacts/phase7-final-tests.xml`.
HTTP drip-body regression önce eksik absolute deadline nedeniyle RED oldu;
per-read timeout yerine tüm HTTP/1.0 bağlantısına bounded deadline eklendi.
İlgili 3 HTTP testi geçti. Credential/project değişimi sonrası gecikmiş gerçek
HTTP cevabının eski tenant verisini göstermediği Chromium'da doğrulandı.

`artifacts/phase7-final-ui.json`: gerçek Chromium/HTTP/Docker ile connect →
generation → unknown oracle reddi → review/approve → freeze → preflight/run →
evidence/report akışı başarılı. XSS, browser storage/cookie'de key bulunmaması,
logout ve geç gelen önceki tenant yanıtı kontrolleri geçti. Ücretli model çağrısı 0;
`external_pilot_verified=false`. JavaScript `node --check` başarılı.
İzole sdist ve sdist'ten wheel build başarılı. Son runtime-only wheel ve GitHub CI
kabulü ayrıca doğrulanacaktır; bu yerel kayıt CI başarısı iddia etmez.

`artifacts/phase7-final-wheel.json`: checkout dışında runtime-only temiz kurulum,
Phase 0–6 CLI/generation/gateway/session/baseline/regression ve Phase 7 HTTP auth,
insan onaylı suite, preflight ve gerçek Docker private runner başarılı. Paketlenmiş
HTML/JS/CSS var; runtime'a Playwright/dev dependency sızmamış. `phase7_control`,
`phase7_authorization`, `phase7_runner` true; `external_pilot_verified=false`.
Wheel fixture'ı gerçek LLM veya dış ekip değildir. Sonraki kabul adımı GitHub CI'dır.

Kurulum ve manuel adımlar: [onboarding rehberi](phase7-onboarding.md).
