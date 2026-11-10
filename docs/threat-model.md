# AgentGuard tehdit modeli

Güncelleme: 10 Kasım 2026 — Phase 0 / AG-003.

## Mevcut kapsam ve güven varsayımı

Mevcut ürün tek organizasyonda güvenilen yerel Python koduyla kullanılan SDK/CLI
prototipidir. `test`, `regression` ve örnekler agent kodunu controller ile aynı
process içinde çalıştırır. Bu akış **observed** moddur; güvenilmeyen kod izolasyonu
veya bağımsız davranış doğrulaması sağlamaz. SecurityPolicy varsayılanının isolated
olması CLI'ın gerçekten isolated çalıştığı anlamına gelmez.

Controlled modun sözleşmesi tanımlıdır fakat agent runtime implementasyonu yoktur.
IsolatedExecutionBackend çalışmayı reddeder. Docker worker yalnızca senaryo adı
taşıyan isteğe ready cevabı verir. Worker'da agent import/çalıştırma veya CLI'a
bağlı ToolBroker akışı yoktur. Docker smoke testi izole agent execution testi değildir.

Hosted management, private runner enrollment ve managed multi-tenant execution
uygulanmamıştır. Yerel veya müşteri kontrolündeki runner raporu bağımsız attestation
değildir. Bu belge bir güvenlik sertifikası değildir.

## Varlıklar ve gerçek güven sınırları

Korunacak varlıklar: host dosyaları/environment/secrets, fixture/oracle'lar, tool
state'i, rapor/baseline bütünlüğü ve compute bütçesi.

| Akış | Gerçek sınır | Kanıt ve sınır |
|---|---|---|
| CLI → agent/sandbox | OS/process sınırı yok | cli/main.py, adapters/python.py ve adapters/sandbox.py host import/constructor çalıştırır. Sadece güvenilen kod. |
| Runner → agent/sandbox | Aynı process | runner/runner.py, sandbox/generic.py, tracing/collector.py: state, registry ve trace agent'tan korunmaz. |
| ProcessIPC → dispatcher → broker | Process pipe'ı, controller registry/state'i | Subprocess testleri var; CLI/Docker agent yoluna bağlı değil. Handler'lar güvenilen controller kodudur. |
| DockerRuntime → container_worker | Kısıtlı Linux container | Non-root, network none, read-only FS, cap drop, PID/CPU/memory/output limitleri. Yalnızca handshake. |
| Dosya → rapor/comparator | Yapısal doğrulama; kaynak kimlik doğrulaması yok | reporting/models.py, core/manifest.py, core/outcome.py: strict tip, boyut, fingerprint/verdict kontrolü; imza yok. |
| Phase 0 definition → local bridge | Deklarasyon kontrolü; sandbox sınırı değil | contracts/models.py, compatibility.py ve local.py: entrypoint veri kalır; caller güvenilen agent/factory nesnesini sağlar. |

Controller, evaluator, adapter implementasyonu, bağımlılıklar ve seçilen
fixture/oracle yerel geliştirmede güvenilen bileşenlerdir. Senaryo ve rapor
belgeleri yine de yapısal doğrulamadan geçirilir. Özel handler'ı host'ta çalıştırmak
onu güvenli yapmaz. Güvenilmeyen agent/dependency/build/custom evaluator gelecekte
ayrı execution sınırlarında çalışmalıdır.

## Tehdit kaydı

| Tehdit | Mevcut kontrol | Kalan açık / takip |
|---|---|---|
| Import/constructor ile host kod çalıştırma | Spec doğrulaması import yapmaz; local bridge controlled isteğini çalıştırmadan reddeder | Eski CLI host import eder. Worker-side loading ve RunController AG-020/021. |
| State/trace/PASS uydurma | Strict result/verdict, manifest kapsamı ve controller evaluator | Aynı process'teki kötü niyetli agent state/trace/controller'ı değiştirebilir. Authoritative evidence AG-020–024. |
| Boş/bozuk oracle ile yanlış PASS | Strict Scenario, kontrol/çelişki/duplicate ve evaluation tamlığı kontrolleri | Oracle iş doğruluğu bağımsız fixture/insan doğrulaması gerektirir; AG-042/043. |
| Parent/root/list kontrolünü atlatma | Ortak path, tip duyarlı deterministic diff ve regresyon testleri | Geçici/gecikmiş etki final snapshot ile kanıtlanmaz; journal/temporal AG-022/051/083. |
| Rapor/verdict oynama, kontrol silme | Typed/bounded loader, duplicate JSON reddi, stable IDs/fingerprint, tek gate | Hash yeniden hesaplanabilir; kaynak/agent dürüstlüğünü kanıtlamaz. Auth/audit/signing ayrı işlerdir. |
| Capability/mode downgrade, unsupported protocol | Exact version/protocol matrisi; eksik capability/controlled local isteği BLOCK; unsupported context PASS olmaz | Deklarasyon runtime enforcement değildir. Tam connector preflight AG-035. |
| Tool/resource yetki ihlali | Broker schema/registry/call limit ve GenericSandbox allowlist | CLI broker kullanmaz; handler/state doğrudan erişilebilir. Actor/resource policy ve audit AG-022. |
| Network/host/secrets erişimi | Docker handshake kısıtları; credential reference alanları; örnekler offline | Local agent host'a erişebilir. Gerçek egress/secrets izolasyonu AG-020–025/050. |
| IPC forgery/replay/oversized veri | Typed mesaj, duplicate call ID, framing/byte/message/stderr sınırları | Explicit completion, tool deadline ve cancellation AG-021–024. |
| Sonsuz agent/tool, stdout flood | Docker/process timeout ve bounded output; prototip broker call limiti | Local run/tool handler deadline/budget zorunlu değil; AG-023/024. |
| Cleanup belirsizliği/orphan kaynak | Docker rm + inspect; unverified cleanup PASS olmaz | Restart, lease/reaper/reconciliation yok; AG-024/090. |
| Supply chain/build kodu | Runtime image'da pip install yok | Floating image/dependency, izole build ve digest/lock eksikleri AG-025/100. |
| Fixture/oracle/secret sızıntısı | HTML metni escape edilir; credential alanları reference tutar | Rapor/manifest fixture ve oracle içerebilir. Redaction/retention/export AG-063/073. |
| Cross-tenant erişim/hesap kötüye kullanımı | Hosted deployment yok | Auth, tenant authorization, quota/abuse kontrolleri AG-070–074/093/100. |
| SSRF/redirect/private-network erişimi | Spec URI doğrulaması bağlantı kurmaz | HTTP/OpenAPI/MCP connector için URL/egress threat review AG-030/031. |

## Modların güvence sözleşmesi

- Observed: yalnızca alınan trace/snapshot ve tanımlı kontroller ölçüsünde sonuç.
  Tool observation bütün tool'ların intercept edildiği anlamına gelmez. Policy
  bilinmiyorsa unknown kalır. Mevcut local runner bu moddadır.
- Controlled: izolasyon + broker tool interception + controller authoritative
  evidence birlikte gerekir. Bir capability eksikse context geçersizdir. Mevcut
  matrix controlled agent runtime sunmaz; spec'te mode değiştirmek backend kurmaz.
- Eksik evaluation, unsupported capability ve cleanup belirsizliği PASS değildir.
  contracts/results.py mevcut RunResult ve gate'ten sonuç boyutlarını türetir;
  yeni bağımsız gate oluşturmaz. Mode'u olmayan legacy sonuç unknown kalır.

## Gelecekteki dağıtım seçenekleri ve kapılar

1. Local: worker-side import ve gerçek isolation tamamlanana kadar sadece güvenilen
   agent/adapter/evaluator kodu.
2. Hosted management + private runner: auth/scopes, enrollment/revoke,
   tenant-scoped storage, artifact redaction, quota ve idempotent orchestration.
   Müşteri runner'ı ayrıca tehdit modelindedir; raporu bağımsız attestation değildir.
3. Managed hostile-code execution: izole build, secrets gateway, egress,
   cancellation, artifact symlink/path traversal savunması, tenant isolation,
   orphan recovery ve abuse testleri olmadan açılmaz. Docker tek başına hostile
   multi-tenancy için yeterli varsayılmaz; AG-100–103 ayrı güvenlik kapısıdır.

Gelecekte worker'a tüm ScenarioVersion/oracle gönderilmez; sadece gerekli task,
launch bilgisi ve tool client bağlantısı verilir. Canonical state, izinler, gizli
assertion'lar ve evaluator controller tarafında kalmalıdır.

## Kanıtlar ve bilinçli sınırlar

test_scenario_validation, test_evaluation_paths, test_evaluation_errors,
test_comparison_integrity, IPC/Docker lifecycle ve Phase 0 contract/compatibility/
result testleri mevcut kontrolleri doğrular. Üç Phase 0 örneği simüle dosya,
retrieval ve API yüzeyinde aynı core'u kullanır; gerçek shell/network/model veya
RAG groundedness evaluator'ı değildir.

Container escape, gerçek tenant isolation, private-runner enrollment, secret
exfiltration engelleme veya bağımsız security review tamamlandı iddiası yoktur.
Kritik runtime sınırları sonraki roadmap fazlarında kalır.
