import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import sgs
from dateutil.relativedelta import relativedelta

# ============================================
# FUNÇÕES UTILITÁRIAS
# ============================================

def format_currency(value):
    """Formata valores no padrão brasileiro R$ 1.234,56"""
    if pd.isna(value) or not isinstance(value, (int, float)):
        return "R$ 0,00"
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def converter_juros_anual_para_mensal(taxa_anual):
    """Converte uma taxa de juros anual para a taxa efetiva mensal."""
    if taxa_anual <= -1: return -1
    return (1 + taxa_anual)**(1/12) - 1

# ============================================
# LÓGICA DE CÁLCULO ORIGINAL (CENÁRIO 1: CONSTRUTORA)
# Todas as funções foram renomeadas com _original para garantir a preservação.
# ============================================

def construir_parcelas_futuras_original(params):
    parcelas = []
    num_parcelas_entrada = params['num_parcelas_entrada'] if params['tipo_pagamento_entrada'] == 'Parcelada' else 0
    
    for mes in range(1, num_parcelas_entrada + 1):
        parcelas.append({'mes': mes, 'valor_original': params['entrada_mensal'], 'correcao_acumulada': 0.0, 'tipo': 'entrada'})
    
    for mes in range(num_parcelas_entrada + 1, num_parcelas_entrada + 1 + params['meses_pre']):
        valor_parcela = params['parcelas_mensais_pre']
        mes_local = mes - num_parcelas_entrada
        if mes_local in params.get('parcelas_semestrais', {}):
             valor_parcela += params['parcelas_semestrais'][mes_local]
        if mes_local in params.get('parcelas_anuais', {}):
             valor_parcela += params['parcelas_anuais'][mes_local]
        if valor_parcela > 0:
            parcelas.append({'mes': mes, 'valor_original': valor_parcela, 'correcao_acumulada': 0.0, 'tipo': 'pre'})
            
    for mes in range(num_parcelas_entrada + 1 + params['meses_pre'], num_parcelas_entrada + 1 + params['meses_pre'] + params['meses_pos']):
        parcelas.append({'mes': mes, 'valor_original': params['valor_amortizacao_pos'], 'correcao_acumulada': 0.0, 'tipo': 'pos'})
        
    return parcelas

def calcular_correcao_original(saldo, mes, fase, params):
    if fase in ['Assinatura', 'Carência']: return 0
    inicio_correcao = params.get('inicio_correcao', 1)
    if mes < inicio_correcao: return 0
    if fase in ['Entrada', 'Pré']: return saldo * params['incc_medio']
    elif fase == 'Pós': return saldo * params['ipca_medio']
    return 0

def processar_parcelas_vencidas_original(parcelas_futuras, mes_atual):
    vencidas = [p for p in parcelas_futuras if p['mes'] == mes_atual]
    pagamento_total, amortizacao_total, correcao_paga_total = 0, 0, 0
    for parcela in vencidas:
        pagamento_parcela = parcela['valor_original'] + parcela['correcao_acumulada']
        pagamento_total += pagamento_parcela
        amortizacao_total += parcela['valor_original']
        correcao_paga_total += parcela['correcao_acumulada']
        parcelas_futuras.remove(parcela)
    return pagamento_total, amortizacao_total, correcao_paga_total

def simular_financiamento_original(params):
    historico = []
    try:
        data_assinatura = datetime.strptime(params['mes_assinatura'], "%m/%Y")
        data_primeira_parcela = datetime.strptime(params['mes_primeira_parcela'], "%m/%Y")
    except ValueError:
        st.error("Datas inválidas na simulação da Construtora! Use o formato MM/AAAA.")
        return pd.DataFrame()

    saldo_devedor = params['valor_total_imovel']
    amortizacao_assinatura = 0
    if params['tipo_pagamento_entrada'] == 'Paga no ato':
        amortizacao_assinatura = params['valor_entrada']
        saldo_devedor -= amortizacao_assinatura
    
    historico.append({'DataObj': data_assinatura, 'Fase': 'Assinatura', 'Parcela Total': amortizacao_assinatura, 'Custo Acumulado': amortizacao_assinatura})

    meses_carencia = (data_primeira_parcela.year - data_assinatura.year) * 12 + (data_primeira_parcela.month - data_assinatura.month)
    data_corrente_carencia = data_assinatura
    saldo_temp_carencia = saldo_devedor
    total_correcao_carencia = 0
    for i in range(meses_carencia):
        data_corrente_carencia += relativedelta(months=1)
        correcao_mes_carencia = calcular_correcao_original(saldo_temp_carencia, 0, 'Carência', params)
        total_correcao_carencia += correcao_mes_carencia
        saldo_temp_carencia += correcao_mes_carencia

    parcelas_futuras = construir_parcelas_futuras_original(params)
    if total_correcao_carencia > 0 and parcelas_futuras:
        total_original = sum(p['valor_original'] for p in parcelas_futuras)
        if total_original > 0:
            for p in parcelas_futuras:
                p['correcao_acumulada'] += total_correcao_carencia * (p['valor_original'] / total_original)

    num_parcelas_entrada = params.get('num_parcelas_entrada', 0)
    total_meses_pagamento = num_parcelas_entrada + params['meses_pre'] + params['meses_pos']
    mes_pos_chaves_contador = 0
    custo_acumulado = amortizacao_assinatura

    for mes_atual in range(1, total_meses_pagamento + 1):
        data_mes = data_primeira_parcela + relativedelta(months=mes_atual-1)
        fase = 'Pós'
        if mes_atual <= num_parcelas_entrada: fase = 'Entrada'
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']: fase = 'Pré'
        
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas_original(parcelas_futuras, mes_atual)
        saldo_devedor -= (amortizacao + correcao_paga)
        
        correcao_mes = calcular_correcao_original(saldo_devedor, mes_atual, fase, params)
        saldo_devedor += correcao_mes
        
        if parcelas_futuras and correcao_mes != 0:
            total_original_restante = sum(p['valor_original'] for p in parcelas_futuras)
            if total_original_restante > 0:
                for p in parcelas_futuras:
                    p['correcao_acumulada'] += correcao_mes * (p['valor_original'] / total_original_restante)
        
        juros_mes = 0.0
        if fase == 'Pós':
            mes_pos_chaves_contador += 1
            taxa_juros_mes = mes_pos_chaves_contador / 100.0
            juros_mes = (amortizacao + correcao_paga) * taxa_juros_mes
        
        parcela_total = pagamento + juros_mes
        custo_acumulado += parcela_total
        historico.append({'DataObj': data_mes, 'Fase': fase, 'Parcela Total': parcela_total, 'Custo Acumulado': custo_acumulado})
            
    return pd.DataFrame(historico)

# ============================================
# LÓGICA DE CÁLCULO (CENÁRIO 2: FINANCIAMENTO BANCÁRIO COMPLETO)
# ============================================
def simular_financiamento_bancario_completo(params):
    historico = []
    taxa_juros_mensal = converter_juros_anual_para_mensal(params['taxa_juros_anual'] / 100)
    valor_financiado = params['valor_total_imovel'] - params['valor_entrada']
    data_corrente = datetime.strptime(params['mes_assinatura'], "%m/%Y")
    
    custo_acumulado = params['valor_entrada']
    historico.append({'DataObj': data_corrente, 'Fase': 'Entrada', 'Parcela Total': params['valor_entrada'], 'Custo Acumulado': custo_acumulado})
    data_corrente += relativedelta(months=1)

    # Fase de Obra
    if params['prazo_obra_meses'] > 0:
        saldo_liberado_obra = 0
        liberacao_mensal = valor_financiado / params['prazo_obra_meses']
        for _ in range(params['prazo_obra_meses']):
            saldo_liberado_obra += liberacao_mensal
            juros_obra = saldo_liberado_obra * taxa_juros_mensal
            encargos_obra = params['taxa_admin_mensal']
            parcela_obra = juros_obra + encargos_obra
            custo_acumulado += parcela_obra
            historico.append({'DataObj': data_corrente, 'Fase': 'Juros de Obra', 'Parcela Total': parcela_obra, 'Custo Acumulado': custo_acumulado})
            data_corrente += relativedelta(months=1)
    
    # Fase de Amortização
    saldo_devedor = valor_financiado
    amortizacao_constante = saldo_devedor / params['prazo_amortizacao_meses']
    
    for _ in range(params['prazo_amortizacao_meses']):
        juros = saldo_devedor * taxa_juros_mensal
        seguro_dfi = (params['taxa_dfi'] / 100) * params['valor_total_imovel']
        seguro_mip = (params['taxa_mip'] / 100) * saldo_devedor
        encargos = seguro_dfi + seguro_mip + params['taxa_admin_mensal']
        
        parcela_total = amortizacao_constante + juros + encargos
        saldo_devedor -= amortizacao_constante
        custo_acumulado += parcela_total
        historico.append({'DataObj': data_corrente, 'Fase': 'Amortização SAC', 'Parcela Total': parcela_total, 'Custo Acumulado': custo_acumulado})
        data_corrente += relativedelta(months=1)
        
    return pd.DataFrame(historico)

# ============================================
# INTERFACE STREAMLIT
# ============================================

def main():
    st.set_page_config(layout="wide", page_title="Comparador de Financiamento")
    st.title("Comparador de Cenários: Construtora vs. Financiamento Bancário")

    st.info(
        """
        **Para uma comparação eficaz, preencha os parâmetros de ambos os cenários.**
        Os campos já contêm valores padrão para demonstração. Ajuste-os para refletir sua realidade.
        
        💡 **Dica:** Para o cenário do **Financiamento Bancário**, consulte o 
        **[Simulador Habitacional da CAIXA](https://www8.caixa.gov.br/siopi/simulacao-financiamento/imobiliario/dados-iniciais.asp)** para obter taxas de juros e seguros precisas para o seu perfil.
        """,
        icon="⚖️"
    )

    # --- PARÂMETROS GERAIS ---
    st.sidebar.header("Parâmetros Gerais do Imóvel")
    params_gerais = {
        'mes_assinatura': st.sidebar.text_input("Mês da assinatura (MM/AAAA)", "09/2025"),
        'valor_total_imovel': st.sidebar.number_input("Valor total do imóvel", value=500000.0, format="%.2f"),
        'valor_entrada': st.sidebar.number_input("Valor da entrada", value=100000.0, format="%.2f")
    }
    
    col1, col2 = st.columns(2)

    # --- COLUNA 1: PARÂMETROS DA CONSTRUTORA ---
    with col1:
        st.header("🏗️ Cenário 1: Fluxo com a Construtora")
        params_construtora = {
            'tipo_pagamento_entrada': st.selectbox("Entrada paga em parcelas?", ['Não (ato)', 'Sim'], index=1),
            'mes_primeira_parcela': st.text_input("Mês da 1ª parcela (MM/AAAA)", "10/2025"),
            'meses_pre': st.number_input("Meses pré-chaves", value=24, key="c_pre"),
            'parcelas_mensais_pre': st.number_input("Parcela mensal pré-chaves (R$)", value=2500.0, format="%.2f"),
            'meses_pos': st.number_input("Meses pós-chaves", value=120, key="c_pos"),
            'valor_amortizacao_pos': st.number_input("Amortização mensal pós-chaves (R$)", value=1500.0, format="%.2f"),
            'incc_medio': st.number_input("INCC médio mensal (%)", value=0.55, format="%.4f") / 100,
            'ipca_medio': st.number_input("IPCA médio mensal (%)", value=0.45, format="%.4f") / 100,
            'inicio_correcao': 1,
            'parcelas_semestrais': {},
            'parcelas_anuais': {}
        }
        if params_construtora['tipo_pagamento_entrada'] == 'Sim':
            params_construtora['num_parcelas_entrada'] = st.number_input("Nº de parcelas da entrada", min_value=1, value=4)
            params_construtora['entrada_mensal'] = (params_gerais['valor_entrada'] / params_construtora['num_parcelas_entrada']) if params_construtora['num_parcelas_entrada'] > 0 else 0
        else:
            params_construtora['num_parcelas_entrada'] = 0
            params_construtora['entrada_mensal'] = 0

    # --- COLUNA 2: PARÂMETROS DO BANCO ---
    with col2:
        st.header("🏦 Cenário 2: Financiamento Bancário Completo")
        params_banco = {
            'prazo_obra_meses': st.number_input("Prazo de obra (meses)", value=24, key="b_obra", help="Período pagando apenas juros de obra."),
            'prazo_amortizacao_meses': st.number_input("Prazo de amortização (meses)", value=420, step=12, key="b_amort"),
            'taxa_juros_anual': st.number_input("Taxa de Juros Efetiva (% a.a.)", value=10.5, format="%.4f"),
            'taxa_dfi': st.number_input("Taxa DFI (% vlr. imóvel)", value=0.012, format="%.4f"),
            'taxa_mip': st.number_input("Taxa MIP (% saldo devedor)", value=0.025, format="%.4f"),
            'taxa_admin_mensal': st.number_input("Taxa de Admin Mensal (R$)", value=25.0, format="%.2f"),
        }
    
    # --- BOTÃO E EXECUÇÃO ---
    if st.button("Comparar Cenários", type="primary", use_container_width=True):
        # Preparar dicionários de parâmetros completos
        full_params_construtora = {**params_gerais, **params_construtora}
        full_params_construtora['tipo_pagamento_entrada'] = 'Parcelada' if full_params_construtora['tipo_pagamento_entrada'] == 'Sim' else 'Paga no ato'
        
        full_params_banco = {**params_gerais, **params_banco}

        # Executar simulações
        st.session_state.df_construtora = simular_financiamento_original(full_params_construtora)
        st.session_state.df_banco = simular_financiamento_bancario_completo(full_params_banco)

    # --- EXIBIÇÃO DOS RESULTADOS ---
    if 'df_construtora' in st.session_state and not st.session_state.df_construtora.empty:
        df_c = st.session_state.df_construtora
        df_b = st.session_state.df_banco

        st.divider()
        st.header("Resultados da Comparação")

        # Métricas de Resumo
        c_custo_total = df_c['Custo Acumulado'].iloc[-1]
        c_parcela_max = df_c['Parcela Total'].max()
        c_data_fim = df_c['DataObj'].iloc[-1]
        
        b_custo_total = df_b['Custo Acumulado'].iloc[-1]
        b_parcela_max = df_b['Parcela Total'].max()
        b_data_fim = df_b['DataObj'].iloc[-1]

        res1, res2 = st.columns(2)
        with res1:
            st.subheader("🏗️ Construtora")
            st.metric("Custo Total", format_currency(c_custo_total))
            st.metric("Maior Parcela", format_currency(c_parcela_max))
            st.metric("Término do Pagamento", c_data_fim.strftime("%m/%Y"))
        with res2:
            st.subheader("🏦 Financiamento Bancário")
            st.metric("Custo Total", format_currency(b_custo_total), delta=format_currency(c_custo_total - b_custo_total))
            st.metric("Maior Parcela", format_currency(b_parcela_max), delta=format_currency(c_parcela_max - b_parcela_max))
            st.metric("Término do Pagamento", b_data_fim.strftime("%m/%Y"))

        # Gráfico Comparativo
        df_merged = pd.merge(
            df_c[['DataObj', 'Parcela Total']].rename(columns={'Parcela Total': 'Parcela Construtora'}),
            df_b[['DataObj', 'Parcela Total']].rename(columns={'Parcela Total': 'Parcela Banco'}),
            on='DataObj',
            how='outer'
        ).sort_values('DataObj').fillna(0)

        st.subheader("Evolução Comparativa das Parcelas")
        st.line_chart(df_merged, x='DataObj', y=['Parcela Construtora', 'Parcela Banco'])

        # Tabelas detalhadas em expanders
        with st.expander("Ver Tabela Detalhada - Cenário Construtora"):
            st.dataframe(df_c, use_container_width=True)
        with st.expander("Ver Tabela Detalhada - Cenário Financiamento Bancário"):
            st.dataframe(df_b, use_container_width=True)

if __name__ == "__main__":
    main()
