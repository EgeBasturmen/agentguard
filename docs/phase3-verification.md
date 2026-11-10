# Phase 3: genel connector ve environment sistemi

Kalıcı geliştirme dalı `codex/development`, aynı `artifacts/phase2/repo` checkout'unda,
doğrulanmış Phase 2 `37e3638dc6c65681df332251eb912887cac424c7` üzerinden açıldı.
Yeni proje kopyası oluşturulmadı; özgün main, index ve kullanıcı raporları korunur.

## Ön inceleme ve kapsam

Phase 2'nin Python kaynak paketleme, güvenli Docker runtime, v1 tool IPC/v2 lifecycle,
ToolRegistry/ToolBroker, transactional handler, audit ve ortak evaluator/gate'i hazırdı.
Yeniden yazılmadılar. Genel connector discovery, ortam yaşam döngüleri, HTTP agent,
pinned genel container artifact'i, mode routing ve capability-aware preflight eksikti.

Phase 3'te `integrations/`, `environments/` ve public `agentguard.sdk` bu mevcut
motorun üstüne eklendi. `RunController` yalnızca bağımsız state observer ve immutable
container image seçimini destekleyecek şekilde genişletildi. Yeni evaluator yoktur.

## Desteklenen entegrasyonlar

| Yüzey | Gerçek uygulama | Sınır |
| --- | --- | --- |
| Python agent | `PythonAgent(AgentLaunchSpec(...))`; worker-side import | Standard-library Python, açık kaynak allowlist; host import yok |
| Container agent | `ContainerAgent(ContainerLaunchSpec(image=...))` | Yerel immutable image id/registry digest, JSON-lines v2 lifecycle/v1 tools; gerçek shell worker testli |
| HTTP agent | POST JSON endpoint ve bağımsız HTTP staging observer | Observed-only; remote iç tool trace, interception, session reset/model usage iddiası yok |
| OpenAPI tool | 3.1.1 discovery ve gerçek JSON HTTP invocation | Scalar path/query, JSON body/response, acyclic local refs; security/regex/remote refs ve karmaşık serialization reddedilir |
| MCP tool | 2025-11-25 initialize/initialized, tools/list pagination, tools/call, session DELETE | Streamable HTTP JSON response profili; SSE, stdio, OAuth ve farklı protocol sürümleri desteklenmez |
| Filesystem | Attempt-owned gerçek geçici workspace; read/write/delete ve bağımsız snapshot | UTF-8 dosya mapping; path/symlink/hardlink/boyut sınırları |
| Document dataset | Gerçek dosyada immutable versioned fixture; get/search | Deterministic içerik araması; RAG kalitesi evaluator'u değildir |
| HTTP environment | Namespaced gerçek fixture/inspect/reset/delete protokolü | Ayrılmış staging gateway ve doğrulanan synchronous-effect/namespace lifecycle sözleşmesi gerekir |
| Custom protocol | `examples/custom_connector.py`, aynı broker/observer/contract yolu | Güvenilen module-level adapter kodu; otomatik host plugin import'u yok |

OpenAPI ve MCP'nin yeni sürümleri otomatik kabul edilmez. Pinler resmi belirtimlere
dayanır: [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html),
[MCP 2025-11-25 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
ve [transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
Schema/annotation güvenlik izni sayılmaz; örneğin MCP `readOnlyHint` otomatik izin vermez.

## CLI ve SDK

Kaynak agent generic `sandbox.call(name, arguments)` veya `call_tool(name, **kwargs)`
kullanır. Uzak tool business implementasyonu yeniden yazılmaz; bağlantı adapter'ı ve
staging lifecycle observer yapılandırılır. Üretim servislerine rastgele passthrough yoktur.

```python
from pathlib import Path
from agentguard.sdk import IntegrationRunner, PythonAgent, FilesystemEnvironment, environment_tools
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.core.scenario import Scenario
from agentguard.security.policy import SecurityPolicy

if __name__ == "__main__":
    environment = FilesystemEnvironment()
    scenario = Scenario(
        name="edit-note", task="Set note.txt to done",
        initial_state={"files": {"note.txt": "draft"}},
        expected_state=[{"path": r"files.note\.txt", "value": "done"}],
    )
    result = IntegrationRunner().run(
        scenario,
        PythonAgent(AgentLaunchSpec(reference="phase3_agent:FileAgent", source_dir=Path("examples"))),
        environment, environment_tools(environment),
        policy=SecurityPolicy(allowed_tools=frozenset({"file.write"})),
    )
```

Declarative plan (`schema_version=1`) inline mevcut `Scenario`, agent, environment,
tool connection ve policy içerir. Bilinmeyen alanlar reddedilir. Python source_dir
plan dosyasına göre çözülür; host'ta agent import edilmez. Aynı plan iki yolla kullanılır:

```console
agentguard integration examples/phase3_plan.json --preflight-only
agentguard integration examples/phase3_plan.json --json-report reports/run.json --html-report reports/run.html
```

`--preflight-only` discovery/compatibility kontrolü yapar; fixture provision veya agent
invoke etmez. MCP initialize/discovery metadata istekleri bağlantı gerektirir.
`preflight(...)`, `environment_tools(...)` ve `contract_test(environment, fixture)`
SDK'dan kullanılabilir. Contract probe gerçek provision/snapshot/reset/cleanup çalıştırır.

## Mode routing ve güvenlik

- Simulator, staging, replay ve mock açık seçilir; yalnızca `fallback="error"` desteklenir.
- Staging tool effect_type ve strict boolean onayı gerektirir. Bağlantı origin/path'i
  açık namespace environment ile eşleşmelidir. Input schema ve resource scope broker'da
  çağrıdan önce uygulanır. Agent'a ağ/host filesystem/env erişimi açılmaz.
- HTTP connector sabit origin kullanır; proxy env ve redirect kullanmaz. DNS adresleri
  doğrulanır ve bağlantı seçilen IP'ye pinlenir (TLS SNI/sertifika hostname'i korunur).
  Private/loopback host'lar exact grant gerektirir. Link-local/metadata, multicast ve
  unspecified adresler grant verilse de reddedilir. Plain HTTP ayrı boolean grant ister.
- Remote schema/ref indirme, credential/header injection ve kullanıcı Dockerfile build'i
  yoktur. JSON Schema 2020-12 runtime validator'u kullanılır; regex/dynamic refs/resource-scoped identifiers/anchors ve bilinmeyen dialect güvenlik
  nedeniyle reddedilir. HTTP/message/schema/state boyutları ve süreleri bounded'dır.
- State gerçek environment observer'dan alınır. Worker veya remote HTTP agent'ın
  state/trace/PASS beyanı kabul edilmez. HTTP agent için tool assertion/interception
  gereksinimi varsa preflight BLOCK olur; endpoint çıktısı tek başına PASS değildir.
- Replay key; tool/adapter version ve schema fingerprint, canonical arguments, fixture
  state ve context içerir. Yalnızca strict boolean ile onaylı kayıt eşleşir. Miss/version/
  fixture/context farkı hata olur; canlı bağlantıya veya mock'a fallback olmaz.
- Mock statik/schema-validated interface cevabıdır; state/effect/invariant kabul
  kriterlerini karşılamasına izin verilmez. Interface-only PASS iş doğruluğu iddiası değildir.
- Staging oracle/forbidden-effect kontrolleri dış etkiyi atomik olarak geri alamaz.
  Resource scope önleyicidir; bağımsız snapshot/audit ve namespace cleanup etkileri
  doğrular. Bu nedenle yalnızca ayrılmış test namespace'i desteklenir.
- Environment cleanup ve Docker cleanup ayrı doğrulanır. Herhangi biri unverified ise
  sonuç BLOCK'tur. `integration_status`, `integration_manifest` ve `coverage_gaps`
  typed result/JSON/CLI/HTML'e taşınır; eski raporlar yeni alanların güvenli varsayılanlarıyla açılır.

Container artifact'i `artifact_kind=container_image` olarak raporlanır. Opaque
prebuilt image için source/worker hash image id'den, base digest image kimliğinden gelir;
bu kaynak Dockerfile'ının/dependency history'sinin doğrulandığı iddiası değildir.

Integration metadata typed bir envelope ve SHA-256 fingerprint ile comparison manifest'e
bağlıdır. Mode/endpoint/operation/context değişikliği fark edilir. Bu bir dijital imza
değildir. Provisioning süresinin değişmesi ilan edilen policy fingerprint'ini değiştirmez;
kalan çalışma zamanı ayrı absolute monotonic deadline ile uygulanır. MCP session cleanup
hatası transactional process sınırından typed cleanup hatası olarak taşınır ve unverified olur.

## Test ve CI kanıtı

İlk red testler eksik network policy/routing/environment sınıflarını gösterdi.
Yeni CLI komutunun yokluğu gerçek CliRunner testinde yeniden üretildi.
Ek red→green güvenlik testleri strict grant/approval, link-local metadata engeli ve
endpoint/operation değişiminde fingerprint farklılığını doğruladı. Testler yalnızca
mock transport'a dayanmaz: ThreadingHTTPServer üzerinde gerçek servis/agent/MCP,
gerçek filesystem/dataset ve Docker içinden broker çağrıları kullanılır.

Son kaynak koşusu: **492 passed**, 157.99 saniye; 295 unit, 168 integration,
29 gerçek Docker; failure/error/skip yok. Phase 0–2'nin 448 testi korunur; 44 yeni
Phase 3 testi eklendi. Yeni testsupport dosyaları sdist manifest'ine açıkça eklendi;
temiz paket kontrolü kaynak dağıtımında conftest/helper varlığını da doğrular.

Yerel kanıtlar `artifacts/phase3-tests.xml`, `artifacts/phase3-build.log`,
`artifacts/phase3-wheel-check.json` içindedir ve Git'e eklenmez. Kaynak commit ve uzak CI artifact doğrulaması aşağıda kaydedildi.
Nihai Phase 3 kapanışı yalnızca bu doğrulama başarıyla tamamlandıktan sonra işlendi.
Phase 4 başlatılmadı.

## Bilinen sınırlar

Docker kernel/daemon, controller, kaynak checkout ve connector kodu güvenilen tabandır;
VM veya düşmanca multi-tenant isolation iddiası yoktur. Staging observer'ın lifecycle
sözleşmesi güvenilen servis sınırıdır; gizli/delayed job, webhook veya namespace dışı
etkiler kanıtlanmaz ve bu profil kapsamında kabul edilmez. HTTP agent'ın dışarıdaki
araçlarını platform OS seviyesinde denetleyemez; bu nedenle observed olarak raporlanır.

OpenAPI/MCP destek profilleri sınırlıdır; authentication/production secrets, SSE/stdio,
çok turlu session reset, gerçek model budget/output quality ve otomatik mock generation
bu fazın destek iddiasına dahil değildir. Ek dependency içeren agent build'i desteklenmez.
Raporlar hassas tool/fixture verisi içerebilir; redaction/signing/retention ayrı roadmap
kapsamıdır. Image cache retention ve controller crash orphan reconciliation hâlâ yoktur.

## Değişen dosyalar

- `.github/workflows/ci.yml`
- `MANIFEST.in`
- `README.md`
- `ROADMAP.md`
- `docs/phase3-verification.md`
- `examples/custom_connector.py`
- `examples/phase3_agent.py`
- `examples/phase3_plan.json`
- `pyproject.toml`
- `scripts/check_wheel.py`
- `src/agentguard/cli/main.py`
- `src/agentguard/contracts/compatibility.py`
- `src/agentguard/contracts/results.py`
- `src/agentguard/core/outcome.py`
- `src/agentguard/core/result.py`
- `src/agentguard/environments/__init__.py`
- `src/agentguard/environments/adapters.py`
- `src/agentguard/execution/controlled_runtime.py`
- `src/agentguard/execution/errors.py`
- `src/agentguard/execution/records.py`
- `src/agentguard/execution/transaction.py`
- `src/agentguard/integrations/__init__.py`
- `src/agentguard/integrations/agents.py`
- `src/agentguard/integrations/evidence.py`
- `src/agentguard/integrations/http.py`
- `src/agentguard/integrations/plan.py`
- `src/agentguard/integrations/schema.py`
- `src/agentguard/integrations/sdk.py`
- `src/agentguard/integrations/tools.py`
- `src/agentguard/orchestration/controller.py`
- `src/agentguard/reporting/export.py`
- `src/agentguard/sdk.py`
- `tests/conftest.py`
- `tests/phase3_service.py`
- `tests/test_phase0_compatibility.py`
- `tests/test_phase3_docker.py`
- `tests/test_phase3_environments.py`
- `tests/test_phase3_integration.py`
- `tests/test_phase3_security.py`

## Doğrulanmış CI ve kapanış

- Dal: `codex/development`; mevcut checkout kullanıldı, yeni repo kopyası yoktur.
- Doğrulanmış Phase 2 atası: `37e3638dc6c65681df332251eb912887cac424c7`.
- Phase 3 kaynak commit'i: `ea2f0e567fd684551ae5d7f5e4077dbfce3478b3`.
- [GitHub Actions koşusu 42854247327](https://github.com/EgeBasturmen/agentguard/actions/runs/42854247327):
  **completed/success, 13/13 job başarılı**; başarısız job yok.

| CI grubu | Sonuç | Kanıt |
| --- | --- | --- |
| workflow-lint | Başarılı | actionlint |
| Ubuntu 24.04 × Python 3.10–3.14 | 5/5 başarılı | Her hücrede 295 unit + 168 integration; isolated sdist→wheel ve temiz runtime kurulum |
| Windows 2022 × Python 3.10–3.14 | 5/5 başarılı | Her hücrede 295 unit + 168 integration; isolated sdist→wheel ve temiz runtime kurulum |
| docker | Başarılı | 29 gerçek Docker testi; temiz wheel'den Phase 2 yürütmesi + Phase 3 integration CLI/filesystem agent ve rapor roundtrip |
| Phase 1 gate (korunan mevcut job adı) | Başarılı | Bütün lint/matrix/Docker işlerinin başarısını zorunlu kılar |

11 artifact indirildi ve içerikleri kontrol edildi. 21 JUnit XML kaydında failure/error/
skip sıfır. 11 wheel-check JSON kaydı runtime-only, checkout dışı console CLI ve verdict
parity doğruladı; Docker kaydında `controlled_docker=true` ve `phase3_integration=true`.
Kaynak dağıtımında yeni test helper/conftest bulunması temiz paket kontrolünün parçasıdır.

Main GitHub referansı `d5673ea29aa4ec63938d33b16a46ef714191e23a`, Phase 2 referansı
`37e3638dc6c65681df332251eb912887cac424c7` olarak kaldı. Özgün HEAD/branch/index/status,
kullanıcı dosyaları ve bu checkout'taki mevcut demo raporlarının hash kontrolleri başarılı.
Hassas dosya/anahtar taraması yapıldı; test ortamı, model/image cache, indirilen CI ZIP'leri
ve yerel test artifact'leri Git'e eklenmedi. Source commit 39 gerekli dosyayı içerir.

Kalıcı geliştirme bu aynı checkout ve `codex/development` dalından devam edebilir.
Kapanış kaydı gerçek CI başarısından sonra ayrı docs commit'iyle yayımlanır; bu doküman
bağımsız bir pentest veya genel güvenlik sertifikası değildir. Destek profili dışındaki
özellikler yukarıda açıkça sınırlanmıştır. Phase 4 için kullanıcı onayı beklenir.

## Son şema güvenliği regresyonu

Kapanış sonrası ek sınır kontrolünde nested `$id` ile yerel `$ref` scope'unun farklı
bir schema resource'a geçebilmesi yeniden üretildi: referans Draft202012Validator'un
reddettiği veri, eski flatten işlemi tarafından kabul edilebiliyordu. Önce başarısız
test yazıldı. Resource-scoped `$id`/anchor/vocabulary profili ve bilinmeyen `$schema`
dialect'i artık açıkça reddedilir. Typed report envelope da tool şemalarını aynı
sınırlarla yeniden doğrular; önceden sealed bir hash bu reddi geçersiz kılamaz.

Dört yeni red→green regresyon case’i eklendi (resource scope, dialect ve iki sealed-report şema durumu). Bozuk JSON Schema hatası da validation error olarak normalize edilir; gate çökmesi yerine reddedilir. Son kaynak için tam test, temiz wheel ve
GitHub CI doğrulaması yeniden çalıştırılıyor; nihai kapanış bu son commit'in kanıtını
bekler. Önceki CI tablosu kendi kaynak commit'inin tarihsel kanıtıdır.

Son şema düzeltmesiyle yerel tam koşu: **496 passed**, 135.72 saniye;
299 unit, 168 integration, 29 gerçek Docker; failure/error/skip yok. Temiz wheel
runtime-only kurulumundan kontrollü execution, Phase 3 integration CLI ve rapor
roundtrip yeniden başarılı. Phase 0–2'nin 448 testi korunur; toplam 48 yeni Phase 3 case'i vardır.
