# Phase 5: model gateway, evaluation ve oturumlar

**Güncel durum (11 Kasım 2026):** Kullanıcının beş çağrılık gerçek Gemini
doğrulaması başarılı; son teşhis düzeltmelerinin test/wheel/CI kapanışı doğrulanıyor.
Aşağıdaki eski başarısız koşular ve bekleyen canlı test notları tarihsel kayıtlardır;
güncel canlı kanıt belgenin sonundadır. Phase 6 başlatılmadı.

Başlangıç: doğrulanmış Phase 4 `f1b8599a56065aa09d1307731365e17146994ad1`,
aynı `artifacts/phase2/repo` checkout'u ve `codex/development` dalı.
Main, özgün index/değişiklikler ve kullanıcı raporları korunur. Yeni repo kopyası yoktur.

## Ön inceleme ve uygulama

Phase 0–4'ün controller, transactional broker, Docker isolation, integration observer,
generation/review/frozen suite, assertion ve ortak release gate'i yeniden kullanılır.
Eksik gateway, evaluator registry, worker session ve recovery kanıtları bu motorlara eklenir.

| Görev | Uygulanan profil | Doğrulama sınırı |
| --- | --- | --- |
| AG-050 | Provider bağımsız ModelProvider/GatewayConfig; Gemini REST text generateContent, açık JSON HTTP bridge ve offline mock; controller-owned ledger/reservation, çağrı/token/maliyet/rate/deadline limitleri | Kullanıcının beş çağrılık Gemini koşusu başarılı; ayrı yerel kanıt. CI'daki HTTP/Docker fixture gerçek LLM sayılmaz. |
| AG-051 | Sürümlü declarative evaluator registry; output, tool, state, temporal; aynı ComparisonManifest/assertion/effective_pass | Kullanıcı kodu host evaluator olarak import edilmez; bilinmeyen kind reddedilir. |
| AG-052 | Retrieval/citation/extractive grounding; file SHA256 ve ayrı container'da Python CLI argv/stdout testleri | Semantik entailment, serbest shell, pytest/unittest report ingestion veya genel coding doğruluğu iddiası yoktur. |
| AG-053 | Python worker içinde aynı instance/event loop ile bounded multi-turn; sıralı turn IPC ve attempt başına yeni container/process | HTTP/container session protokolü desteklenmez; eksik/replayed/out-of-order turn BLOCK olur. |
| AG-054 | Opsiyonel judge; sürümlü rubric, bağımsız positive/negative etiketler, calibration disagreement, model-reported confidence, ayrı judge usage | Gerçek Gemini'de iki bağımsız etiket ve bir değerlendirme geçti. Küçük kalibrasyon seti genel judge doğruluğu değildir; model cevabı label yapılmaz. |
| AG-055 | Bildirilmiş tool/call-index failure ve timeout sinyalleri; bağımsız recovery evaluator, attempt/denial/effect ayrımı | Enjeksiyon pre-dispatch ve no-effect'tir; gerçek provider gecikmesi/rollback iddiası değildir. Gerçek deadline/cancellation/cleanup testleri korunur. |

## Model ve güven sınırı

Worker `sandbox.call('model.generate', {'messages': [...], 'max_output_tokens': 256})`
kullanır. Provider/model/system prompt, policy ve anahtar controller'dadır. Worker
provider/model/purpose/header seçemez; model route'u connector ile değiştirilemez.
Agent çağrıları purpose=agent, judge purpose=judge, açık SDK generation çağrıları
purpose=generation olarak ayrılır. Worker'a anahtar veya provider credential gönderilmez.

Gemini yalnızca `GEMINI_API_KEY` okur; .env yüklenmez, başka proje/anahtar aranmaz.
Sabit HTTPS origin'e `x-goog-api-key` header'ı kullanılır; key URL, body, ledger,
rapor veya log'a yazılmaz. Provider hata gövdesi/exception metni aktarılmaz.
Yanıttaki metin ve modelVersion'da anahtar yankısı da temizlenir.
Redirect/private-network sınırları mevcut HTTP transport ile korunur. JSON HTTP
bridge ayrı açık endpoint/private-host grant ister ve credential forwarding yapmaz.

Gemini profili text-only, tek candidate, non-streaming; araç/search, medya, cache,
automatic retry ve provider fallback yoktur. `modelVersion`, prompt/config/pricing
sürümü ve provider-reported token sayıları kaydedilir. Thinking token'ları toplam
usage'a dahildir; eksik/çelişen usage, truncation, safety block veya rate/HTTP hatası
unverified olur. Başarısız çağrı rezervi serbest bırakılmaz; maliyet sıfır uydurulmaz.
Anahtarın bulunması bağlantı/model erişimi veya quota doğrulaması değildir.

Kullanımdan önce UTF-8 byte + framing margin ve max output için konservatif rezerv
alınır. Bu tokenizer ölçümü değildir. Tek in-flight çağrı ve açık rate limiti vardır.
Maliyet, versioned operator rates × provider-reported usage tahminidir; verified
invoice değildir. Belirsiz in-flight maliyet nedeniyle matematiksel fatura üst sınırı
vaat edilmez. Budget run/gateway kapsamındadır; SaaS project-wide durable quota değildir.
Remote HTTP agent'ın gateway dışı model kullanımı gözlenmiş gibi raporlanmaz.

## Evaluator API

`Scenario.evaluations`: `{version:'1.0.0', id, kind, config}`. Desteklenen kind'lar:

- `output_json`, `output_contains`, `output_equals`: bağımsız output capture ve strict JSON/schema.
- `tool_arguments`, `tool_sequence`: controller audit üzerinden argüman ve kesin sıra.
- `state_equals`: bağımsız final snapshot; eski expected/unchanged/invariant kontrolleri korunur.
- `temporal_never`, `temporal_before`: audit mutation journal ve completed çağrı sırası;
  sonradan restore edilen geçici değişim yakalanır. Gözlemsiz boş audit INCONCLUSIVE'dır.
- `rag_sources`: required document'lar, gerçek `document.get` ve fixture içindeki exact quote.
- `rag_grounded`: answer'ın alıntıların tam birleştirilmiş hali olması; yalnızca extractive profil.
- `coding_files`: gözlenen UTF-8 dosyaların SHA256'ları.
- `coding_tests`: gözlenen dosyalar yeni non-root/read-only/network-none container'a kopyalanır;
  Python entrypoint'in argv/stdout sonucu host'taki bağımsız beklenen değerle karşılaştırılır.
  Oracle/expected stdout test container'ına gönderilmez. Agent'ın PASS/test report beyanı kullanılmaz.
  Çevre whitespace normalize edilir; sonuç sonlu test vakaları içindir.
  Aygıt/alias adları ve case çakışmaları dosya açılmadan reddedilir. Image build
  öncesi materialize edilmiş gerçek dosya adları/byte'ları observed snapshot ile
  karşılaştırılır; farklı veri aynı workspace hash'iyle test edilmiş sayılmaz.
- `turn_output`: belirtilen turn çıktısı; declared turn tamamlanmadan gate geçmez.
- `fault_recovery`: declared no-effect fault ve aynı tool'un sonradan gerçek completed çağrısı.
- `judge`: her çalışmada ayrık positive/negative etiketlerle kalibrasyon; bilinmeyen/tekrarlı/
  çakışan label, disagreement ve düşük model-reported confidence INCONCLUSIVE olur.

Finding `pass/fail/error/inconclusive` taşır. Error, behavior failure'dan ayrılır.
Tüm kontroller AND ile birleşir; judge state/policy/cleanup FAIL'ini örtemez.
Eksik finding, usage, receipt, session üyesi veya değişen fingerprint gate'i engeller.
Finding'ler hem spec hem before/after/output/audit/turn observation hash'ine bağlıdır.
Output veya audit sonradan değiştirilip eski PASS finding'i bırakılamaz; snapshot
ile mutation listesi de ortak gate'te karşılaştırılır. Hash'ler imza değildir ve
güvenilen operatörün tüm veriyi yeniden üretmesine karşı authentication sağlamaz.
Mock model/tool yeni business evaluator'larını da doğrulayamaz. CLI/JSON/HTML aynı
core sonucu kullanır; yeni finding/model/session alanları escaped HTML'da gösterilir.

Eski senaryolarda boş yeni alanlar serialize edilmez; Phase 0–4 comparison ve approval
hash'lerinin davranışı korunur. Yeni evaluator/config/turn/fault tanımları manifest'e
bağlanır. Model/prompt sürümü ayrı usage manifest'inde görünür; aynı oracle/environment
korunurken farklı model/prompt'lar karşılaştırılabilir. Mock/live değişimi uyumsuzdur.

## Örnek ve canlı doğrulama

`examples/phase5_agent.py`: provider bağımsız history tutan stdlib agent.
`agentguard integration examples/phase5_plan.json --preflight-only` API çağrısı yapmaz.
Plan source_dir'i plan dosyasına göre çözülür. Gerçek yürütme ücretli çağrı yapabilir;
CI bu planı otomatik çalıştırmaz. Mevcut onay yalnızca aşağıdaki beş çağrılık smoke içindir.

```powershell
.\artifacts\phase3-env\Scripts\python.exe scripts/check_gemini.py
.\artifacts\phase3-env\Scripts\python.exe scripts/check_gemini.py --live --result artifacts/phase5-gemini-live.json
```

Default sıfır çağrıdır. Kullanıcı beş çağrılık canlı planı onayladı ve anahtarın hazır
olduğu kendi terminalinde çalıştırmayı seçti. Bu ajan sürecinde key yoktur.
En fazla beş çağrı, 256 output token/call, 12.500 toplam token rezervi ve toplam
0,025 USD yapılandırılmış konservatif tahmin; retry yoktur. İki Docker agent prompt
sürümü, model/prompt comparison, iki bağımsız aritmetik label ve bir judge yanıtı
ölçülür. Judge olumlu olsa bile gerçek broker policy reddi BLOCK kalmalıdır.
Sonuç dosyası exclusive yazılır; var olan kullanıcı dosyası üzerine yazılmaz.

Canlı deneme kayıtları (mevcut kullanıcı dosyaları değiştirilmedi):

- `artifacts/phase5-gemini-live.json`: incomplete/ValueError; kullanıcı aynı
  terminalde `api_key_present=false` bildirdi. Credential readiness için sıfır
  çağrılı, explicit `CredentialUnavailable` kontrolü ve regression testi eklendi.
- `artifacts/phase5-gemini-live-2.json`: first_agent aşamasında ERROR;
  gateway ledger bir başarısız attempt, usage_source=unknown ve tutulan rezerv
  kaydetti. Cleanup verified; kalan model/judge çağrılarına geçilmedi. Eski genel
  `MODEL_CALL_UNVERIFIED` kodundan HTTP/timeout/finish nedeni çıkarılamaz.
- Güvenli `HTTP_###`, `MODEL_TIMEOUT`, `MODEL_TLS_OR_NETWORK_ERROR`,
  `PROVIDER_MAX_TOKENS` vb. kodları ayrı regression'larla eklendi. Anahtar veya
  provider hata gövdesi açığa çıkarılmaz. Otomatik retry/fallback yapılmaz.
- `--live --single-agent --result artifacts/phase5-gemini-diagnostic.json`
  yalnızca bir agent/model çağrısı yapar (en fazla 256 output token, 2.500 token
  rezervi, 0,005 USD yapılandırılmış tahmin); judge/comparison çalıştırmaz ve
  `phase5_complete=false` kaydeder. Tek çağrılık tanı sonucu henüz yoktur.

Dosya adlarında aygıt/alias ve taşınabilirlik sınırlarının gerekçesi:
[Windows adlandırma kuralları](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file).
Linux'ta oluşturulabilen bazı taşınamayan isimler de artık reddedilir.

Resmî referanslar (11 Kasım 2026): [generateContent/usage](https://ai.google.dev/api/generate-content),
[Gemini model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite),
[fiyatlandırma](https://ai.google.dev/gemini-api/docs/pricing).

## İlk CI doğrulama kaydı (tarihsel)

**AG-051/052/053/055 belgelenen profillerle doğrulandı. AG-050/054 kısmi;
başarılı gerçek Gemini kanıtı bekliyor. Phase 5 kapatılmadı.**

- Son uygulama kaynağı: `8c8011ff8a1a02fba1d0c836498fad48922a6883`.
- Yerel tüm suite: **593 passed**, 343 unit + 205 integration + 45 gerçek Docker;
  failure/error/skip yok. Phase 0–4'ün 544 case'i korunur, 49 yenidir.
- İzole sdist ve sdist'ten wheel build başarılı. Temiz runtime-only kurulumda
  checkout dışındaki CLI, Phase 2/3/4 akışları, model HTTP gateway, judge protokolü,
  yeni evaluator ve çok turlu gerçek Docker worker akışı başarılı.
- [Kaynak CI 42956806818](https://github.com/EgeBasturmen/agentguard/actions/runs/42956806818)
  **13/13 başarılı**: Ubuntu 24.04 / Windows 2022 × Python 3.10–3.14 her job'da
  343 unit + 205 integration ve temiz wheel; ayrı Docker job'ında 45 case ve
  kurulu wheel'den gateway/session/önceki execution akışları. Lint ve zorunlu gate başarılı.
- 11 artifact ZIP indirildi; **21 JUnit ve 11 wheel-check JSON** sayı/hata/skip
  ve yeni feature flag'leriyle doğrulandı. `phase5_gateway`, `phase5_evaluations`
  true; Docker kaydında `phase5_worker` true. Tümünde `real_llm_verified=false`;
  ücretli Gemini CI'da otomatik çağrılmaz ve fixture gerçek LLM sayılmaz.
- Main/Phase 2 remote ref'leri, özgün main HEAD/branch/index hash/status,
  kullanıcı dosyaları ve mevcut rapor hash'leri başlangıç snapshot'ıyla eşleşir.
  Key, .env, DB, user live result veya test artefaktı Git'e eklenmedi.

Phase 6 başlatılmadı. Başarılı Gemini/prompt comparison/judge kanıtı ve Phase 5
kapanışı olmadan sonraki faza geçilmez; ayrıca kullanıcı onayı gerekir.

Yeni testler önce eksik özellikleri/hataları yeniden üretti: output-only senaryo,
eksik temporal gözlem, invented citation, uncalibrated judge, budget rezervasyonu,
multi-turn IPC, gateway/worker loading, recovery, key echo, usage silinmesi,
audit prompt/usage ayrışması ve yeni business evaluator/mock bypass.
Sync `asyncio.run` uyumluluğu, observation/output çelişkisi, credential readiness,
provider safe error code ve portable artifact materialization testleri de önce
başarısız çalıştırılmıştır.

Başlıca dosyalar: `model_gateway/gateway.py`; `evaluation/specs.py`, `registry.py`,
`judge.py`, `coding.py`; mevcut scenario/result/manifest/outcome, runner/controller,
ToolBroker/records/faults, worker/lifecycle/ProcessIPC; integration plan/config/sdk;
generation model/validation, comparator/models, SDK/report export; dört test modülü
ve HTTP helper; `scripts/check_gemini.py`, `phase5_wheel_probe.py`, `check_wheel.py`,
MANIFEST/CI, örnekler, README/ROADMAP ve bu rapor.
Portable workspace doğrulaması için mevcut `environments/adapters.py` küçük bir
güvenlik düzeltmesi aldı. Toplam 46 dosya değişti; yeni repo kopyası oluşturulmadı.

Kalan sınırlar: shared-hostile multi-tenancy yok; custom provider adapter güvenilen
host kodudur; semantic RAG, genel coding framework runner, HTTP/container session,
arbitrary tool post-effect fault/rollback, gizli call dışı usage ve büyük istatistiksel
judge calibration kapsam dışıdır. Key yönetimi managed secret store değildir.

## Canlı hata teşhisi düzeltmesi (11 Kasım 2026)

Eski `incomplete/ValueError` dosyası aşama veya gateway kanıtı içermediği için
hatanın geçmişte hangi satırda oluştuğu kesin olarak belirlenemez. Kullanıcının
bildirdiği `api_key_present=false` credential eksikliğini gösterir; eski genel
ValueError kaydı tek başına bir HTTP veya model erişimi hatası kanıtlamaz.
İkinci mevcut dosya yalnızca ilk agent aşamasında unknown usage kanıtlar.
Mevcut kullanıcı sonuç dosyaları değiştirilmedi.

`scripts/check_gemini.py` artık configuration, worker_setup, first_agent,
second_agent, prompt_comparison, judge_setup, judge ve budget_verification
kontrol noktalarını tutar. Beklenmeyen exception durumunda son kontrol noktası
ve gateway kullanım özeti kaybolmaz. Model yapılandırması Docker kurulmadan
doğrulanır. Sonuç dosyası ve terminal çıktısı güvenli kategori/kod/HTTP status
bildirir; exception metni, Pydantic input değerleri, prompt, model cevabı ve
provider response gövdesi dışarı verilmez. Sonuç JSON'u artık tam RunResult
yerine yalnızca izin verilen operasyonel özetleri içerir.

HTTP 401/403/404 model erişimi, 429 provider rate limit, diğer HTTP hataları,
transport/timeout, çıktı sınırı, eksik/tutarsız/rezervi aşan usage, gateway
budget/rate reddi, judge calibration disagreement ve judge response sorunları
ayrılır. HTTP kodu tek başına erişim reddinin provider tarafındaki kesin nedenini
kanıtlamaz. Hatalı attempt'te `actual_api_calls=null` kullanılır: kayıt sayısı
sunucunun isteği aldığı veya ücretlendirdiği anlamına gelmez. Bilinmeyen kullanım
rezervi korunur; sıfır maliyet iddia edilmez. Otomatik retry yoktur.

Önce 10 yeni regression testi başarısız çalıştırıldı. Düzeltme sonrasında
hedefli 19 test ve tüm suite **603/603 başarılı** (353 unit, 205 integration,
45 gerçek Docker; failure/error/skip yok). Docker Gemini harness'i offline
provider fixture'ıyla test edildi; gerçek Gemini API çağrısı yapılmadı.
JUnit: `artifacts/phase5-gemini-diagnostics-elevated-tests.xml`.
Bu ek düzeltmeler henüz commit/push veya yeni GitHub CI koşusuna dahil edilmedi;
yukarıdaki CI bağlantıları önceki doğrulanmış commitlere aittir.

Kullanıcı yeniden canlı çalıştırmayı onayladıktan sonra, aynı anahtarlı terminalde
ve bu checkout kökünde tek çağrılık teşhis komutu:

```powershell
.\artifacts\phase3-env\Scripts\python.exe scripts/check_gemini.py --live --single-agent --result artifacts/phase5-gemini-diagnostic-safe.json
```

Var olan sonuç dosyası üzerine yazılmaz. En fazla bir çağrı, 256 çıktı token'ı,
2.500 token rezervi ve 0,005 USD yapılandırılmış tahmin sınırı korunur.
Tek agent sonucu judge/comparison doğrulaması değildir. AG-050/054 ve Phase 5,
başarılı tam canlı doğrulama bulunana kadar açık kalır; Phase 6 başlatılmadı.

## HTTP 400: generateContent istek sözleşmesi incelemesi (11 Kasım 2026)

Kullanıcının `artifacts/phase5-gemini-diagnostic-safe.json` kaydı
first_agent / HTTP_400 / provider_attempt_records=1 / retries=0 ve cleanup=verified
gösteriyor. Dosyanın yalnızca güvenli teşhis alanları incelendi; dosya değiştirilmedi.
Judge veya prompt comparison aşamasına geçilmemiş. Provider hata gövdesi okunmadı
ve loglanmadı; bu durum HTTP 400'ün provider tarafındaki kesin gerekçesini kanıtlamaz.

Alan karşılaştırması için resmi
[generateContent REST referansı](https://aistudio.google.com/api-reference/generate-content/generate-content),
[model sayfası](https://aistudio.google.com/docs/models/gemini-3.5-flash-lite) ve
[modele özgü geçiş kılavuzu](https://aistudio.google.com/docs/whats-new-gemini-3.6#api-changes-and-parameter-updates)
kullanıldı. Genel REST şeması model bazındaki daha dar destek sınırlarını tek başına
göstermiyor; migration checklist `candidate_count` alanının Gemini 3+ için kaldırılmasını istiyor.

| Alan | Mevcut isteğin denetimi / değişiklik |
| --- | --- |
| Endpoint/API version | `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent`; resmi REST yolu ile uyumlu, değiştirilmedi. |
| Model | `gemini-3.5-flash-lite` resmi model kodu; isim veya model fallback'i değiştirilmedi. |
| `contents` | `[{role: user, parts: [{text: ...}]}]`; geçmişteki assistant rolleri REST `model` rolüne çevrilir. Teşhis isteğinin son rolü user; yasak prefilled model turn içermiyor. |
| `systemInstruction` | Yalnızca text parts içerir; opsiyonel role alanının yokluğu geçerli, değiştirilmedi. |
| `generationConfig` | Artık yalnızca istenen çıktı sınırı ve gerekiyorsa JSON MIME alanını gönderir. |
| JSON formatı | `responseMimeType: application/json` doğru REST alanıdır; schema opsiyoneldir, iş oracle'ı yerelde bağımsız doğrulanır. |
| `maxOutputTokens` | Integer 256 tek çağrılık sınır korunur. Varsayılan sınırsız çıktıya geçilmez. |
| `candidateCount: 1` | Modele özgü migration talimatına uymadığı için kaldırıldı. API varsayılanı tek yanıt; beklenmeyen çoklu yanıt hâlâ reddedilir. |
| `temperature: 0` | Bu aile için deprecated alan kaldırıldı. Belge mevcut modellerde yok sayıldığını belirttiği için tek başına kesin HTTP 400 nedeni sayılmıyor. |
| Diğer alanlar | topP/topK, tools, toolConfig, cache, safety override veya düşünme ayarı eklenmedi. Header anahtarı, TLS, origin ve broker sınırları korunur. |

En güçlü istek uyumsuzluğu adayı `candidateCount` alanıdır; yalnızca HTTP koduna
bakarak kesin kök neden veya yeni isteğin canlı kabul edildiği iddia edilmez.
Model varsayılan örneklemesi kullanılır; temperature=0/deterministik LLM garantisi
verilmez. Usage, hidden thinking token muhasebesi, reservation/budget, tek candidate
doğrulaması, STOP gereksinimi ve fail-closed evaluator/gate davranışı korunur.
Retry, hata sonrası parametre değiştirme veya ikinci otomatik istek yoktur.

Dört yeni REST contract case'i önce başarısız, düzeltmeden sonra başarılı çalıştı.
JSON/text ve system instruction var/yok durumları, çok turlu rol eşlemesi, sabit
origin/path/header, maxOutputTokens, çoklu candidate reddi ve thinking token maliyeti
offline doğrulandı. HTTP_400 ayrımına da regression eklendi; hedefli toplam 26 test geçti.
Offline fixture gerçek Gemini kabul kanıtı değildir. Bu düzeltme için canlı çağrı yapılmadı.

Tüm suite: **610/610 başarılı** (360 unit, 205 integration, 45 gerçek Docker;
failure/error/skip yok), 220,27 saniye. JUnit:
`artifacts/phase5-gemini-request-contract-tests.xml`. Yeni commit/push/CI koşusu
başlatılmadı; önceki CI kayıtları bu yerel ek düzeltmeyi içermez.

Kullanıcı yeni canlı koşuyu onayladıktan sonra, mevcut checkout kökünde tek çağrılık komut:

```powershell
.\artifacts\phase3-env\Scripts\python.exe scripts/check_gemini.py --live --single-agent --result artifacts/phase5-gemini-request-fixed.json
```

En fazla 1 çağrı, 256 çıktı token'ı, 2.500 token rezervi, 0,005 USD yapılandırılmış
tahmin ve retry=0 korunur. Eski sonuçların üzerine yazılmaz. AG-050/054 ve Phase 5 açık kalır.

## Devam eden HTTP 400 ve kaybolan provider nedeninin düzeltilmesi

Kullanıcının `phase5-gemini-request-fixed.json` sonucu parametreler kaldırıldıktan
sonra da first_agent/HTTP_400 kaydetti. Bu yeni kanıt nedeniyle önceki candidateCount
uyumsuzluğu gerçek canlı hatanın doğrulanmış kök nedeni olarak kabul edilmez.
Başarılı canlı doğrulama yoktur. Mevcut dosyada status dışında provider nedenini
geri çıkaracak bilgi bulunmuyor; bilinmeyen neden tahmin edilerek model/API değiştirilmiyor.

Uçtan uca akış yeniden incelendi: check_gemini → RunController → Docker worker
→ IPC model.generate → host ToolBroker → ModelGateway → GeminiProvider → request_json
→ ledger → güvenli rapor. Mevcut model adı, v1beta generateContent yolu, header
authentication, UTF-8 JSON serialization, contents/systemInstruction ve iki alanlı
generationConfig için ek bir istek hatası kanıtı bulunmadı. Docker cleanup ve policy
başarılı; hata host HTTP katmanındaki cevap işleme aşamasında.

**Kesin kod kusuru:** request_json her HTTP hatasında gövdeyi atıp sadece HTTP
status metni üretiyordu. Böylece aynı 400 altındaki farklı nedenler siliniyordu.
Resmi [generateContent hata sözleşmesi](https://aistudio.google.com/docs/api-errors?generate-content=true)
INVALID_ARGUMENT ile FAILED_PRECONDITION'ı ayırır ve API_KEY_INVALID örneğini
Google RPC ErrorInfo içinde gösterir. [İstek referansı](https://aistudio.google.com/api-reference/generate-content/generate-content)
ile [model sayfası](https://aistudio.google.com/docs/models/gemini-3.5-flash-lite)
yeniden kontrol edildi.

HTTP helper'a opsiyonel trusted error_factory eklendi; diğer connector'ların
varsayılan davranışı korunur. Gemini adapter bounded error bytes'ı geçici olarak
ayrıştırıp yalnızca sabit allowlist'teki RPC status/reason ve bilinen normalize
field path'lerini saklar. Error message, localized message, metadata, proje/key
bilgisi ve ham gövde saklanmaz/loglanmaz. Hatalı JSON veya bilinmeyen alanlarda
yalnız HTTP status kalır; sebep icat edilmez. Gemini yalnız HTTP 200'ü başarı kabul eder.

Yeni güvenli diagnostic alanları: provider_status, provider_reasons, invalid_fields.
API_KEY_INVALID/EXPIRED credential; API key scope/restriction model_access;
FAILED_PRECONDITION provider_precondition; bilinen field violations request_parameters
olarak ayrılır. Bunlar provider'ın bildirdiği sebeplerdir; örneğin FAILED_PRECONDITION
tek başına faturalandırmanın kapalı olduğunu kanıtlamaz.

Typed provider_error ledger'a bağlanır; status/code/usage çelişkisi reddedilir.
Boş alan serialize edilmediğinden eski ledger hash'leri korunur. Bilinmeyen kullanım
rezervi tutulur, actual_api_calls=null kalır; otomatik retry veya provider/model
fallback yoktur. HTTP 400 başarılı değerlendirmeye dönüştürülmez.

Önce gerçek yerel HTTP'de kaybolan API_KEY_INVALID nedeni ve çelişkili ledger
regression'ları başarısız çalıştırıldı. Düzeltmeden sonra hedefli 43 test başarılı:
yerel HTTP hata sınıfları, hatalı/duplicate JSON, gizli metin sızdırma girişimleri,
eski ledger uyumluluğu ve contradiction rejection. Docker worker/broker üzerinden
aynı güvenli hata raporu için ek test yazıldı. Gerçek Gemini çağrısı yapılmadı.
Tüm suite **628/628 başarılı** (370 unit, 212 integration, 46 gerçek Docker;
failure/error/skip yok), 252,23 saniye. JUnit:
`artifacts/phase5-gemini-structured-errors-tests.xml`. Bu ek düzeltme için
commit/push veya yeni CI koşusu başlatılmadı; mevcut kullanıcı dosyaları ve index korundu.

Yeni canlı koşu kullanıcı tarafından onaylandığında aynı checkout'ta tek komut:

```powershell
.\artifacts\phase3-env\Scripts\python.exe scripts/check_gemini.py --live --single-agent --model gemini-3.5-flash-lite --result artifacts/phase5-gemini-cause.json
```

En fazla 1 çağrı, 256 output token, 2.500 token rezervi, 0,005 USD yapılandırılmış
tahmin ve retry=0. Provider yapılandırılmış ayrıntı vermezse kesin neden bilinmiyor
kalır. Tek çağrı başarılı olsa bile judge/comparison doğrulanmış olmaz; Phase 5 açık kalır.

## AQ authorization key / HTTP 401 incelemesi (11 Kasım 2026)

Kullanıcının `phase5-gemini-key-corrected.json` dosyasında yalnız güvenli alanlar
incelendi: first_agent / HTTP_401 / UNAUTHENTICATED. Saklanan provider reason
listesi boş; bu dosyadan kesin reddetme sebebi çıkarılamaz. Anahtar okunmadı,
yeniden canlı çağrı yapılmadı, mevcut sonuç dosyası değiştirilmedi.

Resmi [Gemini API key belgesi](https://aistudio.google.com/docs/api-key)
authorization key'lerin service account'a bağlı ve desteklenen tür olduğunu,
AI Studio'nun yeni anahtarları bu türde oluşturduğunu açıklar.
[Google destek yanıtı](https://discuss.ai.google.dev/t/aq-authorization-key-returns-401-access-token-type-unsupported-with-gemini-api/178763)
AQ biçiminin Gemini API, resmi SDK ve REST'te desteklendiğini doğrular.
[Cloud REST kimlik doğrulama belgesi](https://docs.cloud.google.com/docs/authentication/api-keys-use)
x-goog-api-key header'ını kullanır. AQ prefix'i tek başına bozuk anahtar veya
başka bir API endpoint'i gerektiği anlamına gelmez. Uzunluk/prefix aktif key ve
service-account binding doğrulaması değildir.

Kod GEMINI_API_KEY değerini değiştirmeden yalnız sabit Gemini HTTPS origin'ine
x-goog-api-key header'ında gönderiyor. Prefix veya uzunluk üzerinden AIza-only
doğrulama yok; Authorization/Bearer, query key, ADC veya GOOGLE_API_KEY fallback'i
eklenmedi. Header/API yöntemini değiştirmek için kanıt bulunmadı.

İki küçük teşhis düzeltmesi yapıldı: HTTP 401 artık credentials olarak sınıflanıyor;
Google destek kayıtlarında bulunan ACCESS_TOKEN_TYPE_UNSUPPORTED sabit kodu güvenli
reason allowlist'ine eklendi. Önceki yanıt bu nedeni içeriyorsa eski parser onu
elemiş olabilir; mevcut dosyadan bunun gerçekten olduğu iddia edilemez. Mesaj,
metadata, proje bilgisi veya ham gövde açığa çıkarılmıyor.

Önce bu iki eksik regression testi başarısız çalıştı; AQ fixture'ının header'a
değişmeden iletildiği testi zaten geçti. Düzeltmeden sonra ilgili 46 offline test
başarılı; gerçek yerel HTTP error akışları dahil. Yeni tam-suite, wheel veya CI
koşusu yapılmadı; önceki 628/628 kaydı önceki kod durumuna aittir.

Kullanıcı tarafında kontrol: AI Studio'da doğru proje ve auth key kaydı; Cloud'da
Generative Language API'nin etkin olması, key'in bu API'ye izin vermesi, bağlı
service account'ın mevcut/etkin ve binding'in geçerli olması. Origin/IP kısıtları
PowerShell'in gerçek çıkış ortamıyla uyumlu olmalı. Gerekirse aynı doğru projede
AI Studio üzerinden yeni auth key oluşturulabilir; eski key sonucu doğrulanmadan
silinmez. Free-tier proje doğrulaması veya seçilmiş paid-tier koşulları ayrıca
kontrol edilir; yalnız 401 faturalandırma veya kota problemi kanıtlamaz.
Kontroller doğruysa Google destek kanalına güvenli hata kodlarıyla başvurulabilir;
anahtar hiçbir destek mesajına eklenmez. Phase 5 açık kalır.

## Başarılı gerçek Gemini kanıtı ve güncel kapanış kontrolleri

Kullanıcı kendi anahtarlı PowerShell oturumunda bağımsız minimal generateContent
isteğini HTTP 200 ile tamamladı (6 input + 1 output token). Ardından tek çağrılık
AgentGuard/Docker kontrolü passed oldu (20 input + 11 output token). Sonrasında
onaylanan beş çağrılık tam koşu ilk denemede ikinci agent aşamasında 503/UNAVAILABLE
ile durdu; unknown usage rezervi tutuldu. Yeniden onaylanan ayrı tam koşu başarılı.
Önceki 400/401'in geçmişteki kesin nedeni bu başarıdan çıkarılamaz; IAM rolü atanmadı.

Başarılı tam kaydın dosyası `artifacts/phase5-gemini-full-verification-2.json`;
SHA256: `46650e2375cada19e0bcbbf18cf9a8bd134feee9e2f31b0b6ba7f923ce48110e`.
Dosya yerelde salt okunur kontrol edildi; Git'e eklenmez veya üzerine yazılmaz.
Bu hash dosya kimliğidir, sağlayıcı imzası veya faturası değildir. Ajan yeni canlı
çağrı yapmadı; çalıştıran ve canlı sonuç kaynağı kullanıcıdır.

| Aşama | Tamamlanmış çağrı | Input / output token | Yapılandırılmış tahmin (micro USD) |
| --- | --- | --- | --- |
| first_agent / smoke-v1 | 1 | 20 / 11 | 130 |
| second_agent / smoke-v2 | 1 | 21 / 11 | 131 |
| judge / iki kalibrasyon + bir değerlendirme | 3 | 290 / 113 | 1420 |
| Toplam (yalnız başarılı tam koşu) | 5 | 331 / 135 | 1681 |

Sonuç passed/budget_verification, diagnostic VERIFIED, retries=0.
Tüm tamamlanmış çağrılarda usage_source=provider-reported; toplam 466 token ve
0,001681 USD yapılandırılmış tahmin. Önceki başarısız denemelerin bilinmeyen
kullanımı bu toplamda yoktur; toplam sağlayıcı faturası veya sıfır hata maliyeti
iddia edilmez. İki agent'ın output_json oracle'ı, policy ve cleanup kontrolleri geçti.

Kodda prompt karşılaştırması başarısızsa judge'a geçilmeden dönülür; başarılı
budget_verification aşamasına ulaşılması, iki prompt sürümünün mevcut comparator
kontrolünü de geçtiğini kanıtlar. Güvenli JSON karşılaştırmanın ham içeriğini saklamaz.
Judge'ın iki bağımsız aritmetik etiketi ve son değerlendirmesi geçti; buna rağmen
judge run status=error, policy_status=violation kaldı. Bu beklenen negatif güvenlik
örneği, olumlu LLM kararının gerçek broker policy ihlalini PASS yapmadığını gösterir.
Üç cleanup verified. second_agent gateway'inin sondaki blocked durumu kasıtlı
altıncı denemeye aittir; budget_denied_before_network=true ve ağ çağrısı sayısı beştir.

Scriptte phase5_complete=false korunur: harness sonucu faz/CI durumunu otomatik
değiştirmez. Judge kalibrasyonu yalnız bu küçük bağımsız etiket seti için geçerlidir.

Güncel kodda son yerel tüm suite **631/631 başarılı** (373 unit, 212 integration,
46 gerçek Docker; failure/error/skip yok), 266,37 saniye.
JUnit: `artifacts/phase5-final-closure-tests.xml`. Önceki 544 Phase 0–4 case korunur;
Phase 5 toplam 87 yeni case içerir. İzole sdist ve sdist'ten wheel build başarılı.
`artifacts/phase5-closure-wheel.json`: checkout dışında runtime-only temiz kurulum,
CLI/verdict parity, önceki execution/generation profilleri, gateway/evaluation ve
gerçek Docker worker/session akışı passed. real_llm_verified=false bu wheel
probe'un offline protokol fixture'ı olduğunu belirtir; ayrı kullanıcı canlı
kanıtıyla çelişmez. Son commit CI kapanışı bekleniyor.
