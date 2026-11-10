# AgentGuard

AI agent'ları için yerel davranış, tool kullanımı ve state değişikliği testleri.

## Kurulum ve ilk test

Python 3.10 veya üzeri gerekir. CI hedef matrisi Python 3.10–3.14,
Linux ve Windows'tur; gerçek doğrulama durumu aşağıdaki Phase 1 belgesindedir.
Proje kökünde yalnızca runtime bağımlılıklarıyla kurulum:

```console
python -m pip install .
agentguard demo --output-dir reports/demo
```

Paketlenmiş demo checkout dışında da çalışır. `examples.*` modülleri ve kökteki
`scenarios/` yalnızca kaynak checkout'unda bulunur; checkout içindeki ilk tek-test:

```console
docker pull python:3.11-slim
agentguard test --agent examples.refund_agent:GoodRefundAgent --scenario scenarios/refund_order.yaml
```

Geliştirme kurulumu ve Docker gerektirmeyen testler:

```console
python -m pip install -e ".[dev]"
python -m pytest -m unit
python -m pytest -m integration
```

Docker smoke testleri için çalışan Linux Docker daemon'ı ve worker image gerekir:

```console
docker build -f docker/Dockerfile.worker -t agentguard-worker:dev .
docker pull python:3.11-slim
python -m pytest -m docker
```

Tüm testler `python -m pytest` ile çalışır; Docker yokluğu tam koşuda başarı/skip
olarak gizlenmez. Her test tam bir `unit`, `integration` veya `docker` katmanına
atanır. Mock Docker testleri unit, gerçek subprocess/CLI/rapor yolları integration,
gerçek daemon/container testleri docker katmanındadır. Bilinmeyen marker ve
çalıştırılmamış coroutine RuntimeWarning'leri hata sayılır.

Wheel ve kaynak dağıtımını oluşturup gerçek temiz kurulumu doğrulamak için:

```console
python -m pip install -e ".[dev]"
python -m build
python scripts/check_wheel.py dist --result artifacts/wheel-check.json
```

`dist` altında eski sürümler varsa doğrulanacak tek wheel dosyasını açıkça verin.
Kontrol betiği ayrı venv oluşturur; dev extras/site-packages/PYTHONPATH aktarmaz.
Checkout dışındaki geçici dizinden gerçek console entrypoint, YAML fixture'ları,
good/bad verdict, geçersiz spec exit kodu ve CLI/HTML/JSON karar eşitliğini denetler.

GitHub Actions workflow'u: [ci.yml](.github/workflows/ci.yml). Linux/Windows Python
matrisinin her hücresinde unit/integration ve isolated sdist→wheel build/temiz
kurulum; ayrı işte gerçek Docker testleri çalışır. `Phase 1 gate` bütün işlerin
başarısını gerektirir. [Phase 1 kabul ve doğrulama kaydı](docs/phase1-verification.md)
uzak CI koşusu ile yerel eşdeğer kontrolleri birbirinden ayırır.

## Yürütme ve güven sınırı

`test` ve `regression` varsayılan olarak gerçek Docker container kullanır.
Çalışan Linux Docker daemon ve önceden çekilmiş `python:3.11-slim` gerekir.
Agent host'ta import edilmez; entrypoint `.py`, paket `__init__.py` ve açıkça
`--include` ile seçilen Python dosyaları image'a kopyalanır. Kullanıcı Dockerfile'ı,
setup hook'u veya dependency installer çalıştırılmaz.

```console
docker pull python:3.11-slim
agentguard test --source-dir . --agent examples.refund_agent:GoodRefundAgent --scenario scenarios/refund_order.yaml
agentguard regression --source-dir . --baseline examples.refund_agent:GoodRefundAgent --candidate examples.refund_agent:BadRefundAgent --scenario scenarios/refund_order.yaml
```

`--policy policy.json` tool izinlerini, tam değer eşleşmeli resource scope'larını,
süreleri ve çıktı limitlerini belirler. Varsayılan politika seçilen simulator'ın
kayıtlı tool'larına izin verir; açıkça verilen boş `allowed_tools` tümünü reddeder.
Sync/async `run(task, sandbox)` desteklenir; tool çağrıları senkron IPC'dir.

Eski yerel API `--trusted-local` ile korunur; yalnızca güvenilen kodla kullanılmalıdır.
Özel host sandbox import'u bu seçeneği gerektirir. Paketlenmiş `demo` güvenilen
örnekleri yerel çalıştırır; Docker güvenlik kanıtı sayılmaz.

```console
agentguard test --trusted-local --agent examples.refund_agent:GoodRefundAgent --scenario scenarios/refund_order.yaml
```

Docker bir VM değildir. Otoritatif evidence garantisi broker simulator state ve
çağrılarıyla sınırlıdır; output doğruluk kanıtı sayılmaz. API, desteklenen
garantiler, testler ve riskler: [Phase 2 doğrulama belgesi](docs/phase2-verification.md).

## Senaryo ve değerlendirme sözleşmesi

- Bilinmeyen alanlar, yinelenen YAML anahtarları, boş ad/task/path ve kontrolsüz
  senaryolar reddedilir.
- Tool beklentisi `times` veya `max_times` içermelidir. Limitler negatif olamaz;
  ikisi birlikte verilirse `times <= max_times` olmalıdır.
- Yinelenen kontroller, bilinmeyen invariant türleri ve beklenen state ile
  yasak değişiklik arasındaki çelişkiler reddedilir.
- Path'ler `customers.CUST-1.status` biçimindedir. Listeler için `items.0.name`,
  kök state için `$` kullanılır. Nokta, ters eğik çizgi ve `$` içeren anahtarlar
  sırasıyla `\.`, `\\` ve `\$` ile kaçırılır. YAML içinde bu path'leri tek
  tırnakla yazın: `'files.report\.txt.content'`.
- Liste indeksleri sıfırdan başlar; negatif veya başında gereksiz sıfır olan
  indeksler desteklenmez. Diff için dictionary anahtarları boş olmayan string
  olmalıdır. State eşitliği tip duyarlıdır: `true`, `1` ve `1.0` farklıdır.
- Eksik beklenen path davranış başarısızlığıdır. Evaluator/fixture hatası
  `status=error`, `evaluation_status=error` olarak kaydedilir.
- PASS için en az bir kontrol, `evaluation_status=complete`, başarılı execution
  ve uygun cleanup gerekir. Eski sonuçlarda evaluation alanı eksikse varsayılan
  `not_run` olur; typed sonuç doğrudan PASS kabul edilmez.
- `evaluation_status=complete`, tanımlı kontrollerin değerlendirildiğini belirtir;
  agent'ın güvenli olduğunu veya tüm davranışlarının gözlendiğini kanıtlamaz.

CLI `test` için exit kodları: `0` kabul, `1` davranış/yürütme/değerlendirme
başarısızlığı, `2` geçersiz spec veya yükleme hatasıdır. Tek koşunun CLI/JSON/HTML
çıktıları aynı `effective_pass` kararını kullanır.

## Phase 0 sözleşmeleri ve örnekleri (AG-001–005)

Sürümlü AgentSpec, ToolDefinition, EnvironmentSpec ve ScenarioVersion taslakları
`agentguard.contracts` içindedir. Mevcut Scenario, evaluator, runner ve gate
korunmuştur. Dosya, araştırma/retrieval ve API/resource örneklerini aynı core'da
çalıştırmak için:

```console
python -m examples.phase0
```

Örnekler güvenilen yerel Python ve bellek içi simulator kullanır. Gerçek terminal,
network veya model erişimi yoktur. Mode `observed`, policy sonucu `unknown` olarak
kaydedilir. Controlled, model usage, cancellation, reset ve unsupported
adapter/version/protocol istekleri desteklenmiş gibi çalıştırılmaz.

Tanımlar modül import etmeden doğrulanır. `assess_compatibility` sürüm pinli destek
kontrolü, `summarize_result` mevcut gate'ten çok boyutlu sonuç görünümü sunar.
Schema/resource scope/limit deklarasyonları runtime enforcement değildir.

- [Sözleşmeler, başlangıç denetimi ve kabul kapısı](docs/phase0-contracts.md)
- [Core/adapter/evaluator destek matrisi](docs/supported-matrix.md)
- [Gerçek local/hosted akışa göre tehdit modeli](docs/threat-model.md)

Phase 0, gerçek izole execution veya connector implementasyonu tamamlandı
anlamına gelmez; bu işler sonraki roadmap fazlarında kalır.

## Karşılaştırma ve release kararı (AG-013)

`regression` ve kaydedilmiş koşuları karşılaştıran `compare`, aynı
`RegressionComparator` ve typed `ComparisonResult` sonucunu kullanır. Her ikisi
de aynı sonuçtan HTML ve JSON üretebilir:

```console
agentguard regression --baseline examples.refund_agent:GoodRefundAgent --candidate examples.refund_agent:BadRefundAgent --json-report reports/comparison.json --html-report reports/comparison.html
agentguard compare reports/baseline.json reports/candidate.json --json-report reports/comparison.json --html-report reports/comparison.html
```

`compare` girdileri `test --json-report` ile üretilmiş tek-koşu raporlarıdır;
karşılaştırma JSON çıktısı tekrar koşu raporu olarak içe aktarılmaz.

- `comparison_status`: `verified`, `incompatible` veya `unverified`.
- `regression_detected`: gözlenen yeni başarısızlık, kapsam kaybı veya davranış
  değişikliği. `no_regression` ancak karşılaştırma doğrulanmışsa true olabilir.
- `gate_decision`: `ALLOW` veya `BLOCK`. Candidate başarısızsa, yeni regresyon
  olmasa bile BLOCK üretilir. CLI exit kodu bu karardan türetilir: ALLOW `0`,
  BLOCK `1`, bozuk/çelişkili rapor veya okuma/yükleme hatası `2`.
- Kaldırılan assertion, eksik/tekrarlanan kontrol kimliği, eksik manifest ve
  farklı fingerprint kabulü engeller. Mevcut tool/mutation değişikliği kuralları
  korunur; severity politikasının genişletilmesi bu görevin kapsamı değildir.

Kontrol kimlikleri kontrol türü ve hedefinden türetilir. Beklenen değer/limit
değiştiğinde kimlik sabit kalır; fingerprint değişir. Fingerprint; senaryo adı,
task, başlangıç fixture'ı, tüm kontrol tanımları, evaluator sürümü ve environment
kimliğini kapsar. Dictionary/kontrol sırası hash'i değiştirmez. Fixture ve oracle
değerleri JSON uyumlu ve sonlu sayılardan oluşmalıdır. Varsayılan environment
kimliği sandbox factory'nin Python kimliğidir; özel sürüm/config için
`ScenarioRunner(..., environment_id="my-environment-v2")` verilebilir.

Yeni koşu raporları `schema_version=2` kullanır. Loader tipleri, bilinmeyen
alanları, yinelenen JSON anahtarlarını, manifest hash'ini ve kaydedilmiş verdict'in
sonuçla uyumunu denetler; giriş 16 MiB ile sınırlıdır. Schema v1 tanınır ve v2
modeline taşınır; eksik değerlendirme kanıtı, manifest veya kontrol kimliği
uydurulmaz. Eski PASS raporu evaluation kanıtı taşımıyorsa reddedilir; manifest'i
olmayan geçerli raporlar karşılaştırmada BLOCK alır. Bu koşuları yeniden çalıştırın.

Python API'de `load_report` artık `RunReport`, `compare_reports` ise
`ComparisonResult` döndürür. Eski `RegressionResult` import'u alias olarak korunur.
Fingerprint imza veya bağımsız attestation değildir; agent kodunun hash'i de
değildir. Ortam/evaluator implementasyonu değişirse ilgili kimlik/sürüm
güncellenmelidir. Manifest fixture ve oracle tanımlarını içerir; redaction ve
izole yürütme ayrı roadmap görevleridir.

## Demo video

[▶ Watch AgentGuard Demo](https://www.youtube.com/watch?v=HI7czD8PkOg)
