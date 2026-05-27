import streamlit as st
import requests
import pandas as pd
import altair as alt
from datetime import datetime, timedelta
import urllib.parse
from supabase import create_client, Client

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
SUPABASE_URL  = st.secrets["SUPABASE_URL"]
SUPABASE_KEY  = st.secrets["SUPABASE_KEY"]

ML_AUTH_URL   = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL  = "https://api.mercadolibre.com/oauth/token"
ML_API_BASE   = "https://api.mercadolibre.com"

# Cliente Supabase
@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# =========================
# SUPABASE — CUSTOS
# =========================
def load_custos(user_id: str) -> pd.DataFrame:
    sb = get_supabase()
    resp = sb.table("custos_sku").select("*").eq("user_id", user_id).execute()
    if not resp.data:
        return pd.DataFrame(columns=["id","sku","produto","vigencia","qtd_comprada","qtd_disponivel",
                                      "custo_produto","frete_fornecedor","embalagem","outros_custos","margem_alvo","observacao"])
    df = pd.DataFrame(resp.data)
    df["vigencia"] = pd.to_datetime(df["vigencia"], errors="coerce")
    df = df.sort_values("vigencia", na_position="first").reset_index(drop=True)
    return df

def save_custo(user_id: str, row: dict):
    sb = get_supabase()
    row["user_id"] = user_id
    row["updated_at"] = datetime.now().isoformat()
    if row.get("id"):
        sb.table("custos_sku").update(row).eq("id", row["id"]).execute()
    else:
        sb.table("custos_sku").insert(row).execute()

def load_regime(user_id: str) -> pd.DataFrame:
    sb = get_supabase()
    resp = sb.table("regime_tributario").select("*").eq("user_id", user_id).order("vigencia").execute()
    if not resp.data:
        return pd.DataFrame(columns=["id","vigencia","regime","aliquota"])
    df = pd.DataFrame(resp.data)
    df["vigencia"] = pd.to_datetime(df["vigencia"], errors="coerce")
    return df

def load_fifo_consumo(user_id: str) -> dict:
    sb = get_supabase()
    resp = sb.table("fifo_consumo").select("*").eq("user_id", user_id).execute()
    if not resp.data:
        return {}
    return {r["venda_id"]: r for r in resp.data}

def save_fifo_venda(user_id: str, venda_id: str, sku: str, qtd: int, custo: float, fifo: bool):
    sb = get_supabase()
    sb.table("fifo_consumo").upsert({
        "user_id": user_id, "venda_id": venda_id,
        "sku": sku, "qtd": qtd, "custo_unitario": custo, "fifo": fifo
    }, on_conflict="user_id,venda_id").execute()

def save_custos_batch(user_id: str, df: pd.DataFrame):
    """Salva todos os custos de uma vez (upsert por id)."""
    sb = get_supabase()
    records = []
    for _, row in df.iterrows():
        rec = {
            "user_id": user_id,
            "sku": str(row.get("sku","")),
            "produto": str(row.get("produto","")),
            "vigencia": row["vigencia"].strftime("%Y-%m-%d") if pd.notna(row.get("vigencia")) else None,
            "qtd_comprada": int(row.get("qtd_comprada", 0) or 0),
            "qtd_disponivel": float(row.get("qtd_disponivel", 0) or 0),
            "custo_produto": float(row.get("custo_produto", 0) or 0),
            "frete_fornecedor": float(row.get("frete_fornecedor", 0) or 0),
            "embalagem": float(row.get("embalagem", 0) or 0),
            "outros_custos": float(row.get("outros_custos", 0) or 0),
            "margem_alvo": float(row.get("margem_alvo", 0) or 0),
            "observacao": str(row.get("observacao","") or ""),
            "updated_at": datetime.now().isoformat(),
        }
        if row.get("id"):
            rec["id"] = int(row["id"])
        records.append(rec)
    if records:
        sb.table("custos_sku").upsert(records, on_conflict="id").execute()

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
        resp = requests.get(f"{ML_API_BASE}/orders/search", headers=headers, params=params, timeout=30)
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



def parse_orders(orders: list, fretes: dict = None) -> pd.DataFrame:
    """
    Converte lista de ordens da API em DataFrame.

    Lógica financeira (confirmada pelo XLSX do ML):
      Receita Bruta = unit_price × qty         (coluna "Receita por produtos")
      Tarifa ML     = abs(sale_fee)             (coluna "Tarifa de venda e impostos")
      Total ML      = paid_amount da order      (coluna "Total" — já descontado frete e tarifa)
      Frete         = Receita - Tarifa - Total  (coluna "Tarifas de envio", ex: R$16,85 Full)
    """
    rows = []
    for order in orders:
        order_id    = order.get("id", "")
        status      = order.get("status", "")
        date_str    = order.get("date_created", "")
        date        = pd.to_datetime(date_str, errors="coerce")
        paid_amount = float(order.get("paid_amount") or 0)

        for item in order.get("order_items", []):
            sku      = item.get("item", {}).get("seller_sku", "") or ""
            produto  = item.get("item", {}).get("title", "") or ""
            qty      = int(item.get("quantity", 1) or 1)
            unit_price = float(item.get("unit_price", 0) or 0)
            sale_fee   = abs(float(item.get("sale_fee", 0) or 0))

            receita  = unit_price * qty
            total_ml = paid_amount
            frete    = max(receita - sale_fee - total_ml, 0.0)

            rows.append({
                "Venda":         str(order_id),
                "Data":          date,
                "Status":        status,
                "SKU":           sku.strip(),
                "Produto":       produto[:50],
                "Quantidade":    qty,
                "Receita Bruta": receita,
                "Taxas ML":      sale_fee,
                "Frete":         frete,
                "Total ML":      total_ml,
                "Cancelada":     status in ["cancelled"],
            })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)

def apply_costs_online(df: pd.DataFrame, user_id: str) -> pd.DataFrame:
    """Aplica custos, FIFO, correções e impostos nas vendas."""
    if df.empty:
        return df

    df = df.copy()
    sb = get_supabase()

    custos_df  = load_custos(user_id)
    regime_df  = load_regime(user_id)
    fifo_hist  = load_fifo_consumo(user_id)

    corr_resp  = sb.table("correcoes_custo").select("*").eq("user_id", user_id).execute()
    correcoes  = {r["venda_id"]: r["custo_unitario"] for r in (corr_resp.data or [])}

    FIFO_CORTE = pd.Timestamp("2026-05-20", tz="UTC")

    def aliquota_para_data(data):
        if regime_df.empty:
            return 0.0
        validos = regime_df[pd.to_datetime(regime_df["vigencia"], utc=True) <= data]
        if validos.empty:
            return 0.0
        return float(validos.iloc[-1]["aliquota"]) / 100

    def custo_por_vigencia(sku, data):
        if custos_df.empty:
            return 0.0
        sku_df = custos_df[custos_df["sku"] == sku].copy()
        if sku_df.empty:
            return 0.0
        sku_df["vigencia"] = pd.to_datetime(sku_df["vigencia"], utc=True, errors="coerce")
        validos = sku_df[sku_df["vigencia"].isna() | (sku_df["vigencia"] <= data)]
        if validos.empty:
            return 0.0
        return float(validos.iloc[-1]["custo_produto"])

    custos_out = custos_df.copy()
    novos_fifo = {}

    df["Custo Unitário"] = 0.0
    df["Custo Total"]    = 0.0
    df["Imposto"]        = 0.0
    df["Lucro"]          = 0.0
    df["Margem %"]       = 0.0

    df_sorted = df.sort_values("Data").reset_index(drop=True)

    for idx, row in df_sorted.iterrows():
        venda_id  = str(row["Venda"])
        sku       = str(row["SKU"]).strip()
        qty       = int(row["Quantidade"])
        data      = pd.to_datetime(row["Data"], utc=True)
        cancelada = row["Cancelada"]

        # Custo unitário
        if venda_id in correcoes:
            custo_unit = float(correcoes[venda_id])
        elif venda_id in fifo_hist:
            custo_unit = float(fifo_hist[venda_id].get("custo_unitario", 0))
        elif data >= FIFO_CORTE and not cancelada:
            lotes = custos_out[
                (custos_out["sku"] == sku) & (custos_out["qtd_disponivel"] > 0)
            ].sort_values("vigencia", na_position="first")

            if lotes.empty:
                todos = custos_out[custos_out["sku"] == sku].sort_values("vigencia", na_position="first")
                custo_unit = float(todos.iloc[-1]["custo_produto"]) if not todos.empty else 0.0
            else:
                qtd_rest  = qty
                custo_tot = 0.0
                for lidx in lotes.index:
                    if qtd_rest <= 0:
                        break
                    qtd_lote  = float(custos_out.at[lidx, "qtd_disponivel"])
                    c_lote    = float(custos_out.at[lidx, "custo_produto"])
                    consumido = min(qtd_rest, qtd_lote)
                    custo_tot += consumido * c_lote
                    nova_qtd  = qtd_lote - consumido
                    custos_out.at[lidx, "qtd_disponivel"] = nova_qtd if nova_qtd > 0 else -1
                    qtd_rest  -= consumido
                custo_unit = custo_tot / qty if qty > 0 else 0.0
                novos_fifo[venda_id] = {"sku": sku, "qtd": qty, "custo_unitario": custo_unit, "fifo": True}
        else:
            custo_unit = custo_por_vigencia(sku, data)

        df_sorted.at[idx, "Custo Unitário"] = custo_unit

        aliquota = aliquota_para_data(data)
        imposto  = row["Receita Bruta"] * aliquota if not cancelada else 0.0
        df_sorted.at[idx, "Imposto"] = imposto

        custo_total = custo_unit * qty if not cancelada else 0.0
        lucro = row["Total ML"] - custo_total - imposto
        df_sorted.at[idx, "Custo Total"] = custo_total
        df_sorted.at[idx, "Lucro"]       = lucro
        df_sorted.at[idx, "Margem %"]    = (lucro / row["Receita Bruta"] * 100) if row["Receita Bruta"] > 0 and not cancelada else 0.0

    if novos_fifo:
        records = [{"user_id": user_id, "venda_id": vid, **d} for vid, d in novos_fifo.items()]
        sb.table("fifo_consumo").upsert(records, on_conflict="user_id,venda_id").execute()
        save_custos_batch(user_id, custos_out)

    return df_sorted.sort_values("Data", ascending=False).reset_index(drop=True)

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
.nav-btn button { border-radius: 999px !important; font-weight: 700 !important; }
.badge-ok { background:#DCFCE7; color:#15803D; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700; }
.badge-cancel { background:#FEE2E2; color:#DC2626; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700; }
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
    st.markdown("""
    <div class="login-card">
        <div style="font-size:48px;margin-bottom:16px;">🛒</div>
        <h2 style="font-weight:900;color:#1E1040;margin-bottom:8px;">Reobote Imports</h2>
        <p style="color:#64748B;margin-bottom:24px;">Conecte sua conta do Mercado Livre para acessar o dashboard.</p>
    </div>
    """, unsafe_allow_html=True)
    st.link_button("🔗 Conectar com Mercado Livre", auth_url, use_container_width=True, type="primary")
    st.stop()

# =========================
# DASHBOARD AUTENTICADO
# =========================
token   = get_token()
user_id = st.session_state.get("user_id", "")
user    = get_user_info(str(user_id), token)
nickname = user.get("nickname", "Vendedor")

# Navegação por abas via session_state
if "aba_ativa" not in st.session_state:
    st.session_state["aba_ativa"] = "financeiro"

nav_cols = st.columns([2, 2, 2, 4])
with nav_cols[0]:
    if st.button("📊 Financeiro", use_container_width=True,
                 type="primary" if st.session_state["aba_ativa"] == "financeiro" else "secondary"):
        st.session_state["aba_ativa"] = "financeiro"
        st.rerun()
with nav_cols[1]:
    if st.button("📦 Custos", use_container_width=True,
                 type="primary" if st.session_state["aba_ativa"] == "custos" else "secondary"):
        st.session_state["aba_ativa"] = "custos"
        st.rerun()
with nav_cols[2]:
    if st.button("🔓 Sair", use_container_width=True, type="secondary"):
        for key in ["access_token","refresh_token","user_id","expires_at","aba_ativa"]:
            st.session_state.pop(key, None)
        st.rerun()

st.markdown("<br>", unsafe_allow_html=True)

# ===================================================
# ABA: FINANCEIRO
# ===================================================
if st.session_state["aba_ativa"] == "financeiro":

    import zoneinfo
    tz_br = zoneinfo.ZoneInfo("America/Sao_Paulo")
    agora_br = datetime.now(tz_br)
    hoje_str = agora_br.strftime("%Y-%m-%d")

    col_f1, col_f2, col_f3 = st.columns([3, 3, 1])
    with col_f1:
        periodo = st.selectbox("Período", ["Hoje", "7 dias", "15 dias", "30 dias"], index=1,
                               key="periodo_sel")
    with col_f3:
        if st.button("🔄", help="Atualizar dados"):
            st.cache_data.clear()
            st.rerun()

    if periodo == "Hoje":
        date_from = f"{hoje_str}T00:00:00.000-03:00"
        date_to   = f"{hoje_str}T23:59:59.000-03:00"
    elif periodo == "7 dias":
        d = agora_br - timedelta(days=7)
        date_from = d.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
        date_to   = agora_br.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
    elif periodo == "15 dias":
        d = agora_br - timedelta(days=15)
        date_from = d.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
        date_to   = agora_br.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
    else:
        d = agora_br - timedelta(days=30)
        date_from = d.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
        date_to   = agora_br.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")

    with st.spinner("Buscando vendas..."):
        orders = get_orders(str(user_id), token, date_from, date_to)

    if not orders:
        st.info("Nenhuma venda encontrada no período.")
        st.stop()

    # DEBUG — remove após confirmar
    if orders:
        o = orders[0]
        item0 = o.get("order_items", [{}])[0]
        paid = o.get("paid_amount")
        unit = item0.get("unit_price")
        fee  = item0.get("sale_fee")
        qty  = item0.get("quantity")
        st.caption(f"DEBUG paid_amount={paid} | unit_price={unit} | sale_fee={fee} | qty={qty} | frete_calc={float(unit or 0)*int(qty or 1) - abs(float(fee or 0)) - float(paid or 0):.2f}")

    df_raw = parse_orders(orders)
    if df_raw.empty:
        st.info("Nenhuma venda encontrada no período.")
        st.stop()

    with st.spinner("Calculando custos e margens..."):
        df = apply_costs_online(df_raw, str(user_id))

    # Métricas
    aprovadas   = df[~df["Cancelada"]]
    canceladas  = df[df["Cancelada"]]
    faturamento = aprovadas["Receita Bruta"].sum()
    tarifas     = aprovadas["Taxas ML"].sum()
    fretes_sum  = aprovadas["Frete"].sum()
    custos      = aprovadas["Custo Total"].sum() if "Custo Total" in aprovadas.columns else 0
    impostos    = aprovadas["Imposto"].sum()
    lucro_total = aprovadas["Lucro"].sum() if "Lucro" in aprovadas.columns else 0
    margem_real = (lucro_total / faturamento * 100) if faturamento > 0 else 0
    qtd_cancel  = int(canceladas["Quantidade"].sum())

    # Hero
    st.markdown(f"""
    <div class="hero">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:24px;">
            <div>
                <p style="opacity:.85;font-size:14px;font-weight:700;margin:0;">Olá, {nickname} 👋</p>
                <h1 class="hero-title">Financeiro</h1>
                <div style="background:rgba(255,255,255,.15);border-radius:999px;padding:8px 16px;
                            width:fit-content;margin-top:12px;font-size:13px;font-weight:800;">
                    {periodo} • {agora_br.strftime('%d/%m/%Y')}
                </div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:14px;font-weight:700;opacity:.85;">Faturamento</div>
                <div class="hero-value">R$ {faturamento:,.2f}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Cards KPI
    c1, c2, c3, c4 = st.columns(4)
    def card(col, label, value, sub="", color="#1E1040"):
        col.markdown(f"""
        <div class="metric-card">
            <div class="metric-label" style="color:{color};">{label}</div>
            <div class="metric-value">{value}</div>
            {f'<div style="color:#64748B;font-size:13px;margin-top:4px;">{sub}</div>' if sub else ''}
        </div>
        """, unsafe_allow_html=True)

    pct = lambda v: f"{v/faturamento*100:.1f}%" if faturamento else ""
    card(c1, "Tarifas ML",  f"R$ {tarifas:,.2f}",   pct(tarifas),   "#F59E0B")
    card(c2, "Frete ML",    f"R$ {fretes_sum:,.2f}", pct(fretes_sum),"#0EA5E9")
    card(c3, "Custos",      f"R$ {custos:,.2f}",     pct(custos),    "#8B5CF6")
    card(c4, "Impostos",    f"R$ {impostos:,.2f}",   pct(impostos))

    st.markdown("<br>", unsafe_allow_html=True)

    lc1, lc2, lc3 = st.columns([1, 1, 2])
    with lc1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Canceladas</div>
            <div class="metric-value" style="color:#EF4444;">R$ {canceladas['Receita Bruta'].sum():,.2f}</div>
            <div style="color:#64748B;font-size:13px;margin-top:4px;">{len(canceladas)} pedidos</div>
        </div>
        """, unsafe_allow_html=True)
    with lc2:
        ticket = faturamento / len(aprovadas) if len(aprovadas) > 0 else 0
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Ticket Médio</div>
            <div class="metric-value">R$ {ticket:.2f}</div>
            <div style="color:#64748B;font-size:13px;margin-top:4px;">{len(aprovadas)} pedidos</div>
        </div>
        """, unsafe_allow_html=True)
    with lc3:
        cor_lucro = "#16A34A" if lucro_total >= 0 else "#DC2626"
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,{cor_lucro},{cor_lucro}CC);border-radius:20px;
                    padding:24px 32px;color:white;text-align:center;">
            <div style="font-size:16px;font-weight:800;margin-bottom:8px;">Lucro Líquido Real</div>
            <div style="font-size:48px;font-weight:900;letter-spacing:-2px;">R$ {lucro_total:,.2f}</div>
            <div style="font-size:14px;font-weight:700;opacity:.9;margin-top:4px;">Margem real: {margem_real:.2f}%</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Gráfico lucro por dia (linha + área + média)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**Lucro por dia**")
    daily = aprovadas.copy()
    daily["Dia"] = pd.to_datetime(daily["Data"]).dt.date
    daily_agg = daily.groupby("Dia").agg(
        Lucro=("Lucro","sum"),
        Receita=("Receita Bruta","sum"),
        Quantidade=("Quantidade","sum")
    ).reset_index()
    daily_agg["Dia"] = pd.to_datetime(daily_agg["Dia"])
    media_lucro = daily_agg["Lucro"].mean()
    daily_agg["Cor"] = daily_agg["Lucro"].apply(lambda x: "Acima" if x >= media_lucro else "Abaixo")

    base = alt.Chart(daily_agg)
    area = base.mark_area(interpolate="monotone", color="#7C3AED", opacity=0.12).encode(
        x=alt.X("Dia:T", title=None),
        y=alt.Y("Lucro:Q", title="Lucro (R$)")
    )
    linha = base.mark_line(interpolate="monotone", color="#7C3AED", strokeWidth=2.5).encode(
        x="Dia:T", y="Lucro:Q"
    )
    pontos = base.mark_point(filled=True, size=80).encode(
        x="Dia:T",
        y="Lucro:Q",
        color=alt.Color("Cor:N", scale=alt.Scale(domain=["Acima","Abaixo"], range=["#16A34A","#DC2626"]),
                        legend=None),
        tooltip=[
            alt.Tooltip("Dia:T", title="Data", format="%d/%m/%Y"),
            alt.Tooltip("Lucro:Q", title="Lucro R$", format=",.2f"),
            alt.Tooltip("Receita:Q", title="Receita R$", format=",.2f"),
            alt.Tooltip("Quantidade:Q", title="Qtd", format=",.0f"),
        ]
    )
    media_line = alt.Chart(pd.DataFrame({"media": [media_lucro]})).mark_rule(
        strokeDash=[6,3], color="#94A3B8", strokeWidth=1.5
    ).encode(y="media:Q")

    st.altair_chart((area + linha + pontos + media_line).properties(height=300), use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # Gráfico receita por dia
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**Receita por dia**")
    receita_line = alt.Chart(daily_agg).mark_area(
        interpolate="monotone", color="#0EA5E9", opacity=0.13
    ).encode(
        x=alt.X("Dia:T", title=None),
        y=alt.Y("Receita:Q", title="Receita (R$)")
    ) + alt.Chart(daily_agg).mark_line(
        interpolate="monotone", color="#0EA5E9", strokeWidth=2.5,
        point=alt.OverlayMarkDef(filled=True, size=60, color="#0EA5E9")
    ).encode(
        x=alt.X("Dia:T", title=None),
        y=alt.Y("Receita:Q"),
        tooltip=[
            alt.Tooltip("Dia:T", title="Data", format="%d/%m/%Y"),
            alt.Tooltip("Receita:Q", title="Receita R$", format=",.2f"),
            alt.Tooltip("Quantidade:Q", title="Qtd", format=",.0f"),
        ]
    )
    st.altair_chart(receita_line.properties(height=240), use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # Tabela detalhada
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**Pedidos detalhados**")

    tabela = df[[
        "Venda","Data","Status","SKU","Produto","Quantidade",
        "Receita Bruta","Taxas ML","Frete","Custo Total","Imposto","Lucro","Margem %"
    ]].copy()
    tabela["Data"] = pd.to_datetime(tabela["Data"]).dt.strftime("%d/%m/%Y %H:%M")

    for col_r in ["Receita Bruta","Taxas ML","Frete","Custo Total","Imposto","Lucro"]:
        tabela[col_r] = tabela[col_r].apply(lambda x: f"R$ {x:,.2f}")
    tabela["Margem %"] = tabela["Margem %"].apply(lambda x: f"{x:.1f}%")

    st.dataframe(tabela.rename(columns={
        "Venda": "N.º Venda",
        "Receita Bruta": "Receita",
        "Custo Total": "Custo",
    }), use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ===================================================
# ABA: CADASTRO DE CUSTOS
# ===================================================
elif st.session_state["aba_ativa"] == "custos":

    st.markdown(f"""
    <div class="hero">
        <h1 class="hero-title">Cadastro de Custos</h1>
        <div style="opacity:.85;font-size:15px;margin-top:8px;">
            Gerencie custos por SKU e vigência — FIFO ativo a partir de 20/05/2026
        </div>
    </div>
    """, unsafe_allow_html=True)

    custos_df = load_custos(str(user_id))

    # Formulário para novo custo
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**➕ Novo lote / vigência**")

    with st.form("form_custo", clear_on_submit=True):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            sku_novo    = st.text_input("SKU", placeholder="ex: S_001")
            produto_novo = st.text_input("Produto", placeholder="ex: Seladora Térmica")
        with fc2:
            vigencia_nova = st.date_input("Vigência (início)", value=None, help="Deixe em branco para custo sem data")
            custo_produto = st.number_input("Custo unitário (R$)", min_value=0.0, step=0.01, format="%.2f")
        with fc3:
            qtd_comprada  = st.number_input("Qtd comprada", min_value=0, step=1)
            frete_forn    = st.number_input("Frete fornecedor (R$)", min_value=0.0, step=0.01, format="%.2f")

        fc4, fc5, fc6 = st.columns(3)
        with fc4:
            embalagem = st.number_input("Embalagem (R$)", min_value=0.0, step=0.01, format="%.2f")
        with fc5:
            outros    = st.number_input("Outros custos (R$)", min_value=0.0, step=0.01, format="%.2f")
        with fc6:
            margem_alvo = st.number_input("Margem alvo (%)", min_value=0.0, step=0.1, format="%.1f")

        obs = st.text_input("Observação", placeholder="opcional")

        submitted = st.form_submit_button("💾 Salvar lote", type="primary", use_container_width=True)
        if submitted:
            if not sku_novo:
                st.error("SKU é obrigatório.")
            else:
                # Custo por unidade incluindo frete rateado
                qtd = int(qtd_comprada) if qtd_comprada > 0 else 1
                custo_total_unit = custo_produto + (frete_forn / qtd) + (embalagem / qtd) + (outros / qtd)
                row = {
                    "sku": sku_novo.strip().upper(),
                    "produto": produto_novo,
                    "vigencia": vigencia_nova.strftime("%Y-%m-%d") if vigencia_nova else None,
                    "qtd_comprada": int(qtd_comprada),
                    "qtd_disponivel": int(qtd_comprada),  # começa igual à comprada
                    "custo_produto": round(custo_produto, 4),
                    "frete_fornecedor": round(frete_forn, 4),
                    "embalagem": round(embalagem, 4),
                    "outros_custos": round(outros, 4),
                    "margem_alvo": round(margem_alvo, 2),
                    "observacao": obs,
                }
                save_custo(str(user_id), row)
                st.success(f"Lote {sku_novo} salvo! Custo unitário total: R$ {custo_total_unit:.4f}")
                st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    # Tabela de custos cadastrados
    if not custos_df.empty:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("**📋 Lotes cadastrados**")

        exibir = custos_df[[
            "id","sku","produto","vigencia","qtd_comprada","qtd_disponivel",
            "custo_produto","frete_fornecedor","embalagem","outros_custos","margem_alvo","observacao"
        ]].copy()
        exibir["vigencia"] = exibir["vigencia"].apply(
            lambda x: x.strftime("%d/%m/%Y") if pd.notna(x) else "Sem data"
        )
        exibir["qtd_disponivel"] = exibir["qtd_disponivel"].apply(
            lambda x: "ESGOTADO" if x < 0 else str(int(x))
        )
        for col_r in ["custo_produto","frete_fornecedor","embalagem","outros_custos"]:
            exibir[col_r] = exibir[col_r].apply(lambda x: f"R$ {x:.4f}")
        exibir["margem_alvo"] = exibir["margem_alvo"].apply(lambda x: f"{x:.1f}%")

        st.dataframe(exibir.rename(columns={
            "id": "ID",
            "sku": "SKU",
            "produto": "Produto",
            "vigencia": "Vigência",
            "qtd_comprada": "Qtd Comprada",
            "qtd_disponivel": "Qtd Disponível",
            "custo_produto": "Custo Unit.",
            "frete_fornecedor": "Frete Forn.",
            "embalagem": "Embalagem",
            "outros_custos": "Outros",
            "margem_alvo": "Margem Alvo",
            "observacao": "Obs.",
        }), use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("Nenhum custo cadastrado ainda.")
