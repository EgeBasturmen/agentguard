# Phase 8: kapalı browser, servis fixture’ları, sıralı ekip ve sanal zaman

Durum: teknik uygulama yerelde doğrulanıyor; CI kabulü henüz kaydedilmedi.
AG-075 **BLOCKED / PENDING EXTERNAL VALIDATION** olarak kalır; Phase 7 bütünü
kapatılmaz. Gerçek dış ekip/pilot sonucu üretilmedi. Phase 9 başlatılmadı.
Başlangıç aynı checkout/dal: `codex/development`, `95d0ebc55d5fc4bb8a9f0598a805c2a8a9202a6a`.
Phase 0–7’nin 716 testi korunur; main/özgün index/kullanıcı dosyaları değiştirilmez.

## Roadmap karşılaştırması ve bağımlılıklar

| Görev | Önceki temel / eksik | Uygulanan profil |
| --- | --- | --- |
| AG-080 | Phase 7 UI smoke gerçek Chromium kullanıyordu; agent browser environment/tool yoktu | Attempt-owned ayrı Chromium/context; kapalı declarative test app, sayfa geçişi/form/fill/submit/text; bağımsız DOM snapshot, broker audit ve gerçek cleanup |
| AG-081 | Memory/filesystem/document/HTTP vardı; database/inbox/calendar lifecycle yoktu | Ayrı namespace’te gerçek SQLite record fixture paketleri; parametrik get/put/delete/list, snapshot/reset/cleanup; calendar interval doğrulaması |
| AG-082 | Tek agent multi-turn vardı; controller-owned çoklu kimlik/handoff yoktu | 2–10 farklı isimli sıralı controlled Python üyesi, ayrı container, sabit routing; gözlenen shared state ve untrusted mesaj, tekrar doğrulanabilir bağlanmış run kanıtları |
| AG-083 | Gerçek deadline/fault/cancel vardı; virtual clock/job fixture yoktu | Bounded monotonik sanal saat; declared set-value job, deterministic due/id sırası, cancel/reset/history ve pending effect kontrolü |
| AG-084 | Genel Docker/broker/policy testleri vardı | Her yeni yüzeyde gerçek E2E ve scope-denial; browser auth/context ayrımı, SSRF/HTML/DOM identity sınırları, SQLite injection/size/interval, team forgery, clock window ve gerçek iptal/cleanup |

Bağımlılık: environment lifecycle/contract → mevcut plan ve broker → bağımsız
observer/evaluator → generation/review → team state transfer → security/E2E → wheel/CI.
Yeni evaluator/gate motoru yazılmadı. Mevcut IntegrationRunner’a genel completion
inspection ve trusted observation callback eklendi; agent’ın state/PASS beyanı kullanılmaz.

## Destek sınırları

Browser yalnız fixture page/field kimlikleri kabul eder; URL, arbitrary HTML/JS,
profil, credential, download, popup veya dış site otomasyonu kabul etmez. Sabit
test app HTML’ı escape edilir; agent yalnız broker tool çağırır. Host-side Chromium
güvenilen test fixture işlemidir, hostile siteler için sandbox/managed hosting
garantisi değildir. Service worker ve tüm dış istekler kapalıdır. Browser RPC yalnız
loopback, attempt secret’ı ve bounded JSON ile çalışır; agent container network-none’dır.
Anahtar veya browser nesnesi worker’a verilmez. Snapshot gerçek DOM’dandır; submit
DOM değişimidir, dış ödeme/mail teslimatı veya görünmeyen backend etkisi sayılmaz.
Proses/context normal cleanup ve iptal akışında kapatılır. Host crash/kill sonrası
orphan recovery/soak Phase 9 operasyon kapısıdır; cleanup belirsizse PASS yoktur.

Database SQL gerçek SQLite’dır fakat kullanıcıdan arbitrary SQL kabul edilmez;
parametrik record sözleşmesi vardır. Inbox/calendar kalıcı fixture ledger’larıdır,
SMTP/IMAP/Google Calendar/Exchange entegrasyonu veya gerçek teslimat değildir.
Tüm kayıtlar bounded JSON, kimlikler bounded ASCII; calendar start/end verildiğinde
geçerli integer interval gerekir. Production verisi/credential okunmaz.

Team yalnız aynı transferable fixture türünde sıralı Python üyeleri destekler.
Her üye yeni container/worker ve sabit policy ile çalışır. Kimlik ve alıcı controller
planından gelir; `sender=admin` yazan model/agent metni yetki veya routing sağlamaz.
Önceki üyenin IPC çıktısı açıkça untrusted data olarak sonraki task’a eklenir.
Shared state worker beyanından değil environment observer’dan alınır; before/after
mutasyonları core run kanıtıyla tekrar karşılaştırılır. Başarısız/iptal üye zinciri
durdurur. Hash tutarlılık kanıtıdır; imza, kötü niyetli host’a karşı attestation veya
release approval değildir. `authorizes_release=false`. Paralel ekip, dağıtık message
bus, autonomous routing, HTTP/container team sessions ve native framework adapter yoktur.
Team şu anda SDK/CLI’dadır; Phase 7 UI/kuyruğu tam TeamPlan yönetimi sunmaz.

Sanal saat wall clock yerine açık `clock.advance` ile ilerler. Job yalnız declared
fixture key/value etkisi üretir; arbitrary callback/shell/network çalıştırılmaz.
Pending job bırakılırsa mevcut state oracle’ları geçse bile run unverified/BLOCK olur.
İptal edilen job çalışmış sayılmaz. Dış sistem kuyruğu, görünmeyen job, webhook,
gerçek zaman gecikmesi ve gözlem penceresi dışındaki etkiler doğrulanmış sayılmaz.
Seed history fixture’dır; actual tool audit yerine execution kanıtı olarak kullanılmaz.

## Kullanım ve ürün deneyimi

Tek agent için mevcut integration planında environment `kind`:
`browser`, `database`, `inbox`, `calendar`, `clock`. Tool mapping için tek
`{"connector":"environment"}` sözleşmeleri otomatik getirir; `allowed_tools` ve
resource scopes ayrıca açık izin gerektirir. Aynı runtime Phase 7 connection ve
generation/review/freeze akışında kullanılabilir. Agent sınıfı/adapter, source hash,
fixture, görev ve bağımsız oracle kuralları hâlâ kullanıcı girdisidir. Her senaryoyu
manuel yazmak gerekmez; insan oracle doğrulaması kaldırılmaz.

```powershell
agentguard integration examples/phase8_clock.json --preflight-only
agentguard integration examples/phase8_clock.json --json-report artifacts/new-clock-result.json
```

Örnek agent deterministik scripted fixture’dır, gerçek framework keşfi değildir.
Gerçek agent aynı `run(task,sandbox)`/broker sözleşmesine uyarlanır.
Browser isteğe bağlı runtime extra’dır: `pip install "agentguard[browser]"`, sonra
`python -m playwright install chromium`. Linux sistem bağımlılıklarını operatör
sağlar. Browser kurulumu canlı LLM veya ücretli API çağrısı değildir.

Team: `agentguard team team.json --output artifacts/new-team-result.json`.
JSON `{steps:[{id,plan},...],timeout_seconds:120}`; ilk plan initial fixture taşır,
sonrakiler `initial_state:{}` ile observed state alır. Her plan kendi oracle ve
tool policy’sini taşır. Çıktı exclusive private canonical JSON; CLI yalnız özet
ALLOW/BLOCK ve `authorizes_release=false` gösterir. `verify_team` kimlik/sıra,
plan, mesaj, shared state ve core run evidence’ını tekrar denetler.

“Agent’ını Bağla → Test Et → Rapor Al” yeni fixture yüzeylerine genişledi;
otomatik framework entegrasyonu, zero-config onboarding veya tüm browser sitelerine
uyumluluk değildir. LangChain/LangGraph/CrewAI/AutoGen native keşfi hâlâ yoktur.

## Test kaydı

Önce missing environment/config/team/CLI ve browser DOM reserved identity testleri
RED çalıştırıldı. SQLite bağlantılarının Windows cleanup öncesi kapanmaması, browser
helper JSON Content-Type/header uyumu, Playwright fixture callback’inin erken çalışması
ve strict datetime JSON team parsing sorunları testlerle ortaya çıkıp düzeltildi.
Son targeted environment/team/security/CLI ve gerçek resource-scope/cancel testleri geçti.
Tüm suite, clean wheel ve GitHub CI sonucu aşağıya yalnız doğrulandıktan sonra eklenir.

Son kaynak üzerinde `artifacts/phase8-verified-wheel.json` başarılı: izole sdist ve
sdist’ten wheel, checkout dışında temiz runtime; temel kurulumda browser dependency
yok, ayrı browser extra profilinde gerçek Chromium contract/Docker yürütmesi var.
Database/inbox/calendar/clock ve sıralı iki container team akışı da kurulu paketten
geçti. `phase8_contracts/execution/browser/team=true`, `live_api_calls=0`,
`external_pilot_verified=false`. Önceki Phase 0–7 wheel probe’ları korunup geçti.
`artifacts/phase8-phase7-ui-regression.json` gerçek Chromium/HTTP/Docker UI akışı,
XSS ve cross-session görünüm kontrolleri başarılı. Örnek `phase8_clock.json` gerçek
Docker koşusunda beklenen delayed state’i ve boş pending job kümesini doğruladı.

İlk geniş koşuda 752 test başarılıydı; browser RPC auth testi HTTP 400’ün exception
türünü yanlış bekledi. Reddetme gevşetilmedi; test mevcut RuntimeError/HTTP status
sözleşmesine düzeltildi ve tek hedefli test geçti. Yeni fixture doğrulaması için
beş RED testten sonra provision yapmadan static preflight/generation kontrolü eklendi.
Son 753 testlik temiz koşu ve CI kabulü beklenir; önceki başarısız JUnit korunur.

Browser context/close sözleşmesi resmi [Playwright context](https://playwright.dev/python/docs/api/class-browsercontext)
ve [browser](https://playwright.dev/python/docs/api/class-browser) belgeleriyle kontrol edildi.
