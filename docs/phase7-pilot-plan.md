# AG-075: gerçek dış pilot kabul planı

**BLOCKED / PENDING EXTERNAL VALIDATION (16 Kasım 2026).** Kullanıcı doğrulanmış
3–5 bağımsız dış ekip ve paylaşılabilir gerçek pilot kanıtı bulunmadığını bildirdi.
Bu belge test planıdır; yapılmış pilot veya başarı raporu değildir.

## Pilot önkoşulları

AG-070–074 source/test/wheel/CI kanıtı; private storage/TLS deployment; müşteri
runner Docker/CI ortamı; seçilen adapter/protokol ve gerekli staging reset/cleanup;
sentetik fixture veya açık veri işleme izni. Gerçek API ücreti için ekip/operatörün
ayrı açık bütçe onayı gerekir. Production credential/tenant kullanılmaz.

Bağımsız ekipleri geliştirici veya teknik fixture kimlikleriyle doldurmayın. Her ekip
için kişisel veri içermeyen gerçek takım referansı, bağımsızlık teyidi, katılım/veri
paylaşım izni ve kanıt linklerini yetkili pilot operatörü toplar. AG-075'i runtime
feedback sayacı veya teknik smoke kendi başına tamamlayamaz.

## Her gerçek ekip için yürütme

1. Doküman ve desteklenen/unsupported matrix verilir. İlk oturumun assisted mı
   self-service mi olduğu baştan kaydedilir. Operatörün yaptığı adımlar ayrı tutulur.
2. Ekibin mevcut agent/tool'u desteklenen yolla bağlanır. Wrapper gereksinimi, runtime
   JSON/fixture/rule/oracle düzeltmesi, geçirilen süre ve ihtiyaç duyulan destek kaydedilir.
3. Approved küçük suite ile gerçek private runner run'u alınır. Capability gaps,
   execution/policy/cleanup ve report açıklamasını ekip kendi sözleriyle doğrular.
4. Ekibin kendi agent'ında gerçek bir hata bulunursa `/feedback` bug kaydı açılır:
   yeniden üretim adımları, beklenen/gözlenen davranış, rule/suite hash, failing run
   ve kaynak sürümü; secret/PII eklenmez. Başka ekibe ait artifact eklenemez.
5. Fix sonrası aynı oracle/suite korunarak yeni artifact hash'i ile tekrar run yapılır.
   Eski BLOCK/yeni ALLOW, farklı agent digest'i ve aynı suite kontrolü `retested`
   geçişinde uygulanır. Oracle değişmişse yeni review gerekir; hata çözülmüş sayılmaz.
6. Ekip daha sonra ürünü yeniden kullanır. Tek destekli demo, returning/self-service
   başarı yerine yazılmaz. İkinci kullanım tarihi/run linki ve destek seviyesi kaydedilir.
7. Feedback open → investigating → fix_ready → retested → closed izlenir. Teknik
   fixture feedback'i `external_validation=false` kalır. Ticari dönüşüm varsa gerçek
   harici invoice/payment kanıtı ayrıca ve sınırlı erişimle teyit edilir.

## Toplanacak ölçümler ve kabul

Bağlantı başarı/başarısızlık nedeni, ilk anlamlı rapora süre, manuel adım/düzeltme
sayısı, unknown oracle sayısı, capability gap, destek süresi, bulunan gerçek bug'ın
anlaşılması/düzeltilmesi/yeniden doğrulanması, daha sonraki tekrar kullanım.
Assisted ve self-service kohortları ayrı raporlanır; unknown değerler sıfır yapılmaz.

Kapanış için 3–5 gerçek bağımsız ekibin dokümanla bağlandığı, veri erişimi ve quota'nın
server-side kaldığı; gerçek bug'ı bulup açıklamayı anladığı ve düzeltip tekrar kullandığı
kanıtları değerlendirilir. Tek bir başarılı demo tüm ekiplerin self-service başarısı
değildir. Başarısız veya desteklenen profil dışında kalan sonuçlar da kayda girer.

Ekip referansı, izin kaydı, assisted/self-service etiketi, run/suite/source hash'leri,
feedback/retest ve tekrar kullanım linkleri gerçek verilerle doldurulmadan sayısal pilot
sonucu yayınlanmaz. Şu anda doldurulmuş kayıt yoktur; ekip/bug/gelir sayısı uydurulmaz.
