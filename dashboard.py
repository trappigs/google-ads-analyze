import os
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from google.ads.googleads.client import GoogleAdsClient
import datetime
from openai import OpenAI
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign

# ─── CONFIG ─────────────────────────────────────────────────────────────────────
TARGET_CPL = 500   # ₺500 hedef
API_VERSION = "v23"

# ─── DEFAULTS FROM secrets.toml ─────────────────────────────────────────────
DEFAULT_CUSTOMER_ID = st.secrets.get("dashboard", {}).get("customer_id", "")
DEFAULT_OPENAI_KEY  = st.secrets.get("dashboard", {}).get("openai_api_key", "")
DEFAULT_META_TOKEN  = st.secrets.get("meta_ads", {}).get("access_token", "")
DEFAULT_META_ACC    = st.secrets.get("meta_ads", {}).get("account_id", "")

st.set_page_config(page_title="Google + Meta Reklam Analizi", layout="wide")
st.title("📊 Google + Meta Reklam Analizi")

with st.sidebar:
    st.header("⚙️ Ayarlar")

    # Platform & mode selection (shown first, before credentials)
    customer_id = DEFAULT_CUSTOMER_ID
    openai_key  = DEFAULT_OPENAI_KEY
    meta_token  = DEFAULT_META_TOKEN
    meta_account_id = DEFAULT_META_ACC

    # Credential Resolution & UI
    all_set = all([DEFAULT_CUSTOMER_ID, DEFAULT_OPENAI_KEY, DEFAULT_META_TOKEN, DEFAULT_META_ACC])
    
    if not all_set:
        st.caption("🚨 Bazı kimlik bilgileri eksik")
        with st.expander("🔧 Kimlik Bilgilerini Yapılandır", expanded=True):
            st.markdown("⚠️ **Not:** Buradaki değişiklikler sadece bu oturum için geçerlidir.")
            _cid = st.text_input("Google Ads Müşteri ID", type="password", value=DEFAULT_CUSTOMER_ID, placeholder="123-456-7890")
            _okey = st.text_input("OpenAI API Key", type="password", value=DEFAULT_OPENAI_KEY, placeholder="sk-...")
            _mtoken = st.text_input("Meta Access Token", type="password", value=DEFAULT_META_TOKEN, placeholder="EAAB...")
            _macc = st.text_input("Meta Ad Account ID", type="password", value=DEFAULT_META_ACC, placeholder="act_...")
            
            if st.button("💾 Bilgileri Uygula"):
                st.session_state.customer_id = _cid or DEFAULT_CUSTOMER_ID
                st.session_state.openai_key  = _okey or DEFAULT_OPENAI_KEY
                st.session_state.meta_token  = _mtoken or DEFAULT_META_TOKEN
                st.session_state.meta_account_id = _macc or DEFAULT_META_ACC
                st.success("Bilgiler oturuma uygulandı!")
                st.rerun()
    else:
        st.caption("🔒 Kimlik bilgileri kilitlendi (secrets.toml)")

    # Final credential resolution
    customer_id = st.session_state.get("customer_id", DEFAULT_CUSTOMER_ID)
    openai_key  = st.session_state.get("openai_key", DEFAULT_OPENAI_KEY)
    meta_token  = st.session_state.get("meta_token", DEFAULT_META_TOKEN)
    meta_account_id = st.session_state.get("meta_account_id", DEFAULT_META_ACC)
    
    st.divider()
    st.markdown(f"🎯 **Hedef CPL:** ₺{TARGET_CPL:,}")

# ─── DATA HELPERS ────────────────────────────────────────────────────────────────

def get_client():
    # 1. Try Streamlit Secrets (Highest Priority)
    if "google_ads" in st.secrets:
        try:
            # Ensure all values are strings for the client library
            credentials = {k: str(v) for k, v in st.secrets["google_ads"].items()}
            return GoogleAdsClient.load_from_dict(credentials, version=API_VERSION)
        except Exception as e:
            st.error(f"Secrets yüklenirken hata: {e}")
            pass

    # 2. Local development fallback (google-ads.yaml)
    import os
    yaml_path = "google-ads.yaml"
    if os.path.exists(yaml_path):
        try:
            # Sanity check: Don't load if it's just placeholders
            with open(yaml_path, "r") as f:
                content = f.read()
                if "INSERT_" in content:
                    return None # placeholder detected
            return GoogleAdsClient.load_from_storage(yaml_path, version=API_VERSION)
        except Exception:
            return None
            
    return None

def _run(c_id, query):
    client = get_client()
    if not client:
        st.error("❌ Google Ads Client başlatılamadı. Lütfen secrets.toml veya google-ads.yaml dosyasını kontrol edin.")
        st.stop()
        
    svc = client.get_service("GoogleAdsService")
    rows = []
    for batch in svc.search_stream(customer_id=c_id, query=query):
        rows.extend(batch.results)
    return rows, client

def get_campaigns(c_id, start, end):
    q = f"""
        SELECT campaign.name, metrics.cost_micros, metrics.conversions,
               metrics.clicks, metrics.impressions, metrics.search_impression_share
        FROM campaign
        WHERE metrics.cost_micros > 0
          AND segments.date BETWEEN '{start}' AND '{end}'
    """
    rows, _ = _run(c_id, q)
    data = [{"Kampanya": r.campaign.name, "Harcama (TL)": round(r.metrics.cost_micros / 1e6, 0), "Dönüşümler": round(r.metrics.conversions, 0), "Clicks": round(r.metrics.clicks, 0), "Impressions": round(r.metrics.impressions, 0), "IS (%)": round(r.metrics.search_impression_share * 100, 1) if r.metrics.search_impression_share > 0 else 0} for r in rows]
    df = pd.DataFrame(data)
    return df.groupby("Kampanya", as_index=False).sum().round(1) if not df.empty else df

def get_match_types(c_id, start, end):
    q = f"""
        SELECT ad_group_criterion.keyword.match_type, metrics.cost_micros, metrics.conversions, metrics.clicks
        FROM keyword_view
        WHERE metrics.cost_micros > 0
          AND segments.date BETWEEN '{start}' AND '{end}'
    """
    rows, client = _run(c_id, q)
    mt = client.get_type("KeywordMatchTypeEnum").KeywordMatchType
    data = [{"Eşleme Türü": mt.Name(r.ad_group_criterion.keyword.match_type), "Harcama (TL)": round(r.metrics.cost_micros / 1e6, 0), "Dönüşümler": round(r.metrics.conversions, 0), "Clicks": round(r.metrics.clicks, 0)} for r in rows]
    df = pd.DataFrame(data)
    return df.groupby("Eşleme Türü", as_index=False).sum().round(1) if not df.empty else df

def get_time_series_data(c_id, start, end):
    q = f"""
        SELECT segments.date, metrics.cost_micros, metrics.conversions, metrics.clicks
        FROM campaign
        WHERE metrics.cost_micros > 0
          AND segments.date BETWEEN '{start}' AND '{end}'
    """
    rows, _ = _run(c_id, q)
    data = [{"Tarih": r.segments.date, "Harcama (TL)": round(r.metrics.cost_micros / 1e6, 0), "Dönüşümler": round(r.metrics.conversions, 0), "Clicks": round(r.metrics.clicks, 0)} for r in rows]
    df = pd.DataFrame(data)
    if not df.empty:
        df["Tarih"] = pd.to_datetime(df["Tarih"])
        df = df.groupby("Tarih", as_index=False).sum(numeric_only=True).sort_values("Tarih").round(1)
    return df

@st.cache_data(show_spinner=False)
def get_ai_micro_insight(api_key, context_title, data_dict):
    try:
        client = OpenAI(api_key=api_key)
        prompt = f"""
        Sen bir Google Ads Uzmanısın. Müşterine (bir işletme yöneticisine) şu bölüm hakkında çok kısa, vurucu ve teknik terim boğmayan bir özet yaz: '{context_title}'
        Veriler: {data_dict}
        Maksimum 2-3 cümle olsun. Sadece en önemli bulguyu ve ne yapılması gerektiğini söyle. Profesyonel ama samimi bir dil kullan.
        """
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "Sen profesyonel bir Google Ads stratejistisin."}, {"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=200
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI Analizi şu an yapılamıyor: {e}"

def generate_ai_summary(api_key, context_data):
    client = OpenAI(api_key=api_key)
    prompt = f"Sen Google Ads uzmanısın. Verileri analiz et: {context_data}. Markdown formatında rapor yaz. %90 analiz %10 öneri olsun."
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        return res.choices[0].message.content
    except Exception as e: return str(e)

def get_keywords(c_id):
    q = """
        SELECT campaign.name, ad_group.name, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, ad_group_criterion.quality_info.quality_score, metrics.cost_micros, metrics.conversions, metrics.clicks, metrics.impressions, metrics.ctr
        FROM keyword_view
        WHERE campaign.status = 'ENABLED' AND metrics.cost_micros > 0
    """
    rows, client = _run(c_id, q)
    MATCH_LABELS = {2: "Tam Eşleme", 3: "Sıralı Eşleme", 4: "Geniş Eşleme"}
    data = [{"Kampanya": r.campaign.name, "Reklam Grubu": r.ad_group.name, "Anahtar Kelime": r.ad_group_criterion.keyword.text, "Eşleme Türü": MATCH_LABELS.get(r.ad_group_criterion.keyword.match_type, str(r.ad_group_criterion.keyword.match_type)), "QS": r.ad_group_criterion.quality_info.quality_score or 0, "Harcama (TL)": round(r.metrics.cost_micros / 1e6, 0), "Dönüşümler": round(r.metrics.conversions, 0), "Clicks": round(r.metrics.clicks, 0), "CTR (%)": round(r.metrics.ctr * 100, 1)} for r in rows]
    return pd.DataFrame(data)

# ─── META ADS HELPERS ────────────────────────────────────────────────────────────

def get_meta_insights(token, acc_id, start=None, end=None):
    try:
        FacebookAdsApi.init(access_token=token)
        account = AdAccount(acc_id)
        
        params = {'level': 'campaign'}
        if start and end:
            params['time_range'] = {'since': start, 'until': end}
        else:
            params['date_preset'] = 'maximum'
            
        fields = ['campaign_name', 'spend', 'impressions', 'clicks', 'actions']
        insights = account.get_insights(fields=fields, params=params)
        
        data = []
        for insight in insights:
            leads = 0
            if 'actions' in insight:
                for action in insight['actions']:
                    if action['action_type'] in ['lead', 'onsite_conversion.messaging_first_reply']:
                        leads += int(action.get('value', 0))
            
            data.append({
                "Kampanya": insight.get('campaign_name', 'Unknown'),
                "Harcama (TL)": float(insight.get('spend', 0)),
                "Dönüşümler": leads,
                "Clicks": int(insight.get('clicks', 0)),
                "Impressions": int(insight.get('impressions', 0))
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Meta API Hatası: {e}")
        return pd.DataFrame()

def get_meta_ad_insights(token, acc_id, start=None, end=None):
    try:
        FacebookAdsApi.init(access_token=token)
        account = AdAccount(acc_id)
        
        params = {'level': 'ad'}
        if start and end:
            params['time_range'] = {'since': start, 'until': end}
        else:
            params['date_preset'] = 'last_30d' # Varsayılan son 30 gün
            
        fields = ['ad_name', 'spend', 'impressions', 'frequency', 'actions', 'inline_link_click_ctr']
        insights = account.get_insights(fields=fields, params=params)
        
        data = []
        for insight in insights:
            leads = 0
            if 'actions' in insight:
                for action in insight['actions']:
                    if action['action_type'] in ['lead', 'onsite_conversion.messaging_first_reply']:
                        leads += int(action.get('value', 0))
            
            imp = int(insight.get('impressions', 0))
            spend = float(insight.get('spend', 0))
            cpl = spend / leads if leads > 0 else 0
            ctr = float(insight.get('inline_link_click_ctr', 0))
            
            data.append({
                "Kreatif Adı": insight.get('ad_name', 'Unknown'),
                "Harcama (TL)": spend,
                "Dönüşümler": leads,
                "CPL (₺)": round(cpl, 0),
                "Frekans": round(float(insight.get('frequency', 1.0)), 2),
                "Link CTR (%)": round(ctr, 2),
                "Impressions": imp
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Meta Kreatif Hatası: {e}")
        return pd.DataFrame()

def get_meta_breakdown_insights(token, acc_id, start, end, breakdowns):
    """Generic Meta Insights breakdown fetcher."""
    try:
        FacebookAdsApi.init(access_token=token)
        account = AdAccount(acc_id)
        params = {
            'level': 'account',
            'time_range': {'since': start, 'until': end},
            'breakdowns': breakdowns,
        }
        fields = ['spend', 'impressions', 'clicks', 'actions']
        insights = account.get_insights(fields=fields, params=params)
        data = []
        for insight in insights:
            leads = sum(
                int(a.get('value', 0)) for a in insight.get('actions', [])
                if a['action_type'] in ['lead', 'onsite_conversion.messaging_first_reply']
            )
            row = {
                'Harcama': float(insight.get('spend', 0)),
                'Clicks': int(insight.get('clicks', 0)),
                'Lead': leads,
            }
            for b in breakdowns:
                row[b] = insight.get(b, 'N/A')
            data.append(row)
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Meta Breakdown Hatası ({breakdowns}): {e}")
        return pd.DataFrame()

def get_meta_time_series_data(token, acc_id, start, end):
    try:
        FacebookAdsApi.init(access_token=token)
        account = AdAccount(acc_id)
        params = {
            'level': 'account',
            'time_range': {'since': start, 'until': end},
            'time_increment': 1,
        }
        fields = ['date_start', 'spend', 'actions', 'clicks', 'inline_link_click_ctr']
        insights = account.get_insights(fields=fields, params=params)
        
        data = []
        for insight in insights:
            leads = 0
            if 'actions' in insight:
                for action in insight['actions']:
                    if action['action_type'] in ['lead', 'onsite_conversion.messaging_first_reply']:
                        leads += int(action.get('value', 0))
            data.append({
                "Tarih": insight.get('date_start'),
                "Harcama (TL)": float(insight.get('spend', 0)),
                "Dönüşümler": leads,
                "Clicks": int(insight.get('clicks', 0))
            })
        df = pd.DataFrame(data)
        if not df.empty:
            df["Tarih"] = pd.to_datetime(df["Tarih"])
            df = df.sort_values("Tarih")
        return df
    except Exception as e:
        st.error(f"Meta Zaman Serisi Hatası: {e}")
        return pd.DataFrame()

# ─── MAIN ────────────────────────────────────────────────────────────────────────

if not customer_id:
    st.info("← Soldaki menüden Müşteri ID girin.")
    st.stop()

cid = customer_id.replace("-", "")

with st.sidebar:
    selected_platform = st.radio("Platform Seçimi", ["Google Ads", "Meta Ads"], horizontal=True)
    st.divider()
    
    if selected_platform == "Google Ads":
        mode = st.radio("Google Analiz Modları", ["📊 Anlık Durum", "🕰️ Tarihsel Karşılaştırma", "📈 Detaylı Zaman Analizi", "🤖 AI Strateji Raporu"])
    else:
        mode = st.radio("Meta Analiz Modları", ["📊 Anlık Durum", "🌍 Büyük Resim", "🔻 Huni Analizi", "🎨 Kreatif Analizi", "🪾 Kitle Röntgeni", "🤖 AI Aksiyon Merkezi", "🕰️ Tarihsel Karşılaştırma", "📈 Detaylı Zaman Analizi", "🤖 AI Strateji Raporu"])

if selected_platform == "Google Ads" and mode == "📊 Anlık Durum":
    st.header("📊 Google Ads Anlık Durum")
    df = get_keywords(cid)
    if df.empty: st.warning("Veri yok."); st.stop()
    
    s, c = df["Harcama (TL)"].sum(), df["Dönüşümler"].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Toplam Harcama", f"₺{s:,.0f}")
    c2.metric("Toplam Dönüşüm", f"{c:,.0f}")
    c3.metric("CPL", f"₺{s/c:,.0f}" if c>0 else "0")
    
    st.dataframe(
        df.sort_values("Harcama (TL)", ascending=False).style.format({
            "Harcama (TL)": "₺{:,.0f}",
            "Dönüşümler": "{:,.0f}",
            "Clicks": "{:,.0f}",
            "Impressions": "{:,.0f}",
            "CTR (%)": "{:.1f}%",
            "QS": "{:.0f}"
        }), 
        use_container_width=True, 
        hide_index=True
    )

elif selected_platform == "Meta Ads" and mode == "📊 Anlık Durum":
    st.header("🔵 Meta Ads — Anlık Durum")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik. Lütfen yan menüden doldurun."); st.stop()
    with st.spinner("Meta verileri çekiliyor..."):
        df_meta = get_meta_insights(meta_token, meta_account_id)
    if df_meta.empty:
        st.warning("Meta tarafında veri bulunamadı."); st.stop()
    
    m_s, m_c, m_clicks = df_meta["Harcama (TL)"].sum(), df_meta["Dönüşümler"].sum(), df_meta["Clicks"].sum()
    mk1, mk2, mk3, mk4 = st.columns(4)
    mk1.metric("Toplam Harcama", f"₺{m_s:,.0f}")
    mk2.metric("Toplam Lead", f"{m_c:,.0f}")
    mk3.metric("Ortalama CPL", f"₺{m_s/m_c:,.0f}" if m_c > 0 else "₺0")
    mk4.metric("Toplam Tıklama", f"{m_clicks:,.0f}")
    st.divider()
    st.subheader("📋 Meta Kampanya Detayları (Tüm Zamanlar)")
    df_meta["CPL (₺)"] = (df_meta["Harcama (TL)"] / df_meta["Dönüşümler"]).replace([float("inf")], 0).round(0)
    st.dataframe(
        df_meta.sort_values("Harcama (TL)", ascending=False).style.format({
            "Harcama (TL)": "₺{:,.0f}", "Dönüşümler": "{:,.0f}",
            "Clicks": "{:,.0f}", "Impressions": "{:,.0f}", "CPL (₺)": "₺{:,.0f}"
        }), use_container_width=True, hide_index=True)
    st.divider()
    mc1, mc2 = st.columns(2)
    mc1.plotly_chart(px.pie(df_meta, values="Harcama (TL)", names="Kampanya", title="Harcama Dağılımı"), use_container_width=True)
    mc2.plotly_chart(px.bar(df_meta.sort_values("Dönüşümler", ascending=True).tail(10), x="Dönüşümler", y="Kampanya", orientation="h", title="En Çok Lead Getiren Meta Kampanyaları", color_discrete_sequence=["#1877F2"]), use_container_width=True)

elif selected_platform == "Meta Ads" and mode == "🌍 Büyük Resim":
    st.header("🌍 Meta Ads — Büyük Resim")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik."); st.stop()

    # ── Date Picker: Sadece Tarih A ─────────────────────────────────────
    dp_a, dp_info = st.columns([2, 1])
    with dp_a:
        st.markdown("### 📅 Tarih A (Analiz Etmek İstediğin Dönem)")
        a_s = st.date_input("Başlangıç A", datetime.date(2026, 2, 1), key="br_as")
        a_e = st.date_input("Bitiş A", datetime.date.today(), key="br_ae")
    
    # Tarih B = aynı gün sayısı kadar geriye git
    period_days = (a_e - a_s).days
    b_e = a_s - datetime.timedelta(days=1)
    b_s = b_e - datetime.timedelta(days=period_days)

    with dp_info:
        st.markdown("### 🔄 Tarih B (Otomatik)")
        st.info(f"📐 **{period_days + 1} günlük dönem**\n\n"
                f"Karşılaştırma:\n**{b_s}** → **{b_e}**")

    with st.spinner("Meta verileri yükleniyor..."):
        # Account-level aggregates for both periods
        FacebookAdsApi.init(access_token=meta_token)
        acc_br = AdAccount(meta_account_id)

        def _fetch_br_totals(acc, start, end):
            params = {'level': 'account', 'time_range': {'since': start, 'until': end}}
            fields = ['spend', 'actions', 'clicks', 'impressions', 'inline_link_click_ctr']
            res = list(acc.get_insights(fields=fields, params=params))
            if not res: return 0, 0, 0, 0
            row = res[0]
            leads = sum(int(a.get('value', 0)) for a in row.get('actions', [])
                        if a['action_type'] in ['lead', 'onsite_conversion.messaging_first_reply'])
            ctr = float(row.get('inline_link_click_ctr', 0))
            return float(row.get('spend', 0)), leads, int(row.get('clicks', 0)), ctr

        a_spend, a_leads, a_clicks, a_ctr = _fetch_br_totals(acc_br, a_s.strftime("%Y-%m-%d"), a_e.strftime("%Y-%m-%d"))
        b_spend, b_leads, b_clicks, b_ctr = _fetch_br_totals(acc_br, b_s.strftime("%Y-%m-%d"), b_e.strftime("%Y-%m-%d"))
        a_cpl = a_spend / a_leads if a_leads > 0 else 0
        b_cpl = b_spend / b_leads if b_leads > 0 else 0

        # Daily time-series for both periods
        ts_a = get_meta_time_series_data(meta_token, meta_account_id, a_s.strftime("%Y-%m-%d"), a_e.strftime("%Y-%m-%d"))
        ts_b = get_meta_time_series_data(meta_token, meta_account_id, b_s.strftime("%Y-%m-%d"), b_e.strftime("%Y-%m-%d"))

    st.divider()
    # ── 4 KPI Delta Cards ────────────────────────────────────────────
    def _delta(a, b, inverse=False):
        """Returns delta string and color for metric. 'inverse' means lower is better (CPL)."""
        if b == 0: return None
        pct = (a - b) / abs(b) * 100
        return f"{pct:+.1f}%"

    kc1, kc2, kc3, kc4 = st.columns(4)
    kc1.metric(
        "💰 Toplam Harcama (A)",
        f"₺{a_spend:,.0f}",
        delta=_delta(a_spend, b_spend),
        delta_color="normal",
        help=f"B Dönemi: ₺{b_spend:,.0f}"
    )
    kc2.metric(
        "🎯 Lead Sayısı (A)",
        f"{a_leads:,}",
        delta=_delta(a_leads, b_leads),
        delta_color="normal",
        help=f"B Dönemi: {b_leads:,}"
    )
    kc3.metric(
        "📊 CPL (A)",
        f"₺{a_cpl:,.0f}",
        delta=_delta(a_cpl, b_cpl),
        delta_color="inverse",   # Düşünce iyi (yeşil)
        help=f"B Dönemi CPL: ₺{b_cpl:,.0f}"
    )
    kc4.metric(
        "🔗 Link CTR (A)",
        f"%{a_ctr:.2f}",
        delta=_delta(a_ctr, b_ctr),
        delta_color="normal",
        help=f"B Dönemi CTR: %{b_ctr:.2f}"
    )

    st.divider()
    # ── Overlay Line Charts ─────────────────────────────────────────
    if not ts_a.empty and not ts_b.empty:
        # Normalize to relative day offset so periods align on same x-axis
        ts_a_n = ts_a.copy().reset_index(drop=True)
        ts_b_n = ts_b.copy().reset_index(drop=True)
        ts_a_n["Gün"] = range(1, len(ts_a_n)+1)
        ts_b_n["Gün"] = range(1, len(ts_b_n)+1)

        st.subheader("📈 Günlük Harcama Karşılaştırması")
        fig_spend = go.Figure()
        fig_spend.add_trace(go.Scatter(x=ts_a_n["Gün"], y=ts_a_n["Harcama (TL)"],
            name=f"Tarih A ({a_s} → {a_e})", mode="lines+markers",
            line=dict(color="#1877F2", width=3)))
        fig_spend.add_trace(go.Scatter(x=ts_b_n["Gün"], y=ts_b_n["Harcama (TL)"],
            name=f"Tarih B ({b_s} → {b_e})", mode="lines+markers",
            line=dict(color="#94a3b8", width=2, dash="dot")))
        fig_spend.update_layout(hovermode="x unified", xaxis_title="Dönem Günü",
            yaxis_title="Harcama (₺)", legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig_spend, use_container_width=True)

        st.subheader("🎯 Günlük Lead Karşılaştırması")
        fig_leads = go.Figure()
        fig_leads.add_trace(go.Scatter(x=ts_a_n["Gün"], y=ts_a_n["Dönüşümler"],
            name=f"Tarih A ({a_s} → {a_e})", mode="lines+markers",
            line=dict(color="#10b981", width=3)))
        fig_leads.add_trace(go.Scatter(x=ts_b_n["Gün"], y=ts_b_n["Dönüşümler"],
            name=f"Tarih B ({b_s} → {b_e})", mode="lines+markers",
            line=dict(color="#f59e0b", width=2, dash="dot")))
        fig_leads.update_layout(hovermode="x unified", xaxis_title="Dönem Günü",
            yaxis_title="Lead Sayısı", legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig_leads, use_container_width=True)
    else:
        st.info("⚠️ Seçilen dönemler için günlük veri bulunamadı.")

elif selected_platform == "Meta Ads" and mode == "🔻 Huni Analizi":
    st.header("🔻 Meta Ads — Huni Analizi ve Kaçak Tespiti")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik."); st.stop()

    # Date picker
    hf_col1, hf_col2 = st.columns(2)
    hf_s = hf_col1.date_input("Başlangıç", datetime.date(2026, 1, 1), key="hf_s")
    hf_e = hf_col2.date_input("Bitiş", datetime.date.today(), key="hf_e")

    with st.spinner("Meta huni verileri çekiliyor..."):
        try:
            FacebookAdsApi.init(access_token=meta_token)
            acc_hf = AdAccount(meta_account_id)
            params_hf = {
                'level': 'account',
                'time_range': {'since': hf_s.strftime("%Y-%m-%d"), 'until': hf_e.strftime("%Y-%m-%d")}
            }
            fields_hf = ['impressions', 'clicks', 'actions']
            res_hf = list(acc_hf.get_insights(fields=fields_hf, params=params_hf))

            if not res_hf:
                st.warning("Seçilen dönemde Meta verisi bulunamadı."); st.stop()

            row_hf = res_hf[0]
            impressions = int(row_hf.get('impressions', 0))
            link_clicks = int(row_hf.get('clicks', 0))

            # Parse actions
            leads = 0
            for action in row_hf.get('actions', []):
                if action['action_type'] in ['lead', 'onsite_conversion.messaging_first_reply']:
                    leads += int(action.get('value', 0))
        except Exception as e:
            st.error(f"Meta API Hatası: {e}"); st.stop()

    # ── Funnel Chart ───────────────────────────────────────────────────
    st.divider()
    funnel_data = {
        "Aşama": ["👁️ Gösterim", "🖱️ Link Tıklaması", "🎯 Lead (Dönüşüm)"],
        "Değer": [impressions, link_clicks, leads],
    }
    import pandas as _pd
    df_funnel = _pd.DataFrame(funnel_data)

    fc1, fc2 = st.columns([3, 2])
    with fc1:
        fig_funnel = go.Figure(go.Funnel(
            y=df_funnel["Aşama"],
            x=df_funnel["Değer"],
            textposition="inside",
            textinfo="value+percent initial",
            marker=dict(color=["#1877F2", "#3b82f6", "#10b981"]),
            connector=dict(line=dict(color="#334155", width=2))
        ))
        fig_funnel.update_layout(title="Reklam Hunisi — 3 Adımlı Performans Analizi", height=380)
        st.plotly_chart(fig_funnel, use_container_width=True)

    with fc2:
        st.markdown("### 🔍 Kaçak Tespit Uyarıları")

        # Drop-off oranları
        ctr       = link_clicks / impressions * 100 if impressions > 0 else 0
        cvr_total = leads / link_clicks * 100       if link_clicks > 0 else 0

        # Metrikler
        st.metric("Gösterim → Tıklama (CTR)", f"%{ctr:.2f}")
        st.metric("Tıklama → Lead (CVR)", f"%{cvr_total:.1f}")

        st.divider()

        # ── Koşullu Uyarılar ──────────────────────────────────────────
        warnings_shown = 0

        if link_clicks > 100 and cvr_total < 1:
            st.warning("📋 **Düşük Dönüşüm Oranı**\n\n"
                       f"Tıklama başına lead oranı çok düşük (**%{cvr_total:.2f}**). "
                       "Formu sadeleştirin, teklifi (indirimi/hediyeyi) öne çıkarın veya "
                       "hedef kitleyi daraltın.")
            warnings_shown += 1

        if ctr < 0.5 and impressions > 10000:
            st.info("🎨 **Reklam Kreatifi Zayıf**\n\n"
                    f"Gösterim yüksek ama CTR çok düşük (**%{ctr:.2f}**). "
                    "Reklam görseli veya metni kullanıcıların ilgisini çekmiyor.")
            warnings_shown += 1

        if link_clicks > 0 and leads == 0:
            st.error("⚠️ **Dönüşüm SIFIR**\n\n"
                     "Tıklama alıyorsunuz ancak hiç lead gelmedi. Pixel kurulumunu "
                     "veya formun düzgün çalıştığını kontrol edin.")
            warnings_shown += 1

        if warnings_shown == 0:
            st.success("✅ **Huni sağlıklı görünüyor!**\n\nBelirgin bir kaçak noktası tespit edilmedi.")

elif selected_platform == "Meta Ads" and mode == "🎨 Kreatif Analizi":
    st.header("🎨 Meta Ads — Kreatif Yorgunluğu ve Rekabet Tablosu")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik."); st.stop()

    # Tarih seçici (Kreatif analizi için son 30 gün yaygındır ama kullanıcıya bırakalım)
    cr_col1, cr_col2 = st.columns(2)
    cr_s = cr_col1.date_input("Başlangıç", datetime.date.today() - datetime.timedelta(days=30), key="cr_s")
    cr_e = cr_col2.date_input("Bitiş", datetime.date.today(), key="cr_e")

    with st.spinner("Kreatif verileri çekiliyor..."):
        df_creatives = get_meta_ad_insights(meta_token, meta_account_id, cr_s.strftime("%Y-%m-%d"), cr_e.strftime("%Y-%m-%d"))
    
    if df_creatives.empty:
        st.warning("Seçilen dönemde kreatif verisi bulunamadı."); st.stop()

    # ── 3-Kademeli Durum Mantığı ───────────────────────────────────────
    avg_cpl = df_creatives[df_creatives["Dönüşümler"] > 0]["CPL (₺)"].mean() if not df_creatives[df_creatives["Dönüşümler"] > 0].empty else 0
    red_threshold    = avg_cpl * 1.5   # Kural 1: Bütçe yakan
    orange_threshold = avg_cpl         # Kural 2: Yorgun

    def get_status(row):
        if row["CPL (₺)"] > red_threshold and row["Dönüşümler"] > 0:
            return "🔴 Kapatılmalı"
        if row["Frekans"] > 3 and row["CPL (₺)"] > orange_threshold:
            return "🟠 Yenilenmeli"
        return "✅ Aktif/Sağlıklı"

    def highlight_tier(row):
        if row["Durum"] == "🔴 Kapatılmalı":
            return ['background-color: #fee2e2; color: #991b1b' for _ in row]
        if row["Durum"] == "🟠 Yenilenmeli":
            return ['background-color: #fff3cd; color: #92400e' for _ in row]
        return ['' for _ in row]

    df_creatives["Durum"] = df_creatives.apply(get_status, axis=1)

    cols = ["Kreatif Adı", "Durum", "Harcama (TL)", "Dönüşümler", "CPL (₺)", "Frekans", "Link CTR (%)"]
    df_styled = df_creatives[cols].sort_values("CPL (₺)", ascending=True)

    # ── KPI'lar ────────────────────────────────────────────────
    n_red    = (df_creatives["Durum"] == "🔴 Kapatılmalı").sum()
    n_orange = (df_creatives["Durum"] == "🟠 Yenilenmeli").sum()
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Toplam Kreatif", len(df_creatives))
    kpi2.metric("Ortalama CPL", f"₺{avg_cpl:,.0f}")
    kpi3.metric("🔴 Kapatılmalı", n_red)
    kpi4.metric("🟠 Yenilenmeli", n_orange)

    st.divider()
    st.subheader("📊 Kreatif Performans Radar Tablosu")

    st.dataframe(
        df_styled.style.apply(highlight_tier, axis=1).format({
            "Harcama (TL)": "₺{:,.0f}",
            "CPL (₺)": "₺{:,.0f}",
            "Frekans": "{:.2f}",
            "Link CTR (%)": "%{:.2f}"
        }),
        use_container_width=True,
        hide_index=True
    )

    st.warning(
        f"""
        **Uyarı Seviyeleri:**
        - � **Kapatılmalı (Kırmızı):** CPL, ortalama CPL'in **1.5 katından** fazla (₺{red_threshold:,.0f} ₺ ve üzeri). Bu kreatif bütçe yakıyor — durdurulması veya yeniden yapılandırılması gerekiyor.
        - 🟠 **Yenilenmeli (Turuncu):** Frekans **3.0 üzerinde** VE CPL ortalamanın üstünde (₺{orange_threshold:,.0f} ₺+). Kitle bu kreatifte “yorulmuş” — görsel veya metin yenilenmelidir.
        - ✅ **Aktif/Sağlıklı (Beyaz):** Bu iki şarta uymayan, verimli çalışan kreatifler.
        """
    )

elif selected_platform == "Meta Ads" and mode == "🪾 Kitle Röntgeni":
    st.header("🪾 Meta Ads — Kitle, Demografi ve Cihaz Röntgeni")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik."); st.stop()

    # Tarih seçici
    kol1, kol2 = st.columns(2)
    kx_s = kol1.date_input("Başlangıç", datetime.date(2026, 1, 1), key="kx_s")
    kx_e = kol2.date_input("Bitiş", datetime.date.today(), key="kx_e")
    date_s = kx_s.strftime("%Y-%m-%d")
    date_e = kx_e.strftime("%Y-%m-%d")

    with st.spinner("Demografi ve cihaz verileri çekiliyor..."):
        df_demo  = get_meta_breakdown_insights(meta_token, meta_account_id, date_s, date_e, ['age', 'gender'])
        df_plat  = get_meta_breakdown_insights(meta_token, meta_account_id, date_s, date_e, ['publisher_platform'])
        df_dev   = get_meta_breakdown_insights(meta_token, meta_account_id, date_s, date_e, ['impression_device'])

    st.divider()

    # ── Sol kolon: Yaş × Cinsiyet breakdown  ──────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("👥 Yaş & Cinsiyet Kırılımı")
        if not df_demo.empty:
            df_demo["CPL (₺)"] = (df_demo["Harcama"] / df_demo["Lead"].replace(0, float("nan"))).round(0)
            df_demo["Cinsiyet"] = df_demo["gender"].map({"male": "👨 Erkek", "female": "👩 Kadın"}).fillna(df_demo["gender"])
            df_demo["Yaş"] = df_demo["age"]

            # Yığılmış çubuk: Harcama by age, colored by gender
            fig_demo = px.bar(
                df_demo,
                x="Yaş", y="Lead", color="Cinsiyet",
                barmode="group",
                title="Yaş Grubu × Cinsiyet bazlı Lead Dağılımı",
                color_discrete_map={"👨 Erkek": "#3b82f6", "👩 Kadın": "#f472b6"},
                text="Lead"
            )
            fig_demo.update_layout(xaxis_title="Yaş Grubu", yaxis_title="Lead Sayısı",
                                   legend=dict(orientation="h", y=1.12))
            st.plotly_chart(fig_demo, use_container_width=True)

            # En ucuz CPL satırları
            st.markdown("**🏆 En Ucuz CPL Getiren Segmentler (Lead > 0)**")
            best_segs = df_demo[df_demo["Lead"] > 0][["Yaş", "Cinsiyet", "Harcama", "Lead", "CPL (₺)"]]\
                .sort_values("CPL (₺)").head(5)
            st.dataframe(best_segs.style.format({"Harcama": "₺{:,.0f}", "CPL (₺)": "₺{:,.0f}"}),
                         use_container_width=True, hide_index=True)
        else:
            st.info("Demografi verisi alınamadı.")

    # ── Sağ kolon: Platform & Cihaz pasta grafikleri  ─────────────────
    with col_right:
        st.subheader("📱 Platform & Cihaz Dağılımı")
        if not df_plat.empty:
            df_plat["platform_label"] = df_plat["publisher_platform"].str.capitalize()
            fig_plat = px.pie(
                df_plat, values="Harcama", names="platform_label",
                title="Harcama: Platform (Facebook vs Instagram vs ...)",
                color_discrete_sequence=["#1877F2", "#E1306C", "#405DE6", "#833AB4"]
            )
            fig_plat.update_traces(textinfo="label+percent+value",
                                   texttemplate="%{label}<br>₺%{value:,.0f} (%{percent})")
            st.plotly_chart(fig_plat, use_container_width=True)
        else:
            st.info("Platform verisi alınamadı.")

        if not df_dev.empty:
            df_dev["device_label"] = df_dev["impression_device"].str.replace("_", " ").str.title()
            fig_dev = px.pie(
                df_dev, values="Harcama", names="device_label",
                title="Harcama: Cihaz Türü",
                color_discrete_sequence=["#10b981", "#f59e0b", "#6366f1", "#ef4444"]
            )
            fig_dev.update_traces(textinfo="label+percent+value",
                                  texttemplate="%{label}<br>₺%{value:,.0f} (%{percent})")
            st.plotly_chart(fig_dev, use_container_width=True)
        else:
            st.info("Cihaz verisi alınamadı.")

    # ── Kara Liste Adayları  ──────────────────────────────────────────
    st.divider()
    st.subheader("🚫 Kara Liste Adayları — Para Yiyip Lead Getirmeyen Segmentler")
    if not df_demo.empty:
        # Minimum harcama eşiği: ortalama CPL'in 1.5 katı veya en az 150 TL
        demo_avg_cpl = (df_demo[df_demo["Lead"] > 0]["Harcama"] / df_demo[df_demo["Lead"] > 0]["Lead"]).mean()
        demo_avg_cpl = demo_avg_cpl if (not pd.isna(demo_avg_cpl) and demo_avg_cpl > 0) else 100
        min_harcama_esigi = max(demo_avg_cpl * 1.5, 150)

        kara = df_demo[
            (df_demo["Lead"] == 0) &
            (df_demo["Harcama"] > min_harcama_esigi)
        ][["Yaş", "Cinsiyet", "Harcama"]].sort_values("Harcama", ascending=False)

        st.caption(f"🔎 Minimum eşik: **₺{min_harcama_esigi:,.0f}** (Ort. CPL × 1.5 veya 150₺ — ikisi büyük olanı). "
                   "Bu eşiğin altındaki düşük harcamalı segmentler henüz kendini kanıtlama fırsatı bulamamış olabilir.")

        if not kara.empty:
            st.dataframe(
                kara.style
                    .apply(lambda x: ['background-color: #fee2e2; color: #991b1b' for _ in x], axis=1)
                    .format({"Harcama": "₺{:,.0f}"}),
                use_container_width=True, hide_index=True
            )
            st.warning(f"⚠️ Bu **{len(kara)} segment** ₺{min_harcama_esigi:,.0f}+ harcıyor ama hiç lead getirmiyor. "
                       "Meta'da **Kitle Dışlamaları** > **Detaylı Hedefleme Hariç Tut** seçeneğiyle bunları dışlayın.")
        else:
            st.success(f"✅ Eşiği (₺{min_harcama_esigi:,.0f}) aşan ve sıfır lead getiren segment yok — kara liste boş!")
    else:
        st.info("Demografik veri alınamadığı için kara liste hesaplanamadı.")

elif selected_platform == "Meta Ads" and mode == "🕰️ Tarihsel Karşılaştırma":
    st.header("🔵 Meta Ads — İki Dönem Karşılaştırması")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik."); st.stop()
    
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.subheader("1. Dönem (Geçmiş)")
        md1_s = st.date_input("Başlangıç (1)", datetime.date(2025, 1, 1), key="m_d1s")
        md1_e = st.date_input("Bitiş (1)", datetime.date(2025, 12, 31), key="m_d1e")
    with col_d2:
        st.subheader("2. Dönem (Güncel)")
        md2_s = st.date_input("Başlangıç (2)", datetime.date(2026, 1, 1), key="m_d2s")
        md2_e = st.date_input("Bitiş (2)", datetime.date.today(), key="m_d2e")

    with st.spinner("Meta verileri çekiliyor..."):
        pre_m = get_meta_insights(meta_token, meta_account_id, md1_s.strftime("%Y-%m-%d"), md1_e.strftime("%Y-%m-%d"))
        post_m = get_meta_insights(meta_token, meta_account_id, md2_s.strftime("%Y-%m-%d"), md2_e.strftime("%Y-%m-%d"))
        ts_m = get_meta_time_series_data(meta_token, meta_account_id, md1_s.strftime("%Y-%m-%d"), md2_e.strftime("%Y-%m-%d"))
    
    if pre_m.empty or post_m.empty:
        st.warning("Seçilen tarihler için yeterli Meta verisi bulunamadı."); st.stop()
    
    # KPIs
    pre_ms, pre_mc, pre_mck = pre_m["Harcama (TL)"].sum(), pre_m["Dönüşümler"].sum(), pre_m["Clicks"].sum()
    post_ms, post_mc, post_mck = post_m["Harcama (TL)"].sum(), post_m["Dönüşümler"].sum(), post_m["Clicks"].sum()
    pre_mcpl = round(pre_ms / pre_mc, 0) if pre_mc > 0 else 0
    post_mcpl = round(post_ms / post_mc, 0) if post_mc > 0 else 0

    st.subheader("⬅️ 1. Dönem (Geçmiş)")
    mk1, mk2, mk3 = st.columns(3)
    mk1.metric("Harcama (1)", f"₺{pre_ms:,.0f}"); mk2.metric("Lead (1)", f"{pre_mc:,.0f}"); mk3.metric("CPL (1)", f"₺{pre_mcpl:,.0f}")
    st.subheader("➡️ 2. Dönem (Güncel)")
    mk4, mk5, mk6 = st.columns(3)
    mk4.metric("Harcama (2)", f"₺{post_ms:,.0f}", f"δ: {((post_ms-pre_ms)/pre_ms*100):+.1f}%" if pre_ms > 0 else None)
    mk5.metric("Lead (2)", f"{post_mc:,.0f}", f"{post_mc - pre_mc:+.0f} Fark")
    mk6.metric("CPL (2)", f"₺{post_mcpl:,.0f}", f"Δ: {post_mcpl - pre_mcpl:+.0f}₺", delta_color="inverse" if post_mcpl > pre_mcpl else "normal")
    st.divider()

    # Tabs
    mtab1, mtab2, mtab3 = st.tabs(["📋 Kampanya Kırılımı", "📅 Zaman Trendi", "📊 Dönem Özeti"])
    with mtab1:
        st.subheader("Hangi Meta Kampanya Ne Kadar Harcadı?")
        pre_m_l = pre_m.copy(); pre_m_l["Kampanya"] += " (1. Dönem)"; pre_m_l["Dönem_ID"] = 1
        post_m_l = post_m.copy(); post_m_l["Kampanya"] += " (2. Dönem)"; post_m_l["Dönem_ID"] = 2
        mg_m = pd.concat([pre_m_l, post_m_l], ignore_index=True)
        mg_m["CPL (₺)"] = (mg_m["Harcama (TL)"] / mg_m["Dönüşümler"]).replace([float("inf")], 0).round(0)
        def style_meta_rows(row):
            c = "rgba(71, 85, 105, 0.15)" if row["Dönem_ID"] == 1 else "rgba(24, 119, 242, 0.2)"
            return [f"background-color: {c}"] * len(row)
        st.dataframe(mg_m.sort_values("Harcama (TL)", ascending=False).style.apply(style_meta_rows, axis=1).format(
            {"Harcama (TL)": "₺{:,.0f}", "Dönüşümler": "{:,.0f}", "Clicks": "{:,.0f}", "Impressions": "{:,.0f}", "CPL (₺)": "₺{:,.0f}"}),
            use_container_width=True, hide_index=True, column_order=["Kampanya", "Harcama (TL)", "Dönüşümler", "Clicks", "Impressions", "CPL (₺)"])
        st.divider()
        mcomp = pd.merge(
            pre_m[["Kampanya", "Harcama (TL)", "Dönüşümler"]].rename(columns={"Harcama (TL)": "Harcama (1)", "Dönüşümler": "Lead (1)"}),
            post_m[["Kampanya", "Harcama (TL)", "Dönüşümler"]].rename(columns={"Harcama (TL)": "Harcama (2)", "Dönüşümler": "Lead (2)"}),
            on="Kampanya", how="outer").fillna(0)
        mcomp["CPL (1)"] = (mcomp["Harcama (1)"] / mcomp["Lead (1)"]).replace([float("inf")], 0).round(0)
        mcomp["CPL (2)"] = (mcomp["Harcama (2)"] / mcomp["Lead (2)"]).replace([float("inf")], 0).round(0)
        cc_m = mcomp.melt(id_vars="Kampanya", value_vars=["Harcama (1)", "Harcama (2)"], var_name="Dönem", value_name="Harcama (₺)")
        st.plotly_chart(px.bar(cc_m, x="Kampanya", y="Harcama (₺)", color="Dönem", barmode="group", title="Bütçe Kullanımı Kıyaslaşması", color_discrete_map={"Harcama (1)": "#94a3b8", "Harcama (2)": "#1877F2"}), use_container_width=True)
    
    with mtab2:
        st.subheader("📅 Aylık Performans Trendi")
        if not ts_m.empty:
            ts_ma = ts_m.copy()
            ts_ma["Period"] = ts_ma["Tarih"].dt.to_period("M")
            ts_ma = ts_ma.groupby("Period", as_index=False).sum(numeric_only=True).sort_values("Period")
            ts_ma["Ay"] = ts_ma["Period"].dt.strftime("%b %Y")
            fig_m = go.Figure()
            fig_m.add_trace(go.Bar(x=ts_ma["Ay"], y=ts_ma["Harcama (TL)"], name="Harcama", marker_color="#1877F2"))
            fig_m.add_trace(go.Scatter(x=ts_ma["Ay"], y=ts_ma["Dönüşümler"], name="Lead", yaxis="y2", line=dict(color="#10b981", width=4)))
            fig_m.update_layout(yaxis2=dict(overlaying="y", side="right"), hovermode="x unified", title="Meta Harcama / Lead Trendi")
            st.plotly_chart(fig_m, use_container_width=True)

    with mtab3:
        st.subheader("🏢 Dönem Özeti")
        summary_m = pd.DataFrame([
            {"Dönem": "1. Dönem (Geçmiş)", "Harcama": pre_ms, "Lead": pre_mc, "CPL": pre_mcpl, "Tıklama": pre_mck, "TBM": pre_ms/pre_mck if pre_mck > 0 else 0},
            {"Dönem": "2. Dönem (Güncel)", "Harcama": post_ms, "Lead": post_mc, "CPL": post_mcpl, "Tıklama": post_mck, "TBM": post_ms/post_mck if post_mck > 0 else 0},
        ])
        st.dataframe(summary_m.style.format({
            "Harcama": "₺{:,.0f}", "Lead": "{:,.0f}", "CPL": "₺{:,.0f}", "Tıklama": "{:,.0f}", "TBM": "₺{:.2f}"
        }), use_container_width=True, hide_index=True)
        sc_m1, sc_m2 = st.columns(2)
        sc_m1.plotly_chart(px.bar(summary_m, x="Dönem", y="Harcama", color="Dönem", text_auto=True, title="Toplam Harcama"), use_container_width=True)
        sc_m2.plotly_chart(px.bar(summary_m, x="Dönem", y="CPL", color="Dönem", text_auto=True, title="CPL (Lead Başı Maliyet)"), use_container_width=True)

elif selected_platform == "Meta Ads" and mode == "📈 Detaylı Zaman Analizi":
    st.header("🔵 Meta Ads — Detaylı Zaman Analizi")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik."); st.stop()
    col_ta1, col_ta2 = st.columns(2)
    mta_s = col_ta1.date_input("Başlangıç", datetime.date(2025, 1, 1), key="mta_s")
    mta_e = col_ta2.date_input("Bitiş", datetime.date.today(), key="mta_e")
    with st.spinner("Meta zaman serisi yükleniyor..."):
        ts_mz = get_meta_time_series_data(meta_token, meta_account_id, mta_s.strftime("%Y-%m-%d"), mta_e.strftime("%Y-%m-%d"))
    if ts_mz.empty:
        st.info("Seçilen aralıkta Meta verisi bulunamadı."); st.stop()
    
    freq_m = st.radio("📊 Grafik Kırılımı", ["Günlük", "Haftalık", "Aylık"], horizontal=True, key="meta_freq")
    ts_mz_p = ts_mz.copy()
    if freq_m == "Günlük":
        ts_mz_p = ts_mz_p.groupby("Tarih", as_index=False).sum(numeric_only=True).sort_values("Tarih")
        ts_mz_p["L"] = ts_mz_p["Tarih"].dt.strftime("%d %b")
    elif freq_m == "Haftalık":
        ts_mz_p["Period"] = ts_mz_p["Tarih"].dt.to_period("W")
        ts_mz_p = ts_mz_p.groupby("Period", as_index=False).sum(numeric_only=True).sort_values("Period")
        ts_mz_p["L"] = ts_mz_p["Period"].apply(lambda r: r.start_time).dt.strftime("%W. Hafta (%d %b)")
    else:
        ts_mz_p["Period"] = ts_mz_p["Tarih"].dt.to_period("M")
        ts_mz_p = ts_mz_p.groupby("Period", as_index=False).sum(numeric_only=True).sort_values("Period")
        ts_mz_p["L"] = ts_mz_p["Period"].dt.strftime("%b %Y")
    
    fig_mz = go.Figure()
    fig_mz.add_trace(go.Bar(x=ts_mz_p["L"], y=ts_mz_p["Harcama (TL)"], name="Harcama (₺)", marker_color="#1877F2", opacity=0.75))
    fig_mz.add_trace(go.Scatter(x=ts_mz_p["L"], y=ts_mz_p["Dönüşümler"], name="Lead", yaxis="y2", mode="lines+markers+text", text=ts_mz_p["Dönüşümler"], textposition="top center", line=dict(color="#10b981", width=4)))
    fig_mz.update_layout(yaxis2=dict(overlaying="y", side="right"), hovermode="x unified", legend=dict(orientation="h", y=1.1), title="Meta Harcama vs Lead Trendi")
    st.plotly_chart(fig_mz, use_container_width=True)
    ts_mz_p["CPL"] = (ts_mz_p["Harcama (TL)"] / ts_mz_p["Dönüşümler"]).replace([float("inf")], 0).round(0)
    st.plotly_chart(px.line(ts_mz_p, x="L", y="CPL", markers=True, title="Meta CPL Trendi", color_discrete_sequence=["#ef4444"]), use_container_width=True)
    st.info("💡 Harcama sabitken lead sayısının artması CPL'yi düşürür ve verimli bir kampanyaya işaret eder.")

elif selected_platform == "Meta Ads" and mode == "🤖 AI Strateji Raporu":
    st.header("🔵 Meta Ads — AI Strateji Raporu")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik."); st.stop()
    if "meta_ai_report" not in st.session_state: st.session_state.meta_ai_report = None
    col_mr1, col_mr2 = st.columns(2)
    mrep_s = col_mr1.date_input("Analiz Başlangıç", datetime.date(2025, 1, 1), key="mrep_s")
    mrep_e = col_mr2.date_input("Analiz Bitiş", datetime.date.today(), key="mrep_e")
    if st.button("🚀 Meta AI Raporunu Başlat"):
        if not openai_key: st.error("OpenAI API Anahtarı gerekli (sol menü)!")
        else:
            with st.status("🔍 Meta verileri analiz ediliyor...", expanded=True) as status:
                st.write("📊 Meta kampanya verileri toplanıyor...")
                df_rep = get_meta_insights(meta_token, meta_account_id, mrep_s.strftime("%Y-%m-%d"), mrep_e.strftime("%Y-%m-%d"))
                if not df_rep.empty:
                    df_rep["CPL"] = (df_rep["Harcama (TL)"] / df_rep["Dönüşümler"]).replace([float("inf")], 0).round(0)
                    ctx = {"platform": "Meta Ads", "dönem": f"{mrep_s} - {mrep_e}", "kampanyalar": df_rep[["Kampanya", "Harcama (TL)", "Dönüşümler", "CPL"]].to_dict(orient="records"), "toplam_harcama": df_rep["Harcama (TL)"].sum(), "toplam_lead": df_rep["Dönüşümler"].sum()}
                    st.write("🧠 GPT-4o derin analiz yapıyor...")
                    report = generate_ai_summary(openai_key, str(ctx))
                    st.session_state.meta_ai_report = report
                    status.update(label="✅ Analiz Tamam!", state="complete", expanded=False)
    if st.session_state.meta_ai_report:
        st.markdown("""<style>.report-box{background:rgba(255,255,255,0.05);padding:25px;border-radius:12px;border:1px solid rgba(255,255,255,0.1);margin-top:20px;}</style>""", unsafe_allow_html=True)
        st.markdown(f'<div class="report-box">{st.session_state.meta_ai_report}</div>', unsafe_allow_html=True)
        st.download_button("📩 İndir (.md)", st.session_state.meta_ai_report, file_name=f"Meta_Ads_AI_Rapor_{datetime.date.today()}.md")

elif selected_platform == "Meta Ads" and mode == "🤖 AI Aksiyon Merkezi":
    st.header("🤖 Meta Ads — AI Aksiyon Merkezi & Karar Destek")
    if not meta_token or not meta_account_id:
        st.warning("⚠️ Meta Token veya Account ID eksik."); st.stop()
    if not openai_key:
        st.error("⚠️ OpenAI API Anahtarı eksik! Soldaki menüden ekleyin."); st.stop()

    TARGET_CPL_META = st.sidebar.number_input("🎯 Hedef CPL (₺)", value=TARGET_CPL, min_value=10, step=10, key="meta_target_cpl")

    if "meta_ai_actions" not in st.session_state:
        st.session_state.meta_ai_actions = None

    if st.button("🚀 Son 7 Günü Analiz Et (GPT-4o)", type="primary"):
        with st.status("🔍 Meta verileri ve AI analizi çalışıyor...", expanded=True) as status:
            st.write("📊 Son 7 günlük kampanya verileri çekiliyor...")

            # Fetch last 7 days at campaign level
            FacebookAdsApi.init(access_token=meta_token)
            acc_ai = AdAccount(meta_account_id)
            seven_ago = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
            today_str = datetime.date.today().strftime("%Y-%m-%d")

            try:
                ai_insights = list(acc_ai.get_insights(
                    fields=['campaign_name', 'spend', 'impressions', 'clicks', 'frequency', 'actions', 'inline_link_click_ctr'],
                    params={
                        'level': 'campaign',
                        'time_range': {'since': seven_ago, 'until': today_str}
                    }
                ))
            except Exception as e:
                st.error(f"Meta API Hatası: {e}"); st.stop()

            campaigns_summary = []
            for ins in ai_insights:
                leads = sum(int(a.get('value', 0)) for a in ins.get('actions', [])
                            if a['action_type'] in ['lead', 'onsite_conversion.messaging_first_reply'])
                spend = float(ins.get('spend', 0))
                cpl = round(spend / leads, 0) if leads > 0 else None
                campaigns_summary.append({
                    "kampanya": ins.get('campaign_name', 'N/A'),
                    "harcama_tl": round(spend, 0),
                    "lead": leads,
                    "cpl_tl": cpl,
                    "frekans": round(float(ins.get('frequency', 1)), 2),
                    "link_ctr_pct": round(float(ins.get('inline_link_click_ctr', 0)), 2),
                })

            st.write(f"📋 {len(campaigns_summary)} kampanya bulundu. GPT-4o analiz yapıyor...")

            # Build prompt
            data_str = "\n".join([
                f"- {c['kampanya']}: Harcama=₺{c['harcama_tl']}, Lead={c['lead']}, "
                f"CPL={'₺'+str(c['cpl_tl']) if c['cpl_tl'] else 'Yok (0 lead)'}, "
                f"Frekans={c['frekans']}, CTR=%{c['link_ctr_pct']}"
                for c in campaigns_summary
            ])

            prompt = f"""Sen bir Meta Ads optimizasyon uzmanısın. Aşağıdaki son 7 günlük kampanya verilerini analiz et. 
Hedef CPL: ₺{TARGET_CPL_META}

Veri:
{data_str}

GÖREV: Her kampanya için net, emir kipli bir karar ver. Cevabını MUTLAKA şu JSON formatında ver (başka şey yazma):
{{
  "durdurulmali": [
    {{"kampanya": "...", "neden": "...", "aciliyet": "Yuksek/Orta"}}
  ],
  "olceklenmeli": [
    {{"kampanya": "...", "oneri": "...", "artis_orani": "%20 gibi"}}
  ]
}}

Kurallar:
- CPL hedefin 1.5x üzerindeyse VE en az ₺50 harcadıysa → durdurulmalı
- CPL hedefin altındaysa VE lead sayısı > 0 → ölçeklenmeli  
- 0 lead ama düşük harcamalıysa (< ₺100) → ne öner ne durdur, listeye alma
- Sadece bu JSON'u yaz, başka açıklama ekleme"""

            try:
                client_ai = OpenAI(api_key=openai_key)
                resp = client_ai.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    response_format={"type": "json_object"}
                )
                import json
                ai_result = json.loads(resp.choices[0].message.content)
                st.session_state.meta_ai_actions = ai_result
                status.update(label="✅ Analiz Tamamlandı!", state="complete", expanded=False)
            except Exception as e:
                st.error(f"OpenAI Hatası: {e}"); st.stop()

    # ── Sonuçları Göster ──────────────────────────────────────────────
    if st.session_state.meta_ai_actions:
        result = st.session_state.meta_ai_actions
        dur_list  = result.get("durdurulmali", [])
        olc_list  = result.get("olceklenmeli", [])

        tab_dur, tab_olc = st.tabs([
            f"🛑 Durdurulması Gerekenler ({len(dur_list)})",
            f"📈 Ölçeklenmesi Gerekenler ({len(olc_list)})"
        ])

        with tab_dur:
            if not dur_list:
                st.success("✅ Durdurulması önerilen kampanya yok — hepsi hedef dahilinde!")
            for item in dur_list:
                with st.container(border=True):
                    col_ic, col_btn = st.columns([4, 1])
                    with col_ic:
                        aciliyet = item.get("aciliyet", "Orta")
                        icon = "🚨" if aciliyet == "Yuksek" else "⚠️"
                        st.markdown(f"**{icon} {item.get('kampanya', '?')}**")
                        st.markdown(f"📝 {item.get('neden', '')}")
                        st.caption(f"Aciliyet: **{aciliyet}**")
                    with col_btn:
                        if st.button("🛑 Durdur *(sim)*", key=f"dur_{item.get('kampanya','')}"):
                            st.toast(f"✅ '{item.get('kampanya')}' durduruldu (simülasyon).", icon="🛑")

        with tab_olc:
            if not olc_list:
                st.info("ℹ️ Ölçeklenmesi önerilen kampanya bulunamadı.")
            for item in olc_list:
                with st.container(border=True):
                    col_ic, col_btn = st.columns([4, 1])
                    with col_ic:
                        st.markdown(f"**📈 {item.get('kampanya', '?')}**")
                        st.markdown(f"💡 {item.get('oneri', '')}")
                        st.caption(f"Önerilen artış: **{item.get('artis_orani', '?')}**")
                    with col_btn:
                        if st.button("📈 Onayla *(sim)*", key=f"olc_{item.get('kampanya','')}"):
                            st.toast(f"✅ '{item.get('kampanya')}' bütçesi artırıldı (simülasyon).", icon="📈")

        st.caption(f"🕒 Analiz tarihi: {datetime.date.today()} — Son 7 günlük veri baz alındı.")

else: # Google Karşılaştırmalı Modlar
    if mode == "🕰️ Tarihsel Karşılaştırma": st.header("🕰️ İki Dönem Karşılaştırması")
    elif mode == "📈 Detaylı Zaman Analizi": st.header("📈 Detaylı Zaman Analizi")
    else: st.header("🤖 AI Strateji Raporu")

    with st.expander("ℹ️ Yöneticiler İçin Hızlı Rehber (Terimler Sözlüğü)"):
        st.markdown("""
        **Sistem Nasıl Çalışır?** Bu sayfa, seçtiğiniz iki farklı tarih aralığındaki reklam performansınızı kıyaslar.
        *   **Dönüşüm (CV):** Potansiyel Müşteri (Lead). Form doldurma, WhatsApp'a tıklama vb.
        *   **CVR:** Sitemize giren her 100 kişiden kaçı form doldurdu? (Verimlilik oranı).
        *   **CPA / CPL:** Bize 1 adet potansiyel müşteri bulmanın maliyeti nedir? **Düşükse o kadar iyidir.**
        *   **Impression Share (IS):** Pazardaki potansiyel müşterilerin yüzde kaçına reklamımızı gösterebildik?
        """)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.subheader("1. Dönem (Geçmiş)")
        d1_s = st.date_input("Başlangıç (1)", datetime.date(2026, 1, 1))
        d1_e = st.date_input("Bitiş (1)", datetime.date(2026, 1, 31))
    with col_d2:
        st.subheader("2. Dönem (Güncel)")
        d2_s = st.date_input("Başlangıç (2)", datetime.date(2026, 2, 1))
        d2_e = st.date_input("Bitiş (2)", datetime.date.today())

    with st.spinner("6 derin analiz sorgusu çalışıyor, lütfen bekleyin (~30 sn)..."):
        try:
            pre_camp = get_campaigns(cid, d1_s.strftime("%Y-%m-%d"), d1_e.strftime("%Y-%m-%d"))
            post_camp = get_campaigns(cid, d2_s.strftime("%Y-%m-%d"), d2_e.strftime("%Y-%m-%d"))
            pre_mt = get_match_types(cid, d1_s.strftime("%Y-%m-%d"), d1_e.strftime("%Y-%m-%d"))
            post_mt = get_match_types(cid, d2_s.strftime("%Y-%m-%d"), d2_e.strftime("%Y-%m-%d"))
            ts = get_time_series_data(cid, d1_s.strftime("%Y-%m-%d"), d2_e.strftime("%Y-%m-%d"))
        except Exception as e:
            st.error(f"API Hatası: {e}"); st.stop()

    if pre_camp.empty or post_camp.empty:
        st.warning("Seçilen tarihler için yeterli veri bulunamadı."); st.stop()

    # Disambiguate for chart clarity
    pre_camp_labeled = pre_camp.copy(); pre_camp_labeled["Kampanya"] = pre_camp_labeled["Kampanya"] + " (1. Dönem)"
    post_camp_labeled = post_camp.copy(); post_camp_labeled["Kampanya"] = post_camp_labeled["Kampanya"] + " (2. Dönem)"

    # Basic Metrics
    pre_cost, post_cost = pre_camp["Harcama (TL)"].sum(), post_camp["Harcama (TL)"].sum()
    pre_conv, post_conv = pre_camp["Dönüşümler"].sum(), post_camp["Dönüşümler"].sum()
    pre_clicks, post_clicks = pre_camp["Clicks"].sum(), post_camp["Clicks"].sum()
    
    pre_cvr = round(pre_conv / pre_clicks * 100, 1) if pre_clicks > 0 else 0
    post_cvr = round(post_conv / post_clicks * 100, 1) if post_clicks > 0 else 0
    pre_cpl = round(pre_cost / pre_conv, 0) if pre_conv > 0 else 0
    post_cpl = round(post_cost / post_conv, 0) if post_conv > 0 else 0
    pre_cpc = round(pre_cost / pre_clicks, 0) if pre_clicks > 0 else 0
    post_cpc = round(post_cost / post_clicks, 0) if post_clicks > 0 else 0
    cpl_gap = round(post_cpl - TARGET_CPL, 0)

    # Session State for Micro Insights
    for key in ["ai_kpi", "ai_tab1", "ai_tab2", "ai_tab3", "ai_tab4"]:
        if key not in st.session_state: st.session_state[key] = None

    # 📊 KPI SECTION
    st.markdown("### 📊 Temel Performans Göstergeleri (KPI)")
    
    if st.button("✨ KPI Analizi Yap", key="btn_kpi"):
        if not openai_key: st.error("API Anahtarı gerekli!")
        else:
            with st.status("📊 KPI verileri analiz ediliyor...") as s:
                ctx = {"Dönem 1 Harcama": pre_cost, "Dönem 2 Harcama": post_cost, "Dönem 1 CPL": pre_cpl, "Dönem 2 CPL": post_cpl}
                st.session_state.ai_kpi = get_ai_micro_insight(openai_key, "Temel KPI Değişimi", ctx)
                s.update(label="✅ KPI Analizi Tamam!", state="complete")
    
    if st.session_state.ai_kpi:
        st.info(f"💡 **AI Notu:** {st.session_state.ai_kpi}")

    st.subheader("⬅️ 1. Dönem (Geçmiş / Eski Sistem)")
    ck1, ck2, ck3, ck4 = st.columns(4)
    ck1.metric("Harcama (1)", f"₺{pre_cost:,.0f}", help="Geçmiş dönem toplam harcama.")
    ck2.metric("Dönüşüm (1)", f"{pre_conv:,.0f}", help="Sistem hatası nedeniyle sahte/gerçek tüm dönüşümler.")
    ck3.metric("Tıklama (1)", f"{pre_clicks:,.0f}")
    ck4.metric("CVR (1)", f"%{pre_cvr:.1f}")

    st.subheader("➡️ 2. Dönem (Güncel / Yeni Sistem)")
    kr1, kr2, kr3, kr4 = st.columns(4)
    kr1.metric("Harcama (2)", f"₺{post_cost:,.0f}", f"Δ: {((post_cost-pre_cost)/pre_cost*100):+.1f}%" if pre_cost>0 else None)
    kr2.metric("Dönüşüm (2)", f"{post_conv:,.0f}", f"{post_conv - pre_conv:+.0f} Fark", help="Sadece gerçek form/WhatsApp başvuru sayısı.")
    kr3.metric("Tıklama (2)", f"{post_clicks:,.0f}", f"{post_clicks - pre_clicks:+.0f} Fark")
    kr4.metric("CVR (2)", f"%{post_cvr:.1f}", f"{post_cvr - pre_cvr:+.1f}%")

    st.write("") 
    km1, km2, km3 = st.columns(3)
    km1.metric("CPA/CPL (Müşteri Başı)", f"₺{post_cpl:,.0f}", f"Eski: ₺{pre_cpl:,.0f}", delta_color="inverse" if post_cpl > pre_cpl else "normal")
    km2.metric("Ort. TBM/CPC", f"₺{post_cpc:,.0f}", f"Eski: ₺{pre_cpc:,.0f}", delta_color="inverse" if post_cpc > pre_cpc else "normal")
    km3.metric("Hedef CPL Sapması", f"₺{cpl_gap:,.0f}", "Hedefe göre pahalı" if cpl_gap > 0 else "Hedef altında ✅", delta_color="inverse" if cpl_gap > 0 else "normal")

    st.divider()

    if mode == "🕰️ Tarihsel Karşılaştırma":
        verdict_color = "🔴" if cpl_gap > 200 else ("🟡" if cpl_gap > 0 else "🟢")
        st.info(f"""
        **{verdict_color} Uzman AI Olarak Yönetici Özetim:**
        Bu analiz, **"Geçmişte çok müşteri geliyordu, şimdi azaldı"** yanılgısını gidermek ve gerçek verimliliği göstermek için hazırlanmıştır. 
        Geçmiş dönemlerde (1. Dönem) dönüşüm oranınız **%{pre_cvr:.1f}** gibi yüksek görünüyordu; sebebi "sayfa kaydırma" gibi değersiz hareketlerin dönüşüm sayılmasıydı. 
        Güncel dönemde (2. Dönem) ise sadece **gerçek form dolduran / WhatsApp'tan yazanlar** sayılıyor.
        
        **Durum:** 2. Dönem maliyetiniz (**₺{post_cpl:,.0f}**) hedefimiz olan **₺{TARGET_CPL}**'un {"**üzerinde.** Kaliteli dönüşümlere odaklandığınız için birim fiyat arttı, optimizasyon sürüyor." if cpl_gap > 0 else "**altında ✅.** Reklamlarınız şu an mükemmel verimlilikle çalışıyor."}
        """)

        st.divider()
        tab1, tab2, tab1_alt, tab3, tab4 = st.tabs(["📋 Kampanya Kırılımı", "📅 Zaman Trendi", "📊 Dönem Özeti", "🔗 Eşleme Türü", "🔭 Impression Share"])

        with tab1:
            st.subheader("📋 Hangi Kampanya Ne Kadar Harcadı?")
            
            # Table Legend
            st.markdown("""
            <div style="display: flex; justify-content: flex-end; gap: 20px; font-size: 0.9em; margin-bottom: 10px;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <div style="width: 15px; height: 15px; background-color: rgba(71, 85, 105, 0.2); border: 1px solid rgba(255,255,255,0.1);"></div>
                    <span>1. Dönem (Geçmiş)</span>
                </div>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <div style="width: 15px; height: 15px; background-color: rgba(30, 64, 175, 0.3); border: 1px solid rgba(255,255,255,0.1);"></div>
                    <span>2. Dönem (Güncel)</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            pre_camp_style = pre_camp_labeled.copy(); pre_camp_style["Dönem_ID"] = 1
            post_camp_style = post_camp_labeled.copy(); post_camp_style["Dönem_ID"] = 2
            
            mg = pd.concat([pre_camp_style, post_camp_style], ignore_index=True)
            mg["CPL (₺)"] = (mg["Harcama (TL)"] / mg["Dönüşümler"]).replace([float("inf")], 0).round(0)
            mg["TBM (₺)"] = (mg["Harcama (TL)"] / mg["Clicks"]).replace([float("inf")], 0).round(2)
            
            def style_rows(row):
                if row["Dönem_ID"] == 1:
                    return ['background-color: rgba(71, 85, 105, 0.15)'] * len(row)
                return ['background-color: rgba(30, 64, 175, 0.25)'] * len(row)

            styled_mg = mg.sort_values("Harcama (TL)", ascending=False).style.apply(style_rows, axis=1).format({
                "Harcama (TL)": "₺{:,.0f}",
                "Dönüşümler": "{:,.0f}",
                "Clicks": "{:,.0f}",
                "Impressions": "{:,.0f}",
                "IS (%)": "{:.1f}%",
                "CPL (₺)": "₺{:,.0f}",
                "TBM (₺)": "₺{:.2f}"
            })
            st.dataframe(styled_mg, use_container_width=True, hide_index=True, column_order=["Kampanya", "Harcama (TL)", "Dönüşümler", "Clicks", "Impressions", "IS (%)", "CPL (₺)", "TBM (₺)"])
            
            st.markdown("#### 1. Dönem Dağılımı (Geçmiş)")
            c1_1, c1_2, c1_3 = st.columns(3)
            c1_1.plotly_chart(px.pie(pre_camp, values="Harcama (TL)", names="Kampanya", title="Harcama Dağılımı (1)"), use_container_width=True)
            c1_2.plotly_chart(px.pie(pre_camp, values="Dönüşümler", names="Kampanya", title="Lead Dağılımı (1)"), use_container_width=True)
            c1_3.plotly_chart(px.pie(pre_camp, values="Clicks", names="Kampanya", title="Tık Dağılımı (1)"), use_container_width=True)
            
            st.markdown("#### 2. Dönem Dağılımı (Güncel)")
            c2_1, c2_2, c2_3 = st.columns(3)
            c2_1.plotly_chart(px.pie(post_camp, values="Harcama (TL)", names="Kampanya", title="Harcama Dağılımı (2)"), use_container_width=True)
            c2_2.plotly_chart(px.pie(post_camp, values="Dönüşümler", names="Kampanya", title="Lead Dağılımı (2)"), use_container_width=True)
            c2_3.plotly_chart(px.pie(post_camp, values="Clicks", names="Kampanya", title="Tık Dağılımı (2)"), use_container_width=True)

            st.divider()
            st.subheader("🔍 Dönemler Arası Direkt Kıyaslama")
            # Prepare comparison data
            comp_df = pd.merge(
                pre_camp[["Kampanya", "Harcama (TL)", "Dönüşümler"]].rename(columns={"Harcama (TL)": "Harcama (1)", "Dönüşümler": "Dönüşüm (1)"}),
                post_camp[["Kampanya", "Harcama (TL)", "Dönüşümler"]].rename(columns={"Harcama (TL)": "Harcama (2)", "Dönüşümler": "Dönüşüm (2)"}),
                on="Kampanya", how="outer"
            ).fillna(0)
            
            comp_df["CPL (1)"] = (comp_df["Harcama (1)"] / comp_df["Dönüşüm (1)"]).replace([float("inf")], 0).round(0)
            comp_df["CPL (2)"] = (comp_df["Harcama (2)"] / comp_df["Dönüşüm (2)"]).replace([float("inf")], 0).round(0)

            comp_melted_cost = comp_df.melt(id_vars="Kampanya", value_vars=["Harcama (1)", "Harcama (2)"], var_name="Dönem", value_name="Harcama (₺)")
            comp_melted_conv = comp_df.melt(id_vars="Kampanya", value_vars=["Dönüşüm (1)", "Dönüşüm (2)"], var_name="Dönem", value_name="Müşteri Sayısı")
            comp_melted_cpl = comp_df.melt(id_vars="Kampanya", value_vars=["CPL (1)", "CPL (2)"], var_name="Dönem", value_name="Birim Maliyet (₺)")

            st.plotly_chart(px.bar(comp_melted_cost, x="Kampanya", y="Harcama (₺)", color="Dönem", barmode="group", title="Bütçe Kullanımı Kıyaslaması", color_discrete_map={"Harcama (1)": "#94a3b8", "Harcama (2)": "#3b82f6"}), use_container_width=True)
            
            cc1, cc2 = st.columns(2)
            cc1.plotly_chart(px.bar(comp_melted_conv, x="Kampanya", y="Müşteri Sayısı", color="Dönem", barmode="group", title="Müşteri (Lead) Kazanımı Kıyas", color_discrete_map={"Dönüşüm (1)": "#94a3b8", "Dönüşüm (2)": "#10b981"}), use_container_width=True)
            cc2.plotly_chart(px.bar(comp_melted_cpl, x="Birim Maliyet (₺)", y="Kampanya", color="Dönem", barmode="group", orientation="h", title="Verimlilik (CPL) Kıyas", color_discrete_map={"CPL (1)": "#94a3b8", "CPL (2)": "#ef4444"}), use_container_width=True)

            if st.button("✨ Kampanya Analizi Yap", key="btn_tab1"):
                if not openai_key: st.error("API Anahtarı gerekli!")
                else:
                    with st.status("📋 Kampanya verileri inceleniyor...") as s:
                        ctx = comp_df[["Kampanya", "Harcama (1)", "Harcama (2)", "CPL (1)", "CPL (2)"]].to_dict(orient="records")
                        st.session_state.ai_tab1 = get_ai_micro_insight(openai_key, "Kampanya Bazlı Performans Kayması", ctx)
                        s.update(label="✅ Analiz Tamam!", state="complete")
            if st.session_state.ai_tab1: st.success(f"🤖 **Strateji:** {st.session_state.ai_tab1}")

        with tab2:
            st.subheader("Büyük Resim: Aylık Performans Trendi")
            if not ts.empty:
                ts_m = ts.copy()
                ts_m["Period"] = ts_m["Tarih"].dt.to_period("M")
                ts_m = ts_m.groupby("Period", as_index=False).sum(numeric_only=True).sort_values("Period")
                ts_m["Ay"] = ts_m["Period"].dt.strftime("%b %Y")
                
                fig = go.Figure()
                fig.add_trace(go.Bar(x=ts_m["Ay"], y=ts_m["Harcama (TL)"], name="Harcama", marker_color="#3b82f6"))
                fig.add_trace(go.Scatter(x=ts_m["Ay"], y=ts_m["Dönüşümler"], name="Dönüşüm", yaxis="y2", line=dict(color="#10b981", width=4)))
                fig.update_layout(yaxis2=dict(overlaying="y", side="right"), hovermode="x unified", title="Harcama / Dönüşüm Kıyası")
                st.plotly_chart(fig, use_container_width=True)

                ts_m["CVR (%)"] = (ts_m["Dönüşümler"] / ts_m["Clicks"] * 100).fillna(0).round(1)
                st.plotly_chart(px.line(ts_m, x="Ay", y="CVR (%)", markers=True, title="Verimlilik (CVR) Trendi", color_discrete_sequence=["#f59e0b"]), use_container_width=True)

                if st.button("✨ Trend Analizi Yap", key="btn_tab2"):
                    if not openai_key: st.error("API Anahtarı gerekli!")
                    else:
                        with st.status("📅 Trendler okunuyor...") as s:
                            ctx = ts_m[["Ay", "Harcama (TL)", "Dönüşümler", "CVR (%)"]].to_dict(orient="records")
                            st.session_state.ai_tab2 = get_ai_micro_insight(openai_key, "Zaman Trendi ve Mevsimsellik", ctx)
                            s.update(label="✅ Analiz Tamam!", state="complete")
                if st.session_state.ai_tab2: st.warning(f"📈 **Trend Notu:** {st.session_state.ai_tab2}")

        with tab1_alt:
            st.subheader("🏢 Genel Özet Karşılaştırması")
            summary_df = pd.DataFrame([
                {"Dönem": "1. Dönem (Geçmiş)", "Harcama": pre_cost, "Dönüşüm": pre_conv, "CPL": pre_cpl, "CVR": pre_cvr, "Tıklama": pre_clicks, "TBM": pre_cost/pre_clicks if pre_clicks > 0 else 0},
                {"Dönem": "2. Dönem (Güncel)", "Harcama": post_cost, "Dönüşüm": post_conv, "CPL": post_cpl, "CVR": post_cvr, "Tıklama": post_clicks, "TBM": post_cost/post_clicks if post_clicks > 0 else 0}
            ])
            st.dataframe(
                summary_df.style.format({
                    "Harcama": "₺{:,.0f}",
                    "Dönüşüm": "{:,.0f}",
                    "CPL": "₺{:,.0f}",
                    "CVR": "%{:.1f}",
                    "Tıklama": "{:,.0f}",
                    "TBM": "₺{:.2f}"
                }), 
                use_container_width=True, 
                hide_index=True
            )
            sc1, sc2 = st.columns(2)
            sc1.plotly_chart(px.bar(summary_df, x="Dönem", y="Harcama", color="Dönem", text_auto=True, title="Toplam Harcama (₺)"), use_container_width=True)
            sc2.plotly_chart(px.bar(summary_df, x="Dönem", y="CPL", color="Dönem", text_auto=True, title="Lead Başı Maliyet (Hedef: 500)"), use_container_width=True)

        with tab3:
            st.subheader("Kelime Kalitesi ve Eşleme Türü Analizi")
            pre_mt["Dönem"] = "1. Dönem"; post_mt["Dönem"] = "2. Dönem"
            df_mt = pd.concat([pre_mt, post_mt])
            st.plotly_chart(px.bar(df_mt, x="Eşleme Türü", y="Harcama (TL)", color="Dönem", barmode="group", title="Eşleme Türüne Göre Para Dağılımı"), use_container_width=True)
            st.warning("💡 **Uzman Notu:** Broad Match (Geniş Eşleme) harcaması yüksekse, negatif anahtar kelime çalışmasını artırmak kritik önemdedir.")
            
            if st.button("✨ Kelime Kalite Analizi Yap", key="btn_tab3"):
                if not openai_key: st.error("API Anahtarı gerekli!")
                else:
                    with st.status("🔗 Kelime yapıları inceleniyor...") as s:
                        ctx = df_mt.to_dict(orient="records")
                        st.session_state.ai_tab3 = get_ai_micro_insight(openai_key, "Anahtar Kelime Eşleme Stratejisi", ctx)
                        s.update(label="✅ Analiz Tamam!", state="complete")
            if st.session_state.ai_tab3: st.info(f"🔍 **Kelime Notu:** {st.session_state.ai_tab3}")

        with tab4:
            st.subheader("Gösterim Payı (Impression Share)")
            is_data = post_camp[post_camp["IS (%)"] > 0][["Kampanya", "IS (%)"]].sort_values("IS (%)", ascending=False)
            if not is_data.empty:
                st.plotly_chart(px.bar(is_data, x="Kampanya", y="IS (%)", color="IS (%)", text_auto=True, labels={"IS (%)": "Gösterim Payı (%)"}), use_container_width=True)
                avg_is = is_data["IS (%)"].mean()
                st.metric("Ortalama Pazar Payı", f"%{avg_is:.1f}")
                if avg_is < 30: st.error("⚠️ Pazar payınız %30'un altında. Bütçe veya teklif artışı düşünülmeli.")
                
                if st.button("✨ Pazar Payı Analizi Yap", key="btn_tab4"):
                    if not openai_key: st.error("API Anahtarı gerekli!")
                    else:
                        with st.status("🔭 Pazar verileri analiz ediliyor...") as s:
                            ctx = is_data.to_dict(orient="records")
                            st.session_state.ai_tab4 = get_ai_micro_insight(openai_key, "Gösterim Payı ve Görünürlük", ctx)
                            s.update(label="✅ Analiz Tamam!", state="complete")
                if st.session_state.ai_tab4: st.info(f"🔭 **Pazar Notu:** {st.session_state.ai_tab4}")
            else: st.info("Gösterim payı verisi bulunamadı.")

    elif mode == "📈 Detaylı Zaman Analizi":
        st.subheader("📊 Performans Tomografisi")
        if not ts.empty:
            freq = st.radio("📊 Grafik Kırılımı Seçin", ["Günlük", "Haftalık", "Aylık"], horizontal=True)
            ts_p = ts.copy()
            if freq == "Günlük":
                ts_p = ts_p.groupby("Tarih", as_index=False).sum(numeric_only=True).sort_values("Tarih")
                ts_p["L"] = ts_p["Tarih"].dt.strftime("%d %b")
            elif freq == "Haftalık":
                ts_p["Period"] = ts_p["Tarih"].dt.to_period("W")
                ts_p = ts_p.groupby("Period", as_index=False).sum(numeric_only=True).sort_values("Period")
                ts_p["L"] = ts_p["Period"].apply(lambda r: r.start_time).dt.strftime("%W. Hafta (%d %b)")
            else:
                ts_p["Period"] = ts_p["Tarih"].dt.to_period("M")
                ts_p = ts_p.groupby("Period", as_index=False).sum(numeric_only=True).sort_values("Period")
                ts_p["L"] = ts_p["Period"].dt.strftime("%b %Y")

            fig = go.Figure()
            fig.add_trace(go.Bar(x=ts_p["L"], y=ts_p["Harcama (TL)"], name="Harcama (₺)", marker_color="#3b82f6", opacity=0.7))
            fig.add_trace(go.Scatter(x=ts_p["L"], y=ts_p["Dönüşümler"], name="Dönüşüm (Lead)", yaxis="y2", mode="lines+markers+text", text=ts_p["Dönüşümler"], textposition="top center", line=dict(color="#10b981", width=4)))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right"), hovermode="x unified", legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)

            ts_p["CPL"] = (ts_p["Harcama (TL)"] / ts_p["Dönüşümler"]).replace([float("inf")], 0).round(0)
            ts_p["CVR (%)"] = (ts_p["Dönüşümler"] / ts_p["Clicks"] * 100).fillna(0).round(1)
            c_tr1, c_tr2 = st.columns(2)
            c_tr1.plotly_chart(px.line(ts_p, x="L", y="CPL", markers=True, title="CPA (CPL) Trendi", color_discrete_sequence=["#ef4444"]), use_container_width=True)
            c_tr2.plotly_chart(px.line(ts_p, x="L", y="CVR (%)", markers=True, title="CVR Trendi", color_discrete_sequence=["#f59e0b"]), use_container_width=True)
            st.info("💡 **Görsel Analiz Notu:** Harcama sabitken yeşil çizginin (dönüşüm) yükselmesi verimlilik artışını gösterir.")
        else: st.info("Zaman serisi verisi yok.")

    elif mode == "🤖 AI Strateji Raporu":
        st.subheader("GPT-4o Analizi")
        if "ai_report" not in st.session_state: st.session_state.ai_report = None
        if st.button("🚀 Raporu Başlat"):
            if not openai_key: st.error("API Anahtarı eksik!")
            else:
                with st.status("🔍 Veriler analiz ediliyor...", expanded=True) as status:
                    st.write("📊 Metrikler toplanıyor...")
                    ctx = {"pre": pre_cost, "post": post_cost, "cpl_pre": pre_cpl, "cpl_post": post_cpl, "kampanya_detay": post_camp[["Kampanya", "Harcama (TL)", "Dönüşümler"]].to_dict(orient="records")}
                    st.write("🧠 GPT-4o derin analiz yapıyor (20 sn)...")
                    report = generate_ai_summary(openai_key, str(ctx))
                    st.session_state.ai_report = report
                    status.update(label="✅ Analiz Tamam!", state="complete", expanded=False)

        if st.session_state.ai_report:
            st.markdown("""<style>.report-box{background:rgba(255,255,255,0.05);padding:25px;border-radius:12px;border:1px solid rgba(255,255,255,0.1);margin-top:20px;}</style>""", unsafe_allow_html=True)
            st.markdown(f'<div class="report-box">{st.session_state.ai_report}</div>', unsafe_allow_html=True)
            st.download_button("📩 İndir (.md)", st.session_state.ai_report, file_name=f"Google_Ads_AI_Rapor_{datetime.date.today()}.md")

