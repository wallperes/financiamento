import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta  # Para avançar meses corretamente
import math
import sgs

# ============================================
# FUNÇÕES UTILITÁRIAS
# ============================================

def format_currency(value):
    """Formata valores no padrão brasileiro R$ 1.234,56"""
    if pd.isna(value) or value == 0:
        return "R$ 0,00"
    abs_value = abs(value)
    formatted = f"{abs_value:,.2f}"
    parts = formatted.split('.')
    integer_part = parts[0].replace(',', '.')
    decimal_part = parts[1] if len(parts) > 1 else "00"
    decimal_part = decimal_part.ljust(2, '0')[:2]
    sign = "-" if value < 0 else ""
    return f"{sign}R$ {integer_part},{decimal_part}"

def construir_parcelas_futuras(params):
    """Cria lista de parcelas futuras considerando parcelas mensais, semestrais, anuais e entrada parcelada."""
    parcelas = []
    valor_entrada = params['valor_entrada']
    entrada_mensal = params['entrada_mensal']

    # Calcula número de parcelas para a entrada parcelada
    if params['entrada_parcelada'] and entrada_mensal > 0:
        num_parcelas = math.ceil(valor_entrada / entrada_mensal)
    else:
        num_parcelas = 0

    # Parcelas do período pré-chaves
    for mes in range(1, params['meses_pre'] + 1):
        valor_parcela = params['parcelas_mensais_pre']
        # Adiciona parcelas semestrais e anuais extras
        if mes in params['parcelas_semestrais']:
            valor_parcela += params['parcelas_semestrais'][mes]
        if mes in params['parcelas_anuais']:
            valor_parcela += params['parcelas_anuais'][mes]
        # Adiciona parte da entrada parcelada
        if params['entrada_parcelada'] and mes <= num_parcelas:
            if mes < num_parcelas:
                valor_parcela += entrada_mensal
            else:
                # Última parcela (talvez menor)
                parcela_restante = valor_entrada - entrada_mensal * (num_parcelas - 1)
                valor_parcela += parcela_restante

        if valor_parcela > 0:
            parcelas.append({
                'mes': mes,
                'valor_original': valor_parcela,
                'correcao_acumulada': 0.0,
                'tipo': 'pre'
            })

    # Parcelas do período pós-chaves (amortizações fixas mensais)
    for mes in range(1, params['meses_pos'] + 1):
        mes_global = params['meses_pre'] + mes
        parcelas.append({
            'mes': mes_global,
            'valor_original': params['valor_amortizacao_pos'],
            'correcao_acumulada': 0.0,
            'tipo': 'pos'
        })
    return parcelas

def calcular_correcao(saldo, mes, fase, params, valores_reais):
    """
    Calcula a correção monetária do mês (INCC no pré, IPCA no pós).
    Se houver valores_reais, usa-os; caso contrário, aplica índices médios.
    """
    limite = params.get('limite_correcao')
    if valores_reais:
        # Usa índice real se disponível
        if mes in valores_reais:
            idx = valores_reais[mes]
            if fase == 'Pré' and idx.get('incc') is not None:
                return saldo * idx['incc']
            elif fase == 'Pós' and idx.get('ipca') is not None:
                return saldo * idx['ipca']
        # Se passou do limite ou não há índice real para este mês, não corrige
        if limite is not None and mes > limite:
            return 0
        return 0
    else:
        # Sem índices reais: usa INCC/IPCA médios até o limite definido
        if limite is not None and mes > limite:
            return 0
        if fase == 'Pré':
            return saldo * params.get('incc_medio', 0)
        else:
            return saldo * params.get('ipca_medio', 0)

def processar_parcelas_vencidas(parcelas_futuras, mes_atual):
    """Soma parcelas vencidas no mês atual e remove-as da lista futura."""
    vencidas = [p for p in parcelas_futuras if p['mes'] == mes_atual]
    pagamento = amortizacao = correcao_paga = 0.0
    for parcela in vencidas:
        pagamento += parcela['valor_original'] + parcela['correcao_acumulada']
        amortizacao += parcela['valor_original']
        correcao_paga += parcela['correcao_acumulada']
        parcelas_futuras.remove(parcela)
    return pagamento, amortizacao, correcao_paga

def verificar_quitacao_pre(params, total_amortizado):
    """Verifica se quitado mínimo (30%) foi atingido no final do pré-obra."""
    valor_quitado = (0 if params['entrada_parcelada'] else params['valor_entrada']) + total_amortizado
    percentual = valor_quitado / params['valor_total_imovel']
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(valor_quitado)
        st.warning(
            f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, "
            f"abaixo de {params['percentual_minimo_quitacao']*100:.0f}%."
        )

# ============================================
# SIMULAÇÃO PRINCIPAL
# ============================================

def simular_financiamento(params, valores_reais=None):
    """
    Executa a simulação completa, mês a mês, aplicando correções e juros.
    Retorna um DataFrame com o histórico detalhado.
    """
    # Saldo inicial (desconta a entrada imediata se não parcelada)
    saldo_devedor = params['valor_total_imovel'] - (0 if params['entrada_parcelada'] else params['valor_entrada'])
    if params['entrada_parcelada']:
        saldo_devedor = params['valor_total_imovel']

    parcelas_futuras = construir_parcelas_futuras(params)
    historico = []
    total_amortizado_pre = 0
    total_meses = params['meses_pre'] + params['meses_pos']

    for mes_atual in range(1, total_meses + 1):
        fase = 'Pré' if mes_atual <= params['meses_pre'] else 'Pós'
        saldo_inicial = saldo_devedor

        # Calcula correção monetária do mês
        correcao_mes = calcular_correcao(saldo_devedor, mes_atual, fase, params, valores_reais)
        saldo_devedor += correcao_mes

        # Diluição da correção nas parcelas futuras
        if parcelas_futuras and correcao_mes != 0:
            total_original = sum(p['valor_original'] for p in parcelas_futuras)
            for p in parcelas_futuras:
                p['correcao_acumulada'] += correcao_mes * (p['valor_original'] / total_original)

        # Calcula juros (apenas no período pós-obra)
        juros_mes = saldo_inicial * params['juros_mensal'] if fase == 'Pós' else 0

        # Processa pagamento das parcelas vencidas
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas(parcelas_futuras, mes_atual)
        saldo_devedor -= (amortizacao + correcao_paga)
        saldo_devedor = max(saldo_devedor, 0)

        if fase == 'Pré':
            total_amortizado_pre += amortizacao

        # Armazena histórico
        historico.append({
            'Mês': mes_atual,
            'Fase': fase,
            'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento + juros_mes,
            'Amortização Base': amortizacao,
            'Correção INCC ou IPCA diluída (R$)': correcao_paga,
            'Juros (R$)': juros_mes,
            'Ajuste INCC (R$)': correcao_mes if fase == 'Pré' else 0,
            'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós' else 0
        })

        # Verifica quitação mínima no fim do pré
        if fase == 'Pré' and mes_atual == params['meses_pre']:
            verificar_quitacao_pre(params, total_amortizado_pre)

    return pd.DataFrame(historico)

# ============================================
# INTEGRAÇÃO COM SGS (ÍNDICES DO BC)
# ============================================

def buscar_indices_bc(mes_inicial, meses_total):
    """
    Obtém os índices INCC (código 192) e IPCA (código 433) para o período.
    Itera mês a mês (primeiro dia de cada mês) usando relativedelta:contentReference[oaicite:2]{index=2} para compatibilidade.
    """
    try:
        data_inicio = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_fim = data_inicio + relativedelta(months=meses_total)
        start_str = data_inicio.strftime("%d/%m/%Y")
        end_str = data_fim.strftime("%d/%m/%Y")

        # Busca série no SGS do Bacen (valores em porcentagem)
        df = sgs.dataframe([192, 433], start=start_str, end=end_str)
        df = df.rename(columns={192: 'incc', 433: 'ipca'})
        # Converte para razão (ex: 0.31% -> 0.0031)
        df['incc'] = df['incc'] / 100
        df['ipca'] = df['ipca'] / 100

        indices = {}
        current_date = data_inicio
        for mes in range(1, meses_total + 1):
            if current_date in df.index:
                row = df.loc[current_date]
                incc_val = None if pd.isna(row['incc']) else row['incc']
                ipca_val = None if pd.isna(row['ipca']) else row['ipca']
            else:
                incc_val = None
                ipca_val = None
            indices[mes] = {'incc': incc_val, 'ipca': ipca_val}
            # Avança para o próximo mês (usa relativedelta:contentReference[oaicite:3]{index=3})
            current_date += relativedelta(months=1)

        # Exibição de depuração
        st.subheader("Dados Capturados do Banco Central")
        if not df.empty:
            st.write(f"Período: {start_str} a {end_str} -- {len(df)} registros")
            st.write("Primeiros registros:")
            st.dataframe(df.head().style.format({'incc': '{:.4%}', 'ipca': '{:.4%}'}))
            st.write("Últimos registros:")
            st.dataframe(df.tail().style.format({'incc': '{:.4%}', 'ipca': '{:.4%}'}))
        else:
            st.warning("Nenhum dado encontrado para o período")
        return indices
    except Exception as e:
        st.error(f"Erro ao acessar dados do BC: {e}")
        st.info("Verifique: 1) conexão internet 2) formato MM/AAAA")
        return {}

# ============================================
# INTERFACE STREAMLIT (entrada de parâmetros e exibição)
# ============================================

def criar_parametros():
    st.sidebar.header("Parâmetros Gerais")
    params = {
        'mes_inicial': st.sidebar.text_input("Mês inicial (MM/AAAA)", value="01/2023"),
        'valor_total_imovel': st.sidebar.number_input("Valor total do imóvel", value=455750.0),
        'valor_entrada': st.sidebar.number_input("Valor de entrada", value=22270.54),
        'entrada_parcelada': st.sidebar.checkbox("Entrada parcelada?", value=False),
        'entrada_mensal': 0,
        'meses_pre': st.sidebar.number_input("Meses pré-chaves", value=17),
        'meses_pos': st.sidebar.number_input("Meses pós-chaves", value=100),
        'incc_medio': st.sidebar.number_input("INCC médio mensal", value=0.00544640781, step=0.0001, format="%.4f"),
        'ipca_medio': st.sidebar.number_input("IPCA médio mensal", value=0.00466933642, step=0.0001, format="%.4f"),
        'juros_mensal': st.sidebar.number_input("Juros mensal", value=0.01, step=0.001, format="%.3f"),
        'parcelas_mensais_pre': st.sidebar.number_input("Parcela mensal pré (R$)", value=3983.38),
        'valor_amortizacao_pos': st.sidebar.number_input("Amortização mensal pós (R$)", value=3104.62),
        'parcelas_semestrais': {},
        'parcelas_anuais': {},
        'percentual_minimo_quitacao': 0.3,
        'limite_correcao': None
    }
    if params['entrada_parcelada']:
        params['entrada_mensal'] = st.sidebar.number_input("Valor mensal da entrada", value=5000.0)

    st.sidebar.subheader("Parcelas Semestrais")
    for i in range(2):
        mes = st.sidebar.number_input(f"Mês semestral {i+1}", value=6*(i+1), key=f"sem_{i}")
        valor = st.sidebar.number_input(f"Valor semestral {i+1} (R$)", value=6000.0, key=f"sem_val_{i}")
        if mes > 0 and valor > 0:
            params['parcelas_semestrais'][int(mes)] = valor

    st.sidebar.subheader("Parcelas Anuais")
    for i in range(1):
        mes = st.sidebar.number_input(f"Mês anual {i+1}", value=17, key=f"anu_{i}")
        valor = st.sidebar.number_input(f"Valor anual {i+1} (R$)", value=43300.0, key=f"anu_val_{i}")
        if mes > 0 and valor > 0:
            params['parcelas_anuais'][int(mes)] = valor

    params['fonte_indices'] = st.sidebar.radio("Fonte dos índices:", ['Valores Médios', 'Banco Central'])
    return params

def criar_editor_indices(total_meses):
    st.subheader("Valores Reais de Índices")
    df = pd.DataFrame(index=range(1, total_meses + 1), columns=['INCC', 'IPCA'])
    df.index.name = 'Mês'
    return st.data_editor(df.fillna(0.0), use_container_width=True, height=min(300, 35 * total_meses + 40))

def mostrar_resultados(df_resultado):
    st.subheader("Tabela de Simulação Detalhada")
    colunas = ['Mês', 'Fase', 'Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)',
               'Correção INCC ou IPCA diluída (R$)', 'Amortização Base', 'Juros (R$)', 'Parcela Total']
    df_display = df_resultado[colunas].copy()
    for col in colunas[2:]:
        df_display[col] = df_display[col].apply(format_currency)
    st.dataframe(df_display)

    st.subheader("Gráficos")
    fig, axs = plt.subplots(1, 2, figsize=(16, 6))
    axs[0].plot(df_resultado['Mês'], df_resultado['Saldo Devedor'], 'b-', label='Saldo Devedor')
    axs[0].set_title("Evolução do Saldo Devedor")
    axs[0].set_xlabel("Mês")
    axs[0].set_ylabel("R$")
    axs[0].grid(True)

    base_amort = df_resultado['Amortização Base']
    base_correcao = base_amort + df_resultado['Correção INCC ou IPCA diluída (R$)']
    axs[1].bar(df_resultado['Mês'], df_resultado['Amortização Base'], label='Amortização')
    axs[1].bar(df_resultado['Mês'], df_resultado['Correção INCC ou IPCA diluída (R$)'], bottom=base_amort, label='Correção')
    axs[1].bar(df_resultado['Mês'], df_resultado['Juros (R$)'], bottom=base_correcao, label='Juros')
    axs[1].set_title("Composição das Parcelas")
    axs[1].set_xlabel("Mês")
    axs[1].set_ylabel("R$")
    axs[1].legend()
    axs[1].grid(True)

    st.pyplot(fig)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_resultado.to_excel(writer, index=False)
    st.download_button(
        label="💾 Baixar tabela completa (Excel)",
        data=output.getvalue(),
        file_name='simulacao_financiamento.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def main():
    st.title("Simulador de Financiamento Imobiliário 🚧🏠")
    params = criar_parametros()
    total_meses = params['meses_pre'] + params['meses_pos']
    edited_df = criar_editor_indices(total_meses)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Simular com Parâmetros Médios"):
            params['limite_correcao'] = None
            st.session_state.df_resultado = simular_financiamento(params)
    with col2:
        limite_correcao = st.number_input("Aplicar correção até o mês:", min_value=1, max_value=total_meses, value=params['meses_pre'])
        if st.button("Simular Parcial"):
            params['limite_correcao'] = limite_correcao
            st.session_state.df_resultado = simular_financiamento(params)
    with col3:
        if st.button("Simular com Valores Reais"):
            if params['fonte_indices'] == 'Banco Central':
                valores_reais = buscar_indices_bc(params['mes_inicial'], total_meses)
            else:
                valores_reais = {}
                for mes, row in edited_df.iterrows():
                    if row['INCC'] != 0 or row['IPCA'] != 0:
                        valores_reais[mes] = {'incc': row['INCC'], 'ipca': row['IPCA']}
            st.session_state.df_resultado = simular_financiamento(params, valores_reais)

    if 'df_resultado' in st.session_state:
        mostrar_resultados(st.session_state.df_resultado)

if __name__ == "__main__":
    main()
