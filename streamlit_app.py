import streamlit as st
import sgs
import pandas as pd

st.title("Teste de Conexão com Banco Central")

# Parâmetros de busca
with st.expander("Parâmetros de Busca", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        codigo_incc = st.number_input("Código INCC", value=192)
        data_inicio = st.text_input("Data Início (DD/MM/AAAA)", "01/01/2023")
    with col2:
        codigo_ipca = st.number_input("Código IPCA", value=433)
        data_fim = st.text_input("Data Fim (DD/MM/AAAA)", "31/12/2023")

# Botão para buscar dados
if st.button("Buscar Dados do BC", type="primary"):
    st.subheader("Resultado da Busca")
    
    try:
        # Tentar buscar os dados
        with st.spinner("Buscando dados no Banco Central..."):
            df = sgs.dataframe([codigo_incc, codigo_ipca], start=data_inicio, end=data_fim)
        
        if df.empty:
            st.warning("⚠️ Nenhum dado encontrado!")
            st.stop()
            
        st.success(f"✅ {len(df)} registros encontrados!")
        
        # Mostrar dados brutos
        st.subheader("Dados Brutos")
        st.dataframe(df)
        
        # Mostrar informações técnicas
        st.subheader("Informações Técnicas")
        st.json({
            "Tipo do DataFrame": str(type(df)),
            "Colunas": df.columns.tolist(),
            "Tipo das Colunas": dict(df.dtypes),
            "Exemplo de Índice": str(df.index[0]) if len(df) > 0 else "N/A",
            "Formato do Índice": str(type(df.index))
        })
        
        # Verificar valores ausentes
        st.subheader("Valores Ausentes")
        st.write("Total de valores ausentes por coluna:")
        st.write(df.isnull().sum())
        
        # Mostrar estatísticas básicas
        st.subheader("Estatísticas Básicas")
        st.write(df.describe())
        
    except Exception as e:
        st.error(f"❌ Erro na busca: {str(e)}")
        st.markdown("""
        **Solução de problemas:**
        1. Verifique sua conexão com a internet
        2. Confira os códigos das séries temporais
        3. Valide o formato das datas (DD/MM/AAAA)
        4. Tente um período menor
        """)

# Informações de ajuda
st.markdown("---")
st.subheader("Informações Úteis")
st.markdown("""
**Códigos padrão:**
- INCC: 192 (National Index of Building Costs)
- IPCA: 433 (Índice Nacional de Preços ao Consumidor Amplo)

**Formato de datas:**
- Use DD/MM/AAAA (ex: 01/01/2023)
- O sistema do BC aceita datas desde 01/01/1980

**Problemas comuns:**
- Períodos muito longos podem demorar
- Algumas séries têm atualização mensal
- Feriados/finais de semana não têm dados
""")
