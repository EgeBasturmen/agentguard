# Private pilot operasyon ve incident runbook

17 Kasım 2026. Tek aktif controller/private runner profili. Main veya kullanıcı
repo’sunu resetlemeyin; kaynak recovery veri recovery’sinin yerine geçmez.
AG-075 dış pilot kanıtı bekler. Bu belge yapılmış müşteri incident’i değildir.

## Deploy / migration

Önce doğrulanmış source/wheel/CI ve özel state dizini, ACL ve TLS edge kontrol edilir.
Windows ACL operatörce doğrulanır; POSIX dizin owner-only olmalıdır. State’i web root,
public bucket veya paylaşılan/senkronize klasöre koymayın. Model grant default sıfır;
ücretli çağrı ayrıca açık onay gerektirir. Eski/yeni controller binary’lerini aynı
state üzerinde eşzamanlı çalıştırmayın. Graceful drain: yeni enqueue’yu durdurun,
queued işleri iptal edin, çalışan cleanup’ın doğrulanmasını bekleyin, runner/server’ı durdurun.

```powershell
agentguard management backup --state .agentguard/server --output .agentguard/pre-upgrade.agbackup
agentguard management migrate --state .agentguard/server
agentguard management serve --state .agentguard/server --host 127.0.0.1 --port 8765
```

Backup komutu legacy v1’i yükseltmeden snapshot alır. Migration v1→v2 tek transaction’dır;
başarısızlıkta eski schema/data korunur. Future/yanlış schema reddedilir; SQL’i elle
yeniden oluşturup bozuk DB’ye boş tablolar eklemeyin. Disk dolu/permission/lock sorunu
varsa yeni koşu başlatmayın. Önce dosya sistemi/boş alan/tek-process durumunu düzeltin.
Yeni state oluşturup eski veriyi sessizce kaybolmuş gibi göstermeyin.

## Backup / restore

Backup imzalıdır, şifreli değildir. Archive, `signing.key` ve **güncel** `deletions.json`
ayrı kontrollü/şifreli storage’da tutulur. Secret/key içeriğini terminale, Git’e,
incident ticket’a veya rapora yazmayın. `.agbackup` ve `.agentguard/` Git dışındadır.
Backup’ı en az her release öncesi ve operatörün kabul ettiği RPO’ya göre alın.
RPO son başarılı backup; RTO müşteri verisi/donanımıyla ayrıca ölçülür, fixture süresi
production RTO garantisi değildir. Journal her silmeden sonra authoritative offsite
kopyaya güncellenmelidir; eski backup ile eski journal’ı birlikte geri yüklemeyin.

```powershell
agentguard management restore .agentguard/pre-upgrade.agbackup --target .agentguard/restored --signing-key .agentguard/server/signing.key --deletions .agentguard/server/deletions.json
```

Mevcut dizin üzerine restore yoktur. Önce yeni hedef doğrulanır; eski controller
kapalı kalır. Aynı anda kaynak ve restored controller’ı aynı ajan ortamına bağlamayın.
Eski credentials/runner token’ları iptaldir. `recover-owner` ile yeni credential dosyasını
exclusive oluşturun, runner’ı yeniden enroll edin. İlk deneme küçük/ücretsiz fixture
suite olmalı; normal veri/cleanup/scope görülmeden yeni ücretli model grant’i vermeyin.
Rollback gerekiyorsa yeni DB’yi eski binary ile açmayın; verified backup’tan başka yeni
hedefe restore edin. Eski backup ve key’i doğrulamadan silmeyin.

## Queue/runner veya host ölümü

Lease kaybı BLOCK/unknown cleanup’tır; otomatik tekrar çalıştırmayın. Dış etkiler veya
provider maliyeti bilinmiyorsa sıfır kabul etmeyin. Yeni run farklı idempotency key ile
iş etkisini tekrarlayabilir; önce kaynakları ve provider/billing kanıtını inceleyin.
Browser helper parent ölümünü izleyip context/proses/owned workspace’i kapatır;
controller/host/power loss her koşulda cleanup receipt üretmiş sayılmaz.

Yerel daemon’da bilinen container/run için:

```powershell
agentguard management reap-container --container-id EXACT_64_HEX --run-id EXACT_32_HEX
```

Kimlikleri gerçek incident manifest/Docker inspect kanıtından alın; örnek metinleri
çalıştırmayın. Canlı owner, farklı host/label, mount veya remote daemon reddedilir.
`docker rm` wildcard/prune veya bütün kullanıcı container’larını öldürme kullanmayın.
Resource cleanup receipt iş sonucunu PASS yapmaz. Gerçek private runner cache varsa:

```powershell
agentguard management recover-job --state .agentguard/server --job EXACT_JOB_ID --report PRIVATE_CANONICAL_CACHE.json --reason "Verified private cache after lease loss"
```

Oracle/source/runtime kanıtı doğrulanır; cleanup recover edilebilir, eski karar BLOCK
kalır. Unknown compute/model rezervi otomatik boşaltılmaz. Cache yoksa kanıt uydurmayın;
operatör dış etki/maliyet incelemesini ve destek kaydını sürdürmelidir.

## Billing / quota incident

`operations` durumunda ledger mismatch varsa admission BLOCK olur. Eksik satırı
uydurup sıfır yazmayın, plan cap’ini yükselterek sorunu gizlemeyin. Verified backup,
admission/run reference ve provider-reported receipt’i uzlaştırın. Harici manual
payment reference tek plan aktivasyonuna aittir; aynı reference farklı proje için kullanılamaz.
`reconcile-billing --paid/--unpaid` yerel operatör işlemidir, bankaya çağrı yapmaz.
Yanlış/çelişen status/reference reddedilir; gerçek finansal kayıt ayrı kaynaktır.
Aktif plan veri silmeden önce iptal/teyit ister. Budget azaltma/reprice unstarted live
işleri iptal eder; in-flight iş için cancellation istenir, gerçekleşmiş charge geri alınmaz.

## Data deletion / retention

Kullanıcı doğru proje adını yazarak primary silmeyi ister. Aktif/unknown cleanup
veya aktif pilot plan varken reddedilir. Signed intent silmeden önce yazılır;
restart kalan silmeyi tekrar uygular. Journal eksik/regressed ise durun; güncel signed
kopyayı güvenilir operator storage’dan geri getirin. Boş journal üretmek çözüm değildir.
Private runner cache, indirilen raporlar, source/image cache ve operator backup’ları
ayrıca kendi retention sürecine göre temizlenir. Primary delete bu kopyaların fiziksel
olarak silindiğini iddia etmez. Workspace owner ayrıca UI/API veya
`management delete-account --state ... --credential-file PRIVATE.key --confirmation "WORKSPACE NAME"`
ile primary hesabını silebilir; üyelik/key/audit ve bütün projeleri kaldırılır.
Başka workspace silinmez. Workspace tombstone’u da restore’da uygulanır; private
cache/download/backup/image kopyaları ayrıca operatör sorumluluğundadır.

## Credential / security incident

1. Etkilenen scope/proje için yeni run admission’ını durdurun; ilgili key/runner’ı iptal edin.
2. Secret’ı ticket’a yapıştırmadan request ID, run ID, UTC zaman, güvenli error code,
   image/source/suite digest ve cleanup durumunu kaydedin. Raw provider response istemeyin.
3. Aktif agent cancel/cleanup, harici tool/model etkisi ve faturayı ayrı inceleyin.
4. Operator-assisted credential recovery/rotation yapın; backup key’i ayrı korunur.
5. Küçük fixture regression ve required CI başarılı olmadan yeniden açmayın.

Bağımsız pentest, yasal bildirim süreleri ve gerçek incident response tatbikatı yapılmış
gibi raporlanmaz. İletişim/veri koruma yükümlülüklerini gerçek deploy operatörü belirler.
