import streamlit as st
import pandas as pd
import datetime
from sgs import SGS

# Função para obter uma série temporal do SGS
@st.cache_data(show_spinner=False)
def obter_serie_sgs(codigo_serie: int, data_inicio: datetime.date) -> pd.DataFrame:
    try:
        sgs = SGS()
        # CORREÇÃO: usar fetch() em vez de data()
        serie = sgs.fetch(codigo_serie, start=data_inicio)
        # Converter para DataFrame e processar
        dados = pd.DataFrame(serie)
        dados.columns = [codigo_serie]
        dados.index = pd.to_datetime(dados.index)
        dados = dados.fillna(0.0)
        return dados
    except Exception as e:
        st.error(f"Erro ao buscar a série {codigo_serie}: {e}")
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

# Obtém dados
df_incc = obter_serie_sgs(codigo_incc, data_inicio)
df_ipca = obter_serie_sgs(codigo_ipca, data_inicio)

# Processamento dos dados
if not df_incc.empty and not df_ipca.empty:
    # Converter datas para Timestamp sem timezone
    start = pd.Timestamp(data_inicio)
    end = pd.Timestamp(data_fim) + pd.Timedelta(days=1)
    
    # Filtra os dados
    df_incc = df_incc.loc[start:end]
    df_ipca = df_ipca.loc[start:end]
    
    # Combina as séries
    df = pd.concat([
        df_incc.rename(columns={codigo_incc: "INCC-M (%)"}),
        df_ipca.rename(columns={codigo_ipca: "IPCA (%)"})
    ], axis=1)

    st.success("✅ Séries carregadas com sucesso!")

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
else:
    st.warning("⚠️ Não foi possível obter os dados de uma ou ambas as séries.")
