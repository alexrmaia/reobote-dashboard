
import json

st.subheader("Teste API - Simulação de Frete")

if st.button("Consultar frete de R$ 255"):
    token = get_token()
    user_id = st.session_state.get("user_id")

    if not token or not user_id:
        st.error("Conta Mercado Livre não autenticada.")
    else:
        url = (
            f"{ML_API_BASE}/users/{user_id}"
            "/shipping_options/free"
        )

        params = {
            "dimensions": "9x7x13,340",
            "item_price": 255,
            "listing_type_id": "gold_special",
            "mode": "me2",
            "condition": "new",
            "logistic_type": "drop_off",
            "free_shipping": "true",
            "verbose": "true",
        }

        try:
            response = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}"
                },
                params=params,
                timeout=30,
            )

            st.write("HTTP:", response.status_code)

            # Não exibir headers ou token.
            st.json(response.json())

        except requests.RequestException as erro:
            st.error(f"Erro na consulta: {erro}")
        except ValueError:
            st.error("Resposta não está em formato JSON.")
