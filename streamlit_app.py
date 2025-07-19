import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io

def simular_financiamento(
    valor_total_imovel,
    valor_entrada,
    entrada_parcelada,
    entrada_mensal,
    meses_pre,
    meses_pos,
    incc_medio,
    ipca_medio,
    juros_mensal,
    parcelas_mensais_pre,
    parcelas_semestrais,
    parcelas_anuais,
    valor_amortizacao_pos,
    percentual_minimo_quitacao=0.3
):
    # Inicialização do saldo devedor
    saldo_devedor = valor_total_imovel - valor_entrada if not entrada_parcelada else valor_total_imovel
    
    # Lista para armazenar todas as parcelas futuras
    parcelas_futuras = []
    historico = []
    total_amortizado_pre = 0

    # 1. Construir lista de parcelas futuras para fase pré-chaves
    for mes in range(1, meses_pre + 1):
        valor_parcela = parcelas_mensais_pre
        
        # Adicionar parcelas especiais se existirem neste mês
        if mes in parcelas_semestrais:
            valor_parcela += parcelas_semestrais[mes]
        if mes in parcelas_anuais:
            valor_parcela += parcelas_anuais[mes]
        
        # Adicionar parcela de entrada se aplicável
        if entrada_parcelada and mes <= (valor_entrada / entrada_mensal):
            valor_parcela += entrada_mensal
        
        if valor_parcela > 0:
            parcelas_futuras.append({
                'mes': mes,
                'valor_original': valor_parcela,
                'correcao_acumulada': 0.0,
                'tipo': 'pre'
            })

    # 2. Fase pré-chaves
    for mes in range(1, meses_pre + 1):
        # Calcular correção do período
        correcao_mes = saldo_devedor * incc_medio
        saldo_devedor += correcao_mes

        # Distribuir correção entre parcelas futuras
        if parcelas_futuras:
            total_valor_original = sum(p['valor_original'] for p in parcelas_futuras)
            for parcela in parcelas_futuras:
                proporcao = parcela['valor_original'] / total_valor_original
                parcela['correcao_acumulada'] += correcao_mes * proporcao

        # Encontrar parcelas vencendo neste mês
        parcelas_vencidas = [p for p in parcelas_futuras if p['mes'] == mes and p['tipo'] == 'pre']
        pagamento_total = 0
        amortizacao_total = 0
        correcao_paga_total = 0
        
        for parcela in parcelas_vencidas:
            # Calcular valor total a pagar (original + correção acumulada)
            pagamento_parcela = parcela['valor_original'] + parcela['correcao_acumulada']
            pagamento_total += pagamento_parcela
            amortizacao_total += parcela['valor_original']
            correcao_paga_total += parcela['correcao_acumulada']
            
            # Remover parcela da lista de futuras
            parcelas_futuras.remove(parcela)
        
        # Atualizar saldo devedor
        saldo_devedor -= amortizacao_total
        saldo_devedor = max(saldo_devedor, 0)
        total_amortizado_pre += amortizacao_total

        # Registrar no histórico
        historico.append({
            'Mês': mes,
            'Fase': 'Pré',
            'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento_total,
            'Amortização Base': amortizacao_total,
            'Correção Paga (R$)': correcao_paga_total,
            'Juros (R$)': 0,
            'Ajuste INCC (R$)': correcao_mes,
            'Ajuste IPCA (R$)': 0
        })

    # 3. Verificação de quitação mínima
    valor_quitado = total_amortizado_pre + (0 if entrada_parcelada else valor_entrada)
    percentual_quitado = valor_quitado / valor_total_imovel
    if percentual_quitado < percentual_minimo_quitacao:
        st.warning(f"Atenção: valor quitado na pré ({valor_quitado:,.2f}) equivale a {percentual_quitado*100:.2f}% do valor do imóvel, abaixo de {percentual_minimo_quitacao*100:.0f}%.")

    # 4. Construir parcelas futuras para fase pós-chaves
    for mes in range(1, meses_pos + 1):
        mes_global = meses_pre + mes
        parcelas_futuras.append({
            'mes': mes_global,
            'valor_original': valor_amortizacao_pos,
            'correcao_acumulada': 0.0,
            'tipo': 'pos'
        })

    # 5. Fase pós-chaves
    for mes in range(1, meses_pos + 1):
        mes_global = meses_pre + mes
        
        # Calcular correção do período
        correcao_mes = saldo_devedor * ipca_medio
        saldo_devedor += correcao_mes

        # Distribuir correção entre parcelas futuras
        if parcelas_futuras:
            total_valor_original = sum(p['valor_original'] for p in parcelas_futuras)
            for parcela in parcelas_futuras:
                proporcao = parcela['valor_original'] / total_valor_original
                parcela['correcao_acumulada'] += correcao_mes * proporcao

        # Encontrar parcelas vencendo neste mês
        parcelas_vencidas = [p for p in parcelas_futuras if p['mes'] == mes_global and p['tipo'] == 'pos']
        pagamento_total = 0
        amortizacao_total = 0
        juros_total = 0
        correcao_paga_total = 0
        
        for parcela in parcelas_vencidas:
            # Calcular juros sobre saldo atualizado
            juros_parcela = saldo_devedor * juros_mensal
            
            # Calcular valor total a pagar (amortização + juros + correção)
            pagamento_parcela = parcela['valor_original'] + juros_parcela + parcela['correcao_acumulada']
            pagamento_total += pagamento_parcela
            amortizacao_total += parcela['valor_original']
            juros_total += juros_parcela
            correcao_paga_total += parcela['correcao_acumulada']
            
            # Remover parcela da lista de futuras
            parcelas_futuras.remove(parcela)
        
        # Atualizar saldo devedor
        saldo_devedor -= amortizacao_total
        saldo_devedor = max(saldo_devedor, 0)

        # Registrar no histórico
        historico.append({
            'Mês': mes_global,
            'Fase': 'Pós',
            'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento_total,
            'Amortização Base': amortizacao_total,
            'Correção Paga (R$)': correcao_paga_total,
            'Juros (R$)': juros_total,
            'Ajuste INCC (R$)': 0,
            'Ajuste IPCA (R$)': correcao_mes
        })

    return pd.DataFrame(historico)

# ------------------------------
# Interface Streamlit
# ------------------------------

st.title("Simulador de Financiamento Imobiliário 🚧🏠")

st.sidebar.header("Parâmetros Gerais")

valor_total_imovel = st.sidebar.number_input("Valor total do imóvel", value=455750.0)
valor_entrada = st.sidebar.number_input("Valor de entrada total", value=22270.54)

entrada_parcelada = st.sidebar.checkbox("Entrada parcelada?", value=False)
entrada_mensal = 0
if entrada_parcelada:
    entrada_mensal = st.sidebar.number_input("Valor mensal da entrada", value=5000.0)

meses_pre = st.sidebar.number_input("Meses de pré-chaves", value=17)
meses_pos = st.sidebar.number_input("Meses de pós-chaves", value=100)
incc_medio = st.sidebar.number_input("INCC médio mensal", value=0.0046, step=0.0001, format="%.4f")
ipca_medio = st.sidebar.number_input("IPCA médio mensal", value=0.0046, step=0.0001, format="%.4f")
juros_mensal = st.sidebar.number_input("Juros remuneratórios mensal", value=0.01, step=0.001, format="%.3f")

parcelas_mensais_pre = st.sidebar.number_input("Parcela mensal pré (R$)", value=3983.38)
valor_amortizacao_pos = st.sidebar.number_input("Amortização mensal pós (R$)", value=3104.62)

st.sidebar.subheader("Parcelas Semestrais")
parcelas_semestrais = {}
for i in range(2):  # Exemplo: 2 semestrais
    mes = st.sidebar.number_input(f"Mês semestral {i+1}", value=6 * (i+1), key=f"sem_{i}")
    valor = st.sidebar.number_input(f"Valor semestral {i+1} (R$)", value=6000.0, key=f"sem_val_{i}")
    parcelas_semestrais[mes] = valor

st.sidebar.subheader("Parcelas Anuais")
parcelas_anuais = {}
for i in range(1):  # Exemplo: 1 anual
    mes = st.sidebar.number_input(f"Mês anual {i+1}", value=17, key=f"anu_{i}")
    valor = st.sidebar.number_input(f"Valor anual {i+1} (R$)", value=43300.0, key=f"anu_val_{i}")
    parcelas_anuais[mes] = valor

if st.button("Simular"):
    df_resultado = simular_financiamento(
        valor_total_imovel,
        valor_entrada,
        entrada_parcelada,
        entrada_mensal,
        meses_pre,
        meses_pos,
        incc_medio,
        ipca_medio,
        juros_mensal,
        parcelas_mensais_pre,
        parcelas_semestrais,
        parcelas_anuais,
        valor_amortizacao_pos
    )

    st.subheader("Tabela de Simulação Detalhada")
    
    # Reordenar colunas para melhor visualização
    col_order = [
        'Mês', 'Fase', 'Saldo Devedor', 
        'Parcela Total', 'Amortização Base', 'Correção Paga (R$)', 'Juros (R$)', 
        'Ajuste INCC (R$)', 'Ajuste IPCA (R$)'
    ]
    df_display = df_resultado[col_order]
    
    # Formatar valores para melhor visualização
    format_mapping = {
        'Saldo Devedor': '{:,.2f}',
        'Parcela Total': '{:,.2f}',
        'Amortização Base': '{:,.2f}',
        'Correção Paga (R$)': '{:,.2f}',
        'Juros (R$)': '{:,.2f}',
        'Ajuste INCC (R$)': '{:,.2f}',
        'Ajuste IPCA (R$)': '{:,.2f}'
    }
    
    for col, fmt in format_mapping.items():
        df_display[col] = df_display[col].apply(lambda x: fmt.format(x))
    
    st.dataframe(df_display)

    st.subheader("Gráficos")

    fig, ax = plt.subplots(1, 2, figsize=(16, 6))

    ax[0].plot(df_resultado['Mês'], df_resultado['Saldo Devedor'], label='Saldo Devedor', color='blue')
    ax[0].set_title("Evolução do Saldo Devedor")
    ax[0].set_xlabel("Mês")
    ax[0].set_ylabel("R$")
    ax[0].grid(True)
    ax[0].legend()

    # Gráfico com composição da parcela
    ax[1].bar(df_resultado['Mês'], df_resultado['Amortização Base'], label='Amortização Base', color='green')
    ax[1].bar(df_resultado['Mês'], df_resultado['Correção Paga (R$)'], bottom=df_resultado['Amortização Base'], 
             label='Correção Paga', color='orange')
    ax[1].bar(df_resultado['Mês'], df_resultado['Juros (R$)'], 
             bottom=df_resultado['Amortização Base'] + df_resultado['Correção Paga (R$)'], 
             label='Juros', color='red')
    ax[1].set_title("Composição da Parcela Total")
    ax[1].set_xlabel("Mês")
    ax[1].set_ylabel("R$")
    ax[1].grid(True)
    ax[1].legend()

    st.pyplot(fig)

    st.subheader("Resumo da Composição da Parcela")
    st.write("""
    - **Amortização Base**: Valor original da parcela sem correção
    - **Correção Paga**: Valor da correção (INCC/IPCA) diluída que está sendo paga no mês
    - **Juros**: Juros remuneratórios aplicados (apenas na fase pós)
    """)

    # Exportar para Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_resultado.to_excel(writer, index=False, sheet_name='Simulação')
    excel_data = output.getvalue()

    st.download_button(
        label="💾 Baixar tabela completa (Excel)",
        data=excel_data,
        file_name='simulacao_financiamento.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
