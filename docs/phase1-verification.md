# Phase 1 — AG-014/015 doğrulama kaydı

Bu kayıt workflow dosyasını, yerel testleri, bağımsız paket kurulumunu ve uzak CI
kanıtını ayrı değerlendirir. Workflow eklemek tek başına CI'ın geçtiği anlamına
gelmez. AG-010–013 bu teslimde yeniden uygulanmamıştır.

## Başlangıç incelemesi

| Görev | Mevcut parça | Kapatılacak eksik |
|---|---|---|
| AG-014 | PyYAML runtime dependency, setuptools entrypoint ve paketlenmiş YAML demo fixture'ları, README | Tekrarlanabilir temiz wheel/console CLI testi ve CI'a bağlantısı; README metadata/checkout sınırı |
| AG-015 | Üç gerçek Docker testi işaretli | Unit/integration ayrımı, strict marker, Python/OS matrisi, zorunlu Docker işi ve workflow doğrulaması |

## CI sözleşmesi

`.github/workflows/ci.yml` push, pull_request ve manuel tetikleme sunar.

1. `workflow-lint`: gerçek actionlint 1.7.12 ile YAML/Actions expression ve job kontrolü.
2. `tests-and-wheel`: ubuntu-24.04 / windows-2022 × Python 3.10, 3.11, 3.12,
   3.13, 3.14. Her hücre unit ve integration katmanlarını ayrı çalıştırır,
   `python -m build` ile isolated sdist→wheel üretir ve temiz runtime kontrolü yapar.
3. `docker`: Ubuntu/Python 3.11, daemon erişimi zorunlu; mevcut source'tan worker
   image build edilir, timeout image hazırlanır ve gerçek Docker testleri çalışır.
4. `Phase 1 gate`: lint, bütün test/wheel matrisi ve Docker işi başarılı olmadıkça
   başarısızdır. Bu tek job branch protection için required check olarak seçilebilir;
   repository ayarları bu teslimde değiştirilmez.

Matrix fail-fast kapalıdır. Docker işi skip/fallback yapmaz. Test XML ve wheel
kanıtı/dağıtımlar platform+Python kimliğiyle artifact olarak saklanır. Workflow
contents:read kullanır; checkout kimlik bilgilerini saklamaz.

Test katmanları conftest'te toplama sırasında, `-m` filtresinden önce atanır.
Unit default'tur; gerçek CLI/rapor/filesystem/subprocess testleri integration,
gerçek daemon testleri docker'dır. Açık marker ile override mümkündür fakat bir
case birden fazla katman taşıyamaz. `test_test_layers.py` gerçek collection
sonuçlarının ayrık ve eksiksiz partition olduğunu, bilinmeyen marker'ın collection
hatası verdiğini doğrular.

## Temiz paket kabul testi

`scripts/check_wheel.py` verilmiş tek wheel'i kaynak checkout'undan farklı geçici
dizinde yeni, system-site-packages kullanmayan venv'e kurar. Dev extras kurulmaz;
PYTHONPATH/PYTHONHOME aktarılmaz. Aşağıdakiler gerçekten çalıştırılır:

- Dependency resolution + pip check; runtime PyYAML ve README metadata.
- Modüllerin yeni venv'den geldiği, editable/source import olmadığı ve
  pytest/pytest-asyncio/build bulunmadığı kontrolü.
- Her iki paketlenmiş YAML fixture'ının yüklenmesi ve gerçek console entrypoint.
- Checkout dışında demo; good/bad test exit kodları ve JSON verdict'leri.
- Canlı regression ve kayıtlı compare için aynı BLOCK/findings; HTML/JSON eşitliği.
- Aynı good raporunun kabulü, geçersiz/boş kontrollerin exit 2 ile reddi.

Betik çalışma ağacını veya mevcut demo raporlarını değiştirmez. CI'da --result
ile Python sürümü, wheel kimliği ve başarılı kontrol alanları JSON'a yazılır.
Dist dizinindeki birden fazla wheel sessizce seçilmez; dosya açıkça verilmelidir.

## Gerçek doğrulama sonuçları

10 Kasım 2026 sonuçları aşağıdadır. Linux koşuları gerçek Python sürümlerinde
Docker `python:*-slim` ortamlarında, Windows koşusu yerel Python 3.11.9 ile yapıldı.
Bunlar GitHub-hosted ubuntu-24.04/windows-2022 job koşuları olarak sunulmaz.

| Kontrol | Durum |
|---|---|
| Windows/Python 3.11 unit/integration | Doğrulandı: 262 unit, 134 integration |
| Windows/Python 3.11 Docker dahil tam suite | Doğrulandı: 399 passed, 0 failed/error/skipped; worker güncel dosyadan yeniden build edildi |
| Workflow actionlint | Son workflow actionlint 1.7.12 ile geçti (exit 0) |
| Linux/Python 3.10.22 | 262 unit + 134 integration, 0 failed/error/skipped; isolated sdist→wheel ve temiz runtime kurulumu geçti |
| Linux/Python 3.11.16 | 262 unit + 134 integration, 0 failed/error/skipped; isolated sdist→wheel ve temiz runtime kurulumu geçti |
| Linux/Python 3.12.16 | 262 unit + 134 integration, 0 failed/error/skipped; isolated sdist→wheel ve temiz runtime kurulumu geçti |
| Linux/Python 3.13.18 | 262 unit + 134 integration, 0 failed/error/skipped; isolated sdist→wheel ve temiz runtime kurulumu geçti |
| Linux/Python 3.14.10 | 262 unit + 134 integration, 0 failed/error/skipped; isolated sdist→wheel ve temiz runtime kurulumu geçti |
| Windows/Python 3.11 isolated build + temiz wheel | Doğrulandı: pytest/build/pytest-asyncio bulunmayan venv, checkout dışı console CLI, YAML/metadata ve verdict parity |
| GitHub-hosted tam Linux/Windows matrix + Phase 1 gate | Bekliyor: kaydedilmemiş değişiklikler commit/push edilmedi |

Yerel kanıt dizini `artifacts/phase1-local/`: summary.json, source-hashes.json,
platform başına wheel-check.json/JUnit ve build/test logları içerir. Kaynak snapshot
SHA-256: `faf9e15cdc498b47b81b2d15355fe2ebaecc14b3bb7c9ce3111bdbad6d8641b9`.
Kaynaklar geçici kopyada test edildi; mevcut build çıktıları, Git index'i ve demo
raporları değiştirilmedi. Artefaktlar Git'e dahil edilmez.

**Kapanış kararı:** AG-014 tamamlandı. AG-015'in kod, test ayrımı ve workflow
implementasyonu tamam; GitHub-hosted CI doğrulaması **NEEDS_VERIFICATION**.
Dolayısıyla Phase 1 bütünüyle kapandı denmez. Linux yerel matrix + Windows 3.11
kanıtı, kalan Windows hosted hücrelerinin veya Actions cache/upload/gate
entegrasyonunun başarılı çalıştığı iddiasına dönüştürülmez.

Son GitHub satırı başarılı run URL'si ve aynı revision kanıtı olmadan kapatılmaz.
Uzak CI'ı tetiklemek için incelenen değişikliklerin bir dala commit/push edilmesi
gerekir. Kullanıcının kaydedilmemiş değişikliklerini korumak için bu yayın işlemi
otomatik yapılmadı. Mevcut çalışma ağacı/index değişmeden ayrı bir dalda yayın
onayından sonra CI kanıtı alınabilir; ardından AG-015 ve Phase 1 kapanışı güncellenir.
Python >=3.10 metadata alt sınırıdır; gelecekteki bütün minor sürümlerin test
edildiği iddiası değildir. Remote job'ın varlığı platform kanıtı yerine geçmez.

AG-010–013 için boş/bozuk/çelişkili spec, parent/root/list diff, evaluator/async,
eksik evidence/cleanup, kaldırılan kontrol/fingerprint ve CLI/HTML/JSON eşitliği
regresyonları bu koşularda geçti. Host import ve hostile agent state/trace
forgery savunması Phase 2'nin ayrı kabul kapısıdır; bu rapor izolasyon garantisi
vermez.

## Tekrarlama

```console
python -m pip install -e ".[dev]"
python -m pytest -m unit
python -m pytest -m integration
python -m build
python scripts/check_wheel.py dist --result artifacts/wheel-check.json
docker build -f docker/Dockerfile.worker -t agentguard-worker:dev .
docker pull python:3.11-slim
python -m pytest
```

Workflow araçlarının birincil belgeleri:
[checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python),
[upload-artifact](https://github.com/actions/upload-artifact),
[actionlint](https://github.com/rhysd/actionlint/releases/tag/v1.7.12),
[pytest markers](https://docs.pytest.org/en/stable/how-to/mark.html).

Phase 2 izolasyon/worker implementasyonuna geçilmez. Mevcut Docker testleri
handshake/lifecycle kapsamındadır; güvenilmeyen agent izolasyonu kanıtı değildir.
