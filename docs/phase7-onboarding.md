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
İşletim sistemi kullanıcısı, controller operatörü ve custom host adapter’lar güvenilir
kabul edilir. Private runner’ın kendi beyanı bağımsız attestation değildir.

## 2. Test et

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
