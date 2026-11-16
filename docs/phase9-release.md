# Private-pilot release, onboarding ve support

Bu sürüm operator-assisted private pilot içindir. Gerçek dış ekip ve tekrar kullanım
AG-075 **PENDING EXTERNAL VALIDATION**; self-service/GA başarısı veya ödeme kanıtı yoktur.

## Yayına alma kapısı

- Tam unit/integration/Docker suite, gerçek Chromium ve temiz installed-wheel profili başarılı.
- Source commit ve 13 matrix/lint/Docker/gate sonucu kontrol edilmiş; skip/failure gizlenmemiş.
- Bounded HTTP soak ve gerçek process-death/recovery kanıtı var; bunu production SLO saymayın.
- Private state ACL, TLS edge, disk kapasitesi; quiescent backup, ayrı key ve güncel journal hazır.
- Recovery/retention/cancellation operatörü belirli; küçük ücretsiz fixture ile ilk smoke yapılmış.
- AG-075 veya yeni framework/production connector destekleniyor diye pazarlanmaz.

## Hesap → bağla → test → rapor → ödeme/iptal → silme

Operatör `management init` ile ilk hesabı, `management create-account --state ...
--owner-credential PRIVATE.key --user ... --name ... --credential-file NEW.key`
ile açık yeni private hesabı oluşturur. Credential sadece exclusive dosyaya yazılır;
public signup/email/password flow yoktur. Kullanıcı [Phase 7 onboarding](phase7-onboarding.md)
ile kendi source/endpoint/image, tool/environment, fixture ve oracle’ını belirtir;
generator taslak üretir, insan review/approve yapar, private runner gerçek testi yürütür.

Funnel `configured_connections`, `approved_cases`, `verified_current_runs` ayrımını
gösterir. Configuration bir erişim veya başarı doğrulaması değildir. Artifact expiry
sonrasında historical ALLOW current verified run sayılmaz. Assisted/self-service ve
external pilot ölçümleri teknik fixture sayacından üretilmez.

Manuel plan isteği pending kalır. Gerçek harici ödeme operatör tarafından kendi
finansal kaynağında kontrol edilir; `activate-plan` ve `reconcile-billing` yalnız
reference/status kaydıdır. Kullanıcı kendi "ödendi" beyanıyla plan açamaz. Cancel
talebi ve operator external billing teyidi ayrı işlemdir; otomatik charge/refund yoktur.
Bu geliştirmedeki synthetic reference gerçek ödeme değildir. Silme formu doğru proje
adı, plan iptali ve verified quiescence ister. Private cache/backup/image cleanup
ayrıca [runbook](operations-runbook.md) kapsamındadır. Workspace owner ayrıca primary
hesabını ve bütün projelerini kaldırabilir; başka workspace korunur ve restore’da
workspace intent’i uygulanır. Harici kopyaların fiziksel silinmesi ayrıca doğrulanır.

## Durum / hata iletişimi

| Durum | Anlam / sonraki adım |
| --- | --- |
| queued/leased/running | Bitmiş doğrulama yok; yeni key ile kör tekrar başlatmayın. |
| BLOCK + unknown cleanup/lease loss | Kaynak/dış etki/usage incelenmeli; cleanup recovery PASS değildir. |
| RATE_LIMITED | Bir sonraki window’u bekleyin; key rotation limiti sıfırlamaz. |
| QUOTA_LEDGER_UNVERIFIED | İşlem durur; eksik/negatif kaydı sıfırla doldurmayın, verified kanıtla uzlaştırın. |
| PLAN_CANCELLATION_REQUIRED | Önce external cancellation operatörce teyit edilmeli. |
| VERIFIED_QUIESCENCE_REQUIRED | Pending işi iptal edin, aktif/unknown cleanup’ı doğrulayın. |
| DELETION_JOURNAL_UNVERIFIED/REGRESSION | Boş dosya oluşturmayın; authoritative latest signed kopyayı operatör geri getirsin. |
| Future schema / restore unverified | Eski binary ile zorlamayın, mevcut kullanıcı dosyaları üzerine restore etmeyin. |

Destek için secretsiz request/run/suite/source digest, zaman, güvenli error code,
yeniden üretim, beklenen/gözlenen ve cleanup durumunu verin. Model anahtarı, token,
ham provider response veya primary kullanıcı verisini ticket’a koymayın. Bug retest
aynı oracle + değişen agent + eski BLOCK/yeni ALLOW ister; gerçek kullanıcı başarısı
yalnız gerçek ekip kanıtıyla ölçülür. Harici kişilere bu geliştirmede mesaj gönderilmedi.

Pilot hazır oluş: teknik closed-fixture/private-runner akışları doğrulanabilir; ilk
gerçek ekipler düşük concurrency ve operatör gözetimiyle başlamalı. Daha uzun donanım
soak’ı, production financial reconciliation, bağımsız pentest, tüm harici kopyaların erase’i,
public signup ve gerçek assisted/self-service funnel ölçümü genel kullanıma açılış
öncesi ayrıca kabul ister. Phase 10 otomatik başlatılmaz.
