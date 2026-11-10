# Phase 0 — genel sözleşmeler ve kapsam

Bu teslim AG-001–005 içindir. Contract taslağının tamamlanması, sonraki
fazlardaki connector, environment veya güvenli runtime implementasyonunun
tamamlandığı anlamına gelmez.

## Başlangıç incelemesi ve korunan bileşenler

| Görev | İnceleme öncesi durum | Eksik parça / bu teslim |
|---|---|---|
| AG-001 | Kısmi | `Scenario`, AgentProtocol, SandboxFactory ve registry vardı. Bunlar korunup AgentSpec/ToolDefinition/EnvironmentSpec/ScenarioVersion zarfları eklendi. |
| AG-002 | Kısmi | RunResult execution/evaluation/cleanup ve ortak gate vardı. Mode, capability ve çok boyutlu sonuç görünümü eklendi. |
| AG-003 | Kısmi | Threat model hedef izolasyonu mevcut davranış gibi anlatıyordu. Gerçek local/IPC/Docker akışı ve gelecekteki hosted/private/managed sınırları ayrıldı. |
| AG-004 | Kısmi | Mevcut file/document ve commerce/CRM örnekleri korundu. Aynı contract ile üç genel capability örneği ve iyi/kötü varyantları eklendi. |
| AG-005 | Yok | Sürüm/protokol pinli core/adapter/evaluator kataloğu ve açık destek matrisi eklendi. |

AG-010–013 doğrulama, path/evaluator ve comparison çalışmaları tekrar yazılmadı.

## Sözleşme sürümü 1

`agentguard.contracts.models` strict Pydantic modelleri sunar. Bilinmeyen alanlar,
geçersiz sürüm/tip, duplicate referanslar, tanımsız tool expectation ve fixture
çelişkileri reddedilir. `contract_version` tam sayı `1` olmalıdır. Adapter
kimliği, `version` ve `protocol_version` ayrı alanlardır; paket sürümü 0.1.0 ile
contract/adapter sürümleri birbirinin yerine kullanılmaz.

| Tanım | Temel alanlar | Sınır |
|---|---|---|
| AgentSpec | ID/version, adapter/version/protocol, transport, entrypoint, execution_mode, credential_refs | Sadece veri. URI'ya bağlanmaz, Python modülü import etmez. |
| ToolDefinition | Stable ID/version/name, description, input/output schema, adapter, effect, resource scopes, test mode, timeout, idempotency, credential_ref | Şema belgesi saklanır; JSON Schema metavalidasyonu, çağrı routing ve business semantics implementasyonu değildir. Unknown effect güvenli varsayılmaz. |
| EnvironmentSpec | ID/version, adapter, kind, test mode, namespace, fixture | Gerçek provision/reset/cleanup lifecycle'ı AG-033/034'tedir. |
| ScenarioVersion | ID/version/provenance, agent/environment/tools, actor/session, permissions/rules/messages, limits, severity/tags/lifecycle, evaluator ve required_capabilities | Mevcut Scenario ve evaluator'ları kullanır. Lifecycle alanı approval/version store implementasyonu değildir. |

Geçiş için fixture hem EnvironmentSpec hem mevcut Scenario içinde bulunur;
değerleri tip duyarlı olarak eşit olmak zorundadır. Yeni core engine veya
sektörel alan eklenmez. Beklenen/yasak state etkileri, tool beklentileri ve mevcut
invariant'lar aynı Scenario'da kalır. Output/judge/temporal evaluator eklenmez.

Tüm metadata JSON roundtrip ve `model_json_schema()` ile dışa aktarılabilir.
Credential alanları `secret://…`, `env://…`, `credential://…` referanslarını tutar;
bu fazda credential çözümlemesi yapılmaz. Fixture/oracle içine secret koymayın:
mevcut raporlar redaction sağlamaz.

## Capability, mode ve sonuç

Capabilities varsayılan olarak false'tur: tool observation/interception, state
snapshot, reset, streaming, cancellation, model usage, output, isolation,
authoritative evidence, sessions ve memory reset. Destek, agent'ın kendi
beyanından çıkarılmaz; `supported_matrix()` içindeki uygulama profilleri kullanılır.

Controlled context izolasyon + tool interception + authoritative evidence
birlikteliğini gerektirir. Yapısal olarak geçerli controlled bir tanım bile
mevcut runtime tarafından desteklenmez. Local bridge controlled/unsupported
isteğini agent veya sandbox çalıştırmadan reddeder; local fallback yapmaz.

Mevcut ScenarioRunner `execution_context` içinde observed mod, gerçek local
adapter kimliği, mevcut observation/snapshot yetenekleri ve açık limitations
kaydeder. HTML/CLI assurance gösterir; JSON context'i taşır. Context'i olmayan
eski sonuç `unknown` kalır. Uyumsuz context'ler karşılaştırmada eşdeğer sayılmaz;
desteklenmeyen mode/capability iddiası ortak PASS kontrolünden geçmez.

`summarize_result(result)` mevcut gate'i kullanarak execution_status,
behavior_status, policy_status, cleanup_status, evaluation_completeness,
label/gate_decision, failure_reasons ve coverage_gaps üretir. Local policy
enforcement doğrulanmadığından policy_status **unknown** kalır. Unsupported
contract `SKIPPED/not_started/BLOCK`, evaluator hatası ERROR, davranış hatası
FAIL olarak ayrılır. Gelecekteki cancelled/timed_out/partial/policy status'ları
sözleşmede ifade edilebilir; mevcut runtime bunları uyguluyor iddiası yoktur.

## Kabul kapısı: üç farklı capability, aynı core

```console
python -m examples.phase0
```

| Örnek | Capability ve bağımsız fixture oracle'ı | Gerçek çalışma sınırı |
|---|---|---|
| file-edit | `note.txt` değişir; `protected.txt` değişmez; read/write sayısı | Bellek içi workspace; terminal/shell veya host filesystem işlemi yok. |
| research-retrieval | Retry limit fixture'dan alınır; cevap ve source ID doğrulanır; dataset değişmez | Deterministic retrieval; LLM, embedding, canlı kaynak ve groundedness evaluator yok. |
| api-update | item-1 güncellenir, item-2 korunur; get/patch sayısı | Bellek içi HTTP/resource semantiği; gerçek network/staging yok. |

Üçü de aynı `ScenarioVersion → run_local_definition → ScenarioRunner →
GenericSandbox → mevcut evaluator/gate` yolunu kullanır. Yeni domain-specific
runner yoktur. Good/bad varyantları, her attempt'te temiz state, manifest/rapor
roundtrip ve capability reddi testlidir. Eski refund/CRM/dosya/doküman örnekleri
korunur.

`run_local_definition` yalnızca güvenilen, önceden oluşturulmuş agent ve
sandbox factory nesnesi alır. Entrypoint çözümleyici/worker launcher değildir.
Tanımdaki tools/permissions ile handler kurulumu örnekteki ortak factory'nin
sorumluluğudur. Şema, scope, timeout/call cap deklarasyonları engine tarafından
zorunlu uygulanmaz; limitations içinde görünür. Budget, messages/session,
credential ve versioned rule çözümlemesi istenirse unsupported döner.
Bu adaptör kontrolü AG-035'in tam connector preflight uygulaması değildir.

Karşılaştırma kimliğine envelope tool/evaluator/permission/capability sürümleri
katılır. Candidate agent artifact/version değişikliği test sözleşmesi
değişikliği sayılmaz. Fingerprint source imzası değildir.

## Uyumluluk politikası

Makine tarafından okunabilir kaynak `contracts/compatibility.py::supported_matrix`;
insan tarafından okunabilir kapsam [supported-matrix.md](supported-matrix.md).
Eşleştirme adapter ID + version + protocol + component üzerinden tam eşleşmedir.
Bilinmeyen sürüm sessizce en yakın adaptöre düşmez. Required capability eksikse
desteklenmiş sayılmaz. Değişiklikler compatible ilan edilmeden contract/örnek ve
mevcut regression testleri çalıştırılmalıdır.

Güven sınırları ve kalan açıklar [threat-model.md](threat-model.md) içindedir.
Phase 2 izolasyon, Phase 3 gerçek connector/environment, Phase 4 lifecycle ve
Phase 5 geniş evaluator çalışmaları bu teslimin kapsamına alınmamıştır.
