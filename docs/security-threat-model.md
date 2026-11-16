# AgentGuard private-pilot threat model / Phase 9 review

17 Kasım 2026. Bu repository review ve test kaydıdır; bağımsız pentest veya güvenlik
sertifikası değildir. Shared-hostile hosting/SSO/managed workers Phase 10 kapsamıdır.

| Sınır / aktör | Beklenen savunma | Test / kalan sınır |
| --- | --- | --- |
| Untrusted agent → worker/controller | Container network-none/non-root/read-only, broker policy/scope; final state/PASS beyanı otorite değil | Phase 0–8 Docker testleri korunur. Host kernel ortak; VM isolation veya kötü niyetli Docker admin savunması yok. |
| Tenant/API key → başka proje | Güncel membership+scope, principal/key rate, grant auth, redacted artifacts | Cross-tenant, rotation/revoke/expiry, failed-operation rate ve metadata cap testleri. Public signup/OIDC/MFA yok. |
| İnternet → pilot HTTP/TLS | Loopback default, TLS edge/host/origin/CSP, body/thread/rate/absolute deadline | Slow-body ve stalled TLS handshake testleri. Public internet/volumetric DDoS ve HA kapasite garantisi yok. |
| Browser fixture → host/network | Sadece closed declarative DOM, sabit handler, tüm context network request’leri block, özel RPC key | SSRF/HTML/ID/auth/context ve parent-crash testleri. Chromium host-side trusted fixture; arbitrary hostile web sitesi çalıştırılmaz. |
| Controller/process ölümü | SQLite transaction + process lock, lease fencing, no redispatch, owned resource recovery | Gerçek process rollback/claim death, browser auto-cleanup ve exact dead-owner container reaping. Power loss/OS kill/remote daemon için genel garanti yok. |
| Backup corruption/tamper → restore | Key dışarıda HMAC manifest, hash/integrity/schema/size/path kontrolü, yeni hedef | Wrong key, traversal, migration rollback, stale/expired/deleted evidence testleri. Archive encrypted değil; operator storage/ACL/offsite key gerekli. |
| Silinmiş veri → eski snapshot | Ayrı signed latest deletion intent journal + applied checkpoint | Restart/restore primary silme uygulanır. Eski DB ve eski journal’ı birlikte seçen trusted operator’ı dışarıdan tespit eden transparency service yok. |
| Ledger/ödeme → yeni quota | Admission idempotent, eksik/negatif/orphan kayıt BLOCK, reserve tutulur; manual reference bir kez kullanılır | Budget revoke/cancel/reconcile testleri. Fatura/ödeme bağımsız attestation değil; privileged DB/key birlikte tamper kapsam dışı. |
| UI/veri → XSS/credential storage | textContent/CSP, session epoch fence, key yalnız bellek, auth grant | Gerçek Chromium XSS/late response/logout testleri korunur. Genel PII/DLP veya browser extension/malware savunması yok. |

Güvenilenler: operatör/private host, Docker daemon, custom host adapter, OS ACL/TLS
terminator, immutable source/wheel kaynağı ve ayrı backup key/journal storage.
Agent source, tool/model yanıtı, önerilen oracle, uploaded report ve HTTP request
güvenilir kabul edilmez. Customer private runner kanıtı bağımsız platform attestation’ı değildir.

Phase 9’da doğrulanan düzeltmeler: auth rollback rate bypass, rotation rate reset,
pending enrollment/key kapasitesi, pending model grant revoke, duplicate receipt,
future scenario schema yazılması, eksik/regressed deletion journal, TLS accept bloklama
ve browser parent-death kaynağı. Bulunmayan açık yoktur iddiası yapılmaz.

Secrets yalnız açık runner env/private credential file’dadır. Başka projelerin
anahtarları veya .env dosyaları aranmaz; provider body/exception text destek ticket’ına
aktarılmaz. Model default grant sıfırdır; ücretli çağrı ayrı kullanıcı onayı ister.
Audit minimum operator/reference digest tutar; imza ve audit kötü niyetli local admin’e
karşı immutable transparency log değildir. [Incident runbook](operations-runbook.md).

Ek doğrulamalar: private canonical dosya okuması credential değişiminden sonra
upload başlatabiliyordu; backend tenant guard reddetti ve başka tenant’a kalıcı
kayıt kanıtlanmadı. File-read await sınırına da epoch fence eklendi; gerçek browser
regression’ı geçti. Windows owner erişimi belirsizse reaper owner’ı alive kabul edip
cleanup’ı reddeder. Etkin Docker context ayrıca kontrol edilir:
[`DOCKER_CONTEXT`, `DOCKER_HOST`’u geçersiz kılar](https://docs.docker.com/reference/cli/docker/#environment-variables).
Remote context yerel host değişkeniyle gizlenip cleanup’a sokulamaz.
