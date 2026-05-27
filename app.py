import streamlit as st
import requests
import pandas as pd
import altair as alt
from datetime import datetime, timedelta, date
import urllib.parse
from supabase import create_client, Client

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Reobote Imports", page_icon="🛒", layout="wide")

CLIENT_ID     = st.secrets["ML_CLIENT_ID"]
CLIENT_SECRET = st.secrets["ML_CLIENT_SECRET"]
REDIRECT_URI  = st.secrets["ML_REDIRECT_URI"]
SUPABASE_URL  = st.secrets["SUPABASE_URL"]
SUPABASE_KEY  = st.secrets["SUPABASE_KEY"]

ML_AUTH_URL  = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API_BASE  = "https://api.mercadolibre.com"

@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# =========================
# SUPABASE HELPERS
# =========================
def load_custos(user_id):
    sb = get_supabase()
    resp = sb.table("custos_sku").select("*").eq("user_id", user_id).execute()
    if not resp.data:
        return pd.DataFrame(columns=["id","sku","produto","vigencia","qtd_comprada","qtd_disponivel",
                                     "custo_produto","frete_fornecedor","embalagem","outros_custos","margem_alvo","observacao"])
    df = pd.DataFrame(resp.data)
    df["vigencia"] = pd.to_datetime(df["vigencia"], errors="coerce")
    return df.sort_values("vigencia", na_position="first").reset_index(drop=True)

def save_custo(user_id, row):
    sb = get_supabase()
    row["user_id"] = user_id
    row["updated_at"] = datetime.now().isoformat()
    if row.get("id"):
        sb.table("custos_sku").update(row).eq("id", row["id"]).execute()
    else:
        sb.table("custos_sku").insert(row).execute()

def save_custos_batch(user_id, df):
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

def load_regime(user_id):
    sb = get_supabase()
    resp = sb.table("regime_tributario").select("*").eq("user_id", user_id).order("vigencia").execute()
    if not resp.data:
        return pd.DataFrame(columns=["id","vigencia","regime","aliquota"])
    df = pd.DataFrame(resp.data)
    df["vigencia"] = pd.to_datetime(df["vigencia"], errors="coerce")
    return df

def save_regime(user_id, row):
    sb = get_supabase()
    row["user_id"] = user_id
    if row.get("id"):
        sb.table("regime_tributario").update(row).eq("id", row["id"]).execute()
    else:
        sb.table("regime_tributario").insert(row).execute()

def load_fifo_consumo(user_id):
    sb = get_supabase()
    resp = sb.table("fifo_consumo").select("*").eq("user_id", user_id).execute()
    if not resp.data:
        return {}
    return {r["venda_id"]: r for r in resp.data}

def load_correcoes(user_id):
    sb = get_supabase()
    resp = sb.table("correcoes_custo").select("*").eq("user_id", user_id).execute()
    return {r["venda_id"]: r for r in (resp.data or [])}

def save_correcao(user_id, venda_id, custo, motivo=""):
    sb = get_supabase()
    sb.table("correcoes_custo").upsert({
        "user_id": user_id,
        "venda_id": str(venda_id),
        "custo_unitario": float(custo),
        "motivo": motivo,
    }, on_conflict="user_id,venda_id").execute()

def delete_correcao(user_id, venda_id):
    sb = get_supabase()
    sb.table("correcoes_custo").delete().eq("user_id", user_id).eq("venda_id", str(venda_id)).execute()

def load_capital(user_id):
    sb = get_supabase()
    resp = sb.table("capital_investido").select("*").eq("user_id", user_id).order("data").execute()
    if not resp.data:
        return pd.DataFrame(columns=["id","data","valor","descricao","categoria"])
    df = pd.DataFrame(resp.data)
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df

def save_capital(user_id, row):
    sb = get_supabase()
    row["user_id"] = user_id
    if row.get("id"):
        sb.table("capital_investido").update(row).eq("id", row["id"]).execute()
    else:
        sb.table("capital_investido").insert(row).execute()

# =========================
# OAUTH
# =========================
def get_auth_url():
    params = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI}
    return f"{ML_AUTH_URL}?{urllib.parse.urlencode(params)}"

def exchange_code_for_token(code):
    resp = requests.post(ML_TOKEN_URL, data={
        "grant_type": "authorization_code", "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET, "code": code, "redirect_uri": REDIRECT_URI,
    }, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    return resp.json()

def refresh_access_token(refresh_token):
    resp = requests.post(ML_TOKEN_URL, data={
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET, "refresh_token": refresh_token,
    }, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    return resp.json()

def get_token():
    if "access_token" not in st.session_state:
        return None
    if datetime.now().timestamp() >= st.session_state.get("expires_at", 0) - 60:
        data = refresh_access_token(st.session_state.get("refresh_token", ""))
        if "access_token" not in data:
            return None
        save_token(data)
    return st.session_state["access_token"]

def save_token(data):
    st.session_state["access_token"]  = data["access_token"]
    st.session_state["refresh_token"] = data.get("refresh_token", "")
    st.session_state["user_id"]       = data.get("user_id", "")
    st.session_state["expires_at"]    = datetime.now().timestamp() + data.get("expires_in", 21600)

# =========================
# API
# =========================
@st.cache_data(ttl=300, show_spinner=False)
def get_user_info(user_id, token):
    resp = requests.get(f"{ML_API_BASE}/users/{user_id}",
                        headers={"Authorization": f"Bearer {token}"}, timeout=15)
    return resp.json() if resp.status_code == 200 else {}

@st.cache_data(ttl=300, show_spinner=False)
def get_orders(user_id, token, date_from, date_to):
    headers = {"Authorization": f"Bearer {token}"}
    orders, offset, limit = [], 0, 50
    while True:
        resp = requests.get(f"{ML_API_BASE}/orders/search", headers=headers, params={
            "seller": user_id, "order.date_created.from": date_from,
            "order.date_created.to": date_to, "sort": "date_desc",
            "offset": offset, "limit": limit,
        }, timeout=30)
        if resp.status_code != 200:
            break
        data = resp.json()
        results = data.get("results", [])
        orders.extend(results)
        paging = data.get("paging", {})
        offset += limit
        if offset >= paging.get("total", 0) or not results:
            break
    return orders

def get_orders_reembolsados(orders):
    """
    Identifica orders com reembolso verificando payments[].transaction_amount_refunded > 0.
    Usa a lista de orders já buscada — sem chamadas extras à API.
    Retorna dict {order_id: valor_reembolsado}.
    """
    reembolsadas = {}
    for order in orders:
        order_id    = str(order.get("id", ""))
        reembolsado = 0.0
        for payment in order.get("payments", []):
            refunded = float(payment.get("transaction_amount_refunded") or 0)
            reembolsado += refunded
        if reembolsado > 0:
            reembolsadas[order_id] = reembolsado
    return reembolsadas

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fretes_batch(shipping_ids_tuple, token_hash, token):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if not shipping_ids_tuple:
        return {}
    headers = {"Authorization": f"Bearer {token}"}
    def fetch_one(sid):
        try:
            resp = requests.get(f"{ML_API_BASE}/shipments/{sid}", headers=headers, timeout=15)
            if resp.status_code != 200:
                return sid, 0.0
            opt = resp.json().get("shipping_option", {})
            cost = opt.get("list_cost") or opt.get("base_cost") or opt.get("cost") or 0
            return sid, float(cost)
        except:
            return sid, 0.0
    fretes = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for future in as_completed({ex.submit(fetch_one, sid): sid for sid in shipping_ids_tuple}):
            sid, custo = future.result()
            fretes[sid] = custo
    return fretes

def parse_orders(orders, fretes=None, reembolsados=None):
    fretes       = fretes or {}
    reembolsados = reembolsados or {}
    rows = []
    for order in orders:
        order_id    = order.get("id", "")
        status      = order.get("status", "")
        date        = pd.to_datetime(order.get("date_created", ""), errors="coerce")
        shipping_id = order.get("shipping", {}).get("id", 0)
        paid_amount = float(order.get("paid_amount") or 0)
        reemb_val   = float(reembolsados.get(str(order_id), 0) or 0)
        # Cancelada = status cancelled OU reembolso total (>= 90% do valor pago)
        cancelada   = status in ["cancelled"] or (reemb_val > 0 and paid_amount > 0 and reemb_val >= paid_amount * 0.9)
        for item in order.get("order_items", []):
            sku        = (item.get("item", {}).get("seller_sku", "") or "").strip()
            produto    = (item.get("item", {}).get("title", "") or "")[:50]
            qty        = int(item.get("quantity", 1) or 1)
            unit_price = float(item.get("unit_price", 0) or 0)
            sale_fee   = abs(float(item.get("sale_fee", 0) or 0))
            frete      = float(fretes.get(shipping_id, 0) or 0)
            receita    = unit_price * qty
            total_ml   = receita - sale_fee - frete
            rows.append({
                "Venda": str(order_id), "Data": date, "Status": status,
                "SKU": sku, "Produto": produto, "Quantidade": qty,
                "Receita Bruta": receita, "Taxas ML": sale_fee,
                "Frete": frete, "Total ML": total_ml,
                "Cancelada": cancelada,
                "Reembolsado": reemb_val,
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def apply_costs_online(df, user_id):
    if df.empty:
        return df
    df = df.copy()
    sb = get_supabase()
    custos_df  = load_custos(user_id)
    regime_df  = load_regime(user_id)
    fifo_hist  = load_fifo_consumo(user_id)
    correcoes  = {r: v["custo_unitario"] for r, v in load_correcoes(user_id).items()}
    FIFO_CORTE = pd.Timestamp("2026-05-20", tz="UTC")

    def aliquota_para_data(data):
        if regime_df.empty: return 0.0
        validos = regime_df[pd.to_datetime(regime_df["vigencia"], utc=True) <= data]
        return float(validos.iloc[-1]["aliquota"]) / 100 if not validos.empty else 0.0

    def custo_por_vigencia(sku, data):
        if custos_df.empty: return 0.0
        sk = custos_df[custos_df["sku"] == sku].copy()
        if sk.empty: return 0.0
        sk["vigencia"] = pd.to_datetime(sk["vigencia"], utc=True, errors="coerce")
        validos = sk[sk["vigencia"].isna() | (sk["vigencia"] <= data)]
        return float(validos.iloc[-1]["custo_produto"]) if not validos.empty else 0.0

    custos_out = custos_df.copy()
    novos_fifo = {}
    df["Custo Unitário"] = 0.0
    df["Custo Total"]    = 0.0
    df["Imposto"]        = 0.0
    df["Lucro"]          = 0.0
    df["Margem %"]       = 0.0
    df["FIFO"]           = False
    df["Corrigido"]      = False

    df_sorted = df.sort_values("Data").reset_index(drop=True)
    for idx, row in df_sorted.iterrows():
        venda_id  = str(row["Venda"])
        sku       = str(row["SKU"]).strip()
        qty       = int(row["Quantidade"])
        data      = pd.to_datetime(row["Data"], utc=True)
        cancelada = row["Cancelada"]

        if venda_id in correcoes:
            custo_unit = float(correcoes[venda_id])
            df_sorted.at[idx, "Corrigido"] = True
        elif venda_id in fifo_hist:
            custo_unit = float(fifo_hist[venda_id].get("custo_unitario", 0))
            df_sorted.at[idx, "FIFO"] = True
        elif data >= FIFO_CORTE and not cancelada:
            lotes = custos_out[(custos_out["sku"] == sku) & (custos_out["qtd_disponivel"] > 0)].sort_values("vigencia", na_position="first")
            if lotes.empty:
                # Sem lote disponível — usa custo por vigência da data da venda
                custo_unit = custo_por_vigencia(sku, data)
            else:
                qtd_rest, custo_tot = qty, 0.0
                for lidx in lotes.index:
                    if qtd_rest <= 0: break
                    qtd_lote  = float(custos_out.at[lidx, "qtd_disponivel"])
                    c_lote    = float(custos_out.at[lidx, "custo_produto"])
                    consumido = min(qtd_rest, qtd_lote)
                    custo_tot += consumido * c_lote
                    nova_qtd   = qtd_lote - consumido
                    custos_out.at[lidx, "qtd_disponivel"] = nova_qtd if nova_qtd > 0 else -1
                    qtd_rest  -= consumido
                custo_unit = custo_tot / qty if qty > 0 else 0.0
                novos_fifo[venda_id] = {"sku": sku, "qtd": qty, "custo_unitario": custo_unit, "fifo": True}
                df_sorted.at[idx, "FIFO"] = True
        else:
            custo_unit = custo_por_vigencia(sku, data)

        df_sorted.at[idx, "Custo Unitário"] = custo_unit
        aliquota   = aliquota_para_data(data)
        imposto    = row["Receita Bruta"] * aliquota if not cancelada else 0.0
        custo_tot  = custo_unit * qty if not cancelada else 0.0
        lucro      = row["Total ML"] - custo_tot - imposto
        margem     = (lucro / row["Receita Bruta"] * 100) if row["Receita Bruta"] > 0 and not cancelada else 0.0
        df_sorted.at[idx, "Imposto"]     = imposto
        df_sorted.at[idx, "Custo Total"] = custo_tot
        df_sorted.at[idx, "Lucro"]       = lucro
        df_sorted.at[idx, "Margem %"]    = margem

    if novos_fifo:
        sb.table("fifo_consumo").upsert(
            [{"user_id": user_id, "venda_id": vid, **d} for vid, d in novos_fifo.items()],
            on_conflict="user_id,venda_id").execute()
        save_custos_batch(user_id, custos_out)

    return df_sorted.sort_values("Data", ascending=False).reset_index(drop=True)

# =========================
# ESTILOS
# =========================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif;}
.main{background:#F5F7FB;}
.block-container{padding-top:0!important;padding-bottom:2rem;max-width:1380px!important;margin:0 auto;}
header[data-testid="stHeader"]{display:none!important;}
#MainMenu,footer{display:none!important;}
.navbar{background:linear-gradient(90deg,#1E1040 0%,#2D1B69 50%,#1E1040 100%);
        padding:0 32px;height:52px;display:flex;align-items:center;
        box-shadow:0 3px 16px rgba(109,40,217,.25);margin-bottom:20px;border-radius:0 0 12px 12px;}
.navbar-name{color:white;font-size:15px;font-weight:900;letter-spacing:1px;margin-left:10px;}
.card{background:white;border-radius:24px;padding:28px;
      box-shadow:0 8px 28px rgba(15,23,42,.08);border:1px solid #E7ECF5;margin-bottom:18px;}
.hero{background:linear-gradient(135deg,#7C3AED 0%,#8B5CF6 45%,#6D28D9 100%);
      border-radius:24px;padding:34px 38px;color:white;
      box-shadow:0 18px 45px rgba(109,40,217,.22);margin-bottom:18px;min-height:230px;}
.hero-title{font-size:48px;font-weight:900;line-height:1;margin:0 0 22px 0;letter-spacing:-1.6px;}
.hero-small{font-size:14px;font-weight:700;opacity:.95;margin-bottom:0;}
.hero-value-label{font-size:18px;font-weight:800;opacity:.98;text-align:right;}
.hero-value{font-size:60px;font-weight:900;line-height:1.05;text-align:right;letter-spacing:-2px;}
.hero-growth{color:#22C55E;font-weight:900;font-size:16px;}
.metric-card{background:#FFFFFF;border:1px dashed #D9E2F0;border-radius:16px;
             padding:24px 22px 20px 22px;min-height:155px;position:relative;overflow:hidden;}
.metric-card:after{content:"";position:absolute;height:8px;left:0;bottom:0;right:0;background:var(--accent);}
.metric-title{font-size:22px;font-weight:800;color:var(--accent);margin-bottom:12px;text-align:center;}
.metric-value{font-size:30px;font-weight:900;color:#020617;text-align:center;letter-spacing:-1px;}
.metric-pill{margin:12px auto 0 auto;width:fit-content;border-radius:999px;padding:5px 16px;
             border:1px solid #E2E8F0;color:#94A3B8;font-weight:800;font-size:14px;background:#FAFBFF;}
.kpi-card{background:#F4F1EA;border-radius:14px;padding:18px 14px;text-align:center;
          min-height:112px;display:flex;flex-direction:column;justify-content:center;overflow:hidden;}
.kpi-title{font-size:13px;font-weight:800;color:#44403C;text-transform:uppercase;
           letter-spacing:.25px;margin-bottom:10px;white-space:nowrap;}
.kpi-value{font-size:clamp(20px,2.05vw,28px);font-weight:900;color:#1F2937;
           letter-spacing:-.8px;white-space:nowrap;line-height:1.05;}
.green-box{background:linear-gradient(135deg,#22C55E 0%,#16A34A 100%);color:white;
           border-radius:16px;padding:26px;box-shadow:0 18px 35px rgba(34,197,94,.22);
           min-height:138px;text-align:center;}
.red-box{background:linear-gradient(135deg,#EF4444 0%,#B91C1C 100%);color:white;
         border-radius:16px;padding:26px;box-shadow:0 18px 35px rgba(239,68,68,.18);
         min-height:138px;text-align:center;}
.green-title{font-size:24px;font-weight:900;margin-bottom:8px;}
.green-value{font-size:36px;font-weight:900;line-height:1;}
.green-sub{font-size:17px;font-weight:900;margin-top:6px;}
.small-title{font-size:26px;font-weight:900;color:#020617;margin-bottom:2px;letter-spacing:-.7px;}
.muted{color:#64748B;font-size:16px;}
.alert{background:#FFF7ED;border:1px solid #FED7AA;color:#9A3412;
       padding:14px 18px;border-radius:14px;font-weight:700;margin-bottom:16px;}
.success-box{background:#ECFDF5;border:1px solid #BBF7D0;color:#166534;
             padding:14px 18px;border-radius:14px;font-weight:700;margin-bottom:16px;}
.login-card{background:white;border-radius:24px;padding:48px;max-width:480px;
            margin:80px auto;text-align:center;box-shadow:0 8px 32px rgba(15,23,42,.1);}
div[data-baseweb="select"]>div{border-radius:12px!important;border-color:#D8E0EC!important;
    background:white!important;min-height:46px!important;
    box-shadow:0 8px 20px rgba(15,23,42,.06);font-weight:800;}
</style>
""", unsafe_allow_html=True)

# =========================
# NAVBAR
# =========================
st.markdown('<div class="navbar"><span style="font-size:20px;">🛒</span><span class="navbar-name">REOBOTE IMPORTS</span></div>', unsafe_allow_html=True)

# =========================
# AUTH
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
            if st.button("Tentar novamente"):
                st.query_params.clear()
                st.rerun()
            st.stop()

if "access_token" not in st.session_state:
    st.markdown("""<div class="login-card">
        <div style="font-size:48px;margin-bottom:16px;">🛒</div>
        <h2 style="font-weight:900;color:#1E1040;margin-bottom:8px;">Reobote Imports</h2>
        <p style="color:#64748B;margin-bottom:24px;">Conecte sua conta do Mercado Livre para acessar o dashboard.</p>
    </div>""", unsafe_allow_html=True)
    st.link_button("🔗 Conectar com Mercado Livre", get_auth_url(), use_container_width=True, type="primary")
    st.stop()

# =========================
# DASHBOARD
# =========================
token    = get_token()
user_id  = st.session_state.get("user_id", "")
nickname = get_user_info(str(user_id), token).get("nickname", "Vendedor")

if "aba_ativa" not in st.session_state:
    st.session_state["aba_ativa"] = "financeiro"

nav_cols = st.columns([2, 2, 2, 2, 4])
abas = [("financeiro","📊 Financeiro"), ("custos","📦 Custos"), ("regime","🏛️ Regime"), ("caixa","💰 Caixa")]
for col, (aba_id, aba_label) in zip(nav_cols[:4], abas):
    with col:
        if st.button(aba_label, use_container_width=True,
                     type="primary" if st.session_state["aba_ativa"] == aba_id else "secondary"):
            st.session_state["aba_ativa"] = aba_id
            st.rerun()
with nav_cols[4]:
    c1, c2 = st.columns([1,1])
    with c2:
        if st.button("🔓 Sair", use_container_width=True):
            for k in ["access_token","refresh_token","user_id","expires_at","aba_ativa"]:
                st.session_state.pop(k, None)
            st.rerun()

st.markdown("<br>", unsafe_allow_html=True)

# ══════════════════════════════════════════
# ABA: FINANCEIRO
# ══════════════════════════════════════════
if st.session_state["aba_ativa"] == "financeiro":
    import zoneinfo
    tz_br    = zoneinfo.ZoneInfo("America/Sao_Paulo")
    agora_br = datetime.now(tz_br)
    hoje_str = agora_br.strftime("%Y-%m-%d")

    col_f1, col_f2, col_f3, col_f4 = st.columns([2, 1.5, 1.5, 0.5])
    with col_f1:
        periodo = st.selectbox("Período", ["Hoje","7 dias","15 dias","30 dias","Personalizar"], index=1, key="periodo_sel")
    with col_f4:
        if st.button("🔄", help="Limpar cache e atualizar"):
            st.cache_data.clear()
            st.rerun()

    # Datas personalizadas
    if periodo == "Personalizar":
        with col_f2:
            data_ini = st.date_input("De", value=agora_br.date() - timedelta(days=7), key="data_ini")
        with col_f3:
            data_fim = st.date_input("Até", value=agora_br.date(), key="data_fim")
        date_from = f"{data_ini}T00:00:00.000-03:00"
        date_to   = f"{data_fim}T23:59:59.000-03:00"
        label_periodo = f"{data_ini.strftime('%d/%m')} – {data_fim.strftime('%d/%m/%Y')}"
    elif periodo == "Hoje":
        date_from = f"{hoje_str}T00:00:00.000-03:00"
        date_to   = f"{hoje_str}T23:59:59.000-03:00"
        label_periodo = f"Hoje • {agora_br.strftime('%d/%m/%Y')}"
    else:
        dias = {"7 dias": 7, "15 dias": 15, "30 dias": 30}[periodo]
        d = agora_br - timedelta(days=dias)
        date_from = d.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
        date_to   = agora_br.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
        label_periodo = f"{periodo} • até {agora_br.strftime('%d/%m/%Y')}"

    with st.spinner("Buscando vendas..."):
        orders = get_orders(str(user_id), token, date_from, date_to)

    if not orders:
        st.info("Nenhuma venda encontrada no período.")
        st.stop()

    shipping_ids = tuple(sorted({o.get("shipping",{}).get("id") for o in orders if o.get("shipping",{}).get("id")}))
    token_hash   = token[-8:] if token else ""

    with st.spinner("Buscando fretes..."):
        fretes       = fetch_fretes_batch(shipping_ids, token_hash, token)
    # Detecta reembolsos nas orders já buscadas — sem chamada extra à API
    reembolsados = get_orders_reembolsados(orders)

    df_raw = parse_orders(orders, fretes, reembolsados)
    if df_raw.empty:
        st.info("Nenhuma venda encontrada.")
        st.stop()

    with st.spinner("Calculando custos e margens..."):
        df = apply_costs_online(df_raw, str(user_id))

    aprovadas   = df[~df["Cancelada"]]
    canceladas  = df[df["Cancelada"]]
    faturamento = aprovadas["Receita Bruta"].sum()
    tarifas     = aprovadas["Taxas ML"].sum()
    fretes_sum  = aprovadas["Frete"].sum()
    custos      = aprovadas["Custo Total"].sum()
    impostos    = aprovadas["Imposto"].sum()
    lucro_total = aprovadas["Lucro"].sum()
    margem_real = (lucro_total / faturamento * 100) if faturamento > 0 else 0

    # HERO
    st.markdown(f"""
    <div class="hero">
      <div style="display:flex;justify-content:space-between;gap:30px;align-items:flex-start;">
        <div>
          <p class="hero-small">Resumo</p>
          <h1 class="hero-title">Financeiro</h1>
          <div style="background:rgba(255,255,255,.18);color:white;border:1px solid rgba(255,255,255,.35);
                      border-radius:999px;padding:10px 16px;font-weight:900;width:fit-content;">
              Período selecionado: {label_periodo}
          </div>
        </div>
        <div style="min-width:320px;">
          <div class="hero-value-label">Faturamento</div>
          <div class="hero-value">R$ {faturamento:,.2f}</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # KPIs — metric-card estilo local (dashed border + barra colorida)
    def metric_card(col, title, value, sub, color):
        col.markdown(f"""<div class="metric-card" style="--accent:{color};">
            <div class="metric-title">{title}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-pill">{sub}</div>
        </div>""", unsafe_allow_html=True)

    def kpi_card(col, title, value, color="#1F2937"):
        col.markdown(f"""<div class="kpi-card">
            <div class="kpi-title">{title}</div>
            <div class="kpi-value" style="color:{color};">{value}</div>
        </div>""", unsafe_allow_html=True)

    pct = lambda v: f"{v/faturamento*100:.1f}%" if faturamento else "0%"
    fat_cancel   = canceladas["Receita Bruta"].sum()
    ticket       = faturamento / len(aprovadas) if len(aprovadas) > 0 else 0
    lucro_venda  = lucro_total / len(aprovadas) if len(aprovadas) > 0 else 0

    st.markdown('<div class="card">', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    metric_card(c1, "Tarifas",    f"R$ {tarifas:,.2f}",   pct(tarifas),    "#FBBF24")
    metric_card(c2, "Custos",     f"R$ {custos:,.2f}",    pct(custos),     "#8B5CF6")
    metric_card(c3, "Impostos",   f"R$ {impostos:,.2f}",  pct(impostos),   "#64748B")
    metric_card(c4, "Canceladas", f"R$ {fat_cancel:,.2f}", f"{len(canceladas)} vendas", "#EF4444")

    st.markdown("<br>", unsafe_allow_html=True)
    left, right = st.columns([1,1])
    with left:
        st.markdown(f"""<div class="metric-card" style="--accent:#E5E7EB;min-height:138px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                    <div style="font-size:22px;font-weight:900;color:#020617;margin-bottom:12px;">Ticket Médio</div>
                    <div class="muted">De vendas</div>
                    <div style="font-size:24px;font-weight:900;color:#020617;">R$ {ticket:,.2f}</div>
                </div>
                <div>
                    <div class="muted">Lucro por venda</div>
                    <div style="font-size:24px;font-weight:900;color:#020617;">R$ {lucro_venda:,.2f}</div>
                </div>
            </div>
        </div>""", unsafe_allow_html=True)
    with right:
        box_cls = "green-box" if lucro_total >= 0 else "red-box"
        st.markdown(f"""<div class="{box_cls}">
            <div class="green-title">Lucro Líquido Real</div>
            <div class="green-value">R$ {lucro_total:,.2f}</div>
            <div class="green-sub">Margem real: {margem_real:.2f}%</div>
        </div>""", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # 6 KPI-cards bege (Receita, Taxas, Frete, Custo, Lucro, Margem)
    k1, k2, k3 = st.columns(3)
    kpi_card(k1, "Receita Bruta",  f"R$ {faturamento:,.2f}")
    kpi_card(k2, "Taxas ML",       f"R$ {tarifas:,.2f}",    "#EF4444")
    kpi_card(k3, "Frete ML",       f"R$ {fretes_sum:,.2f}", "#EF4444")
    k4, k5, k6 = st.columns(3)
    kpi_card(k4, "Custo Produto",  f"R$ {custos:,.2f}",     "#EF4444")
    kpi_card(k5, "Lucro Real",     f"R$ {lucro_total:,.2f}", "#059669" if lucro_total >= 0 else "#DC2626")
    kpi_card(k6, "Margem",         f"{margem_real:.2f}%",   "#B45309")

    st.markdown("<br>", unsafe_allow_html=True)

    # Preparar dados dos gráficos
    daily = aprovadas.copy()
    daily["Dia"] = pd.to_datetime(daily["Data"]).dt.date
    daily_agg = daily.groupby("Dia").agg(
        Lucro=("Lucro","sum"), Receita=("Receita Bruta","sum"), Quantidade=("Quantidade","sum")
    ).reset_index()
    daily_agg["Dia"] = pd.to_datetime(daily_agg["Dia"])
    media_lucro = daily_agg["Lucro"].mean()
    daily_agg["Cor"] = daily_agg["Lucro"].apply(lambda x: "Acima" if x >= media_lucro else "Abaixo")

    qty_daily = aprovadas.copy()
    qty_daily["Dia"] = pd.to_datetime(qty_daily["Data"]).dt.date
    qty_agg = qty_daily.groupby(["Dia","SKU"]).agg(Quantidade=("Quantidade","sum")).reset_index()
    qty_agg["Dia"] = pd.to_datetime(qty_agg["Dia"])

    cores_sku = ["#7C3AED","#0EA5E9","#F59E0B","#16A34A","#EF4444"]
    skus      = qty_agg["SKU"].unique().tolist() if not qty_agg.empty else []
    cor_map   = {s: cores_sku[i % len(cores_sku)] for i, s in enumerate(skus)}
    media_sku = qty_agg.groupby("SKU")["Quantidade"].mean().reset_index().rename(columns={"Quantidade":"Media"}) if not qty_agg.empty else pd.DataFrame()

    # Layout duas colunas
    gc1, gc2 = st.columns(2)

    # ── Coluna esquerda: Resumo de Vendas ──
    with gc1:
        st.markdown('<div class="card" style="height:100%;">', unsafe_allow_html=True)
        st.markdown("**Resumo de Vendas**")
        st.markdown(f'<div style="color:#64748B;font-size:13px;margin-bottom:16px;">{label_periodo}</div>', unsafe_allow_html=True)

        # Métricas em grid 2x2
        qtd_vendas = int(aprovadas["Quantidade"].sum())
        qtd_cancel = len(canceladas)
        val_cancel = canceladas["Receita Bruta"].sum()



        m1, m2 = st.columns(2)
        with m1:
            st.markdown(f"""
            <div style="margin-bottom:20px;">
                <div style="font-size:12px;font-weight:700;color:#7C3AED;">Vendas</div>
                <div style="font-size:36px;font-weight:900;color:#0F172A;line-height:1.1;">{len(aprovadas)}</div>
                <div style="font-size:12px;color:#64748B;">{qtd_vendas} unidades</div>
            </div>
            <div>
                <div style="font-size:12px;font-weight:700;color:#7C3AED;">Ticket médio</div>
                <div style="font-size:28px;font-weight:900;color:#0F172A;line-height:1.1;">R$ {faturamento/len(aprovadas):.2f}</div>
                <div style="font-size:12px;color:#64748B;">lucro/venda R$ {lucro_total/len(aprovadas):.2f}</div>
            </div>
            """, unsafe_allow_html=True)
        with m2:
            st.markdown(f"""
            <div style="margin-bottom:20px;">
                <div style="font-size:12px;font-weight:700;color:#EF4444;">Cancelamentos</div>
                <div style="font-size:36px;font-weight:900;color:#0F172A;line-height:1.1;">{qtd_cancel}</div>
                <div style="font-size:12px;color:#64748B;">R$ {val_cancel:,.2f}</div>
            </div>
            <div>
                <div style="font-size:12px;font-weight:700;color:#16A34A;">Receita</div>
                <div style="font-size:28px;font-weight:900;color:#0F172A;line-height:1.1;">R$ {faturamento:,.2f}</div>
                <div style="font-size:12px;color:#7C3AED;font-weight:700;">margem {margem_real:.2f}%</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Mini gráfico de receita por dia — gradiente roxo estilo local
        if len(daily_agg) > 1:
            mini = alt.Chart(daily_agg).mark_area(
                interpolate="monotone",
                color=alt.Gradient(gradient="linear",
                    stops=[alt.GradientStop(color="#8B5CF666",offset=0),
                           alt.GradientStop(color="#FFFFFF00",offset=1)],
                    x1=1,x2=1,y1=1,y2=0),
                line={"color":"#7C3AED","strokeWidth":3}
            ).encode(
                x=alt.X("Dia:T", title=None, axis=alt.Axis(labelAngle=0, format="%d/%m", labelFontSize=10)),
                y=alt.Y("Receita:Q", title=None),
                tooltip=[alt.Tooltip("Dia:T",format="%d/%m/%Y",title="Data"),
                         alt.Tooltip("Receita:Q",format=",.2f",title="Receita")]
            ).properties(height=230)
            st.altair_chart(mini, use_container_width=True)

        st.markdown('</div>', unsafe_allow_html=True)

    # ── Coluna direita: Resumo do Período (quantidade por SKU) ──
    with gc2:
        st.markdown('<div class="card" style="height:100%;">', unsafe_allow_html=True)
        st.markdown("**Resumo do Período**")
        st.markdown(f'<div style="color:#64748B;font-size:13px;margin-bottom:16px;">Quantidade vendida por dia no período selecionado — {label_periodo}</div>', unsafe_allow_html=True)

        if len(qty_agg) > 1:
            # Enriquecer qty_agg com receita e lucro por dia/SKU para o tooltip
            daily_sku = aprovadas.copy()
            daily_sku["Dia"] = pd.to_datetime(daily_sku["Data"]).dt.date
            daily_sku_agg = daily_sku.groupby(["Dia","SKU"]).agg(
                Quantidade=("Quantidade","sum"),
                Receita=("Receita Bruta","sum"),
                Lucro=("Lucro","sum"),
                Frete=("Frete","sum"),
                Tarifa=("Taxas ML","sum"),
            ).reset_index()
            daily_sku_agg["Dia"] = pd.to_datetime(daily_sku_agg["Dia"])
            daily_sku_agg["Margem"] = (daily_sku_agg["Lucro"] / daily_sku_agg["Receita"] * 100).round(1)
            daily_sku_agg["Receita_fmt"]   = daily_sku_agg["Receita"].apply(lambda x: f"R$ {x:,.2f}")
            daily_sku_agg["Lucro_fmt"]     = daily_sku_agg["Lucro"].apply(lambda x: f"R$ {x:,.2f}")

            base_qty = alt.Chart(daily_sku_agg)

            area_qty = base_qty.mark_area(interpolate="monotone", opacity=0.18, line=True).encode(
                x=alt.X("Dia:T", title=None, axis=alt.Axis(format="%d/%m", labelFontSize=10)),
                y=alt.Y("Quantidade:Q", title="Quantidade vendida", axis=alt.Axis(labelFontSize=10)),
                color=alt.Color("SKU:N", scale=alt.Scale(domain=skus, range=[cor_map[s] for s in skus]), legend=None),
            )
            pontos_qty = base_qty.mark_point(filled=True, size=70).encode(
                x="Dia:T",
                y="Quantidade:Q",
                color=alt.Color("SKU:N", scale=alt.Scale(domain=skus, range=[cor_map[s] for s in skus]), legend=None),
                tooltip=[
                    alt.Tooltip("Dia:T",        title="Data",     format="%d/%m/%Y"),
                    alt.Tooltip("SKU:N",         title="SKU"),
                    alt.Tooltip("Quantidade:Q",  title="Qtd vendida"),
                    alt.Tooltip("Receita_fmt:N", title="Receita"),
                    alt.Tooltip("Lucro_fmt:N",   title="Lucro"),
                    alt.Tooltip("Margem:Q",      title="Margem %", format=".1f"),
                ]
            )
            media_rules = alt.Chart(media_sku).mark_rule(strokeDash=[4,3], strokeWidth=1.5, opacity=0.5).encode(
                y="Media:Q",
                color=alt.Color("SKU:N", scale=alt.Scale(domain=skus, range=[cor_map[s] for s in skus]), legend=None)
            )
            st.altair_chart((area_qty + pontos_qty + media_rules).properties(height=240), use_container_width=True)
        else:
            st.info("Gráfico disponível com 2+ dias de dados.")

        # Legenda com média por SKU
        legenda_html = '<div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:4px;">'
        for _, ms in media_sku.iterrows():
            cor = cor_map.get(ms["SKU"], "#666")
            legenda_html += (f'<div style="display:flex;align-items:center;gap:6px;">'
                             f'<div style="width:10px;height:10px;border-radius:50%;background:{cor};opacity:.5;"></div>'
                             f'<span style="font-size:12px;color:#64748B;font-weight:600;">'
                             f'{ms["SKU"]} (média: {ms["Media"]:.1f}/dia)</span></div>')
        legenda_html += '</div>'
        st.markdown(legenda_html, unsafe_allow_html=True)

        # Período selecionado
        data_ini_str = pd.to_datetime(daily_agg["Dia"].min()).strftime("%d/%m/%Y")
        data_fim_str = pd.to_datetime(daily_agg["Dia"].max()).strftime("%d/%m/%Y")
        n_dias = (daily_agg["Dia"].max() - daily_agg["Dia"].min()).days + 1
        st.markdown(f"""
        <div style="margin-top:16px;display:flex;align-items:center;gap:12px;">
            <span style="font-size:13px;font-weight:700;color:#64748B;">Período selecionado:</span>
            <span style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;
                         padding:8px 16px;font-size:13px;font-weight:800;color:#1E1040;">
                📅 {n_dias} dias — {data_ini_str} - {data_fim_str}
            </span>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # GRÁFICO LUCRO POR DIA — gradiente verde + label de média
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="small-title">Lucro real por dia (R$)</div><br>', unsafe_allow_html=True)
    if len(daily_agg) > 1:
        # Adiciona margem média ao daily_agg para tooltip
        daily_agg["Margem"] = (daily_agg["Lucro"] / daily_agg["Receita"].replace(0,1) * 100).round(1)
        area_lucro = alt.Chart(daily_agg).mark_area(
            interpolate="monotone",
            color=alt.Gradient(gradient="linear",
                stops=[alt.GradientStop(color="#16A34A44",offset=0),
                       alt.GradientStop(color="#16A34A00",offset=1)],
                x1=1,x2=1,y1=1,y2=0)
        ).encode(x=alt.X("Dia:T",title=None), y=alt.Y("Lucro:Q",title=None))

        linha_lucro = alt.Chart(daily_agg).mark_line(
            interpolate="monotone", color="#16A34A", strokeWidth=3
        ).encode(x="Dia:T", y="Lucro:Q")

        pontos_lucro = alt.Chart(daily_agg).mark_point(filled=True, size=80).encode(
            x=alt.X("Dia:T",title=None), y=alt.Y("Lucro:Q",title=None),
            color=alt.Color("Cor:N", scale=alt.Scale(domain=["Acima","Abaixo"],
                            range=["#16A34A","#EF4444"]),
                            legend=alt.Legend(title="vs Média",
                                labelExpr="datum.label === 'Acima' ? '▲ Acima' : '▼ Abaixo'",
                                orient="top-right")),
            tooltip=[alt.Tooltip("Dia:T",title="Data",format="%d/%m/%Y"),
                     alt.Tooltip("Lucro:Q",title="Lucro R$",format=",.2f"),
                     alt.Tooltip("Receita:Q",title="Receita R$",format=",.2f"),
                     alt.Tooltip("Quantidade:Q",title="Qtd vendida",format=",.0f"),
                     alt.Tooltip("Margem:Q",title="Margem média %",format=".1f")])

        media_rule = alt.Chart(pd.DataFrame({"media":[media_lucro]})).mark_rule(
            color="#94A3B8", strokeDash=[6,4], strokeWidth=1.5
        ).encode(y=alt.Y("media:Q"),
                 tooltip=[alt.Tooltip("media:Q",title="Média do período R$",format=",.2f")])

        media_text = alt.Chart(pd.DataFrame({
            "media":[media_lucro], "Dia":[daily_agg["Dia"].max()]
        })).mark_text(align="right", dy=-8, fontSize=11, fontWeight=700, color="#64748B").encode(
            x=alt.X("Dia:T"), y=alt.Y("media:Q"),
            text=alt.value(f"Média: R$ {media_lucro:,.0f}")
        )

        st.altair_chart(
            (area_lucro + linha_lucro + pontos_lucro + media_rule + media_text).properties(height=300),
            use_container_width=True
        )
    else:
        st.info("Gráfico disponível com 2+ dias de dados.")
    st.markdown('</div>', unsafe_allow_html=True)

    # MARGEM PONDERADA POR SKU
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="small-title">Margem ponderada por SKU</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="muted">Faturamento, participação e margem ponderada — {label_periodo}</div><br>', unsafe_allow_html=True)

    pond = aprovadas.copy()
    sku_pond = pond.groupby("SKU").agg(
        Vendas=("Venda","count"),
        Unidades=("Quantidade","sum"),
        Receita=("Receita Bruta","sum"),
        Lucro=("Lucro","sum"),
    ).reset_index()
    sku_pond["Margem %"]      = (sku_pond["Lucro"] / sku_pond["Receita"].replace(0,1) * 100).round(2)
    sku_pond["Participação %"]= (sku_pond["Receita"] / sku_pond["Receita"].sum() * 100).round(1)
    sku_pond = sku_pond.sort_values("Receita", ascending=False).reset_index(drop=True)

    for _, sr in sku_pond.iterrows():
        cor_m = "#16A34A" if sr["Margem %"] >= 15 else "#B45309" if sr["Margem %"] >= 8 else "#DC2626"
        bar_w = min(int(sr["Participação %"] * 3), 100)
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:16px;padding:12px 0;border-bottom:1px solid #F1F5F9;">
            <div style="min-width:70px;font-weight:900;color:#7C3AED;font-size:15px;">{sr['SKU']}</div>
            <div style="flex:1;">
                <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                    <span style="font-weight:700;color:#0F172A;">R$ {sr['Receita']:,.2f}</span>
                    <span style="color:#64748B;font-size:13px;">{sr['Participação %']:.1f}% do faturamento</span>
                </div>
                <div style="background:#F1F5F9;border-radius:999px;height:6px;">
                    <div style="background:#7C3AED;width:{bar_w}%;height:6px;border-radius:999px;"></div>
                </div>
            </div>
            <div style="min-width:80px;text-align:center;">
                <div style="font-size:12px;color:#64748B;font-weight:600;">Vendas / Unid</div>
                <div style="font-weight:800;color:#0F172A;">{int(sr['Vendas'])} / {int(sr['Unidades'])}</div>
            </div>
            <div style="min-width:90px;text-align:right;">
                <div style="font-size:12px;color:#64748B;font-weight:600;">Lucro</div>
                <div style="font-weight:800;color:{cor_m};">R$ {sr['Lucro']:,.2f}</div>
            </div>
            <div style="min-width:70px;text-align:right;">
                <span style="background:{'#DCFCE7' if sr['Margem %']>=15 else '#FEF9C3' if sr['Margem %']>=8 else '#FEE2E2'};
                             color:{cor_m};border-radius:999px;padding:4px 12px;font-size:14px;font-weight:900;">
                    {sr['Margem %']:.2f}%
                </span>
            </div>
        </div>""", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # TABELA DE PEDIDOS DETALHADOS — dentro de expander
    st.markdown('<div class="card">', unsafe_allow_html=True)

    fat_total = aprovadas["Receita Bruta"].sum() or 1

    def badge(valor, total, bg, txt):
        pct = abs(valor/total*100) if total else 0
        return (f'<span style="font-weight:700;">R$ {valor:,.2f}</span> '
                f'<span style="background:{bg};color:{txt};border-radius:999px;'
                f'padding:2px 7px;font-size:11px;font-weight:800;">{pct:.0f}%</span>')

    def margem_badge(pct, lucro=None):
        bg  = "#DCFCE7" if pct>=15 else "#FEF9C3" if pct>=8 else "#FEE2E2"
        txt = "#15803D" if pct>=15 else "#854D0E" if pct>=8 else "#DC2626"
        lucro_str = f'<span style="font-weight:700;color:{txt};">R$ {lucro:,.2f}</span> ' if lucro is not None else ""
        return (f'{lucro_str}'
                f'<span style="background:{bg};color:{txt};border-radius:999px;'
                f'padding:2px 9px;font-size:12px;font-weight:800;">{pct:.1f}%</span>')

    def status_icon(s):
        return {"paid":"🚚","cancelled":"❌"}.get(s,"⏳")

    corr_map = {r: v for r, v in load_correcoes(str(user_id)).items()}

    linhas = ""
    for _, row in df.iterrows():
        cancelada = row["Cancelada"]
        bg_row    = "#FFF5F5" if cancelada else "white"
        rec       = row["Receita Bruta"]
        corrigido = row.get("Corrigido", False)
        fifo_flag = row.get("FIFO", False)

        tag_custo = ""
        if corrigido:
            tag_custo = '<span style="background:#FEF3C7;color:#92400E;border-radius:4px;padding:1px 5px;font-size:10px;font-weight:700;">CORRIGIDO</span> '
        elif fifo_flag:
            tag_custo = '<span style="background:#EDE9FE;color:#5B21B6;border-radius:4px;padding:1px 5px;font-size:10px;font-weight:700;">FIFO</span> '

        linhas += f"""<tr style="background:{bg_row};border-bottom:1px solid #F1F5F9;">
            <td style="padding:10px 8px;font-weight:800;color:#7C3AED;white-space:nowrap;">{row['SKU']}</td>
            <td style="padding:10px 8px;color:#64748B;font-size:13px;white-space:nowrap;">{pd.to_datetime(row['Data']).strftime('%d/%m/%Y %H:%M')}</td>
            <td style="padding:10px 8px;font-size:18px;text-align:center;">{status_icon(row['Status'])}</td>
            <td style="padding:10px 8px;text-align:center;font-weight:700;">{int(row['Quantidade'])}</td>
            <td style="padding:10px 8px;font-weight:700;">{badge(rec, fat_total,'#DCFCE7','#15803D')}</td>
            <td style="padding:10px 8px;">{'–' if cancelada else badge(row['Frete'], rec,'#DBEAFE','#1D4ED8')}</td>
            <td style="padding:10px 8px;">{'–' if cancelada else badge(row['Taxas ML'], rec,'#FEF3C7','#B45309')}</td>
            <td style="padding:10px 8px;">{'–' if cancelada else f'{tag_custo}{badge(row["Custo Total"], rec, "#EDE9FE","#6D28D9")}'}</td>
            <td style="padding:10px 8px;">{'–' if cancelada else badge(row['Imposto'], rec,'#F1F5F9','#475569')}</td>
            <td style="padding:10px 8px;text-align:center;">{'<span style="color:#DC2626;font-weight:700;">Cancelada</span>' if cancelada else margem_badge(row.get('Margem %',0), row.get('Lucro',0))}</td>
            <td style="padding:10px 8px;color:#94A3B8;font-size:12px;white-space:nowrap;">{row['Venda']}</td>
        </tr>"""

    tabela_html = f"""<div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-family:'Inter',sans-serif;font-size:13px;">
        <thead><tr style="background:#F8FAFC;border-bottom:2px solid #E2E8F0;">
            <th style="padding:10px 8px;text-align:left;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">SKU</th>
            <th style="padding:10px 8px;text-align:left;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">Data</th>
            <th style="padding:10px 8px;text-align:center;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">Transp.</th>
            <th style="padding:10px 8px;text-align:center;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">Qnt.</th>
            <th style="padding:10px 8px;text-align:left;color:#16A34A;font-size:11px;font-weight:800;text-transform:uppercase;">Receita (=)</th>
            <th style="padding:10px 8px;text-align:left;color:#1D4ED8;font-size:11px;font-weight:800;text-transform:uppercase;">Frete (-)</th>
            <th style="padding:10px 8px;text-align:left;color:#B45309;font-size:11px;font-weight:800;text-transform:uppercase;">Tarifa (-)</th>
            <th style="padding:10px 8px;text-align:left;color:#6D28D9;font-size:11px;font-weight:800;text-transform:uppercase;">Custo (-)</th>
            <th style="padding:10px 8px;text-align:left;color:#475569;font-size:11px;font-weight:800;text-transform:uppercase;">Imposto (-)</th>
            <th style="padding:10px 8px;text-align:center;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">M. de Contrib. (=)</th>
            <th style="padding:10px 8px;text-align:left;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">N.º Venda</th>
        </tr></thead>
        <tbody>{linhas}</tbody>
    </table></div>"""

    with st.expander(f"🧾 Ver vendas detalhadas ({len(df)} pedidos)", expanded=False):
        st.markdown(tabela_html, unsafe_allow_html=True)
        # CORREÇÕES MANUAIS dentro do mesmo expander
        st.markdown("<br>", unsafe_allow_html=True)
        with st.expander("✏️ Corrigir custo de vendas manualmente"):
            vendas_opts = {f"{row['Venda']} — {row['SKU']} {pd.to_datetime(row['Data']).strftime('%d/%m %H:%M')} R${row['Receita Bruta']:.2f}": str(row['Venda'])
                           for _, row in df[~df["Cancelada"]].iterrows()}
            venda_sel    = st.selectbox("Selecione a venda", list(vendas_opts.keys()), key="corr_venda")
            venda_id_sel = vendas_opts[venda_sel]
            row_sel      = df[df["Venda"] == venda_id_sel].iloc[0]
            custo_atual  = corr_map.get(venda_id_sel, {}).get("custo_unitario", row_sel["Custo Unitário"])
            cc1, cc2 = st.columns(2)
            with cc1:
                novo_custo = st.number_input("Custo unitário correto (R$)", value=float(custo_atual), step=0.01, format="%.4f", key="corr_custo")
            with cc2:
                motivo = st.text_input("Motivo (opcional)", key="corr_motivo")
            bc1, bc2 = st.columns(2)
            with bc1:
                if st.button("💾 Salvar correção", type="primary", use_container_width=True):
                    save_correcao(str(user_id), venda_id_sel, novo_custo, motivo)
                    st.success(f"Custo da venda {venda_id_sel} corrigido para R$ {novo_custo:.4f}")
                    st.cache_data.clear()
                    st.rerun()
            with bc2:
                if venda_id_sel in corr_map:
                    if st.button("🗑️ Remover correção", use_container_width=True):
                        delete_correcao(str(user_id), venda_id_sel)
                        st.success("Correção removida.")
                        st.cache_data.clear()
                        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════
# ABA: CADASTRO DE CUSTOS
# ══════════════════════════════════════════
elif st.session_state["aba_ativa"] == "custos":
    st.markdown("""<div class="hero">
        <h1 class="hero-title">Cadastro de Custos</h1>
        <div style="opacity:.85;font-size:15px;margin-top:8px;">Custos por SKU e vigência — FIFO ativo a partir de 20/05/2026</div>
    </div>""", unsafe_allow_html=True)

    custos_df = load_custos(str(user_id))

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**➕ Novo lote / vigência**")
    with st.form("form_custo", clear_on_submit=True):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            sku_novo     = st.text_input("SKU", placeholder="ex: S_001")
            produto_novo = st.text_input("Produto", placeholder="ex: Seladora Térmica")
        with fc2:
            vigencia_nova = st.date_input("Vigência (início)", value=None)
            custo_produto = st.number_input("Custo unitário (R$)", min_value=0.0, step=0.01, format="%.4f")
        with fc3:
            qtd_comprada = st.number_input("Qtd comprada", min_value=0, step=1)
            frete_forn   = st.number_input("Frete fornecedor (R$)", min_value=0.0, step=0.01, format="%.2f")
        fc4, fc5, fc6 = st.columns(3)
        with fc4:
            embalagem   = st.number_input("Embalagem (R$)", min_value=0.0, step=0.01, format="%.2f")
        with fc5:
            outros      = st.number_input("Outros custos (R$)", min_value=0.0, step=0.01, format="%.2f")
        with fc6:
            margem_alvo = st.number_input("Margem alvo (%)", min_value=0.0, step=0.1, format="%.1f")
        obs = st.text_input("Observação", placeholder="opcional")
        if st.form_submit_button("💾 Salvar lote", type="primary", use_container_width=True):
            if not sku_novo:
                st.error("SKU é obrigatório.")
            else:
                qtd = int(qtd_comprada) if qtd_comprada > 0 else 1
                save_custo(str(user_id), {
                    "sku": sku_novo.strip().upper(), "produto": produto_novo,
                    "vigencia": vigencia_nova.strftime("%Y-%m-%d") if vigencia_nova else None,
                    "qtd_comprada": int(qtd_comprada), "qtd_disponivel": int(qtd_comprada),
                    "custo_produto": round(custo_produto, 4),
                    "frete_fornecedor": round(frete_forn, 4),
                    "embalagem": round(embalagem, 4),
                    "outros_custos": round(outros, 4),
                    "margem_alvo": round(margem_alvo, 2),
                    "observacao": obs,
                })
                st.success(f"Lote {sku_novo} salvo!")
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    if not custos_df.empty:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("**📋 Lotes cadastrados**")
        exibir = custos_df[["id","sku","produto","vigencia","qtd_comprada","qtd_disponivel",
                             "custo_produto","frete_fornecedor","embalagem","outros_custos","margem_alvo","observacao"]].copy()
        exibir["vigencia"]       = exibir["vigencia"].apply(lambda x: x.strftime("%d/%m/%Y") if pd.notna(x) else "Sem data")
        exibir["qtd_disponivel"] = exibir["qtd_disponivel"].apply(lambda x: "ESGOTADO" if float(x)<0 else str(int(float(x))))
        for c in ["custo_produto","frete_fornecedor","embalagem","outros_custos"]:
            exibir[c] = exibir[c].apply(lambda x: f"R$ {float(x):.4f}")
        exibir["margem_alvo"] = exibir["margem_alvo"].apply(lambda x: f"{float(x):.1f}%")
        st.dataframe(exibir.rename(columns={"id":"ID","sku":"SKU","produto":"Produto","vigencia":"Vigência",
            "qtd_comprada":"Qtd Comprada","qtd_disponivel":"Qtd Disponível","custo_produto":"Custo Unit.",
            "frete_fornecedor":"Frete Forn.","embalagem":"Embalagem","outros_custos":"Outros",
            "margem_alvo":"Margem Alvo","observacao":"Obs."}), use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════
# ABA: REGIME TRIBUTÁRIO
# ══════════════════════════════════════════
elif st.session_state["aba_ativa"] == "regime":
    st.markdown("""<div class="hero">
        <h1 class="hero-title">Regime Tributário</h1>
        <div style="opacity:.85;font-size:15px;margin-top:8px;">Alíquotas por período — aplicadas automaticamente por data de venda</div>
    </div>""", unsafe_allow_html=True)

    regime_df = load_regime(str(user_id))

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**➕ Novo regime / alíquota**")
    with st.form("form_regime", clear_on_submit=True):
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            vig_reg  = st.date_input("Vigência (a partir de)", value=date.today())
        with rc2:
            regime   = st.selectbox("Regime", ["Simples Nacional","Lucro Presumido","Lucro Real","MEI","Isento"])
        with rc3:
            aliquota = st.number_input("Alíquota (%)", min_value=0.0, max_value=100.0, step=0.01, format="%.2f")
        if st.form_submit_button("💾 Salvar", type="primary", use_container_width=True):
            save_regime(str(user_id), {
                "vigencia": vig_reg.strftime("%Y-%m-%d"),
                "regime": regime,
                "aliquota": aliquota,
            })
            st.success(f"{regime} {aliquota:.2f}% a partir de {vig_reg.strftime('%d/%m/%Y')}")
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    if not regime_df.empty:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("**📋 Regimes cadastrados**")
        exibir = regime_df[["id","vigencia","regime","aliquota"]].copy()
        exibir["vigencia"]  = exibir["vigencia"].apply(lambda x: x.strftime("%d/%m/%Y") if pd.notna(x) else "–")
        exibir["aliquota"]  = exibir["aliquota"].apply(lambda x: f"{float(x):.2f}%")
        st.dataframe(exibir.rename(columns={"id":"ID","vigencia":"Vigência","regime":"Regime","aliquota":"Alíquota"}),
                     use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════
# ABA: CAIXA / CAPITAL
# ══════════════════════════════════════════
elif st.session_state["aba_ativa"] == "caixa":
    st.markdown("""<div class="hero">
        <h1 class="hero-title">Caixa & Capital</h1>
        <div style="opacity:.85;font-size:15px;margin-top:8px;">Controle de capital investido e retorno sobre investimento</div>
    </div>""", unsafe_allow_html=True)

    capital_df = load_capital(str(user_id))

    # Cards de resumo
    if not capital_df.empty:
        total_investido = capital_df[capital_df["valor"] > 0]["valor"].sum()
        total_retirado  = capital_df[capital_df["valor"] < 0]["valor"].abs().sum()
        saldo_capital   = capital_df["valor"].sum()

        ck1, ck2, ck3 = st.columns(3)
        ck1.markdown(f"""<div class="metric-card">
            <div class="metric-label" style="color:#7C3AED;">Capital Investido</div>
            <div class="metric-value">R$ {total_investido:,.2f}</div>
        </div>""", unsafe_allow_html=True)
        ck2.markdown(f"""<div class="metric-card">
            <div class="metric-label" style="color:#EF4444;">Retiradas</div>
            <div class="metric-value">R$ {total_retirado:,.2f}</div>
        </div>""", unsafe_allow_html=True)
        ck3.markdown(f"""<div class="metric-card">
            <div class="metric-label" style="color:#16A34A;">Saldo em Caixa</div>
            <div class="metric-value">R$ {saldo_capital:,.2f}</div>
        </div>""", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

    # Formulário novo lançamento
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**➕ Novo lançamento**")
    with st.form("form_capital", clear_on_submit=True):
        cap1, cap2, cap3 = st.columns(3)
        with cap1:
            cap_data  = st.date_input("Data", value=date.today())
            cap_valor = st.number_input("Valor (R$) — negativo para retirada", step=0.01, format="%.2f")
        with cap2:
            cap_desc  = st.text_input("Descrição", placeholder="ex: Compra lote S_001")
            cap_cat   = st.selectbox("Categoria", ["Compra de estoque","Taxa/Tarifa","Retirada","Aporte","Outro"])
        with cap3:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
        if st.form_submit_button("💾 Registrar", type="primary", use_container_width=True):
            if cap_valor == 0:
                st.error("Valor não pode ser zero.")
            else:
                save_capital(str(user_id), {
                    "data": cap_data.strftime("%Y-%m-%d"),
                    "valor": cap_valor,
                    "descricao": cap_desc,
                    "categoria": cap_cat,
                })
                st.success(f"Lançamento de R$ {cap_valor:.2f} registrado.")
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    # Histórico
    if not capital_df.empty:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("**📋 Histórico de lançamentos**")
        exibir = capital_df[["id","data","descricao","categoria","valor"]].copy()
        exibir["data"]  = exibir["data"].apply(lambda x: x.strftime("%d/%m/%Y") if pd.notna(x) else "–")
        exibir["valor"] = exibir["valor"].apply(lambda x: f"R$ {x:,.2f}")
        st.dataframe(exibir.rename(columns={"id":"ID","data":"Data","descricao":"Descrição",
                                             "categoria":"Categoria","valor":"Valor"}),
                     use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("Nenhum lançamento registrado ainda.")
