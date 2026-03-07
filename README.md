# Google Ads Dashboard - Premium Analiz Paneli

Bu proje, Google Ads kampanyalarınızı derinlemesine analiz eden, geçmiş dönem kıyaslamaları yapan ve AI destekli içgörüler sunan profesyonel bir Streamlit panelidir.

## 🚀 Özellikler

- **📊 Anlık Durum Analizi**: Lifetime veriler, toplam harcama, dönüşüm ve CPL takibi.
- **🕰️ Tarihsel Karşılaştırma (Premium)**: 
    - İki farklı tarih aralığının (Geçmiş vs Güncel) kafa kafaya kıyaslanması.
    - 6 adet dinamik pasta grafiği (Harcama, Lead, Tık dağılımları).
    - Kampanya bazlı **Direkt Kıyaslama** bar grafikleri.
- **✨ AI Micro-Insights**: Her bölüm için saniyeler içinde yönetici özeti çıkaran GPT-4o entegrasyonu.
- **📈 Detaylı Zaman Analizi**: Günlük, haftalık ve aylık bazda kronolojik performans tomografisi.
- **🔗 Eşleme Türü & IS**: Anahtar kelime stratejisi ve pazar payı analizi.
- **🎨 Estetik & Profesyonel Tasarım**: Temiz sayı formatları (Para: ₺X, Yüzde: %X.X) ve renk kodlu tablolar.

## 🛠️ Kurulum

1. Depoyu klonlayın:
   ```bash
   git clone <repo-url>
   cd google-ads-python
   ```

2. Bağımlılıkları yükleyin:
   ```bash
   pip install -r requirements.txt
   ```

3. Kimlik bilgilerini hazırlayın:
   - `google-ads.yaml` dosyasını ana dizine ekleyin (API Key, Developer Token vb.).
   - `client_secrets.json` dosyasını ekleyin.

4. Uygulamayı çalıştırın:
   ```bash
   streamlit run dashboard.py
   ```

## 🔐 Güvenlik Notu

`.gitignore` dosyası `google-ads.yaml` ve `client_secrets.json` gibi hassas dosyaları içerecek şekilde yapılandırılmıştır. **Lütfen API anahtarlarınızı asla GitHub'a yüklemeyin.**

---
*DeepMind Advanced Agentic Coding ekibi tarafından tasarlanmıştır.*
