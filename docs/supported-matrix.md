# Destek ve uyumluluk matrisi — Phase 0

Contract sürümü: 1. Mevcut paket: 0.1.0. Aşağıdaki adapter sürümleri contract
profil sürümleridir. `supported_matrix()` taze, typed profil nesneleri döndürür;
bir caller'ın değişikliği sonraki kontrolleri etkilemez.

| Component | Adapter ID | Version / protocol | Mevcut implementasyon |
|---|---|---|---|
| Core | scenario.core | 1.0.0 / 1 | Mevcut ScenarioRunner ve strict Scenario; observed local |
| Agent | python.local | 1.0.0 / 1 | Önceden oluşturulmuş güvenilen Python `run(task, sandbox)` nesnesi |
| Tool | generic.function | 1.0.0 / 1 | GenericSandbox'ta güvenilen, manuel kayıtlı simulator handler |
| Environment | generic.memory | 1.0.0 / 1 | Bellek içi filesystem/http/dataset/custom fixture ve snapshot |
| Evaluator | builtin.state | 1.0.0 / 1 | Expected/forbidden state, tool count, mevcut legacy invariant |
| Agent | http.agent | 0.0.0 / 1 | Yalnızca gelecek contract profili; çalıştırılamaz |
| Agent | container.agent | 0.0.0 / 1 | Yalnızca gelecek contract profili; Docker handshake agent adapter değildir |
| Tool | openapi.tool | 0.0.0 / 1 | Yalnızca gelecek contract profili; discovery/routing yok |
| Tool | mcp.tool | 0.0.0 / 1 | Yalnızca gelecek contract profili; discovery/routing yok |

Uygulanmış profiller **sadece observed + simulator** kombinasyonunu destekler.
Filesystem/HTTP/dataset tür adları simüle verinin anlamını belirtir; gerçek OS,
HTTP server veya versioned dataset connector garantisi vermez. Legacy CLI'ın
CommerceSandbox, CRMSandbox, FileSandbox ve özel trusted sandbox yolları korunur;
bu katalog bunların yeniden implementasyonu değil, yeni sürümlü tanımların
local bridge üzerinden destek kontrolüdür.

| Yetenek | Yerel core/adapter yolu | Test/garanti sınırı |
|---|---|---|
| Tool observation | Var | Sadece instrumented trace; agent değiştirebilir, complete interception değil |
| State snapshot | Var | Aynı process'teki simülatör state'i; hostile agent'a karşı authoritative değil |
| Tool interception / isolation / authoritative evidence | Yok | Controlled runtime AG-020–025 |
| Reset/session/memory reset | Yok | Yeni local sandbox her attempt'te oluşur; agent/session memory reset sözleşmesi uygulanmış değildir |
| Streaming/cancellation | Yok | AG-023/024 |
| Model usage/budget | Yok | Örnekler modele bağlanmaz; AG-050 |
| Direct output evaluation | Yok | Runner dönüş metnini değerlendirmez; AG-051 |
| JSON Schema validation / resource policy | Deklarasyon | Registry'de mevcut Pydantic şemalar korunur; yeni ToolDefinition'dan enforcement/discovery üretilmez |
| Staging/replay/mock routing | Yok | AG-032 |
| Browser/multi-agent/delayed effects | Yok | AG-080–084 |

`builtin.state@1.0.0`, mevcut comparison manifest'indeki `state-evaluators-v1`
semantiğine karşılık gelir. Evaluator davranışı değiştiğinde sürüm/manifest
kimliği birlikte güncellenmelidir. Unknown evaluator/plugin controller'da import
edilmez veya otomatik desteklenmiş sayılmaz.

## Platform doğrulaması

- Python gereksinimi pyproject'te `>=3.10`; CI hedefi Linux/Windows Python
  3.10–3.14'tür. Workflow hedefi ile gerçekten çalıştırılan kontrollerin kapsamı
  [phase1-verification.md](phase1-verification.md) içinde ayrı kaydedilir; workflow
  dosyasının varlığı tüm platformlarda başarılı koşu kanıtı sayılmaz.
- Docker testleri Linux worker handshake/lifecycle testleridir; gerçek agent
  izolasyonu, Linux host'ta tüm CLI matrisi veya hostile multi-tenancy testi değildir.
- HTTP, container, OpenAPI ve MCP profilinin modelde ifade edilebilmesi destek
  anlamına gelmez. `assess_compatibility` bunları ve bilinmeyen version/protocol
  kombinasyonlarını açıkça reddeder.

Kullanım ve kabul örnekleri: [phase0-contracts.md](phase0-contracts.md).
