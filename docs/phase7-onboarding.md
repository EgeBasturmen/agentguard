# Agent’ını Bağla → Test Et → Rapor Al

Bu rehber tek controller ve müşterinin kendi makinesi/CI runner’ı içindir.
AG-075 dış ekip doğrulaması bekliyor; self-service başarı kanıtı değildir.
Sunucu agent kodunu import etmez veya shared-hostile execution sunmaz.
Public signup, e-posta/parola, SSO ve otomatik framework keşfi yoktur.

## Ön koşullar ve operatör kurulumu

Python 3.10–3.14 ve çalışan Docker gerekir. Kurulu wheel’in `agentguard` CLI’sini
kullanın. Aşağıdaki yollar yeni, açıkça seçilmiş özel dizinler olmalıdır; var olan
anahtar dosyalarının üzerine yazılmaz. `.agentguard/` ve `*.key` Git dışında tutulur.
Windows’ta state, credential ve cache dizinlerinin ACL’sini yalnız operatör/runner
hesabına izin verecek şekilde önceden sınırlandırın; POSIX dosyaları owner-only’dır.
Paylaşılan/senkronize dizin veya genel web kökü kullanmayın.

```powershell
agentguard management init --state .agentguard/server --owner pilot-operator --credential-file .agentguard/owner.key
agentguard management serve --state .agentguard/server --host 127.0.0.1 --port 8765
```

İlk komut anahtarı dosyaya yazar; terminale anahtar basmaz. İkinci komut açık kalır.
Tarayıcıda `http://127.0.0.1:8765` açın. Owner anahtarını güvenilir yerel editörle
dosyadan alıp parola alanına girin; terminale, rapora veya destek kaydına yapıştırmayın.
Tarayıcı anahtarı yalnız bellekte tutar, çıkış/proje değişiminde özel görünümü temizler.
Bu anahtar model API anahtarı değildir. Uzak kullanımda doğrulanmış TLS ve private
network/reverse proxy gerekir. Standart kütüphane sunucusu genel internet/HA hizmeti
olarak doğrulanmış değildir. Anahtar TTL’si ve mevcut üyelik runner için de geçerlidir;
anahtar rotasyonu/yeniden enrollment operatör tarafından yönetilir.

## 1. Agent’ını bağla

Yeni proje oluşturun. Agent’ınızın kaynak kökünü açıkça belirleyin. `run(task,sandbox)`
metodu olan Python sınıfı veya desteklenen HTTP/container protokolü gerekir.
Python sınıfı için kaynak hash’ini host import yapmadan hesaplayın:

```powershell
agentguard management source-hash --workspace C:/agents/my-agent --reference agent:Agent
```

Çıktıdaki digest’i arayüze girin. Runtime JSON’unu kendi agent/tool/environment
sözleşmenize göre düzenleyin. Örneğin filesystem test profili:

```json
{"agent":{"transport":"python","reference":"agent:Agent","source_dir":"."},"environment":{"kind":"filesystem"},"tools":[{"connector":"environment"}],"policy":{"allowed_tools":["file.write"]}}
```

Bu örneğin görevi ve fixture’ı aşağıda örneklenir; agent’ınızın hedefi farklıysa
kuralları ve ortamı siz belirlemelisiniz. Sınıfın gerçek tool çağrısı sandbox üzerinden
geçmelidir. Container image’i digest ile pinleyin. HTTP agent için AgentGuard protokolü
ve bağımsız namespace/reset/snapshot/cleanup observer gerekir; black-box endpoint’in
gizli dış etkileri veya gateway dışı model kullanımı gözlenmiş sayılmaz.
Kaydetme yalnız `configuration_saved` üretir; bağlantı/behavior doğrulaması değildir.
LangChain, LangGraph, CrewAI ve AutoGen için native otomatik wrapper yoktur; açık
Python/HTTP/container uyarlaması gerekir. OpenAPI/MCP tool discovery, agent framework
keşfi veya sıfır yapılandırma değildir.

Arayüzden runner enrollment oluşturun. Yeni terminalde tek kullanımlık değeri gizli
istemden girin (10 dakika geçerli); komut satırı argümanına koymayın:

```powershell
agentguard management enroll --server http://127.0.0.1:8765 --credential-file .agentguard/runner.key
agentguard management runner --server http://127.0.0.1:8765 --credential-file .agentguard/runner.key --workspace C:/agents/my-agent --cache .agentguard/private-cache
```

Runner cache başlangıçta yeni/boş olmalıdır. Canonical kanıt bu özel cache’te kalır.
S2-03 cache'i runner credential/origin bağıyla korur; report ve teslim metadata'sı
tek atomik kayıttadır. Pending, incomplete veya corrupt kayıt varsa runner otomatik
yeni job almaz; TTL bunları sessizce silmez. Eski V1 cache'leri otomatik dönüştürülmez.
Canonical `job.json` export'u ile özel `job.entry.json` dosyasını karıştırmayın;
entry dosyası lease içerir ve paylaşılmamalıdır. Recovery ve orphan staging kuralları:
[S2-03 private cache](s2-03-private-cache.md). Upload retry varsayılan kapalıdır;
S2-04'ün açık recovery seçeneği aşağıda açıklanır.
Her runner için ayrı cache kullanın; paylaşılan cache desteklenmez. Windows'ta güvenilen,
tercihen ayrı bir OS hesabı kullanın.
İşletim sistemi kullanıcısı, controller operatörü ve custom host adapter’lar güvenilir
kabul edilir. Private runner’ın kendi beyanı bağımsız attestation değildir.

### Güvenli enrollment input ve credential publication (S2-01)

Varsayılan gizli terminal istemi korunur. Terminal gizli giriş sağlayamazsa komut
durur; echo yapan otomatik fallback yoktur. PowerShell yapıştırma sorunu yaşanırsa
aşağıdaki **açık stdin seçeneğini**, önceden hazırlanmış özel dizindeki yeni bir
hedefle kullanın. Kod yalnız gizli `Read-Host` istemine yapıştırılır; komut metnine,
environment variable'a veya dosyaya konmaz. Bu örnek enrollment yapar; otomatik
retry içermez. Runner başlatılması ayrı işlemdir.

```powershell
$agEnrollment = Read-Host 'Tek kullanımlık enrollment kodu' -AsSecureString
try {
    ([System.Net.NetworkCredential]::new('', $agEnrollment)).Password |
        agentguard management enroll --server http://127.0.0.1:8765 --credential-file "$env:LOCALAPPDATA/AgentGuard/runner-new.key" --enrollment-stdin
}
finally {
    Remove-Variable agEnrollment -ErrorAction SilentlyContinue
}
```

`--enrollment-stdin` interaktif stdin'i reddeder; pipe EOF ile kapanmalıdır. En fazla
128 karakter okunur; yalnız tek, geçerli enrollment kodu ve opsiyonel tek satır sonu
kabul edilir. Boş/iptal/geçersiz giriş sunucuya gönderilmez. PowerShell/Python
belleğinde secret'ın kısa süreli plaintext karşılığı vardır; SecureString tüm
belleği sıfırlama veya güvenilmeyen yerel kullanıcıya karşı koruma garantisi değildir.
Transcription, debug trace ve komut çıktısını kaydeden wrapper kullanmayın.

Credential hedefi varsa, boş olsa bile değiştirilmez, silinmez veya izinleri
onarılmaz. Windows'ta özel staging ve final dosya sahibi mevcut kullanıcı SID'i,
korumalı DACL tek kullanıcı FullControl olmalıdır. POSIX dosyası owner-only'dir.
Ebeveyn dizine güvenilmeyen hesapların yazma/silme izni varsa exchange başlamaz.
Windows SYSTEM/yerel yöneticiler güvenilen host sınırındadır; izin kontrolü bunun
yerine geçmez. Credential içeriği izin doğrulanmadan okunmaz/yazılmaz.

Yeni credential önce rastgele özel staging alanında tamamen yazılır, flush ve
izin kontrolü yapılır; ardından overwrite etmeyen atomik rename ile yayımlanır.
Başarı yalnız final credential tekrar doğrulanınca bildirilir. Yerel Windows/Linux
dosya sistemleri desteklenir; Linux `renameat2(RENAME_NOREPLACE)` desteği gereklidir.
Desteklenmeyen dosya sistemi/atomiklikte fail-closed; network/sync dizini desteklenmez.
Bu süreç çökmesine karşı absent-or-complete publication sağlar; donanım/power-loss
dayanıklılığı dosya sistemi ve işletim sistemi garantileriyle sınırlıdır.

Hata JSON'u secret yerine `operation_status`, `publication_status`,
`credential_written=false` ve `recovery_required` bildirir:

- `not_started`: exchange yapılmadı; yerel input/izin/hedef sorununu inceleyin.
- `rejected`: sunucu açık 4xx reddi verdi; otomatik tekrar yapılmaz.
- `unknown`: bağlantı/yanıt belirsiz; token tüketilmiş olabilir. Aynı kodla körlemesine
  retry veya yeniden enrollment yapmayın. Operatör sunucudaki enrollment/runner
  kayıtlarını salt okunur incelemeli; belirsiz runner'ı güvenli şekilde revoke edip
  yeni kod oluşturma kararı ayrıca verilmelidir.
- `accepted` + yerel publication hatası: sunucu başarılı yanıt verdi, fakat credential
  saklanması doğrulanamadı. Başarı değildir. `publication_status=unverified` ise final
  dosya mevcut ve tam olabilir; kesinlikle üzerine yazmayın/silmeyin. Önce yalnız
  metadata/izin kontrolü yapın; içerik veya secret'ı destek kaydına koymayın. Operatör
  gerekirse orphan runner'ı revoke eder, yeni kod ve **yeni hedef** ile ayrı işlem yapar.

Revoke kararı öncesi ilgili proje, enrollment adı/zamanı ve runner kimliği bağımsız
sunucu kayıtlarından eşleştirilmelidir. Kimlik kesinleşmiyorsa başka runner'ları
tahminen revoke etmeyin; operatör incelemesini bekleyin. Credential'ın bir kere
dönen plaintext değeri sunucudan yeniden okunabilir kabul edilmez.

Sunucu exchange ve yerel dosya yayını distributed transaction değildir. Aradaki
crash'in sunucu sonucunu yerel dosyadan tek başına belirlemek mümkün değildir.
Sonuç teslimi retry ve belirsiz execution yeniden çalıştırılması S2-01 kapsamında
değişmez; belirsiz execution otomatik tekrar çalıştırılmaz.

Secret içermeyen kalıcı `.agentguard-credential-*.lock` eşzamanlı yerel publisher'ları
koordine eder; işlem sonunda silinmez (split-lock yarışını önler). Hard crash özel
`.agentguard-private-*` orphan staging bırakabilir; otomatik tarama/kurtarma/silme
yapılmaz. Yalnız normal hatada işlemin kendi staging'i temizlenir. Orphan'ları backup
ve erişim yönetiminde secret içerebilen dosya olarak değerlendirin. Ortak writer'ı
kullanan `init`, `recover-owner`, `create-account` da aynı publication ve belirsizlik
sınırlarına tabidir; başarısız yerel yazım sunucu/DB işlemini geri almış sayılmaz.

Orphan staging için güvenli operatör temizliği ayrı, açık onaylı bir işlemdir:

1. İlgili publisher'ın artık çalışmadığını doğrulayın. Çalışan publisher varken
   staging veya kalıcı guard dosyasına müdahale etmeyin.
2. Tam hedefi, sahipliğini, özel izinlerini ve reparse/symlink olmadığını içerik
   okumadan doğrulayın. İzinler belirsizse okumayın; güvenlik incelemesine bırakın.
3. Exchange sonucunu bağımsız sunucu kayıtlarıyla eşleştirin. Gerekli revoke/recovery
   kararını verin; orphan içeriğini terminale açmayın veya otomatik credential yapmayın.
4. Yalnız doğrulanmış, artık gerekmeyen tek staging dizini ve içindeki bilinen
   credential dosyası için açık silme onayı alın. Wildcard/genel temizlik yapmayın;
   final credential ve kalıcı guard'a dokunmayın. Normal silme, SSD/snapshot/backup
   kopyalarından güvenli silinme garantisi değildir. İçeriği loglamadan sonucu kaydedin.

## 2. Test et

Sonuç tesliminde opt-in recovery için mevcut `management runner` komutuna
`--recover-pending` eklenebilir. Bu seçenek yalnız bütünlüğü doğrulanmış pending
raporu sınırlı retry ile teslim eder; agent'ı yeniden çalıştırmaz. Varsayılan kapalıdır.
Credential yine özel dosyadan okunur; secret'ı argümana veya shell history'ye yazmayın.
Budget tükenmesi veya bozuk/legacy cache operatör incelemesi ister; cache'i silerek
execution'ı yeniden başlatmayın. [Teslim recovery sınırları](s2-04-transport-recovery.md).

Queued job, çalıştırılmadan önce güncel approval/revision/source/suite ve yetki
kontrolünden tekrar geçer. Built-in runner ilk heartbeat ACK'ini bekler; admission
reddinde işi otomatik yeniden çalıştırmaz. Geçerli cleanup raporu BLOCK ile birlikte
görülebilir. ACK sonrası revoke/cancellation dış etkileri kesin geri alamaz.
Ayrıntılı sınırlar: [dispatch admission profili](s2-02-dispatch-admission.md).

Görevi, başlangıç fixture’ını, başarı ve yasak davranış kurallarını girin. Örnek fixture
`{"files":{"note.txt":"draft"}}`, kural metni `expect files.note\.txt = "done"`.
JSON kural dizisinde backslash’i `\\` ile encode edin. Bunlar sizin bağımsız
beklentileriniz olmalıdır; agent veya judge cevabını ground truth yapmayın.

Taslak üretin. Her oracle tanımını, fixture’ı ve sınıflamayı inceleyin; bilinmeyenleri
onaylamayın. Review ve approve ayrı işlemlerdir. Sadece onaylanmış case’leri seçip
suite’i dondurun. Revision/retirement eski admission’ı geçersiz kılabilir.
Preflight kaynak/config/capability uyumunu kontrol eder, davranış PASS’i vermez.
Sonra testi kuyruğa alın; runner gerçek SuiteRunner/RunController/Docker akışını kullanır.
Kuyruk, koşu ve event cursor’ları kalıcıdır; lease kaybı unknown cleanup/BLOCK olur,
iş otomatik tekrar çalıştırılmaz. Aktif iptal cleanup doğrulaması ister.

Model bütçesi varsayılan sıfırdır. Ücretli çağrı için ayrıca açık kullanıcı onayı,
operatörün proje bütçesi/fiyat tabanı ve runner’ın `--allow-live-models --allow-external`
izinleri gerekir. Provider anahtarı yalnız runner ortamında tutulur; web formuna veya
runtime JSON’a yazılmaz. Bu rehber canlı model çağrısı gerektirmez.

## 3. Rapor al ve tekrar doğrula

Run detail’de case sonuçları, audit, assertion, policy ve cleanup kanıtlarını inceleyin.
Eksik/çelişkili kanıt BLOCK’tur; olumlu judge policy hatasını örtemez.
Sunucu yalnız redacted sunum artifact’i saklar. İndirme mevcut yetki ve süreli grant
gerektirir. Silinen/süresi dolan artifact mevcut gate’i BLOCK yapar; tarihsel karar
ayrı gösterilir. Retention yalnız primary artifact ve özel runner cache profili içindir;
genel hesap silme, backup, metadata ve audit retention garantisi değildir.

Sürüm karşılaştırması için aynı frozen oracle’a ait iki run ID’sini ve runner cache’teki
iki orijinal canonical JSON’u manuel seçin. Redacted indirme canonical comparison
authority değildir. Runtime değişirse yeni review; yalnız source/image digest değişirse
aynı runtime için sürüm bağlama API’sini kullanın. Mevcut Phase 6 comparator kullanılır;
başarısız baseline’dan düzelmiş run’a geçiş release baseline promotion değildir.
Web sonucu `authorizes_release=false` taşır.

Destek formunda secretsiz yeniden üretim, beklenen/gözlenen davranış ve run referansı
verin. Bug süreci open → investigating → fix_ready → retested → closed’dur.
Retest aynı oracle altında eski BLOCK, değişen artifact ve yeni ALLOW kanıtı ister.
Plan talepleri pending kalır; harici ödeme/iptal teyidi operatör içindir. Otomatik
tahsilat, fatura doğruluğu veya ödeme sağlayıcısı entegrasyonu iddiası yoktur.

Gerçek dış ekip pilotunun ölçüm ve kabul planı [pilot planındadır](phase7-pilot-plan.md).
Teknik test ve demo kimlikleri bu kabulü karşılamaz.
