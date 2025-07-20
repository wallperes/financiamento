import streamlit as st
import pandas as pd
import datetime
import sgs

# Função para obter uma série temporal individual com tratamento de erros
@st.cache_data(show_spinner=False)
def obter_serie_sgs(codigo_serie: int, data_inicio: datetime.date, data_fim: datetime.date) -> pd.Series:
    try:
        # Converter datas para o formato dd/mm/YYYY exigido pela biblioteca
        data_inicio_str = data_inicio.strftime('%d/%m/%Y')
        data_fim_str = data_fim.strftime('%d/%m/%Y')
        
        # Obter a série temporal
        serie = sgs.time_serie(codigo_serie, start=data_inicio_str, end=data_fim_str)
        
        # Verificar se o retorno é uma Series válida
        if isinstance(serie, pd.Series):
            return serie
        else:
            # Se não for uma Series, provavelmente é uma mensagem de erro
            st.error(f"Resposta inesperada para série {codigo_serie}: {str(serie)[:100]}...")
            return pd.Series()
            
    except Exception as e:
        st.error(f"Exceção ao buscar a série {codigo_serie}: {str(e)}")
        return pd.Series()

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
with st.spinner("🔄 Carregando dados do Banco Central..."):
    serie_incc = obter_serie_sgs(codigo_incc, data_inicio, data_fim)
    serie_ipca = obter_serie_sgs(codigo_ipca, data_inicio, data_fim)

# Processamento dos dados
if not serie_incc.empty and not serie_ipca.empty:
    # Cria DataFrame combinado
    df = pd.DataFrame({
        "INCC-M (%)": serie_incc,
        "IPCA (%)": serie_ipca
    })
    
    # Preenche valores ausentes
    df = df.fillna(0.0)
    
    st.success(f"✅ Dados carregados com sucesso para o período {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}!")

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
    
    # Exibir informações básicas sobre as séries
    st.subheader("ℹ️ Informações sobre as Séries")
    st.markdown(f"""
    - **INCC-M ({codigo_incc})**: Índice Nacional de Custo da Construção - Mercado (FGV)
    - **IPCA ({codigo_ipca})**: Índice Nacional de Preços ao Consumidor Amplo (IBGE)
    
    *Dados obtidos diretamente do Sistema Gerenciador de Séries Temporais (SGS) do Banco Central do Brasil*
    """)
    
    # Estatísticas básicas
    st.subheader("📊 Estatísticas Descritivas")
    st.dataframe(df.describe().style.format("{:.2f}"))
    
else:
    st.warning("⚠️ Não foi possível obter os dados de uma ou ambas as séries. Verifique sua conexão e os parâmetros.")
    
    # Exibir informações de debug
    st.subheader("🔍 Informações para Depuração")
    st.write(f"Status da série INCC-M ({codigo_incc}): {'Dados encontrados' if not serie_incc.empty else 'Série vazia'}")
    st.write(f"Status da série IPCA ({codigo_ipca}): {'Dados encontrados' if not serie_ipca.empty else 'Série vazia'}")
    st.write(f"Intervalo solicitado: {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}")
    st.write("Dica: Verifique se os códigos das séries estão corretos e se as datas estão no formato DD/MM/AAAA")
