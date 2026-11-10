# Phase 2: izole yürütme ve otoritatif evidence

`codex/phase2-execution` dalı doğrulanmış Phase 1 commit'i
`e5d5cae25587f71067974a4e37f4da1199d69e4e` üzerinden ayrılmış kopyada oluşturuldu.
Özgün dizin ile doğrulanmış Phase 1 kaynak allowlist'i karşılaştırıldı: yalnızca
ROADMAP ve Phase 1 doğrulama belgesinin CI kapanış kayıtları farklıydı.
Özgün main, HEAD, index ve kullanıcı dosyaları geliştirme dışında tutuldu.

## Yürütme yolu ve API

CLI → `RunController.run` → güvenilen state view/relay → mevcut `ScenarioRunner`
(snapshot/diff/evaluator) → `ControlledDockerRuntime` → worker-side import →
`ProcessIPC` → `IPCDispatcher` → `ToolBroker` → killable transactional simulator.
İkinci evaluator/gate motoru eklenmedi. `IsolatedExecutionBackend` aynı controller'a
bağlanır; canlı host agent nesnelerini reddeder. Eski observed runner ve v1
handshake/IPC API'ları korunur.

```python
from pathlib import Path
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.orchestration.controller import RunController
from agentguard.spec.loader import load_scenario

if __name__ == "__main__":
    result = RunController().run(
        load_scenario("scenarios/refund_order.yaml"),
        AgentLaunchSpec(reference="examples.refund_agent:GoodRefundAgent", source_dir=Path(".")),
        environment="commerce",
    )
```

Spawn process kullanan scriptlerde `if __name__ == "__main__"` koruması gerekir.
`environment="files"` aynı yolu kullanır. Domain mantığı `controlled_tools.py`
içindedir; controller domain bağımsızdır. Özel registry yalnızca güvenilen,
module-level/pickle edilebilir `handler(state, **arguments)` fonksiyonları ve
`stateful=True` kayıtlarıyla, açık policy ve sürümlü environment kimliğiyle kullanılır.
Bu deklarasyon handler'a OS güvenlik sınırı kazandırmaz.

```json
{
  "allowed_tools": ["file.read", "file.write"],
  "resource_scopes": {"file.write": {"filename": ["note"]}},
  "timeout_seconds": 30,
  "tool_timeout_seconds": 5,
  "max_tool_calls": 100,
  "max_stdout_bytes": 262144,
  "max_stderr_bytes": 16384
}
```

## Desteklenen garantiler

- Agent yalnızca container'da import edilir. Host'ta kullanıcı Dockerfile'ı,
  setup hook'u, dependency installer veya canlı agent nesnesi çalıştırılmaz.
- Build context explicit `.py` allowlist'idir. `.env`, credential dosyaları veya
  kaynak ağacı topluca aktarılmaz. Path escape, symlink/hardlink ve boyut aşımı reddedilir.
- Build digest'e pinlenmiş Python base üstünde yalnızca COPY/ENV/USER/ENTRYPOINT
  kullanır; build network none'dır. Image id, source/worker hash, base digest,
  protocol version ve container id raporda tutulur. Ek dependency listesi boştur.
- Worker non-root, network none, read-only root, no-new-privileges, cap-drop ALL,
  PID/memory/CPU limitleri ve noexec/nosuid/nodev tmpfs ile çalışır. Host volume,
  Docker socket, credential veya host environment aktarılmaz.
- Worker'a task/reference/run id gönderilir; oracle, kurallar, tam state ve
  controller trace nesnesi gönderilmez. Tool sonuçları izinli handler'ın döndürdüğü veridir.
- Broker izin, schema, toplam deneme sayısı ve tam eşleşmeli argüman scope kontrolü yapar.
  Güvenilen simulator ayrı spawn process'te state kopyası üzerinde çalışır.
  Süre/iptal/hata/yasak etki halinde canonical state commit edilmez.
- Audit controller tarafındadır. Başarılı/reddedilmiş/hatalı çağrı ve önerilen mutation
  kaydedilir. Agent hatayı yakalasa da policy ihlali veya tool hatası kalıcıdır.
- Sync/async lifecycle, run id/order, explicit completion ve sıfır exit birlikte gerekir.
  Mesaj, toplam stdout/stderr, tool sonucu/state boyutları sınırlıdır. Yinelenen JSON
  anahtarı, non-finite sayı ve belirsiz protocol version reddedilir.
- Monotonic run deadline build'i de kapsar. Tool deadline kalan run süresiyle sınırlıdır.
  İptal build, IPC okuma/yazma, tool ve süreç kapanışında kontrol edilir. Cleanup rm+inspect
  ile doğrulanır; belirsizlik BLOCK'tur. Geç create/build belirsizliği başarı sayılmaz.
- Controlled PASS için tamamlanmış runtime evidence, başarılı policy/audit, verified
  cleanup ve mevcut tüm evaluator/gate koşulları birlikte zorunludur.

## Doğrulama

İlk başarısız testler controller/launch spec ve stateful registry eksikliğini ve
varsayılan CLI'ın host loader çağırdığını yeniden üretti. İlk gerçek Docker denemesi
`python -I` import yolu sorununu gösterdi; yalnızca sabit `/opt/guard` eklenerek giderildi.
Mock sonuçlar gerçek Docker kanıtı sayılmadı. Son schema incelemesinde `true` ve
`"1.0"` sayısal argümanlarının örtük dönüşümü iki başarısız testle doğrulandı;
controlled broker strict validation ile kapatıldı. Eski yerel broker davranışı korundu.

10 Kasım 2026, Windows Python 3.11.9 + Docker Engine 29.1.3 Linux container:

- `python -m pytest -q --junitxml=artifacts/phase2-tests.xml`: **448 passed**, 104.37 sn;
  278 unit, 152 integration, 18 gerçek Docker; failure/error/skip yok.
- `python -m build --outdir artifacts/phase2-dist`: isolated sdist→wheel başarılı;
  build ortamında setuptools 84.2.1. Kaynak çalışma alanına editable install yapılmadı.
- `python scripts/check_wheel.py artifacts/phase2-dist --docker --result artifacts/phase2-wheel-check.json`:
  runtime-only temiz venv, checkout dışı gerçek console CLI, modül/worker kaynaklarının
  wheel'de bulunması, YAML/HTML/JSON verdict eşitliği ve gerçek async Docker agent başarılı.
- `rhysd/actionlint:1.7.12`: güncellenen workflow için exit 0.
- `git diff --check`: başarılı.
- Özgün repo: HEAD, branch, index SHA-256, porcelain status ve değişmiş/untracked
  kullanıcı dosyalarının hash'leri başlangıç snapshot'ıyla aynı (tümü true).

E2E kontrolleri non-root/read-only/network/socket/env sınırlarını gerçek agent içinde;
network/cap/PID/memory/CPU/user/bind yapılandırmasını daemon inspect ile; kaldırılan
container'ı tekrar inspect ederek doğrular. State/trace forgery, caught forbidden/scope
ve unknown-tool ihlali, crash sonrası mutation evidence, os._exit(0), forged protocol,
stderr flood, timeout, cancellation ve cleanup doğrulama hatası PASS alamaz.
10 ek gerçek subprocess testi eksik/yanlış/tekrarlanan lifecycle ve stdout kapanışından
sonra iptali doğrular; transactional tool testleri rollback ve geç mutation olmamasını denetler.

`artifacts/phase2-tests.xml`, `artifacts/phase2-wheel-check.json`,
`artifacts/phase2-build.log`, `artifacts/phase2-preservation.json` yerel kanıtlardır;
Git'e eklenmezler. Kaynak mevcut `codex/phase2-execution` dalından GitHub’a commit/push edildi;
main veya özgün çalışma ağacı/index üzerinde commit/push/merge yapılmadı.

CI'ın ayrık unit/integration ve gerçek Docker job'ı yeni testleri de toplar.
Daemon/image eksikliği skip değildir. Bu dalın kendi GitHub Actions ve artifact kanıtları aşağıdadır; Phase 1'in
uzak matrix sonucu Phase 2 sonucu yerine kullanılmadı.

## Sınırlar ve kalan riskler

Docker kernel/daemon'ı güvenilen tabandır; kernel escape ve düşmanca çok kiracılı
izolasyon garantisi yoktur. Worker completion/output'unu taklit edebilir; bununla
controller state/trace/PASS üretemez. Output semantik doğruluğu Phase 5 kapsamındadır.
Worker içi dosya/syscall etkileri simulator evidence kapsamına girmez.

Tool handler'ları güvenilen kod olmalıdır. Resource scope argüman eşleşmesini
sınırlar; özel handler'ın host filesystem/network etkilerini OS seviyesinde sandbox'lamaz.
Güvenlik kanıtı saf simulator'lar içindir; staging/replay/gerçek connector Phase 3'tedir.

Yalnızca standard-library Python agent'ları desteklenir. Üçüncü parti dependency
kurulumu, HTTP endpoint ve başka dil container contract'ı henüz yoktur. `.py`, task
ve izinli tool sonuçlarının içindeki hassas veri image/output/audit'te bulunabilir.

Build iptali daemon işleminin sonlandığını kanıtlamaz; unverified/BLOCK olur. Image
cache kalıcıdır; otomatik prune yapılmaz. Disk kotası/retention, controller crash sonrası
orphan reconciliation ve uzak iptal/session yönetimi yoktur. Tool çağrıları sıralıdır.
Özel handler semantik sürümünün environment id ile değiştirilmesi operatörün
sorumluluğudur. Raporlar kriptografik imzalı değildir; dışarıdan değiştirilen raporlar
için authenticity garantisi verilmez. Faz 3 başlatılmadı.

## Değişen dosyalar

- `.github/workflows/ci.yml`
- `README.md`
- `ROADMAP.md`
- `docs/phase2-verification.md`
- `scripts/check_wheel.py`
- `src/agentguard/cli/main.py`
- `src/agentguard/contracts/compatibility.py`
- `src/agentguard/contracts/results.py`
- `src/agentguard/core/outcome.py`
- `src/agentguard/core/result.py`
- `src/agentguard/execution/agent_spec.py`
- `src/agentguard/execution/controlled_runtime.py`
- `src/agentguard/execution/controlled_tools.py`
- `src/agentguard/execution/controlled_worker.py`
- `src/agentguard/execution/errors.py`
- `src/agentguard/execution/ipc_protocol.py`
- `src/agentguard/execution/isolated.py`
- `src/agentguard/execution/lifecycle_protocol.py`
- `src/agentguard/execution/process_ipc.py`
- `src/agentguard/execution/records.py`
- `src/agentguard/execution/tool_broker.py`
- `src/agentguard/execution/tool_registry.py`
- `src/agentguard/execution/transaction.py`
- `src/agentguard/execution/worker_client.py`
- `src/agentguard/orchestration/__init__.py`
- `src/agentguard/orchestration/controller.py`
- `src/agentguard/reporting/export.py`
- `src/agentguard/security/policy.py`
- `tests/conftest.py`
- `tests/test_cli.py`
- `tests/test_comparison_cli.py`
- `tests/test_controlled_contracts.py`
- `tests/test_controlled_execution.py`
- `tests/test_controlled_ipc.py`
- `tests/test_controlled_policy.py`
- `tests/test_evaluation_errors.py`
- `tests/test_execution_security.py`
- `tests/test_phase0_compatibility.py`

## GitHub kapanış denetimi

Kaynak mevcut checkout/daldan yayımlandı; yeni bağımsız proje kopyası oluşturulmadı.
Son denetimde sıfır exit sonrası tamamlanmamış stderr drain'inin başarı sayılabildiği
gerçek subprocess regresyon testiyle gösterildi. IPC artık stderr tamamen okunmadan
başarı vermez; drain deadline ve cancellation kapsamında kalır. İlgili 10 IPC testi geçti.
Host import, broker bypass, izin/resource scope, state/trace forgery ve false-PASS
kontrolleri mevcut gerçek Docker testleriyle yeniden çalıştırıldı.
Son yerel koşu: 448 passed, failure/error/skip yok; temiz wheel + paketlenmiş async
Docker agent ve JSON/HTML karşılaştırması başarılı. Nihai Phase 2 kapanışı yalnızca
aşağıdaki uzak CI ve artifact doğrulaması başarıyla tamamlandıktan sonra işlendi.

Geliştirme düzeni önerisi: Bu checkout tek aktif geliştirme alanı olarak kullanılmalı.
Phase 2 sonrasında kullanıcı onayıyla aynı checkout'ta kalıcı `codex/development` dalı
oluşturulabilir; sonraki aşamalar yeni proje kopyaları gerektirmez. Özgün çalışma ağacındaki
kullanıcı değişiklikleri otomatik taşınmaz veya birleştirilmez. Şu anda dal oluşturma,
taşıma, merge veya özgün main/index değişikliği yapılmadı.

## Doğrulanmış GitHub CI kapanışı

- Dal: `codex/phase2-execution`.
- Doğrulanmış Phase 1 atası: `e5d5cae25587f71067974a4e37f4da1199d69e4e`;
  GitHub Phase 1 referansı da aynı. Kaynak aktarımı/merge gerekmedi.
- Phase 2 kaynak commit'i: `d55405d360bade0955c2f83fa798ae9f4a326088`.
- [GitHub Actions koşusu 42834398786](https://github.com/EgeBasturmen/agentguard/actions/runs/42834398786):
  **completed/success; 13/13 job başarılı**.
- GitHub `main`: `d5673ea29aa4ec63938d33b16a46ef714191e23a`; değiştirilmedi.

| Job grubu | Sonuç | Doğrulanan kanıt |
| --- | --- | --- |
| workflow-lint | Başarılı | actionlint |
| Ubuntu 24.04 × Python 3.10, 3.11, 3.12, 3.13, 3.14 | 5/5 başarılı | Her hücrede 278 unit + 152 integration; isolated sdist→wheel; runtime-only temiz kurulum/CLI |
| Windows 2022 × Python 3.10, 3.11, 3.12, 3.13, 3.14 | 5/5 başarılı | Her hücrede 278 unit + 152 integration; isolated sdist→wheel; runtime-only temiz kurulum/CLI |
| docker | Başarılı | 18 gerçek Docker testi; temiz wheel içinden gerçek async agent, broker audit, verified cleanup ve rapor roundtrip |
| Phase 1 gate (korunan mevcut CI job adı) | Başarılı | Tüm matrix, lint ve Docker işlerinin başarılı olması zorunlu |

11 artifact indirildi. 21 JUnit XML kaydında failure/error/skip sıfır; 11 wheel-check
kaydı runtime-only, checkout dışı CLI ve verdict parity başarısını doğruladı.
Docker artifact'inin `controlled_docker=true` kaydı ayrıca kontrol edildi.
İndirilen ZIP ve doğrulama JSON'ları yalnızca ignored yerel evidence dizinindedir;
credential/token, image cache veya test artifact'i Git'e eklenmedi.

Bu kapanış kaydı kaynak CI başarıyla tamamlandıktan sonra ayrı dokümantasyon commit'i
olarak yayımlanır. Phase 3 kullanıcı onayını bekler. Bu doğrulama bir bağımsız pentest
veya Docker kernel escape garantisi değildir; yukarıdaki destek sınırları geçerlidir.
