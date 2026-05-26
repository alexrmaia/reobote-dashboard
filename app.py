import streamlit as st
import requests
import pandas as pd
import altair as alt
from datetime import datetime, timedelta
import urllib.parse

# =========================
# CONFIG
# =========================
st.set_page_config(
    page_title="Reobote Imports",
    page_icon="🛒",
    layout="wide",
)

CLIENT_ID     = st.secrets["ML_CLIENT_ID"]
CLIENT_SECRET = st.secrets["ML_CLIENT_SECRET"]
REDIRECT_URI  = st.secrets["ML_REDIRECT_URI"]
ML_AUTH_URL   = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL  = "https://api.mercadolibre.com/oauth/token"
ML_API_BASE   = "https://api.mercadolibre.com"

# Session com retry para conexões instáveis
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def make_session():
    s = requests.Session()
    retry = Retry(connect=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

# =========================
# OAUTH HELPERS
# =========================
def get_auth_url():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
    }
    return f"{ML_AUTH_URL}?{urllib.parse.urlencode(params)}"

def exchange_code_for_token(code: str) -> dict:
    try:
        session = make_session()
        resp = session.post(
            ML_TOKEN_URL,
            data={
                "grant_type":    "authorization_code",
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  REDIRECT_URI,
            },
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def refresh_access_token(refresh_token: str) -> dict:
    resp = requests.post(
        ML_TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token,
        },
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    return resp.json()

def get_token() -> str | None:
    """Retorna access_token válido, renovando se necessário."""
    if "access_token" not in st.session_state:
        return None
    expires_at = st.session_state.get("expires_at", 0)
    if datetime.now().timestamp() >= expires_at - 60:
        # Token expirado — renova
        refresh_token = st.session_state.get("refresh_token")
        if not refresh_token:
            return None
        data = refresh_access_token(refresh_token)
        if "access_token" not in data:
            return None
        save_token(data)
    return st.session_state["access_token"]

def save_token(data: dict):
    st.session_state["access_token"]  = data["access_token"]
    st.session_state["refresh_token"] = data.get("refresh_token", "")
    st.session_state["user_id"]       = data.get("user_id", "")
    st.session_state["expires_at"]    = datetime.now().timestamp() + data.get("expires_in", 21600)

# =========================
# API HELPERS
# =========================
def api_get(path: str, params: dict = None) -> dict | list:
    token = get_token()
    if not token:
        return {}
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{ML_API_BASE}{path}", headers=headers, params=params or {}, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return {}

@st.cache_data(ttl=300, show_spinner=False)
def get_user_info(user_id: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{ML_API_BASE}/users/{user_id}", headers=headers)
    return resp.json() if resp.status_code == 200 else {}

@st.cache_data(ttl=300, show_spinner=False)
def get_orders(user_id: str, token: str, date_from: str, date_to: str) -> list:
    """Busca todas as ordens do período."""
    headers = {"Authorization": f"Bearer {token}"}
    orders = []
    offset = 0
    limit  = 50

    while True:
        params = {
            "seller": user_id,
            "order.date_created.from": date_from,
            "order.date_created.to":   date_to,
            "sort":   "date_desc",
            "offset": offset,
            "limit":  limit,
        }
        resp = requests.get(f"{ML_API_BASE}/orders/search", headers=headers, params=params)
        if resp.status_code != 200:
            break
        data = resp.json()
        results = data.get("results", [])
        orders.extend(results)
        paging = data.get("paging", {})
        total  = paging.get("total", 0)
        offset += limit
        if offset >= total or not results:
            break

    return orders

def parse_orders(orders: list) -> pd.DataFrame:
    """Converte lista de ordens da API em DataFrame."""
    rows = []
    for order in orders:
        order_id    = order.get("id", "")
        status      = order.get("status", "")
        date_str    = order.get("date_created", "")
        date        = pd.to_datetime(date_str, errors="coerce")
        total_amount = float(order.get("total_amount", 0) or 0)
        paid_amount  = float(order.get("paid_amount", 0) or 0)

        for item in order.get("order_items", []):
            sku      = item.get("item", {}).get("seller_sku", "") or ""
            produto  = item.get("item", {}).get("title", "") or ""
            qty      = int(item.get("quantity", 1) or 1)
            unit_price = float(item.get("unit_price", 0) or 0)
            sale_fee   = float(item.get("sale_fee", 0) or 0)

            rows.append({
                "Venda":         str(order_id),
                "Data":          date,
                "Status":        status,
                "SKU":           sku.strip(),
                "Produto":       produto[:50],
                "Quantidade":    qty,
                "Receita Bruta": unit_price * qty,
                "Taxas ML":      abs(sale_fee),
                "Total ML":      paid_amount,
                "Cancelada":     status in ["cancelled"],
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df

# =========================
# ESTILO
# =========================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.main { background: #F5F7FB; }
.block-container { padding-top: 0 !important; max-width: 1380px !important; margin: 0 auto; }
header[data-testid="stHeader"] { display: none !important; }
#MainMenu { display: none !important; }
footer { display: none !important; }
.navbar {
    background: linear-gradient(90deg, #1E1040 0%, #2D1B69 50%, #1E1040 100%);
    padding: 0 32px; height: 52px; display: flex; align-items: center;
    box-shadow: 0 3px 16px rgba(109,40,217,.25); margin-bottom: 20px;
    border-radius: 0 0 12px 12px;
}
.navbar-name { color: white; font-size: 15px; font-weight: 900; letter-spacing: 1px; margin-left: 10px; }
.card { background: white; border-radius: 20px; padding: 28px 32px; margin-bottom: 20px;
        box-shadow: 0 2px 12px rgba(15,23,42,.06); border: 1px solid #E7ECF5; }
.hero { background: linear-gradient(135deg, #7C3AED 0%, #8B5CF6 45%, #6D28D9 100%);
        border-radius: 24px; padding: 34px 38px; color: white;
        box-shadow: 0 18px 45px rgba(124,58,237,.22); margin-bottom: 20px; }
.hero-title { font-size: 48px; font-weight: 900; line-height: 1; margin: 0; letter-spacing: -1.6px; }
.hero-value { font-size: 56px; font-weight: 900; line-height: 1; letter-spacing: -2px; }
.hero-label { font-size: 14px; font-weight: 700; opacity: .85; margin-bottom: 4px; }
.metric-card { background: white; border-radius: 16px; padding: 20px 24px;
               border: 1px solid #E7ECF5; box-shadow: 0 2px 8px rgba(15,23,42,.05); }
.metric-label { font-size: 12px; font-weight: 800; color: #64748B; text-transform: uppercase; letter-spacing: .5px; }
.metric-value { font-size: 28px; font-weight: 900; color: #0F172A; margin-top: 4px; }
.login-card { background: white; border-radius: 24px; padding: 48px; max-width: 480px;
              margin: 80px auto; text-align: center; box-shadow: 0 8px 32px rgba(15,23,42,.1); }
</style>
""", unsafe_allow_html=True)

# =========================
# NAVBAR
# =========================
st.markdown("""
<div class="navbar">
    <span style="font-size:20px;">🛒</span>
    <span class="navbar-name">REOBOTE IMPORTS</span>
</div>
""", unsafe_allow_html=True)

# =========================
# AUTENTICAÇÃO
# =========================
# Captura code da URL (retorno do OAuth)
query_params = st.query_params
code = query_params.get("code", None)

if code and "access_token" not in st.session_state:
    with st.spinner("Conectando com o Mercado Livre..."):
        token_data = exchange_code_for_token(code)
        if "access_token" in token_data:
            save_token(token_data)
            st.query_params.clear()
            st.rerun()
        else:
            st.error(f"Erro na autenticação: {token_data}")
            st.info("Tente conectar novamente.")
            if st.button("Tentar novamente"):
                st.query_params.clear()
                st.rerun()
            st.stop()

# Não autenticado — tela de login
if "access_token" not in st.session_state:
    auth_url = get_auth_url()
    st.markdown(f"""
    <div class="login-card">
        <div style="font-size:48px;margin-bottom:16px;">🛒</div>
        <h2 style="font-weight:900;color:#1E1040;margin-bottom:8px;">Reobote Imports</h2>
        <p style="color:#64748B;margin-bottom:32px;">Conecte sua conta do Mercado Livre para acessar o dashboard.</p>
        <a href="{auth_url}" target="_self">
            <button style="background:linear-gradient(135deg,#7C3AED,#6D28D9);color:white;border:none;
                           border-radius:12px;padding:14px 32px;font-size:16px;font-weight:800;
                           cursor:pointer;width:100%;">
                🔗 Conectar com Mercado Livre
            </button>
        </a>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# =========================
# DASHBOARD AUTENTICADO
# =========================
token   = get_token()
user_id = st.session_state.get("user_id", "")
user    = get_user_info(str(user_id), token)
nickname = user.get("nickname", "Vendedor")

# Filtro de período
col_f1, col_f2, col_f3 = st.columns([2, 2, 1])
with col_f1:
    periodo = st.selectbox("Período", ["Hoje", "7 dias", "15 dias", "30 dias"], index=1)
with col_f3:
    if st.button("🔄 Atualizar"):
        st.cache_data.clear()
        st.rerun()

hoje = datetime.now()
if periodo == "Hoje":
    date_from = hoje.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
elif periodo == "7 dias":
    date_from = (hoje - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
elif periodo == "15 dias":
    date_from = (hoje - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
else:
    date_from = (hoje - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
date_to = hoje.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")

with st.spinner("Buscando vendas..."):
    orders = get_orders(str(user_id), token, date_from, date_to)

st.caption(f"Debug: user_id={user_id} | date_from={date_from} | orders={len(orders)}")

if not orders:
    st.info("Nenhuma venda encontrada no período.")
    st.stop()

df = parse_orders(orders)
if df.empty:
    st.info("Nenhuma venda encontrada no período.")
    st.stop()

# Métricas
aprovadas   = df[~df["Cancelada"]]
canceladas  = df[df["Cancelada"]]
faturamento = aprovadas["Receita Bruta"].sum()
tarifas     = aprovadas["Taxas ML"].sum()
qtd_vendas  = int(aprovadas["Quantidade"].sum())
qtd_cancel  = int(canceladas["Quantidade"].sum())

# Hero
st.markdown(f"""
<div class="hero">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:24px;">
        <div>
            <p style="opacity:.85;font-size:14px;font-weight:700;margin:0;">Olá, {nickname} 👋</p>
            <h1 class="hero-title">Dashboard</h1>
            <div style="background:rgba(255,255,255,.15);border-radius:999px;padding:8px 16px;
                        width:fit-content;margin-top:12px;font-size:13px;font-weight:800;">
                {periodo} • {hoje.strftime('%d/%m/%Y')}
            </div>
        </div>
        <div style="text-align:right;">
            <div class="hero-label">Faturamento</div>
            <div class="hero-value">R$ {faturamento:,.2f}</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# Cards
c1, c2, c3, c4 = st.columns(4)
def card(col, label, value, sub=""):
    col.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        {f'<div style="color:#64748B;font-size:13px;margin-top:4px;">{sub}</div>' if sub else ''}
    </div>
    """, unsafe_allow_html=True)

card(c1, "Tarifas ML",     f"R$ {tarifas:,.2f}",   f"{tarifas/faturamento*100:.1f}%" if faturamento else "")
card(c2, "Qtd Vendas",     f"{qtd_vendas} unid",    f"{len(aprovadas)} pedidos")
card(c3, "Ticket Médio",   f"R$ {faturamento/len(aprovadas):.2f}" if len(aprovadas) else "R$ 0", "por pedido")
card(c4, "Canceladas",     f"{qtd_cancel} unid",    f"{len(canceladas)} pedidos")

st.markdown("<br>", unsafe_allow_html=True)

# Gráfico de vendas por dia
st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown("**Vendas por dia**")
daily = aprovadas.copy()
daily["Dia"] = pd.to_datetime(daily["Data"]).dt.date
daily_agg = daily.groupby("Dia").agg({"Receita Bruta": "sum", "Quantidade": "sum"}).reset_index()
daily_agg["Dia"] = pd.to_datetime(daily_agg["Dia"])

line = alt.Chart(daily_agg).mark_area(
    interpolate="monotone", color="#7C3AED", opacity=0.15
).encode(
    x=alt.X("Dia:T", title=None),
    y=alt.Y("Receita Bruta:Q", title="Receita (R$)"),
) + alt.Chart(daily_agg).mark_line(
    interpolate="monotone", color="#7C3AED", strokeWidth=3,
    point=alt.OverlayMarkDef(filled=True, size=60, color="#7C3AED")
).encode(
    x=alt.X("Dia:T", title=None),
    y=alt.Y("Receita Bruta:Q"),
    tooltip=[
        alt.Tooltip("Dia:T", title="Data", format="%d/%m/%Y"),
        alt.Tooltip("Receita Bruta:Q", title="Receita R$", format=",.2f"),
        alt.Tooltip("Quantidade:Q", title="Qtd vendida", format=",.0f"),
    ]
)
st.altair_chart(line.properties(height=280), use_container_width=True)
st.markdown('</div>', unsafe_allow_html=True)

# Tabela de pedidos recentes
st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown("**Pedidos recentes**")
tabela = df[["Venda","Data","Status","SKU","Produto","Quantidade","Receita Bruta","Taxas ML"]].copy()
tabela["Data"] = pd.to_datetime(tabela["Data"]).dt.strftime("%d/%m/%Y %H:%M")
tabela["Receita Bruta"] = tabela["Receita Bruta"].apply(lambda x: f"R$ {x:,.2f}")
tabela["Taxas ML"] = tabela["Taxas ML"].apply(lambda x: f"R$ {x:,.2f}")
st.dataframe(tabela.rename(columns={
    "Venda": "N.º Venda", "Receita Bruta": "Receita", "Taxas ML": "Tarifa"
}), use_container_width=True, hide_index=True)
st.markdown('</div>', unsafe_allow_html=True)

# Logout
st.markdown("<br>", unsafe_allow_html=True)
if st.button("🔓 Desconectar"):
    for key in ["access_token","refresh_token","user_id","expires_at"]:
        st.session_state.pop(key, None)
    st.rerun()
