import streamlit as st
import pandas as pd
import io
import numpy as np
import numpy_financial as npf
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

def buscar_indices_bc_original(mes_inicial, meses_total):
    """Busca INCC e IPCA da API do Banco Central."""
    try:
        data_inicio_simulacao = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_inicio_busca = data_inicio_simulacao - relativedelta(months=2)
        data_fim_busca = data_inicio_simulacao + relativedelta(months=meses_total)
        start_str = data_inicio_busca.strftime("%d/%m/%Y")
        end_str = data_fim_busca.strftime("%d/%m/%Y")
        
        df = sgs.dataframe([192, 433], start=start_str, end=end_str)
        if df.empty: return {}, 0

        df = df.rename(columns={192: 'incc', 433: 'ipca'})
        df['incc'] = df['incc'] / 100
        df['ipca'] = df['ipca'] / 100
        
        indices = {}
        ultimo_mes_com_dado = 0
        dados_por_data = {idx.strftime("%Y-%m-%d"): {'incc': row['incc'], 'ipca': row['ipca']} for idx, row in df.iterrows()}
        
        current_date_simulacao = data_inicio_simulacao
        for mes in range(1, meses_total + 1):
            data_referencia_str = (current_date_simulacao - relativedelta(months=2)).strftime("%Y-%m-%d")
            if data_referencia_str in dados_por_data:
                valores = dados_por_data[data_referencia_str]
                if pd.notna(valores.get('incc')) or pd.notna(valores.get('ipca')):
                    ultimo_mes_com_dado = mes
                indices[mes] = valores
            else:
                indices[mes] = {'incc': None, 'ipca': None}
            current_date_simulacao += relativedelta(months=1)
            
        return indices, ultimo_mes_com_dado
    except Exception as e:
        st.error(f"Erro ao acessar dados do BC: {str(e)}")
        return {}, 0


# ============================================
# LÓGICA ORIGINAL DA CONSTRUTORA (PRESERVADA)
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

def calcular_correcao_original(saldo, mes, fase, params, valores_reais):
    if fase not in ['Assinatura', 'Carência']:
        inicio_correcao = params.get('inicio_correcao', 1)
        if inicio_correcao == 0: inicio_correcao = 1
        if mes < inicio_correcao: return 0
            
    limite = params.get('limite_correcao')
    if limite is not None and mes > limite: return 0
    
    if valores_reais is not None and mes in valores_reais:
        idx = valores_reais[mes]
        if fase in ['Entrada','Pré', 'Carência'] and pd.notna(idx.get('incc')):
            return saldo * idx['incc']
        elif fase == 'Pós' and pd.notna(idx.get('ipca')):
            return saldo * idx['ipca']

    if fase in ['Entrada','Pré', 'Carência']: return saldo * params['incc_medio']
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

def verificar_quitacao_pre_original(params, total_amortizado_acumulado):
    percentual = total_amortizado_acumulado / params['valor_total_imovel']
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(total_amortizado_acumulado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

def simular_financiamento_construtora(params, valores_reais=None):
    """Função de simulação principal do código original, 100% preservada."""
    historico = []
    try:
        data_assinatura = datetime.strptime(params['mes_assinatura'], "%m/%Y")
        data_primeira_parcela = datetime.strptime(params['mes_primeira_parcela'], "%m/%Y")
    except ValueError:
        st.error("Datas inválidas! Use o formato MM/AAAA.")
        return pd.DataFrame()

    saldo_devedor = params['valor_total_imovel']
    amortizacao_total_acumulada = 0
    amortizacao_assinatura = 0

    if params['tipo_pagamento_entrada'] == 'Paga no ato':
        amortizacao_assinatura = params['valor_entrada']
        saldo_devedor -= amortizacao_assinatura
        amortizacao_total_acumulada += amortizacao_assinatura
    
    historico.append({
        'Mês/Data': f"Assinatura [{data_assinatura.strftime('%m/%Y')}]", 'Fase': 'Assinatura', 'DataObj': data_assinatura,
        'Saldo Devedor': saldo_devedor, 'Parcela Total': amortizacao_assinatura, 'Amortização Base': amortizacao_assinatura,
        'Correção INCC ou IPCA diluída (R$)': 0, 'Taxa de Juros (%)': 0, 'Juros (R$)': 0, 'Ajuste INCC (R$)': 0, 'Ajuste IPCA (R$)': 0
    })

    meses_carencia = (data_primeira_parcela.year - data_assinatura.year) * 12 + (data_primeira_parcela.month - data_assinatura.month)
    data_corrente_carencia = data_assinatura
    saldo_temp_carencia = saldo_devedor
    total_correcao_carencia = 0
    for i in range(meses_carencia):
        data_corrente_carencia += relativedelta(months=1)
        correcao_mes_carencia = calcular_correcao_original(saldo_temp_carencia, 0, 'Carência', params, valores_reais)
        total_correcao_carencia += correcao_mes_carencia
        saldo_temp_carencia += correcao_mes_carencia
        historico.append({
            'Mês/Data': f"Gerou Correção [{data_corrente_carencia.strftime('%m/%Y')}]", 'Fase': 'Carência', 'DataObj': data_corrente_carencia,
            'Saldo Devedor': saldo_devedor, 'Parcela Total': 0, 'Amortização Base': 0, 'Correção INCC ou IPCA diluída (R$)': 0,
            'Taxa de Juros (%)': 0, 'Juros (R$)': 0, 'Ajuste INCC (R$)': correcao_mes_carencia, 'Ajuste IPCA (R$)': 0
        })

    parcelas_futuras = construir_parcelas_futuras_original(params)
    if total_correcao_carencia > 0 and parcelas_futuras:
        total_original = sum(p['valor_original'] for p in parcelas_futuras)
        if total_original > 0:
            for p in parcelas_futuras:
                p['correcao_acumulada'] += total_correcao_carencia * (p['valor_original'] / total_original)

    num_parcelas_entrada = params.get('num_parcelas_entrada', 0)
    total_meses_pagamento = num_parcelas_entrada + params['meses_pre'] + params['meses_pos']
    mes_pos_chaves_contador = 0

    for mes_atual in range(1, total_meses_pagamento + 1):
        data_mes = data_primeira_parcela + relativedelta(months=mes_atual-1)
        if mes_atual <= num_parcelas_entrada: fase = 'Entrada'
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']: fase = 'Pré-Chaves'
        else: fase = 'Pós-Chaves'
        
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas_original(parcelas_futuras, mes_atual)
        amortizacao_total_acumulada += amortizacao
        saldo_devedor -= (amortizacao + correcao_paga)
        
        correcao_mes = calcular_correcao_original(saldo_devedor, mes_atual, fase, params, valores_reais)
        saldo_devedor += correcao_mes
        
        if parcelas_futuras and correcao_mes != 0:
            total_original_restante = sum(p['valor_original'] for p in parcelas_futuras)
            if total_original_restante > 0:
                for p in parcelas_futuras:
                    p['correcao_acumulada'] += correcao_mes * (p['valor_original'] / total_original_restante)
        
        taxa_juros_mes, juros_mes = 0.0, 0.0
        if fase == 'Pós-Chaves':
            mes_pos_chaves_contador += 1
            taxa_juros_mes = mes_pos_chaves_contador / 100.0
            juros_mes = (amortizacao + correcao_paga) * taxa_juros_mes
        
        saldo_devedor = max(saldo_devedor, 0)
        historico.append({
            'Mês/Data': f"{mes_atual} - [{data_mes.strftime('%m/%Y')}]", 'Fase': fase, 'DataObj': data_mes,
            'Saldo Devedor': saldo_devedor, 'Parcela Total': pagamento + juros_mes, 'Amortização Base': amortizacao,
            'Correção INCC ou IPCA diluída (R$)': correcao_paga, 'Taxa de Juros (%)': taxa_juros_mes, 'Juros (R$)': juros_mes,
            'Ajuste INCC (R$)': correcao_mes if fase in ['Entrada','Pré-Chaves'] else 0,
            'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós-Chaves' else 0
        })
        
        if fase == 'Pré-Chaves' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre_original(params, amortizacao_total_acumulada)
            
    return pd.DataFrame(historico)


# ============================================
# LÓGICA DO FINANCIAMENTO BANCÁRIO (NOVO MÓDULO)
# ============================================
def simular_financiamento_bancario(params_caixa):
    """Executa a simulação do financiamento bancário."""
    if params_caixa['modo_entrada'] == 'Simplificado':
        if params_caixa['primeira_parcela'] <= params_caixa['ultima_parcela']:
            st.error("Erro: A 'Primeira Parcela' deve ser maior que a 'Última Parcela' no Modo Simplificado.")
            return pd.DataFrame()

    historico = []
    taxa_juros_mensal = converter_juros_anual_para_mensal(params_caixa['taxa_juros_anual'] / 100)
    saldo_devedor = params_caixa['valor_financiado']
    data_corrente = params_caixa['data_inicio']
    
    # Fase de Obra
    if params_caixa['prazo_obra_meses'] > 0:
        saldo_liberado_obra = 0
        liberacao_mensal = saldo_devedor / params_caixa['prazo_obra_meses']
        for _ in range(1, params_caixa['prazo_obra_meses'] + 1):
            saldo_liberado_obra += liberacao_mensal
            juros_obra = saldo_liberado_obra * taxa_juros_mensal
            encargos_obra = params_caixa.get('taxa_admin_mensal', 0)
            parcela_obra = juros_obra + encargos_obra
            historico.append({'DataObj': data_corrente, 'Fase': 'Taxa de Obra', 'Parcela Total': parcela_obra,
                              'Amortização': 0, 'Juros': juros_obra, 'Encargos': encargos_obra,
                              'Saldo Devedor': saldo_liberado_obra})
            data_corrente += relativedelta(months=1)
    
    # Fase de Amortização
    amortizacao_constante = saldo_devedor / params_caixa['prazo_meses']
    encargos_fixos_mensais = 0
    if params_caixa['modo_entrada'] == 'Simplificado':
        primeiros_juros = saldo_devedor * taxa_juros_mensal
        encargos_fixos_mensais = params_caixa['primeira_parcela'] - (amortizacao_constante + primeiros_juros)
    
    for _ in range(1, params_caixa['prazo_meses'] + 1):
        saldo_devedor_corrigido = saldo_devedor
        juros = saldo_devedor_corrigido * taxa_juros_mensal
        
        if params_caixa['modo_entrada'] == 'Avançado':
            seguro_dfi = (params_caixa['taxa_dfi'] / 100) * params_caixa['valor_avaliacao_imovel']
            seguro_mip = (params_caixa['taxa_mip'] / 100) * saldo_devedor_corrigido
            encargos = seguro_dfi + seguro_mip + params_caixa['taxa_admin_mensal']
        else:
            encargos = encargos_fixos_mensais
        
        parcela_total = amortizacao_constante + juros + encargos
        saldo_devedor_final = saldo_devedor_corrigido - amortizacao_constante
        
        historico.append({'DataObj': data_corrente, 'Fase': 'Financiamento Bancário', 'Parcela Total': parcela_total,
                          'Amortização': amortizacao_constante, 'Juros': juros, 'Encargos': encargos,
                          'Saldo Devedor': saldo_devedor_final})
        
        saldo_devedor = max(saldo_devedor_final, 0)
        data_corrente += relativedelta(months=1)
        
    return pd.DataFrame(historico)

# ============================================
# INTERFACE STREAMLIT
# ============================================

def inicializar_session_state():
    if 'df_resultado' not in st.session_state: st.session_state.df_resultado = pd.DataFrame()
    if 'df_banco' not in st.session_state: st.session_state.df_banco = pd.DataFrame()
    if 'df_unificado' not in st.session_state: st.session_state.df_unificado = pd.DataFrame()
    if 'saldo_para_banco' not in st.session_state: st.session_state.saldo_para_banco = 0.0
    if 'data_para_banco' not in st.session_state: st.session_state.data_para_banco = datetime.now()
    if 'ipca_medio' not in st.session_state: st.session_state.ipca_medio = 0.4669

def criar_parametros_construtora():
    st.sidebar.header("Parâmetros Gerais (Construtora)")
    params = {}
    params['mes_assinatura'] = st.sidebar.text_input("Mês da assinatura (MM/AAAA)", "04/2025")
    params['mes_primeira_parcela'] = st.sidebar.text_input("Mês da 1ª parcela (MM/AAAA)", "05/2025")
    params['valor_total_imovel'] = st.sidebar.number_input("Valor total do imóvel", value=455750.0, format="%.2f", key="vlr_imovel_construtora")
    params['valor_entrada'] = st.sidebar.number_input("Valor total da entrada", value=22270.54, format="%.2f")
    params['tipo_pagamento_entrada'] = st.sidebar.selectbox("Como a entrada é paga?", ['Parcelada', 'Paga no ato'])
    
    if params['tipo_pagamento_entrada'] == 'Parcelada':
        params['num_parcelas_entrada'] = st.sidebar.number_input("Nº de parcelas da entrada", min_value=1, value=3)
        params['entrada_mensal'] = params['valor_entrada'] / params['num_parcelas_entrada'] if params['num_parcelas_entrada'] > 0 else 0
    else:
        params['num_parcelas_entrada'] = 0
        params['entrada_mensal'] = 0

    st.sidebar.subheader("Parâmetros de Correção")
    params['inicio_correcao'] = st.sidebar.number_input("Aplicar correção a partir de qual parcela?", min_value=1, value=1)
    params['incc_medio'] = st.sidebar.number_input("INCC médio mensal (%)", value=0.5446, format="%.4f") / 100
    params['ipca_medio'] = st.sidebar.number_input("IPCA médio mensal (%)", value=st.session_state.ipca_medio, format="%.4f") / 100
    
    st.sidebar.subheader("Fases de Pagamento")
    c1, c2 = st.sidebar.columns(2)
    params['meses_pre'] = c1.number_input("Meses pré-chaves", value=17)
    params['meses_pos'] = c2.number_input("Meses pós-chaves", value=100)
    c3, c4 = st.sidebar.columns(2)
    params['parcelas_mensais_pre'] = c3.number_input("Valor parcela pré (R$)", value=3983.38, format="%.2f")
    params['valor_amortizacao_pos'] = c4.number_input("Valor parcela pós (R$)", value=3104.62, format="%.2f")
    
    params['parcelas_semestrais'] = {6: 6000.0, 12: 6000.0}
    params['parcelas_anuais'] = {17: 43300.0}
    params['percentual_minimo_quitacao'] = 0.3
    params['limite_correcao'] = None
    return params

def mostrar_resultados_originais(df_resultado):
    """Função de exibição original, AGORA CORRIGIDA para usar column_config."""
    st.subheader("Tabela de Simulação Detalhada (Construtora)")
    
    df_display = df_resultado[['Mês/Data', 'Fase', 'Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)', 'Correção INCC ou IPCA diluída (R$)', 'Amortização Base', 'Taxa de Juros (%)', 'Juros (R$)', 'Parcela Total']].copy()

    st.dataframe(
        df_display,
        column_config={
            "Saldo Devedor": st.column_config.NumberColumn(format="R$ %.2f"),
            "Ajuste INCC (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Ajuste IPCA (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Correção INCC ou IPCA diluída (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Amortização Base": st.column_config.NumberColumn(format="R$ %.2f"),
            "Juros (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Parcela Total": st.column_config.NumberColumn(format="R$ %.2f"),
            "Taxa de Juros (%)": st.column_config.ProgressColumn(
                "Taxa de Juros (%)",
                format="%.2f%%",
                min_value=0,
                max_value=float(df_display["Taxa de Juros (%)"].max() * 100) if not df_display["Taxa de Juros (%)"].empty else 1,
            ),
        },
        use_container_width=True,
        height=400
    )


def mostrar_graficos_comparativos(df_unificado, df_banco):
    """Exibe os gráficos comparativos."""
    st.header("📊 Gráficos Comparativos")
    df_unificado['Tipo'] = np.where(df_unificado['Fase'].isin(['Financiamento Bancário', 'Taxa de Obra']), 'Financiamento Bancário', 'Fluxo Construtora')
    df_plot_parcela = df_unificado.pivot_table(index='DataObj', columns='Tipo', values='Parcela Total').reset_index()

    st.subheader("Evolução da Parcela (Construtora vs. Banco)")
    st.line_chart(df_plot_parcela, x='DataObj', y=['Fluxo Construtora', 'Financiamento Bancário'])

    st.subheader("Composição da Parcela (Financiamento Bancário)")
    df_banco_composicao = df_banco[['DataObj', 'Amortização', 'Juros', 'Encargos']].set_index('DataObj')
    st.area_chart(df_banco_composicao)
    
    st.subheader("Evolução do Saldo Devedor")
    st.line_chart(df_unificado.set_index('DataObj'), y='Saldo Devedor')

def main():
    st.set_page_config(layout="wide", page_title="Simulador de Financiamento Avançado")
    st.title("Simulador de Financiamento Imobiliário Ponta a Ponta 🏗️ 🏦")
    
    inicializar_session_state()
    params_construtora = criar_parametros_construtora()

    tab1, tab2 = st.tabs(["Simulação com a Construtora", "Simulação Financiamento Bancário"])

    with tab1:
        st.header("Opções de Simulação (Fluxo Construtora)")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("1. Simular com Médias", type="primary", use_container_width=True):
                st.session_state.df_resultado = simular_financiamento_construtora(params_construtora.copy())
        with col2:
            if st.button("2. Simular Híbrido (BC + Médias)", use_container_width=True):
                total_meses = params_construtora['num_parcelas_entrada'] + params_construtora['meses_pre'] + params_construtora['meses_pos']
                valores_reais, ultimo_mes = buscar_indices_bc_original(params_construtora['mes_primeira_parcela'], total_meses)
                if ultimo_mes > 0: st.info(f"Dados reais do BC aplicados até a parcela {ultimo_mes}.")
                st.session_state.df_resultado = simular_financiamento_construtora(params_construtora.copy(), valores_reais)
        with col3:
            if st.button("3. Simular Apenas com BC (Puro)", use_container_width=True):
                total_meses = params_construtora['num_parcelas_entrada'] + params_construtora['meses_pre'] + params_construtora['meses_pos']
                valores_reais, ultimo_mes = buscar_indices_bc_original(params_construtora['mes_primeira_parcela'], total_meses)
                if ultimo_mes > 0:
                    params_sim = params_construtora.copy()
                    params_sim['limite_correcao'] = ultimo_mes
                    st.info(f"Dados reais do BC aplicados até a parcela {ultimo_mes}. Novas correções param após isso.")
                    st.session_state.df_resultado = simular_financiamento_construtora(params_sim, valores_reais)
                else: st.warning("Nenhum dado histórico encontrado.")
        with col4:
            limite_manual = st.number_input("Limite Manual de Correção", min_value=1, value=params_construtora['meses_pre'] + params_construtora.get('num_parcelas_entrada', 0))
            if st.button("4. Simular com Limite", use_container_width=True):
                params_sim = params_construtora.copy()
                params_sim['limite_correcao'] = limite_manual
                st.session_state.df_resultado = simular_financiamento_construtora(params_sim)

        if not st.session_state.df_resultado.empty:
            mostrar_resultados_originais(st.session_state.df_resultado)
            
            saldo_final = st.session_state.df_resultado['Saldo Devedor'].iloc[-1]
            data_final = st.session_state.df_resultado['DataObj'].iloc[-1]
            st.info(f"Saldo devedor final com a construtora: **{format_currency(saldo_final)}** em **{data_final.strftime('%m/%Y')}**")

            if st.button("➡️ Usar Saldo Devedor para Simular Financiamento Bancário"):
                st.session_state.saldo_para_banco = saldo_final
                st.session_state.data_para_banco = data_final
                st.success("Dados enviados para a aba 'Financiamento Bancário'. Clique na aba para continuar.")

    with tab2:
        st.header("Simulação do Financiamento Bancário (CEF)")
        params_caixa = {}
        
        c1, c2 = st.columns(2)
        params_caixa['cenario'] = c1.selectbox("Cenário de financiamento:", ("Financiar Pós-Chaves", "Financiamento Completo na Planta"))
        params_caixa['modo_entrada'] = c2.radio("Modo de Entrada:", ("Avançado", "Simplificado"), horizontal=True)
        
        valor_financiado_default = st.session_state.saldo_para_banco if st.session_state.saldo_para_banco > 0 else params_construtora['valor_total_imovel'] * 0.8
        data_inicio_default = st.session_state.data_para_banco + relativedelta(months=1) if st.session_state.saldo_para_banco > 0 else datetime(2027, 1, 15)

        st.subheader("Parâmetros do Financiamento")
        col1, col2, col3 = st.columns(3)
        with col1:
            params_caixa['valor_financiado'] = st.number_input("Valor a ser financiado (R$)", value=valor_financiado_default, format="%.2f")
            params_caixa['prazo_meses'] = st.number_input("Prazo (meses)", value=420, step=12)
            params_caixa['data_inicio'] = st.date_input("Data da 1ª parcela", value=data_inicio_default).replace(day=1)
        with col2:
            params_caixa['taxa_juros_anual'] = st.number_input("Taxa de Juros Efetiva (% a.a.)", value=9.75, format="%.4f")
            params_caixa['sistema_amortizacao'] = st.selectbox("Sistema", ["SAC"], disabled=True)
        with col3:
            params_caixa['valor_avaliacao_imovel'] = st.number_input("Valor de avaliação do imóvel (R$)", value=params_construtora['valor_total_imovel'], format="%.2f")
            params_caixa['prazo_obra_meses'] = st.number_input("Prazo de Obra (meses)", value=24, help="Apenas para 'Financiamento Completo'") if params_caixa['cenario'] == 'Financiamento Completo na Planta' else 0

        if params_caixa['modo_entrada'] == 'Avançado':
            st.subheader("Taxas e Seguros (Avançado)")
            c1, c2, c3 = st.columns(3)
            params_caixa['taxa_dfi'] = c1.number_input("Taxa DFI (% vlr. imóvel)", value=0.0118, format="%.4f")
            params_caixa['taxa_mip'] = c2.number_input("Taxa MIP (% saldo devedor)", value=0.0248, format="%.4f")
            params_caixa['taxa_admin_mensal'] = c3.number_input("Taxa de Admin (R$)", value=25.0, format="%.2f")
        else:
            st.subheader("Valores da Parcela (Simplificado)")
            c1, c2 = st.columns(2)
            params_caixa['primeira_parcela'] = c1.number_input("Valor da 1ª Parcela", value=4500.0, format="%.2f")
            params_caixa['ultima_parcela'] = c2.number_input("Valor da Última Parcela", value=1500.0, format="%.2f")

        if st.button("Simular Financiamento Bancário", type="primary", use_container_width=True):
            st.session_state.df_banco = simular_financiamento_bancario(params_caixa)
            if not st.session_state.df_banco.empty:
                if params_caixa['cenario'] == 'Financiar Pós-Chaves' and not st.session_state.df_resultado.empty:
                    df_c_norm = st.session_state.df_resultado.rename(columns={'Amortização Base': 'Amortização', 'Juros (R$)': 'Juros'})
                    df_b_norm = st.session_state.df_banco
                    df_c_norm['Encargos'] = df_c_norm['Correção INCC ou IPCA diluída (R$)']
                    st.session_state.df_unificado = pd.concat([df_c_norm, df_b_norm], ignore_index=True)
                else:
                    st.session_state.df_unificado = st.session_state.df_banco

        if not st.session_state.df_unificado.empty:
            st.subheader("Resultado da Simulação Unificada")
            df_display = st.session_state.df_unificado[['DataObj', 'Fase', 'Parcela Total', 'Amortização', 'Juros', 'Encargos', 'Saldo Devedor']]
            st.dataframe(
                df_display,
                column_config={
                    "DataObj": st.column_config.DateColumn("Data",format="DD/MM/YYYY"),
                    "Parcela Total": st.column_config.NumberColumn(format="R$ %.2f"),
                    "Amortização": st.column_config.NumberColumn(format="R$ %.2f"),
                    "Juros": st.column_config.NumberColumn(format="R$ %.2f"),
                    "Encargos": st.column_config.NumberColumn(format="R$ %.2f"),
                    "Saldo Devedor": st.column_config.NumberColumn(format="R$ %.2f"),
                },
                use_container_width=True
            )

            if not st.session_state.df_banco.empty:
                 mostrar_graficos_comparativos(st.session_state.df_unificado, st.session_state.df_banco)

if __name__ == "__main__":
    main()
