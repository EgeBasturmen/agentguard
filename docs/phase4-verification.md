# Phase 4: genel senaryo üretimi ve onaylı dataset

Doğrulanmış Phase 3 `77e7f76f697bf4db473cb2c9f63e8a59236fa02f` üzerine aynı
`artifacts/phase2/repo` checkout'unda, `codex/development` dalında geliştirilir.
Yeni proje kopyası oluşturulmaz. Main, özgün index ve kullanıcı raporları korunur.

## Kapsam ve kullanım

Mevcut Phase 0–3 ScenarioRunner, IntegrationPlan, RunController, ToolBroker,
environment observer, evaluator ve effective_pass kullanılır. İkinci agent yürütme
motoru veya sektöre özel senaryo üreticisi eklenmez. Public API `agentguard.sdk` ve
`agentguard.generation` altındadır; CLI aynı servisleri çağırır.

```console
agentguard scenario generate examples/phase4_request.json --db artifacts/scenarios.sqlite --output artifacts/drafts.json
agentguard scenario show CASE_ID 1 --db artifacts/scenarios.sqlite
agentguard scenario validate CASE_ID 1 --db artifacts/scenarios.sqlite
agentguard scenario review CASE_ID 1 --db artifacts/scenarios.sqlite --reviewer operator --confirm-fixture --confirm-classification --confirm-oracle CHECK_ID
agentguard scenario approve CASE_ID 1 --db artifacts/scenarios.sqlite --reviewer approver --fingerprint CASE_FINGERPRINT
agentguard scenario freeze release --db artifacts/scenarios.sqlite --member CASE_ID:1 --output artifacts/suite.json
agentguard scenario run artifacts/suite.json --db artifacts/scenarios.sqlite --base-dir . --output artifacts/generated-run.json
agentguard scenario coverage artifacts/drafts.json --db artifacts/scenarios.sqlite --execution artifacts/generated-run.json
```

`CASE_ID`, `CHECK_ID`, `CASE_FINGERPRINT` gerçek `show`/`validate` çıktısından alınır.
Her oracle ayrı `--confirm-oracle` ister. İnceleme; fixture, sınıflandırma ve beklenen
sonucun bağımsız doğruluğu görüldükten sonra yapılır. Örnek inceleme komutu tek
oracle gösterir; örnek dataset'in tüm oracle kimlikleri onaylanmalıdır.
Çıktılar mevcut dosyanın üzerine yazılmaz. Relative agent source_dir, `run` için
verilen base-dir'e göre çözülür. Agent kaynağı generation/validation sırasında
host'ta import edilmez; Python agent gerçek container worker içinde yüklenir.

## AG-040–045 uygulaması

| Görev | Uygulama ve doğrulama sınırı |
| --- | --- |
| AG-040 | JSON/YAML generation request task/fixture/runtime; CSV/JSON/YAML rule import. Sabit source ID, version, URI ve içerik hash'i. Aynı hedefte çelişen bool/int dahil oracle'lar sessizce birleştirilmez; validate kaynakları gösterir. |
| AG-041 | Deterministic `deterministic-rules@1.0.0`; benign görev, `instruction_override` risk varyasyonu ve `schema_boundary` tool argüman sınırları. Capability gereksinimi runtime preflight ile doğrulanır. Python/HTTP/container için ortak veri modeli. |
| AG-042 | Scenario schema, fixture, izinler/resource scope, tool kontratı, capability ve oracle kaynak eşlemesi; exact-content dedup. Generator sürümü, request hash'i, seed/config, kaynak sürümü ve gerekçe her taslakta tutulur. |
| AG-043 | Immutable revision, explicit review/approve, frozen suite, stale/retire. Validate saf readiness kontrolüdür; ayrı kalıcı validated etiketi kullanılmaz. Active üyelik frozen suite ile temsil edilir. Kural/kontrat sync veya yeni revision önceki onayı geçersiz kılar. |
| AG-044 | CSV/JSON/YAML proposals roundtrip; import her zaman yeni draft revision'dır, onay taşımaz. Mevcut typed RunReport'tan incident task/fixture/gözlemleri alınır; gözlemler oracle yapılmaz. Dataset kimliği revision/hash'ler ve immutable suite snapshot üzerinden sabitlenir. |
| AG-045 | Benign/adversarial/edge × rule/contract/risk matrisi; proposed, approved, executed, passed ayrı. Gerçek assertion kimliği ve controller tool audit argümanları aranır. Eksik alanlar JSON/CLI'da raporlanır. |

Desteklenen kural dilinin örnekleri:

```text
expect records.status = "ready"
unchanged records.protected
tool resource.write times 1
tool resource.read max_times 2
require supports_tool_observation
```

Beklenen değer finite JSON'dur; path dili mevcut escaped-dot/list-index evaluator
dilidir. Serbest doğal dil veya belirsiz iş kuralı unresolved kalır. Schema-valid
argümanlardan iş sonucu çıkarılmaz. Trace cevabı, eski PASS, state veya model
cevabı ground truth sayılmaz. Çözümlenmeyen oracle `validate` çıktısında
INCONCLUSIVE'dır; review/approve/freeze mümkün değildir. `revise` bağımsız gerekçe
ve yeni scenario içeriğiyle manual kaynak oluşturur; yeni sürüm tekrar incelenir.

Default frozen suite yalnızca seçilmiş onaylı üyeler için karar verir. Tüm planlanan
matrisin zorunlu olması için `freeze --required-coverage artifacts/drafts.json`
kullanılır. Eksik başarılı kanıt suite'i BLOCK yapar. Tek bir benign koşu
adversarial/edge coverage sağlamaz. Onaysız, retired veya stale sürüm release'e
alınmaz; her koşudan önce/sonra ve tüm suite sonunda transaction içinde admission
kontrol edilir. Kaynak, oracle, suite membership, review ve comparison manifest
hash'leri receipt'e bağlanır. Boundary case'in ilan ettiği tool/argüman gerçekten
audit'te tamamlanmadıysa, normal oracle geçse bile BLOCK olur.

## Sınırlar ve kalan riskler

- Oracle doğruluğu insanın bağımsız fixture/kural incelemesine bağlıdır. Hash'ler
  imza, kimlik doğrulama veya bağımsız attestation değildir. SQLite/reviewer kimliği
  güvenilen yerel operatör sınırındadır; SaaS RBAC sonraki fazdadır.
- Dataset ve raporlar task, fixture ve trace verisi içerir; üretim secret'ları
  girilmemelidir. Otomatik redaction, kriptografik imza ve dağıtık audit yoktur.
- Dış kaynak değişimi dosya/URL watcher ile izlenmez; `sync_sources` veya yeniden
  import gerekir. Canlı tool contract drift discovery sırasında agent başlamadan
  reddedilir. Eski rapor karar anının kaydıdır; yeni release için yeniden yürütün.
- Public Candidate.plan onaysız generation marker taşır; mevcut ortak gate reddeder.
  Güvenilen operatörün metadata'yı bilerek kaldırıp legacy manuel scenario yapması
  veya DB/receipt'i yeniden üretmesi kimlik doğrulamalı güvenlik sınırı değildir.
- HTTP agent observed-only kalır; iç tool çağrısı/izolasyon kanıtı üretmez.
  Python ve container aynı mevcut controlled güvenlik profilini kullanır.
- Üretim bounded, deterministic ve exact-content dedup yapar; LLM/paraphrase,
  semantik benzerlik ve fuzzy clustering yoktur. Schema sınır üretimi primitive
  minimum/maximum, bounded string length ve enum uçları içindir; desteklenmeyen
  şemalar gap üretir. Hata/temporal/output judge oracle genişletmeleri Phase 5'tedir.
  Input 1 MiB, tek candidate 2 MiB, proposal materialization 200 case/8 MiB,
  dataset import/export 8 MiB ile sınırlıdır. Limitler bellekte üretim sırasında da
  uygulanır ve kesilen üretimin eksik kapsamı görünür kalır.
- Coverage açık sonlu requirement matrisidir; kapsamlı güvenlik veya yüzde yüz
  davranış güvencesi değildir. Manual oracle replacement eski rule kaynaklarının
  coverage'ını otomatik devralmaz. Yeni source bağları ayrıca incelenmelidir.

## Doğrulama durumu

Yerel test, temiz wheel ve GitHub CI sonuçları tamamlandığında bu bölüm gerçek
kanıtlarla güncellenecektir. Bu aşamada Phase 4 tamamlandı olarak işaretlenmemiştir.

Yeni testler: `test_generation.py`, `test_scenario_repository.py`,
`test_generated_execution.py`, `test_generated_docker.py`, `test_scenario_cli.py`.
False-PASS regression testleri önce başarısız çalıştırılmış, ardından uygulama
düzeltilmiştir: bool/int conflict, geç retirement, boundary stimulus'un agent
tarafından yok sayılması, onaysız plan preview ve değişen comparison context.
Kontrat bool/int drift ve proposal materialization sınırı için de önce başarısız
regression testi yazılmıştır.

Yeni modüller: `generation/models.py`, `generator.py`, `validation.py`,
`repository.py`, `evidence.py`, `execution.py`, `coverage.py`, `io.py`, `cli.py`.
Ortak entegrasyon/gate değişiklikleri: `integrations/plan.py`, `core/scenario.py`,
`core/result.py`, `core/outcome.py`, `runner/runner.py`, `sdk.py`, `cli/main.py`.
Paket/CI: `scripts/check_wheel.py`, `.github/workflows/ci.yml`, `tests/conftest.py`.
