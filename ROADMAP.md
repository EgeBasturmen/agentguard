AgentGuard — Genel Amaçlı Platform Roadmap’i
Tarih: 28 Ekim 2026

Bu plan, kullanıcı tarafından paylaşılan 0.1.0 kaynak kodunun önceki teknik incelemesini temel alır. Ürün kapsamı sipariş, CRM veya başka bir sektörle sınırlandırılmaz. Önceki planın sektör odaklı başlangıç önerisinin yerine geçer; doğrulanmış teknik bulgular geçerliliğini korur. Aşağıdaki API, komut, modül ve ekran adları hedef tasarımdır; mevcut özellik oldukları iddia edilmez.
1. Ürün tanımı ve sınırları
AgentGuard; farklı diller, framework’ler ve çalışma biçimleriyle geliştirilmiş AI agent’larının görev başarısını, tool kullanımını, yan etkilerini, güvenliğini, maliyetini ve sürümler arası değişimini test eden genel amaçlı bir platformdur.

Genellik sektör bağımsız sözleşmelerden gelir: agent bağlantısı, tool bağlantısı, test ortamı, senaryo, gözlem, evaluator ve release policy. Her müşteri için ayrı test motoru veya bütün tool’ların yeniden yazılması gerekmez. Standart adapter’lar yeniden kullanılır; özel sistemler küçük entegrasyonlar gerektirebilir.

Ürün vaadi: “Agent’ını bağla; görevlerini, kurallarını ve test edilebilir ortamını tanımla; önerilen testleri gözden geçir; davranışını ve sürüm değişikliklerini kanıtlarla değerlendir.”

Ürün şu iddialarda bulunmaz: yalnızca URL’den bütün iç davranışı görmek; şemadan bütün iş semantiğini doğru çıkarmak; her olası davranışı sonlu testlerle kanıtlamak; test PASS sonucunu genel güvenlik sertifikası saymak. Desteklenen yetenekler ve test edilmeyen alanlar her raporda görünür olur.
2. Hedef kullanıcı ve kullanım alanları
Hedef: agent geliştiren ve sürümleyen geliştiriciler, platform ekipleri, QA/evaluation ekipleri ve güvenlik ekipleri. İlk teknik destek alanı sınırlanabilir; domain sınırlanmaz.
Agent tipi	Temel test yüzeyi	Ek ortam
Konuşma/asistan	Görev başarısı, format, çok turlu tutarlılık	Oturum/memory reset
Araştırma/RAG	Retrieval, kaynak doğruluğu, groundedness, veri yetkisi	Versioned doküman/dataset
Tool/API	Argüman, sıra, yetki, retry, yan etki	API fixture veya staging
Coding/terminal	Dosya diff, test sonucu, komut ve kaynak kullanımı	İzole workspace/runner
Browser	Navigasyon, form, işlem, dış yan etki	Ayrı browser context ve test sitesi
Dosya/veri agent’ı	İçerik, izin, veri bütünlüğü, sorgu etkisi	Geçici filesystem/database
Multi-agent	Görev devri, kimlik, mesaj ve ortak state	Koordinasyon/message environment
Uzun süreli agent	Zamanlanmış iş, devam etme, sonradan oluşan etki	Sanal saat ve job/event fixture


Yeni agent türü yeni core engine gerektirmez; yeni adapter/environment/evaluator yetenekleri gerektirebilir. “Başlangıçta HTTP ve Python desteği” teknik bir aşamadır, sektör kısıtı değildir.
3. Kullanıcı yolculuğu
1. Workspace/proje oluşturur; agent’ın amacını belirtir.
2. Agent’ı Python, HTTP, container veya desteklenen framework adapter’ıyla bağlar.
3. AgentGuard bağlantıyı test eder; erişebildiği tool, çıktı, oturum ve state yüzeylerini listeler.
4. Kullanıcı her etki alanı için ortam seçer: simülasyon, replay veya ayrılmış staging.
5. Görev örnekleri, başarı kuralları, yasak davranışlar ve bütçeler eklenir. Doküman veya kayıt import edilebilir.
6. Sistem ilgili hazır paketleri seçer ve agent’a özel senaryo taslakları üretir.
7. Kullanıcı kritik kuralları ve beklenen sonuçları gözden geçirir. Eksik bilgi belirsiz bırakılır, uydurulmaz.
8. Preflight; tool bağlantısı, fixture, reset, evaluator uygunluğu ve tahmini maliyeti denetler.
9. Testler yürür; ilerleme, bütçe, cancellation ve altyapı sorunları görünür.
10. Sonuçta davranış, güvenlik, execution, cleanup ve coverage ayrı gösterilir.
11. Bulgu tek case olarak tekrar çalıştırılabilir ve kalıcı regresyon testine dönüştürülebilir.
12. Onaylanan suite ve baseline CI’a bağlanır. Sonraki sürümlerde aynı kurallar ve sürümlenmiş veriler kullanılır.
YAML zorunlu kullanıcı arayüzü değildir. UI form/tablo, doğal dil taslağı ve SDK aynı versioned tanıma dönüşür. Kullanıcı üretilen tanımı inceleyebilir ve dışa aktarabilir.
4. Yedi ana sözleşme
Sözleşme	Sorumluluk	Sınır
AgentAdapter	Validate, start/invoke, stream, cancel, session reset	Host’ta güvenilmeyen import yok
ToolAdapter	Discover, invoke, normalize schema/result	Şema iş kuralının tamamı değildir
EnvironmentAdapter	Provision, seed, snapshot/inspect, reset, cleanup	Remote ortamda garanti edilemeyen etki açık
ModelAdapter	Kontrollü model çağrısı, usage, timeout, provider config	Tüm agent’larda model çağrısını intercept edemeyebilirsin
Evaluator	Evidence’dan typed finding/score üretme	Agent’ın PASS iddiasına güvenmez
ScenarioProvider	Paket, generation, import ve incident kaynakları	Taslak oracle onaylanmadan gerçek kabul edilmez
ResultStore	Run, event, artifact ve version kaydı	Secret/PII redaction ve erişim kontrolü


Sözleşmelerde capability negotiation bulunur. Örnek yetenekler: supports_tool_interception, supports_state_snapshot, supports_reset, supports_streaming, supports_cancellation, supports_model_usage. Adapter sürümleri ve desteklenen protokol sürümleri kayıtlı olur.

Her bağlantıya contract test suite sağlanır. Kullanıcı “AgentGuard-compatible” adapter yazdığında davranışı, hata sınıfları ve cleanup’ı bu suite ile doğrular.
5. Tool entegrasyonu: müşterinin araçlarını yeniden yazmamak
5.1 Keşif ve normalizasyon
Python signature/type hints, HTTP/OpenAPI tanımı, MCP tool şeması veya framework tool registry’den bilgi alınabilir. Güvenilmeyen Python modülü keşif için de controller’da import edilmez; worker veya bildirim dosyası kullanılır.

Ortak ToolDefinition: stable ID, name, description, input/output schema, adapter/version, effect type, resource scope, test mode, timeout, idempotency bilgisi ve credential reference. Side-effect ve permission önerileri otomatik çıkarılabilir; kritik olanlar kullanıcı tarafından doğrulanır. Belirsiz tool otomatik güvenli sayılmaz.
5.2 Çalışma modları
Mod	Kullanım	Güvence
Staging passthrough	Mevcut tool ayrılmış test servisine gider	Etki doğrulaması ve cleanup bağlanmışsa güçlü entegrasyon testi
Record/replay	Onaylı çağrı/cevap kayıtları tekrar kullanılır	Kaydedilmiş durumlar; görülmemiş koşullarda otomatik başarı yok
Schema mock	Şemaya uygun cevap/hata üretilir	Arayüz ve dayanıklılık testi; iş doğruluğu garantisi değil
Stateful simulator	Kontrollü dünya tool çağrılarıyla değişir	Simülatör semantiği ve kapsamı ölçüsünde
Custom adapter	Özel servis/protokol bağlanır	Contract test ve açık yetenek bildirimi


Replay key: tool/adapter version, canonical arguments, fixture state ve gerekli session context. Cache miss görünür olur; live çağrıya sessiz fallback yapılmaz. Kayıtları anonimleştirirken ilişki bütünlüğü korunur. Mock cevap üretimi agent’ın performansına göre beklenen sonucu değiştiremez.

Tool çağrıları sadece decorator ile izleniyorsa agent’ın doğrudan network veya dosya erişimini kaçırabilirsin. Enforced modda ağ/OS sınırları ve yönlendirme broker’ı destekler. Observed-only bağlantı ise bu sınırını raporda belirtir.
5.3 Simülatör doğrulaması
Hazır generic environments: filesystem, HTTP service, key-value/database, inbox/calendar, browser test app ve message bus. Sektörel paketler bunların üzerinde opsiyonel modüllerdir. API sözleşmesi, hata davranışı ve state etkileri için simülatör contract testleri bulunur. Kullanıcı staging’e karşı kontrollü uyumluluk testi yapabilir.
6. Senaryo üretimi ve oracle tasarımı
Senaryo kaynakları: hazır capability/risk paketleri, kullanıcının görev/kural tanımları, onaylı kullanım kayıtları, elle eklenen örnekler ve mevcut senaryolardan üretilen varyasyonlar.

Üretim hattı:
1. Agent hedefleri ve erişilebilir yetenekleri al.
2. İş kurallarını çıkar; kaynağa bağla; çelişki ve eksikleri göster.
3. Risk/requirement coverage planı oluştur.
4. Fixture, task, actor, permissions, tool behavior ve expected outcome içeren taslaklar üret.
5. Şema, referans, fixture ve evaluator uygunluğunu validate et.
6. Benzer kopyaları ayıkla; sınır, hata ve normal durum çeşitliliğini koru.
7. Oracle doğruluğunu gözden geçir; kararsız sonucu INCONCLUSIVE bırak.
8. Approved suite snapshot oluştur; generation model/config ve kaynak sürümünü kaydet.
ScenarioVersion: ID, version, provenance, task/messages, initial environment, actor/session, rules, expected output/effects, forbidden effects, evaluator config, time/budget, tags/severity ve required capabilities.

Hayat döngüsü: draft → validated → approved → active → retired. Keşif suite’leri otomatik genişleyebilir; release gate suite’i onaylı ve sabit snapshot kullanır. Kural değiştiğinde bağlı senaryolar stale olarak işaretlenir.

Agent’ın eski cevabı otomatik ground truth değildir. LLM tarafından üretilen expected answer, modelin kendi hatasını test oracle’ına taşıyabilir. Deterministic rules ve bağımsız fixture doğrulaması önce gelir. Generator ve grader aynı rubric/source üzerinde yanlılık taşıyabilir; insan etiketli örneklerle kalibre edilir.

Gizli acceptance case’leri ile agent geliştirirken kullanılan örnekleri ayır. Assertion ve cevap anahtarlarını worker’a gereksiz gönderme. Kullanıcı instruction’ı görünür olabilir; testin gizli oracle’ı görünür olmak zorunda değildir.
7. Environment ve execution
7.1 Environment yaşam döngüsü
Her attempt için provision → seed → run → inspect → evaluate → cleanup. Başlangıç state’i, saat, random fixture seed, session, memory, browser context, filesystem ve gerekiyorsa database namespace ayrılır. Paralel run’lar test verisini paylaşmaz.

Geçici yan etkiler journal ile izlenir; sadece final snapshot yeterli değildir. Sonradan çalışan job, e-posta, webhook ve scheduled task için bekleme penceresi ve event observation gerekir. Bunlar gözlenemiyorsa rapor sınırı söyler. External sistem rollback’i her durumda mümkün değildir; ayrılmış test tenant’ı veya sink kullanılır.
7.2 İki güvence seviyesi
- Observed mode: kullanıcı endpoint’i/SDK telemetry’si; veri kalitesi ve görünürlük kadar test.
- Controlled mode: izole agent, broker-managed tools ve otoritatif environment evidence.
Controlled mode’da controller canonical state, policy ve evaluator’a sahip olur. Worker agent/framework/ToolClient içerir. Worker’ın final state, trace veya PASS beyanı otorite değildir. Yetkili filesystem/browser observer’ları gözlemleri toplar. Tüm test türlerinde tek bir global state dict varsayılmaz.
7.3 Runtime güvenliği
Agent build aşaması çalışma aşamasından ayrılır. Source/dependency build işlemleri de güvenilmeyen kod çalıştırabilir; ayrı izole build ortamı kullanılır. Kilitli bağımlılıklar ve image digest kaydedilir. Runtime’da kontrolsüz pip install yapılmaz.

Non-root, read-only temel FS, capability drop, process/CPU/memory/storage limitleri, varsayılan kapalı ağ, secrets izolasyonu, bounded stdout/stderr/IPC, per-tool ve per-run deadline uygulanır. Browser/code agent için yazılabilir alan açıkça sınırlandırılır. Artifact toplanırken path traversal ve symlink kaçışı kontrol edilir.

İzinli network gerekiyorsa açık egress policy uygulanır. Server-side URL connector’larında SSRF, redirect ve private-network erişimi sınırları ayrıca ele alınır. Arbitrary host mount, Docker socket ve production credentials varsayılan olarak verilmez.

Cleanup verified/unverified ayrı alan; bilinmeyen cleanup PASS olmaz. Run ID label, heartbeat/lease, orphan reaper ve restart reconciliation bulunur. Docker’ın tek başına shared hostile multi-tenancy için yeterli olduğu varsayılmaz; managed execution öncesi threat-model-driven isolation seçimi yapılır.
8. Evaluation sistemi
Katman	Kontrol örnekleri
Spec validation	Bilinmeyen alan, eksik oracle, çelişen limit, eksik capability
Output	JSON/schema, gerekli alanlar, içerik ve format
Tool	İsim, argüman, sıra, limit, yetki, retry
State/effect	Dosya/DB değişimi, gönderim, kayıt ve resource identity
Temporal	Hiç yazılmamalı, onaydan önce işlem olmamalı, tekrar etmemeli
Task	Görev başarı kriterleri ve kısmi tamamlanma
Quality	Gerekli alanlarda rubric, groundedness, kaynak doğruluğu
Security	Yetkisiz girişim, exfiltration, prompt injection, kaçış girişimleri
Efficiency	Latency, model/tool kullanımı, token ve maliyet


LLM judge hard safety ihlalini geçersiz kılamaz. Judge model/rubric/version saklanır; human-labeled calibration set, disagreement ve düşük güven sonuçları görünür olur. Evaluator hatası agent başarısızlığından ayrılır; missing evidence başarılı kabul edilmez. Custom evaluator güvenilmeyen kullanıcı koduysa controller’da doğrudan çalıştırılmaz.

Run sonucu tek bool’dan zengin olmalı: execution_status, behavior_status, policy_status, cleanup_status, evaluation_completeness, gate_decision ve failure_reasons. Kullanıcı etiketi PASS/FAIL/ERROR/INCONCLUSIVE/SKIPPED olabilir; içeride boyutlar ayrı kalır. FAILED davranış, infrastructure ERROR ve unsupported SKIPPED aynı metrikte eritilmez.

No-regression ve release-accepted ayrı sonuçtur. Agent’ın yasak girişimi, broker’ın engellemesi ve gerçekleşen zarar ayrı bulgu alanlarıdır. Severity kural/context tarafından belirlenir; her yeni tool critical kabul edilmez.
9. Regression, tekrarlar ve CI
Baseline; agent artifact, suite/dataset/policy ve evaluator sürümlerine bağlı immutable kayıttır. Promote/approve yetkili işlem ve gerekçe gerektirir. Aynı scenario adı karşılaştırılabilirlik için yeterli değildir; fingerprint’ler kullanılır. Silinen assertion ve case coverage regression olarak görünür.

Deterministic replay koşuları çekirdeği doğrular; live model tekrarları stochastic davranışı ölçer. Aynı scenario bazında paired comparison, pass-rate ve belirsizlik raporu kullanılır. Seed tam determinizm garantisi değildir. Yetersiz örnekle güçlü istatistiksel iddia yapılmaz. Flaky test karantinaya alınırsa coverage kaybı görünür ve süresi/owner’ı olur.

PR smoke suite hızlıdır; daha geniş scheduled suite derindir; release suite onaylı kritik kuralları zorunlu tutar. Retry altyapı arızası için politika ile yapılır; FAIL geçene kadar çalıştırılıp önceki denemeler silinmez. State her attempt’te yenilenir.

Tek ComparisonResult ve gate evaluator CLI/UI/API/JUnit/HTML sonuçlarını üretir. Exit code sözleşmesi belgelenir; bozuk spec veya altyapı hatası başarı değildir. JSON report loader typed schema doğrular, migration uygular; saklanan verdict string’ine körü körüne güvenmez.
10. Güvenlik test kataloğu
İki ayrı suite ailesi tutulur:
1. Agent’ın güvenliği: prompt injection (user/tool/document/web), goal hijack, yetki/resource ihlali, veri sızdırma, tool misuse, sahte onay, repeated side effects, resource exhaustion, memory poisoning, inter-agent message manipulation.
2. AgentGuard’ın güvenliği: IPC forgery, schema bypass, replayed ID, oversized data, stdout flood, symlink/path traversal, network bypass, secret leak, cross-tenant access, queue/job duplication, cleanup uncertainty, report injection.
Her security case; saldırı yüzeyi, önkoşul, beklenen güvenli davranış, gözlenen girişim/etki ve sınırlamayı taşır. Bir saldırı ailesinden yüzlerce paraphrase üretmek kapsamı eşit ölçüde artırmış sayılmaz. Paketlerin sürümleri, provenansı ve iyi/kötü kontrol örnekleri vardır.
11. Veri modeli, depolama ve API
Temel entity’ler: Workspace, Membership, Project, AgentVersion, AdapterVersion, ToolDefinition, EnvironmentVersion, RuleVersion, ScenarioVersion, DatasetVersion, SuiteVersion, Run, CaseAttempt, Event, Artifact, Finding, Baseline, GatePolicy, UsageRecord.

İlişkisel veri deposu metadata ve ilişkiler; object storage büyük artifact/trace/fixture; durable queue işler için kullanılır. İlk uygulama modüler monolith olabilir. Worker ve güvenilmeyen build/evaluator execution ayrı güven sınırında kalır.

Run manifest: source/commit/artifact hash, image digest, runtime, model/provider/config, prompt hash, scenario/dataset/policy/evaluator versions, adapter versions, supported capabilities, timestamps, limits ve usage. Gizli değerler manifest’e yazılmaz; credential reference tutulur.

API’de idempotent run creation, pagination/filter, auth scopes, input size limit, rate limit ve request ID bulunur. Webhook gerekiyorsa imza, retry/deduplication ve delivery history eklenir. Live UI event’leri reconnect sonrası devam edebilir. API key yenileme ve revoke desteklenir.

Proje/tenant authorization sadece UI’da değil her API/query/artifact erişiminde uygulanır. Artifact URL’leri süreli ve kapsamlı olur. Import edilen rapor/HTML/metin UI’da güvenli render edilir; custom plugin veya report çalıştırılabilir kod gibi ele alınmaz.
12. Web, CLI ve SDK
Ekranlar: onboarding; proje/agent bağlantısı; capability summary; tool/environment mapping; kurallar; scenario/suite editor; generation review; run list/progress; case evidence; baseline comparison; coverage; usage/billing; team/settings.

Run detail her bulguda “hangi kural, hangi adım, hangi gözlem, beklenen/gerçekleşen fark ve tekrar çalıştırma” gösterir. Global safety score tek başına karar olmaz. Kullanıcının göremediği ve gözlenmeyen alanlar açık kalır.

Hedef CLI komut ailesi: init, connect/validate, doctor, scenarios generate/review, run, replay, compare, baseline promote, report export. İsimler uygulanırken sadeleştirilebilir. SDK ve UI aynı servis/core sözleşmesini kullanır; üç ayrı davranış motoru oluşmaz.

Onboarding önce bağlantı ve ilk küçük suite’i doğrular. Kullanıcı ilk aşamada tüm ayarları doldurmak zorunda kalmaz. Desteklenmeyen adapter, geçersiz key, erişilemeyen staging, eksik reset ve bütçe problemi için aksiyon alınabilir hata mesajı gerekir.
13. Model gateway ve maliyet
Kontrollü worker’da model erişimi gateway üzerinden olabilir: provider/model allowlist, input/output sınırları, concurrency, rate limit, timeout ve budget. Agent key’i görmez. Remote black-box agent’ta interception yoksa harici model kullanımı bilinmeyebilir; tahmin “ölçülen” diye sunulmaz.

Generation, tested-agent execution ve judge maliyetleri ayrı tutulur. Worker compute ve storage ayrıca ölçülür. Preview maliyet aralığıdır; gerçek usage run sonunda uzlaştırılır. İn-flight çağrı maliyetleri nedeniyle limitin tam matematiksel aşılmazlığı vaat edilmez; reservation ve safety margin politikası tanımlanır.

Plan quota, project budget, run cap ve user concurrency sınırları vardır. Harcama limiti UI’dan bağımsız backend’de uygulanır. Usage record idempotent ve audit edilebilir olmalı; failed/retried çağrıların ücretlendirme politikası açık olmalı.
14. Operasyon, veri güvenliği ve ticari ürün
Queue wait, run duration, execution error, evaluator error, worker crash, cleanup unknown, orphan resource, model failure, usage drift ve artifact büyüklüğü izlenir. Cancellation, retry, controller restart, queue duplicate delivery, worker loss ve storage failure için runbook ve testler bulunur.

Backup almak yeterli değildir: restore drill yapılır. DB migration geri dönüş/ileri düzeltme planı ve schema compatibility bulunur. Loglar structured ve redacted; retention proje politikasına bağlı. Veri silme metadata, artifact, cache ve kayıtlar için tanımlanır; backup saklama sınırı açık belgelenir.

Hosted ürün için minimum: auth, email/account recovery veya seçilen auth sağlayıcısının eşdeğeri, workspace membership, roles/scopes, API key rotation, audit log, quotas, plan/usage, payment/cancellation/refund policy, support ve incident iletişimi. Hukuki metinler gerçek veri akışına göre ayrıca değerlendirilir; bu roadmap hukuki uygunluk garantisi değildir.

Ticari hipotez: Developer local kullanım; Team suite/baseline/CI/collaboration; Enterprise private runner/SSO/audit. Fiyatlar pilotlarda değer ve maliyet ölçülmeden sabitlenmez. Core fark: genel test girdisi üretmekten ziyade task/tool/state evidence ile release güveni sağlamak.
15. Dağıtım stratejisi
Önerilen ilk ücretli dağıtım: hosted management/reporting + kullanıcının CI/private ortamında runner. Bu domain-independent kapsamı korurken ilk günden shared hostile-code hosting yükünü azaltır. Kendi runner’ını kontrol eden müşterinin raporu bağımsız attestation olarak sunulmaz.

İkinci seçenek tamamen local SDK/CLI; offline/replay çalışabilmeli. Sonraki seçenek managed worker execution. Managed çok kiracılı model açılmadan isolation, abuse prevention, build pipeline, secrets, egress, cross-tenant storage ve cancellation doğrulanır. Bu ayrı güvenlik kapısıdır; dashboard tamamlanınca kendiliğinden çözülmez.
16. Mevcut projeye uygulanacak değişiklik haritası
Önceki inceleme: mevcut suite’te 151 test geçti; 3 gerçek Docker gerektiren test ortamda Docker olmadığı için başarısız oldu. Wheel üretildi. CLI host execution, worker’ın yalnızca ready yanıtı, state/trace değiştirilebilirliği, boş spec PASS, parent forbidden-change boşluğu ve comparator ayrışması doğrulandı. Bu plan hazırlanırken yeni tam test koşusu yapılmadı.
Mevcut alan	Yapılacak
cli/main.py	Host agent import’u kaldır; tek RunController kullan
adapters/python.py	Worker-side loading; ayrı AgentSpec ve capability bildirimi
execution/isolated.py	Gerçek backend implementasyonu
execution/container_worker.py	Agent lifecycle ve tool client protocol
execution/docker_runtime.py	IPC, lifecycle result ve cancellation entegrasyonu
execution/tool_broker.py	Policy/registry ayrımı, audit, deadline ve resource scope
sandbox/generic.py	Genel environment sözleşmesine geçiş; canonical state worker’dan ayrı
runner/runner.py	Execution ile evaluation ayrımı; outer lifecycle yönetimi
core/scenario.py	Strict/versioned schema, stable assertion ID, requirements
evaluation/*	Sağlam path semantics, temporal controls, typed errors
regression/comparator.py + reporting/comparison.py	Tek comparison ve gate motoru
core/result.py	Çok boyutlu status, evidence completeness, cleanup
reporting/*	Aynı typed result; versioned loader ve redaction
pyproject.toml / README	Runtime YAML dependency, clean install ve kullanım sözleşmesi


Yeni modüller aşamalı eklenir: contracts, orchestration, environments, connectors, datasets, generation, model_gateway, policies, storage, api. Mevcut her dosyayı topluca yeniden yazma; tek vertical slice üzerinden geçir.
17. Fazlar, işler ve tamamlanma kapıları
Süreler tek geliştirici için yaklaşık odaklı mühendislik haftalarıdır; garanti veya piyasa benchmark’ı değildir. Entegrasyon, güvenlik ve pilot beklemeleri toplam takvimi uzatabilir. Önceki 12–18 haftalık tahmin dar çekirdek/pilot içindi; burada daha geniş genel ürün planlanmaktadır.
Faz 0 — Sözleşmeler ve kapsam [1–2 hafta]
- AG-001: Genel AgentSpec/ToolDefinition/EnvironmentSpec/ScenarioVersion taslağı. **Tamamlandı (10 Kasım 2026):** contracts/models.py içinde strict/sürümlü, JSON roundtrip ve referans doğrulamalı taslaklar; mevcut Scenario korunur.
- AG-002: Observed/controlled mode, result ve capability sözleşmesi. **Tamamlandı (10 Kasım 2026):** Explicit capability/context ve çok boyutlu result projection; mevcut gate korunur, local observed/policy unknown, unsupported controlled/capability iddiası kabul edilmez.
- AG-003: Security threat model’i gerçek local/hosted akışa uyarla. **Tamamlandı (10 Kasım 2026):** docs/threat-model.md gerçek host import, IPC/Docker handshake sınırları ve planlanan hosted/private/managed tehditlerini ayırır.
- AG-004: Üç farklı agent örneği seç: dosya/terminal, araştırma/RAG, API/tool. **Tamamlandı (10 Kasım 2026):** examples/phase0.py dosya düzenleme, deterministic retrieval/source ve API/resource simülasyonu; good/bad varyantları aynı core yolunda testli. Gerçek shell/network/model desteği iddia edilmez.
- AG-005: Core/adapter/evaluator compatibility ve supported matrix. **Tamamlandı (10 Kasım 2026):** contracts/compatibility.py sürüm/protokol pinli katalog ve destek kontrolü; docs/supported-matrix.md uygulanan/deklaratif kapsamı ayırır.
Kapı: Bu üç örnek aynı core contract ile ifade edilebiliyor; domain-specific runner gerekmiyor. **Doğrulandı:** ScenarioVersion → local bridge → mevcut ScenarioRunner/GenericSandbox/evaluator/gate; üç good/bad çift, temiz attempt state, capability reddi ve rapor roundtrip testleri. Phase 0 sözleşme/kapsam kapısı tamamdır; gerçek controlled runtime AG-020–025, connector/environment AG-030–035 ve geniş evaluator AG-050–055 kapsamındadır. Başlangıç incelemesi ve kabul sınırları docs/phase0-contracts.md içindedir.
Faz 1 — Sonuç doğruluğu ve paketleme [2–3 hafta]
- AG-010: Strict scenario/schema; boş kontrol ve çelişki reddi. **Tamamlandı (önceki teslim):** Mevcut strict spec ve false-PASS regresyonları Phase 1 doğrulamasında yeniden geçti; yeniden uygulanmadı.
- AG-011: Parent/root/list path kontrolü; deterministic diff sırası. **Tamamlandı (önceki teslim):** Mevcut path/diff regresyonları Phase 1 doğrulamasında yeniden geçti; yeniden uygulanmadı.
- AG-012: Evaluator exception ve unsupported async handling. **Tamamlandı (önceki teslim):** Mevcut hata/async regresyonları Phase 1 doğrulamasında yeniden geçti; RuntimeWarning artık test yapılandırmasında hata sayılır.
- AG-013: Tek report/comparison/gate; removed check ve fingerprint kontrolü. **Tamamlandı (10 Kasım 2026):** Ortak typed ComparisonResult ve gate, stable assertion kimlikleri, senaryo/evaluator/environment fingerprint'i ve doğrulanan versioned report loader; CLI/JSON/HTML karar eşitliği regresyon testleriyle doğrulandı.
- AG-014: Runtime dependencies, clean wheel install, README/quickstart. **Tamamlandı (10 Kasım 2026):** Runtime PyYAML korunur; README metadata/checkout dışı quickstart, isolated sdist→wheel build ve scripts/check_wheel.py eklendi. Windows 3.11 ve Linux 3.10–3.14 temiz runtime venv/console CLI/YAML/verdict testleri geçti.
- AG-015: Unit/integration/Docker marker; desteklenen Python CI matrix. **Tamamlandı (10 Kasım 2026):** Ayrık/eksiksiz test katmanları, strict marker ve .github/workflows/ci.yml (Linux/Windows × Python 3.10–3.14, ayrı gerçek Docker işi, artifact ve zorunlu Phase 1 gate). Kaynak commit a622f73be13bbe532aba758165c31bf52dec17c6 için [GitHub Actions run 42732949709](https://github.com/EgeBasturmen/agentguard/actions/runs/42732949709) 13/13 job başarılı: 10/10 matrix hücresinin her birinde 262 unit + 134 integration/build/temiz wheel, ayrı Docker job'ında 3 test. 11 artifact'in XML ve wheel-check kayıtları indirildi; failure/error/skip yok.
Kapı: Doğrulanmış false-PASS örneklerinin hepsi regression testinde kapanmış; CLI/HTML/JSON tutarlı. **Phase 1 tamamlandı:** AG-010–015 kanıtları ve gerçek uzak CI sonucu docs/phase1-verification.md içinde. Kapanış yalnızca tüm CI işleri başarılı olduktan sonra işlendi. main ve özgün index/çalışma ağacı korunur; yayın ayrı codex/phase1-ci dalındadır. Phase 2'ye kullanıcı onayı olmadan geçilmez.
Faz 2 — İzole yürütme ve otoritatif evidence [3–5 hafta]
- AG-020: RunController ve worker-side agent import. **Tamamlandı (yerel ve GitHub CI, 10 Kasım 2026):** `RunController` ve `AgentLaunchSpec`; varsayılan CLI host loader çağırmaz, import gerçek container içinde yapılır. Mevcut evaluator/runner korunur.
- AG-021: Docker worker + ProcessIPC + ToolBroker uçtan uca. **Tamamlandı:** Gerçek Docker agent → IPC → controller broker → canonical simulator state; dosya ve commerce domain aynı yolda doğrulandı. Canlı host agent nesneleri isolated backend tarafından reddedilir.
- AG-022: Policy enforcement, audit journal, argument/resource scopes. **Tamamlandı:** Tool/schema/deneme limiti, tam eşleşmeli resource scope, forbidden-effect rollback ve controller-owned audit. Yakalanan ihlal/hata PASS üretemez; simulator handler deadline/iptalde canonical state commit etmez.
- AG-023: Sync/async lifecycle, explicit completion ve bounded output. **Tamamlandı:** Native sync/async worker; run id/order, completion ve exit doğrulaması; mesaj/stdout/stderr/output sınırları ve strict JSON kontrolleri. Worker state/trace beyanı authoritative evidence sayılmaz.
- AG-024: Timeout, per-tool deadline, cancellation, cleanup sonucu. **Tamamlandı:** Build dahil monotonic deadline, killable transactional tool, cancellation ve rm+inspect cleanup. Eksik completion, crash, timeout ve cleanup unverified fail-closed; daemon-side build/late-create belirsizliği BLOCK.
- AG-025: Build isolation, image/version manifest ve gerçek Docker E2E. **Tamamlandı (stdlib Python kapsamı):** Explicit kaynak allowlist, digest-pinned base, network-none COPY-only build; image/base/source/worker/protocol manifest. 18 gerçek Docker testi ve temiz runtime-only wheel kurulumundan gerçek async agent/CLI/rapor doğrulandı. Kaynak commit `d55405d360bade0955c2f83fa798ae9f4a326088` için [GitHub Actions 42834398786](https://github.com/EgeBasturmen/agentguard/actions/runs/42834398786) 13/13 job başarılı; 11 artifact indirildi, JUnit ve temiz wheel/Docker kayıtları doğrulandı.
Kapı: Host import yok; state/trace forgery sonucu değiştiremiyor; crash/timeout/cleanup unknown PASS olmuyor. En az iki domain aynı yolda çalışıyor. **Phase 2 tamamlandı (desteklenen simulator/stdlib Python kapsamı):** Yerelde 448/448 test (278 unit, 152 integration, 18 Docker), failure/error/skip yok; isolated sdist→wheel, checkout dışı temiz runtime kurulumu ve gerçek Docker yürütmesi başarılı. Uzak CI: Linux/Windows × Python 3.10–3.14 hücrelerinin her birinde 278 unit + 152 integration, temiz runtime wheel; ayrı Docker işinde 18 test + paketlenmiş async agent. Failure/error/skip yok. Sıfır exit sonrası eksik stderr drain false-PASS riski regresyon testiyle kapatıldı. Destek/kalan riskler ve dosya listesi `docs/phase2-verification.md` içinde. Phase 3 kullanıcı onayı olmadan başlatılmaz.
Faz 3 — Genel connector ve environment sistemi [3–5 hafta]
- AG-030: Python SDK, HTTP agent endpoint ve language-neutral container contract. **Tamamlandı (desteklenen profiller):** Public SDK ve declarative integration CLI; worker-side Python, observed HTTP endpoint ve immutable image/v2 IPC kullanan gerçek shell container.
- AG-031: Tool registry discovery; OpenAPI ve MCP adapter’larını sürüm pinleyerek geliştirme. **Tamamlandı (desteklenen profiller):** OpenAPI 3.1.1 JSON profili ve MCP 2025-11-25 Streamable HTTP JSON discovery/invocation/session cleanup; schema/capability/version eşleştirme ve açık effect approval.
- AG-032: Staging/replay/mock/simulator routing; explicit fallback policy. **Tamamlandı (desteklenen profiller):** Dört gerçek route; strict approved replay key (version/schema/args/state/context), miss→error, live fallback yok; mock business-state PASS veremez.
- AG-033: Filesystem, HTTP, doküman dataset environment’ları. **Tamamlandı (desteklenen profiller):** Gerçek attempt-owned filesystem, immutable doküman dataset ve namespace lifecycle protokollü gerçek HTTP staging observer.
- AG-034: Fixture/reset/inspect/cleanup ve connector contract tests. **Tamamlandı (desteklenen profiller):** Public contract probe, bağımsız snapshot, doğrulanan fixture/reset/cleanup; MCP session cleanup belirsizliği dahil typed unverified/BLOCK.
- AG-035: Capability-aware preflight ve raporda coverage gaps. **Tamamlandı (desteklenen profiller):** SDK ve CLI --preflight-only; missing capability/unknown effects/protocol/mode reddi; JSON/CLI/HTML coverage gaps ve typed fingerprint-bound integration evidence.
**Phase 3 tamamlandı (10 Kasım 2026, belgelenen destek profilleri):** 492/492 yerel test (295 unit, 168 integration, 29 gerçek Docker), failure/error/skip yok. Kaynak `ea2f0e567fd684551ae5d7f5e4077dbfce3478b3` için [GitHub Actions 42854247327](https://github.com/EgeBasturmen/agentguard/actions/runs/42854247327) 13/13 job başarılı: Linux/Windows × Python 3.10–3.14, ayrı gerçek Docker/temiz wheel entegrasyonu, lint ve zorunlu gate. 11 artifact/21 JUnit/11 wheel kaydı indirildi ve doğrulandı. Destek profilleri ve kalan sınırlar `docs/phase3-verification.md` içinde. Phase 4 kullanıcı onayını bekler.
Kapı: Dış kullanıcının mevcut tool’u yeniden yazılmadan bağlanıyor; eksik gözlem PASS diye gösterilmiyor. Desteklenmeyen özel protokol için örnek adapter var.
Faz 4 — Senaryo ve dataset ürünü [3–4 hafta]
- AG-040: Rule/task import, kaynak ve çelişki gösterimi.
- AG-041: Capability/risk paketleri ve senaryo generator.
- AG-042: Fixture/oracle validation, deduplication ve generation provenance.
- AG-043: Review/approve/version/stale/retire lifecycle.
- AG-044: CSV/JSON/YAML import-export, incident-to-test.
- AG-045: Benign/adversarial/edge coverage matrisi.
Kapı: Kullanıcı tek tek YAML yazmadan üç farklı agent türü için işe yarar taslak suite oluşturabiliyor; bilinmeyen iş kuralı uydurulmuyor.
Faz 5 — Geniş evaluation ve gerçek model [3–5 hafta]
- AG-050: Model gateway ve usage/budget; mock/live ayrımı.
- AG-051: Output/tool/state/temporal evaluator registry.
- AG-052: Retrieval/source ve coding file/test evaluators.
- AG-053: Multi-turn session ve memory reset testleri.
- AG-054: Optional judge calibration, human labels ve disagreement.
- AG-055: Fault injection; attempt/blocked/effect ayrımı.
Kapı: Aynı görev farklı model/prompt sürümleriyle karşılaştırılabiliyor; judge safety FAIL’i örtemiyor; maliyet kaynağı açık.
Faz 6 — Regression ve CI [2–3 hafta]
- AG-060: Baseline lifecycle ve frozen manifests.
- AG-061: Repeat runs, uncertainty, flaky policy ve toleranslar.
- AG-062: PR/scheduled/release suite profiles, JUnit/JSON/HTML.
- AG-063: Replay/repro bundle ve sensitive data export sınırı.
- AG-064: Tek gate API, exit codes ve version compatibility.
Kapı: Gerçek bir değişiklik CI’da nedenleriyle bloklanıyor; aynı veri bütün yüzeylerde aynı kararı üretiyor.
Faz 7 — Web yönetimi ve private-runner pilot [4–6 hafta]
- AG-070: Auth/workspace/project ve API scopes.
- AG-071: Suite/rule review, run detail ve comparison UI.
- AG-072: Durable queue/run orchestration; private runner enrollment/revoke.
- AG-073: Tenant authorization, artifacts, retention/redaction.
- AG-074: Usage/quotas, pilot payment/plan/support akışı.
- AG-075: 3–5 dış ekiple onboarding ve gerçek bug doğrulaması.
Kapı: Kullanıcı dokümanla bağlanabiliyor, tekrar kullanıyor; veri erişimi ve quota server-side uygulanıyor. Teknik destekle yapılmış demo ile self-service başarı ayrı ölçülüyor.
Faz 8 — Browser, multi-agent ve uzun görev genişlemesi [4–8 hafta]
- AG-080: Browser lifecycle, test site fixtures ve authoritative action evidence.
- AG-081: Database/inbox/calendar environment paketleri.
- AG-082: Multi-agent identity, message/handoff trace ve shared state.
- AG-083: Virtual clock/event/job fixtures; delayed effect inspection.
- AG-084: Bu yüzeylere özgü security ve cancellation testleri.
Kapı: Her yeni yetenek en az bir gerçek entegrasyonda contract/E2E testli; gözlenemeyen delayed effect açıkça kapsam dışında.
Faz 9 — Genel kullanıma açılış ve operasyon [3–5 hafta]
- AG-090: Yük, soak, queue duplication, crash/restart ve cleanup testleri.
- AG-091: Backup restore, schema migration, retention/delete doğrulaması.
- AG-092: Billing reconciliation, cancellation, recovery ve plan limits.
- AG-093: Security review, abuse limits, incident runbook ve support docs.
- AG-094: Onboarding funnel, status/error iletişimi ve release docs.
Kapı: Hesap oluşturma → bağlama → test → ödeme/iptal → veri silme yolculuğu tamam. Operasyon hataları müşteri verisini veya verdict’i bozmaz.
Faz 10 — Managed execution / enterprise [ayrı plan]
- AG-100: Managed build/worker pools ve güçlü tenant isolation.
- AG-101: Dedicated/private deployment, SSO ve ileri audit gereksinimleri.
- AG-102: Daha fazla framework, adapter signing/version governance ve kontrollü plugin dağıtımı.
- AG-103: Kapasite, unit economics ve bağımsız güvenlik doğrulaması.
Kapı: Shared hostile-code threat model’i ayrı değerlendirilmiş; pilot talebi ve operasyon bütçesi mevcut. Bu faz ilk ücretli pilotun zorunlu koşulu değildir.
18. Bağımlılıklar ve gerçekçi takvim
Ana zincir: sözleşmeler → sonuç doğruluğu → izolasyon → genel connector/environment → scenario/evaluation → CI → pilot → operasyon. UI tasarımı ve dokümantasyon erken başlayabilir; unstable contract üzerine büyük UI implementasyonu yapılmaz. Browser/multi-agent, kendi environment sözleşmeleri hazır olmadan core’a özel if bloklarıyla eklenmez.

Önerilen teslim seviyeleri:
Seviye	Kapsam	Yaklaşık takvim varsayımı
Teknik alpha	Genel contract + doğru evaluator + gerçek izolasyon	6–10 hafta
Dış entegrasyon alpha	Python/HTTP/container + temel ortamlar	10–16 hafta
Ücretli private-runner pilot	Suite generation/review, evaluation, CI, temel web	5–8 ay
Daha geniş self-service v1	Browser/multi-agent’ın seçili kapsamı + operasyon	8–12 ay
Managed/enterprise	Daha güçlü hosting ve kurum ihtiyaçları	Ayrı kapasite planı


Bu tahminler tek geliştirici için kapsamın tamamını görünür kılar; faz süreleri bire bir takvim sözü değildir. Scope azaltımı pilotu hızlandırabilir. Teknik destek matrisi daraltılabilir; ürün sektör bağımsız kalır.
19. İlk 30 gün ve ilk teslim
1. hafta: AG-001/002; mevcut false-PASS testlerini kalıcılaştır; strict schema ve dependency düzeltmesi.
2. hafta: Path/evaluator hataları; tek comparator; stable IDs/fingerprint; clean install ve CI ayrımı.
3. hafta: RunController/AgentSpec; worker import; broker IPC; canonical state/audit ayrımı.
4. hafta: Timeout/cleanup E2E; biri dosya, biri API kullanan iki agent ile aynı akış; CLI capability ve result raporu.
İlk demo: dosya düzenleyen agent ve API kullanan agent aynı CLI/core üzerinde çalışır. Good sürüm geçer; yetkisiz etki, bozuk spec, trace forgery ve timeout doğru bloklanır. Sektöre özgü runner eklenmez. Otomatik scenario UI’sı bu temel doğrulanmadan öncelik almaz.
20. Doğrulama stratejisi ve launch checklist
Test katmanları: unit; adapter contract; integration; gerçek worker E2E; adversarial integrity; property/fuzz; packaging; migration/compatibility; load/soak; recovery; UI journey.

Golden set: bilinen doğru ve hatalı agent varyantları; farklı domain ve capability’leri kapsar. Bilinen hatayı yakalama oranı ve iyi davranışı yanlış bloklama oranı birlikte ölçülür. Bu benchmark farklı gerçek agent’lara genel garanti diye sunulmaz.

Launch kriterleri:
- Temiz ortamdan belgeli kurulum ve en az üç farklı capability örneği çalışır.
- Kullanıcı mevcut agent/tool’unu desteklenen yol üzerinden yeniden yazmadan bağlar.
- Observed/controlled ve unsupported capability açık görünür.
- Empty/malformed/unevaluated sonuç PASS değildir.
- Authoritative evidence worker’dan korunur; report loader tutarsız veriyi reddeder.
- Baseline/candidate kararları bütün yüzeylerde aynıdır.
- State/session izolasyonu, retry ve cancellation testlidir.
- Model/worker/storage bütçeleri backend’de uygulanır.
- Tenant access, redaction, export ve delete testlidir.
- Backup restore, worker loss ve restart toparlanması denenmiştir.
- Pilot kullanıcı gerçek hatayı bulmuş, açıklamayı anlayıp düzeltmiş ve tekrar kullanmıştır.
- Desteklenen sürümler, bilinen sınırlamalar ve support yolu belgelenmiştir.
21. Başarı metrikleri ve risk kaydı
Ölçümler: bağlantı başarı oranı; ilk anlamlı sonuca kadar süre; suite başına manuel düzeltme; kabul edilen generated-case oranı; eksik oracle oranı; mutation benchmark detection/false-block oranı; tekrar üretilebilirlik; infra error/cleanup unknown; run cost; weekly returning projects; pilot conversion.
Risk	Karşılık
Genel ürün hedefi kapsamı patlatır	Genel contract, kademeli teknik destek matrisi
Otomatik generator yanlış oracle üretir	Provenance, review, deterministic controls, calibration
Telemetry eksikliği yanlış güven verir	Capability preflight ve INCONCLUSIVE/coverage gap
Mock gerçek sistemi temsil etmez	Contract/staging compatibility ve görünür fidelity
Güvenilmeyen kod controller’a ulaşır	Build/runtime/evaluator trust boundaries
Model varyansı gate’i oynatır	Repeats, uncertainty ve immutable suites
Hosting maliyeti büyür	Private runner, quota, budget ve usage reconciliation
Yeni entegrasyon bakım yükü yaratır	Adapter contract tests ve desteklenen sürüm politikası
Veri sızıntısı	Redaction, scopes, egress, retention ve test fixtures
Dashboard geliştirmesi çekirdeği geciktirir	Önce iki farklı capability ile uçtan uca slice


22. Stratejik karar
AgentGuard’ın tekrar kullanılabilir ürünü “her müşteri için tool yazan ekip” değildir. Ürün; agent, tool ve environment bağlantılarını ortak sözleşmeye taşıyan; senaryoları üreten/yöneten; kanıtları toplayan ve sürüm kararını açıklayan motordur.

Birinci öncelik: doğru sonuç + güvenilir execution. İkinci: farklı agent/tool ortamlarına bağlanma. Üçüncü: senaryo üretimi ve güçlü evaluation. Dördüncü: CI ve kullanıcı deneyimi. Beşinci: sürdürülebilir hosted operasyon. Sektörel paketler opsiyonel genişlemedir; core mimari sektör bağımsız kalır.
