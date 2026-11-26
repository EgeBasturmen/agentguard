# S2-02: Dispatch admission ve lifecycle fencing

Bu profil tek controller/private runner içindir; domain bağımsızdır. Yeni migration,
endpoint, zorunlu RPC alanı veya API sürümü eklenmez. Canlı kullanıcı state'inde
kurulum/migration yapılmaz. İşler otomatik redispatch edilmez.
Güvence güncel controller ve güncel built-in runner birlikte kullanıldığında geçerlidir;
wire uyumu, eski controller'ın bu admission kontrollerini yaptığı anlamına gelmez.

## Kontrol noktaları

1. Enqueue mevcut onaylı frozen suite'i ve agent binding'ini sabit spec olarak saklar.
2. Claim aynı control DB transaction'ında güncel repository admission'ını doğrular.
   Sprint 1'in ATTACH/SAVEPOINT ve disk rollback-journal mekanizması kullanılır;
   approval/revision/source/snapshot okuması ile lease yazımı aynı atomik sınırdadır.
   Güncel snapshot, case/review ve connection içeriği queued spec ile karşılaştırılır.
   Creator'ın güncel run rolü ve runner/issuer yetkisi kontrol edilir. Admission
   okunurken issuer süresi dolarsa lease/spec verilmez.
3. PrivateRunner execution çağrısından önce mevcut heartbeat RPC'sinin sonucunu
   **senkron** bekler. Yalnız `cancel=false` ve geçerli pozitif lease yanıtı kabul edilir.
   Cancel, hata, bozuk yanıt veya kayıp ACK durumunda execution çağrılmaz. Arka plan
   heartbeat thread'inin başlamış olması izin sayılmaz.
4. Heartbeat geçerli runner/lease bağını, lease sonunu, execution deadline'ını ve
   güncel admission'ı yeniden kontrol eder. Doğrulama sürerken dolan süreler de
   ACK öncesi tekrar kontrol edilir. İlk başarılı heartbeat mevcut `running`
   geçişidir; ayrı bir start endpoint'i değildir.
5. Finish, mevcut rapor/oracle/source/evaluation kontrollerini korur. Güncel
   admission artık geçersizse veya cancel istenmişse doğrulanmış rapor bile ALLOW
   olamaz. Validation sonunda süre ve runner yetkisi tekrar kontrol edilir.

Geçerli lease ile çalışan job'a iptal/admission reddi bildirildiğinde cancel bayrağı
kalıcıdır; lease süresi uzatılmadan BLOCK tutulur. Süre dolmadan sunulan geçerli
cleanup raporu hâlâ doğrulanabilir. Bu yüzden `BLOCK + cleanup=verified` mümkündür;
cleanup doğruluğu davranış PASS'i değildir. Revoked credential, yanlış/stale lease
ve expired deadline durumlarında güvenlik doğrulamaları atlanarak rapor kabul edilmez.

## Terminal durum ve rezervler

Claim öncesi reddedilen job'a spec/lease hiç gönderilmez. Job terminal ERROR/BLOCK,
cleanup `not_required` olur ve execution rezervleri sıfırlanır. Admitted-run kullanımı
geri alınmaz. Bu işlem lease/event yazımlarıyla aynı transaction'da commit/rollback olur.

Lease tesliminden sonraki reddin execution yapmadığını sunucu tek başına kanıtlayamaz.
İlk heartbeat reddinde terminal BLOCK ve unknown cleanup/rezervler korunur. Çalışan
job'daki admission/cancellation reddi, geçerli cleanup teslimi için lease'i süresi
doluncaya kadar tutar; yeni execution veya otomatik retry sağlamaz. Runner'ın yerel
pre-execution BLOCK özeti sunucudan gelen completion/cleanup attestation değildir.

Sabit hata nedenleri job decision/event kayıtlarındadır: `DISPATCH_ADMISSION_UNVERIFIED`,
`DISPATCH_SPEC_CHANGED`, `DISPATCH_CREATOR_AUTHORITY_REVOKED`, `RUN_CANCELLED`,
`LEASE_LOST_EXECUTION_AND_CLEANUP_UNVERIFIED`, `JOB_DEADLINE_CLEANUP_UNVERIFIED`.
Yanlış runner/credential için mevcut NOT_FOUND/UNAUTHENTICATED, yanlış lease için
STALE_LEASE korunur. Ham exception, spec veya token hata mesajına eklenmez.

## Güven sınırı ve kalan dağıtık yarış

Sunucunun start ACK'i ile gerçek agent/container başlangıcı tek distributed
transaction değildir. ACK'ten **sonra**, ilk agent adımından önce approval/revoke/
cancel değişebilir; ağ gecikebilir veya bölünebilir. Heartbeat ve cancellation
best-effort durdurma sağlar; dış HTTP servisindeki başlamış/commit edilmiş etkileri
kesin durdurma, rollback veya exactly-once execution garantisi yoktur. Finish'te
güncel admission/cancel kontrolü yanlış ALLOW'u engeller; geçmiş yan etkiyi silmez.

Zero-invocation garantisi built-in PrivateRunner'ın claim veya senkron ACK öncesi
reddettiği akış içindir. Eski/custom veya kötü niyetli private runner bu client
fence'ini atlayabilir. Private runner beyanı bağımsız execution attestation değildir.
Önceden kabul edilmiş aynı finish raporunun idempotent teslimi yeni execution değildir;
mevcut retention ve tarihsel sonuç kuralları korunur.

Server catalog'ındaki source/connection pin'i kontrol edilir. Sunucu runner'ın diskini
uzaktan hash'leyemez; Python source/image hash kontrolü mevcut execute_spec akışında,
host agent import'u olmadan, gerçek execution'dan önce korunur. Yeni version binding
ayrı immutable suite oluşturur; eski binding kendiliğinden yeni agent'a yükseltilmez.

Job creator'ın güncel principal rolü kontrol edilir. Mevcut job schema'sı enqueue
key ID'sini saklamadığından, yalnız enqueue anahtarına özgü sonradan revoke bağlaması
iddia edilmez. Runner credential ve ona bağlı issuer key/rol/revoke/expiry her RPC'de
doğrulanır. Kuyruk bekleme TTL'si eklenmez; mevcut execution deadline claim'de başlar.

## Doğrulama profili

Sentetik bağımsız controller'lar, gerçek SQLite transaction'ları, barrier/event ile
iki yarış sırası, gerçek HTTP ve gerçek Docker kullanılır. Invocation sayacı production
RunController'ı sarmalar; başarılı çağrıyı mock cevapla değiştirmez. Geçerli Docker
agent'ının completed tool audit'i ve cleanup kanıtı doğrulanır. Önceden reddedilen
akışlarda sayaç sıfırdır. Test credential/lease değerleri assertion introspection'a
taşınmaz. S2-03 sonuç teslimi retry/cache recovery bu pakette uygulanmaz.


## Local verification record - 2026-11-26

Final source: 848 unit/integration, 78 real Docker E2E/security and 145 combined
Sprint 1/S2-01/S2-02 regression tests passed; zero failures, errors or skips.
Groups overlap: 926 unique suite cases. S2-02 adds 37 integration and 6 Docker
cases. Docker tests explicitly selected the existing local Chromium directory;
assertions and timeouts were not weakened. These are local results only:
no commit, push or new GitHub CI run was performed.
