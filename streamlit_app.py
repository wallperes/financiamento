import streamlit as st
import pandas as pd
import datetime
import sgs  # Importar diretamente o módulo sgs

# Função para obter múltiplas séries temporais do SGS
@st.cache_data(show_spinner=False)
def obter_series_sgs(codigos: list, data_inicio: datetime.date, data_fim: datetime.date) -> pd.DataFrame:
    try:
        # Usar a função dataframe() para obter múltiplas séries
        df = sgs.dataframe(codigos, start=data_inicio, end=data_fim)
        df.index = pd.to_datetime(df.index)
        return df.fillna(0.0)
    except Exception as e:
        st.error(f"Erro ao buscar as séries: {str(e)}")
        return pd.DataFrame()

# Configuração da interface Streamlit
st.set_page_config(page_title="Índices Econômicos", layout="centered")
st.title("📊 Consulta de Índices Econômicos - SGS / Banco Central")

st.markdown("""
Este aplicativo acessa os dados diretamente do **SGS (Sistema Gerenciador de Séries Temporais)** do Banco Central do Brasil para exibir as variações mensais de:

- **INCC-M** (Índice Nacional de Custo da Construção - FGV)
- **IPCA** (Índice Nacional de Preços ao Consumidor Amplo - IBGE)
""")

# Define intervalo de datas
data_inicio = st.sidebar.date_input("Data inicial", value=datetime.date(2021, 1, 1))
data_fim = st.sidebar.date_input("Data final", value=datetime.date.today())

# Verificação de datas válidas
if data_inicio > data_fim:
    st.sidebar.error("Erro: Data inicial maior que data final!")

# Códigos das séries
codigo_incc = 7456  # INCC-M mensal
codigo_ipca = 433   # IPCA mensal
codigos_series = [codigo_incc, codigo_ipca]

# Obtém dados
with st.spinner("🔄 Carregando dados do Banco Central..."):
    df = obter_series_sgs(codigos_series, data_inicio, data_fim)

# Processamento dos dados
if not df.empty:
    # Renomear colunas para nomes amigáveis
    df = df.rename(columns={
        codigo_incc: "INCC-M (%)",
        codigo_ipca: "IPCA (%)"
    })
    
    st.success(f"✅ Dados carregados com sucesso para o período {data_inicio} a {data_fim}!")

    # Exibe tabela
    st.subheader("📅 Variações mensais")
    st.dataframe(df.style.format({"INCC-M (%)": "{:.2f}", "IPCA (%)": "{:.2f}"}), use_container_width=True)

    # Gráfico
    st.subheader("📈 Evolução temporal")
    st.line_chart(df)
    
    # Adiciona download de dados
    csv = df.to_csv().encode('utf-8')
    st.download_button(
        label="📥 Download dos dados (CSV)",
        data=csv,
        file_name="indices_economicos.csv",
        mime="text/csv"
    )
    
    # Exibir metadados
    st.subheader("ℹ️ Metadados das Séries")
    try:
        metadados = sgs.metadata(df)
        for meta in metadados:
            st.markdown(f"""
            **Série {meta['code']}**: {meta['name']}
            - **Fonte**: {meta['source']}
            - **Unidade**: {meta['unit']}
            - **Frequência**: {meta['frequency']}
            - **Período**: {meta['first_value'].strftime('%Y-%m-%d')} a {meta['last_value'].strftime('%Y-%m-%d')}
            """)
    except Exception as e:
        st.warning(f"Não foi possível obter metadados: {str(e)}")
else:
    st.warning("⚠️ Não foi possível obter os dados das séries. Verifique sua conexão e os parâmetros.")
