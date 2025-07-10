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
    valor_parcela_pos,
    percentual_minimo_quitacao=0.3
):
    saldo_devedor = valor_total_imovel - valor_entrada if not entrada_parcelada else valor_total_imovel

    historico = []
    total_amortizado_pre = 0

    # Fase pré-chaves
    for m in range(1, meses_pre + 1):
        incc_valor = saldo_devedor * incc_medio
        saldo_devedor += incc_valor

        amortizacao_mes = parcelas_mensais_pre

        if entrada_parcelada and m <= (valor_entrada // entrada_mensal if entrada_mensal > 0 else 0):
            amortizacao_mes += entrada_mensal

        if m in parcelas_semestrais:
            amortizacao_mes += parcelas_semestrais[m]

        if m in parcelas_anuais:
            amortizacao_mes += parcelas_anuais[m]

        saldo_devedor -= amortizacao_mes
        saldo_devedor = max(saldo_devedor, 0)
        total_amortizado_pre += amortizacao_mes

        historico.append({
            'Fase': 'Pré',
            'Mês': m,
            'Saldo Devedor': saldo_devedor,
            'Parcela': amortizacao_mes,
            'Amortização': amortizacao_mes,
            'Juros': 0,
            'Ajuste INCC (R$)': incc_valor,
            'Ajuste IPCA (R$)': 0
        })

    valor_quitado = total_amortizado_pre
    if valor_quitado < percentual_minimo_quitacao * valor_total_imovel:
        st.warning(f"Atenção: valor quitado na pré ({valor_quitado:,.2f}) não atingiu {percentual_minimo_quitacao*100:.0f}% do valor do imóvel.")

    # Fase pós-chaves
    for m in range(1, meses_pos + 1):
        ipca_valor = saldo_devedor * ipca_medio
        saldo_devedor += ipca_valor

        juros = saldo_devedor * juros_mensal
        amortizacao = max(valor_parcela_pos - juros, 0)
        saldo_devedor -= amortizacao
        saldo_devedor = max(saldo_devedor, 0)

        historico.append({
            'Fase': 'Pós',
            'Mês': meses_pre + m,
            'Saldo Devedor': saldo_devedor,
            'Parcela': valor_parcela_pos,
            'Amortização': amortizacao,
            'Juros': juros,
            'Ajuste INCC (R$)': 0,
            'Ajuste IPCA (R$)': ipca_valor
        })

    df = pd.DataFrame(historico)
    return df

# ------------------------------
# Interface Streamlit
# ------------------------------

st.title("Simulador de Financiamento Imobiliário 🚧🏠")

st.sidebar.header("Parâmetros Gerais")

valor_total_imovel = st.sidebar.number_input("Valor total do imóvel", value=445000.0)
valor_entrada = st.sidebar.number_input("Valor de entrada total", value=23000.0)

entrada_parcelada = st.sidebar.checkbox("Entrada parcelada?", value=False)
entrada_mensal = 0
if entrada_parcelada:
    entrada_mensal = st.sidebar.number_input("Valor mensal da entrada", value=5000.0)

meses_pre = st.sidebar.number_input("Meses de pré-chaves", value=18)
meses_pos = st.sidebar.number_input("Meses de pós-chaves", value=100)
incc_medio = st.sidebar.number_input("INCC médio mensal", value=0.0046, step=0.0001, format="%.4f")
ipca_medio = st.sidebar.number_input("IPCA médio mensal", value=0.0046, step=0.0001, format="%.4f")
juros_mensal = st.sidebar.number_input("Juros remuneratórios mensal", value=0.01, step=0.001, format="%.3f")

parcelas_mensais_pre = st.sidebar.number_input("Parcela mensal pré (R$)", value=3983.38)
valor_parcela_pos = st.sidebar.number_input("Parcela mensal pós (R$)", value=3104.62)

st.sidebar.subheader("Parcelas Semestrais")
parcelas_semestrais = {}
for i in range(2):  # Exemplo: 2 semestrais
    mes = st.sidebar.number_input(f"Mês semestral {i+1}", value=6 * (i+1), key=f"sem_{i}")
    valor = st.sidebar.number_input(f"Valor semestral {i+1} (R$)", value=6000.0, key=f"sem_val_{i}")
    parcelas_semestrais[mes] = valor

st.sidebar.subheader("Parcelas Anuais")
parcelas_anuais = {}
for i in range(1):  # Exemplo: 1 anual
    mes = st.sidebar.number_input(f"Mês anual {i+1}", value=18, key=f"anu_{i}")
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
        valor_parcela_pos
    )

    st.subheader("Tabela de Simulação (todos os meses)")
    st.dataframe(df_resultado)

    st.subheader("Gráficos")

    fig, ax = plt.subplots(1, 2, figsize=(16, 6))

    ax[0].plot(df_resultado['Mês'], df_resultado['Saldo Devedor'], label='Saldo Devedor', color='blue')
    ax[0].set_title("Evolução do Saldo Devedor")
    ax[0].set_xlabel("Mês")
    ax[0].set_ylabel("R$")
    ax[0].grid(True)
    ax[0].legend()

    ax[1].plot(df_resultado['Mês'], df_resultado['Parcela'], label='Parcela Total', color='black')
    ax[1].plot(df_resultado['Mês'], df_resultado['Amortização'], label='Amortização', color='green')
    ax[1].plot(df_resultado['Mês'], df_resultado['Juros'], label='Juros', color='red')
    ax[1].set_title("Evolução da Parcela")
    ax[1].set_xlabel("Mês")
    ax[1].set_ylabel("R$")
    ax[1].grid(True)
    ax[1].legend()

    st.pyplot(fig)

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
