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

    def _fetch(params_extra):
        """Busca paginada com parâmetros extras."""
        results_all, offset, limit = [], 0, 50
        while True:
            params = {"seller": user_id, "sort": "date_desc",
                      "offset": offset, "limit": limit, **params_extra}
            resp = requests.get(f"{ML_API_BASE}/orders/search",
                                headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                break
            data    = resp.json()
            results = data.get("results", [])
            results_all.extend(results)
            paging  = data.get("paging", {})
            offset += limit
            if offset >= paging.get("total", 0) or not results:
                break
        return results_all

    # 1) Ordens criadas no período (aprovadas + canceladas antes de sair do período)
    ordens_criadas = _fetch({
        "order.date_created.from": date_from,
        "order.date_created.to":   date_to,
    })

    # 2) Ordens canceladas/fechadas NO período (podem ter sido criadas antes)
    ordens_fechadas = _fetch({
        "order.date_closed.from": date_from,
        "order.date_closed.to":   date_to,
        "order.status":           "cancelled",
    })

    # Mesclar sem duplicatas (usar order id como chave)
    seen = {str(o.get("id")): o for o in ordens_criadas}
    for o in ordens_fechadas:
        oid = str(o.get("id"))
        if oid not in seen:
            seen[oid] = o

    return list(seen.values())


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
            data = resp.json()
            opt  = data.get("shipping_option", {})
            # Estrutura ML:
            # list_cost = frete total (vendedor + comprador)
            # cost      = parte extra paga pelo comprador (excede o padrão)
            # custo do vendedor = list_cost - cost
            list_cost = float(opt.get("list_cost") or 0)
            extra_cost = float(opt.get("cost") or 0)
            cost = max(list_cost - extra_cost, 0)
            return sid, cost
        except:
            return sid, 0.0
    fretes = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for future in as_completed({ex.submit(fetch_one, sid): sid for sid in shipping_ids_tuple}):
            sid, custo = future.result()
            fretes[sid] = custo
    return fretes

def parse_orders(orders, fretes=None, reembolsados=None, token=""):
    import requests
    import streamlit as st
    import pandas as pd
    
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
            
            # ==========================================
            # INÍCIO DA NOVA LÓGICA DE CANCELADOS
            # ==========================================
            if cancelada:
                receita = unit_price * qty  # MANTÉM A RECEITA para o card "Canceladas" não zerar
                sale_fee = 0.0
                frete = 0.0
                
                # Busca frete reverso na API — mesma lógica do diagnóstico
                if shipping_id and token:
                    try:
                        r = requests.get(
                            f"https://api.mercadolibre.com/shipments/{shipping_id}",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=10
                        )
                        if r.status_code == 200:
                            import json as _j
                            sd          = r.json()
                            ship_status = sd.get("status", "")
                            opt         = sd.get("shipping_option", {})
                            bc          = float(sd.get("base_cost") or 0)
                            lc          = float(opt.get("list_cost") or 0)
                            ec          = float(opt.get("cost") or 0)
                            import streamlit as _st
                            if ship_status in ("delivered", "not_delivered"):
                                # Buscar motivo de cancelamento da order
                                r2 = requests.get(
                                    f"https://api.mercadolibre.com/orders/{order_id}/cancellations",
                                    headers={"Authorization": f"Bearer {token}"},
                                    timeout=10
                                )
                                cancel_data = r2.json() if r2.status_code == 200 else {}
                                _st.write(f"🔍 order={order_id} | status={ship_status} | bc={bc} | lc={lc} | cancellations={_j.dumps(cancel_data)[:300]}")
                            if ship_status in ("delivered", "not_delivered"):
                                frete_ida     = max(lc - ec, 0)
                                frete_reverso = bc
                                frete = frete_ida + frete_reverso
                    except Exception as e:
                        import streamlit as _st
                        _st.write(f"❌ {order_id} {e}")
                
                # O repasse do ML é apenas o débito do frete reverso (prejuízo)
                total_ml = -frete
                
            else:
                # Lógica original para vendas aprovadas
                sale_fee   = abs(float(item.get("sale_fee", 0) or 0)) * qty
                frete      = float(fretes.get(shipping_id, 0) or 0)
                receita    = unit_price * qty
                total_ml   = receita - sale_fee - frete
            # ==========================================
            # FIM DA NOVA LÓGICA
            # ==========================================

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
        # Atualiza qtd_disponivel apenas dos lotes que foram efetivamente consumidos agora
        lotes_alterados = custos_out[custos_out["qtd_disponivel"] != custos_df["qtd_disponivel"]]
        if not lotes_alterados.empty:
            save_custos_batch(user_id, lotes_alterados)

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

/* ── Mobile responsivo ── */
@media (max-width: 768px) {
    .block-container { padding: 0 8px 2rem 8px !important; }
    .hero { padding: 20px 18px !important; min-height: auto !important; }
    .hero-title { font-size: 32px !important; }
    .hero-value { font-size: 36px !important; }
    .card { padding: 16px !important; border-radius: 16px !important; }
    .metric-card { min-height: auto !important; padding: 16px !important; }
    .metric-title { font-size: 16px !important; }
    .metric-value { font-size: 22px !important; }
    .kpi-card { min-height: auto !important; padding: 14px 10px !important; }
    .kpi-value { font-size: 18px !important; }
    .kpi-title { font-size: 11px !important; }
    .green-box, .red-box { padding: 18px !important; }
    .green-value { font-size: 28px !important; }
    .small-title { font-size: 20px !important; }
    /* Tabelas HTML — scroll horizontal */
    div[data-testid="stMarkdownContainer"] table { font-size: 11px !important; }
    div[data-testid="stMarkdownContainer"] td,
    div[data-testid="stMarkdownContainer"] th { padding: 6px 4px !important; }
    /* Navbar */
    .navbar { padding: 0 12px !important; height: 44px !important; }
    .navbar-name { font-size: 12px !important; letter-spacing: 0.5px !important; }
}
</style>
""", unsafe_allow_html=True)

# =========================
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
    _auth_url = get_auth_url()
    st.markdown("""<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    html, body {
        height: 100%;
        background-color: #060d1f !important;
    }

    /* Remove padding/margin do Streamlit */
    [data-testid="stAppViewContainer"] {
        padding: 0 !important;
        background-color: #060d1f !important;
    }
    [data-testid="stAppViewBlockContainer"] {
        padding: 0 !important;
        max-width: 100% !important;
    }
    [data-testid="stVerticalBlock"] {
        gap: 0 !important;
        padding: 0 !important;
    }
    header, footer, [data-testid="stToolbar"],
    [data-testid="stDecoration"], [data-testid="stStatusWidget"] {
        display: none !important;
    }

    .login-wrap {
        position: fixed;
        inset: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        background: radial-gradient(ellipse at 50% 40%, #0a1a4a 0%, #060d1f 70%);
        gap: 36px;
    }

    .logo-img {
        width: 320px;
        max-width: 88vw;
        border-radius: 24px;
        filter: drop-shadow(0 0 40px rgba(59,130,246,0.4));
    }

    .ml-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
        background: linear-gradient(135deg, #2563EB 0%, #7C3AED 100%);
        color: white !important;
        text-decoration: none !important;
        font-family: 'Inter', sans-serif;
        font-size: 16px;
        font-weight: 700;
        padding: 16px 40px;
        border-radius: 16px;
        box-shadow: 0 8px 32px rgba(37,99,235,0.45), 0 0 0 1px rgba(255,255,255,0.1) inset;
        letter-spacing: 0.3px;
        transition: all .2s ease;
        min-width: 260px;
    }

    .ml-btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 14px 40px rgba(37,99,235,0.6), 0 0 0 1px rgba(255,255,255,0.15) inset;
    }
    </style>""", unsafe_allow_html=True)

    st.markdown(f"""
    <div class="login-wrap">
        <img class="logo-img" src="data:image/png;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCATmBOYDASIAAhEBAxEB/8QAHQABAQACAgMBAAAAAAAAAAAAAAECAwQHBQYICf/EAGoQAAIBAgMEBQYGCggTBQcCBwABAgMRBAUhBhIxQQdRYXGBCBMiMpGhFEJScrHRFSMzU2KCkpTB0hZDc4OTorKzCRgkNDU2REVUVWNldHWEtNPh8BclZJXCJic3haPD1OJGKDjE8UdWpP/EABwBAQEAAgMBAQAAAAAAAAAAAAABAgMEBQYHCP/EAEMRAQABAwEEBgcGAwcEAwEBAAABAgMRBBIhMVEFBkGRodETYXGBscHwFCIyQlLhFjPxFSMkU2JykkOCstI0NaIl4v/aAAwDAQACEQMRAD8A+OUO8IpzGo0HMpAAFi25gA9QCgAAgVDxAAcyDmBdAAUCgWKIwUgABXsCAXQhbalFRCk5gGBYqQBFIEVCw4BAATkZMgFQuQqCKTQIMBxCHILiBSaAWAcrgcAUOJUQpRQQr1AEL9JAijmQqKCCBAMkCIAC3HeABCixURjTkCgQoIUVcBfUDuArIuAHaUUABB8QCAXQligoIIItwgS5WQooCAFRAUIgAAquLgFAAFFCIuBRAqZTEXLlGQIilRPEAIigY7AUETgXkSxRV1lIADuQrIAYAIKuIfGwDAEKGBAAQAwAqNELzIyByABAIykZFhO8MPvISVOYBQICrgCAnoQDkFCBF5EEIykaMVQAElRkZSAQoBBAARQj4lIwoACCPgOYBFGgAAsQoYEYFu0EE5EfaZMj4kVO1Cw4AijAAGYARWKgDUAOQHIB3jmCIDIMhSoIlykYAo4ABx7ik5lLAviORAADHIFELyIORBQEXmURDxKORQt2gAIBAMC9o6ggAZEUiCL2kLzKBOQKQAx3DxHiUUDmQCkCKUANSK4F7CpkL2gAChEAYv1FAAMAF1gAUEBUUBgKBhgqAY7CAUAcCgB3lQBFZChEFijQCFQBUBzFwUGAUCAAIAAAUhUUOQKAA0AKABSicwAECjkLAAgEAABQZC8gUAAwgycyk1CiLy1AADtDBAuACiMWKCYEABAIA+sihGUhADAII+JCgMkRdLXJwKQQFJzAEDD0IqFCBBGTiUhiqDgAFOQAIJ3AACApDFQjKAqApGQAAFQNlIQAABAykIHMgYCjJy1AIqAcwQbEUhWVipAwgIVk8SoCPgVAjKKVEL2AUhQETlqOY7xrcAuJQCwAA5lBFYJyACwKgCBeQKIB4gAUIBAhQAQt2kKtAAsEVBEFyhAAAUQcwhzAFBOwCgAoAFuA5hDvKA5ApAg+wAFAW7R4jQoB8R3AAEC8giMFAAWBNSiksUMAArcwVDigEO4ByKQq4lABcQEANR2gO0MDxKAQ5gCk5gAUEKEAgUogYBQKQoAAAOYRQVApAUVkYYABDUFApEUCACwQAsGFA2AAABAAAABkYBgAihHwAIFyPgUjZAAXeTmQAAFTQo7iMinIAgFZCojIsJqXmRFIIyMpLEUREARQDkQgAAggBSKgACjIUxIKAGBAGLhQcgCCMMDxAgHEIijMS8iEUABBtABWINA2AAL4jQonEBgInOxeQLxAIq10IVXAEKCgBzAAvAEKAFwAACAqKRFKiAAKFIUIEBQC4kBQBSWARQECgAABLAcwKOZCgAAUUAICgBhDsAGtygCkApAyW1AyHIhSouhOYbBRLjiUAPEAIACk4cQA1A5FQuAAABQJzKTUvYUAu8AqCAAAcgAhxBSoCAAoBgAAByAFIVFFQRClQRQQqKAgAIysj4lAhQFS5eRCgBzFwgAAYQAKBAUhFCX1KyAC9hAQUjAAjIVksRVIAQCFCYURHoUMghBccgqacgUhiAYDCoGXtHEgj4EZSNIkqi7QikIpYMpGQRAvMgAAEVAUhFQBggAAAyFZCKDxAAhbEABkKERUaIUjIHMAEVs5AArEDA7QKQFKFu0BcShEHPQFAAmtyoBzA56FKD1BOAb1ApACgByADmUAAUhSgGAAXYAgEGLAAOYtqCgEOYQKiFQAADsHEByA5XAB2BUiWALtCBe0oq4AIBFIOQLAIW7SgARlYCIwGEUUEBRfAAeIAr4EQAaAFAgHgVFBhgcwiMhWGARSIviVAd5ChVSug+oIcSoPiB2BFQBdABCoheYAAALFHIFEsCsBEACKqopFxKVAMAqCALysEQMMMCBlIFAikCoUchyADmAAKQrCIyFZCKvFAAggKRgOQAAAE5kUBScwDRCshAHIgbIoyIX0AU5i45ggEAZJIAwCKjABBGQoIyQXKQgAXADgRlRCAQAihCsEVABzAEKyBQDkCAQpAAA7CSIAQkshgAg2cgCczJipRyAAAIBcIFCA7gAIUACoNkDKDA7wUORRYALAK3aABSAoAcARAq4EQ7CqqBC8wgUAAAAA5gFQ4AItwDIO8AAgEBQgCoDqKyBVt2jkAwh3i7BUUAEOYBgAIAAoAAodg7wVgAgAAAKgwAwHMrQQKIxxRQERDuA94AFI+JQKuAZAKuIHIIqKLAFDvJzKx3EDkUAoAXFwgAQooQsABSACgAqBSAopAAgAyBVBCgQcCgCAC4AAAAARRjkGUCcyFIQAAAIy94IoCAByIysxIsKRgMggCBFOIYuQCkuAtCKDkO0ciCXABBAUhFCFuQKeAY5ggEDKyCAgIoGi8iMKWAI7kAhWTUKAheRBHqAHwABkBFGQpOJJC4AIrNFIgVivIpClADnwHMIdoQCAoAAjKQoApOYKHIvImo7ACKQNFFHFkKAKABAUhUF3gBhTmXQIadYReYJ2scwKiXFwBSFQKgATmAA5hcQKkVEXWUCoAIoveQoCMQVoAOLAC4XKKAOQQHIFRQGtxYFELZAaAOQ5AagAGQqKBYAAQoFIC2KIXkQqCABXqWBBz4goEVi8SIoQIUMoIAFDmPEAAGAAABUCrgCBVQYAQAfAAAUhRSagBFIAA8QB3BQpECoveQAA2AAoACIDS4DCgILgVkKQgMAAAwRkUfAhe8hAIVkZFAEwBLBjmH1kVAAmRQDmAABGQAByIpoQpCCAAKMlykIAD4EIAAIyByAAgDD4EEA5BBUCKCCEXAqAEA7AyKELyIQQFBFZhBArFQiXKUXQBAIF5kAFBLlAAAAAEUAVcCFBFHMAAB3AUc+IBQQAAADgALoTmEEBqWxOBQH0CwswgikQQFAAEtqNSgoFIXUClIAKAAid45AaFAcxzAF5gnAoCyLyIUqDKQFCxbE5FAWJzLoTmUEUAIgLyAEsLIpAHYUcg+BUAgGBbXA1GtyoMj4F4gBxIGVFAF5E5FBADncAEEEgFy8widgF5AAqCAQAEKACA5CxQ7ACgTxHMCwAaAABzAuEAAygLkKRQAAAAA4gNBgQaFIAADIAI+ACroYviUhAKQED6CFFiKgAAEKRkVHoTtK+BGRRFCHMgIhWOQEYBjoYqvIhSahRAAgEZeBHxAABkEABGScy8QAIAwQGQAKDuAIIC2AEAAE56hlIYqAAKzFgOZWILgAVFIhzCBQOQAEKA4FsQdpRQCAVBgcygQDmBUCFAvIK45AoaBkAF4jkEUAByBUAAgBCjmEBy0BSiWBUABCl4ATkXgQAUagAAOBSojDGoQAvcLBAALEKjJcRcIFBhlIwC4ApO4orIETmBSkCCL2DtIXkUGQqCAIvvINAKF3BBdZUAByAhQQqDKTkEBkACiMnIrABCwKAQ5gBAAhRQAAA4lfUAWo5BDUociFZAgULUcGAIykABABQAABqAAAFwgO4BhQWA5gAUEEIXgQAQrBFQDhyKBAwQgMAEAjKQKW0uARkUZCshFUgfAciAyFHMCPUngUhioANABGUhFRhlIAABAABFTqBSPgFR8QAyKncEGNQKRlJx5AB2BagggKQAR8QCKAAiswAisQFYAIAAC20IihAAAAAUXmTmXkTmBXwDJ3lKIB3ABYpCgUDgwUQIvAgGSBEUAV6GPIpUBYcgEGCWKUAAQAGO4oqKYlAMIACkLwIAKiFKKmCF5BJGACopLalHaUEGgUCFIAHaBz1BQAAACwAo5AcioAMAAwrDmVFQIUAxxDHMogYFgCKERXCMgQrKIAABQAAsUXKiCxSAAAwBUQqAqIUhUCFIBQABAUBUAsADD4AAQo7wAAAAPiAA1AYAAMhBSAAwEZSEUCBCCvsIykAAAgjIysxDJQRFMQAIFXkCcCkEsTgUjIBC8iWIpxHAACcyMoIpwIyh6ATgACAACKEKQKgAIIAUKEQYYAAEEZC8CCVAAQAARWYAKxLFIUAAAKQDkECkQAofADmUOQ7QOYF7SPiXhyBREUhQFh4DsDKLcESAFJYoQBDmUnACgdosEAAUAAEAu0AAwVkKBUQLrAq4DkLFABgvICAMFQL2EKAYBSgUgZUNeYCHACkAAFIuJSiMBjuApAXkBC9oIVFABUOYuLdQAoYD4ARgoSKguAL3DkUQAAEUiKBC8gxYqCC4BDvAWKidiCAoIAKRlRCgUlgBQAwAACAAABgoEIyviAICgAQpAqggAAMEAj4l7iAUgAUJyFhYgAAgjBSANShEIKRjkOQVGYl4k4EVbCxCsgiA8BrYSoOBLAgDQAgAAijRiykZAHIIBQAARgEIoUEIpwA5EAF8CcgQAAFRjvKQAHxDBBCGTMQoACAB4AiswHwGhWIXkQAUBAAUhQAFgEAOYfYVQeAAReICBQYACgAKgik5CwFASKABEUAXgRFCIB3EKBQOYQZfAgKKgB1AAgOAAcC8iFFHMACgLQAATmUIIpAUUcyAqKQACgIAUguGUGCgqIUFAhOwrBQFwgEVBjuAAcwCoqHEeA1KDJyKycigUcgEFxuUE56AVkKQoFHHiRkADiChyK0AEAGCgQtgAAAAhQAAAAAAGQosQAAAIUgUKQAUg5AAiFZCAACKnYLFAAAgEKQEFIUhAfUTuAIoR8C8dCcGFAAQTQWBSSqEK+wEEFikAEKRkUIUhFAAAICEAAEUAAUIVkIADAAAFUIXloQgWDAIIydhWOAUDHHgCCIFBMDIDkORUCkLcCsgAAcx2jmEUECKKFwHMAABoUUIBgAiXKBSAFAuoAAAagB2gcwKgOQCBCkuiwKgQpUCgAEAAL4EAAvgCMFFGoQCKgTkVABzAKADFgALy1ARCcCoFBDQctAUXkGQqCCLoQLrKKBcdwEYWoehSgwAEAikKLxRSFKgByCfWAY8ACod4AAcy8iFAg4galFDACIwGAqoveYovEqKQAAUhQgSxkuBCiFAsQF2ixSWKHIjRQyCAECqACAQDUKIAEABkAAAAAwRQEDABghABSEDwDDAUIAQGRlZGRQAgFQAIqPsIVkIAHaABCkZFQcgCKAgTAMhdAQACEUsCgCWCKQKgAZAADCjIxy1DICD7iC7AABgOscgRkUAYIM1wASDKgOwch3AUABBgNhAAAIFDICiggKMguBCsgEAKHMqIgkUUpByAoXEAAwgAKAAgyFBUAPAAUgCAFIUociF9wAcg1oQoC4CBReQAAF4EL4FFD4gBCwfAcgULEZSBAEBRkAilQJfXgOYsAfEXBSh3iw5gIFWgYKgrDwCLwKAJxKA5EL2ixUOYHaAoQo4oBYBcB4FQ5FJqGEUBMFEACAoCKXCJyAYSICKgC4FIAUALDkBQQoAhSMghOZkRkEAAUBSEABkIqgpGAIwOwCApCKBiw5gAAiAyXD4WIRVIxqOwiiBFwHAAyFehCCkZeQtoFEHYciciAQrJyJILiACKEKQCAMEUZAuIAchYAgnIAEUAKFCMACAMMgAAKjREZMnIgADQAQpiBWRlIRQEYIrZcMIdxWIQoAFIUAwBzCFgRjuAouOYKqoAAFwAYZQKQBApCgEUhUUB4ELzABDkABSAqKAAgAhzAAACgXBQY8ACgwgw+4gfSXwIUoWAKEB2jiCwqjsAKgwER6gNbgcgggO0FKBVYd4KhzDQKyiALgLBDiWwReRRAPAFwgOI5AociiwsBScrFAROQKyFQt1AFGBAWwsXAELYthgylgWwsXCJqF3FsWxcCFFgXBlAZWFhhMsUUqQsXBlBYtgkTBlAWzFi4Mse0pUhYYMoC2LukwZYMhm12EaJhWJDKwaYwZY8wWw7zFUZOZQyLCFuQEAAEBk8SkYUGoAAiAIoAGiCMhQRQjYD4AACEVQwgyBYjKRgCFDIIAHfqIqAchyAgYBFBzC4kfEAACAQrIRQABTgQoAgDBAAAVGBqCAGABB4AoEIVkZFPABgis0NbkRbFYg7wABSAC3JzHAAGWwAAAFFIgAKgAVAAcwCCAAveAOZQAAFHIhbhBAFKIhzKLhBAcgUBzIUgIqCKUACABwAKBUEUAgNCFRbAEYFA5BlAAAACsqAAZUUr7Sch2FAofAgRUBYFgBoFwKVE5gCxQBbFZcJlLDtLYbrYwIU5+S5Lm2d11QybLMZmNS9msNRlUS72tF4s7F2e6CNuMyUZ4+OByim3qsRV85US+ZTv/ACjZRbrr/DGXA1nSmj0UZ1F2KfbO/u4urEN12vbQ79w/QnsllNntDtViMRNP0oUdyhF+HpTPNYHJOijKElg9mY5hUXx69KVZt9d6r/Qdnp+hNbf300bnn7vXPQx/IorueuKcR31Y+D5qowdeoqdCLqzfCNNObfgrs85gNjNrse18D2XzqsnwksFOMfbJJH0nS2vo4OmqWUZDhsJTXBXjBeyCRrrbZZ9V4VMNSXVGm5fymztLfVTVTH3qoj697rLnXDWVfy9PEf7qs+ER83RuF6IukTEWa2ZrUk+dbE0Ye5zueXwvQPt9WgnOllGHfVUx92vyYs7RltBndR3eZ1o/MSj9CNcsyzKp90zHFy/fWcunqnMR96v67nDr609KTwiiPdVPxl6Fh/J82skvt+b5JSfVF1qn/oRzoeTtnO7eptRlMH1fBqv1o9s8/Xk7yxFaXfUbM4Sb4zk++TNn8LWY41y409Zelv8AMj/jD1JeTvmXB7XZSv8AZqn6xnHydcxktNsMo/Nqn6x7bdf9MyjYk9WLH6p+vekdZ+lI43I/4w9Qfk5ZtfTa/Jn30Ki/SZ0/JuzmX/7tyX+AqfWe3xSvwNsUr8DCerNmPzfXeyjrV0j21R3R5PUP6WrOm9NrMl/gan1j+ln2gfq7VZG/3up9Z7pFL/pm+CXUa56uWo/P4fu2U9a9dzjujyeif0sm0vxdqMhffCqZR8mDauSutpdn/ZV+o99jGN+BuhGKXD3muer9EcK/D92+nrXrO35eTr3+le2u5bR7Pv8AhfqL/St7Zv1c/wBnn+NVX6DsVRj1GyMY9RrnoGmPz+H7tsdatVy+Hk63/pV9uXwz3Z1/vlRf+kq8lTbx/wB+9m/4Wp+qdmRiur3nIpqNlp7zXPQkR+fw/wD9NtPWnUdtPw8nVn9Klt++Gc7Nv9+qL/0lXkn9IT4Zvs1+cVP1TtmCV19ZyKSj1GqrofH5o7p/9mynrRen8vjHk6gXkm9IVv7L7NL/AGip+qP6UzpDfDN9mPzqp+qd0QS00N0Iwv6qNU9GTH5o7p/9m6nrLc/T4x/6uk15JPSM1pm2zH51U/UL/Sk9I/LNdmH/ALVU/UO84qPUbqe7fgap6Pqj80d0/wDs3U9Y6p/J4x/6uhv6UfpIf99dmF/tVT9Qj8kfpHX99tl/zup+ofQVPc6kcimo/JRrnR1R2x3T/wCzbT1gqn8njH/q+dV5I/SRzzXZj86qfqF/pRukd8M22X/Oqn6h9IRUXyRyKCinrFGqrTVR2x3T5t1PTdUz+Hxj/wBXzXDyQeke2ucbML/aav6hrq+SH0ir+/eyy78TV/UPqCSi46I6/wCnDbyGwOx8sTh5R+zWP3qOWwfGErelWa6oJ+LaMaNJXcmKYmO6fNyKOlqqqoppp3z648nyB0o7DYjYHP1kuNz3Js1xkN5YiOXVJzWHkrehNyilva8Fe1tbM9SZ5HOalSrGnVqzlUqTqznOcndyk7XbfNnjbmGss0Wbs0UTu3fB3VqqaqczxGECHEy2qGQMAAgQELAAGAQgABBUAYIoyMpAJYoY5EBBhAijICMAOYCIAAaIqMhWQECABFCMveQBYhSEUABFAAABQETmQMdxFAAFGQpOZAA5AAyFQAmoYBFQF0BFZcikQKxBoUAQFsAiAAKFIUILtAAUABQKQvMoIABAMDmBQAUAgEAKAEUgRSiFQIgi8ggNCgEPAAVAAB2DgAAHMhUULlILgUEKEOWg8CgoDiGChyHBgFQ7ihFLCBLlIUCksUQDHMcgVAcBYvAqCHaC2MkNblsLHMyrLcfmuPp4DLcFiMZi6vqUaFNzm+2y4LtdkXDGqqKYzM7nDijlYLBYrHYuGEwWGr4rE1PUo0KbqVJd0Urncex/QhKlh1me3OaU8tw0dXhaFWO/3TqvSPdC77T3jC7SbNbKYOWX7EZJQprhKu4OCm+tyfp1H2to7fQ9CavWz9ynEPKazrXp6apt6OmbtXON1Mf93b7s+11rsl0EbV5oo185qYfIsM9ZKr9tr2+ZF7sfGXge7YPYboo2Va+GOe0ONhyrS89G/wAyNqa8bnDznP8AOM5dsxx9SpTvdUYehTX4qPHw3Y8Ekj2Oi6pWLeJvzmfrn5POarX9Jaz+demmOVH3f/1+KXus9usRQoLC5JleFwGHirQUktF2RilFe88JmGfZ1mDfwvM8TOL+JCW5D2R0PEqRkpHobWh09n8FEOuo0dm3Oaad/PjPfLbHd42V3zNyZxlLUzjJnImGyqlyEzYmcZSNkWa5hqmlyIyNkXzOPBmxMwmGmqlyFI2QZx4szizXMNU0uTGRnFmiLM4s1zDVNLkQZti+BxovQ3QZpqhrmHJizdCWhxYs3QkaaoIhyoM2wZxYS6zbGVjTVDZS5MWnxNkWcZM2wl7TTVDbDkwlc3U3yOLF2N0HzNVUM4cuEjkU5cDhQkb4S1OPVDbDnQlwN8JaHApydzkU5aanHqpbqZc2E9TdCZwoy0N0JGiqltiXOpz1OTTmeOhJnJpSfWaK6W6ip5GlJLicmEud9Dx0JWZyKc22opNt6JLmcWulyqK3LxePwOW5fiszzPFQwuAwlKVbEVpcIQXF9/JLmz4k6Wts8Tt9tpic8qxlSwqXmcDh5P7hQi/RXzn6z7X2HvvlPdI/2Vxz2IyXFKeXYOopZjVpv0cRXjwpp84w98u46UpPrN2moponfxnw+u16bo/TTRbi5Vxng4edrdp4dLrn/wCk8arnks+tfDxut7dlK3e19R43U6XpP/5VXu+EO/0/8qPrtXkGY8ynCy3KS+oAAvYQEFIAwqkD4BgCPiUgAAEU5gBkEAuAppcEBAIykIIUDgiKDtIABChkVAAA5EDBBAUhFAAFAABbkuGLgQDkEQQoAVBcpOBAZCkAFBLACFIyKPiCW1BFbAAViXFxYAXmAAIx4lsTmAAARSkAVUhYIFCw0Q53HAAgAVFQIkUB2ghSgOYD7wKByCAC4AReOoAKCHIAIBIAC2A7wUACAVBAFAMDwAPrLqQqWpQLyIUIAMctSiixCsqKADJAhQUCjnwIwkjLyIVe4yQLbrCRbFhJkS0Moxcmkk227JJXbfUutnmtjNlM92uzhZXkWCeJrJb1Wbe7ToQ+XUnwive+SZ9DbO7IbFdEtCljs0qrOdpXHep+gt6P7lB6U4/hy9J8jl6XR3tXXFFqMy6fpPpizoPuzG1XPCmOPv5R658XXXR70KZtmtCOa7U1amR5Wo7/AJuVliJx62paUo9stew7Ep5/szsfl88o2FyvDK+lTFWbU31yk/Sqvvsuw9e2s2rzbaWu3jaqpYRSvTwlJvzce185y7WeFUrH0Povq1Z00RXf+9V9fW7xeK1dzVdIztaur7v6I/DHt7ap9u55DM8yx2aYj4RmOKqYmpy336MexR4I49+00b43j1FMRTGKYxCRbimMRGIb94yUjj7xkpalyk0uTGRsTONGRsjIYa5pb0zNM0KRnFmMw1TS3xZtizjxZsizCYa6ociLNkZHHjI2Rka5hpqpchMzjI46kbIyMJhpmlyIs2RZx4s2RZqmGqqHJizbFnHgzZFmqqGiqHIizdF6nGizbF2NVUMXJhI2wkcWMjbCTNNVLKHKi1xNsGcaD6zdBmiqG2mXIgzdB6HGgzbGVuZpqhshyos2xlrocSMtDZGfYaZpZxLm05cDkQkcGnLq4HIhJWNNVLbTLlxkboT7ThKTZthM0VUtkVOdCRyac7WOBCXA5FORoqpbKankqTvqdY+UL0jvY7JvsFk9dLaDMKWsovXB0Xo59k5cI9Su+o8r0n9ImX7A5F8JqbmIzXERfwDCN+s/vk+qC970R8iZrmmPzvNcTm2a4mpisbiqjqVas3rKT+jsXJHEqxTL0fQ/R835i9cj7seP7c+5x6abd22+u5yqW7FOc3uwinKT6kjTBaXOJm2IcYLCQertKr+iP6fYaarsWLc3Kv6y9Xszcq2YcTEV5YjETrSVt56LqXJew1tmKMrnnaq5rmaquMuwiIiMQqBO0oFHIhWwgAGAAGoABiwEBX2kIouAHIEAjKRgNBqGRsKpACBfrDIwQGRghFVggIqgACcAi+BABLAEBkKQigACgAYDmQpGQAAwAACnaQpOYCwGpCAAXiBOBCkIqAAitiABWIUhQCYAAAMBAIDiwAAAoICqAoKAQCCKNQGUBzAuAQAAF56ERe4AALhApAUUDUnMIMo8CAZAnYUoECDApCrqIBbAFKIUBcQAYBUAwCiopEwVFKTiOZkiovAcgWEB3kuLGUQi8y2JYySbMkWKOx+h7onzrb/Exxbc8BkcJ7tXGuF3Ua4wop+tLrfqx5nsHQR0Ovaij+yjatvBbNUYupBVJ+beLUeMnL4lFc5c+CPdekbpFjjsH+xrZBfAMgpQ8y6tKHmpYmC+LCK+50uzjLizt+iuibvSFzZp4ds/XhHb7N7oOkek6qc2tPO/tnsj2c58I7XMz3ajINh8r/Yn0eUMPF0nbEYyPpwjPm979uq/hP0VyOta1erXr1MRiK1StXqy3qlWpLelN9bZxlJK0YpJJaJF3j6dodBZ0NvYtR7+2frk8zRZijM8ZnjM8Z9stykZbxo3jJS7TnZZTS3qXWXe6jSn2lTLljNLcpGaZo3jOMmWGE0uRF3NkXoceMjZF6laqqW9M2RZoizYmMNVUN0WbIs48ZGyMtDGYaqqXIi1c2RZx4s2xZhMNNVLfFm2Jx4s2wfaa6oaaob4myLNMeRtgzVVDRVDdBm2LNKbRsiaphpqhuizZFmiLfA2RZqmGqYb4s2weupoi+0zgapghyoPkboM4sJG2EjTVDOJcqMtTZFnHjI2QfsNNUNsS5KZsgzjxZsizVMMolyoSN0ZHEhI2xkzVVSziXMjJG2EjiRZupb0pKEE5Sb0SWrNNVLPLm05c7nrfSVt5lWwuUefxTjiczrQbwmBvrPqnP5MF7XyPV+lDpVy3ZGnUy/Kp0cwzq1rJ71LDvrk/jS7OB815xm2Y51mlbM81xdXFYqvLenUqSu2zq9XqqLP3eM8vN6XoboS5qsXr0Yo8Z/b19zlbTZ5mm0ud184zjFTxOLryvKT0SXKKXKK4JHDp6GqGrN8ErNyajFatvgl1nV0zNdW1L2+zTRTFNMYiG2daOHoOrJJtaQi/jS5fWeGnKUpOUm5Sbu2+bM8XXdervK6hHSCfJdfezWddq9V6arFPCPrLkWrWxGZ4ljJEsU4kNsiKQpUUgHBFQAQYAJi5GFW45C1gRFICBVDBLkAAj4EUAQAhWS4IoAyEC2osOQfACFIERVHAdwYEAIAABBAUhFAwgFCFDIBGUgAC2pWBATmAogCoggDAABMMCMB8AQY27AUEVmgAVAC4AoBAKAAAHiAgALBQpAUVAi6y8SgUmpb2CINQAKCF4WAW5CxQUCkQCAAAaFCAAAjKKOYAQS00DAKAAAchzKPEoLiOfaVE5gXkOQIBQAUAggVFKQFReZSAyhFHeAZQklrmSIkZxj2GcQxmVjG53h5PXQ3DaWi9s9sFHCbLYSLqwjXluRxe7xlJ8qMeb+M9EcHybOiKp0g5zUzjO08Psrlst7GVpy3FiJRV/MxlyilrOXJacWe/dNXSPR2kqR2c2aaw+y+C3YQjTjuLGShpGW7ypR+JHxZ23RfRlfSF6LdPCOM8o857I+TqukdTNuiaaZx9dnn2e3h4/pX2/e01X7EZMp4bZzDtKnT3dx4tx0jOcV6sF8WHBLjqdfynd6slSbbbua76n1DS6e3pbcW7cYiHmopjhEbmzeKpGreKmcjJstykZJmlMyi+0sSxmltTM1I03uZJszhhMN0ZGaNMWZxbuZRLXMN0TbFmiL1M4syaphvTM0zRFmyLK1TDkRZnE0RZsizGWmqG+DNsGaIPU2xZjMNNUN8TbB2NEZG2DMJhoqhvgzdA48GzbFmmqGiqG+JsgzTCV9DbFmqWiqG2JsiaYyM09DXMNUw3xZsiaIszjLtNdUMW+LNsGceMjbBs1VQyiXJi+Rug+s4sZGyE78GaZpZxLlRZsizjxkuuxyaFOpNehCUl1209vA01Yhllsj2G1NLVs9f2g2r2cyCnJ5nm9CNRL7jRfnKj7NNEdabTdN1ZKdHZnLlh+SxNf0p96XBHFv3rdmM1zj673ZaPovV6uf7qicc53R3+TuTOs1y3IsC8dnWNpYHDpXjv+vP5seLOj+kTppx2Z0q2V7L06mX4GacJ4hv7dVXa/irsR1jn2dZpneMli81x1fFVZcXObZ47mea1nTFVU7Nnd6+33cvrg9v0Z1as6fFd+dqrl2R5/W5m5SnNznJyk+Lb1ZkjWjbTV2lbVnU0ZqnMvS1bm6km5JJXbNGMxCmvM03eCfpS+U/qRMRXW66VJ6PSclz7F2HG5GF/UYjYo95Rb37UhkiF1OFDaqvxKRAyRSkCKigC+gAAlwiuw5aBDQKAEAAO4IBLgjIqhkKQQpOY5hQcwCAQcgA4DnqARSwYZAKQAAQpOZAADCoACKAIAGAEQByAYEAAAERSKEA8QBOZe4gDmBcMgEQQCgDWoCswFwAYhbE5lIog+4cylRjzMiAAEAEACgB4AFVF3l7SFALvABUXtsCMAXkPeEChYoHMCFIUCkAfECjwIUCc+wc+AKVADuJoBRwAtoAKQIqKgOQ5AAAUOweIBRQAAYA5FFXAeAHYiwxCq5CmUIpTHmZczOGMsoLme9dC/R5mvSXtxhdnsuUqVD7rjsWo3WFoJ+lN9r4RXNvvPT8twmIxuLoYPCUKmIxFepGlRpQV5VJydoxS622kfZfmMJ5PHQ1RyTBVaMtt8/j5zFVoaunK1pSX4FNPdj1yu+s5un09y9cptWozVVujzn1Rxlx712m3TNVXY8P077U5Rs7klHol2DjDDZNlsFRzGpSld1JLV0d7m7+lN85acmdHubuZVazm25Nybbbbd229W31s0SlqfUej9Fb0NiLNHvnnPbMvKXblV6uapZuWvEjZquVy1OZtMNlsuVPQ1bxVIsSTS3LtMlJGmMjNMziWEw3XKmak7mUXyM4lhMNydjOLNS6zOLMolrmG6LNiZpXA20lKTtCLk+wziWmqG2Jsi7LiaMRWwWCh5zMMbRoRXLeVzweO28yXCNxwGEqYya+NLSPvOLqukdNpY/vq4j493FbWlv6j+VRM/DvezQvN+hFy7kculhMTO1qLS627HWmM6Rc9rXjhaeGwkeVo7zPD4vaPPsYmsRm+LknyjPcXuOhu9bNLH8umZ92Pj5OfR1e1df4pinvn4ebupYR0/utehTX4UzXUxGVUPu2dYGDXLziOiKk6tZ3q1alR/h1G/pYhShx3I+w4NXWu5XOKLfj+zkR1X/Xd7qf3l3j9nNm6fr59hX81pk/ZPspDjnUX3RR0vCMeUYrwRuhHu8DOnp/UVflhjPVmx23KvDydxfst2TT/svJ90DOO1+yf+M6z7qaOoqcHpxOTTi7cWcq30lfucYjxaaurulj81XfHk7WW2Oykf741/4JGX7Ntk1/d+IfdSR1XZ9bI4SvxftNs6y7yhq/h7ST+arvjydq/s52T/w3E/wQ/Z3smv7sxf8ABI6ocX1v2muaa6zVVrrsdkeLKOrejntq748nbv7Ptkl/deM/gUX/ALQdkV/dWN/gUdOST62Sz62caekrvKPFnHVjRc6u+PJ3LHpF2PX90Y/+CRm+kvY6K+65lLupROlJXJdrmzTV0nd5R4+bOOrGi51d8eTuOv0q7Lw+5YXM6v4sUcGv0u4NK2D2exNR8nVr2XuOqkr9ZshB8jCNdfr5ePm2x1d6Pp40zPvn5Ye+YzpX2nq6YHA5dgFyk478veet5xtTtTmsWswz7Fzg+MKb3I+xHjYw04CpB7plM3ZjM1T8Pg5lnQaSxP8Ad24j3ZnvnMvF1095tuUm+cndnGmc+vTscWpA89qrc7Uy7m3VmHFaFuZsnHXTiYzcaX3R+l8hcf8AkdXVTjfLkxKRV9eCXFvka6ta6cKd1F8ZcHL/AJGFSpKpo7KPKK4f8zA4ty/mMUs4p7ZUqIjJGmGQrmSRCmUMQFBUQosCi8QRcNCgCFIwKTmABQTQIgPiQpCKPUneWxAAdwCAAxqRQhSd4FJ3hgB3E5FZCKApAKQd5SCMhSAAARYACAAygKhQQAAQgAECqCa2KBOQKyEAMMgAcQCBwJogCKAMBWYvYnaEVipUiFApGW4AgAYAABDmXgQoVFwA4AooAABEKVAABVAC4WCL2gAoFImUCF5AnACgEbCA5jkCgLFAEKFxAB8AmAusqKiAAGW5HxBRQEAHWUj1TLyKBScQUXtHIBcblYgARlCSyMorUxXaewdH+zGYbZ7Y5Xsxla/qnMK6pb7V1ShxnUfZGKcn3GyljLv7yPNisty3AZl0w7WJUsqyaFRZc5rSVSKtUqpPi1fcj+FJ80el9Ie1uY7Z7V43aHMm4zxMrUaN7qhRXqU13Lj1tt8zsvykNocvyTL8p6JNmH5jKsko03jYxfr1Er06cutpPfl+FNdR0dOd3qfQOregiza+01x96qN3qp/fj3PO6+/NyrYjhDNy1JvaGpyJvHo5qcHZbrkuat4u92k2jZbEy3uzUmZbxlFSbLdFmSZpjLtM1I2RUwmG1M2JmmL1NkTZEtcw3RZuo051JbsIuT59n1HGrVsNhKHwnHVlSp2ulf0p93Uu09Rz7bHEYmEsJlyWHw/WlrL6+9+w4Ov6W0/R9Obs7+yI4tmn0V7VVYtxu59j2vNM6ynKYv4TX8/W5U6b0/5nqWcbbZnjE6WDUcHR5KKvL6j1icpTm5zk5SfGTd2yI8Jr+smr1UzTbnYp9XHv8sPRabobT2cVVxtT6+HczrVatep5ytVnVn8qcrsiCVzKKOiiJmcy7XdEYhlFGSVyxXYbacHJqyb7tTlUWplqmrCQiboQN1LD1fWcHFdb0RlfDwf2zF4aP75f6DsLdmKfxbvbuceq5nglOByaVPrNUMVlkPWx9N/Npyf6DdDMcojxxk/ChI59m5paPxXKf+Uebj1+knhTPdLk0qZyIU9DiRzbJlb+q638AzbHOslXHF1/4BnZW+kNDH/Vp74cSui9+ie6XJVPsK6fOxo+zeRr+66/5uw87yTli6/5vI3/ANpaD/Np74avRXv0T3SzqUzTKnqJZ1kr/uut/ASNbzfJnwxVb+Akce50hoZ4Xae+Gym3e/RPdKzpmqUbcBLNcp5Ymt/AM1yzPKn/AHTV/gWcWvW6OeF2nvhupou/pnuka1MN13JPMcs5Ymr/AALMVmGW/wCEVP4FnEq1eln/AKlPfDdFFz9M90t0Y6nIpwOHHMsrXGvX8KLMlneVw+LjJ90Ir6WbLet0dHG5Hewqt3Z4Uz3PJU6d0KtO0eR497T4Om/tWVzqrrq1re6KONX2rx8rrDYfBYW/Bwpb0l4yv9Bnc6c0FFOIqmqfVE/PDXTpNTVP4ce2Y/dzquFrVNadKcl12svaeOxU8JQuquJjOfyKPpPxfBHjMbj8bjZN4vFVa3O0peivDgcfl2HnNT0xFzPo6Me3y/eXZWtJVTH357vP9nKrYyUtKMFRi+NneT739RxuYKdPXcquTmqXMimKd0IUF1McAiohUZQkqVEL4GUIo7wUqJyGoBQRSBAEUEIADHICApAoB2oEADS4IpxY7B4kYQfaQXBFOAHMAGNB4kCqAEQEQpAAA5ACF5AghCtAiiIChUAAFTIAAIUgE7AUMioUncUggAAgBQIGAQQMpCKAMEVmtdQQpkxLFC4ACgDgBAXQgADmAh3AAocy2JzKA5Bl5AqoAGA8ACgCFQCCABRUCACgIANRyAQAE7ggKBqO4qKOfAhSgxzI/cUIAaWADgOfEcWLWKBQAGlykQ1sUVAC5RfpBGORUUcyFMoRlHV2PqHyVcqwuwvR1tF0w5zh4up8Hnh8shNevCMlF2/dK27DuhPrPnDZPJMbtJtLluQZcr4vMcTDDUr8IuTtvPsSu32Jn0t5SedYHK8ryDo1yKXm8uy3D069WMX8WMdzDxfa479R9tRM7PorR/bNRFE8O32dvl73X66/6OIpjjLpzMsdi8xx+JzDMK0q2MxVadevUk9Zzk25P2s43nHwuYTlqYN9R9M28bodHs5bnIXNSZd6/MRUbLYmN417wuXJstylqVM1J6mafaZxLCYbosyTNUXzZti9DOJa5hsgzXm+Z4XJcP5zEWqYl+pR47vbLt7Paas5zSjkmF35NSxkl9rh8jtfb9B17jMVWxmIlXrzcpyd9XwOj6a6cjRU+itb7k+DmaHo+dTO3Xup+P7N2b5njM0xUq+KqSld3Ub6I4aBklofPLly5ermu5OZntl6eiim3TFNMYiFWvAySEItvRd/YSdanDSK85L2RX1mX3aYzVODfPBtp05Tdoxbt1Fc8PT9ervP5NNb3v4HDq1qlXSUnu/JWi9hgap1kRuojvWLUzxlzJY2K+5UIrtm95+w1zxuKkvu8orqh6P0HHKuBpq1V6rjV8vgy9HRHYScpO8m5PtdwtOGgXEtjTjflkXZDKxUi4TLGwsZqJdwy2Uy12Ibd0xaJiVyxBlYEwMSGViW1IqCxkLEGIsZWFgZTkEi2LYplLFSBeYTKFCRSsRAAyAyJbQqKgrGWpOZSwgACoaBAcwKQBgO8dwZOQVRyAvzCKQFIqcici8CACcw9AyKAhWQTxAAUF+RC3IAYFwIFqUgFIABE9SkLyIBCkCr2EJcvMgABgQAoVO4cAABGUjIQhQAqagpOJBSMDnxAIgfEAAAQCcCkuRQAEVlwCARUUqIUqKOBOQ8QBe0nAMB2hgoAcwS4RRzAKoL6DvAADmUogACBUAUAC2AheZOQAo5i4AdQHIANQBpxABDkCoBFIVApEUAGOQ5AC8iF5lAnaBzAo4AcigVEDKKUgRYRQyIt+tlR3v5Huz1GttPmm1+PtTweU4V0adWXCFSrGTqTT/AoQqv8ZHqe12fV9pNqMzz7ELdljsTKrGH3uHCEF2RiorwOx8sn+wnyXKeGh9rzDaFpztx/ql7z9mHoxX74zp5y1PddAaf0NnbmN8/1+vY8xXd+0Xq7kcM4j2R+7bKXWY3MHK6FzvtpcNiZd41phPW5YqTDYmZJmu5kn2mUVMZhsibEzUmZJmyJYS2pmdfF0cuwUsfXabV/MxfN/KfYvexQiqk/Tlu04renLqj/wBaHp+1OaPMcdKENKFN2jFcNOHgjg9J9IRoNP6T807oj1/s26bTTqbmx2dv163jcxxlbH4ueIrzcpSd9eRoiWwSPmlddd2ua65zMvU0xFMRTTwWxk3GEbzv2JcWSU1TXC8uS6u848m5Nyk22+LZhXc9HujisRlnVqyqK2kY8orh/wAzWUd5xJmapzLOIiOCAtioRBkRUEXmZYTIkZJBLUySMohjMokWxkkboU7o3UWpq4MJqwwowu9Xa55OrkOb08AsdPKcwhhJcK8sLNU3+Nax9ZeSz0TZVkuy2A21z7L6GOzvNKaxGBjiKaqQwWHfqSUXo6k/Wu77qtbW59DfDMVOm4yxNZxas4uV4tdW7wt2WN8UbO7GXXX+kbVuvZ4y/LOcLcGmuwwcbH175U/Q7k9fZzGbfbM4Cjl+OwFqmaYXDwUKOIotqLrRgtIzi2t5LRrXRrX5Kr0t12uWbOadqHKtX6blMVU8JcVohsaMbdpxaqcOREsWiWMrA1zC5Y2BWSxMLkKLAmBCixbFRNQigAEhyMrFRLWBQUEigpUQAFFBLlCAF9QAADAEKyEVRyIUAgOQAjADIAYHMKcSFBBCFfHgAIQMEUAADkAAGo7B1EuBQ+AXAhA5EKCKEAAo5AgUAZCAAAHIEAVSC4AMhWQgAdxQICgCFIgQGAwBLoAEVkikRSoIqJoUAAUCFIXgAAQQQIUAAAiqchyGrLcCDiUhRWQa8RYAUiZQggECgAAHIFDAAB8QAuOIAAFKgQLiChyKQpEAEXW1yhxFx2kKHYVEKBOfaUjKyiMtyAot+0EFwMkzm5BllXOs+y/J6DtUx2Kp4aL6nOSjf3nBPf8AoBwbxHSThsWkmsuw1fF6/KUHCH8ecTdp7XprlNvnMQ4mtv8A2fT13f0xM+DsHygczhUx+TZJhnbDYWhOvGC4JSap0/ZTpL2nV97HnukfHrH7cZpUhK9OjUjhaevxaUVD6Yt+J69c+lURFFMUw85obU29PRTPHHjO+WdxdGFxfqMoqcvDYnYX1NdzJMyipjMNiZnFmuJnFm2mWEw3RMrewwi0b6Ed+ok/VWr7kbaYzOGqrc4W0+M+AZOqEHaviHr1pf8AJe9npB5HaLHPH5pVnvNwg9yH6X4s8dY+f9N637XqZ2fw07o8/fL0OgsehsxnjO+VQnJU434yfBfpLdRi5PgveaG3KTk3q+J01yvYjEcXNiMo7t3b1YCsVHEZiQsVAuEyW6h4GSQsZRCCKgkZIyiGMyJcjKKIjKKNlNLGZZwR5rZfKq2d55l+TYZfb8wxVLC0/nVJqK+k8VQjvM7o8knIFmvTPleMqUVUw+TUauZ1b8E6cbU3/CSg/A7PTUbMTXPZDjXa4ji+13gsLgKVLL8HBQw2CpwwtGK5QpxUI+6JgtHYwjVcldvXmXeNEUzEYl4+7ei5XNfMzTLaed5FmWR1bbmZ4Kvgnfh9spyive0fmjmGGnQqSo1ItVKcnCa6mnZn6WfCZUZRrRfpU2prvTv+g+Dun7IlkHS/tPlkKe5RWPnXoL/JVbVIe6SOZpKNqmun393H4w7fovURVTNHJ1pUjY1SRyq0bM47R196jEu/oncwsSxnYljjTDPLAWKUwwuWIMiMKg5gqIA7igIW6wNQ+0qAAKCuXuIVXAAoLADkAAsAhpcIcghcXAEKycyKXKQoAAj4EAd4BAIXkRhVuRsEAq7wCBRkKORBAAgA5gdoAERSAQpO0AACKhSFCoORUQCBcQCAAGFOYCAAheVyEANAACAMC8iMAgAXAAAAQFBFZIcwGVBhBlXWACIwBQLgAAAKGAUAAEB3DiO4KEDAAqIUoF7SDvAoA1KihkuUAAEAAY7QAXEeIKDBGwgitBAeAQGgHIoo4EReYFIhyI+JVUcwAgAGURi4Y7ygB2C4GR255N+GgsdnWYy4whQoJ8rSlKpL+aR1Dc7s6E5PDdG2fYzdUbTxVRS5vzeEdvfM7XoWmKtZTM9mZ8JdL1gzOhqoj80xHfMOv6leWJq1MTN3lWnKq++Tb/SS76zCnHdpwj1RSKe1iZiN7VMR2NiegfeYJlMolMMr6mUe8wXEzRnEsZbIszia4s2RN1MtctkC46t8EyfE4nhLd3Y9/Be9+4kO84O2FXzeVYTDrR1Jb8u5f82TU3fQaa5c5Ru9s7o+LG1R6S7TTzn93qaT73zMkuSFhUluU21xeiPm+6nMy9Nxaa8t6W6npH3swQLY66qqapzLbwgRlYIowmUCSKDKIFXWVahFMohjIlqVBIvO5siGK21M4oiNkFc3W6csJlyMIryR9aeRfkaw+y+0u0s4+ni8TRy2jL8GEfO1LeMoLwPlHCQ17FqfeHQXlP2A6G9l8BKKjWxGFlmVfrc8RJyjfugoI7XGxp8c5jz+Xi6fpO9FuzVPu73u6dhvpI0uZrlUsjRsZeN9NiG6bUtGfMHloZO6G2GRbQRp+jmeV+YqT+VVoTcH/EcD6WjU11Op/K1y1Zj0T4TM43dTKM3jfsp14OL/AI0F7TlaWNm9TnhO7v8A3w53Q+o/xE084+H1L43xEdTizRz8XG1Ro4c1qcXVUYql7a3VuaWYs2NGDOuqhvhNPAheZDVhkEKyEUFhzBBQgAgXkPAhQBdCIAXixYcAKBfrCKAXeAADJcEAcSkAcxzJqAKAUABwBBGEGyEVboGLZUAIisEEA5kCqQMgFILgCsjDBALqQqAELoQAwARUAJzCqLkBAAAUAFwIUgAFZGCAAABCgCAo0IIgGAIUhSKAAqMgRl0AF5EYAAAC3HADUABzAFIVAoXBBzAvMcBoOQDkOQAEKAiggABQRalKgAAKLggFATHaBGOQARQQFFQ5jxAF8RzCCKFhyDYCAAKp2i48QEByAKDRGUnMKDkAghbU7y6OqLpdBea1fl4TMZ+3ch+g6NZ9AbBU97yfsXbnlmYv2VL/AKDuugv/AJNX+2fk6Pp7PorUf66fm6olLWyMbmDepY6s9ZtZMM1wMkzFC5shjLYmZLiYR4GyKN0b2Es4m2JritTYbqIaqlekdDxW28v+8cPS5U6C073c8ta6PFbcRttFVj1UoL3HD6ZifsNXrmPnPybdFP8AiI9k/Lzev2NOId6ij8le85SST17zhXcm2+bueA1X3aIjm7+3vnIVdoBwcNmQyREWxYhCxeRQZxCAQKZRCKioiMomymGMsoo30ldmqK1OTRj6SOZYozLVXOIey7E5FW2h2lyvI6CfnMxxlLCxty35qN/Yz77xUqNPGVaOGUY4ei1RoxjwUIJQil4RR8o+SNlfwzpUpZnKKdLJMFXzCV+G+o+bpr8uovYfUN91Jces7XUU/epo5R8f6R3vGdYdRNMU0evPy83LU11klLQ4rqWRoo5xhq+dZnkMIQWKyvD4TEV5KWrVdTdmvwVGH5Rpi3PGI+sxHxmHnLe1diqY7Iy5c57rPW+kfBPPejfavJVrOvldStSXXUoNVo+NoyXieYr1bXNOX4iFPNsNOsk6LqqNVPg4S9GSfg2boomI2o4xv7t7i6fXeg1VFWe3wnc+BcVG6UutXPH1VZnte2+U1cj2ozXJKy3amAxlbDtL8GbSPV661HSFuNqZh9XsVZhx5GDNkjB8Doa43uZDFkMmTgaZhkxYKQmFyAIEwAKuwEwA58QBgCFBcAAALyIBqBSci9wAnAdwHMgpHcFAg5l5EAeIIVEC4uCMKMhSakFYImOYVb3RByFyByIUjYAMcQAA5ACMcxxFtSBzDDAAABYQBgioORRxAg5FIQAAAZLFDCp3hDkEQOWgF9R4gCFDAmoXEASHaACAOQBAVwCBV4ggIMwOREZCi2ugAQ0LyGgABDvFwCFwAAYBQAAAuthyKBBqUiKA7Q+AYAeIADjzKAEBzBO4otwAAF9RzAAvMIAO4hV3gCFFxyKgVEQfEC3BCooDmO0cSgyMpAKTxBQJcaFIBAUAHY+h+idfDOhOeEprenUw2aYey5ycHJL3o+eD6E8murGpsXKjUl6FHPHGfZCrSgn9Ejt+hKsavHOJh0fT8Y01Nf6aqZ+XzdP8YRkuDSZUbsbhKmBxuIwFX7pha06Eu+EnF/Qaj10b4ySqMrIwM4m2mWMsomyJhFI2R4m+iGupsibIK5jBG6C0OVRTlx6pVJJXtwPF7fxttJOXKdGnJfknmFG6scDbym51ssxnKthd1vtizj9L286KqOUx5fNdJXjU0+uJj4T8nqtbSjOXVE4S0PIYtWws/Be84B8519OzXEep6W1OYUAqOFENgrltqFxMrMyiEmUSKWwSM4pY5RGSRbFSNkUpMpbmZRRlFGSRtpoYTLKC10OXQi3Y40FqeRwaSV31Ha6O1tVRDjXasQ+oPJMyv4BsBnWezSVTNcfTwdNta+aoR3527HOcV4Hb8Z7x67sJlD2d6OtlsjmlGrQy2GIxCS4Vq7daV+1KUF4Hm6crM5NURVVNUds+Ebo8IfL+ndbNzXV09lO7z8cufgsM8XjKOGvbzk1FvqV9X7LnRfRFtcs98ona6p51ujnlLFU8Mr8qLU6S/JpW8Tt/ajOY7P7DbSbQbyjPBZZV8y7/ALdUXm6fvkfHXRHn32B6UMgzZz3YYfMaPnX105S3J/xZM5Okpzbu+uNmPbx/9XddA6WLmkuVT+b4fWX2DUr7zumcWvLei1fVo3ZjRlhcdicM+NKrKHsdjgVp25mVqmJiJh80vXKoqmmrjG58++VPlscN0mzzSnBRpZ1gaGPVuDqOO5U/jwl7TpmutT6V8p3ArG7E7O50knPA42vl9WXNQqJVafv3z5txK9JnH1VP9zHq3d274PtnQuq+06S3d5xDhyMGbZI1yPP3I3u9iWDIzJmLOPLNAUGKhCgYEKgUgjCRRbTiBH3DTqHiAIwyh6EMpYAr0AEuVkAAAKtwQPQgMhWQgBC4IKiMuouFRkK3clwpzA1DIA6h9A5gGQC4ABhEBAABzAIBSIAAA9CEUAKgqeIHAANQABAGAoRlIQOQKRkDtY7gADDC7QA5agEApAUggYYZFQcgCBcABWVyk4jmVFfWALhAMgKKUgAoIUABcACgFBAhQBUTtFygCkAC4tceIAqZBzArAIEUeI5k5lFAAAAIAVEAF1BORUVAcwLgCglygA2GADIUAL6jkLlBkACguCcwjJHdPkxYhVaW1GVOpJT8xQx9ON/vc3Tk/ZVj7DpXkdieTrmkMv6V8pw1at5rD5pv5bWly+3R3YN9iqbj8Dl6C96HU0XOUuB0pY9PpLlHq+G95jptyx5Z0k5pUUN2lmO5mNHqarRUpeyoqi8D0rid3+UJk9TFbM5bnipt4jKK0sBjOtUaknKlJ9iqKrH8eJ0ek7nuLe6Nnk6jR3vTWKa/V8FS1M0rBIySORTDfMs4rU2wRrib4HKtxlpqnDOmtTfBGEI6nIpxdznW6HFrqZQjfgY7T4V19kYV4q88BiLv5ktPqOTRieUyulSrOvgcR9xxdJ0pX5N8H/11HKu6X09iqjnDiVX/AEVVNz9M58/DLqrHf1pO3Jp+88ejzOZ4OthamLwNeLVag5U5LtXP2Hh48LnyfpSiabsZjs+b2enqiqjMCKuOgsW2tzrYhuyJGSQS1MkbIhjMqkZRiIrU3U43Xcb7duamuqcMIwvwRkqbO3NgOhHaDPcqoZxnOYYLZvL8TDzmG+FQlVxFeD4TjRi01F8nJq/JHmNofJ6z2hl9XG7M53l20nmYOdTCUqU8PirLi4U5tqfcpX7Gc2jT7szw8O/g66rpTSxe9D6SNvlmMujFCxUjlYii6U3GUXFptNNWafVY0uJsq0+xOHKivJTV2e39GOQvaXbfI8gV7Y/H0aE2lwg5LefhG7PV8PC7O8/JQypvbbMNopJqGS5ZVqQlbhWrfaafj6cn4HP0tM26Kq44xG729ni4mrv02qJrq4RGZ9kPo7NcTTxWY4nEUlalOo/NrqgtIr8lI4jnZcTjRqWSS4IybbWhsptxTERyfD7+qm9XNyeMznvda+VFnssB0bZfk0J2nm2Pdaor8aVBaeDnJfkny5gZS+G70ZWd9Gdw+VlmrxPSIsmjNSpZLgaOFVnp5yS85U8d6dvA6Ww1Tdr6D0vo5t49vfv+GH2HoPSTp9BTRPHG995PMI5vk+U56mn9k8tw+Jk18twUZ/xoyPFYmprY9d6F82jmnQtlS1dXKsbiMFN3+JK1WH8qXsPM1p31Obp7ezmmOyZj3dng+P8AWC1On6RuUevPfv8Ai8B0lYP7L9E+1eDUHOthqNDMaKXJ0am7N/kVH7D5RxcVvX6z7RyKhTxmYyyyu7UcyoVcBU7q1NwX8ZxfgfG+bYephMTVwtRWqUKkqU11OLs/oNWqoxFUT6p+XyfQupWo29F6P9Mz473iaiszUzdU4s0y4HmL0b3vaWDIUjRxZhshCgGOFRgpCAhyA4IgpGysgFuTiBcAAwQCMNhkAcwihUAKBA2GDEQagEUA7gAQDIA4scwwiKAcgAZCvhYneAAQIJzKL8ycwKTkABQiAi4AUhRAAyAgLi4UHiQpACAKoQpCCdwKgAA5hgQIa3sOBA5EAdgKQAgABgOQAIoQpCAA+wBWRRzBUBpYDiEOKAZCigIvMAGLgCFAAAoKCHIdoAAFAC+pBwAPsCIUoAIMAAUInIpECigEAcCgAAgwBfoBBzCKACqIpHoQovUGLi+oQ5AnYUKcwQMoE8S9pOdwAY7wQLm3CYirhcVSxFCcqdWlUjOnOPGMk7p+DNNyPsLFU0zmExni+1ZYjL9rdnaOZ4tWyrafASWLUFfzM292rZfKp1oqa7l1nzTtHkeP2ezzGZLmdNQxeDqulUt6sualF84yTUk+aaO0PJg2gWZ5Li9icRPerzUsflSfF14RtWor59OKkl8qHaezdJeya2xyehWwMI/Z/AUtzDa2+G0Fd+Yb++Ru3Bvim4fJt9CtVRs01dmI7p3xPyn2TyeIpqjo/VVWK5xTVOY9vbH16nzxbU2RXtFaMqdWUJRlGUZOMoyTTi07NNPg0+RlTRzbcZl2tTOMTdTXIxhE5EInPt23HrqZ0onKpQNVKHsObRidjatuFcrZUqeqZzKUbNPg+RhSgcmnHkdhRRiHAuVvC7e5Z5+lT2goRu4JUsdFcUuEZ93JnWuIpeZr1KT+JK3hyO8cK1TlJTpxq0qkXCrTl6s4PimdadIGzlTJ8XTxWH3quXV/Qo1nxi1wpz6pJe1Hg+tvRU0U/aKI3Z3+/wCod10D0hEVfZq59nl7uz1ex6qVCxkkeDil6mZEZpahIyijbTSwmWdON+B2P0EbGUtq9sY1Mypy+wmVQWMzKS+PFP0KK7Zysu651/hoOUlGEZSk2lGMVdtvRJd59ZbFbPx2K2LwmzVo/ZCq443N5ri8RJejSv1U4u3zmzt9FpvS1RTH1Dz3WDpaOjtJVXH4p3R7f2ez4nEzxmLqYutZTqSu0uEVyiuxKyS6kbsPiJ0KkatKpKnUg1KE4uzi1waPF0p20ublO6sekm1ERs43PhdVyua5rmd/HLpfyk9lVhdoae1+AoxhgM7k/hEYRtGjjYq9RWXBTXprt3uo6fnSPsfMMkw21OzuY7K46cadPMYLzFWXChiY60an5Wj7JM+Ss5wOJy7MMTgMdQlh8XhqsqNelLjCcXaUfajgV2IxNPbHw7PL+r7H1a6WnpDR0zVP36d0+fvcChH00kfUvk45b9juiXE5hJNVs6zV2fXRw8bLw35y9h8v0YPjHV8l2n2llOWrZ7ZzItnFo8syyjSqr/LTXnav8abXgSKcURRznPd++GnrbrPs/R9WONX3e/j4ZcvgzyOz8KdbNsPGu1GjCXnKrfBQit6T9iZ4xSuzx23mbPZ7ot2rzmNRQrPBLA4Z8/OV3uadqjvMXKZqp2Y4zu79z5h0TZnU621a5z8N75H6RM4qZ9tXm2dVH6WPxlXEW6lObaXgmj1GlU/qhtM5mZVnd68NEeMg7TT7TqOk9TE3/u8IfoHTWtm3h9JeSrmXwnK9rMjlKTk8NQzCjFPS9KpuT/i1V7Ds+pojoLyWcyhgOl3KMLWrOlh81jWy2s+tVqbUf46gd9Yrep1J0pq0oScWu1aHe6CvbmY9UT8vk+TdedHsaui9EbpjHd/VgsRPDSVak7VKbU4NcpLVe9Hzz5QWXU8t6V8+p0ElQxVaOOopcNyvFVF/KO/K89DqTykcG6lTZnPIxVq+CqYCrLrnQn6N/wB7lA29IW8URV7vn8mfUnUej1VVqfzR8HS9TiapG+ouJpkjyl6ne+r0y1shk0YtHEmG2AhQYTChCgmDKCxUORMCEMiImBGCksFLgJalZBi2C8gQLEKyMiqQB8ABCshiA7AAoAxcgjeouGGFBYAgW0A1YYAgHEAAyEDvA5F1CoAyAUAIgAACDmGQKF4EKQCABQAANRzAYAIhQBCkAcx4hhkAgAFIW4IIOYAAAEUZGAQAAFZBdgKVAXAKGgHaXtCBAUCF5kCAoHIFArZAuwAAAKgiBAXiNAXmUQAvICAMBAcgilEAHMAUILiBQOZAKCcy8wJcAMC8gQvZcojFwEBVx7ACPiAYuCMooCYQC3UQvLQMCcgCMBzIGCD2jo8zfG5Tm1LFZdiHh8dhK0MXhaq+JUg07+5adVz6yxOY4DaTIMLtlktONPBY+W7i8NF64HGLWpRfUr+lF84tHxXgsRLC4qnXjf0HdrrXNew7j6JtvJ7I5zUeKpSx2Q5nCNHNMGn91p/FqQ6qkL3i+9cz3HQV+b+liKd9dvdjnTO/Hu7O7teZ6e6NjVUTHCZ3xPKfKXve2ey2T7WVZ4qvVWW51b+v4w3qeItwVeC1b/ykdetM6v2j2Rz7Zz7ZmmXVI4V+pjKP23DT7qkdF3Ss+w71zTLqVBUMbgcXDMMpxsXVwGOp+pXh1fgzXCUXqmcajisTg23hsRUo34qL0feuDO+t2aaoiuzO6ez64fW54vT9NanRz6DU05xu9ceboChHeSlBqa64u/0HIpx11O4cxw2TY6o55hs5k+Kk/WmsP5qbfW5QszxlXItlbvdyCpT/AHPMa1vezn2/SU8ae6Y+eHY/23p647fDzde0oLicujBHuDyfZyL0ynGL/wCYTL9jsgjwyvGfn8jsrdyY/LPh5uPX0nanhE+Hm9ZpRRyYQPPxwmRxemWYtf7fI2Rw+S/4txf59I5Eajd+GfDzcWrW0z+WfDzeCglwOR5ujiMNWwmLowxGFrx3atGfqyX6GuTWqPMRw+Sc8sxn59I3UqGSL+9uM/PpfUa7t6m5TNFVEzE+zzcerVxxiJ8PN01tdsJi8u38blHnMdgFq4pXrUF+El6y/CXikenQjfhqj6dVDKFJSp4LHwkuDWOenuPG4/ZTYzM6rrY7Z/ESrt3danjXCb72lqeF6Q6tUzVNel3eqceE5eh0fW70dOzqKJq9cYz74z4+D533bIygkfQK6PdgnH+w+a/+aS+oQ6N9hG/7E5r/AOZv6jqp6D1lM/hjvhzp64aDG+Ku6PN655Omy1PG5zW2uzGgqmAyScXh4TXo18ZL7nHtUfXfcjujfqVKsqtScp1Jycpylq5Nu7b8TxmS4bB5TkmFyTKcK8Jl+GlOcKbm5ylOT9Kc5P1pPRdiVjnwkzvtBop09v734pfPesXS09Jajap/DG6I+ublRZvovrOHPE4TCYTE4/H1lRweEpSr4io36sIr6XwNOyGeYPafZPL9osDSVClit+FWgpuXmKsJWlBt6+q4y8WZ3L1EXIt53y6H7Hdrs1X6Y+7TMRM+uf6fWXnqMkjqTym9n1Wr4TbrCU7/AArdweaqK4YiMftdV/PgrN/Kj2naSlpoa8Zl+DzrLMdkeaX+A5jQdCs1xpvjCou2EkpLuOPftzjajjDsOr3Ss9HauKqp+5O6r2c/c+cOhvJ47QdJ2z+VVoqWGnjYVsTfgqNL7ZUb/Fiz6pxWLnjsbXxtTSeIqyqtdW827e+x015P+y+OyHaja3EZrR83jMooPKl1OtWlaUo9jpRm0+qSO2qbaOPZjbmap9nz8ne9eNbFy9bsUTuiM9/DwcyDdjrPyrc2eC2E2b2dhK1TH4irmddc9yC83T97k/A7Kw8Z1qkKVNXnOSjFdbbsj538qbO4Zn0t5jgaFTewuT0qWWUddF5qPp/x3IlzdV7Imfl88+5o6jaP0usqvTwpj4/08XTOOleSXW7nHNuLknXlbloaW2zyGoqzcmX2miMUw9k2Szetk2d5dnOGlu1cDiaWKg+2E1L9B9k7d+ZhtTjauGkpYfEuOKpSXBwqxU1b8o+HcDN7u6+F7M+vdncwlnXRfsXnctZyyz4BWlfV1MNN09e3dUT03RN6arlE84mPn8peF676WK9HFf6av2Z1J8T07pmwXw/osxFaMb1MqzKjik+ap1YunP3xge2VHc42YYNZpkWd5O9Xj8sr0oLrnFedh76dvE9Bq7W1ZqeB6Fv/AGbWW7nKYz7O18r1lrY0NG+pK8U3xtqamtDxuopjO59uoamjBnYnRf0dT2ow1fPM4zCWUbO4WqqM8TGl5ytiatr+ZoQejlbVyekb634HYD2I6LqUY06eQ7R4hRVnVrZuoyl22jGy8DXp+jdVq4zZozHPhHi4mp6V0mkq2bte/k+e7Cx9Dx2M6MFx2Yzz/wA7f6pf2G9Fieuy+fP/AOeP9U5P8P8ASP8Al+MebjfxFoP1+D53sGj6Hlsh0WLhsrnvjnj/AFTD9iXRYnrspnv/AJ4/1R/D3SU/9PxjzZR1g0E/n8Hz3YH0FLZXosT/ALU8+/8APH+qYPZborvrsnn/AP57/wDpH8OdJ/5XjHmyjp3Qz+fwdAWJY+gVsv0Vc9k9oX/89/8A0D9jHRRfXZHaLwz7/wDQT+HOk/8AK8Y82cdN6Kfz+D59aFj6CWzHRPe37Etpf/P1+oSpst0STg4vZfaum2tJQz2Dt7YGE9Xek/8AK8afNsjpfRz+fwl8/WFjvOewHRZXUlTxe3OAm+EprC4qEfBRi2cDE9C2W4522a6RclxNST9DD5th6mAqPs3vShfxRxb3ROvs767NWPZn4Zb7ev01zdTcj4fF020YvQ9w226N9tNj6ar59s/isPg2/QxtK1fCy1tpVptx9tj1CSfHj2o63MOYxBRyAnPiACKEKyEBgpCAgwGFAO4EVAAAKRggjHIvHmTkA5EKyd5A0AvcMioEAgqghQgQoAjIVgKWIUXsQQMAAAAoAAAC4gCADvIAAAEZQ+IEABAAAAAEUZCkYIAARWReZEh7yoo5ED4gUE+kqKgGAgoxoAEVWDAAAMhRQO8cwBSACjmQqKABQIB3lCHIBDiUAEQCkQAFBORbgOYBNQLzGoGoF0sLDUFBB8QAHMcO0McSiWGgeoAEuUgFZPEAAwCARjQE4GMg2ee2cxXnKLw0n6dPWHbH/keAZlh61TD4iFak7Tg7r6jsOjNfVodRF2OHCfY1X7PpaJpd9dGG2uM2boVcFXw8czyTEzUsXl1We6pS++U5ftdVL4y48zs+jhsu2gy+pmux2Olm+EprexGElHdx2D7KlLjJfhxuj5+yDE0sZgI4ig/Rlo1zhLnF/wDWqM44vHZbmNLMcuxeIwWNoyvSxGHqOFSD7GvoPqly3TctxqbE/ijPqn2+v1xv554PD6vQWtXVNu9GJjhPbH7O4JOM1eLTNFRM9cy3pclipKntnkVHNKlrPMsvaw2L75x9So+9XPP4XaHYfNF/3ftZh8LUf7RmtCWGmuzeV4v3GFrXURuuRjxjv84h5zU9CarTT92nap5xv/o11Fqapo8i8DXq64aeFxceUsNiqdRP2M01Mux6euBxPhC52dvUW6ozFUOvi5TTOJne4NtTKKOUsux74YHEv96ZmstzFf3Biv4Jmyb1HNZvUc473HhFM2wg7nJp5XmLf9j8X/BM5EMqzK/9jsX/AATNVV+jnDTXfo/VHe48I6I2xjqjlU8qzL/F+L/gmb4ZVmf+LsX/AATONXeo/VDi1X6P1R3uPSijkQicmllOZ2/sdi/4JnIhlWZLX7H4v+CZxq79H6o73ErvUc4aaK4HIcHu6I20sszO/wDY/FfwbOBtdtHl2w+T1MyzSdKWPjF/A8Bvp1KlTk5JerFcdTh3dRRTG6ctNq3XqLsW7UbVU8Ih1b5Ru08sJRpbFYOqt57uIzNxfxuMKXh6zXceN8mPadYTaarshjcQqeCztpYeU36NLGRX2t/jawfeuo6uzrH4rNMyxOY46q6uJxNWVWrN/Gk3dnDwtWrRxEK1Go6dWnJThNOzjJO6ftPD6nV1zqvSxL7Zo+r9i10V9gr35jfPOqe33Tw9UQ+3YxcJShUi4zi3GUXxTXFGaZ4vZPP47ZbI5btXT3fPYuPmcfGP7Xi6aSnf5ytNd7PKxgess3Yu0RXHa+F6/SV6S/VYrjE0zhsU4vzjVKEalacZ16iXpVXGO5By63GOi7DdTV7GmMUbqbsxMREbnGuXK65zXOXlcjrUMvxFbN8Xb4NlmHq46rfhanByXvSPhrPszr5lmOKzHFScq2JrTxFVvnKTcn9J9ZdNGdLIuhLPqkZKNbNqtLLaXW4t79S34sbeJ8d4+S81JrnodRrLuxRVP1ujzme59d6iaOKND6T9U/XycFyctXxeoQIeTmd+99CcnBu1Rq/FXPpfyf8AHxx/RBm+WuTdXKM5hiYrqpYinZ/xqb9p8x0JbtWPsO8/JUx7ltJn2zsprdzfJavm0+dWg1Vj42UzvOir0UzE8p8O3wy6Tp/Ten0Vyj1eLs5yMsFiFhsxw2JauqVWM5LrSevuuaJS9FPsOPVno1yase/2NqnEvilFO986bd5U8j2xzjKLtxwmNq04Nq147zcX7GeLyvBYvMszw2XYCjKvjMVWhQoUo8Z1JNKK9rPf/KBo/wDtth800azPL6NaT/DgvNz8bxPLdAeTLBU8ftxXgvOYW+ByreXHEzj9sqr9zpv8qfYeFuaau5qPQUfimcPs2n6Rpjo+nVV/piffy73YOOwuDyjBZfstl1VVcHklD4Mqi4Vq7e9Wq/jTb8EjhtJGuKcVbiWUrLVn0bS6anT2qbVPCHzXUXq792q5VO+UkYy4HJp4DMqsYzp5bjZxkrxlGhJprr4FeWZquOV4/wDN5G6LtGeMNMVU83Cka5HNeWZq/wC9eP8AzeRj9is3eqynMPzeRnF2j9Ud7bTNPNwJmqXFnkJ5TnH+Kcw/N5GDyfOHwyjMPzaRnF63+qO9yaHA5Euc55PnK0+xGYfm8jH7D5z/AIozH82kJvW/1R3uRS4fDUxkc6WU5vHV5TmC/wBml9RqlluaJ65ZjvzeX1E9LRPa5FMw48Un1G3e9Ddeq6nqjYsBmS45bjV+8S+oSwWP4/AMZ/AS+owmume1viXJybPc4yOUnlGZYjCRnpOlF71KouqVOV4yXejxuf7K7E7ZKcq2GobIZ3PVY7BU28urS/y1Ba0r/Lp6daNjoYmKbqYXEQXXKjJfoJSkt70ZJtck9Uddr+iNJr6f72nfzjj3+bnaXW3tPOaJ3cux0zttsjnux+bvLM8wTw9Vx36NSElOjiafKpSqLScH1rxszwD4H0nWr4LH5DPZ3aHCzzDJKknNUotKrhKj/bsPJ+pNc4+rJaNHR23mymM2UzWFCdeGNwGJg6uAx9KLVPE072bs/VnF6Sg9Yvsab+c9KdD3ujq/vb6Z4T58pes0evt6qN26rk9cfeQrI+w6hzoCAGKnABhkUHEPgQBcMXQsRRAgAvAiAIFwQAGAwkQAC3CoLAr4kEA0IADAQU4geAZAZAAAIUKAMAS4DFgKS45AAGOQIAuBcAQoAgHMEAAEADuAUIVEZAQIArN8AQpUB3hFAhdQQooACHEDhzAApEEAHYO4vMoLgCcSoAACijmAA7gxcMB2juBSoAEAIF7gBNAgAqggCHMch2DQAigAAO8cgKGBcoAcA7hBkKiPUqjJzKAIAEAsRl7iMCMjK2RkEIVojMZZPJbP5vWynF+cgnUozsqtK/rL9DXJnvM62Hx2FjisJU85RnwfNPqa5M60fA5WU5jistxPncPPSWk6ctYzXU0ei6F6wV6H+5u77c98euPnH1PW63o+L8+ko3VfF7biYWb5HjcTfVPVdRzsPmeBzGCVKSoV2taNR8/wXz+k0Ymm02mmn1M9fdrtai3t2aoqpnl9bnAtbVE7NcYl4225LeheD64Nx+gz+yGYQVoZhjILqjiJr9JakbNmiUbnTXbey58RTXxjLd9lc2XDNcw/OZ/WHnGb/wCNsx/Op/WcZowkjg1xVDP0Nr9Mdzl/ZjOF/ffMfzqf1k+zedcs5zL86n9Zw7GMkceraX0Fr9Mdzn/Z3POWd5n+dz+sqz/Pl/fzNF/tc/rPHWJyNE1VR2r9ns/ojuh5NbRbQf4+zX87n9Zf2R7QW/s9mv55P6zxdhYx26+Z9ms/ojuh5SO0O0Ddnn2a26vhc/rPL47exGzyxE5SqVXL05zk5Sl3t6nq8Fqj2rBrzuztan1K56DoTaqi7TO/NMuDrKKLexVTGN8cHqdZaminJOWnI2Y2e76HN/QcWMnF7yPJam9FN3d2O3opzS758lPaenh9pcRsZmFeNPBZ+orCzk7KljYX80+xTV4P5yO+5QnCcqc4OE4txlF8U1xR8OZfiK1CvSxGHqyp1aclUpTi7OMk7pruZ9v7JZ5T222LyvbKk4+exkfMZjTj+1YyCSn+UrTXeeh6M1WJ2Z4T8XzHr90RtUU623G+N1Xs7Jb4QM3Te7obvN7rOblmEli8XQw0FrVqRgvFnbV3YpjMvl1NFVyYpp4zudE+V1mnmY7K7KxlrQwk8xxEV8urLdhf8WL9p87YyWsI+J2Z5Rmew2h6Y9o8ZSmpYehifgVC3BU6KVNW8Yt+J1diZXrSty09h5zpG5MURE8Z/rPi/RHQ2kp0ult2qeyGBA+A7TpMu4L215o7A6E87WQdK2zOZzmoUYZhTp1m+CpVX5ud/wAWbOvuZzMLUkqUZQbU4+q1ykuHvSOboa8VTDTftxXRNM9u59b5tRlgszxeCqeth686b8G0ePrN2PKbSYqOaV8Dn1JWpZxl2Gx6t8qdNb/8a54ectD6lo6vS2qa57Yh8MvWPRXqqOUzD0XphyXGZzl+QfY+l57FrMJYGnHr89ZwXdvb3vPcamHweVYLBZBlst7A5XQ+D05/fZ3vVqvtnNt91kbIYp0G2oQlJelTlJa052cd9du7KS/GZxNDXpui6betr1U9vD5y7Wdfcr0dGl7KZme+flmWSabN0sTgstwGMzvMFGWDy2i69SD4VJ8KdP8AGlbwTOHUnuJvqOvunPP3Qw+E2Qw87ShbGZlZ/trX2um/mxd+9jpvXRotLVXHGd0M+jdDVrtTRYjhPH1RHHyj1zD0TNdrtpcxzLEY/EZ5mKrYipKpNQxM4xTb4JJ2SXA437IdoLf2ezX88qfWeJTdzJHyyLtU9r63Gls0xERRHdDyf7IdoP8AHua/nlT6yvaLaBLTPs1/PKn1ni2ySE3a47ZWLFr9Md0PKLaLP5aPPs1v/plTX3mX7IM/tZ55mv55U+s8Ra5ysMlV+1P7p8V/K7O8tqqqucZ3lVq3TGdmO5zHn+fNf2bzP87qfWFn2ff47zP87qfWcTzXYZKn2G+LVye1hi3HZDlPPc9fHOsz/O6n1hZ1nb45xmX51P6zjOAUew2RbrjtTFHKHLWc51zzjMvzuf1mazfOb65vmX51P6zhqJnFG6mmrmwmKeTyOEz7PsPUUqGeZnTfWsVJ/SewYHb/AGmpOPwvE4fNKa+LjaEZPwkrNHqUIm6KZzrFdyic01THvlxrtq1X+KmJ9ztDLNtcjzNRo4yMslxD0TqzdXDSfz/Wh+NdHlM1wdDMMnxWz+bXp4au1Vpz9b4NWtaNeFuKtpK2ko35pW6dsew7LbQ1st3MDjZzq5bf0ecsM/lQ/B64+K1O80/SHponT6uNqirt7YdZe0Po59Jp91Udnk9QzbA4nLMxr4DG0/N4jDzcKkb3V+tPmmrNPmmjiM7B6T8vWIwVDN6O7KeGjGjWlF3U6MvuU780n6N+pwOvWeN6T0VWi1NVmezh647Hd6PURqLUVx7/AGjAuQ65y1BARRhhggBXCDYAx7SsEAgKBGACAgELgGByDCg5AAS4KyEADmAoQo7QHIhSEUYGoAEKABOZRyAgGoAAAgAciACkKQQAEBAAKBjmGBGASxFLgWAGfgO8XC4FRQgHYBwIVgIjABVUDkAiAoQAoQ7ygAAAAAqCHIcCgAACsECgPAjHMMAUnIoEHMrJzAaMX1HIcihzFgOQRQAAKiFXECAdxSiFfAaC4EA4goEBSByADKIwypcyAYsjMmQkicScyhkViwUhjKsew5uGzPGUI7nnfOQXxai3l9ZwxY2Wb9yxVt26pifUlVFNcYqjLyyzWjP7pQnB/gSuveHisJJr7c4/OgzxAOwjprVfmxPu8sNX2eiODyzr4XliIexmDrYf7/D2M8XrcGM9MXZ/LHj5sosxzeT87h/v8PYzF1KH3+HsZ47xGphPSlyfyx4+a+hjm5/nKH3+HsY85R+/R9jOBqDXPSFc/ljx8z0Uc3O36P36PsY36P36PsOFqDH7dXyjx8z0Uc3PVWiv26PsZ5nA59g8Hl1Wl5ueIqzjuxivRiu1v6j1cHK0/TOo08zNvETMYarukouxiplWnKrVlUm7yk7mLQKkdVOZnMuTw4OVgHru81qjvryVdtMNlG0OM2RzjFww+V5/GKpVajtChjIfcpt8lK7g32rqPn2nN05xmuKPNYGcfP0pR1TaaO96LrpuR6OeP18HXdIaem9aqorjNNUTEvvLE4PEYavKjiqUqVWLs4yX/V0cfPM+o7EbL5htbjWorDUZRwVOXGviJJqEYrnZu7fUj5myzpw6Q9msJSy3C5zSxmFhG1KGYYdYh011Rk9bd9zibbbWbQ7a5JTzTP8AMqmLrJuMYJKFOkuqMFoj0lrT3NRXXamYxTGZ4747u32vmOn6nRpNRReqrzTndzdaY3EVateriK03OpOTqTk+bbu37TxqfXxOVjHu05Lm3Y4iPJa65NVze+r2qYincvIgBwm0ORgnxXU7nGZswsrVe9WN2nq2bkMaozS+pOj3GPM+hPZrFSlvzy3E4rKqj6oxkqtO/wCLUt4GVaWp655OeMWL6PttMjnL08LPC5tRXYm6NS3g4Hm6krvQ+n9A17em2eU4+fzfJusGm9Fr6+U7+9hUlqanJosrmqctPqO/iHW00ttXGYXLMuxud46zw2X0vOuL/bKnCnDxlbwTPnHNsbiczzLE5hjKjqYjE1JVasnzk3c7N6cc4eGhg9kqM/SpWxeY2fGrJehB/Nj72zqmq92N1xeiPm/WTXxqL80xP3aPi+gdVNB6KxOpqjfXw/2xw7+Psw1NpN6osZR+UjX2E4Hj/TTD2Gy370flIxbvwaZqAm/M9hFLdEzTaaadmtUzVSld2fE2pXRybUxO+GFTysl57DUsXFaVG4zS5TXH28THducjZ+m6+W5lh+LpwjXh3p2Zgo81z1PS025rtUXf1R4xOJ83W7WKqqeX9WlwG6bnEljH0S7TXulS10M7ahIsWzaVK3AzREXU2xThrneyRsg+ZriZXNtO7ewmHtGzVelj8rxWSYuXoeakoPqpS0kvxZbs13M6+xFGph69TD1o7tWlNwmuqSdmew5ZiXhMxo4hv0YytNdcHpJexs4u2tHzee1Kv3+CqN9cl6Mn4uLfia+mKfT6Si720TifZPDuncmj/ur9VPZVv98cfN4QxKyHkZduAACggIKGTtDsQBzFyMC9wt4DkTUgMBgKAAIFsCagAAFCFHMgg7ANLhQDmABORQRS5C2IAAABk4IoAEKCAyadYYAAACAWBAYAZAJ2FDCnAJkKwJ3hhggjAAVmGOIKgO0FCGoDAAncFwKAABQHMFAg5jvKBF1lb6xwBQ7wTmVACkAApEUAikGpRWTXgBfUBzKQBBgCwDiAuIsVQX1GgAq1AGgQdgtQAKQo5AQB2BRSNO4ABoCwAheBCgORCkfYUQAEEaIVgipYjMmQkkMWC8xaxMKxaFi8hYmBjYWMhZEwrGwehbCwwZQFsXtGBjbUGWgsMGWJUUFwZQoBRDyGSzcq8aT5O67uZ48zoVZ4evCtTtvQd0b9Lf8AQXqa+yOPsa7lG3TMPOZ893ERPZsgmsRspVpcXF3PUcyxmHxrhVpPcdtYS4xffzPMZDneByzKK8a8nVqy9SjD4z7XyR7fo/X2LetuXKq42Jid/wBfDi6XV2LlWnppppnaiYeuZldYnc+Tq/E45lWqzr151p23pu7twMTxV+5Fy5VVHCXc0ximIkABqZIxCW7OMupoWI1dEzMb4V3J5L2LS6VqOSzf2rO8Bistlfg5TpudP+PTie9elFuMlZx0fejonYDOZbP7XZFtDFtPAY6hiXbmoTTkvZvH0bt3goZdttnODpxcaUcXOdK/OnN70H3NNH0PqxfzXXRPbETHz+MPBda9Pvoux64l4e1xKvh8qwONz7HJPC5ZR884vhUqcKcPGVvAxc91HpvTxnCwuHwOxuHkt+lbGZnZ/t0l6FN/Nj72eh6W1v2bTzMcZ3Q8xodHOs1FFiPzcfVTHHyj1zDqnNcdis0zLE5hjajqYnFVZVasnzk3c8ZVlvTsnotEcnEPcg2uL0Rw7aanyPWVzNWzPHtfYLNFNNMRTGIjgEMl2kODhuylgUEwqcDkwldKXXxOOjdhleMl1M36aZ28MK+D2zo5pKvmmOpyV4/Y+tJ+CPHwXoQ+avoPZOiajuR2izCVPehhsqnHe5JydvoPXYq1OHzV9B7XTRP2OiJ5z8nTVzH2iv3fNGjGxm0RmMwzhjawBSYUXWUxLcQjJcC3MQjLKYZ8VbrN21tq2AwGK5u8JPvin9KZoizZnT3tnKP4FaP/AK0W9O1pL1Pqz3TDCIxdoq9fxevvUgB4x24yAqADtAZAHMgAcwBxIAAXUAHIcgAYDHMilwAgABAKQqAVCFAC4uQEAABTsAAANAAByuAAuQpEQOocxyAAcxxGgEKCEAcwAADJzIq8yAEAj4l5EYU4MAAZIrAXWVDgC8iAUELyCIUEApCgAVEBReYYDAgGgAIcy3BQ1uAEAKLgAUgAMhQUAOQCAXaAygColwDKiAgoA8ChzA5gAAAAAAFsRFsUQAcdQHgGAAJw0KwUQBggnMMMARoMrvYWIrF8QZNaksMDGwMrCzAxFjKwGBjZCxbAYE5AtijAxfEFAwJxYLYgwDIUliKgZQY4ViVMWKkUZLsKRXKZwwAGAITkXtIyK8hltp0XFq+7K1uxnf2W7aYLH7I4Ce1MMVHGYCjHC08xoQ846tKKtCNaHFuK0Ule6Suj59yd/wBUuHyldeDOzM03aOxSg+MtT6B1Xtxc09d3tpj68HmenbdN2aLVXCqXnsw6SNnMuSqZJTxGbZivuM69HzWHpS5TafpTa4pWSOpM3xeJxuaYnF4ytPEYmvUc6tST1nJ6ts4WHdsQteZnms/Nzm160tEdTq+kbupom5dnhLkdH9EafQVzFqJmZ4zPH9o9jg4ue/UsuEdEaihHla6prqmqe138RiMCIXSwMMCMjMmYskwojk4KN9/wOOuJ5fZzLsTmeLoYDCQcsRi60aVJLrfPwV2crRW5ruxhru1RTTmXvOQ0vsV0RZljZXhXznFLD0r/ABqcdH+l+B6pLVtnuHSZisPRx2C2cwEk8Hk+HjSTXCVRrV+y7/GPT2e4qo9HRTa/TGPf2+Lo7NU1xNyfzTn3dnhEMWR6FsY6miYcgZCoMxwGlrkIHqRVTKmYsEGa4G3NP7XEn99j9MjVDVmWdtxyWjD5VWL/AJTFzdp7s/6Z+THGa6Y9cPBE5hsh47LtGVycwALyCIXkBGANAAAIoAACA5hEFZA0ABCgCAAKpB3AAx3jkREUHMpAAAAAF0AgDQAAMACcyggnIBizABgEEAHIAx3B9ZNSKoQFgD4EAIqFAYE1AAGYRSFRQR9YIKQBlApO8vIAACoADgBeVycgL9QAoQAAAoAFAcyFJzAoHMcwCA8AUAgAh3gAAO8IBQpC8wgBYFDuHaOQAiKAAAAAoBQJYtwBAAA5jmCgQMO4fEogA8AoWwsOARGiWZlzFusDEW6zIgwILFsg+uwwMWtCW0MyMYEKhbmLDAgKkLATkC2FuoYGNgVixMKxLoWwsMGUsEipci21GERKxQiPiAJz0MkQAQPiRklW/A1Vh8VTrNXUZarrXM7I2or0amyuDnha0a1GpG6nF6Ps7H2HWHI20cXiKNOUKVacIS1lFPR9tuB3vRPTc9H27lqac01x74lwNZoPtFdFyJxNM97k4df1QuCV9W+C7WcfM68cRjJzhfza9GHauvxNNSpUqetNtdRikdTf1M3KPR08M5cym3iralShCxxobCwKLFwiMlrmVi2vwGFywt1HdHR7lNHY7ZCvtlm9O2Mq0PN5bQlpK0+EvnTtp1RTZ690S7EU8zf7Jc+jCnkmFbnFVXuxxMo8bvlSj8aXPSKu2cjpC2nqbS5x5ym5xwFC8cNBx3XK+jqNcm7JJfFikus9P0Do5pidRXHHh5up1930s+hjh2+T1nEVKlatUr1579WpNzqS65N3bNTM5GJ3NUNUMWQyZi+w1TDOEfUQr4XIYTCpYhkQxwuUMrBalsMJMnBOxr2knaGGoJ8Lt+CS+s5FCKlWhF8L693E8Zn1TzmYW+RBLxfpP6Tj9IV+j0df+qYj5s7MbV2PU4JeZFaxeR5OHYnIAFRe4gQ5ALCw5WDIp2DuA5EAAcgFuYXEAAAEgAACo+BCkBAikBFAXkQAwGAABQFiF5EIAKQoPgAwBByKTiQAOwAAAQQoIAsLFuCKlg+IDAjADIqdYbHaNAICgDMdo4DmVAF5gCBC47AKAAhwABQHBAdgAAAGBxKBOBQCih6kXAoAgAC5e0i4gotwQAUEAApABR4EKARSIcwigLiQKo5hBFAAoQIrAWAvPgAxoAIysX7CiBdY5i+oUKyFKIWxOXaVgTmwkXkEAYswOwochbmF3AuASFii3UXAliF1FhgY2FjIjGESwLyFhgQci9g7BgQFFiYRjYFaFhgRaAtuYZMKlgUWGER9pDKxEiYEDK0SwUZi0XnqLEwMdQZNES1McMsokZWCKWISZQqFijCZLAtjzeymyefbUYl0smy+deEGvO4iTUKFL59R+iu7i+RlTTNUxTTGZYVVxTG1VOIeDt4I7T6OejH4RRjn22TeAyqEPPxw1Sfm51ofLqP9qpdvrS4RWtz2LKNmdkOjWnTx+f4mnm+fWU6NGNLeUHydKlL+cq2S5RbPVtstr812mxLeKl5jCRnv08LCblHe+XOT1qT/AAnw5JHpej+gpzFeoj3efk6y9rqrv3bPDn5ebl9Ie1zzucctyyn8GyWhuxp0ow8351R9V7vxYL4sOXF3kz01tmcncwZ6OqMboaKKYpjEMWYtamTMWceuGyGPMjMmiWNEwyhjyIysWMcMkSLbQqDRMCWKA9ESUbcPFXnOTskrX7+Puuev4mo62IqVXxnJyPOY+fwfL9x6Smre3V+7TxPX11vmdP03c2diz75crSRnNTLkCFOhhywAFAAEAAAOYAIAYAAcwAoOI0IBScyhAR9QDBBCsDkFQIMAAEGAABAAAAvAnOwAAIcgBCkYAC2gIHIhSABf2AcgAAZFAOQII+AFg+oKgZeRiBQQEGZUiIpkhzHMcwgDQHIACkKwABGVFBABbaAdxe8CBDkUALAFFAABkZScQA7yjvAgKCiANAACgCFAAActAA7yk5lKBbkARe0AMCApO8CgAoXC4AW5ATmCgKDmOQXAocwO0FDvHMoLgQttBYpcCWDLx5C2plgQveWwtqWKROYsWwt18C4EsN087stshtTtTX8zs3s7mmbyvZvCYWc4x75Jbq8WdtbMeS10mZpuzzL7EZHTteSxOK89Uj2OFFSs+9omYZRRVVwh0RukcWfUkPJWynLoqW0fSjgsK7+lCjhIQt41aqf8U5uG6CegrCaZj0l5lipLj5nE0Yr+LTn9Juo0965/LomfZEyy9BX2vk+zG6fXL6KvJso+g9o88rtcZfDKn6MOa/8As08mpcc5z786rf8AAORHRmtnhZr/AOM+TGbcxxl8lbocT60fRp5NS/vzn351W/4BhLo28mtf35z5/wC1Vv8AgF/srXf5Ff8AxnyYTTEdsPk3dDifV76OPJt4LOM9/O63/wCOYPo28nG+mcZ5442t/wDjl/sfX/5NX/GfJjNVEfmjvfKaiHGx9US6N/J1v6OdZx+e1f8A8cxl0b+Tun/ZvNvz6r/+OZf2Lr5/6NX/ABnyapvW4/NHfD5Z3SW7D6dr9HHQApfa89za3+l1rf7uSPRt0BtX/ZDmPjjay/8A6cv9hdIf5VXdPk1zrLUdvw83zHusri0fTFbo36B4v0NoMd+e1n//AE5x59HXQdf0doMX44yv/wDjmVPQHSE/9Ke6fJoq6Ssx9R5vm63UjHdZ9GVujzoWi/Qz7EP/AGuv/wAA1S6Pehz4ueVvzuv/AMEz/hzpCf8Apz3T5NU9MWY7J8PN887rY3bM+gJ7A9D8NPs5Wf8AtVb/AIJrew3Q+n/Ziu/9rrf8Ev8ADXSH6Pj5MZ6as/pq7o83QW7oxuvqO+pbEdEKf9lqzXZi63/BLHZToco+ticTVt/l8Q/oposdWOkJ/J8fI/tuz2UVd0eboRRDVuLS7zvqWB6HMI70skr4lrrWInf8qcUZUNpNhsrqKeV7D0XOPqznQowa8Zb7N9HVLXVfixHf+yT0zTP4bc+/EfOXSOUZNm+b1VSyvK8bjp8LYehKf0I96yHob2xzGcVi6OFyyD4+fqqdRL9zp70vbY93zHpTzmpQ8zgcvy/CU1w84517eDaj/FPUc62nz/NoOnmGb4qpSf7TCXmqX5ELL2nYWOqUUfzqs+Hn8WqrpK/X+GIp8Z+UPOUtiujnY+ann+Yzz3Hw1eGT9FPqdKm3/HnHtQz3pJzKtho4DIMJRyXA01anuRj5yK/BSShT/FTf4R6Q2lHdjFJdSVjVJnd6fo7T6SMWqYhx5iq5ObkzVPr4d3BjiJzqVqlWpOdSrUlvTnOTlKb623q2aWbGzFmdcN0MGl1Gto2Mwlx6ji1w2QxI0VjiaKoZsGSyM2Ys1TSsSxsSxkGjCYZMbFYBrmBEjfhaSq1Umm4x9KVurq8eBrVrankJP7G5S8S1avUf2tP5TXo+xXl37pus0Rma6vwxvlruVTGIjjLwGfVnLEzhe6g9zTg5X9J/o8DxiNmJd5qPVxNaPE66/N+/VXLt7NGxREKGAcVsAUgQKQvIABqAAAAAAKcwxbqCAcABwAELciAAAgIAMKciFAEKicgyAAAACAAAATkLhFAmo5gpBAgUCAcwBAVkCgYBAHeO4neQLAAKE5lIAYAIMuRUAjJDmUWuGAAHMAAAAACIUFSKJYoLaxRAXkRgGEUICFDARAUBTgCPiXiA5C2gBQAYfEAAOYQKhy0AUAQAnMqAAIcwtQu0oF5kCAosOwWCABNABSF4FDmLApVR9gK+BOBQQKEigAVIyiFwFsLFWplECcAtSqJbGyKQUdTZRoVK9aFGhTnVq1JKMIQi5Sk3wSS1bPfuivoo2j27q08TSg8uyhtp46tTb85biqMNHUa69IrnJHfez9HYbovg6GyuUwzDOFHcq5hWqqdS/NOql6K/ApKK65M5+k6Ov6urZtU5cuxorl7fwh1LsN5PW2meQji8/wDN7M4BLenLFx3sRu9fmk1ufvjidpZDsr0K7C04ypZZPazNKfGvirV4KXYnajHwU32njdo9pM3z+X/eeMnUpJ3jh4Lcow7oLTxd32nhJTS4HsNF1StUxFWoqzPKOHf/AEdnTpLVqN0b3YeZ9LG0NSksLlOGwWVYWGkIRh5xxXYmlBeET1HN9qtpMzbjj8/zOvH5DxMow7t2LS9x4adW3M486lz02l6K0mn/AJduI92/v4sLkwylClvObhFyfNq79pXUskk7GiU1yZrc+07PYy4N2pyJVXe1zBz04nHczCVTtM4oddclyJVXybG+9LtnG39bk3+0y2XAuS5MqlubMXN9bOM6mtySqdplsODccl1NOLNcqna/aceVQx37lihwq4ch1HzbMJVGuZolU7TXKqZxS4dcS3yqPrZrlU7TjuoYuoZYceqjLkOo3zNU6jvxNLqGudQk1YYxbbJ1X1mmVR9ZrnPianPtNVVxtpttsqjfM1TqN8zXKZrlM41d2W6m2ynPU1zlzJKVzVOTscWutvppSc/aapSZZu5qkzhXKm+mkkzGTDMWcSuW2IRmMuBeRH1nHqZwxZgzNmLRx6oZwwsRmVrB9hpqhllizFrTUzsRo1TDKJYpNEaMmhbxNcwuWNhYytobMNQrYmvTw+Hg51akt2MVzZNiZ3Qk1REZlvyTBPGYy0ob1KnZzV7KTfCN+V/ck3yODtHj44zGNwlvYeinGDSspfKlbtfDsSR7BtI6OSZVDKsNNOvXhvVprjuS4vsc+C6ofOPRsdUslSXGWr7jh9MamNLZ+zxO/jPt7I9yaGn7RX6bs7PZz97jXcpOT4t3ZSIyR4p3kgY5gqACADgUgAoAAAAIAdhQqLRi5WAIVkAEfEBggEHeLBQAAAAwAHKwAAAgIoHICAAAQvEhARSdw7wKRjjccwDA5jmAZCshFCcy8iAUjYLpYgjGhWQKE7ygCAq0AGQDDKisAABcAAUgCHaAAoik4AqKARgXmBcFFCJcoQGvEMBQjKAJYAq4gAgCoFROYChSAChBdgYE4lZBYCjvY5iwBApCgUhQJwC4h8QAAAQKQFVeYBCjK5ByIUUvIIWLCqikHM2RCr2lXWEjKEJ1akadOEpzk1GMYq7k3okkuLNkUiwjKbUYpyk2kkldt8kd69GHRFg8Bhqe0W39OKUbSpZZPVRfFKslrKT4+aXDTfa4HM6Ktg8HsjhobQ59CNXOUvtVJWawsrerHk6tvWnwhwV5HsGY4/EY2t53ES0irQgvVprqS+l8W9Wer6J6CqvYuXt0cneaLo3d6S7Hu83kc62jxmMoSwmFvg8DuqCpQaUpxXCMmrKy5QjaK6nxPXKjSVkbKs00zhVZu57vT6ei1Ts0RiHaVYiGFWfpHHnU7RVnxOPOZz6aHBuVM6lQ0SqGE53vc1ylrxORTQ4FyptlM1ynxRrctDCcrcDZFLhVy2OoYOfPU1Sla7MJTdrGcUuHcbt8m/p1GiU9Sb/G5lsuDcb3U1MJVNTQ56cDBz0MopcKuHI3+sx331mjf10JKd11DDi10tsqhqdTXiapT5GEpchO5oqobZVDB1NdGapSMHMxmWmaG2VTQ1uZrcjCUmceqSKGcpmEpGDkYuRx66mcUsnLrNbZGyNnFrqbIpGzCTDepg2caqpsiEk7Gt8DORg+Jxa5bIYkfvKzE0VSzhGYmTMWjVUzhH2EsZENNUKxsRrUyZGaaoZZY8iGT4ho1zCsLCxlZl03W5OyRhs5MsUuX0HuuFwNHZLJZZnmdKM8xxEXCnh5P1dL7n0Ob5K0eLZzdlNnqOz+WranaKLoySvhMO16abV07P475L4q9J8j0najOcRnOZTxeItHTcpUou8aUL6RX0t8W22Z13I0lG3P4p4er1up9LPSF2bVqf7un8U85/THzn6nxGZYuria9bF4qo6lWcnOpN83/wBaWPBTk6lRzlxZycxrb1TzUX6MX6Xa/wDkcax4HX6mb1yYzw+L1untxRSqKQvccKG1QQvcZBfUAgF0BEUAigAQAEAqepABQOYKAICAwAACHIICABhRAdgIAAAABAAAAQAAgZSACFYICQHIgFCYIQGAApyASHMAGCMgAAKMlustyAAABmQqDKgALEFABUVAIiAAAqngVkWhWEQJl7iJAXvF9BYliigAChAgAFQAEKrBgAQIsChABApABQQcwpxFgAKi8iIvUUQcwyBFQCAAWK+GgCpzKOBALxFiFKIXvKQojKAVkoIZGUAVEXEyNsKySO8uhPYWnl+D/ZVnUHHEOKeEptelRjJaSV/2yS1XyI+k9Wj1DoQ2L/ZPnrx+OpRllmBd5qovQq1ErqL/AAYr0pdll8Y70zGuqtTdpuXmYX3FLi78ZP8ACfF+zgkeo6D6Ni7Ppq+HZ5+Tu+itDFyfS18I4PHYypKrU3pJRUVuwguEF1L/AK14nj6zvexzMRLiePrysz3tqnEYh39e5oqzscOrO/eba0tXc4lWWjOwt0uvu1MKkjj1JGdSVjjVJX5HKopcC5LGctTW5Em9Wa27G+KXCuSylJ2MZS5GEpcjG5nEOHXKyk7mDlbmSTNcnqZxDi1snK7MXIwlKxG76FiHErhm3oa3Kz7COXIwkyuLXSz3jGUjXddxi5Elx6qWxy11MJMjd+JhJmEtE0kpGLfaRsxb1NNUtc0rcwbYuYtnHrljhJMxbEmYtnFrqZRCtmDLcwZxa5ZRC3MZMXsYs0VSziB2MX1BsNnHqZQxI0XuI3c1SzSxjYyBrlWLRDJrUljXMLljYhnYWMJpMsLaDdNsYnPybKMwznMqOW5VgquMxlZ+hSprXtbfBRXNvRGPo2NVymiJmqcRDx0KTm0opttpJJXbb4JdbO2djthMFszlsdqttLU6sPSwuBkk5RlxV4/GqdUeEeL1PYMj2VyHovy+lnG0VSlmG0FSO9Qo03dUv3O/DtqP8U61272sxuf4+WKxlVaJxp0oaQpR+TFfS+L5mEVU7O32R28/Y83e1l7pWubGmmabXbV2z6qfVzn6ng7f7S4nP8xlXrfa6ULxo0VK8ace/m3xb5+w9DzHE+aVov7ZLh2LrOTmeLVNOc3dv1Y9f/I8DOcqtR1Ju8nxZ5HpjpLbnYp4/B7HozQUWLcU0ximERkkEVHm4dvKgAyQABULgvgAA0A5gCkBQACIA4jkAHIXBAKQAgpAUKhSACk5lIA5gAAQoAAIAAAAAQZAIUgAcwPAByIXtIwACBFAGLdYBAAgMheJGFO0BC+gAjKRgQAEGzkBYpkicihAgcgGCikHcCgAygTkXQgCBeZEXmAZEUACoEKKQpOIAXHMAUECKBQhpcAEC6ALakfcUATkCoATuHiUgFRWRF5cAI9QkUjAArYKhyFgAHAWDAVAuJSFFBCruKp3AKwZlCnMqAT6jKBlFHNybLcXnGb4XK8BT85isVVVKnHld831Jatvkkzho7u8mnZqm5Y7a7HUr0qKlh8MpL1uG+13txhfqczm6OxOouxRDkaWxN+5FEOxsmynDbMbOYXIcvvuRprzk2rSmm9677Zv032bi+Kaa8rXR5DGznWqTq1Zb1SbcpPrb4nisS7Nu59M0lqLdEURHB7iiiLdEUx2OHiJWPH15u7OTiZas4FaV+w7i1S41yWitLVnEqSNtaRxajepz6KXX3JYVHxNE22ZzdjTNs5NMODclhNmuTVzKTsapSdzbEOHXI2YN6hswbM8OJWsmapMt9f0GMiuLUj4sxvYt7GMnZlceuCTfL2mF7MXMJcmRx6oST1Jcc7GL0MJloqhW9TCTDZi31GuZaZgb5mLeofWYM1VS01QtzFsjZN7Q4tcscEjB8SvgYtnFrlYL6kbHIhx6mSGLLcjOPUyhGxzIZGmVYtdRGtTJ8dCMxmFhCW6yruLFXNeBLFt2GSWplu3E0plgomUaejvolxZ5vZXZjO9pK6hlGClVpp2liJvcow75vj3RuztnK9kthuj6jDH7Y4uGb5svTpYKME0ny3aX/qqPuRqru0W+O+eUOt1fSlnT1+jj71f6Y3z7+Xvej9HnRdtBtdKGJjTeXZV608bXjZSjzdOLtvfOdorrOxsRtLsj0cZXWybYihSxmPnpicyq+mpSXPe/bGuSVoLtPTtv+lXNdooSwcGsuyvgsJRl66XDzkvjd2iXUda5jmcp39LuOHeuRjavf8AGPnPb7ODh/ZdTr5/v91P6Y4e+e34PK7U7QYrMsXWxWLxNTEV6rvOpUleT/66j03MMbGmnUqS48IrjJmvMccqavN3m/Vh19/UjwdarUr1HUqO79yPL9KdMTVOzTx+D1eg6Ppt0xuxC161TEVXUqceSXBLqMEgio8xMzVOZdziIjEKjJEKZQgAAirUBaAocwPAAOwBcAgKRmRLASwDAABAAGOYfYBGCgiisLEfEtwDIBz1AcQABfAgYAcgPEAO4pAAAADvABAIVcSWAAOw5AOBCkAAAgAc+IIoOYFgYAOVidgVSFJzAE5FJYACagDYgO4XKh3FIUAATmBkNSC4FHcOYKIAAi9wC4AB2BAFFHEhUAA5AALagAAAwDAAAJgAUBMFQAQAcgUIKnaUDuAELyIAAAFIGCgW7IAKiAoBi+oHEqjAFjIDJGPKxkjKJXLk4HDV8Zi6OEwtJ1cRXqRpUacVrKcnaK8W0fYWFyXD7MbOZdszg7OGDoxVWa+PPW8vGTnL8Y6E8mTI/sx0qYbF1Uvg2TYepmNVtc4rdh4784vwPoLHYl4mrVryWs5N27OS9lj1PV6zmarj0nQVndVdn2PE4qVr6nicVLieSxz4vrPC4mevYe6sUu9uS4mIerOBWlqzlYiV+84FZtX5o7W1S4FyWiq+PM41Xib6j5nGqS17Dm0Q4NyWqp1mqRsm+JpkzkUw4dbCfaanwNsnqapGyIcOti+41yvqZsxlqzJxa4YPgTmZPiYNhxqoYy7DB6O7M5NGuQaKoYSetyJ35F04sjJMuPVCSMG+ZlxvcwZhLTVDGXG6MW9St9Rg7GqWmqBmLDkicuJpqlqmEejMQ3rwI3pc49TCYORGL6kuji1JgsYstyO1jj1MoSxClSbNExlcsbEsZ2tx07zF7r0TTfZqaqowJoOxnIo4LGVvueErSXW47q9rPJYPZvMcQ05OjSj2Nza8EKbVdf4aZarmotW/xVRDwqV2ZKPpKCTc5cIpXb8Fqz2b7GbM5Z6Wc51ByXGmqiT/ACYXftaM1t/s5ktJw2dyB1qnDztZeZg/Zeb9pxtRqtPpt1yuM8uM+Dj/AGu7c/8Aj2qq/XwjvlqyPYraHNKkVHBPCwlrv4i8XbrUFeT9iPZJZRsDsolLaDMlm+Pjr8Fp2nZ/ucXur8eT7jrvPdu9ps4jKlXzF4bDS44fCLzUPG2svFngadRRWllc6S703TVutxu9ZPRet1H/AMi5sRyo4++qd/dDtfOOlbNqlJ4XIqMMmwqW7GUGpV93qUrWh+Kkeh4zNalSpOpUqzqVJu85zk3KT623xPCzxDtxOLiMVGHry15RXE6+70pNNMznDn6PofT6aNm1Rj4z7Z4y8lXxcpPieLxmZbt4UWpS+VyXd1nBxGKqVvR9WHUufeaDz2p6Urr+7R3u7taSmnfKzlKcnKTbk3dt8wguFyo6uHLVFC4FRlCHMpCmTEABQ5FRABeZScwALpciFtAKAAI+IKyACF4oJAEB3AAACCF+kE5hVIOYAAAAAABSIoEAAAj7CkIKg+BABSAACFABELbQhAAAUIuJSEURSDVahFZLgdwVSDkABLgMACAitg7QuwGTECAAAFAnMpOHMIDIcycgULADnoECk4soDuABQtqUgQF7wF2hAB4ArAnFgMvICBAvMqIOI5lQUHApAgAAHaCiwC/YGPAcOAAhSAAAFUE8QAABQBeJABbkLx4FBcQTmBkUqMblT9JFyPpTyW8n+BdGu0u0c4JVcyxtLLqE+e5Ti51Euy84ew90rS3ItGHRRh45d0F7H4Fxaq4qniMxmrat1a0ox/i00eK282l2d2Uco57mLji2rxy/CpVMS+re+LT8dew9z0Lct6bSRVdnGd72fR1VGn0kTXOIbcZJOLbaS7Tw2KpYhtyhQqyj17rsdb510yZvVqShs9luEymlqlVqLz9d9u9LReCPSc22n2izWo6mYZ3jq7fFedaXsR2H8R2LX4KZqce90xZicURM+DuzEyVNvz1WhR6/OVYxt7WeLxWYZVTb85nOXR/f0/oOkJy3pXneb65Nv6THTlGPsRpnrde/LbiPe4FfSkzwp8XcdTOcg+Nn2A/Lb/QaZZzs7fXPsH4Sf6p1FddhLmP8Y6yOFNPj5uPVr65/LDtv7L7NPjn2F9r/AFQ802WfHP8AD+1/qnUbZG2Yz101sflp8fNrnWVT+WPr3u3Pslso/wD9w0Pa/wBUn2Q2T57RUfb/APpOom9SNmP8ba79NPj5sJ1Ofyx9e92+sbse+O0tJf8AXzTbHFbFPjtTTX/XzTpq76xftNc9dtfyjxa5vxP5Y+ve7leI2HX/AO64+z/9JY1th5cdq4Jd6/VOmG3fiGY/xt0hyjxYTXTP5YdzOWw1/wC26Hu/VKlsI+O2EF7P1DpdMXZP426Q5R4sJ2J/LDuaVPYW+m2VP3fqDzewj47ZQ936h0y2xcn8a9IerxYTTbn8sO5JUdhOW2UPd+oFQ2Dej2yj7V+odNp9pbk/jXXzy8WE2qJ/K7jeG2C5bZr2x/UMHhNheW2UfbH9Q6gTYTLHXLXdsR4tVWntz2O3Xg9hr/24R/Kj+oZRwOwz/wD3hH8uH6h1CmZKTM464aueMR4tNWkpn+jtx5dsQ/V2vg/x4fqmuWXbHJ2W1UX++Q/VOqoz7F7DYprqXsN9HWrUTxpjxcavQzPCrwh2ZUy/ZNcNpYy/fIfqmmWC2YvptBB/vkfqOu1UVuC9hd9dnsN0dZbk8aI8Wiejq5/6k90eTsGWC2bWv2fpW/dY/UY/B9l4+tn9P+FX6p6BvrqXsMJyXUiV9ZK+yinx82MdF1TxuT4eT36rPZGlxzmM+6pJ/RE0/ZPY6lxr1KvdGcvqPRJSMHK/M4dfWjUR+GinunzbI6Ipnjcq748nvdXaPZSkvtOX16r6/MJfS2cee3FKmrYPKN23DfqpL+Kj0pshwbnWbpCr8NUU+yI+eW2nofTfmzPtmXs2L2zziq/tUcLh/m095+2R4vGZzm2MjbFZliqkfkuo0vYjxpnF6HWXuktXf/m3Jn37u7g5VvR2LX4KIj3M4uzuuL5mW8am1FelJLvMJV4LgnJ+xHEm9TRxlv2Zng5SlqJVoU/Xkl2LVnAnXqSVr7q6kazRVrcfghlFnPFyq2MnLSmtxdfFnFd29SixxLlyu5OapbqaYp4Ii8gkWxjEKJaFQRlYsMcpYoBlCKCFKiiw5AogLz0HcAAQ5gUAK+oEKOAAj6yGRiBdAOQAELwAAWFiEFD4DmLaBSwsQvECcwWwQDwIUgBABgNRwBCCk5lIA8AFwDAEBQAWoRQIQo5gQMAig5EKQCFfeTvCq7kD4gAuIAAELzIwAHAEGY0HIFQ4gDncoFCAEfHQcCiwFGvURgoBABDiULgQCgXBRScyhcQHMpNQwBSF7AAIwwKQACgDvKFwAAQ0AQFCAsEUgKBAAARCoIoIPiABANLl0ChC8wBBfQpAD6yFIARJvR9xSMSO8Nr+mGWC2byTZ3YusqdTB5NhMLiMya9KEo0YqcKKfD0nJOXG97HTeIrTrVp1q1Z1KtRuU5znvSk+tt6tnAkrmDRy6ukK6oxMZw5NzUV3Zja7HNvH5S9pd5fKj7TgWBq+21cmvLmuSXxo+0OS+UvacOw0J9sq5GXL311r2kcl1r2nEsWxPtdXJMuTvLrXtDkuTXtON7AT7TPIy3tq3Fe0XXWjRoRoxnUTyRvuutEuute0021FtSennkN1780L9qNVicCennkYbbrsF+1Gt8SE9NPJMNt11i660arAemnkYbG11ot11o1EHpp5GG+660N5daNPMWHp55Gy3XXWvaXeS5r2mixC/aJ5JsuSpLrXtMlNfKXtOKDONVVHYmxDl+cXyl7R5xfKj7TiEsZfba+R6OHM85H5S9oc18qPtOILD7dXyPRw5LnH5UfaYucPlI45dDGdVVPYuxDcpw637Cedj1Nmshrm/XJsw2uq+UUjB1Jv4z8CAwm5XPGV2YhOYKEjDCorFsLFRcGUKCouEynIqKEVMhUEUyQAQKgUhQC07wNGOBQ5gMANQCgQuthyAAAcAJyBSEDvA5goDgAwA0AYEKAQCXKADDC4ECnFAB9gDmAFxAAAgcw/cES4FIW45gQDmCAg2A+BQBCgQFIRQeAYIIC8iBQAMAAABHwAAWABBmuspAZIBAAUAAGAAguIAKBXxFxyAWC4ai5QJyugte8oKAQABjkAgAAKFx4gaEAN6AIoqBCkQKCFAoKu8CBlYAdhFxKS4FYA58QJ2jtKCiBgAGQrHeAuTRscuIArIAFAwHqQQj4FYYGDMGjYzGxhKwxtcljNEsTC5Y27BbUysWwwZYtEtzM7EtyGDLEtigYEDRbAYGPMot2C2owZAWxLajBktzJYyJYmDKAvMMYMsWEZWFhhcpzJZmdgMJlgNTKwGFyxsWxbAmDLEGVgMGWNhYysHwGDKWBbMWLgyg7ilQwmWKRbFsW3WMGWNgrlZS4TKJCxRYYMlhYttBYuEBYoKC0ABUAgABSFKIUIAAORQC4C4BQABAAD0AEA8AHMqIGQUlirgRgAOYKHeGCEF0sCcwFXwJbQupHqAAHIByAYAEKgQRgAAACAwAADAZRAAyAEOwcAoACCApGA4hABTwIXtuAJcAEADxAVmA2OZkxUJAJgAOYCAA7CgGUMCdpVwIALzAXAAAn1jmNSioXJzKA5jmBwAAAoAAICxUAAKEtQFgVItioxLzLYWGDLEpUhYYMoC21sLWAgQtzGoUL3i5AICkYAMMAGAAIC8SBQd47wAZiZES1IIxa5lbUliYGNg0ZEJhcsbCxluiwwJYiRkwXBljYGXAImDLAtjJ8CDBljYcjKwBljYWMrAYRiC26ijAxshYtkLDCpbQWLyFmMGUFi2KhhGNtBYyYGBiLFsBgQFsGMDGxbGViWGDKAoGBLAotqMCcy2KBgSwKAJzBQMAALFQALYCIqQHEALBFKiWKgBgCF5AALFHIplO4FfAnEAALkUAAE7QUgC/IAEDggEGUBcAgEKOYELwJwKFOYJcAEAAABQIQrIQAAAA7QAAAAcgO8gn0gF7SiApORFTnoXiAQCFJzAAdo5hTiAAI+YDAkAARWfEFRDJgXKx4DQCFAKBSFAIC4uECFHMKIcxcpRAXkAICgAAAARC9xUAVMAQqFggKuBkkRI2JMqEY8jJQfM7G6H+hrbfpLqKvkuChhMpjPcq5pjLww8WuKjzqS7I37T6z6PPJW6N8hhSrZ+sXtRjY6ylipOlh7/g0oP6ZPuMKrtNKxTMvgenSlVko0oyqSfKEXJ+48ng9mNpMXDewuz2cV4/Kp4Go19B+peUbNbPZFTp08kyPLMujTVofBcJTpteKV/eeSg5uV3VqP8dmub89kMtiH5TYnZTabCw38Vs5nNGPXPA1EvoPEVqbpT3aqlSl1Ti4v3n66ylK2k5/lM8HnOzmRZ1eOcZHleZJ6P4XgqdX3yjcRfnkbEPyl827XsYtan6F7Y+S/0WbQ06k8vyzEbO4uSdq2W1mob3W6c7xa7FY+a+lryY9vdjoV8wyeMNqMppXlKpg6bjiacVznR4vtcW0bKbtMsZol0M1qR8TZNNNpppp2aas0+owZmjEFtqGBAUgUJqUgFfeS1ygCceAsUAQosUCJNoPcXx4p95XLdhL5rP026Ltk9lK/RlsriKuy+Q1atbJcHUnOeW0ZSlJ0INttxu23zNddezuWmMvzJj5u33SHtMZON9JJ9zP1fp7HbIvR7K5B/wCWUP1T5a/ogOQZNlGT7HTyvKMuwEquJxaqSwuEp0XNKFKye4le13xMIu7U4wy2cb3yILaldrljxNzDKWXxpJd7I3Dhvx9p9S+QJlGU5vn+1tPNMswGOVPAYaVNYrDQrKD85UTa307XPrX9hmybf9qmz/8A5XQ/UNVVzE4ZRS/Kb0GvukfaRxsfq7V2N2SjSutl8hVk7/8AdlDqf4B+VOYOPwyskkvts7W+cxRcidxNOHHBSGxEBSgYtCxWCiLqFiocwJYhkAIB4AACiwRiwZNEsBAUWAgsUfSBC7pUjzGxeFw+P2xyTA4ykq2GxGY4elWpttKcJVYxlFta6ptDDGqrZjMvDqL6iuD6mfadbo66MqWJq010cZHuxm4q+JxT4P8AdCPo/wCjG1v+zjI/zjE/rnaR0RqpjOzHe8xPW/o7nPc+LN19RN19TPtF9H/RmuHRxkfjiMV/xC/9n/Rm/wD/ABvkX5xiv+IX+xtX+mO9P4x6N51dz4u3H1MKD6mfaMej7ozv/wDDfIvzjFf8QyfR70Y8+jfI/DE4pf8A3Cf2Pq/0x3rHW/o2e2e58W7jfJkcWfY2YdFfRRjobktiamAb+PgM2qwkvCopI9O2j8nnZjFKU9mdrcfltW/o0c4wyq0v4WjdrxiabnRmrt75o7t/wcux1l6NvTiLuPbmPi+aWtCM916Q+jPa/YeUaueZZfAVHajmGFmq+Fq91SOifY7M9MlFp2ODPHDvKaoqjNM5hiCtAMhF4kKBAABQhyAQAQAAAocChmVNreV+sIJw5zin3j0PvkfafY3RZOhhei3ZRUcsyZuplcJznVyvD1Jyk5yu3KUG2+9nslPGdeX5Iv8A5Nhf+GdvZ6E1N23FdMxifb5PL3+tmjsXarVVFWYmY7Oz3vhSSjykn3MwPpzyo6lOr0b4afwLLaU1m1FKeHwFGhK3m6l1vU4ptdjPmR8Tr9Tp69Nc9HXx9Tu+j9fb19n01uJiM43gI2DjucoIrlCnAcwAgycivuAUBNbhgAyAgqAIAAKBAAgAQRQoOIFgiACxFQFIA5gcgQLgDmUQAAXmCFIBCoahUAAAnYUEEYHeGFAOYAjJ3lfaQSKCeIIraRcSh2M2CIF5CxAtoByHIoqHElggKgGOIQ5CwYKAACqgiFAEKQCgIMIAWBRUH2kRSAVBFSViix0PoDyTug+PSPmM9pNpadSGyuBq7nmk3GWYVlZummtVTjpvSWrvurm10fs3k+O2g2hy7IsugpYzMcVTwtBPhvzkopvsV7vuP1O2D2fy3ZDZPLNmsppKngsuw8aFPSzlb1pv8KUryb5ts1XK5jdC009rymDwGEy/A0cDgcLQwmFoQVOjQo01CFOK4RjFaJG6No6Gx6nhNs9o8l2R2exWf7Q5hSwGW4SO9VrT146KMUtZSb0UVqzjtjzEmrGtTjfifG3SJ5Y+Z1K9XD7BbO4bDYeLtHGZpepVn2qlFpR7Lt9x1Xi/KZ6acRWdSG2U8LF/EoYKgor2wb95YgfpDH0kNy2p+dWUeVF0z4KalV2ppY5L4uKwFFp/kxizsjY/yzs5ozp09sNk8DjaXCdfLaro1F2qE7xf5SE0zBufZ6lYkndr6T0Hop6Wdh+kuhfZnOIyxsY71XL8SvN4mmub3H6yV/WjdHv04uKuQdGeUF5O2z/SFh8TneQU6GTbU2clWgt2hjH8mtFcG/vi167nwPtBlGY5BnmNyXN8LLC4/BVpUcRRk03Ca4q60fefd/lXdOVLo6yZ7ObP14z2rx9G8ZKzWApS087L8N/FXi+V/gKtXq4itUrVqkqlSpJznOTvKUm7tt822b7WWFSMgFzewXkOROwtgIwWwAgZbCwEBWicwZUcxyFwJU9SfzWfqn0S093os2Rv/iLA/wC7wPytl6kvms/Vfop/+FeyP+osD/u8DjXuLZTwexp7rPlD+iMP/uHYv/S8Z/IpH1dJWR8mf0RVv7AbGX/w3F/zdIwo/FEspfGz1ZL25luOw5TU+sP6HS77T7Xrry/DfztQ+0N1WPi3+h1f217XL/N2H/nZn2lc4lXFthpxmlCfzZfQz8isd/Xlb91n/KZ+u2K1oz+bL+Sz8icxVsZW/dZ/ymZW/wASVcGlMALU5TWal4BFsBAUJFESBlYW9oTKEMrC2oGLQK+IGBLBl4C1wMXyBlYluSAgKkEgJYWMiAyiPYOjlKXSBs4v87YT+fgeAsed6PZKG3mz0urNcK//AK0DKlrufgl9s5lJLH4hX/bZL3nFctDDM66eZ4r92n9JpjUT5n0a3ROxD4FXVmqcOVcb1j1DpF29y7YbCZfXx+XYrHfDalSEFQqRjubii3fe433vcemT8oHZtr+1vNvzikcO70hp7Nc0V1b4dnpuhddqrUXbNuZpnt3dm7m7jUtOJjKqlzOl5eUHs9w/Y5mn5xTMF0/7Oykt/Z/NYrsrU2aY6Y0XbX8XJ/hvpX/Jnvjzd0+d7SOV3xOuNnOl7YXN6saNTMcRlVWTslj6O7B984txXjY7Dg06cKsZwnTnHehUhJSjNdaa0aOdZ1Fi/vtVRLqtTo9RpKtm/RNM+uHMwdedGnWopU6mHxEdzEYerBVKNePyakHpJd+vU0dC9OfQ/hcDhMTtZsVh3DAUl5zMMqTc5YSN9atJvWVG/FPWHO61O7XVtzFPHVaFWNSm0pxvbeV4tNWaa5prRrmmcXX9FUauMxGKufm7Tofp6/0bVEROaO2ny5S+GpJp2JqjsDpy2Ro7L7Xupl1J08ozODxWCj961tOl+JLh2NHoFrHh66KrdU0VRiYfX9NqLeptU3bc5iqMwiKtSFRi3hNbmQ7QIkUcAgHcTuMmSwMohxRbACMsfXj3gR9Zd4H2L0b2XRbsi/8ANUP5czzTlY9e6Oqv/uw2RXVlUf5czzbnc+idG050tE+p8P6Ur/xl3/dPxda+UvO/RvQX+dqP83VPm58WfR3lJP8A93FH/WtD+bqnzi+LPI9ORjWz7IfSeqE56Oj2z8gBcAdQ9QqHcEUqGpNOZWQAAOAwHIhWCCMlyu4tqFORC2KMCDiGLcwAAGA5AB8QBACBy7RqCEVbkHIAAGAHEAneBeQHIgApCgQpA7kUCY5AAR8QERR9ZRyJcA+ICuAIwABAUEVsQAM2AwAECjvHeAA945AAwAHIIBFAAoE5i/aTmUKoSIigACMIyIClDgBcgFuZR0MQn4hHd/kWZNDN+n/JqlSCnDL8Pica03wcae5F9tpVEfoXu7uh8D+QXi6WH6d4UqkrSxOUYulTXXJebn9EGffVWSONc/E2U8E85unw9/RAttsXj9uss2KoVZwwGV4WGLr007KeIqq6k+vdp7qXVvS6z7bqO58O+XzsZjsD0g5ftlSpVJ5fm2DhhqlW11TxFFbu6+q8N1q/H0uoxiMrl8y8TJIz3LLgRxN8U4Y5Y6pEMrCwxky5eUZljcqzGhmOX4uvhMZh5qdGvQqOFSnJc4yWqZ9XbB+WFicJsBjsLtXlbzHabC0d3AYmnHdpYyXBeeS9Vri3HSXUj5FZNTCqhYl5XarPc02lz/G57nOLni8wx1V1sRWl8aT6lyS4JckeOhpzIuFjJI2U0xHBjMrfUyREteAXEzRla5WtdTKFuL4czuzoe8m/bjpAwtHNcWqezuR1bShi8ZTbq1o9dKlo2upycU+TYmqIjMpETMulIwuHGN36cfafoFsV5MPRbs7CnPMMsxW0WLivSrZlXe431qlC0Uux7x2jlGw+xOW0o0sv2P2ewkYqy81ltFP27t2a5vR2Qy2H5VvdXGSXiVJSWln3H6w4nZnZzEU3Tr5BlFWD+LPAUpL2OJ6VtV0FdE20UJ/Ddicsw1WV7Vsvi8JOLfP7W0n4pokX+cLND80nHXUxaPqzpS8kbMcHSrY/o8zl5nCN5LLcxcadZrqhVVoSfZJR7z5fzfK8xynNMRlea4HE4DHYae5Xw+IpuFSnLqaZtiumrgwmmYcIWM5KxjIpDGX3OfzWfqr0Uyf/AGXbJLqyLA/7vA/KqX3OfzWfqn0Vf/DDZL/UWB/3eBx73Fspe08dD5M/ojKtkOxiX+GYt/8A06R9YxZ8o/0Rlf8AcGxb/wDGYv8Am6Rrp/FCzwfGIKyI5cNb6w/oda/9qNr5dWXYb+dmfZ3I+Mf6HX/bRtev83Yb+dmfZpw6uLZ2Ma33OXzX9DPyKzLXG1/3Wp/KZ+ulb7nL5r+hn5F5h/Xlf92n/KZla/EVcGhFSCXMzSOVDWiRbF6j2/oy6OtrukTOHl2yuUzxbp2+EYib3KGHT51JvRd3F8ky5iOKb3qCi2GkuMku9n2tsH5H+zWX0KeI23zzGZzi9HLC4GXwfDRfU5NOc12+id0bL9E3RpkNJU8s2FyCna3p1cHGvPT8OrvM1TeiODLYfmLGk2r3TRi4x4KSb7GfrL9gsihT3IZLlkY2sorB0rfyT1vaHo06Pc+puGa7EbPYm71n8AhTn+XBRl7yRf8AUuw/L1xsybuh9v7f+SRsfm1OriNjsyxmz2Md3ChWk8ThX2a+nBdt5dx8t9KXRbtj0b5lHCbT5W6VGrJrD42i/OYbEW+RPr/BdpdhsoriqcMJiYeiW6yWRtnGzNbNkxhiJJl3brQjPq3yXegzYDpB6K6G0W0WFzGpj5Y7EUJSoY10o7sHHd0s+tmquvZZxTl8puD4mO72H6Cz8lToj3f6xzld2Zy/VOnPKp6Edhejro3oZ7s1h8xp42eZ0sNKWIxrqx3JRm3pZa3itTGL0TKzQ+Xt0kotK57x0Q9Gu1XSbnsss2cwkfNUbPF42u3HD4WL4Ocubetoq7dnpoz7J6P/ACWOjbZ/C06m0FGvtTmCXpVMXKVOgn+DSg1p85yM67lMJFMvz8Uo8N+K8TKCTWkkz9S8PsBsPgKKoYPYrZqhTStuxyqh724tvxPl3y98i2fySnsVPJciyrLKmJnjfPyweDp0HUUVR3d7cSvbedr9bMKbu/es0PlV8Ty2wrttvkPZmeG/noHipM8tsOv/AG2yJ/5yw389A3xGZ3ONenFqqfVL7BxVS+Mru/GrL6TFVLK5x8dPdxuIX+Vl9JpdbTifVKLeaYfn6Ku11P5VFbfyzZxdVbFP3Uzobeud3+U3Ley/Z391xP0UzpBI+ddNxMa65Hs+EPs/VD/6m17av/KUCRkkLHUbL0uVhJrgzsfoe6ScbsnmFPLcfWnXyDETUa1GTv8AB2391p/Ja4tLRrtOuLaXMeLSvxN1q/Xp6ort7phxdZo7OsszZvRmJ+s+19u1KrU2lJSXFNcJJq6a7GrM1yqXR6p0a5jUzDo/yDFVpOVT4GqUpN6ycJON/ZY9j3+0+paWuL9mi5HbES+B6mzVp71dqr8szHdOHpfTtlSzTo4xOJSvWyqvDFw69yXoVF7LM+aZcbM+tNsaaxmx+e4NuyrZbWV+5XX0HyTDXdb52PGdY7UWtVtR+aH0zqPqJuaOu1P5at3smPPLZGDfBXEotLgfQHRbsHsBmOweT5lnOQY7G47FUpzq1YZrKjFtTaVoqm7aLrPZ6vRt0WyjpslmC/8AnlT/AIZxLXRGsvW4roo3Tw3x5u1vdZ+jbFyq3XXOYmYndPZufK9n1E4czvPpe2J2IyXYTE5jkeR4zBY2liKMI1KmZSrx3ZStJbrivadGXu2cPUaa7pa/R3YxLs9B0hY19r0tic05xyGAmGcdzVRXF8jFaHZnQHs7s7tLnecUtpMBXxuHwmAjWpU6WLdB77qwjdyUXfST0sbbVuq7XFuiN88GjU6i3prVV65OKY4utGmnwZEnfgfUlTo36L0vR2Sx7787qf8ADMcN0a9GFXF0aMtk8fFVKkYtrO6mibt8g7KegekIjOx4x5ui/i3ov9c90vl5qxiuKOVm0KdDM8XQpJqnTxFSEE3dpKTS1OKtZI6mY7Ho6ZzGX1j0dVP/AHbbKrqyyK/jyPYFU7T1no/9Ho52X/1ZD+VI85GpbmfTejKP8Jb9kPhXSMz9svf7qvjL0LyjpX6OaK/zrR/m6h85viz6E8oepvdHtJf50o/yKh89s8V1gjGtn2Q+n9T5z0b/AN0/JUZIiKjpoeolUVJ8DyezGR5ttJnNHKMjy+vj8dW9SlSWtlxk3wjFc5OyXM7v2Z6EdnsupQr7W5vWzbGcZYDKqip0IdksQ03N9e5G3VI5On0t3Uzs2qcy4Os6R02ip2r9cR8e58+pLm0u8ycNLqWh9dZVkuy2TJRyjYrZzCWek62E+F1Pyq7l7kjzlDMqsIuMMNldOL+LDK8Kl7PNncU9W9XMZmYj3z5PO1dctHFWKaKp7vN8T2inbeTZN3mfZWbU8nzFNZnsvszj7qzdXKKMJflU1GXvPTM56K+j3OYv4Ng8w2axDWlXBV3iaCfbRqvet3T8DVd6v623TmIir2T54cix1t0F2rZqzT7Y8svmdkPfekLou2k2Rw0synGjmmTKW6sywLc6UG+CqRaUqT7JpX5XPQmnY6auiqiqaaoxMPSWr1F6iK6JzE9sIDIJGDZlEVRKlbXkdi7A9Em0e0uBpZxj6lDZ/I6n3PHY9S3q65+ZpL06veko9bRnRRVXVFNMZmeTVdvUWaZruTiI7ZddqDa4GLSTtvJeJ9P7O9HXRzkdNOOTYnaPErjiM3quFO/XGhSat+NOR7XhpYLB0vN5ds/s3gYLlQybD39s4yfvO6tdXNbcjMxFPtnyy87e616K3ViiJq9n74fGyivlIri7aao+yquY1Zpxr4PJ68GrOFXKMLKL8PNng81yDYvN4KGabE5HN/fcDCWCqrudJ7vtizbX1Y1tMZiYn3+cNdvrdpKpxVTVHd5vk+1uJid17W9C+Hrp19iszq1Kj1WWZlKMar7KdZWhN9klBvkmdOZlgsZluOrYDMMLXwmKoScKtGtBwnCS5NPVHSajS3tNVsXacS9BpNdp9ZTtWas/HucZgDxOM5hyAIRQBgAQvcQBzABAABRQQqAhWAQRgpAoOWoBBBYvMcwIgAFSwfYUAYgoIrYgEDNgAEYRSkRQIC6AAgAACHAFU5gFKiPUAEBFIAoUcwVF5AhWBAAABAQe7dCG2EdhelXZ3aerJxw+ExiWKsr/AGiadOrp8yTfgfp7CvGtGFSlONSnOKlCcXdSi9U0+po/Iu7T0PtbyMOmvB5nlGC6N9qsZGjmmEiqOUYmrK0cVSXq0G+VSPCPyo2XFa6LnHLOng+o4q547azZvI9rNnsVkG0OX0cfl2JjapRqda4Si1rGS5Nao8o1ZdRrc7czWr4t6VvJHz/Lq9bGdH2ZUs5wTblHA4ypGjiqa+Spu0Knf6L7D512q2U2l2WxnwTaTIcyyiteyWLw8qal81tWku1M/Vlre4mvE4LC4zDSwmNw9HFYaekqNanGpCXfGSaM4rntTEPyTdN2vbQxlG2h+kO2Xk89EW0aqTqbK08qxE9fP5VVlhmu6CvT/inSW2fkdYi86uxu2VOovi4bN6Dg/wCFppp+MEbIuQmy+SWtSKOp2Bt90QdIew0ZVto9mMZRwkf7toJV8Nbr85C6j3Ssz0eVOyutUZ072M7mm2pklqWzTKjLCZVLQOOhlHrPoXyLeiyltjtdV2tzvDqrkuRVY+apTinHE4vjGL64wXpPtsiV1bMFMZl2B5LPk74bA4XB7bbfYCOIx1RKtl2VV43hh4vWNWrF+tN8VF6Li9T6whUdrPUxaT1er6+s1ye72d5xJ3tre472qNclNO8YtrsR8u9OXlYYbIcfidnujvD4XMcZRk6dfNcQnLDwktGqUFbzjXym1G60Uj5d2s6WekbajEyxGc7aZ3Xcm35uni5UaS7qdPdivYWKZkfqMnLdvuy9hhvpn5Q5ftptbl9aNbA7U59hakXdSo5jVi17JHdXRR5VW3OzuNo4bbBrajKW1GbqRjTxdOPXCoklNrqmnfrQimTL71jDeOv+m/oe2a6U8hlQx9OGDzqhTawOaU4LzlJ8oz+XTvxi+F7qzPadhdrNn9tdlsJtHs3jo4vL8UnuytadOS9aE48YzXNPv1TTPNb9uDJEzxhX5UbebL5xsbtRjtm8/wAK8NmGCqblSPGMlxjOL5xktU+rtuevSep95eWv0bQ2u2Dltfl2HTzvZ+k6k3GPpV8He9SD63D113SS4nwdu214nKpr22qacMb+hP5rP1V6K/8A4W7Jf6iwP+7wPyql6k/ms/VTorf/ALrtkv8AUWB/3eBqu8WVPB7Kj5R/oi7vs/sZ/puL/m6R9W8j5Q/oizvkOxn+m4z+bpGuj8UMp4PjV9gQBymt9Xf0Oz+2ja//AFdhv52Z9mpnxj/Q7f7Z9r/9XYb+dmfZe8cWeLYyqeo12P6GfkZmC/qysv8ALT/lM/XGUvRfc/oZ+R2P/r2v+7VP5TMrX4kq4NKNiWhrR5rYvIcx2q2qyzZzKqfnMbmOJjh6KtdJt6yfZFXk+xHJzEcWve9+8nnobzTpU2gm51KmB2ewM4/ZDGpek29VRpX0dRrwitXyR+g+xOzmR7IbP4bIdncuo5fl2GVoUqa4vnKT4yk+cnqzjdHWyOUbC7F5fsvk0LYXB07SqNelWqPWdST5uTu/Yedb3Ti1VbUtsRhvn6SvY1JtSsk3fqOuenDpn2Z6KcnpzzJSx+cYqDlgssozUZ1Fw35y+JTvz1b5JnxP0j+UR0o7YYuqpbQ18kwEn6GDymTw8Yrqc19sl23l4IxiJkfo9Pe4bsvYYxvezTT7Ufk9U2q2lqV3WntHnMqjd3N4+q5Px3j37o+8oDpS2PxNL4LtPiszwcH6WDzVvFU5LqvJ78fxZIsUzI/SmCtq0cHabJMn2kyPFZLn2X0Mwy/FQ3K1CtG8ZLr7GuKa1T1R1x0C9OWznSrl06FKl9i8/wANDfxWW1Km9eP3ylLTfh16Jx5rmdoec3iYV+dnlMdDmN6LdoYV8FKtjNmswm1gMVNXlSlxdCo/lJap/GS60zp9O71P1R6Q9j8s262OzLZbOI3wuOpOKqL1qNRawqR7Yysz8yNsNncw2V2ozLZ7Nqfm8bl2Ilh6y5NrhJdklaS7Gci3VNW5rqjDw8VyPv7yE0l0B4f/AFpi/pgfAceJ99+Q5Ld6A8L25ni/pgW9G6Eod77+tjpXyxNmcw2u6Psh2aylReOzPaTC0KO9wjeFVuTtyik5PsTO5E+ZsXm5ypupThOVOTlTlKKbg2nFtPk7Nq65NnGbHq3RnsRkvR7sfgtmchpbuGw8d6pWcbTxNV2360+uUreCSS0R7TGetjj55mWW5PlOIzXNsdhsBgMNDfr4nEVFCnTXW2/+mfN23Xle7FZRiZ4bZbJMx2iqRlb4RVn8Ew8u2N1KbXfFAfTcobyPj7+iLJxrbBJPS2Yf/YPHz8tDaV1r0tiskjR+RLF1pS/K0+g6y8oTpon0uU8g8/s/TyitlPwi/msU60KvnfN8LxTjbc7eJlREzOIJ3OpUzzWxLttlkb/zlhv56B4RnldjpNbX5N2Zhh/52Jz7U4qhw9TGbNXsl9YZlU/q6vr+2S+k4+/oYYypfG13/lJfSYKZ9eoo+5D4DTRuh1Z5Sbvl2z37rifogdL2O5PKOnfA5AuqrifogdOpXPmfT0f/ANC5Hs+EPsfVOMdE2/bV/wCUokDKwtyOow9Hlja5N20kzPge19Gex2K2x2gjhI71LL8OlVzHFNejQo36/lS9WK4tvsZlTbm5MU075lqv36LFuq5cnFMb5d5dGlCpgujrZ2jVfpSwbrW6lObaXsPYlVNFadOU7UaapUYxUKVNfEhFWjH2I1759a0WlnT6ei1PZEQ+D6u5Oov13ZjG1Mz3zlhtFXjR2ZznETfo08urt/k2/SfJ8Fbd7kfRHTDm0cu6PMdSU0quPlDCQV+Kb3p+5Hzrvel4nietFymdVFPKPi+i9R9PVRpblyeFVW73R5y+meiWp/7tdn11Uai/+oz26NTtPSeiiTXRxkK/yVT+cZ7Yp6Hreiac6K1/th8/6Wpn7de/31f+UvVenKz6Mse//FYb+WfNl7Nn0b011L9GePX/AIrD/wAs+cpcWeL6yRjXe6Pm+kdSY/8A59X+6fhSqYv1EKdDl69XwO1vJrbjnu0HblcP94pHVFztfybV/wB9bQv/ADZD/eKZ2HRUZ1tr2ul6xf8A1l6PV84d2Rn6PE3YBp5hhf3aH8pHj3UsZ4Os1jsO+qrD+Uj6jco+5L4rszD5Pz9bueY9dWKq/wAtnCj667zm5897O8e3/hVX+Wzhrij49Mfel+gbX8un2Q+pdhKlujrZhdWWw/lSPLec7T17YepbYDZtf5uh/KkeXVQ+s9F0f4O37IfDekKc6u7/ALqvjL0jp/lfYGiv850f5uodCM726eZX2DpL/OVH+RUOinxPB9ZIxr59kPpvU+MdHf8AdPyInOyjL8XmuZYbLcBQliMXiqsaNClHjOcnZL2nBWh2j5OFCMdrMdncoxlLLcDJ0G/i1qr3Iy8I7/tOp0tmrUXqbVPGqcO81+qjSaeu/V+WP6eLuDZXZzAbEZHLIcsnCpXqJfZTGxXpYuquMU+PmovSMeDtd3Z5GNZRXE4iraceBrnUilKdSap04RlOc38WMU237Ez6pptHa0lmKKYxEfWXxXVai7qrtV25OapeTjU3uGr6kJ/CI/tFT8lnzrtf0pZ9mmKq0MoxVXK8uTapxoS3atSPXOa1u+pWR6XVzfNJzc55njZSvxeIm39J5nUdarNFUxaomqOfB63S9S9XdtxVdriiZ7MZn38Pm+u5VJcJxcX2qxqqVknZHzBke3m1uUVE8JnmMnTT1pYifnqb7N2d/cdq7E9KWW5zWp4HPaVLK8bNqMK8G/g9SXU76034tdxzOj+sek1NUUVxsz6+He4XSHVbXaOia6fv0xy493ll2hluYVsLXdSlNJyg6c4yipRqQfGE4vSUXzi00dCdPOQ7NZNtDQqZBu4Spi6brYrLYXlTwsr6ODeqjLioPWPW1Y7L6Q9q8NsblnnJKFXM6yawmHeqv98l+Avez5zx+OxOYY2tjcbXnXxFebnVqTd3KT5nXdZtTppqi3TGa+fKHbdT9Jq8zf2pi3y/VPlHPnu5uOuJU/Aq1OyegnZTC5pnNfaPOcNHEZRk8oyVCfq4rFS1pUn1xVt+XZHtPKWbNy9cpt24zM7nuNTqbemtVXbk7oe6dD3Rrgcpy7CbV7X4Kni8fiIqtluU143p06b9WvXj8a/GNN8Vq9LJ9iZlicRj8XLF4zEVK9aSSc5vVJcEuSS5JWS5HBr4/EYvFVcViqzq16snOc3zb+hdS5JJGVOpvyUY6t6JH07o3om3oLe7fV2z9dj5H0p0rd6QuTVXOKeyOyGe+oczfSc6kftcJT+arnV3SF0n4bJ8RUyzIKdHG4ym3GriqnpUaUuaivjtdb0XadT5ttltTmVRyxuf5hNP4kK7hBd0Y2S9h1vSHWTTaarYojamOXDvdn0d1X1uroi5OKKZ4Z493nMPqWuq0V6VGol2xZx1UvofKuHz7O8PVVShnOZUpp3UoYqaf0nvOyPSznODqxo58vsrheEqllHEQXWpLSXdLj1o0aTrZp7lezdomn18YczU9UNXao2rdcV+rhPu7PF3jKaUbOzXNHgtuNn8s20y6OEzSUKGPow3cFmTjedG3CFR8Z0uzVx4x5xfKw2ZYTMcBQzDL8THE4TEK9OpHTvTXKS5p8CSnd6M9Re0djX2NmuM0z2/OHnbGovaS7t25mmqPrE+T5pzzKsfkmb4nKc0w8sPjMLUcKsHrryaa0aas01o000cI7u6Z8kjm+zsM7pQvj8sio1GlrUw1+fW4N3X4MnyijpFnyvpPo+vQaibNXunnD6r0T0jT0hp4u4xPCY5T9bwEDeh1ztAAAGOYIAYKQgCwKAsAABACgACKAMEBAg5hQDkABGUgAAEGwaEYepmxLC2hQAKQtgggOQsAQGgRQ4DmA9Ap3lILhDiAUCFIAoUnMpUB3hAAQpAKQAgxZnTqypyU4ScZRd007NMxa0IycFh9PdC3lYZzkOGw+SdIGHxGfYCCUKeY0WvhlKPLfT0rJdbal2s+rNgOkLY7bvDKvsrtDgcylu70sPGe5iKa/CpStNd9rdp+Wa0ZuweJr4PFU8Vha1ShiKclKnVpTcZwa5prVM0zTv3M8v11jFriG7H507EeUv0sbMU6dCWfxzzCwtahm9Pz+n7pdVP4x3Vsf5ZGR4lxp7W7H4/ASuk6+WV414PrbhPdaXdJmGFfVbbaIoo9B2K6aeiza3dp5Rtnl0cQ0v6nxreEqX6kqllJ/NbPe6k92z5SV0+TXZ1hG9SjuOLScZK0k+DXU+s6Q6YPJs2H22pV8dkVClsxnkk5RrYSnbDVpdVSitFf5ULPnrwO6IyuzYpWEZjgPy26SNhdpOj/aOrkO02XywuJit+lNPepYineyqU58JR964NJnq9z9Oumno9yfpN2Mr7P5pGnSxEb1Mvxu76WEr20kn8l8JLmu1I/NbaPI8x2ez/AB+R5tQeHx+AryoYim/iyi+XY+K7GcimuamuacOFRbvot6XJdb5L2n6e9Bex9DYjonyDIKdOMa0MLGvi5Jazr1Epzb7dUvA/OnoiyZ550pbL5TZtYrNcPCSte6U1J6d0T9SnVUnK3Dedu65ruzMzhlTAr3Pn7y3ukbE7I7BYfZnKMTLD5ptDvwqVYStOlhI+u0+Tk2o36mz6BjKzufM/lOdBO3/Sh0jRz7J8z2fo5Xh8DSwuGpYzF1IVE1dzbUackrt9fI1MnwzL1uwquz6P/pPek9/312Q/P63/AAS/0nvSfHV5tsh+fVv+CbIriCYfOO72F4I+jf6UPpN/xtsj+f1v+CSfkg9J8lpmmyP5/W/4Jn6SI4McS8d5FXSLidluk2jsvisQ/sRtHNYdwk9KWK/aqi736D7JdiPvulvOKbuuw+IMl8krpVy7NsFmNDNtk41cLiKdeEo4+rdOMk9PtPYfclVrebXNt+005yySdGjWpyp1oRqU5pwnGSupRas0/A/Lnpg2XWxPSbtDsvF/asvx06dDr8zK06d+3clE/UKVRpM+B/LwwCwvTosXCLX2QyjDYiTta8oudN9+kEZ25mmUq3w6DqP0J9zP1T6KtOi/ZL/UWB/3eB+VrjeEvms/VPovVui/ZNf5jwP+7wF38RTwey3PlD+iK2+wGxn+m4z+bpH1Xc+VP6Ilrs7sa/8Ax2L/AJukY08YWXxsUlio5LW+rf6HfptJti/83Yb+dmfZFz43/od/9sW2P+r8L/O1D7GvqcaeLYs5Wi/my/ks/JHHv+q637rP+Uz9a6j9F/Nl/JZ+SeO/ryt+6z/lMyt/iSeDBK6PqLyA9kY4raTPdtcTTUoZbRjgcI3ratVW9N9jUEte1ny/S1kl1s/QTyJ8pp4HoBy7FRp7tTMsbicVUe7Zu09yPf6MUbbvCGNDumk/RSOBtdnWA2b2XzLaHMpKODy7CzxNbW28oq+73t2Xic56HoXlAbKbSbddFeZbKbM4rAYbGZhUpRqTxlWVOn5mMt6avGMndtLSxx5Zw/OXpD2tzbbbbHMNps6ryq4zHVXNpvSlD4lOK5RirJI8Clc+ia3khdJylrmuyD/26t/wTKl5IXSbLjm2yC/2+t/wTOirEYSYfOlhwPo+fkf9J1v7LbIfn9b/AIJpfkhdJ17PNtkfz6t/wTL0nJMOjtiNps02R2ry7aTJ68qONy+sq1Np6SS4wfWmrprnc/UfYrPMJtPstle0WX/1pmWFp4mkr33VJax/Fd4+B8X0vI96TW0/svsh+fVv+CfV3k/bIZ/sF0V5ZsptJi8DisZgJ1owng6kp01SlPeiryjF83yNUzmWWHv8LWPin+iAbLwy/b/J9qqEYxhnOCdGulG326g/WfW3CSX4h9pOduB87+XvhFjOh3L8clLewGc05XS5VKc4O/VxRaJmmrKTvjD4WnLdZ98eQ7O/QJg/9Z4z6aZ8Aybkz778hhX6BcJ2ZpjP/tm25VljTGHfUVoYSk1JJGafK56R09Z1U2b6G9rM6oVPN4ijllWFCd7ONSp9ri+9OaZpZPizyqumLGdIm2eIyvL8XNbL5VWlSwVGEvRxE46SxEutt33eqNuDbOkZve1bLNJTsuC0BtoiMEywa0M4aEKjONyTLNM8psi//azJ3/4/D/zsTxKPKbJf215P/p9D+cibre+uHG1P8mv2S+n8ZNrG1/3SX0mCqdprzCf9W17ffJfSad+x9qoo+7D4XTb+7D1/pI2Mx22eFy6GAzHLMJLB1Ksp/DK0obymo23d2Mr8GemR6FM+XHaLZhf7XV/4R2tCpZcRKq+tnQ6zq1p9XfqvVVTEz5Y5PQaHrBrNFYixbxsxns5znn63VT6FM957R7MfnVb/AIRP+xbPFq9pNmLf6RXf/wBo7UdZ9bHne04/8IaX9VXfHk5f8WdIf6e793X+V9D2U4apCpnW008alrLD5bh3BS7PO1bW8IM7Cy+ng8sy2GVZPgaOXZfCW+qFK73pcN+cn6U5dr7lZaGG8nqHJI7TQ9BaXRTtW6fvc53y6nXdK6zXbr1eY5cI7o+blRqdZlG85KMVvSbskubOHGblJRim29EkuJ6H0mbf0smw9fJsmrqrmlSLp1q0JXWFT0aT5z5aer3m7pHXWdDZm5dn2RznlDjaLo69rb0WrMZme6PXPq+uL1Ppu2hp5ttJDK8HVVTB5YnS34vSdV+vLuXDwPQYL0lcxTvq2Zx0aPkmp1FWqvVXa+Mvsuh0dGi09FijhTHfzn3zvfSPRfaPRtkPbQqfzjPYfO9p6p0b17dHeRRXKjP+cZ55VD6v0PR/gbX+2Pg+OdJW86u7/uq/8peu9Ms79HONXXicP/LPnx8TvzpenvdHeN/0mh/LOgm9TwvWmMa//tj5voPU2nZ0FUf6p+FKoBDiecesU7T8nOpuZzn/AG5bH+fpnVZ2X0AT3c5z1/5tj/P0zs+h4zr7Uet03WCM9G3fZHxh3HKrd8TPCTvjcP8AusfpR4/zhyMDUTxlD91j9KPrVdv7svj9VvdL5izr+zOOv/hNX+WzhriczPf7NY7/AEmp/LZw1xPite6qX3Wz/Lp9kPpPYuVthNnF/m6H0yPLKVzwOx1T/wBiNnl1ZfD6WeWVTQ+w9FUf4K1/th8W1tH+Juf7qvjL07pznfYWkv8AOVH+RUOj76ndXTc77D0/9Y0v5FQ6U5nzzrRu6Qn2Q+j9U4x0f/3T8mVztTyfaii9oKV/SlRw80uxTnf6UdUt6HtHRdtBSyHayjWxU1DCYmDw2Ik3pGMrWk+6ST7rnXdE6iixrbVyvhE/Hc7HpzTV6no+7bo44z3TE/J9Aqq7aslbcxWGr4SpPchiKNShKXyVOLjfwvc40pSjNwla66no+0wc+0+wVWYu0TTPCXyGKJicw+c9o8izPIM0qZdmeGnRr0/VuvRqR5Ti+EovrR43d6z6hrSw2KwnwLMcFhMwwl7+YxVFVIp9cb6wfbFpnq+a9H+w+NcpUcLmWTza/uXEKtTT+ZUW94b5861nU/VWqp9B9+O6X0bRdbbVdERqKZpq5xvjz+PtdC7ovZWOzM16J8YnfJc/y7H3Tfm8SnhKl+r0rw/jnpu0Oym0eQRU84ybGYSlL1a0oXpS7qkbxfgzzeq0Wo0k4u0TT7Yei03SWl1X8q5Ezy7e6d7xmKxmLxlSE8Xia1eUKcacHVm5OMI8Iq/JdRrRik0zJHHiZni5cRFMYjgzi7O/G3I+ndnstp5BsZkuQwVqlKgsVi/wsRWSlJvuhuRXVqfO2x+C+yG1OV4LdUvP4ylBrrW8m/oPo3MsW6+Y4mvwVStOSXUr6L2WPadUNH6S7Vfn8u6Pe8R1w1MxFvT09uZn4R82TlbVHp/SxtLVyHZlUsJVdPG5i5Uqck7OnTS9OS6m7qK7z2V1j1LbzYjMtrcxoYyhneR4PD0MOqUKWLrVVNO7cnaFOS1uufI9R0/Ooo0VUaemZqndu8XmehrFirWUTqJiKY3zn1cI73RzlfwIkdl/9kGab1ntTsr/AA+I/wCCbo9DuZW12t2T/OMT/wAA+YR0Vr+2zV/xl9Q/tbRf5sd7q9oxbtwOz59EGZLhtZso/wB/xP8AwDFdD+Zt/wBtWyr7q+I/4JJ6J1/ZYq/4yf2tov8ANp73C6FM7q4fP5ZFUm/g2YJ7kW9I14q8WvnW3X3rqO3t/Q68ybovzXKc3wWZR2j2cm8LXhWtCvXTe7JPS9JHvWIrReIqODvDfe73X0PovVC3qremqtaiiY2Z3Zjsn93gusX2e9q/S2KonMb8c4/bHc5CnSnLzOJW9h6qdKtH5VOS3ZL2NnzjnOBqZXm+Myys71MJXnRk+txk1f3H0DKd0zp/pco7m3WMrKKUcRSo1l2t043ftTOF120seit34jfE479/yc3qndmjU12uyqM++J/d6nzBAfOnvlBCoAQFAAEAoIAKCFAgKQgMAhFAByAApGAAKFQAjAAADYADJiAqAQQHMcygAOYUGoAQAuAAAAIAFUYvqARAoBQA7AgICgCAo5kEJYzSZdzRvgJMtVjFoyaCRjMLEsQmWxLGOFyyU3a2h750bdL23+wFan9gNoMS8HBrey7FSdbCzXV5uXq98bPtPQkioTTlcv0k8n7pkyHpVyepGjSWW5/g6aljculPeW7w87SfGVO/XrF6Pk32hvJux+ZHQFtJjNlemDZjNcJKV/shTw9WKlZTpVZKnOL61aV7daR+mij5urOne+7Jr3muYxOBtjG71Pivy+dmKeXdIOT7UYei4xznBOliJW0lWou1+9waPtJTUT5i/ohahPYjZPEX9OnmtenFdalSTf0IUzs1RJMZjD5/8l+0vKE2Jj/nFv2U5n6M0ZXiteR+ZPQPnFPJemXZDM6zkqdHNqKk1xtJ7n/qP04jS3JSi/iya9jLVOZmUiMQzv2lim3qWKTPmbymunfbzox6SvsDk2ByWpltbAUcVQni8JKc25XU1vKSvZoxV9PxSSNNbifCE/K+6T23bA7ML/YZfrmp+V30ot/1lsu/9gl+uY8Ffdul72DnunwtHyu+k62uW7LP/Yp/rmM/K66TX/ezZX8yn/xDLJh930au8zdJNq58Ex8rrpOT0y7Zb8yn+ub15XvSd/i/Zf8AMZ/rk3zwH3PUuuJ8Uf0QPd/7Stnuv7Cf/fmeLreV30nyVll+y35jP9c6q6XOkraHpNz/AAuc7RUsBTxGFwqwtNYOk6cNxSctU29byZlGYlMPVL/a5dzP1N6MZ36Mdkrf4iwP+7wPys3nuSXYz9TeiluXRfsi3zyHA/7vTMrk7VSRGIe0XPlf+iIP/wBm9jv9Oxf81SPqdHyz/REf7Wtjn/4/F/zVMxjjCvjXmHbgL8ycTkMH1b/Q8XbP9sv9Awv87UPsVM+Of6Hm/wD2h2xj15fhX/8AVmfYfA4zNnx9kv5LPyUzD+vK/wC7T/lM/WlT18JfyWfkxmH9eVv3af8AKZlR+Ing0UZWrR7z9KfJZtHyfNjYr/AW/wCPI/NaCtNPtP0T8jfHvG+Txs+pSvLCVcThZa3tu1Xb3NFuxOcpS7eb11LFxNVSfE648oTbDaLYXovx21OzWHwmIxeBxFHzsMVRdSHmZScZOya1T3dbmCuxcTxNdJ6nwzX8rrpOlosu2WT/ANBn+uaqflb9J6euA2X/ADGX64yYfeLNfFnwu/K76T2tMv2W/MJfrmteV10oJ/2P2W/MZfrjMrh96UtEYVau67HwnDyvuk7nl2y35jP9cxreVz0my/vbst+Yz/XG/iYfdEqx0r5abUvJ7zZv/GGDt/CHz5/TbdJv+LdlvzGf65630m+UDtv0g7IVtmM7weRUcDWrU60nhMNKE96DutXJ/QI38Exh1NSV2j798huO70CYb/WmM+mmfAVN2aPvvyIJr/sEwn+s8Z9NM23I3QlPa7xc7M6t8rmo/wCly2s+Zhl//wBVI7Pvc6p8rl28nPaz5uF/3qka8LEvzrrL02YWNlR3kYG9ilioAuEU8nsm7bVZQ/8Ax1D+cieLPJbLf20ZS/8AxtD+cibbX44adT/Jr9k/B9IYmpvYuu7/ALZL6TXvnGxFX+q6/wC6S+kiqXPulFH3YfF4tbocrz0IJOpVp01LRb9RRv3XepjUxFBf3Thv4eH1nWXTlL+osn1/ba/0QOrXJnjOletFeg1deni3E4xvzziJ5et6ro3qxGt01N70mM53YzwmY5vpn4VR/wAJw/8ADw+sPGYdccVhV34iH1nzIxyOt/je7/kx3/s7D+C6f87/APP7vpatm+VYdXxOb5dSX4WLh9Cdzwea9ImyuBi1DHVcfUXxMLSbX5UrL6ToW+obZxr/AFz1lcYt0RT3z5ORZ6naamc3K5n2Yjze9bU9JWb5pCeGy2H2Kwkk1Lzc71ZrqlPSy7IpHo3aRGSPNanV39XX6S9VNU/XDk9LpNFp9HRsWKYiPrjPGfesUZLihHQLijQ5Eu++jyTWwWR9XmZ/zjPYFUPWtgaiWwWRr/Iz/nGeb85ofauhKM9H2Z/0x8Hx7X286q7/ALqv/KXgulme90f4xf8AiKH8s6LO7elCW9sFjuytQb/LOk7anz/rdTjpH/tj5vc9U4xoqo/1T8KQMBnl3p0bOxugiVs2zvty+P8APQOuTsToNUlj88q29FYGEb9rrQt9DO06DiZ6Rs+35Oo6e/8Ar7nu+MO1VPtN+CnbGUP3WP0o4CqG7C1P6qofukfpPs1yj7kvldVvdL5zzqV84xr68RU/lM4i9ZHJzj+y2Mf/AIip/KZxlxR8Grn70vtFr+XT7IfQ2ynobF7PduXQfvZ5OM9DxGzNRfsM2eX+bofSznec0PtnRNOdFa/2w+P6qjN+5/uq+MvVOmid9iqa/wA4Uv5Ezpm+p3B0xu+xtL/WFL+RM6fsfNetkY6Sq9kPoXVeMaH3z8kbMbXZm0Ys8zMPRw7E2C6QpZfh6WU56p1sHTW5RxMVepRj8lr40V7Vy6js7C47CY7DLFZfiaOLoNevRlvW71xj4pHzbdrmZ4PF4rB4hV8JiK2HqrhOlNxa8Ueo6M6139DRFq5G3THumPf2vNdI9WLGqqm5anYqnjyny+tz6RjV3tUxvXOmcq6RdocNuxxTw2YRXF14Wn+VGz9p7ZlXSZlFZqOY5fi8I+cqUlVj7HZ/Sex0fWzo7UfiqmmfXHzjc8xqermusb4o2o9U58N0+D3eUU0cjL8ZicC5fBq8qcZK04LWM11Si9Guxo8TlueZJm7UcszKhiKn3u7jU/JlZvwuciVSzd9D0VquxqreaJiqmffDqK7NdE7FcYnlMYeH2s2HyHaHerZdRoZJmctU6S3cJWfVKH7W38qPo9ceZ01m+AxmU5jXy/MMPPD4rDzcKtOXGL/Suaa4nfUqj5M9O6XcEsxySjm27/VWA3aNSdtZ0W/Rv17r9zPE9Zerlq3Zq1WlpxjjHZjnD1HQXS96i7TYvVZpndGeMT2e6eH7PT+i6dukLI/9Nh+k7tjNuKbfI6D2HxUcBthlOMn6tLGU2+7et+k77xMfMV6lJ/EnKPsZeo87Vq7TPOGvrZR/iqJ50/CZ82W9dmU5WicXztj0zb7bHNchzWlhsHhsFUw9XDxqxlWpybvdpq6a6j1vSWvs9HWfTXs7OcbnQ6PQ3dXc9Hb4vdnJEcjqT/tLz29/geWfwUv1jNdJuepf1nln8FL9Y6Cnrp0b/q7nb/wzr+Ud7tdsqlY6m/7TM94/A8r/AIGX6wfSZnlv6yyv+Cl+sZR106N/1dx/DOv5R3u1p1L6XZrudUvpIzx/3Hli/epfrGcekfO0tcJlv8FL9YtPXTo2f1dzP+GtdHZHe7Tb0OrumX+2fCvry+l9MjF9JOdrhg8s/gpfrHr+02f4zaHMKeNxtOhTqQpKklRjux3U21z7ToesnWLR9I6WLNnOcxO+HadDdC6rSaqLt2Ixie14sBg8M9cFIABSFAd4IUoE5AEBFICgAQiryABBFoLlIBSABQD6QAIygggIAraFxBTNrH3gIMoIC4AMAMAGQoAhewMAhz4kKgKQWLzAnMIFKAKiATnwBQBORQEAFtQypkR5DIMozLPM4wmT5PgquNzDGVVSw9CkryqTfBL6W3okm3oj746GugnZjY3o+xuR7Q4LB55mWd0FTzmrNXhu8VQpPjGMXrvqzckpaWSXi/JW6IcHsJsvh9ps0jQxO0ubYaNTzkJKcMHh5pONOnJaOUlZykvmrRXfeVJW4mqqcsojD4L6fPJ12h2Dq4jO9no4jO9mLuTqxjvYjBrqrRXGKX7ZFW01UefR0abtdark0frPJtaxdmdS9JHk9dHO21Srjo4Kps9m1RuUsXlijGFSXXUotbkteLST7RTViVne/PKULGG72H0btj5JnSBl1Sc9n8xybP6C9VRqvDVn+JO8fZI9CxfQJ0wYKVsRsBm7/cfN1V7YzZnt0ymJdX8OQXUdm4XoH6XcbJRw/R/nSf8AlYQpr2ykjsPYjySts8diac9rs0y3IcGmnOnRn8KxMlzSUbQj3tvuZJriJ3EQ9J8lfYXFbadMGTRVCby/Kq0Mxx9ZerThTd4K/XKaikudn1H6K1bucpvjJtvxPU+jLYnZzo82bhkWzGCdCg2p4ivUe9WxVS1t+pLm+pKyXBJHtO/d6muczOZZMZt24nyT/RBs6hubIbPqSc18Ix811KTVON/yWfW9XzapudSpCnCKcpTk7Rikrtt9SV34H5seUhtxS2/6Ws2zvCTcsupSWEy+/wB4p+jGX4zvLxGM7h6FgK1XC4qliaEt2tRnGpTfVKLTXvSP1S2A2jwu1uwuS7TYae9TzLBU67duE7WmvCSZ+Um+42aPrnyFekyDw2I6NM0xCjUc54vJnKXr31q0V26byXei14zBD6685Z6Hz/5a/R5idstg8PtLlGHlXzTZ5TnUpQjeVXCS1nZc3BpSt1XO94S3o3vczitb3MZgiX5IONn1oOJ9qdPHku0c8zDEbRdHM8HgsXXbqYjKK8vN0ZzerlRnwg38h6XejR8rbYbAbZbJYmpQ2i2YzXLpQes6uGk6b7qkbwfgzKmYxvN71VIPgZ01Fu29H8o9q2S6Otttr68KOzey+aZi5O3nKeHlGku+pK0F4szmqMJiXp6u27cEeybR7F7UbN5ZleY57keNy/CZrh1iMDWrU7RrQd7WfJ213XZ2adrNH1f0E+S/gtnsdh9oOkGphc0zCjJToZVRe/hqUlqpVZP7o18lej17x9D7W5HlG1OQYrIto8uo5ll2KVqtGqufKUWtYSXFSVmjXTNULMw/Kl80EtDu3ygugPOOjytVzvJPP5tstKX9cbt62Db4RrJcuqa0fOz49KONjbTO0xlh8WXcfqf0TyX/AGW7Ir/MOA/3amflla8ZW5o/UTodrrE9EmxuJh6s8gwPPhahCL96ZqmMVMux7c3Y+Vf6IdO+zmx6/wDH4v8AmqR9SSkdReVL0Z5h0mdHdPCZH5l53lmL+F4SnUmoKvFxcalJSeibW603peNtL3J6x+ea1CV+B7bjujPpCwGNqYTE7D7SxrU5OMlHLK01fslGLi+9M8FnOT5pkeYPAZzluLy7FqEZuhiqLp1FGSum4vVXRvoqiqcMJiYfT39Dxiv2RbYy6suwq/8AqzPsCo9dD43/AKHxjYUtttqcvdt/EZTSqx15U62v8tH2NUaT4miY+9LPsYOdpeEv5LPydxzvjK37rP8AlM/V5ycasJpX3ZJ26+w+AOmfoJ242W2wzGrlezuY5vkWIxU6uBxeAoSxC83JuSjOME5Qkr2d1bqZYnZqiTjDpyNkfX/kB7W0p5PtJsbVq/bqVaGZ4aLlxhJKFRJdjSfifL+Z7F7X5flmIzLG7K57hMFh4p1sRiMuq0qcE3ZXlKKXFpHK6HNt8Z0fdIeV7TYeMqlPDVN3FUYv7tQlpUh7NV2pGdyuJjclMP06jNyZwNrMhwO1GyuabPZjphMywlTDVZWvuqS0l4NJ+BtyDH4DOMowmbZViY4rAY2jGvhq0XpOnJXT7+T7Uc5txMJ3kbn5W7fbLZtsdtbmGzWdUJUcbgKrpTutJr4s11xkrNPtPBbuh+kXTh0PbO9KuWQ+Gz+xud4aDhg8zp095xX3upH49Ps0a5M+NekXoD6TNjKtWeK2dr5ngIP0cdlaeIpSXW1Fb8fGPiWmYjdKzv4OpmSxvr0p0K8qVaEqVSLtKM04td6epzsmyTNM5rrD5RluNzGtJ7qp4TDzrSb6rRTMsxKb3i46s9gyDYvanP8AIs1zzJsix2Py7KYxeOxFGnvRoqXC/N9bsnZauyO5uiryXNsM7xVHG7bRls1lF1KVKTjLG1l1RgrqF+uXDqZ9lbF5Bk2yWQ4XItncBSy7LsKmqdKnq2360pN6yk+bfEx2pmNy7oflh5tp68OvrMo6H2v5Rfk3YPaL4TtP0e4ajgs4knUxOUxtCjjJcXKlyp1H8nSMn1M+MMwweLy7HVsDjsNWwuKoTcKtGtTcJ05LipReqZlRVCS1LrPvLyI5W6BcGv8AOmM+mmfBsddD708iSH/uFwT/AM6Yz6aZbnGEjtd5w7Tqvyvv/wCXPav5uF/3qkdpKW6dVeVzUU/J12sX4GF/3qka1ji/O6a9IxRsmvSMHY3xwYoECq9gIjyWy/8AbNlX+m0f5yJ448hsw7bTZU//ABtH+cibbP44adR/Jq9ku+sXK2Mr/ukvpMIzsa8VU/qyv+6S+k17+nE/QFFH3YfJqaN0PSOmye9gco/da/0QOsTsjpjlvYLKf3Wv9EDrho+L9aox0rd93/jD6T1ejHR9H/d/5SguWwtY89h3WRK4sZIWLhMpYqRUgZRDFSc0UkuAmB3X0fVfO7C5TJfEVWm+9Tv+k85vnpPRDj41dn8Zl0penha6rRX4E1Z+9I9w30fa+rdym90ZZmOyMd258v6TsTb1l2mf1TPfv+bDPcBLN9nsyyynHeq18PLzUeupH0or2o6FmteDR3/TrSpzU4ScXF3TXI9T2x2Fed4yrmWz0qEMXVbnXwFSap783xlSk/R14uLa1vZvgdF1v6Hu3saqzTnEYmI445uz6v8ASNGkqqtXZxTVvz2RPDf7Yxv9TqoJHksy2fz3LavmswybMcLPqq4Wcb92mvgMuyDO8wrKlgMozDEzbtanhpv2u1ku8+aREzOMb3t5vW4p2tqMc8vHKJ3J0bZPPJdkI4rELcxObzWIUHxjQhdU2/nScn3RT5nA2Q6PsNlteGP2slQr1IPep5VRqKe8+TrTjoo/gRbb5tHt2NxFbFYmpiK896c3rZJJK1kklokkkklwSR9A6qdA3YvRq71OzEcInjPreO6e6Vo1FP2ezOaeMz2TjhEc9+/PqjjvxVM3YKp/VlD91j9JwXPrNmDqf1ZQ1/bY/SfQblOKZeXqt/dl0NnH9lsZ/pFT+UziricrN3fNsY//ABFT+Uzit6n5+u/il9ctfgp9jvnZuT/YhkH+rofSznKZ4XZHEqvsbkkl8XCeb/JnJfoPJ79j7l0NEToLMx+mPg+Vaq3MX7kT+qr4y9e6XWnsZT/0+n/ImdRM7u2oyx59kOIyyFSnTry3amHdSW7HzkXom+V05K/Xa+h1dX2O2roVJU6mzebtp2vDCTqRfdKKafgz551w0t2nX7c07piMS9h1c1VqnTTbqqiJieE+yHgbaGEjyGbZTmmU1adLM8vxeBqVYecpwxFGVOUo3aulLW10zzmwGyNTaLGVMVjKksLk2DaeMxKWuvClT66kuS5LV9vkaLFy9ci1RTmqd0Q9Bd1Vqzam9XV92O165PLMxhlNPNZ4HERwFWq6NPEum1TlUSu4qXBuz4HFtofQtXGUp03hPgOHWVKkqEcvnHeoqkuEbdfPe431uejbRdHdPE1Z19mMRFp6/AMVVUakeyFR2jNfOs+89J0h1Q1uktxdpjbjG/HGJ9nbDpdH1jtXappvxscp7Pfynw9brPgLs52c5TmWU1lRzPL8VgqjvaNelKG93X4rtRwFqzys5pnZejoqprp2qZzCqcozUk2pJ3TTs0du9HOeYrOMirRx1R1cTgqkYOrJ3lOEuF+tq1rnV2WZZjc0xcMHl2FrYvET9WlQg6k34I7e2X2fnsvkn2PxM6c8xxFVVsWqclKNGytGlvLRtcXa6vpc9Z1No1M9IxNvOxidrljs8XnOstdidPFFWNvMY5+v3Y8cPM30ODtFBVNmc3g1dPA1H4qzRyIzsjxO2eOhhNksylKVpVaPmIdspP6j6h0rVTRob01cNmfg8dprdVV6iKeOY+MOnYzlCalB2kmmn28jvrA5qs2yrBZqpX+FUYyn2TS3Zr2q/idAyd22e+dFOcenU2frz0rS87hG3wqW9KH4y96R8v6p9JRpdfFuucU17vf2eT2fWLQ+n08XY40fCePyn3OxnUuuJ6v0jZHUzjIfhOGg54nL96purjKk/Xt1tWT7kz2C7WjM6OInRqxqUp7s4u6aPqXSXRtGv0tenr/NHj2S8bprtemu03bfGPrHv4OgXBxepGdmbX7GUMxqyxuQ+Zw2Im71MFOShCT66UnpH5rsup8j0DM8pzLLKnm8xwGJwkr2XnqTin3N6PwPiPSPRep6OuTbvU49fZPsl9F0XSVjWUxNE4nlPGPrnwcJBmairetD8pHkcm2fzrOqqp5XluKxTfGUKb3I9rk/RS7Wzr4pmd1O+XNru0W42q5xHreKR5POskzbJXhlmmAr4T4TSVaj5yNt+D5r9K4rmkdi7KbIYLZ6vDH5rUw+ZZpT1pYan6eHw8uUpy4VJLlFeinxcj2HEyw+Z4OtgM5ovHYWvLfmpytOM/vkJfFn28HwaaPXdH9TtZqdNVdr+7V+WJ7fby9Tzmq6xU270Rap2qI4zz/2+z18ezm6IKj2TbLZTEZFU+E4eq8Zls5Wp4hRtKD+TUj8WXufLqXrh5jUaa7prk2rtOKo7HfWNTb1FuLlucxIBzBpbQd4XEoEAAApAAKQoAgABjQACF5AEVACsBzDJwKBBzL2kYVPEdwsOsggKANgAM2CoBAoMDvAC+ofEACIvO4QsAAKwIOYYKKAQCgAB3gdgKHMgBAHaHwCABOxOYehB3b5PXT/AJv0dSo5DndOvm+yrnpQUl5/BXesqLfFc3Tej5Wbufcmxe1mzu2OQQzvZnNcPmWClbenSfpUn8mpB6wl2PwuflZex5zYza/aTY7OoZxsznGKyzGx0c6M7Rmvkzi9JLsaNNUY4M+L9TnK/AyvZHyZ0ceV5R83Swm32zk/OKylmGUtLe7ZUZaX+a13HfOyvTD0Y7UUo/YjbTKfOu16GLqfBaib5WqWTfc2Y5TD3apaT11MqL3fVbXczRSlKvTVXDxdem9VOi1Ui/GN0bYKb0dOou+D+oyRsqVG1ZtvxNcUk9LGUoVHpGlVb7Kcn+g42Nr0sBRdfH16GCoxV5VMVVjRil13m1oBy1HTQwmpbyUU23wS4nU+2/lE9FuycalN599nMZG9sNlMPPa9TqO0Eu1XPl7pm8pLbHbzDVspyuMdm8jq3jPD4Wo3Xrx6qtXi1+DGy7CZ37mWHZHlZ9PeHngsZ0fbF42Fd1L0s4zGjO8N3nh6Ulxv8aS05Lmz5HlK+pi9WrcFwKkZU5gOJysox+NyrM8NmWXYqrhcZhasatCtTlaVOcXdNHGQ4FmmJ4pl+iXk39NGVdKGTwwONnRwe1mGp/1Xg07LEpLWtRXNPjKK1i78jtybUeZ+TOW5hjMsx9DH4DFV8LisPNVKNejNwnTkuDi1qmfUHRT5WmMw1GjlnSPl1XMIxSis1wEYqvbrqU3aM32pxfXc1zulcPsKUr6GEnNw3N5uPyXqvYz1DYjpN2A2vownkG12VYmpPhh6tdUK6fV5upZ37rnu06NTc3vNVGmrpqLa9q0LmEw4tDC4GNTzjy/Ab/y/gtPe9tjl1aspU9xStD5K0XsRxW579lTqN9kH9RlWfmKDrYn+p6UdXOs1TivGVkMRAyhG3BFqQlJKMU23wSOsduenvou2PjUhidpKWa4yF/6kylfCJt9s/UXtZ8q9NflIbXbeYavk+VRWzuQVrxnhsPUvXxEeqrV4tPnGNkM5ncYe/eVZ09UZ0cZsFsNj41adSMqGb5lQkpRnF6SoUpc0/jTXHgtLt/KO85cTW3vPuMkZURMb5JbItJo+/PIv21wm0PQ1hshlVX2R2dnLC1oN6ujOUp0Z92sod8O0+ALntHRft7n/AEd7XUNodn60VVjF06+Hq3dLE0W/SpzXNOy14ppNaotwpfp/vXZVrxOpOi3p/wCjjbTDUoVc3pbP5pJJVMDmlRQW9+BW9Wa7919h27hpU8XQVfBzhiqTV1OhUjUi/GLZhmDDCq6u7aNWql1KbPgny03JdPeYOTbf2OwOr/cIn3liq9PBpyxk6eGilfer1I01brvJo+BPLKzHLsz6b8fictx+ExtH4BhIedw1eNWG9Gkk1vRbV0yxOJgh4nyZducPsL0xZNm2Pqqjl2Ic8DjZt6QpVUlvPsjJQk+xM/RZybk1e9ua59p+Srve59V+Tn5TGFyXKMJsn0i/CamGwsVSweb04upKlTWihWjxlGK4SWqStZiqZzMmNz7A4LUxdWcHenOUX1xdjw2Q7WbM7S4aOI2d2gyrNYTV0sNi4Sn4wbUl4o8zTw2Jerw1Zd8GIwjqryr61Wp5Pu12/VqS+04fjJv9vifnZP7pc/QjytMwy7DdBe1OBrZhgqeLr0aCpYeWKp+dm1Wi3aG9vPTsPz5kvSuSIzUyidz6J8k3p3p7C1Y7H7WVZvZvEVXLD4nVvL6knq2udKT1aXB6o+34YihiMPSxOHrUq9CtBVKVWlNThUg+EoyWjT60fkq3bgdpdDPTptj0a7uAwtWGaZDv708sxkm4RfN0pLWm+7R80xVEU74OL9GYNXMpTnHWEpRfWnY6b6OvKM6L9qoUqWIzeez2Pmknhs1W7De6o1o+i12tRO4MHiMNmWEWKyzEUMfh5erVwlWNaD8YNkzEphxa2Fw1eopYnBYOu1zq4aE37WjlUZKhBQw8KdCPVSpxgvcjVJVVOzo1l+9y+o5FKjVkr+Yrd7ptL3mU4Gt2k9eJrn6GvBHrO2nSJsNsfTlPaHavKcDKP7Qq6rVn2KnTu/bY+culbytnVo1cv6OMrqUJtW+yuYwW/Htp0dUn2yb7kTagw7n6fumDJejHZ5wbo43aTE075flzd7dVasuMaa5LjK1lpdn59bTZ3mm0efYzPM7xtXHZjjarq4ivU4zk/ckkkklokkjj5xmuZZzmeIzPNsdiMdjcTNzrYivUc51JPm2ziozop7ZJ3KnZn3z5ET/9wWDf+c8Z9NM+BktT7a8j/a7ZfJuhDCYDNdpsly/FxzLFydHF46nSmovzdnut3s7PUV8Ujg+iKklc6p8rJ/8A8O+1nzcL/vVI9sjt9sNJ67cbL/8AmtL6zrXypdsdksx6BdpsvyzarIcdjKywvm6GGzCnUqTtiacnaKd3ZJvwJmCHwrVfpGssneVyG2JygwAwB5HZpf8AtJlf+m0f5yJ4452ztSNPaDLpznGEI4ui5Sk7JJTV231G2zjbjLVf/lVeyXduNlu43EfusvpNSqGjH4/L5YutKGZ5e06kmn8Kh195x/huCX98cB+dQ+s+9Ua3TRER6SO+HzOizVNMbnq/S608FlX7rW+iB17Y966UsVhq+Ey2NDFYetKNSq5KlVU926ja9j0ZHx/rTXRc6Vu1UTmN3/jD3vQdM06KiJ9fxlEilRTz7tUsVAFTIQoChiyk5EHltj87lkWd0sY4udCSdPEQXxqb4+K4o7kjKE6NOtRqxrUKsFOlUi9JxfP61yZ0Gz2PZDa3GZFfDVKfwvL5yvKhKVnB/Kg+T9z5nq+rHWKOjK5s3/5dXhPk6HproidXi9a/FHZzjzdryqWJ5y+h4vLs6ynNIKWAx9OU3xo1WqdSPg9H4M5yVSL9KlNfiux9Z0+rsainbs1RVE8peNrs1W52a4xPr3PIUMxzCglGhmGKpxXCMarSRMVjsfiYuOIx2KqxfFSqto4a33wjJ9yYq1IYan53F1KeGp/KrTUF79X4GdVNmj71URHravRxndG9tg1BWWiNFTOMLDPsLkcGp4mtTqVauv3KMYOSv2u3Dq70epbT7c4ahCeHyP7fW4fCZxtCHzU+L7X7D1vo/wAWltnRxWNxUYudOu51a1S15OnLi31s8d0j1rsRqrWl0tWc1U7VXZEZjMRz9vCHdWOhLk2K792MYpmYjtmcfX1x7XnPUuFn/VlD91j9J437I4F/3xwP5zD6zdhMdl/wuhKWZYBRjUi2/hMdFfvPV1azT1Uz9+O+HVVWKopndLpvNH/3pi+2vP8AlM40uByM13XmeKcZJxdabTT0fpM4zPgFyfvS+nW4+5Hsdo9FOOjitn6uWuS89gqjqRj10521XdK9/nI9rlJo6QyPNsZk2Z0swwNRQrU29Grxkno4yXNNaNHaWUbXZLnFGO9Whl+LfrUK8rRv+BPg12Oz7z6d1T6xaedPTpL9UU1U7ozwmOz3vGdNdFXKL1V6inNNW/d2T2+efqfM1JXM8NjMTh9KOJrU11RqNI4+5VmlKEHOL4ODUk/FB0qq9anKPzlb6T3E1UVxnjDodmnGJejdMGLq182y2pVqTqS+BNXlK7+6z6zz+xm0+BzTKMDkdKlSwNXBw3YYaL9GtJ+tUTfGcuaevV1HqnSpKEs0wEYVKc3DCtS3JqW69+Ts7d56fBuElKLaad009UfINX0rV0b07dv26YqjPD1YjhPY9pY6No1nR1uiqcTGZj25nsd8yutGrWMW+s6+yDb3E0YRw+c0ZY2klZV4O1aK7eUvHXtPccDneS5lFfAcyoub/aqz81NeD09jPovR3WTQa+mNmvZq5Tun9/c83qejNTpZ+/Tu5xvj9vfh5mhmWOo0vM08VPzX3udpw/Jd0aatXD1Knna2T5HVqL488sot/wAk0yp1lG/mptdaV17UY+lf1Zfks7K5o9JqPvXLdNXtiJcKidn8M49jnfZbHeZeHp1IYahLjSw1ONGD8IpXNCmuBqjSrSV40ptde67Hj8xzjKstUnjsxo05L9rpvzlR+C/S0K72l0FvNUxRT7ohaLU3KsURmfU8q96TUYK8nwSOsekTPaeY4uGX4SoqmGwrblOPCpU4NrsXBGW0+2uIzCjPBZbTlhMJP0Zycr1aq6m1wXYvE9SXE+bdaOtFGuonS6T8HbPP1R6vi9b0N0PVYq9Nfjf2Ry9c/X7NTKjUnSqxqU5uE4NSjKLs01waMWQ8LmYelxni7c2S2np7QUI0K7jDNYL7ZDh8I/Dj+F1x8UeZlKyOjKc505xqQlKE4u8ZRdmn1pnu+R7e1VGNHPKMsSlp8KpWVX8ZcJd/HtPpPQHXOiKKbGvnExwq8/N5DpDoCqiqa9NGY5cvZ6vV8Xu9V3WpvwOY47CR3cPjKsKfyN68fY9DxeDzPLMxjvYDMKFZv4kpbk1+LL9DZyfN1o6ulUS6912Pe0XtNqqdqiYqpn3w6Gu1j7lyPdMfKXlnnOOfpb2G3vlLC07+3dONisfjsX6OJxlerH5Ln6PsWhxFvvhCT/FZsVKqo784OEflT9Fe1lixp7c5ppiPdDTsUU9kKrJaaGN25WXG1+5dfceLzTaLJcti/PY+Feqv2rDenL2+qveeibR7WY7NoSw1KKwmDfGlB6z+fLi+7gdF0t1p0OgpmKatuvlHzns+Pqdpo+itRqpzEYp5z8uf1veW222qjVpVcpyurvUZrdxNePCor+pH8G61fO3Vx9KuYoyPknSHSN7pC/N+9O+e6I5Q9ppNHb0lv0dv+qgiKcFycA5BBBFRAUohSABwYAKADBADDAU4kAIDA5AKAMXABgMARlIBAAQbSgGxgBhAAAOYEKLgABoNAIXmQoBscg+IKCYBQAYsUqJ9AHIBUFwORBLgABxYYBCGLRGZNEZjMMmKbT0K5N+tZ94aJYxmFiXkcuzzN8tmqmW5pj8FNab1DEzh9DPZcJ0t9J2Epqnh9v8AaWnBKyUcxqfWek2IY7MK9xzDpS6R8wpunjNuto68HxUswqWfvPWsbmmOx0nPG43FYqcuMq1eU2/azhoK5NmBVJ2stF2EWrKkVIziDIkULgUywwyELbmQYGIT1uZEaMcMssozcZKXxlwfM89ku2u1uSrdyjabOcBFcFQxtSKXhc9fIybMGXulfpX6S60HTqbe7SSg1qnmNTX3nruZZ9nOZy3szzbMMbJ86+JnP6WeNQ5k2YXK7zXqq3cTiWwtqZRCZEjKwRTNijMXqZsxZJEv1695zKGZYyhG1HF4mkuqnWlFe5nDa1IYzTEssuViMdisRfz2JxFW/wAutKX0s467iIoppiOBMq7E3mvV0KR9ZlLGGVGtOlPfpylCXXCTi/cc9Z7mypeZ+ymY+b+R8LqbvsueMsDXsQyy216s689+pKc5fKnJyfvMeViIyRnTGEmWLMWjNkEwRKbzta911HNyzOMzyypGpluYYzBVI8JYevKm17GcJk5GM0wuXt9PpO6RKcVGG3O0cYrglmNT6zgZntptbml1me02dYxPRqtjqkk/eev8hcbMGWdWo5zc225Pi27t+Jg9Xd8RxKkWIJlSxTCKZ4Y5DB6vgjNmD4mNUZ3LSKy+KvYNHyS8AVEimFyqKEDNgAAoGMuBkYskrDBWvwXsLp1L2CwsY4ZLFLqMrERkWIYyIWAMheREUBEAYAGLMiBWJHoZcCMwmGUSlzl4XNczwithswxVFdUKrSOJzDLRcqonNM4n1bmNVFNcYqjLys9pM+qQ3J5xjnHq88zxletVrS3q1WdSXXOTf0mAM7l+7d/mVTPtmZY27Nu3+CmI9kG8Y2MuYsaZ38W1EteBlp1L2ECRY3DMjKiMyYsGL6WMmiWMJZM6dWpBWhUnHum0ZTxNaa9OtVk+2bZqCMoqmIxlNmOOFbIyoMiwi6w3fiVohBvoY3F4d3w+Lr0vmVGjlx2gzyKss3xqX7szxnAG2jUXrcYoqmPZMtdVi3Xvqpifc5mIzPMMR/XGOxNX51Vs4jd23zIxYwruVXJzVOWVNFNEYpjBzKLaltqYwyQcygoxBQY4BPW5ysPmOYYdWw+OxNJdUarRxFoEZUV1UTmmcSxqopqjFUZeT/ZBnjjb7LY23V55nDxGLxWIk5V8RWqt/Lm2aAbK9RduRiuqZ9szLGmzbonNNMR7lu/AWIimpsVLQAFRUACgikRQgUgKgUAogKQAwAQAABAGCKDkBcKAABwALyAgHcQABYAbUAO8zYHPQchzHMAgx4DQAGECgAisCDXiLFGAAKVEAAAouABCvgQCAoAgRQiCMhkTmFQNFFmQYtEsZDQmFywsLcjOxLEwZY2FjOwSGDLFItjIWLgylgUIuEQWFgMCEsZEJhUsSxkLEwZY2FjKwsMGUSLYthbUuDIkVBFLhMoRoyJzGBjYjRmQmFyxsVIqQGEyciW0MtSFwMbCxkCYXKWKBYuECWKLDAxYZlYEwuWKRLGVgkMGUSKipCxcJkRWEAIRoyIMDEWMrAmFyFAKgAQoEaMiMhDFrUljKwsTC5EUAqAHAqAW0HIpCgyFZCAAAIRmQZFYBoyYJMLlgLGQJgylgUDBlAigYMgKC4RiGigmFyxsDKwGDKJAoGDKWJYyFhgyxsSxlYEwuWNgjIDBlCjloUuEyhDINAyxFi8ARWK0GpbCxFylhbQyQQTKJBFCLAhQCoWLzACAAKBUCAUAgAFBRAAQACgQcwCKMnMoAjQLyFgIBwY5AOAfEAKAADYA+ReRsYJYMFAhOZQALoAA5hApUSwRWQCi3UOdgA8AOQ5gOI4BdZeIRGQoAgWpQA4EKggqBFHDQCdxDInIGQltSgggsXQAS1grltqBgABYAClScmlFNt8EkBjYh5uOy+0cnZbO503a9lgKv6p4zG4TE4SvKhisPWw9WOrp1abhJeDSZNqJXEuOQpUVGIOXluXY7MsSsLl+CxWMrtOSpYajKpNpcXaKbNNWlUo1p0qtOdOcJOMoSjaUWnZpp8GmTtwrXZEsZW1M6VGrWqwpUac6lSclGMIRcpSb4JJcWVGtFseZ/YrtMpbv7Gs7uuK+x9X9U8bicLXw1edDEUatCrB+lTqwcJR709USJiVmJhoRXqGFxMkGiWPIZTk+aZtiHh8ry3GY+quMMNQlUa77LQyznI84yapGlm2VY7L5y9WOKw8qd+661MdqM4XEvGkMmLXMuKZYpaFsc7KMmzbOK8qGU5Xjswqx1lDC0JVHFdbstPEmbZXmWUYj4NmmXYvAVnwp4mjKnJ9yktTDajOFxPFwgZE7DNGNil5EQwIyGQZMDEpQMKnAcimcKc5yjGEXKUnaKSu23yS5sI12FjzOL2W2lwmA+H4rZ3OKGEtfz9TBVIwt13aPD8uskTErMYQFsbKGHrYitChQpVKtWo7QhCLlKT7EtWWdyNQtyPN5lsltPluCWNzDZzOMJhrb3nq2CqQgl1ttaeJ4bgSmqJWYwhGVs5eCyzMMbhsRicJgMZiKGGjvV6tGhKcKS43m0rRWj49RZRw0gkZyju8TdjcDjcHChPF4LFYaOIh5yjKtRlBVI6elFtektVqhOI4kb3HHAcjyWWZDnWZYfz+X5NmeMpbzj5zD4SdSN1xV4q19RmIXDxttL3JyPOy2S2njpLZrPI9+X1f1TxGLw2IwmJqYbE0KtCtTlu1KdSDjKLXJp6piJiTGGnkLEvY8xS2b2hqwhOns/nE4zSlFxwNVqSfBrTUk1RBiXhxY5uYZbjsuqqljsFisJUkrqGIoypya7FJI4jRY3xkRIEvqeQyfJ81ziq6WV5ZjsfUTs44bDyqNd+6tDHaiDDgFsc/NcnzPKMT8GzTL8Zga9tKeJoypyfcpLXwODJGUb4yieJCtHIxmBxuEp0KmLwWKw8K8N+jKrRlBVI9cW1qu1DKuMOZXaxiwgGcvLMux+ZVpUcvwOLxlSMd6UMPQlUkl1tRT0PIfsV2ka02dzp/7BV/VMZqiGURLwgN2MwuIweKqYXF4eth69N2nSq03CcX1NPVGllQIUEwILaFAEBQFygLpc5GBwGOxyrPB4LFYpUKbq1XRoyn5uC4ylZaLtZJ3DjIoXAplxTKEsbaNGrXrQo0ac6tSpJQhCEXKUpN2SSXFt8i5hhcVgMXPCY3C18LiIW36Vem4TjdXV4vVaNMxmYhWmwaORhcFjMTh6+Iw+ExNajh4qVapTpSlGknwcmlaK7zQInIxBd3sPIZRkubZvWdLKsrx2PqRdpRwtCVRrvstBM44kPH2Bzc1yvMspxPwbM8vxeBr2v5vE0ZU5Nd0lqcNoRv3k7kBtwmGxGLxFPDYWhVr1qj3YU6UHOcn1JLVnlY7KbSybS2bzp242wFXT+KSZiFeEFjficNWw1edDEUqlGrB2nTqRcZRfU09UaXwL2Ilihas87lOyW02a4N4zLdnc3xmHSu6tDBznC3elr4E2oXEvAsM3YrD1sNXnQr0qlKrTdp06kXGUX1NPVGovFEAAAhQRcoEUAyheIAMlgChEHMC2pQALyAgKCiAoIAAAgLxIAKiAAwAACA7woACBzIxzKwJzADCgAA2AA2MFJzKTwAagpCigAABoOYRQCAVWsHwILAXkAu4cQii47B2FAj1KyaALAFAgCKBAABEOwF5gQvMAgligIoC2oHAggKx4lFXWcnLbxzDDyWlq1N/x4nHRysr1zDDrrrU1/HiY1RuWOL6a8p/pg6R9kOlieTbO7V4zL8BDLMHVjQhCEoqUqScn6Sb1Zs6NtqKPlD7O5xsFt/hcJW2ow2CnjckzqjQVOu5QWsJ248r8mn1o9O8snJc3x3TdXq4HKsfiqf2KwMVKhhpzV1SWl0rHnvJl2YzLoywOc9MW2+HrZHl2Cy2rh8to4qO5VxlaorJRg9WtEl169Rxsxhs7Xzdi8PUoVp0qsdypCThOPVJNpr2pnHd07HLzDF1MbjK2KrW87XqzqztylKTk/pNmT5Xi83zXCZZgKUquLxleFChCKu5Tk7L6Tkz+DLXHHDv7ydszodFHRPn/S/jMPCrjsbiqWUZPRm9aiUlKtJc7WXL5LPXfKz2ZwWX9IdPa3JUpZFtbho5tgpq1t+SXnY6c7tS/GZ2R0xroXy/BZH0Y7SbR7UYX9iWHVCdHKcHGpSlXnFSqTlJ8ZXb7kxtDh9hOkPybMbs1sBmebZnjthrZhh45lh1TruhJy85CKXrRs342ONTXirLZMZjD5OlxPP9G9SUekDZ2UW01muGaa4r7ZE8BZ3uuD4HsXRtC/SBs7/rXDfzkTfXmacsKd0u+fKL6Zuk/Zfpu2nyPI9r8Zg8uwmJpqhh404SjBOjCTSur2u2crIM5peUb0dbR5NtJluBjt7s/gnmOWZrhqKpyxVNO0qdRLre7F8vSTVrHoXlW5Tm+L8oXa6thMpzDE054mluzpYWc4u1CmtGlZ8D3noKyDMeh3o62q6VdsqUsqr5hlcstyTAV1u18TObUlLceqTaj4bzNf3dmMcWW98vN8HyaueY2LyPE7T7XZPs7g5KFfM8bSwlOTV1Fzko7z7r38DxM4WUV8lWPYejXaBbJ7f7P7SypSqwyvMaOKnBcZQjNOSXba5snawx3Zd99MvSjieizNanRb0SU6GQZfk8IUcdmFOjGeKxeI3VKUnN9V1d823wSRp6HemDH9IOdUOjHpbVHaTJM+n8EoYqvSjHE4TES+5yjNLnJJJ8U2nwujwnlTbBZnLbSv0h7M0Kud7K7S7uOw2OwcHVjTlKK3oT3buOqur9bXFM4nkxdGme5nt/lu2ec4OtlGy+z2IjmOMzHGRdGm3Re9GMXLj6SV+pX7DCdjY9a/ey6r6R9mMRsbt7ney+IqOrLLMZOhGo1Z1IJ3hO3bFxfieCglvWZ7f0zbT0dtOlLaPafDRksNj8bKeHurN0opQg2utxin4npzVteSMoiYp3pPF9e7m3OUeTVsPi+gzDVVSxVGVXaPE5ZThUxrxSSupX1spb67Eo8jpjpF6UukbPNj6uxO3MFi6qxMK8MVmWB3MbSjG94Rk0vRbs211W5ly3LOmfoqwGW57lVPPsnweb4eOJo18DLz1GpGS0VSKUoqdraNXs0dy4jOM86SvJj2vzrpayOjSrZNCEsjzqrhfg9erVvbcS4+s1FtaS33ppc1xiJZvkXgyGVTj9Jj3HJag2UaVWvWp0KMJVKtSShCEVrKTdkl4ms5mS46eV5vhMxhBVJYatGqov41nqvYWmKZqjancxuTVFMzTGZ7HumK6Ks5pZY68MwwNbGxjvPBwUr34uKnwcuw6/aabTTTTs0+KZ3lU6Rtl44J5hSxVWdeK344R0mqjnyi3wSvz6jpCvUlWxFWvNJSqTlNpcLt3O66Y0uhsbH2SvazG/fn2Oi6D1fSGo2/tlOMYxux7Y9ccN/jLWC2uDpMPQLFXaSPpDos+wnRH0Ex6WMVleGzLazPMVLB5FHFQ36eFgr3qW69HJ8+C01PnCL3Wna59I7P5Riel7yV8v2a2clDEbTbF46pWeWxaVTE4ed7Sgnxdn7VY1XOzkypen5f5R3Szhc9+yOM2nqZph5z+3YDFUKbw1SDesdy2mnsOuekLP8HtPtlmWeYDIsHkWGxdXfhgcL9zpaa+Ld27WV2crKdidrc1z2lkWB2azapmFSp5rzDwk4uMr29JtWSXNmjpA2PzrYjarGbN5/Ro0swwjj5xUaqqQakrpqS//ALobNOfupmcb3gIW5vRan05leJwPQJ0KbP7SZbl+ExO3+11OVejjMVSVRZfhlZ+gnpezj3uWuiPmNRtdPmrH05tfk2O6ZvJ32Pz7ZKDzDOtkMPPLs0yyjZ1lTtFKcY8XpCMu1XtwJXxiKuC08Nz07ZnykOlDK85WLzfPHtDl83bFZdj6MJUqtO/pRVl6Ltw4mjyqtjNn8i2hyTanZDDrC7P7V5esww2HXChU0c4LqXpJ25O/I9K2T2B2w2o2goZDlGz2ZVcbWnuNTw04RpLg5TlJJRS4s7O8r3MsowOJ2R6OMoxtPHR2Ryv4Ni61N3i8RNR3o360o3a5b1uIqinajZImcb3z++o+ovIr2jwey+wHSjnmZ4SeNwGCoYSticNFr7bTtVjKNno7pvRnzA1qd7+T7C/QJ01/6sw30VWS7GFo3vV/KG6P8HsfnuGzfZussbsfn9F43JMVB3iqb1dFv5UL211t2pntXlRxqS2G6HZTnKV9kqdru9rRpHG6ANpsl2h2fxnQvt5WUMkzirv5LjpvXK8e/Vab4Qm33XuuEmef8snK8TkGS9F+R410/hWW7PTwdbzb9HfpulBtdjcTHtiJOzc+cYpJnvGxnS30hbFZDLJtl9psTlmAdWdfzNOnBrfkld3avyR6NJmLV4y7mbrmNlhTxfV/lF9MXSNs1+wZZHtTisH9kdlsLjcW4wg/O1pX3pu60b7D5h2izfMc/wA6xmdZvipYrH42q62IrSSTnN8Xpodx+VtBL/s2ty2Mwf6TotmNqIiMsqljC6n81/QfWHlIdJO3ewdPYLL9mNq8XlWHr7LYarVpQ3GpTTcb+knySXgfKVNrdn8yX0H2H5QPSvmGw+G2FwOA2f2ZzOGK2bw+IlUzTAqvOLXo7sW+EdL99zXdiM7lp4PWdktqs36XegTpDh0jyp5hHIMHDG5VnVWhGFSlX9L7UppJO7UVb/Ka8j5ck7pdquz6b8o7aXO9s+hrIdqtk8XRwmw+JlHD5xk+DoQpLBY+PKrupb0G/VvzSfNHzKoW4ltxMlUvZeizZOvtv0g5JstQqeaeZYuNGU1xhCzlOS7VCMmu2x3d0y9MGYbCbQYno36JvM7MZHkc/glbEYWjH4Ri60dJylNpu17q/F8TqToO2rw2xPSrs5tPi472GwGNUq/ZSnGVOcvCM2/A948pvo02gynpDzPanLMDXzbZrP8AESzDAZhgqbrU2qvpOEnG9mm+fFWZcRFX3jM43PbuhrpGrdLuO/7KulaVPOqGbU5wyvNJ0orF4PEqLlF76WqdnbtVndM+edrMnxWz+0mZ5Fjf65y7FVMLV7ZQla/6TufyWNh81y7bOl0mbUYevkmy2zKnjK2MxlN0lWmoNRhBSs5P0uXYuZ07t7n89p9tc82hlBweZ4+tilF8lOTa9xaKsVbuCTG7e8JJ+jL5r+g738qBzfR50Qb0pP8A9nHxd+aOiYrejL5r+g+iPKNyfM8z6OuiOWW5bjcbubO2n8Hw8qm7e1r7q0JXH3lp4PnS2moPK5hkWdYCg6+PybMsJRUlF1K+FnTjd8Fdq1zxslZ2N2GGX0F5D1XF0NrNsamAqVYYmOy+IlSdL1t9STjbtukeP/7T/KTcacY5jtg5OK/vZxdvmHP8iWviMLtRtnisHUnTxVHZfEVKMoespp3i123seAp9LvlDtQlDaHayWiemX3/+3qcafxNkcHV21mdZ3tBtHj832ixVbFZpiat8VVqxUZucUo6pcGlFLwPF8jyGerMXm2Kq5vTxFPMKtWVXELEU3Co5ze824vhdu/icBm+KcQwygQYKPaNj9i8dtFhqmLWKo4LCRk4Rq1YuTnJcUkuS5s8ZtVkGN2dzL4FjHTmpR36VWn6tSN7XXV3HuPRvtdlWX5LHKc1rPCujOUqVRwcoTjLWztwaZ4bpM2hwef5ph1l6lLC4Wk4RqTjuuo27t25LRWPQX9H0dT0bRet3M3ZxmM98Y7Mc3Q2NVr6ukarVdH93v347Oyc9szy8nqPeLlZDz0u+LHf/AJH8JvCdJ9pSjfY3E8HbmdArQ+hfI59Oh0l04pynPZHERjFK7bbsklzZhcjcsPnyUd1LuRi3Y8zPZzaJpW2ezjgv7hqfUeHq06lOtOjVpzp1IScZwnG0otcU0+DMsx2JiXsnRY2+krZVRbTed4KzXFfb4HvXlkRcvKS2qcpOWuG1bvp8GpHXOw2YUMn2yyPN8Sn5jAZlh8VVtx3adWMn7kzunyydjs/n0u4vbDA5bisfkWe4bDV8HjcLSlVpStRhBxbjez9G/c0a90VZqZdm5w/J/UodBfTU4ykl9icJdJ/h1PrZ0OpcO4+jej/JMy2E8mHpGz7ajB1cthtLSw2AyqhiYuFXESUneSi9bek3+Kz5zcde4U76pmDs3vaeinZaptv0hZHsrSqOl9ksXGjOouMKdnKcl2qMZW7bHdPTL0zZlsRtDiej3okdHZjIcjqfA518LRg6+LrQ0nOU2ndXv38TqToJ2pwuxXSzs3tNjot4TA4xSxFuMac4yhKXbZS3vA9x8pTo0z/JOkHNNoMuy/E5ls1nWJljsvzLCQdalOFX0t1uN7NXt2kz977y9m5wtpOnTaLavo8x+y22uXZbtHiZ7rwGaYmio4nBNN7zTj6za0XDne51Gnc93wnRZtzidhc020nklXB5Ll0YupWxj8w6t3b7XGVnNrnbrPR0mkWnGdyS7Q8l7/4+bFO9v+9Y8PmSPcelTpy6Vcj6TtpMsy7bnG0cHg80rUaFFxpyUIRlotVdpHpnktyt0/bFN8PspH+RI7mzXp7oZP025hk20uyOylfIsLnFXC4nFQyyHwmNNSt51yad2uL69TXVvqZRweq+Upi1tX0PdH3SHnmWUcBtVmcq2Gxk4U1TeLpQWlVx79U+2x87cWd2eWLhtrafSh5zP83+ymVYjDqvkFalBQofBJaqMIrRNc+vRnSkdDZbyxqdy+S5shs9mmdZ7thtjh/hOQbJ4B4+th5K8K9X4kJLmtL256E2j8pLpTzLOZYrK9oJ5DgIS/qXL8BRhClShf0YtW9LTieT8lDNMrzJ7X9GmbY+GAjtflbw2DrTaUViYp7sW+1cOu1jrLa3o8202Zz6tk2b7N5pSxlKe4tzCznGrrZShJK0k+K7zHEZ3q7vzvMMD08dB+f7T5pl2Fw+3uyEI1q2LwtJQWYYZ8d+K4uyfc1poz5nkrS6+0+ktl8kxvQ75PG12bbWQllue7Y0I5flOXVHat5rXeqSjyVnfs06z5tqWb00S0RlRx3JLEAGxEBQQQFAEKABAUAAxcAOQQBQHIMACAAUnEoIBBzKUQAEAAEACwsFCMosBO8LiGFxCgAYAEAG1gWIbGCsagpUQcykAoAAF8CDsAAFQMoUMFQ5Ach3AVAId4CxCgonMpO4oEReQtqUCE5mQAxBeIsBCjuAwCIUMByACAcSF5gAbKFWpQrQrUpbs6clOL6mndP2o18wSYHbS8ovpkUUltxikkrJLDUv1T0bbXbbazbLFRxG0+0OY5tODvBYireEH1xirRXsPXQY+jpXakWjPM7I7Q5rstn+Fz3JMTHDZhhG5UKzpRnuNq10paX7eR4cpnjdhMuXneZY7N80xWZ5jiqmJxmLrSr160/WnOTu2zymwO2m0uw2b1s02ZzKWBxNahLD1nuKcalOXGMoy0aukz18GE0RKxVLZVqSrVp1Z7qlOTk92Nkru+i5I35VjsTluY4bMMFVdLE4WrGtRqJX3Zxd09e04ltAjOMYwxds1fKM6Zm3bbnFK/JYakv/AEnoO1m12021uYfDtps9x+bYhX3ZYmq5KF+O7HhHwR4UhhFuInLLayybvwCfMxsy8jNi9z6PulPb3YKMqWy20mMwGGnLfnhtKlCT69yWievKxs6QelrpC27w7wu0u0+MxeC3t74JC1KjftjHj43PRxY1+jjOWW0t7iQWgNnqYvedg+lzpF2Iwqwezm1WOwmDWqw02qtJd0ZX3fCxp6ROlLbzb6FOltVtHisfh6U9+nhrKnRjLr3I6N9ruemcyczD0cROWW1I9SFFjNiDgByAt3a1yd5bAoxBScyKqPKbOZ9nGzma0s2yLM8XluPpepXw1Rwml1dq7HdHiykxkdtY3yjOmHF5UsuqbZ4iEN3dlVpYenCtJds0uPajqrF4mticRUxGIq1K1arJzqVKk3KU5Pi23q32s1dxOJKaYp4EzkbueY2R2p2i2SzRZns1nONyrGW3XVw1Tdcl1SXCS7GjwxRVTFXFYnDtnPfKI6Xc4yn7G4nbHEUqLhuVJYWjCjUmu2a19ljqitUnVqOpOUpTk25OTu23xbfNmPcQkURBM5WLvxPYdndsdosgyDO8hyjMpYXL88pRpZjRVOMvPxSaSu1daSlw6z16wRlMRMb0jczU3HVNp9h7Ft1t7tZtust/ZRnNXM3ltB4fCyqwipRg7Xu1rJvdWr1PWuViEmIkicFrlVku/iTmOZcQPP7Y7YbQ7WvLpZ/mMsa8twccFhL04x81RjwhotbdbPX7FYRIjG5clvRfarHnts9sdo9sKmXT2izOWOll2Ejg8I3TjDzdJO6jotfE8CFx1JNMTJEvYtmts9pNnsjzfJMqzSdDLc4pebx+FlTjUp10k0m1JO0lf1lrw6kevO3LkQnIsREcDiXaeh2B0ddMPSLsHho4PZzabFYfAxbawlZKtRV+qMvV8Gjr8vExmmJ4rE4e59I/Sjt3t+1DajaPFY7DRlvRwqtToRfXuR0b7Xc9KSMgxFERwTOSPfx4namSdPvStkuS4PKMt2urYfBYOjGhQprDU3uQirJXau9DqtAs0xPEzMPf9uemLpF21yOWSbS7TVswy+VWFV0ZUYQW9H1XeKuegSeoI9REbMYhZnL2bo8252n2DzSvmeyuazy3F16PmKlSNOM96F72tJdaPd35RvTL/wD7xifzal9R1GtNQ3oSaKZ4mZh5jbHabOtrdosVn+0GNljcxxW756s4qLlurdWi00SR4YBliMCWAsLAO4pAEUltSgCHs/R/t7tXsFjsTjtk83qZZiMVRVGtOFOM3KClvJeknbVHrL4EJMZhYl2t/TFdMzd/2eY3+ApfqnWWa4/F5rm2MzXMK8q+MxteeIxFWSs6lScnKUtOtts4yDMaaIhZnLJS3eB2FsH009JOxWXwy3INqcXQwFP7nhasY1qUPmqXqrsTsdd6kMqoirikTh7Pt/t9tft3j6eM2rz7F5pUpXVGNRpU6V+O7BaLv4nrFyshIpiOBllGTTuuJ75sB0wdImwuEeB2b2nxeEwTd1haiVWlF/gxl6vhZHoIfEVUxPFYnD3HpD6TNuNvpU1tXtFi8xo0nvU6DahRg+vcjo32u56e9SAUxjgkzl5LZfPM02az/BZ7kuKeEzDBVfO4esoqW5KzV7PR8Wa88zbMM6zrGZxmeIeIxuMrSr4iq4pb85O7dlocErJsxnK5ez5xt3tPnGxuW7JZpmjxeU5ZLewVKrSi5UOyM7b1uw9YerBDKIiITiypSlTnGpCTjKLumnZp9afWdpZD5QXS5k2V/Y7C7a46dGMNyDxMIVpwXZKSv7bnVYMZoiViXmNrdqNoNq81lmm0ecYzNMZJbvncTU3ml1JcIrsSPDkKWIiOAneALAUhQBAgwAsCjkEQch3lAgACgQQAAACFsAAAAABgCAAgAAAAGRQDmAHMhRewEDKtR1hUtfkAANgANrBQChEAAAAgFAKioJEKOYEYK+BEBUUltCooqAQKiWBQMCMLUWLwQwFgCgQWKkLDCJwBQXAlhYoGBAWwsMCWFi2KMDG2gKBgYgy4E5XJhWLBQMAhoLFsMCDkVgYEYKLDAcycypWDGAAZUtRgQhk0SwwCHItusltRgAWwsXAiQsWxRhEsEi+AYwMRyK1fgBhU5EMgkQY2KVEfUMCahcSiwwIGZWIxgSxDK2o5kwZR8Aii2gEAsHwAlgUBUHYUhA4ACwDSxNOothqAAAACwAELoO4giLzCKwJbUnaUdYEQKOYVGAwBC8hYAEAwAZCgCIpEVgQhkyEEBQ0FQrFgMCFKyAQoAEBeZCABzAAABQAAAAAAAQAIFXmRlBBAigCApABQAgQpAqgg8QAYAAEAAAEAAAAAAABFOBLgcAAACtgANrBeJSBFQA0GowgOZSACjmAFhYoKIwXgABSIqfIqKhqAVACwLgLaAMALDQBAEAUuEQth2gGQDtAwZAUWGBAyiwwZQFfEWLgyliWMrAYMsUhYysLEwZYopQMGWI4mQsMGUSCRbCwwZQhlYgwZS3MpbAYMsWDKwsMGUFi21FhgylhYqRRgynYLFIDIBYDBlAUDAgKgxgYlARMGUtYd5WAoLAoTLGwsWwJhcsRYpRgYsF0uGhgRcRyKXkTAwaFjLmRDCpa5Skt1kwJYFsBgyiFi2sC4EtoDIhMCWBQ7DBlAUlgAfYUWYGJSkGFS2gsUhAHMAYCwAGBBbUo5DACw5F0CsWCsEEDDCAIvIcQDKCwaAELYACCxeQ7AJYFfYCKgAsBAUWAiBQwIAABCgi5AAAAAAAgFAAAnIpCACgCagcgA4gBgQcwEAABAAAAMAARgBUsCgK2AA2tYZX0IAi8QECgyFAwHIWBUXAWAsXkMInAc7lfsAEsVC2hUXAAFRlhCwKC4TKcAXmLDBlEgkWw7hhAhSlwZRIpUiWLgRFHPQowjHmUtgMCWFjKwsXAxsDKwGBjYWMrAYMsbFsUDAxBkLDAxsDKwSGBjYWK0GMCWFilJgYhmQLgYkM2iEwMTK2gsUuBEgZEGBBYosMDGwM7EaGDLGxDKwJgSwLYWGBixx4GViEwqajmVixMGUBR3jBlLCxQMGUIUqRMDHUpbEGFQFsLEwZRoiRlYW0GDLEti2AwZSwsWwGDLG2uoKPAYVLArBMCWFi2AwJYlusySFhgyxaLyKBgSyRLGQ5jAxsLXMncltCKxaFi2AwMSiwa7CYUHIWD1CABWBORC8g0FY9xUUAQCwIBC8QMCAeAAApLajCgAIIAAqgIBAhQBLCxSBUABiAAQAAAEQoYUBEUCCxQBAUgFIUgABggMWHIdoE5goAgAAAACFAIqAquArNFRRY34agAoCxDJDuLhMpbUFAwIuBUByZcIqIC2ACwHIuDJyCKgXCHIosCoqARTLCJYFAwILFFi4EsWxbBDBkFi2BlhMouILYJDBlCpCxbajCZQFSLYuDLGw5mXcRoYMpyHEWKMBbQcirgLDBljYWMmhYYMsbCxmLDCZYWI0ZtaCwwuWNhYyt2CwwZY2uLGVhYYMsbCxbdgsTBlLCxlYli4MpYtilGEyxsDKxBgyhGZCxMLljYWLYNdQwZSxDIPtJhcsR4FCJgyxsC2FiYXKWLZFsBhMsbAoGFY2LYytoLDCZYgysRoYXKAdgsTAAWKMDGwMmuolhgQWLYMmDKNERkCYXLEWLYDBlAZW7SJEwZSwLbQDCoR6mRBgELFsBgyhDInaTAliW1MgMLlLCxkyW6iYMsbEsZWAwMQZWIBLAy0JYioHoBYBxRCgCWBkyWIZRLUjRk9BbQKxBbACCxeRAJYcygggKSwUZCkIAKAqWJYyFtAMRYyJYmBOAKyAOQAAApAAAIqFsAAJco4AQAAEGLAAAAACBAFgAICsgEBkCYXLaF1lBycNWUAAFCuiAChEKVAoBQKiFQwgLFBlgAhYv0jCJyKQpcAVWIUqGgC7SpGUQgi2FimWEQWMtBYuDKIqRUgWKUylioti2LsplhYvgZWFhsmWIZUgXAlhYyS7BawwmWNiWsZ214Cw2VyxsWxlYWGymWIsWwtoXZMpYti2FhsmWLQsZWFibJlLBrUthbUuymWNhYyaFusmyuWNiWMhYbJljYtjKwsNkywsDOxLDBliC2LYmFyxYsZCwwmWNhYysLdZMLljYjVzJoDBljYW5lLZE2VywsDKwaMdkYgo5jAliWM7EGDKAoGBCGViNEwuWLCMiWJhciQKSwwmSxLGRLDC5QhkQmAt1lCKMGWDKWwsTAxsVl1I0MGUsCsEwqWIZEGBA0WwGAaIytAmAtdksUMYVB2hjwJgRgo5kwqEZQMCB68ikJhUtcWMiDAnIBkJhWRGTUowiMAIKWBQQQgYfAYAAEVByBQJYhkTmBOAKyEwKTnoUhFAABLBgBUKByIBC94AgKQKDkAADBGAQt1lBBBcpAAHeAAuCAV8AO8AAQEAEAVyLMFByWlBYosMCWFkUOxcCJCxSjAhRoDLCKSxQWIBFCLqZYQAQsMIAthYuERIthYqMogRF8ChcS4TK204AyXeipMziGOWKSuZWL4BLXgZRSmSwSsZJdgsjPZTKW5izMrWFtS7KZY2FjOwt2l2Eyw3RY2bt+Y3e0bBtNdgkzZujcGwbTXulsZ7vaXd6xsG0127Cme6rCyLsJlrsLGyyJbUuwuWCWhbGe6LIbCbTDdYszPdG6Ngy127AbHFEcSbErtMPAMy3WHF9Q2DLCxbGaTG6TYNpjYjNliNCaUy1tCy5mduwE2VywaFtTOxLE2VyxsLGXMWGyZY2DMicCbJljYWMmiWJsqliWMrMNEwZY2IW2osTZVLCxlYNDZMsbEsZksTBlEhYysRkwZYtEsZCwwuWNgkZcguJMGWNgZEsMGUJzMmTmYzCoQz0JYmBEC2FhgSwsWxWMGWILYhMLlAUMmDKWIZMDC5Y20GhkTgMCAoGDKADUmFS2gsZDmTAxsLFFiYGLRHqZ2I0TC5RIWKyMYEJYy7SMmFyhDJrUjJhcoC20HIYMo+IBSKhCgCEKCYEFi2BFRjsAAEKRkAlyhICFAAgKQgE5lAwoCAmAAAVAyjUCFIuJQICriTtCgFwQCMoYEsAyEF5EBQAAAgsUgDiwAByAYopyWpQC8ywAHiLFQJz0K0LMKAFKiFXcC3KhwLx5EKrmSBV3BJmSTMohJlNBoZJFUdDOKWOWKFjYojdRnFEplikVLsM1BGSgjKLcsZqaknfgZJG1QRkqa6zZFmWM1w1JdhVHuNypoyVM202Jljtw0Jd3tKl2G7zRVSfUbIsVMZrhpt2BLsOR5qXUXzT6mX0FSbcONbsFuw5DpO3Bk80X0Um3DS0+oqi11G5Ui+bHopTbho3XxG7LqNzp9w3Hfl7R6I22ndl1FUXb1Taov/pizJ6M2mqz6hZ/JN2678CpPqZfRm00WafAqT6jdbriNL8DKLabTVZPiN1M22i0NyPLQvozaa3AxcTa0uQ3WY+jNpocdeAcTfa3FEaVrtE9Gu00WfWXdZu3Y24EcUT0S7TXuvlxJuM3JIWQ2E2mm2osbd1GLXIx2F2mtpkaZtcQ42JNC7TXZEsjNpES1Jsrlhu3KomVgY7K5Ybpd0ysw0NlMsHF8TFo2NEszGaViWAsZ2FuwmyuWuxLG2xLdhjNK5YWCRbMWZNkylroWMt1izY2TLDwBnZhRJsrlrsUz3ewbuo2ZMtdhumzdFibJtNdhYzsicrWJsrlhuixnbsKTZMtW6LGy3YWy6ibK5arFSNjJoNhMtdgzOyFkybC5awZ7qFibErlrsGZuJLE2TLAttDKw1JsrljZk3TOxLK4wZTdIzLQNKxJgYCxkRkworAJAmFLdRjYyDWowmWI5FYZjhcsRYr7gMLljYW0K7ksyYVBYtmW3WTAxtYFbtyI+HAio7k1LqSzsSYU8QLaFS0JgyxYMtETwJhcgJqUBYnIpLBUfAItmEiYEsC2FhgSwsW2nEWGBixYtiEwIwUWJgQnMysRhUFi6BEE5AvYAqWHgXQX0Ag8Cu3IhBAV8LkYVCgXVgBiZXuTuIIUF0GBBYcygTgRlDAgHPQAbxZ3LyGpyWoA1KALYniUqI0LFHgXAJFsQcyopSDQqLZGSMboqaMokZJlua94quZRUxmGxFTME5BJme1KYZ3Knbma1HrZUo39YyiqUxDZddZVJGC3PlF+1osVSmGxSXWZKS6zUpQXIKcPkmyLnrYzS3Ka5MyVTtNHnKd/VKqlP5Bsi7jtYzR6nJU78zOM+04qqU7+ozNVKXU0bqb/rYTR6nMhU4GyNRHBVSmubXiZqpDlNm+jU+tqqtvIKonbRMyXm3xijgxqK3ro2Rqdq9py6NRE8WqbcuaqVJvqMo4aD4SXicaFVozVZdq8DfFdueMNM01c254PTSzNcsI1xi/YbIV1b1jbDEX+MjbFFmphtXIcKWHt8UxdHXgzysK0GrSijNRw8+Vi/Y6J4Sn2iqOMPD+Z7/YPNvrXsPM/BKcvVkYTwckuFx9hnkRqoeK3H2MxcHe+6jnzw7XGJg6NuRrnTYbIuxLguP4LI4J8jmuk1qYuk+pGr7Ozi64bpdTHm5LtOVKl2E83JdRPs+OxfSONZ80NTkKEvk3Dj1wZj6FdtxrPqI1L5JynCL+K0NyNuok2V23FfbEmnyTkunHrI6av6yMJszC7cOO1rwMWl1HJcF2Mjiuoxm0sVuNu9g3es3uMeRi0jCbbKKmhrsGl+Bu3EzFwMZtstprsiWNu72Dd7DH0ZtNVknYOKNtrcgovqJ6NdpoaFje49hi49xh6OV2mpolrm5xRjZIk25NpraG6+o2W1I0zCaGWWtxY3TY0SxNg2mFkHozJpIjkkYzGFRdiLcx3kRttcDHK4ZNmLsS0mN19ZMzyVbpEbVxulUVYx3m5N5Eb6kZeil2k3okwrG76ia34GW8ib3YYe9U16irevqhv9iJvPqJuU1F31Eu3yF31DIt9OAb7CJvqLfsYyF+8jDehE0XMLhWTuF0xoTIpA2hdECwafUXQt1biMQMLDdfUZ6daBNky1NMWdzNolibK5RJ9Qs7mV+wXb5DZXLCzDTsVsbz6jHEDFpizMt7TgGxiFYNMjuZtrkRtPkYzCsdRZluL9ZMQqbrv6wslxZWGiYMpdcjHuMrEJMSZYu5LPrMmuQ3WTZlllik+sWsZ7rG4NiU2mGhLaGxxRLIbErtMLFszLQNomyZY21FivuYXcyYVLaDQa/JDv1EB24k0Dv1C8rcCKXtyHHkLvmiNvqIo0yWYu+oX0IFmLMjZLk3C2diNC4vyJuUs0LMXJcC2YfCwIQBYXJdhVdx3hslyABciYVQQAOBC6k8CAUmosBdBcCwC6JzHaOYAAAcjlxBikWxyGo06yqwsWy6yhdC49EXXUELi43l1Eu+wZVdbFszG7F2MwmGW72lsubMUOZYmEZaLtLdGKuXdZlEyLfsG87jdXNj0FzLGU3G82Ltl3ofJuVVLcIovtkY2bKoSfJjzsuxE359bGaU3s1Tl1FVN82ka9582wmWKqUxLbuR5zRVuL45quiK7eiZltx2QYbvtfymVea6pM1RjN/FZsjCp8n2mymqZ/KxmPWzTpfJbM1Kkv2tmrzc+bivEqhbjUgbYqqjsYTEc27ep/eyqVL72jSlHnVXgipQXx37DZFc+rwY7MNylSf7WVOlzg/aa15v5U/wAkv2vrqewziufUxw2p0vw14mcZU+VWpE0JQvxqewy+19dX2G2mufUxmlyYyXKv7UbIzlynTfuOInStrKr7B9pv69Rfim+m7Mf1a5oh5CE6iekV4SORTryXrKS70eJUaXKtJfim6nNx9XGNd6ZzLeomP6w012on6l5iniV1r22ORTxXbL6Tw0a1Z/t+Hn85WM4zq30o05fMqWObRqp+vqXFq08S85GvCS9JRfuZsVPDz64+B4aNeaXpUq8F2reRvo4uF7b8PG8WcqjU01ficerT1R+F5GeBTV4tNGirg3HkZ0cV1OXg7nLp4lSVm4z9zORFNqtomq7Q8XLDtPmYuk7a2PNWoz4pxMZYJSXo2l3E+zR2LGpxxeFdHsMJUteB5WrhJR5HHlSafA1VWPU3U388HAlSfWTzT7DmulpzMfN9zNXoYbIuuG6V+Rg6aXFe45rh2WG72mE2YllF1wPNx7AqUTnOOnBMwlFfIMJsQyi64TpLkyOkjmOEb+qyOnDqaNc2I5M4uOJ5q3MjpnLdOHW/aYypQ+VL2mPoF9I4nm0mHFHJlSp29Z+0wlSpr4z9phNrDOK8uO0iNq5vdOl1+8xlGj2e001W5ZRVDQ2uZi7cTe1R/B9pi3SXUaqqfXDOKmi6I+xN+Bu36a6vYHUj1P2GqYjmyiZ5NDUvksxcZ9Ruc/wG/Axbn8hmuaYZxMte5LsJuS5szaqP4pNyo9TCaY5MssPN9txuRXUZebnzY80r+sY7E8ja9bB7qWljFyXUbfNRXNhwprqMZoqWKoad4xu2bm6aei9xi6i6jXNPOWUT6mFpPkHCRXUfUY78jCdllGTzb60VU1zZjvPrLZvrJ93ku9d2HWS0OwbknyJuMb+R7xuJN5DcfWi7i6/cY4q5G5N5ciprqLuxtxdy2j1liJMwxb7COVuRneCDdPsGzPMy1OQ3uw2fa+wfa31GOzPNctd11C65ozahyI1EbMrlg7dQ9EyaRHEmJXKXj1EuuRd19Qs7cDHEhdWF0NVyJ2boVXwIHe/Amr5AUtzH0uoqhN9SRIzPAW6uL9hkoxXFmadNcDPYntTLX4Ft2GW8g9eFy7EJlqa7DFo2uJdwno1iWlxIos3Pza4zRN+nfTefciTbjmyiZa1Bsqg+4zcuqnMxcqnKl7WNikzI4dcibiWpW63KEUT7f1ImKY7JPeWS5Ma/JZJef4W9hH57nvEmrHZK4V71tIsjUvkMxbqL5Zjd/KkYTWuyye98gnpL4iMdflsa/LMJqZYW8vkol5dVhZ/LJ6XyjDesLvS6iXfULS6yWlzIp6XJD0i2l1j0zHAjuLsXkhvPqIo20Tedi72vAXj1E96pcl+wujXULJ8xvEuuoX7Bu6cSqJN4x0DsVpkt2ASy6xZFaI0RSyQ5agnMgtkYl5hoiowUhAdhoLMbreoB2I2kW2hGgpcXIWxBO4MBgNRqUXXUFQMcSakFSYJqANwuWy6x6Pab8NQBePUXeXJF3ALPqG8w2xuRd19Rd187GN31gZgZJLrL6K62YC5dowz3lyQ3uwxRbN8EXMymDedxftG6y7q5ySLio3ILl9HrbG9HlEY5yIusySk+CJv9SSJvSfMRspvZqnPuMlBL1ppGpN8ymUTTyTEttqa4ybF6a4Qb7zWg2Xb5QYZ76XCCRfOT4JpeBglJvRN+Bkqc+at3sziqueCTEQu/P5TF+tv2l3F8apBe8q80vjyl3IyxV2z4sdyLuLfsLv0rfc5S75DzkeVGmu+7Mo9qe5N/XQyU5Pgn4Iqqz5OC7ol87U++S8DKJjmk+wXnnwhN/imajiPvcl32Rr35PjUl+UYuUed34mcVU85Y4lu3MR8leM0Vxq83TXfURpTh8kb0U/VSLFdP1P7GzLcoy5zo/llS66lL8o0edh1Isal+EJPuiZxcpSaZchR/ytH8pmShf9to/lM0pzf7VU/JMkqr4UZ+w301xyYTHrbFSvwqUX+OZxoz4qdLwqI0qnWv9wn7EZearfeJe4zif9MsZ9rl0li4P0Jy/FqI3qvjvjQc1+FBM8cqVbh5iXsRnGniU9KNZdxyaLtVPCJ+vc1VURPHDnwxW47zwqi+uN4nIp4+h8qceyVpHjYTxsOCxK/FbMnisRH143X4dE5NGrmnn3NNViJ/q81QxcW7wqRfZGVvczm0sXJcX7UesxxdF285hqDf4LcGcmjisOvVliqXzKimvecyzr45uLc0mex7RSxjfH0l7Ub18Gqr0luvrR61SxdO91iqbf8Alabg/ajyFLEVXFNLfX4LVRfWdja1lNXrcC5pZp4bnkquXNxcqUlJHDqYepT0cRSxu5LRqEupScX7Gc+GPUlavFSXXJbr9vBm+PR18GmfS2+O94qUddY27jB2634nmZUsPiNaVRRfyZ/Wji4rB1KablTdutar2oxm1hsovxM4ni8ZI1u3J2ORUjY0uKfI49VLlUyxt1MWfWXdt1oJPvMdlnljZvkmYtNcYGbT4OPsZHbraMZphYlqa/yTMJbn3uS8DkLsqL2B+c5ODNdVGWUVOHLzd/Vf5Ji/NfJ/inKlKdvVXtMXKXOm/aaZt/WGyKnFcqS+L/FDqUvk/wAU3uT+9v2mG+1+0z9xqmjH9GcS0+dp/J/imMqsb+q/YbZVVzoVPYR1486U/YaKo9fgzj2NMqy5RZg6r+Qze8RT505fkmLxMPkS9hpqx+pnGeTS6k+UGYOVTlB+w3/CI84SMXiFyj7zVMU/qbIzyabVX8WXsG5VfJmx4jsXtMXiH1I1zsc5ZRtcmDpTf/8AceYl1pEdaT4PQm9UfNs1zscpX7y+Y11l7h5qK4yMGqj5SJ5up8lmGI7KWW/mz3KfX7zF+aRPN1OonmpdaGJ7KVjHNXOPJGLqdhXSfWi+aXFyMJpuSv3WtzfYRylY2unDjve8NUlx18TCaKu2WW1DS3J8zF3txN+9SXBL2E85D5PuMZojtldr1NOvaEpdTNrqrqJ5zsMJpp5rmWvdlfgNyXUZOb6ib76kTFK7xQl1F3JWuTffYN99hfuG8cJE3JBzfWRTkTNJvXclyI4zG9LkL1Oofd9a7zdmVqYXnG+Bd2ZYjllMsbTDUjYqVSTNiw9n6Ur9hnTYrq7EmuIcezfMyjT65M3OMIaKxLrtM/QxHFNvLW4JcEN2T5u3YZp3dopvuQba4uMfexswZliqTZbRXGXgYupTXFyn7jHz7vanBLwMdqincuJlsf4MGzFua4uEfG5i44mp8WVu3RGHmdfTq04/jXMZmrsj5LERzZScedWT7kYudP5Lfexu0Fxqyl82IvRXCnUl3uxrmavV8fNmnnbcIRRi60+uxk5Q5UIrvbZPOtcIU1+KYVTP6liPUwdSb+Oyb0ucmbPPVOTS7kiefqfLZhOz2zP172W/k13d+LDb62bPPVPlsefqfLZMUc5+veu9r3mvjP2k35fKftNvn5/KXikTzr5xg/xUY4p5m/kx85NfGYVWfX7iuoudKm/Cw3qb40ku6RfZV8T3J51vjGL8Cb0G9YLwMvtL4qa8bk3KXKo13xJ97nldzH7X1SXiN2DWk2u9F811VIPxI6U0vVv3O5Jir9K7uabr5TT8Q4y/6Zi01xTXeRGvMclwz3HzFmusxuwmxmDet2W77CKT6w2nyJ7wbXNC0etj0X1ollyaAu71SRHFiz6hwZN3JUafAt2W+g3huEuw2+Rb9aQv+CMDG7CbGnyWFa3Mx3qa8xqHbtHo9oE4gXQdiKhGW6JoQLsXYHIgXJyLddQbCpZizLcjZBLFt1kKA0GhLMtmBHbWxC27Q7BU4gvcCDbwFi6DQ34a0SKo94uLjMIu6+oqi+tGPiC5jkMrL5SK1HrMC8Rn1GGV49TY3lyijEFzKYZbz5WQ3m+bJut8Ey7kuouapNycR3mW51yS8Ruw5z9iGzKZYAzvTXKTG/FLSC8RiO2TLAyUJPhFl85LgrLuRHJvi2Pum9moNcWl3stqa4zb7kaimUVRHYmGzeprhBvvY8416qjHwNfMXG3Jhsc5y4yZjz+sRjKT9GMn3Iz81NetuwX4UjKIqqTdDEXtwM1GkvWrX+bG5N6iuFOcvnSt9BdnnP17kywcvBFi5N2im+5XMlVt6lOnHttf6Q61WSs6kvoH3eZv5KqVZ67kl2vQvm2vWq04/jX+g1vXjd97CdixVRHYYluUaK1deT+bD6x9o+TWl3yS+g1JkckuOneZekiOER9e1Nn1t+/SXDDx/Gm2VVmvVpUY/vZoheXqxlLuTZt8xiLJuk4rrk0jZTXXP4Y7o8mMxEcWar1fvm73RSDq1XxrVPaYebs7Sr0Y/jX+gu7SXGvJ/Np/WZ7dfbPj+7HFP1C7z5zk/wAZi8X8p/jMxcqCdrV5eKRVOlyw7fzqrG3zn4+S4ZqKt6rLaK+KvFkVWC4Yagu9Nl8+76UqC/ejOKo5sMSl6afxfyixqQXx1+WVYmouHm13UkZRxVX5cP4OJlFdPPw/dMT9f0WNe3q1pLuqM2xxdVcMVU/Lua/hNTnUj/BxCxEnxnTffSibqbuOFU/XvYTTnjH13ORHGYh6Os5L8JRZl8Jk36VHDz76S/QcdV9PVw776KHnYN64fCP8Rr6Gb4vVfq+vFh6OOTlxrUGvSwUF2wqSibITwN7/ANWUX1xkp/8AM4ka9FLXBUfxas0ZOrhZL+tqsfm4i/0o2U3pjtju/Zrm36p7/wB3kqeJ3XanmsJRfxcVQdvbqcyhWrfEo0anbg8Qk/yZHgoywb4yxcPyJGSpYWWqx6i+XnMPJe9HMtauqP6+eWivT0zx+Hlh7B8JjTa8650X1V6Th/GWhz8LmNWC3qdSUl1xaqR92vuPWsM8ZTdsNm2Gl1R8+1fwkjdJ5hF79XK6dX8Oikpe2LOwt62YjP14ZcO5paat0zH16pw9ljicDi5NVqKU/l0Hr4xZjVyl1E5YOtTxK+R6s1+K/wBB4BZhh16OIeKoPqxEN9Lx0kjn4TE+cX2iqq1uCpVVO34srP3nJt6u3cnEuNXprlrfTOPbw+vYlajUpTcKkJQkvitWfsZpaTfK/VwZ5WOZ1HFUsT5urDh5vERcX4N/WZTwuCxEU4ylhW/i1otwfdJG2aaap+7KReqp/HH18Xh+D4OIUrvSUWzm4vL8RhlvypyVPlUpy34PxRwpK/Fwku3Q1TE0zvb6K6a4zCSXyqfijFxpP4zi+8vDVKpDtWqHpyXxKq95hMNkNcqTtpOTRi4W41ZLvRlLzaeqnTYUar9SrCp2M1zEcmcTLX5tv1a0X4E3K3KUH3otRSWtTDeMdTS1Qb+6TpvvaNNWInHzw2RvZyWJXxYPuZrc6640X4MydOX7Xib9+o3cWvVnCa9hpqifX4SyjHqaniJr1qEifCo86TNkp4yPrUb92preKqR0nQ9xoqnHGqY9sNkRns8U+FUvvRHiKX3v6CvGUvjUET4VhnxoL2Gua4n88dzLZ/0sJV6b/a/oI60H+1sz89g3xpfxS7+Cf7X7jCczwrhd0dktPn1ypsjxH4HvNzeC+T7mYyeCt6v0mE01frhlEx+mWh4h/JRi60upG9vB/JMd7C/I9xrqpq/XDOJj9LjupO3L2GDnN/GOV5zC/Iv4GLq4e+lP3Gqbefzs4q/0uNeXOTGvM3uvT5U/cYuvHlA1TRT+plmeTVZ9XuLuvkn7DN138kjry6jCYo5svvcmHm5/JZPNzfL3mTqzfURzm9LmOKPWv3kVKXYZqi+ckYre5yKlLldiIp5EzKulFcZkcKa5jck/isyjRqPkl3mURypTOO1g1SRLwXI2+YfOSRY4ZPrZlFqueEJt083Hc48ol3m+ETlxwyWrVjYqcImcaaueLGbtPY4KVR8IGcaVWWrsjkTnTjpvIw85vaRi2WLFMTvk25mOCKil607mUVGPIwk5LVyjEw85Dtk+0zmaKDEy3Snbg14IwlvtcPGTNXnZvSCS7lqPM1XrVkorrk/0GE3Jq/DvWKccSTitHO/ZFGDqJP0YK/bqbGsPDnKo+zRE+EOOlOEKfcrv2mmrdxnHizj1QKGJqLg4x636KJ5imn9sr3fVBX95rnUlUn6Tcpe1m2OHxElfzUkuub3V7zGnFU7omfr1LOY4zhGqEfVouT/DkR16i9Tdh82JlKjTi/tmLprsppzZg3hY8IV6r/CkoL3Caq44TEfXq3rERPra6spS1nJvvZjBJ+qt7uVzcqi408PSj+K5P3llLESXpTml2WijDZirfM5Z5wwVCu1dUZJfhWQdKSfpSpR753+gjpp+vNeLuRqlH4/uJOIj9yElGnbWuvxYNmKVFc6su5JBzpcE5MyUJSXoUKj8Ga908IjxZYxxSTpJaUqr75k3qX+Dy/hGZulX/wAHmu92MXSrW1ppd80SaauXh+xmOfijdP7x/wDUZL07fcX/AAjK6VT8BfviJ5mpydP+ERMTy8P2Xdz8S9L5FRd0halb9sXgmXzNbqi/3xB0q/3tvuaJsz+nwMxzYuNO2lVrvgTcXKrTfi0WVOquNKa8DF3XGMl3o1zEdsfFlHtPNT5JPukg4yXGMl4GPo9hknb1ZNdzMfurvYu3YTh1o2b8vlX70S6fGEfDQYjmMd+S4SZd+/rRi/APcb4Tj7xux5TXirD73M3I3D5LXcyJRfCVu9FcJJ8L92pjw4q3eYznthWW6+Vn3Mxaa5MWKm1wZjuE0IZ73JpPwJePyfYyYVFfrCky2XW13ox3fwkxvFun8X2D0e1E3ZdRHfmFZWXKSCTvxMbsl+wmYGXpIXfURPTiVt24gS4uuBbvnYl+xEDTqJoW66iadQwo7C66hp1DwIJ3AvIXIIEn1B3IyKWGnWHwCAaLtF+wjLYil33EZWmTxG8PAneW67Q3rwJgQDmArfaPW/YPQ7SBG7LUyvHqftF4/J95iBlMMt5W9VBS/BRiC7UmGe++pewu8+pGCA2pMMt99nsG/Lr9xj3FtLqfsLtSYhd59bI2VQm/iS9hfNz6vaXFUpmGKBkodcorxKow51F4IuzJlgDP7Wvlv3DehyprxZNn1mWBVGUuCb8DLzjXqxjHuRHOb4yl7RilN7JU580o97LuwXGqu6KuatL3K2ZbVMdhiWzepJ6QnL5zt9A8616sYQ7ldmu+o4iLk9ibMM5VJy41JPxMNBdXM4UqstVB263oPvVetd0MdSoy83FP061OPYvSfuKnQj8WpU72oouxPamWtsygpS0hFy7lc2Ksl9zo0odtt5+8k61WWjqza6k7L3FxTHamZ5K6FVL0oqC/DkkRQpp+lXv2Qi37zXbW9tQXapjhH14GJ5tt6K4U5z+fO3uQVZx9SFKHaoXfvNaI3b/mPSTHDcbMNsq9aXGtU8Hb6DXo3dq77dSQUpP0U5dyubfMVErz3aa/DkkWNuvfxTdSx9wbNijRWssQ5dkKbfveg38OnpRqT+fUt7kZY5zH17MpnlDVJ2eugjK79HXuVzZ522sKNGHdC795k8RXa+7TXdZfQMUx2m/kQo15axo1H4W+ky8zWXrunD59RI0Sk5P0pOXe2yKy4JLwMtqj6n9kxLf5qPxsVQXc3L6C7lHi8Xf5tGRpUn1lTMtumOz4+abM8/g3JYdcatd91NL6TNSwq5Yp+MEcZtW1aQUot2Tu+zUsXYjsTYmXJ38L96xD/fYmUZYXnRxH8MjRCFSTtGlUl3QZsWFxf+DVV3qxupqqnhT4fswmKY4z4tu9hPveJX75Fjewn/jF3ODNfwXFc6Nu+pFFWFr81RXfXibdqr9PgxxT+rxbUsG/27FR76UX9DMlTwvLHOPzsNL9BqWHqfKw/wCcRKsPW5Sw/wCcRNkVT+n4+bGcfq+Db5mnLhmGEfz96P0ozp4Wve9CrhpP/JYlJ/oNHwbFPgqb7q8S/AMXL+5t7ucX+kziZ/RPj+7Gcfqjw/Z5CFTOqcbOOLnFcpKNWPvuY1Mav7ryzDO3FulKlL2o4SwmMparCV49sVb6GbFisxo6ecxkF1STa96Zvi/VTxzHj8cNXoqZndj3bvhl5DDZlh4rdpYnHUE/i+cjXh7JanPw2Oh8SvhZP/Jylh5eMXeLPA/ZKq7eejhqv7rQX6LGXwvCVPWwFOP7jWcfc7o5FrXY/N8flmGmvS57Ph88S9ro5hVw739+vQv8adP0fGULr2o5cp4PGQ362Gav+3YZJ+1LR+49RwtbB0pb2HxuZ4GXbFVIfxfqOZTxU5z344vKsTN/GUpYaq/E7C1r87qt/wBd/g4F3RYnNO6e79vF5etlUpXngqkcSlypvdqLvg/0Hj6kZRm4TV5rjGUd2SN0MRiYtSrUMXTt8aVNYiH5UfSPIUMzpYuHmMQsJjox+I6npx7t60l7zlxdtV7o3T9e9pzdt8d8fXbw+DwzbTsqjj2TWntMXC+roxl+FBnmK+CwU39oxVXBTfCli4t033T+s4OMwGLwy36+Fap8qtP04P8AGiY1UTG9tov0VTjhP13+7LhX3XZVqlPsmroycqrjrGjXXY9TJTe76N5LvU19ZqlGnN382m+um2n7DRVu4ORDXUWGf3TDzpPrSMFTov7ljHHsbNqluu0MTH5tVWZhVjJq9TBqa+VTdzjzETvx9e5ticfXmnm8XH1K0JrvMZV8bD16O8u65qaw296NWpRfUzJLEJ3pYqM+9muap/LM+6c/FniO3Hck8ZF/dcOvYY+ewcuNFLuNzq4yK9Okpr5tzVKtTf3XCRv2Kxqqqntq76WURHZHdLFrAvlJDzeCa0rSXeRywcnrCcO5mMoYVr0a013o1TjlTLOPey8xhnwxKJ8FpcsTE1+YpP1cQvFD4NppWpsw2Yn8kd7Lh+bwZSwsb6V4MxeEX36BHhp8pwfiR4erbjD2mM24/Qyif9RLC9VSJj8G/wApEPD1ebh7R8Hn8qHtNc24/Qy2v9SrDLnUiPg8FxqInmHzqQKqUec0+5E2I/R4m1PM8zR++EdOgl61/EqpR5KTNkaK+SvEyi1n8sJtY7Wndo9TZUoJ6U2b7QjxlFdxi6tGPaZRaiOMxCbUyxTS4U0ZpVJLSCRrlib6U4e4ieJnwTSLE08I3+wxPa2unN+tNLuI4U4+tUcvE0uLT+2VUu7UsZUF8qRjmOXfK4luU6aWi9xg68viQbMHVh8WH6TB1Kk9Iq/YiTexuie4ijnDbvV5c4w95hNQX3Ws59iMPM4iXrRnbt0RkqdOK+2Vop9UVdmEzXVxjvZYiO3uYOpBfc6S72Y71ao7QvLsSNnnKEfVouT65y/QjGriJuNnJRj1R0RqmYjjV3M4jlDF0JJ3rVIw7OLKvMw4QlN9cnZEhRrVI70ab3fly9Fe1map4eGtXEOo/k0Y/wDqZIpnjFOPXP7rM85a515btk1CPVFWMaUKtd/aqU6j67fpN3nIL7hg4J/Km3N/USpKvNfbq1l1OVl7EKqdr8U59n7+RG7hGGEqDi/t1enB/JT3n7gnh48KM6r65ysvYiPzEVrUb7IqxnRjOq7YfCTqPrs3/wAjXERnERHxll2ZnyPhFZrdpKNNdVONvea5Upye9Vkk+ucrs5FWniKemIxFHD/g715eyJpksHHW+IxD58Kaf6TOumcfe8Zx4cSmY7PDz4MZeZhHWbl3aIU3Ko7YfDub7E5f8jJYiMJXoYXD032xc37WWpUxlZPfqVXHqvur2Kxq3Tw8I+c+TLetSjjIr7a6eHT5Tml7ka3To/Hxm++qnTb97MFSjF3c6cX2ast6XNzl4WJM44x3z5YWI9f14q3hYrSjVqP8OpZexEVeK+54XDQ74OT95hOpSWihFd7uWCr1H9qo1JfNgYbeZxHhH1LLZ5tvwnFW9GbivwYKJqnKtLWdWb76hnPC4pRvUhGn+6VEjDzMF6+LoL5t5/QWrb4Tn3zj4kbPYwcE+Mk++Vyebh+D7jb5vDLjiKs3+DSt9IfwVcsRLxijVsR2472WWpwh2e4JQ6voM97D/ear76pN+h/g7/hWTZjnHj5GZYWjyjEWhb1YmTlR+8P+FZHKl95l4VWTdzjx8lzItObXdIu9NcKs0S9B/tdRfvhP6n/y6/JY96q5zfGcZd8UYtpvWlT8LoWo8qlVd8EN2HKuvGDMZmfrBEQj3H8Sa7pXFofLku+JbPlWpP8AGsXcqW0SkuySZMer69ysd2/CcJeNg6c0r7j8NStSXrUpW7jFOK4Pd9xjMQb0at2Del16dpnvy+XddupNHxhF92hMcpVjdPjFeGg9H8Je8tov5S95N1PhKL79CYkTdT4Si/cHCS5MrhJauLsRPquvExmIViGZuUuu67TG65xX0GOIVivYVSkubHo9UkLLlL2omJBy60mLx+T7GHHqafiRxfGzG83Lp2i0WvWa8CWfNEZMi2j8oW/CRAxkXd7ULP8A6ZEBuBpizA0IG6xbtIwRTxGnG4IMqXQuuoMnPgQUjZbEt1gQFfeNLGOFQF06hfsAgFwFbt6PyX7S3j8n3mPIG7MtWGSkvk+8t18le0wKMmF3vwIl3/wY+wxsBmUwy33yt7B5yXXbwMCl2pMQy35/KZd6Xype0xs7cH7CqM3ruv2FzJuHfm37SWRluT+SxuS52Xey4q5JmEF1zLu9c4e0qjDnUXgmMSZhiDK1P5U33Kxb0/vcn3yGz6zLDtC14GfnLerTprwuPO1GvXa7kkMRzN6KnN8Iv2GTp29acI97Ncm5aybfex4DNKb2zdpLjUlL5sbfSN+mvVpX7ZSv7jWC7eOEGGzzk/itQX4Ksa3eTvJuT7XcpOehJqmeKxGFXAGcaNWUb7jt1vRFVOK9etBdkbyfuMooq5Mcwwv1MjfW7Gy9FcIVKnzpbq9xVXlH7nCnT7VG79rGzHbJmeTGFOrP1KcpeBl5u3r1KcOy937EYznOfrzlLvZitO4uaY+vr4m9t+0LnVqfxV9ZVWUX9ro0ovra3n7zShcsXJjgmzzbJ1qs/WqSfYnZe41qyei1DfaWEJ1HaEJS7kSaqqp5riIgKjPzLi/tk6dPslK79iMksPHjOrU+bHdXv1Mooq7dybUNfIl76J3fUtTa61OPqYaHfOTn9Q+E4hqyquC6oRUfo1ExT2z3fvhN/JjGhXlHeVKSXW9F7zJUkn9sxFGHYm5P2I1yW87y9J/hO5O7QsTTHYYmW+2GS1qVp/Ngor3hzoRfo4a/bOq37kaCmXpOUR9e3KbPOW5YiUX6FOhD5tK/0szWLxNtK8181JfQjjNpcWWEZz0hCcu6LLF2vsmUminthtlWqy1lWqy76jMGlLV697uZfB66V5wVNdc5KJVSivWxVFfNvP6DKdueMd/7p92ODHdiviR9iKnFfFj+SjPdwy4168vm0kvpZd7DLhSxMu+cY/QZRTjtj69htMVJdS9iF0/ix/JRlv4flhG/nV3+hF87S/wLD+MpMzj2x4+THfy+Hmw3Y/Ij+SibsPvcfyUbfPQXDCYVfiyf6Q66/wAHwv8ABv6zLdzMzyYxe76t49zaNsMTiY+picRHuqswdf8AyGF/gn9ZVXX+D4X+Df1mVNeOEsZjPGHI+H421pYmcl1TjGX0onwybVqlDCVPn4dfoaNHn4PjhcL4Rkv0lVejxeCoPunNG2L1X6+/LD0cdlPwciOIw9vTwFHvp1Jwf6TLfy+ek4Yyn82pGovfY0LEYXngbfNxMl/6TJ1MA1rh8VDtjiFL6UjZ6X1x3fswmj1T3/u5eGhhKb3sNm9bDS/DoTh74Oxzo1swrJQjmWU5ivkVZwcn+Uk/eeGTwDWk8bB/NhL9JVRwdRa4+Uf3XDNr3XORRfqpjFOPdOPjPyaq7UVb53+2M/CPm85KvmGFS38qxWHXN4abcX+K7xOVg88p0Z3p4uFKb4xqUZUZPvcbxfsPX8PTVJ/1NnGFpPlapOl/yObCpm9SO58IwOPj8mpUp1PqfvOZa1dynhn4/DHwcS7pqKo348Y+OfjDz1fFYHGWli8NSU5fttO9KXttuy9xpnlcazXwPF067fq06z3J+Ek7M8MnjKHHJalHreEqygn4ao2QzCMHatKvR61icKmvyoWZyo1lFX8yn5fHHzaI01dH8qfn4Rn5N+LoVsNLzWJhVovhu16e8vBnHcNx3jTnBvnSlZPwZ5LBZzV3fN0atGvT+9U8Qpp/iTM608qqr+qsBUwcn8eknTX5LvH3myYt1xmirv8AP+hFy5ROK6e7y/q8RUlPhOcZdlWnZ+1GvzdB6ui49tKd0eYWU0qy3suzCnWvwg3uS9vqnExWAxeGl/VOGnT/AAqtLT8qJqr09cRmY3d/i20aiiZxE7+6e5wY09ftGNcX1T0LJ46HFRqLuTMpU4Tjrh97tp1frNE4UY2tVxFB/hRuvcceY2Y3fHzciN/9PInWl+2YSD/Fsa/O4Z+thmn2SNkVWv8Aa8dRn2Slb6TKUcdbWhTqLrUUzTMTV/SJ+DZuj+sw0N4R/EqR8TG2F+VUXgbZOqvXwEfyWjXKtSXrYKKfe0apxHHHdLOM9me+EawvKrP2EfwflUq+wjrYb/BV/CMOthv8FX8IzXNdPOnxZYnlPgjdFaqVRhTpfIm/Eeeof4ND8tj4RT+Lh6Xi2zHbjnHdK4nlK+dguFNeLJ8IqP1YxXciLES+LClHuhcyVXFz0jv/AIsLDbzwqn3QbPOEviZ8FPwjYnmavGb3fnSsZOjjqnrU67XboT4JVWs1Th2zqJDZmeyZMxHbAqdFevWT7Iq5XPDw9WlKXbJmPmqS9fF0l8xOX0FSwseLr1X4QX1kzMcIiPHzN082MsRK3oQhBGtvEVX6LnPuRt8/CH3LDUodsryZhUr1anoyqyfYtPcjCqYn8VUz7Pr5M6YxwgWDqrWtUhS+dLX2GW5hocalSo+xWRIYfEtbyoTUflS9Fe8joR/bMTST6oXm/doIpmPw09/74g2s8Z7mXnaa9WlH8Z3MZ4qp6qaiupaFisJDj52b/Ckor3XZshiKcF9rowj+KXNXCa8exMRyy41qs+Unfsubo4PEW3pqNGPyqslEs8ZWd7VHFfg6fRqcbzilK6vKXtbNUzbjtmfr3s4iqXIdHDR9avUrvqpx3V+UyKqqf3KlSo9tt+Xtf1COGxko73mXTg/j1GoL2sLDYdP7bjoyfOOHg5v28DONrjTTjw8Z3pmO2c+Pwa6s1N71SU6r/CZhGo3LdpwUpdUU5M5F8LD7lgpVHyderf8Aix095ZYvF7rgq8MPD5NKKgvdr7yTE8Znuj5zj5rE9kR9e7LF4THOKlWSw8HzqzUF7OJg6WDp61K9XES6qUd1flM1/a95ylUnOT4tLV+LDqU0rqku+bNczR/Wc/DcyxV/Tc2LEQjb4PhKNN/KlepL36CtLFVl9ur1HF8nKy9isiUViq+lClUn+5w0Nk8HUi/6prUMO+qpU3pfkq7Mo266d2cd0eSfdpn198+bjqFOCtvq34KEpUktIp9smbpQwEH6VTFYh/gxVOPtevuIsVSh/W+Cw1N8pVL1Ze12XuNWzjjMR4/t4s855tMJ16st2jGUn1U4GbwmJ413Civ8tUSfsM6uIxleNpVasl8mK3Y+yNkaVRcdbQh3tImzHrnw8/iu9k6WHivSxcqnZSpP6ZE3sNBejhd99dWq37loW1JetVbf4MfrMJzox+K/x5WJMxHZEePxysR9fWGfwuqvuSo0V1U6SX03MKk69VfbKlaafypuxlRhiK7UcPh6s78PN0m7nKeUZlu71elDCx+Via0af0sffrjtmPBnFueTx6pJP4q8DJR01kc14HCQssRnWFT5xoQnVt4pWDhksJW89mWItxtThTT9ruYejiOXe2ejntlw92CXH3oxk6a4/Sc74Tk8Zejk9aol99xr1/JiZrM8LGX2rIsqivw4zqfTJEmKecePksW6e2p41yodfvI5UOv2M8us8xMF9ow2W0fmYKH6bj9kWbq+7i4RX4GHpR/9IxTH9P3XZt857niUqb4RqPuiyunFrSnW/If1HkpZ9nU/741/DdX0RMZZxm8lrmWK/hUv0GOKJ/p+67NHOXj3CK406v5D+owapJ6wmu9M58syzOXHMcQ++r/yMPhuPfHHTffNfUMU/UfumKXCbo25oi818r3nOeLxrVniYyXaoP8AQYOtiZcVRl304k2KfqP3MQ4u7Tfx2Y+bhf7ovGJyZSm16WFw77qbX0MwbhzwqXzZtGE0R9ZMNag16tZLxaMrVrW3lL2MN0edOtHummS1FrSrVi/woJ/QTEfUphH+FSj4aE9Dqmu5pmeitu4mm+xpou7N8PNz+bJMbJhqajyqLxVgovlaXczOUXH1qEl3XMLQfNrvMJgwx1XWvcXeduT70ZWaXoz94d+cE+7QxxIwbT4xt3MlovnbvMmotcZLv1Io34NPxJgNyTWln3MxaaWqa8CuMlxi/YFKVtJP2mE4XexZLGzeb4qL70Y3j8n2MmBN5rmyqT67h7n4SFo29b2om8RyfNL2De7EXd/Ciybr617RvDTqQuuobsuou7LqG8Y3XV7xddXvDi78GTdfGzJvVbrq94v2Es+0WfUwF+xBPuFnfgLPqZN6lyals+olmTeBC+KCXaTAxsDJ27RddTCseQZb9iDbMRjqC3bAVu3l8lDeXyEY8gbsy14Zbyv6sS7/AODEwKMymF3vwY+wu+7/ABV4GIG1JiGW9Lr9wcpfKZiWz6mWJkxC70nxlL2kd3zftLuy+S/YXcn8iRfvSm5jo+RdOou5Pmku9l3H8qC/GQ2Z5GYQF3VzqQ95bQ51PZFl2ZTLAtzL7V1zfghemviSffKwx6zLElzZvRvpSiu9tjzklwUF3RQxHMywWvC5nGlUauoSt26B1Kj4zl4afQYuz4q77dR903snTt604L8a79wSpL405dyt9Jh3AuY5Jhs34LhRi/nSbL56rwUt35qSNY5F25NmFk953k3J9upLkuZxpVZaqErdb0RIzPBd0MQZ7kV69WC7vSfuLeivi1J97UV+ky2Z7WOWHDiZQp1J6QhKXciqrb1IU4dqV37zGc5z0nKUl2v9A+7BvZOluu05wh2Xu/YifaVyqVP4q+swXAcxtRHCDDNVbepTpw6na794lUnJWnUk11N6ew1lY26l2YZLTRJLuFyK7dkrvsNnmqi1mlT+e7CImrgkzEcWCL2mdqUeNSU/mx/Sx5ymvVoR75ycvqMtnnKZa07u0dWbFRqW9JKmuuclExdaq9N9pdUbR+gw0Tvz6yfdj6/qYluUKK9eu5dlODfvdkXzmHj6tCc+2pUt7l9ZpuDLbxwiPr25NnnLesTJK1OFGn82mm/a7mMq1aatKtUa6t6y9iNSF9bF9LXO7KbEcmSilyV+4t3biVU6jV1TlbreiLuRXrVqce70voLs1cjMMLstzO1Fc6s/BRG/BerQh+NJy+ouzzlM+phvdplFTl6sZS7kZKvUXquMfmwRJVqslrVqP8Zliaef13pvZeZxHHzU0ut6DcmvWnSj31EaXZvVJ9+o06ku4bVP1P7LiW7cjzxFFdzb+hF3KX+Ep91KRpT7S3Mtunl8fNNmebco0Pv1V91L/mW2G51MS+6nH6zRdFV3wTfgZRcjl8fNNmebelhec8V+RD6zK2D+Xiv4OH1mmMKjWlOb/FMvM1vvUl3myKpn8vxYzHrbHHCffcT40o/WWMcL/hFZd9D/AJmnzVTmorvmiebn10/4RGcVz+n4+aY/1fByVTw7/u2S76MhKhQf924aXzoSX0o0bk/l0v4RF3KnKdL+FRnFefy/HzY7P+r4eTk06E4O9HF4RPlu4jcf6DmUqucx0hi51F1LEQqL2O54tU6z503++Ivweq/2qMu5xZuouVU8ImPZ/Rrqoir8UxPteUrVcfJf1TllGsuueGX0xZrp42FCWmEr4Z/5DETgvY7o4EaVeDvGjUj83T6Gbo18dBaVMSl1Nt/Tc2RfnOZz3RPkw9DTjG74OfDMKEneVaT/AHfDwn/GjZnk8Bm9ejZYXEyS+Th6+n8HM9eeNrr7pCnP90oRf0WMXisPNWqYLDfiuUH+k5NvXzRvifj+7TXo6a4xMfDHye01sfhsRK2PwWElJ8Z7ksPU9q9Ewjg8DX/rXGYij1RnBVY+2P1HrtLE0YaU546iuqnXUl7HY2uvTnZuvQl/pGG3X7YnJp19Ff4oifDx3S0fY5o3UTMePhvh5mvkuJabj8ExC52Tv7OJwJYJU3aVFwf+Tq29zGGxVSNlTnF24eYxd/4szyCzrG00vPzlOFuGJw7kvarm+mdNc3zmPH44Y/39G7dPh8MvGzw9WOsZ4yHfBte5mqUqsdFmEE+qpFr6UeaoZnl9e/nMHS3uvDYiz9jsKry+escXiqF+VelvL2idNTXGbdXjMfOCL9UTiumfCfN4OXwp8MTg5/jRLGGNfCGGl3OJ5V4GlV1p1svrrtg0/ccepl1KHrYXBv5tZx+lGmrR3eOZ7/2ltjUUTu+X9HEdLMbXjh4W7IxJuZouFBL8WJv+BUHp8E/IxUX9JrqYCitfguNXduS/SaarNccM9/8A/lnFyntx3fu1OObP4s13bqJ5nNZaN1Uv3VIVMNh4aTpY2HfQj9Zr8zl/xqldd+G/5miqmrtmf+X7NsTHZEd37rLB4rjVdNfPrr6zW8Mov0sTg4/vif0Gxwy1cMRU/NV9ZG8v+/YnwpRX6TCaKfqqGcVT9RLW6VH42Mi/mU5MRWDjx+FVH3xgv0llLL1yxcn2ygv0hVMAuGErS+diEvoRrmKfVHfPmy3zz8P2ZKphlrDBQb/ylSUvcrF+G1oq0HCkuqnBR9/EixOGS0wND8erKX1D4ZFephsHD96v9LLFeOFce6MfKE2c8ae/6lx61SVWV5N1JPnKW8yww2KqepRqS/Fdje8xxT0jXjD9zhGP0I01Z4jES+2VK9fvcpGurYmeMz4ebONqOyI+vcqwL089jMNR7HLel+TG7NsKGXU4+nXxWIl1Qiqcfa9fcYRwOO3d5YOuo9bjur3keErR+6VMNS+fXj9C1LFuY3xR35/oTVn83w/qzqVMHH7lgaUX11asqj/QjB4utFWhV82uqlBQ9/EvmMOvXzGD7KVGcvfZIKGAj6yxlZ98aS/SzKZuRwmI9mPkYp5TPf8ANonNTlvyi5y+VN3ftZPOylpGzfUlc5UauHg70sBh11OrKVV/oRlPH4pq0cSqS6qUY0/5Kv7zXsc6u6PPDLM9kd/7ZaVg8fUhvrDVVD5U1ux95isJCL+3Y7DQ7IXqP+LoYVZU5y3qtSpVl1yvJ+1k85RWm633y+owmLft9s+Xmyjan6821xwMLWjicQ+e9JUov2XZtpYqcP61weGpP5Sp+cl7Zae4woU8TV0w+DqSf4NJv6TOpgsd+37tBf5atGmvpNlMVRvpjuj58WMxE7qp8fkmJrYyvpicTOSfxZVLL8lWXuOPFU4q2/4QjY3xwuEi7Vc2w661RpTqP2pW94/7qgrqGYYmX4Uo0V7t5mFW1M5mfHPwZ0xERiPg405UkrqlftlIxhUqTdqMFJ9VOF2cv4VRjb4PlmEi18apvVZP2tL3CpjcxmmliKlOL4qnakvZGxhj190f0ZxhPsfmcoxnVozo038evNU4+9iODwsFLz+aUd5fEw9OVVv8b1fecXcTlvTnDe5t+k/aZehzlOXuMcU8u+f6Mst7eWU36GFxWIduNasoK/zYp/SWGYTop/BsNgsO78Y0FJ+2d/oOPKUF8RL50jHzjk7U4pv8GNzHbxw8GUTLk18xzGvdVMfipJ/FVRxXsjZHEdKzu4JPrdkzlxy7NqqvHBYndet5R3FbvdjH7HtXdfG5fQSdneupy9kbskxVPGJ97LFUuNo73nH2thKnbWUn3Kxy1hstg/tuZVqq/wDD4Z/TOxlGWUU0v6jxtd9dTExpr2RTJsz6vr2LsY4y4UnTS+5t98jF1oLhGmveeT+H4SEk6GS5fC3Dzm/Vb77tL3E+y2OimqMsNRi+VLC0172mybER2+H9FxT2y8cp1Zr0ISfzabZvo4DNK7+1YHFT7qTNs8fmlRW+G4vd6ozcV7rGirPFVPu1arL59Zv6WTZj1/XesbDfLJc53d6WBrQj1zaj9LNU8qxcVepPC0/n4mC/SaNymuLpey5UqPWvCmTFH1P7LmnkzWXzvri8Av8AaYh4Kzd8dgf4a5ip0YrhJ/ir6yqtT+Q/bYx/u1zTySWEX+GYJ/vhisK3/dWD/hkZurSt9zl+WR1KX3uX5RMUJmE+DTXCvhX3V4lWHxS9SUZfNqxZi5UX8SftRjag/iy/JRPu9nxNzY6OM50qj8EzXPzsXadKSfbAqjR5Nr8Uu/JepiJr8aSJj1m5q34c4pPvsR+bfJ+5m/zlZr7vvd9n9JPtj1dOnL8T6iYyNSSXqVJR8WjJb9vukZL8JIvo/Gw8V3SaJem+VSPimY8EGnzoxfbFtGDUL3fnI+8y9DlUa+dF/oHpvSNSEvxvrJxGFk1pUi+9NEcJW4X7nczcZp60n4GD3b8JIxmI7RPShzkiuXWovwLeVtJX7ycfWivDQxwJeHVJeNybseU14qxWo34td6Ju39WUX4kwG5J8LPuZHFp8GvArhJcYv2ETkuDa8THcqCxmpy679+o3+yPsJiBraQ8TLeXyIi8fk+8gx16xftZbx5xftF4dUvaTAxv2sXfWX0e0no9b9gUv2k1K93rfsGluL9hBAXTr9xNP+kQNCF07Rp2kVLEZlddXvF+pATkQt+xEuyAwOIJuVt3rLhH2DefVH2EFzbmWvDLefVH2DffJR9hgUZkxDLffZ7Cqcuv3GC4lLtSmGW9L5TDlL5UvaYlV+ouZBuT4yftIWz6n7CqMn8WXsG+RLLqQMtyfyWPNy4uy75IbM8kzCcRoN3rnBeJVGPOpH2MuzJlNOoXMrU/lyfdEl6fVUfsQwZQIyU6f3tvvmVTXKlDxuxiOYxuVXb0TbL5yVtN1d0UHOfOcn4lxSm9fN1PkPx0Ju29acF43+gx0er1HcM0m9mlTT1nJ90frG9BcKd/nSua9Sjb5QYZurNeq1FfgxSMJNyd5tyfa7kKNqZ4kRgBlGE5cIt+A3LetOEfG/wBBcSZhiLmX2tc5S8LBTivVpx8dSY5yZSN3wTfcbPNy+NaHznYxdSbVnJ26lovcY6J8DL7sJvZ2prjUcvmx+sb8E/RpR75PeMANrkYZurUatvtLqjovcYaLVIBkmqZ4rEYUIsYTauotLreiLuwXrVV3RV/+RYpmUmYYjsM700tIOXbJ/oQ87O1lLdX4Kt/zLiO2U3ip1LX3d1dctEFGK9apfsgr/wDIx534vrF2XNMcIMSz3qa9WlftnL9CHnai0jJQ+akv+ZruB6SewxDJ+k7yu31t3CZDLclx3bLregjMklxfmLRXGp+SrlcoLhFvvZljnKI31hKUlaMWxvvkox7kSTcuLb8SblXdkn6TUe9ltBcZ3+ajXZciosVRHYYbb0l8Wb75JDfXKnDxuzWhcu2my2+dnycY90UPO1Pvk/B2NSuzJKXyWZRXVKbMMm2/WlJ97ZN2PUhbrlFeIvFcZvwRd/ahaPUl4F9HqQ3ofhv2Im9D5D8ZFyK2ur3F06l7DHfX3uPtY85bhCHsG1HMwy9HqXsMbR6o+wvnX8mH5JXUfVD8lF2oTelkuS8DOFScfVnNd0mY+dfVH8lFVV9UPyUZRVEdpMTLZ8IrpaV6v5TZfhNa+slL50Iv9Bq87+DD8kKovkU/YZxen9U+LHYjk3efb9ajQl3wt9DMo1qPxsNFfNnJfWaFUjzpQ9/1mXnKfOivCbNkXp5x3fsxmiOTY5YeXGFVfjKX0mdKdOH3LFV6X4rX0M0b9Hh5uS7p/wDIl6N/2xexmUXpicxjx/ZNjO5zHXqy9bE4at+6xV/a0bKVStH1aM120K7Xu1R4+1J/tkl3xLGFO91Wh4xaN1Ooqz+8fPLGbUYx8vJ5F15J/bJV0/8AKUov3rU5FLM68LbmKq/w9/dNHiouovUxKfYqn1mV6/OMZ/ip/QcmjV10cMtVViirjh5t5rWlH06Eaq65YeDv+Swsdh5RW/gaS7Y0ZfWjwW+0/SwyXddBVoX1VWPdO/0m+npOvtnPt/o1/Y6eyO553z+Ck7xnKk+pTlT+m6N1Nby+1YjEP5tSnU/QeBhX0ssRUt1TpqRXKnN+lHCzfc4M3RrqZ7I+HwywnS+v5vNV1Wj/AHTVj8/DQ+o48qlXnjMM/n4aH1HAjVqRdqca8F/ksQn9JmsXVStPE4iP7rRUhOptVb8THvn5zBFiY5d0eUt83Vk/RxGWvvpRRVHFW0nlj/Ep/WaY4u+jrYCf7pQ3SvcqL+x+Cq/uVRr9LMZmid8TPfPyyy2ZjjHhHzw2Sp5hxisC/mwpfWIwzZerGiu6FE404YaPr5VVh3Vn+mJrf2Nb1w2Jh+PB/SkceZx+ae+fnDZFOeyO6PNz9zPJKyckvwVSX0Ijw+eS0lVxFv8ASYxXuaOGoZa+eIj30YP/ANRl5nLn+3zXfhF+hmOZn80/8oWKIjsj/itbLMY3esqV+upiYfpZr+BTjxxeXQ78VD9Bk8PgU7rGpf7JInmcJyzJL/Zqn1Guqin6qhsiZ+olh8Gpr1s0wV/wd+f0Iqo4VccfKXzMNP8ATYydHCf4zi/9nqfUPMYL/GXswtT6jHEco7/3Zb+c937MXHAJXcsdU7LQgvpbDqZel6OArS7amL/QoldHAr++FV/Nwr/S0RU8u54rGv5uGj+mZjMT2RHhPxysY7ZnxYvEUkvteAwkO1qc372voM1mGLirU6sKS/ydGnH32uHHLUvXzCf4tOP/AKmRTy1ccLjanfiYx+iLEVVxwnHs3fBdmmezP162vEYvE4j7vjK9T51WTXsvY46VKLuo+KSRy5VcEr7uXJ9W/ipP6IoRxNOPqZdgV2yU5v3yNdUZnMz8fJlG7s+DiurTXxY37ZGdN1amlKk5vqjBs5UcdjF9yWHpfueHpp+1pslTF5jNbs8bXt1ee3V7FYRGe2e791+uJHLc3mt5YLEQj1zjuL2sxeX1I/d8XgqOuqniIyfsjdmh0lKV6lSm31ye8y7tOKt5126oxJNNPGfGVhvlhsHD18xdT9ww8n75WRjGeW0+OExVd9dSuqa9kU/pNLnQXKpJ9rSMHVo30jFvtbZjNURwxHj8cs4mXLeYU4O+Hy7L6Lta7pyqP+NK3uDzLNJR3YYmtTj1UYqkv4qRx6UcRWlu0KFWb5KnSf1HJWVZtPWWCq0485V2oL2yaETXVwz7v2ZRtS4laFSpPfrS3pPnUnvP33EYxjo6sF2K5ynl8oXdfMctorsxCm/ZC5PguWxjepm06j6qGFm/fPdMZpmOzvldmZ4uO3RT1lKXdG30mPnaK1VNvvl9RyX9h6drUsxxGmu9UhSXu3iLF5fC/msmoS7a9edT6N0wmr1wy2I7ZcOddcoU14CFWrN+hd9kY/Uc5ZnVTXwfB4DD2XxMKpX/AC94rzbNrNRxteknxVNqmv4qRjiJ7Z7v3XZpjtaaeX5pXjvQwWMnHrVOVjKWT5lB/bcMqC661SMPpZpr1MViPu+IrVf3Sq5fSzV5hLioLvsTFPKVjYjsb3l8o3dTH5fTtp/XCk/4tyxwuDUW55vRv1QoVJfosaN1JW85H2mLjT51L+BhOI4QsVU8nIVPLeeLxcn+BhkvpkjKMMqXPMKn8HH9LOMlSXOb8CqdLlGXtG1HqXa9TkuWWLhg8bLvxMV9ESOrl3LL6/jjP/0HHdSHKm/GRJTXyEvFk9JHq7oTbb3UwLfo4CX5y3/6TF1MHbTAyXb8If6poc+yJi6q6omM1x9RBtN0qmFfDCVV3Yj/APSTew33nEruqp/oNPnfm+0ecXUn4sx2o+oMttsO/wDCV+SybtDlWqR76X1M178epe0ech8n3k2o5Jlnuw+LioeKkv0BKb4VaEvx0Y78ep+0jlF8voG1A2ebrfer/NszCUWvXozXgT0L6R9xkpbukZzXc2MwME4Lg3F9jLvy+W/HU2OpLh5y/fFMxbi+Kpt/NaIMW1zUH4WFo9vg7ke78h+EyNR/DXgmYhur5S8U0RwfY+53Kl1TXimg4ytwUu5pkwMd2S4KSG/NfGfiV3WjUl4jefW/GzMcRAm8+cYvwsLx5xa7mW8fwfZYei+S8JEGPoW4yXgS0fle4ytHnGZPQvxfsMcCbqt68Ru9sfaW0eU/cSy+UhgRxfWn4k3X/wBMysvlRJu9q9pMKm4+oWZbd3tJw6yTAjTJqZX7yX7zHAnIFv2Ib2vBCVR2Gpb6ht8SCWYsw2Qgtu0EBFbd6XWN59Ys7EafUbcy17l3n1+4u9LrMbMWfUMyYhlvPr9w3pdZLMa9TLmUXfkubG9L5T9pjZ9RbO/Abw3n8p+0XfW/aN19TLuy+SxvEsuoWRVGXyWXclbh7xiTLHiXmXdfWvaFH8KK8S4kygMrR+XEegvjN9yGEyxLfUt4dUmRSj8j2yGPWomUby+TEKcutLuSG5BJt6Jl3J2va3foRyk/jP2mPEu4Z7qXGcV43LamucpdysYW1KMxyTDLeiuFNeLuPOTS0aXckjEF2pMQNuXrNy73ccgW0nyZN8iDQtratpF9BLi33IuzJlixzLvRXCHtZVN8rLuRMRzN5GM3ruvxLaPxpruWphdt6tvvBlmIN7PeguEW+92CqSXqtR+av0mHEIbU9iYhXdu7d31sXYLGMnwRN8qgMrW4yS94vBcE336FxPaZY3MkpPl7Q5O2ll3Iwd29dRuGy0VxkvDUl4rhFvvZiC7SYZb8lwdu5GL146gcxNUyYUcia3Lp1gLlJdLk2Xe0tohuCz5ItuuSRG78Xchcwi3iutl3uqKXvMbajmMjJzl127iMhS5mTALiwt2jeLfUcgt3tF49XvKFxcu91JEcm+bG4VKXUy7suowcm+YvoXMJhnuvrXtFvwo+0wbBdqORhnZcd9FSjzn7jWGXajkYbVufL9xVuc5+41Au3HJMNvofL9wtF/HXsNRUWK/UYbGo8pxLup/Hj7TVqXXqMorjkmGzcfKUX4jzc1wS8Ga7PqCv1F245GJbUq0eG+u5mSqVlxcn3xuartdaCnNfGl7TOLmOEyx2ct3nX8anTf4oVSHOkvCRqVSp8plVSXOz70Z+mme3wTZZN0n8WS9jM4zjFejWnHwZr841yj7COa+REypu4Jpy5G/fjVpy+cv+QtCXrRpPukcdShzg/wAozUqVtVNeJui9nt+vex2ccHIjGcX9qnOPzav/ADL5zGLjOtJL5VpL3o4z82/jvxiN1fFqR+gz9LymfdKbPP4OS8ROL+2UaEvnUbfRYLE0H62Bov5tSUfrNKeIXqVW+6dx56uvXipfOppibvP4RJFLd57CPR4Oou7EfXEKWBa1p4qPdOD+o1OvB+th6D8Gv0lVXDP1sLJfNqv9KHpYntju8oXZ9TNxwH33Fx76UX/6goYF/wB11l34b6pGEpYN8I4iH40ZfUY7mEa0r1Y/OpJ/Qy55RHf+697a6WEvpj/bh5hUcN/h9PxoVPqNSpUH/dkF86nJfoHweL9XGYV982vpQ38o7/3XHrn69zd5rD/4wo/wNT6gqWG/xhQ/gKv1GpYWd9K+EfdiI/WVYLEfFVGXza8H+kff/R8fMxHP4eTZ5vD/AOMafhh6n1EccMuOY1H83Dy/5E+A4zlh2+6UX+kLL8c/7krPuQxX+j4+a7Pr+HkNYK3pYzFy7FQS+mRL5fH/AA6f8HH9LMvsfjf8Cr/kk+AYz/Aq3joTYr/R4SsY5/A89l6/uTFy+dikvogPhWDivQyqjL91rVJfQ4keCxi/uOfjNE+B4vnh6cfnVY/pZjMXP0//AJ/ZlEwyePS1pZfl9N9lFy/lSZlDMswi70qipP8AyNCEPoia3hcStHUw0P8AaKa/ST4JUfrYzBrvxMX9BNm7HDPwZRlsq4zMq/3bGYyd/lVpfWcbzaTvJU0+uVrmx4WlFenmODXzXKX0RKqGDXrZin8zDTf02MZpq7fGYZbMtbUbfdV4Jk+1ri5v3G3cy1OzxONmvwcPFfTIy3srjww+Pq99eEPoizCYnnHf5EU+tx5Tp8qftkY+cS4RivA5ir5dH1cqUv3TFzf0JB4+lF3p5Vl1O3BuE5/ypGOzHbXHdPky2Y5uDKvJabyLCdao7QUpdkY3Od9lsZF3prCUv3PCUl9MWyTzfNJqzzLFJdUKm4vZGxhMUfqnu/dlijmwhlubVUtzAY2SfB+aaRk8ozJK9WnSo/u2IhD6WcWvWqVnetVq1X1zqOX0s1WguEY+xGGbcdk9/wCy5o5OZ9j1Ffbszy6n3Vt/+SmWGFy+KvVzdS7KWFnL6bHCb7SMm3TH5Y8Tap5OcoZOr3r5lV7qMIJ+2TIquVRemAxdT5+LS+iBwkOwxm5yiO7zNvlEOc8XgUrQyigu2depL6GifDqdvRy3L4/vc5fTM4XaQnpqvqIPSS5jx9R+rQwUe7DR/TcxWOxC4Oiu7D0/1Tii5PTV8z0lXNyvh+K5VIrupQX/AKTF47F/fv4kfqONdLmTeXWjH0lXOe9durm5LxuKf7c/yI/UT4XiPviffTj9Rx94l3fg/YTbr5yZqcj4TWfF0330o/UTz0nxhRf70jT6XyZewtpv4svYNutMy2ecX3mj+S1+kOdN8aEPCTRqakl6svYHfqfsJtTyN7Nyo86U13VPrRLUXwlVj3pMwb6yNrrMc+oZ7kOVZeMWhudVSm/xrGF+QuTagyzUJ8o37ncjjJcYNeBjp1IqbT0bXcxmBFJ8pP2l3nzs+9F35fKv3pMm9daxj7LEz6xG4v4vsZLR+U13or3PkvwZHuPm13oxkLdUl7bB76438VcbvVKL9xFGa4J+A3hvK+sV9A9Fv4y95W5Lj70S66l4EEaj8q3ehZcpIvo9qJZPhL2ogOPavaTdfZ7S7r60/Eln1EwDj/1cbruBoQFF9aJu87orStxJbtIpuvrXtLuvrJbtDXaQGutoltOIa7Q+8B2glu0BWwa3DaHiZMDxF+0tl1i3ahvQu+ti76xZixd4XfWxd9bFhZjeF32gtmhZjeMeYMrMWGJGJS27SW7RiTIhwZbLrKt1LixhGNgZXj2kUor4pcCeIMt5fJQ3nyshiBEnbQyUZdTJvS6xd8y7jeu6+xeIsubRiBmEwyvHtY3lbSPtMSobRhlvy5WXciNt8WyDUZkSxUVJjTmy4kygLp3i6XBEwBd2T5DefcRtviy7hbWeskLxXJsxCLnkYZ775JIjbfFtkHMbUpgBbMJdoxIcANO8b3UgCvcviY3ZU0XMBp3ht8gBkwXvzIUWADmGtR6IwBVcXJqEZWHeYluZZFuuol2QDIX62BYtiAhcFSSLgTUDQt11DAhbMm8LsuYFsy2XWjG+oT1LmEwy9HrL6Jhco2jDK8eoKXYYgbUmGe/3DfdjAF2pMQz331k35dZFcKLLE1JiF3pdZd+S5kSZUu1F+8m5VOXWPOS6yWXNi0esyiauZuVTl1lVSRFu9ZVudZYmrmmIXzkuz2F8476qPsJ6HWS0HzM9qrmmI5MvOL5MfYN6L+JbuZjaPWVKPyixVUYhfQ6pLxHo9bRLR+UN3tRnmeQrj1ST7yrfjwb8GYuL6yWl1jPqG3zlRW3lfvSZHUjzpw8NDDemiqo7awT8DL0nr7zCuVJr1ZLukN2k/jSXerjep84NeJPtT4SaLtZ5GF83B8K0fG6KqU/izg+6RHCPKpEOm3wafcyxjke9XQr/AHuT7tTF0qi9ajJd8SSjJP1WiqpVjwnNdzZM09sSsZYOKvrBL8UlodUfYb1ia6/bZ27XcvwmrfVxl3wi/wBBM0c57v3Xe0Wj8lewrjDnFew2uvJ8adF/vaHno/eKL/Ff1j7nMzLRuw+RH2Ddj8lew3OtC39b0vbJfpDq0v8ABoflyJinnHj5Msy1JIyUrczKVWk/7nS7psnnKX+Dr+EY+7H5o8fIwxcr8xczU6D/ALl/+qzJVMOuODT/AH2QxT+qPHyXDVcJo3Oth+WCh/CyHn6PLA0fGUn+kbNP6o8fJcetpuY73W0ch4iNvRwmFj+I39LJ8KmvVpYdfvKMZijtq8FxHNx99W1a9oUk9E0cn4bX5Oku6jD6i/D8ZyxM181JfQibNvnPd+64hohSq1H9rpVJfNg2bo5dmE/VwOKf700SWLxcuOKrv98f1mqc5z9ec5fOk2Y7NqOfh+7L7rkfYnMfjYdw+fOMfpZfsZWX3TE4Gn87Ex/Rc4bUV8VewXstFYZtR+We/wDZc08nL+BUE/tma4NfMU5fQh5nL4+tj60/3PDfWzib3aS5JuURwojx8zajk5b+xcVp8PqPtcI/WTzuXrhga0vnYn6onEZGzGbvKI7l25cx4rCr1MtoL51Wcv0ox+Fx+LgsHH97b+lnFZEzH01f1EJty5axtVaxp4Vd1CI+G4jlOEe6lFfoOKuNgiemuczbq5uS8ZiuWIn4JL9Bg8Xin/dNX2mpmNzH0lfOe82pbvhOI/wmq/xifCMQ+Ner+Uag2Tbq5mZbPhFf7/U/KHwiv9+n7TVr1MO9+DJtVczMtvwmtzqyfeYvEVXxkn3xRrs3yZbS+S/YTaq5mZZ+dk+Kg/xUPOq2tKm/A12fUxZ9TJtVGZZ78PvUPBsb1P72/wAo16gm1Jln9r6prxJaHymu9GNyXJn1DLcXKpEig+tPxJcEzArjPqZLNcmgVSl8p+0m4RSl1v2l3r8bPvQ35X1s/AbyfGKHvVG0/i+xk9Hk2itx6mvEj3etkC1+EkVbxjbtRbPkRC7JvLmi3a6xfrRFRuPaNOsadQVgFl1hrtDStxI0r8SA12kt2hpdYa7SAkBwBFW1i8wCsVJfUAqLzKwAJcXAGQuwAAuNQCoXYQADgXkARRAAqAQAURVqAVJVRb0uZbgBnEQwmUdkLrqAJVuWN42Ru4AyJqUAigsAAHaAA4mVtADKmGMpouQ3gBMrguAAHK5AAFygEFSuZWANlLGUuG9ACTKsWADECvgABLlALAySFtADZEMR6EbAMZlUuACZUHEAAOIBAegAKKuJUgDKEWxLrqALKLclwCZlcG8yXYBcmDUoAgCpgFQZQCoFQBYFSLYAzhJLaDduAbMQmUaa5ke8uYBjO5YTekuZkpsAxiurmuIZKXYX0XyAN9EzMb2M7hxRHGy4gGcxGEym9JfGZVUn137wDVNUxwllERK+cfOMX4F3o84LwYBIuVSy2YS9N/FkvEyUKb5yQByLdMVcYY1bl8xfhL3GMsPPlKIBlXaojsYRVLXOEocWjAA4VW6W2FTLvAEiRLi9wBkGyNsAxmWSXZd5gEzKm8xvAFyDkS4AyobYUpS4NAFiMymWyGCqzV1OHvMlgKjetSPsAOXRZonjCxI8C1xqL2GPwWC4ykwDObFuOxswqoUrXe8/ExapL9rb8QDTXRTHCFxDHfpLhRXizHz0FwpRAOLVXMcDB8J0sqcV4E+ES6l7ADCblXNijr1Otewxdep1gGO3VzTKedm/jB1Z29ZgEmqeYx85LrZVOXWATakTzkuTJ52XZ7ACbU4U3/wUVTT4wQBjtypeHyAtz5L9oBYlS0Op+0u5HtAM4iDCebvzJ5vtAGzCMdx9ZLWANcxCIS/UAayFu0VS60AMyo7PkSwBkILAECwsARUV2ACI/9k=" alt="SellerPulse" />
        <a href="{_auth_url}" class="ml-btn">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M13 10V3L4 14h7v7l9-11h-7z"/>
            </svg>
            Entrar com Mercado Livre
        </a>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# =========================
# NAVBAR (só aparece após login)
# =========================
st.markdown('<div class="navbar"><span style="font-size:20px;">🛒</span><span class="navbar-name">REOBOTE IMPORTS</span></div>', unsafe_allow_html=True)

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

    df_raw = parse_orders(orders, fretes, reembolsados, token=token)
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
    # Salva lucro no session_state para uso no ROI do Caixa
    if "lucro_acumulado" not in st.session_state or periodo == "Personalizar":
        st.session_state["lucro_acumulado"] = lucro_total

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

        if cancelada:
            frete_cancel = abs(row['Frete'])
            lucro_cancel = row['Lucro']  # já é negativo = -frete total
            cel_frete  = f'<span style="color:#DC2626;font-weight:700;">-R$ {frete_cancel:,.2f}</span>'
            cel_lucro  = f'<span style="color:#DC2626;font-weight:700;">-R$ {abs(lucro_cancel):,.2f}</span> <span style="background:#FEE2E2;color:#DC2626;border-radius:999px;padding:2px 9px;font-size:12px;font-weight:800;">prejuízo</span>'
        else:
            cel_frete = badge(row['Frete'], rec,'#DBEAFE','#1D4ED8')
            cel_lucro = margem_badge(row.get('Margem %',0), row.get('Lucro',0))

        linhas += f"""<tr style="background:{bg_row};border-bottom:1px solid #F1F5F9;">
            <td style="padding:10px 8px;font-weight:800;color:#7C3AED;white-space:nowrap;">{row['SKU']}</td>
            <td style="padding:10px 8px;color:#64748B;font-size:13px;white-space:nowrap;">{pd.to_datetime(row['Data']).strftime('%d/%m/%Y %H:%M')}</td>
            <td style="padding:10px 8px;font-size:18px;text-align:center;">{status_icon(row['Status'])}</td>
            <td style="padding:10px 8px;text-align:center;font-weight:700;">{int(row['Quantidade'])}</td>
            <td style="padding:10px 8px;font-weight:700;">{'<span style="color:#DC2626;font-weight:700;">-R$ ' + f'{rec:,.2f}</span>' if cancelada else badge(rec, fat_total,'#DCFCE7','#15803D')}</td>
            <td style="padding:10px 8px;">{cel_frete}</td>
            <td style="padding:10px 8px;">{'–' if cancelada else badge(row['Taxas ML'], rec,'#FEF3C7','#B45309')}</td>
            <td style="padding:10px 8px;">{'–' if cancelada else f'{tag_custo}{badge(row["Custo Total"], rec, "#EDE9FE","#6D28D9")}'}</td>
            <td style="padding:10px 8px;">{'–' if cancelada else badge(row['Imposto'], rec,'#F1F5F9','#475569')}</td>
            <td style="padding:10px 8px;text-align:center;">{cel_lucro}</td>
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

    CATEGORIAS_INTER = [
        "Transferência do ML", "Fornecedor", "Frete / Logística",
        "Mercado Ads", "Embalagem", "Operacional",
        "Impostos / Taxas", "Pró-labore / Retirada", "Outros",
    ]

    # ── Supabase helpers para extrato Inter ──
    def load_extrato(uid):
        sb = get_supabase()
        resp = sb.table("extrato_inter").select("*").eq("user_id", uid).order("data", desc=True).execute()
        if not resp.data:
            return pd.DataFrame(columns=["id","data","valor","memo","categoria","conciliado","observacao"])
        df = pd.DataFrame(resp.data)
        df["data"]       = pd.to_datetime(df["data"], errors="coerce")
        df["valor"]      = pd.to_numeric(df["valor"], errors="coerce").fillna(0)
        df["conciliado"] = df["conciliado"].astype(bool)
        return df

    def save_lancamento(uid, row):
        sb = get_supabase()
        sb.table("extrato_inter").upsert({"user_id": uid, **row}, on_conflict="user_id,id").execute()

    def update_lancamento(uid, lancamento_id, categoria, observacao, conciliado):
        sb = get_supabase()
        sb.table("extrato_inter").update({
            "categoria": categoria,
            "observacao": observacao,
            "conciliado": conciliado,
        }).eq("user_id", uid).eq("id", lancamento_id).execute()

    def load_agendamentos_inter(uid):
        sb = get_supabase()
        resp = sb.table("agendamentos_inter").select("*").eq("user_id", uid).order("data").execute()
        if not resp.data:
            return pd.DataFrame(columns=["id","data","valor","descricao","categoria","recorrente","pago"])
        df = pd.DataFrame(resp.data)
        df["data"]       = pd.to_datetime(df["data"], errors="coerce")
        df["valor"]      = pd.to_numeric(df["valor"], errors="coerce").fillna(0)
        df["pago"]       = df["pago"].astype(bool)
        df["recorrente"] = df["recorrente"].astype(bool)
        return df

    def save_agendamento(uid, row):
        sb = get_supabase()
        sb.table("agendamentos_inter").insert({"user_id": uid, **row}).execute()

    def parse_ofx(content_bytes):
        """Extrai lançamentos de um arquivo OFX/QFX."""
        import re
        text = content_bytes.decode("utf-8", errors="ignore")
        rows = []
        txs  = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.S)
        for tx in txs:
            def get(tag):
                pattern = r"<" + tag + r">([^<\n\r]+)"
                m = re.search(pattern, tx)
                return m.group(1).strip() if m else ""
            dt_raw = get("DTPOSTED")
            try:
                dt = pd.to_datetime(dt_raw[:8], format="%Y%m%d")
            except:
                dt = None
            valor = 0.0
            try:
                valor = float(get("TRNAMT").replace(",","."))
            except:
                pass
            fitid = get("FITID") or get("REFNUM") or dt_raw
            memo  = get("MEMO") or get("NAME") or ""
            rows.append({"id": fitid, "data": dt, "valor": valor, "memo": memo,
                         "categoria": "", "conciliado": False, "observacao": ""})
        return pd.DataFrame(rows)

    extrato_df    = load_extrato(str(user_id))
    capital_df    = load_capital(str(user_id))
    agend_df      = load_agendamentos_inter(str(user_id))

    # ── Cards de resumo ──
    entradas  = extrato_df["valor"].sum() if not extrato_df.empty and "valor" in extrato_df.columns else 0.0
    entradas  = extrato_df[extrato_df["valor"] > 0]["valor"].sum() if not extrato_df.empty else 0.0
    saidas    = extrato_df[extrato_df["valor"] < 0]["valor"].abs().sum() if not extrato_df.empty else 0.0
    saldo     = extrato_df["valor"].sum() if not extrato_df.empty else 0.0
    pendentes = extrato_df[~extrato_df["conciliado"]] if not extrato_df.empty and "conciliado" in extrato_df.columns else pd.DataFrame()
    a_pagar   = agend_df[~agend_df["pago"]]["valor"].abs().sum() if not agend_df.empty and "pago" in agend_df.columns else 0.0

    st.markdown(f"""
    <div class="hero" style="min-height:auto;padding:28px 38px;">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:24px;">
            <div>
                <div style="font-size:13px;font-weight:700;opacity:.85;">Banco Inter PJ</div>
                <div style="font-size:28px;font-weight:900;">Caixa Inter</div>
                <div style="margin-top:8px;">
                    {"<span style=\'background:rgba(255,255,255,.2);border-radius:999px;padding:4px 12px;font-size:12px;font-weight:800;\'>✅ Tudo conciliado</span>" if len(pendentes)==0 else f"<span style=\'background:#F59E0B;color:#1F2937;border-radius:999px;padding:4px 12px;font-size:12px;font-weight:800;\'>⚠️ {len(pendentes)} pendentes</span>"}
                </div>
            </div>
            <div style="display:flex;gap:32px;flex-wrap:wrap;">
                <div style="text-align:center;">
                    <div style="font-size:13px;font-weight:700;opacity:.85;">Entradas</div>
                    <div style="font-size:28px;font-weight:900;">R$ {entradas:,.2f}</div>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:13px;font-weight:700;opacity:.85;">Saídas</div>
                    <div style="font-size:28px;font-weight:900;">R$ {saidas:,.2f}</div>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:13px;font-weight:700;opacity:.85;">Saldo atual</div>
                    <div style="font-size:28px;font-weight:900;">R$ {saldo:,.2f}</div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(f"""<div class="kpi-card"><div class="kpi-title">Entradas</div><div class="kpi-value">R$ {entradas:,.2f}</div></div>""", unsafe_allow_html=True)
    k2.markdown(f"""<div class="kpi-card"><div class="kpi-title">Saídas</div><div class="kpi-value" style="color:#EF4444;">R$ {saidas:,.2f}</div></div>""", unsafe_allow_html=True)
    k3.markdown(f"""<div class="kpi-card"><div class="kpi-title">A Pagar</div><div class="kpi-value" style="color:#F59E0B;">R$ {a_pagar:,.2f}</div></div>""", unsafe_allow_html=True)
    k4.markdown(f"""<div class="kpi-card"><div class="kpi-title">Saldo após pagamentos</div><div class="kpi-value" style="color:#7C3AED;">R$ {saldo - a_pagar:,.2f}</div></div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Upload OFX ──
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**📂 Importar Extrato Inter (OFX)**")
    st.caption("Importe o extrato do Banco Inter PJ em formato OFX. Lançamentos novos são adicionados automaticamente sem duplicar.")
    ofx_file = st.file_uploader("Selecione o arquivo OFX", type=["ofx","qfx"], key="ofx_upload")
    if ofx_file:
        novos_df = parse_ofx(ofx_file.read())
        ids_existentes = set(extrato_df["id"].astype(str)) if not extrato_df.empty else set()
        novos = novos_df[~novos_df["id"].astype(str).isin(ids_existentes)]
        if novos.empty:
            st.info("Nenhum lançamento novo encontrado — extrato já importado.")
        else:
            st.success(f"{len(novos)} novos lançamentos encontrados.")
            if st.button(f"✅ Importar {len(novos)} lançamentos", type="primary"):
                for _, row in novos.iterrows():
                    save_lancamento(str(user_id), {
                        "id":          str(row["id"]),
                        "data":        row["data"].strftime("%Y-%m-%d") if pd.notna(row["data"]) else None,
                        "valor":       float(row["valor"]),
                        "memo":        str(row["memo"]),
                        "categoria":   "",
                        "conciliado":  False,
                        "observacao":  "",
                    })
                st.success("Extrato importado!")
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Conciliação do Extrato ──
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="small-title">Conciliação do Extrato</div>', unsafe_allow_html=True)
    st.caption("Categorize cada lançamento. Entradas em verde, saídas em vermelho. Marque como conciliado após identificar.")

    if extrato_df.empty:
        st.info("Nenhum lançamento importado ainda. Faça upload do OFX acima.")
    else:
        # Filtros
        fc1, fc2 = st.columns(2)
        with fc1:
            filtro_status = st.radio("Filtrar por:", ["Todos", "Pendentes", "Conciliados"],
                                     horizontal=True, key="filtro_conciliacao")
        with fc2:
            filtro_cat = st.selectbox("Categoria:", ["Todas"] + CATEGORIAS_INTER, key="filtro_cat")

        df_show = extrato_df.copy()
        if filtro_status == "Pendentes":
            df_show = df_show[~df_show["conciliado"]]
        elif filtro_status == "Conciliados":
            df_show = df_show[df_show["conciliado"]]
        if filtro_cat != "Todas":
            df_show = df_show[df_show["categoria"] == filtro_cat]

        st.markdown(f"**{len(df_show)} lançamentos**")

        # Tabela de conciliação
        for idx, row in df_show.iterrows():
            cor_val = "#16A34A" if row["valor"] >= 0 else "#DC2626"
            sinal   = "+" if row["valor"] >= 0 else ""
            concil  = row["conciliado"]
            bg      = "#F0FDF4" if concil else "white"

            with st.container():
                c1, c2, c3, c4, c5 = st.columns([1.2, 1, 3, 2, 1.5])
                with c1:
                    st.markdown(f"<div style='padding:8px 0;font-size:13px;color:#64748B;'>{pd.to_datetime(row['data']).strftime('%d/%m/%Y') if pd.notna(row['data']) else '–'}</div>", unsafe_allow_html=True)
                with c2:
                    st.markdown(f"<div style='padding:8px 0;font-weight:800;color:{cor_val};'>{sinal}R$ {abs(row['valor']):,.2f}</div>", unsafe_allow_html=True)
                with c3:
                    st.markdown(f"<div style='padding:8px 0;font-size:13px;color:#0F172A;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;'>{str(row['memo'])[:60]}</div>", unsafe_allow_html=True)
                with c4:
                    cat_sel = st.selectbox("", [""] + CATEGORIAS_INTER,
                                           index=([""] + CATEGORIAS_INTER).index(row["categoria"]) if row["categoria"] in CATEGORIAS_INTER else 0,
                                           key=f"cat_{row['id']}", label_visibility="collapsed")
                with c5:
                    concil_btn = st.checkbox("✅ Conciliado", value=bool(concil), key=f"conc_{row['id']}")

                obs_key = f"obs_{row['id']}"
                obs_val = st.text_input("", value=str(row["observacao"] or ""),
                                        placeholder="Observação (opcional)",
                                        key=obs_key, label_visibility="collapsed")

                if cat_sel != row["categoria"] or concil_btn != concil or obs_val != str(row["observacao"] or ""):
                    update_lancamento(str(user_id), str(row["id"]), cat_sel, obs_val, concil_btn)
                    st.rerun()

                st.markdown("<hr style='margin:4px 0;border:none;border-top:1px solid #F1F5F9;'>", unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Agendamentos ──
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="small-title">Agendamentos</div>', unsafe_allow_html=True)

    with st.expander("➕ Novo agendamento"):
        with st.form("form_agend", clear_on_submit=True):
            ag1, ag2, ag3 = st.columns(3)
            with ag1:
                ag_data  = st.date_input("Data vencimento", value=date.today())
                ag_valor = st.number_input("Valor (R$)", step=0.01, format="%.2f")
            with ag2:
                ag_desc  = st.text_input("Descrição")
                ag_cat   = st.selectbox("Categoria", CATEGORIAS_INTER)
            with ag3:
                ag_rec   = st.checkbox("Recorrente (mensal)")
            if st.form_submit_button("💾 Agendar", type="primary", use_container_width=True):
                if ag_valor == 0 or not ag_desc:
                    st.error("Informe valor e descrição.")
                else:
                    save_agendamento(str(user_id), {
                        "data": ag_data.strftime("%Y-%m-%d"),
                        "valor": ag_valor,
                        "descricao": ag_desc,
                        "categoria": ag_cat,
                        "recorrente": ag_rec,
                        "pago": False,
                    })
                    st.success("Agendamento salvo!")
                    st.rerun()

    if not agend_df.empty:
        for _, ag in agend_df[~agend_df["pago"]].iterrows():
            venc = pd.to_datetime(ag["data"])
            dias = (venc - pd.Timestamp.now()).days
            cor  = "#EF4444" if dias < 0 else "#F59E0B" if dias <= 3 else "#64748B"
            ac1, ac2, ac3, ac4 = st.columns([1.5, 3, 1.5, 1])
            with ac1:
                st.markdown(f"<div style='color:{cor};font-weight:800;font-size:13px;padding:6px 0;'>{venc.strftime('%d/%m/%Y')}</div>", unsafe_allow_html=True)
            with ac2:
                st.markdown(f"<div style='padding:6px 0;font-size:13px;'>{ag['descricao']} <span style='color:#94A3B8;'>{ag['categoria']}</span></div>", unsafe_allow_html=True)
            with ac3:
                st.markdown(f"<div style='color:#EF4444;font-weight:800;padding:6px 0;'>R$ {abs(ag['valor']):,.2f}</div>", unsafe_allow_html=True)
            with ac4:
                if st.button("✅ Pago", key=f"pago_{ag['id']}"):
                    get_supabase().table("agendamentos_inter").update({"pago": True}).eq("id", str(ag["id"])).execute()
                    st.rerun()
    else:
        st.info("Nenhum agendamento pendente.")
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Capital Investido ──
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="small-title">Capital Investido & ROI</div>', unsafe_allow_html=True)

    with st.expander("➕ Registrar aporte / retirada"):
        with st.form("form_capital", clear_on_submit=True):
            cap1, cap2 = st.columns(2)
            with cap1:
                cap_data  = st.date_input("Data", value=date.today())
                cap_valor = st.number_input("Valor (R$) — negativo para retirada", step=0.01, format="%.2f")
            with cap2:
                cap_desc = st.text_input("Descrição", placeholder="ex: Compra lote S_001")
                cap_cat  = st.selectbox("Categoria", ["Compra de estoque","Taxa/Tarifa","Retirada","Aporte","Outro"])
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
                    st.success(f"R$ {cap_valor:.2f} registrado.")
                    st.rerun()

    if not capital_df.empty:
        total_inv   = capital_df[capital_df["valor"] > 0]["valor"].sum()
        total_ret   = capital_df[capital_df["valor"] < 0]["valor"].abs().sum()
        saldo_cap   = capital_df["valor"].sum()
        # Lucro acumulado — usa session_state se disponível (calculado na aba financeiro)
        lucro_acum  = st.session_state.get("lucro_acumulado", 0.0)
        roi         = (lucro_acum / total_inv * 100) if total_inv > 0 else 0.0
        cor_roi     = "#16A34A" if roi >= 0 else "#DC2626"

        # Calcula estoque em caixa
        sb = get_supabase()
        estoq_resp = sb.table("custos_sku").select("sku,custo_produto,qtd_disponivel").eq("user_id", str(user_id)).gt("qtd_disponivel", 0).execute()
        estoque_caixa = sum(float(r["qtd_disponivel"]) * float(r["custo_produto"]) for r in (estoq_resp.data or []))
        qtd_estoque   = sum(float(r["qtd_disponivel"]) for r in (estoq_resp.data or []))

        # Média de vendas 15 dias — só conta qtd, NÃO processa FIFO
        import zoneinfo as _tz
        _agora = datetime.now(_tz.ZoneInfo("America/Sao_Paulo"))
        _d15   = _agora - timedelta(days=15)
        _from  = _d15.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
        _to    = _agora.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
        with st.spinner("Calculando média de vendas (15 dias)..."):
            _orders15 = get_orders(str(user_id), token, _from, _to)
        qtd_15d = sum(
            int(item.get("quantity", 1) or 1)
            for o in _orders15 if o.get("status") != "cancelled"
            for item in o.get("order_items", [])
        )
        media_diaria = qtd_15d / 15
        dias_estoque = int(qtd_estoque / media_diaria) if media_diaria > 0 else 0
        cor_dias = "#16A34A" if dias_estoque >= 20 else "#F59E0B" if dias_estoque >= 10 else "#DC2626"

        ci1, ci2, ci3 = st.columns(3)
        ci1.markdown(f"""<div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:16px;padding:24px;text-align:center;">
            <div style="font-size:11px;font-weight:800;color:#64748B;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Total Investido</div>
            <div style="font-size:32px;font-weight:900;color:#0F172A;letter-spacing:-1px;">R$ {total_inv:,.2f}</div>
        </div>""", unsafe_allow_html=True)
        ci2.markdown(f"""<div style="background:#FFF7ED;border:1px solid #FED7AA;border-radius:16px;padding:24px;text-align:center;">
            <div style="font-size:11px;font-weight:800;color:#C2410C;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Estoque em Caixa</div>
            <div style="font-size:32px;font-weight:900;color:#0F172A;letter-spacing:-1px;">R$ {estoque_caixa:,.2f}</div>
            <div style="font-size:12px;color:#92400E;margin-top:4px;font-weight:600;">{int(qtd_estoque)} unidades disponíveis</div>
        </div>""", unsafe_allow_html=True)
        ci3.markdown(f"""<div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:16px;padding:24px;text-align:center;">
            <div style="font-size:11px;font-weight:800;color:#64748B;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Dias de Estoque</div>
            <div style="font-size:40px;font-weight:900;color:{cor_dias};letter-spacing:-1px;">{dias_estoque}</div>
            <div style="font-size:12px;color:#64748B;margin-top:4px;font-weight:600;">média {media_diaria:.1f} un/dia (15d)</div>
        </div>""", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # Tabela de lançamentos estilizada
        linhas_cap = ""
        for _, row in capital_df.sort_values("data", ascending=False).iterrows():
            val  = float(row["valor"])
            cor  = "#16A34A" if val >= 0 else "#DC2626"
            sinal = "+" if val >= 0 else ""
            data_fmt = pd.to_datetime(row["data"]).strftime("%d/%m/%Y") if pd.notna(row["data"]) else "–"
            linhas_cap += f"""
            <tr style="border-bottom:1px solid #F1F5F9;">
                <td style="padding:14px 12px;color:#64748B;font-size:13px;">{data_fmt}</td>
                <td style="padding:14px 12px;font-weight:600;color:#0F172A;">{row.get('descricao','')}</td>
                <td style="padding:14px 12px;">
                    <span style="background:#EDE9FE;color:#6D28D9;border-radius:999px;padding:3px 10px;font-size:12px;font-weight:700;">
                        {row.get('categoria','')}
                    </span>
                </td>
                <td style="padding:14px 12px;text-align:right;font-weight:800;color:{cor};">{sinal}R$ {abs(val):,.2f}</td>
            </tr>"""

        st.markdown(f"""
        <div style="border-radius:16px;border:1px solid #E7ECF5;overflow:hidden;">
            <table style="width:100%;border-collapse:collapse;font-family:'Inter',sans-serif;font-size:14px;">
                <thead>
                    <tr style="background:#F8FAFC;border-bottom:2px solid #E2E8F0;">
                        <th style="padding:12px;text-align:left;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">Data</th>
                        <th style="padding:12px;text-align:left;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">Descrição</th>
                        <th style="padding:12px;text-align:left;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">Categoria</th>
                        <th style="padding:12px;text-align:right;color:#64748B;font-size:11px;font-weight:800;text-transform:uppercase;">Valor</th>
                    </tr>
                </thead>
                <tbody>{linhas_cap}</tbody>
            </table>
        </div>
        """, unsafe_allow_html=True)

    else:
        st.info("Nenhum lançamento registrado ainda. Use o formulário acima para começar.")
    st.markdown('</div>', unsafe_allow_html=True)
