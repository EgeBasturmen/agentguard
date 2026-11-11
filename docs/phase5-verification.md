# Phase 5: model gateway, evaluation ve oturumlar

Başlangıç: doğrulanmış Phase 4 `f1b8599a56065aa09d1307731365e17146994ad1`,
aynı `artifacts/phase2/repo` checkout'u ve `codex/development` dalı.
Main, özgün index/değişiklikler ve kullanıcı raporları korunur. Yeni repo kopyası yoktur.

## Ön inceleme ve uygulama

Phase 0–4'ün controller, transactional broker, Docker isolation, integration observer,
generation/review/frozen suite, assertion ve ortak release gate'i yeniden kullanılır.
Eksik gateway, evaluator registry, worker session ve recovery kanıtları bu motorlara eklenir.

| Görev | Uygulanan profil | Doğrulama sınırı |
| --- | --- | --- |
| AG-050 | Provider bağımsız ModelProvider/GatewayConfig; Gemini REST text generateContent, açık JSON HTTP bridge ve offline mock; controller-owned ledger/reservation, çağrı/token/maliyet/rate/deadline limitleri | Gerçek HTTP ve Docker testleri gerçek LLM doğrulaması değildir. Gemini canlı sonucu ayrı kanıt bekler. |
| AG-051 | Sürümlü declarative evaluator registry; output, tool, state, temporal; aynı ComparisonManifest/assertion/effective_pass | Kullanıcı kodu host evaluator olarak import edilmez; bilinmeyen kind reddedilir. |
| AG-052 | Retrieval/citation/extractive grounding; file SHA256 ve ayrı container'da Python CLI argv/stdout testleri | Semantik entailment, serbest shell, pytest/unittest report ingestion veya genel coding doğruluğu iddiası yoktur. |
| AG-053 | Python worker içinde aynı instance/event loop ile bounded multi-turn; sıralı turn IPC ve attempt başına yeni container/process | HTTP/container session protokolü desteklenmez; eksik/replayed/out-of-order turn BLOCK olur. |
| AG-054 | Opsiyonel judge; sürümlü rubric, bağımsız positive/negative etiketler, calibration disagreement, model-reported confidence, ayrı judge usage | Küçük kalibrasyon seti genel judge doğruluğu değildir. Model cevabı label yapılmaz. Gemini canlı kalibrasyon kanıtı bekler. |
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

## Doğrulama kaydı

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
