# ... (código anterior permanece igual) ...

def mostrar_resultados(df_resultado):
    """
    Exibe resultados da simulação (sem gráficos)
    """
    st.subheader("Tabela de Simulação Detalhada")
    
    # Definir ordem e seleção de colunas para visualização e exportação
    colunas = [
        'Mês/Data', 
        'Fase', 
        'Saldo Devedor', 
        'Ajuste INCC (R$)', 
        'Ajuste IPCA (R$)', 
        'Correção INCC ou IPCA diluída (R$)', 
        'Amortização Base', 
        'Juros (R$)', 
        'Parcela Total'
    ]
    
    # Criar DataFrame para visualização (com formatação)
    df_display = df_resultado[colunas].copy()
    
    # Formatar colunas numéricas para visualização
    for col in colunas[2:]:
        df_display[col] = df_display[col].apply(format_currency)
    
    st.dataframe(df_display)
    
    # Armazenar DataFrame para exportação (sem formatação, mesmo conjunto de colunas)
    st.session_state.df_export = df_resultado[colunas].copy()

# ... (restante do código permanece igual) ...

# Modificar a seção de download para usar o mesmo DataFrame formatado
if 'df_resultado' in st.session_state:
    mostrar_resultados(st.session_state.df_resultado)
    
    # Botão de download da planilha (usando o mesmo DataFrame formatado)
    if 'df_export' in st.session_state:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state.df_export.to_excel(writer, index=False)
        st.download_button(
            label="💾 Baixar planilha de simulação",
            data=output.getvalue(),
            file_name='simulacao_financiamento.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    
    # ... (código de índices permanece igual) ...
