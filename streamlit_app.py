import streamlit as st
import pandas as pd
import io
from datetime import datetime
import sgs
from dateutil.relativedelta import relativedelta
import numpy as np

# ============================================
# FUNÇÕES UTILITÁRIAS (BASE ORIGINAL INTACTA)
# ============================================

def format_currency(value):
    """Formata valores no padrão brasileiro R$ 1.234,56"""
    if pd.isna(value) or value == 0:
        return "R$ 0,00"
    if not isinstance(value, (int, float)):
        return value
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def converter_juros_anual_para_mensal(taxa_anual):
    """Converte taxa de juros anual efetiva para mensal."""
    if taxa_anual <= 0: return 0
    return ((1 + taxa_anual / 100) ** (1/12)) - 1

def construir_parcelas_futuras(params):
    """Cria lista de parcelas futuras com base nos parâmetros (LÓGICA ORIGINAL)"""
    parcelas = []
    num_parcelas_entrada = params['num_parcelas_entrada'] if params['tipo_pagamento_entrada'] == 'Parcelada' else 0
    
    for mes in range(1, num_parcelas_entrada + 1):
        parcelas.append({'mes': mes, 'valor_original': params['entrada_mensal'], 'correcao_acumulada': 0.0, 'tipo': 'entrada'})
    
    for mes in range(num_parcelas_entrada + 1, num_parcelas_entrada + 1 + params['meses_pre']):
        valor_parcela = params['parcelas_mensais_pre']
        mes_local = mes - num_parcelas_entrada
        for sem_mes in params['parcelas_semestrais']:
            if mes_local == sem_mes: valor_parcela += params['parcelas_semestrais'][sem_mes]
        for anu_mes in params['parcelas_anuais']:
            if mes_local == anu_mes: valor_parcela += params['parcelas_anuais'][anu_mes]
        if valor_parcela > 0:
            parcelas.append({'mes': mes, 'valor_original': valor_parcela, 'correcao_acumulada': 0.0, 'tipo': 'pre'})
            
    for mes in range(num_parcelas_entrada + 1 + params['meses_pre'], num_parcelas_entrada + 1 + params['meses_pre'] + params['meses_pos']):
        parcelas.append({'mes': mes, 'valor_original': params['valor_amortizacao_pos'], 'correcao_acumulada': 0.0, 'tipo': 'pos'})
        
    return parcelas

def calcular_correcao(saldo, mes, fase, params, valores_reais=None):
    """Calcula correção monetária, priorizando dados reais do BC. Unificada para todos os cenários."""
    limite = params.get('limite_correcao')
    if limite is not None and mes > limite: return 0

    if valores_reais is not None and mes in valores_reais:
        idx = valores_reais.get(mes, {})
        if fase in ['Entrada', 'Pré-Chaves', 'Carência', 'Juros de Obra'] and pd.notna(idx.get('incc')):
            return saldo * idx['incc']
        elif fase in ['Pós-Chaves', 'Amortização (Banco)'] and pd.notna(idx.get('ipca')):
            return saldo * idx['ipca']

    if fase in ['Entrada', 'Pré-Chaves', 'Carência', 'Juros de Obra']:
        return saldo * params['incc_medio']
    elif fase == 'Pós-Chaves':
        return saldo * params['ipca_medio']
    elif fase == 'Amortização (Banco)':
        indice = params['ipca_medio']
        if params['cef_indice_correcao'] == 'TR': indice = 0.0005 # TR estimada
        return saldo * indice
    return 0

def processar_parcelas_vencidas(parcelas_futuras, mes_atual):
    """Processa as parcelas vencidas do mês, retornando os totais. (LÓGICA ORIGINAL)"""
    vencidas = [p for p in parcelas_futuras if p['mes'] == mes_atual]
    pagamento_total, amortizacao_total, correcao_paga_total = 0, 0, 0
    for parcela in vencidas:
        pagamento_parcela = parcela['valor_original'] + parcela['correcao_acumulada']
        pagamento_total += pagamento_parcela
        amortizacao_total += parcela['valor_original']
        correcao_paga_total += parcela['correcao_acumulada']
        parcelas_futuras.remove(parcela)
    return pagamento_total, amortizacao_total, correcao_paga_total

def verificar_quitacao_pre(params, total_amortizado_acumulado):
    """Verifica se o percentual mínimo de quitação na fase pré-chaves foi atingido. (LÓGICA ORIGINAL)"""
    percentual = total_amortizado_acumulado / params['valor_total_imovel']
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(total_amortizado_acumulado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

# ============================================
# LÓGICA DE SIMULAÇÃO - 3 CENÁRIOS
# ============================================

def simular_cenario_construtora(params, valores_reais=None):
    """Executa a simulação completa do financiamento com a Construtora. LÓGICA ORIGINAL PRESERVADA."""
    historico = []
    try:
        data_assinatura = datetime.strptime(params['mes_assinatura'], "%m/%Y")
        data_primeira_parcela = datetime.strptime(params['mes_primeira_parcela'], "%m/%Y")
    except:
        st.error("Datas inválidas! Use o formato MM/AAAA.")
        return pd.DataFrame()

    saldo_devedor = params['valor_total_imovel']
    amortizacao_total_acumulada = 0
    amortizacao_assinatura = 0
    if params['tipo_pagamento_entrada'] == 'Paga no ato':
        amortizacao_assinatura = params['valor_entrada']
        saldo_devedor -= amortizacao_assinatura
        amortizacao_total_acumulada += amortizacao_assinatura
    
    historico.append({'Mês/Data': f"Assinatura [{data_assinatura.strftime('%m/%Y')}]", 'Data': data_assinatura, 'Fase': 'Assinatura', 'Saldo Devedor': saldo_devedor, 'Parcela Total': amortizacao_assinatura, 'Amortização Base': amortizacao_assinatura, 'Correção INCC ou IPCA diluída (R$)': 0, 'Taxa de Juros (%)': 0, 'Juros (R$)': 0, 'Ajuste INCC (R$)': 0, 'Ajuste IPCA (R$)': 0})

    meses_carencia = (data_primeira_parcela.year - data_assinatura.year) * 12 + (data_primeira_parcela.month - data_assinatura.month)
    saldo_temp_carencia = saldo_devedor
    total_correcao_carencia = 0
    for i in range(meses_carencia):
        correcao_mes_carencia = calcular_correcao(saldo_temp_carencia, 0, 'Carência', params, valores_reais)
        total_correcao_carencia += correcao_mes_carencia
        saldo_temp_carencia += correcao_mes_carencia

    parcelas_futuras = construir_parcelas_futuras(params)
    if total_correcao_carencia > 0 and parcelas_futuras:
        total_original = sum(p['valor_original'] for p in parcelas_futuras)
        if total_original > 0:
            for p in parcelas_futuras: p['correcao_acumulada'] += total_correcao_carencia * (p['valor_original'] / total_original)

    num_parcelas_entrada = params.get('num_parcelas_entrada', 0)
    total_meses_pagamento = num_parcelas_entrada + params['meses_pre'] + params['meses_pos']
    mes_pos_chaves_contador = 0

    for mes_atual in range(1, total_meses_pagamento + 1):
        data_mes = data_primeira_parcela + relativedelta(months=mes_atual-1)
        if mes_atual <= num_parcelas_entrada: fase = 'Entrada'
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']: fase = 'Pré-Chaves'
        else: fase = 'Pós-Chaves'
        
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas(parcelas_futuras, mes_atual)
        amortizacao_total_acumulada += amortizacao
        saldo_devedor -= (amortizacao + correcao_paga)
        
        correcao_mes = calcular_correcao(saldo_devedor, mes_atual, fase, params, valores_reais)
        saldo_devedor += correcao_mes
        
        if parcelas_futuras and correcao_mes != 0:
            total_original = sum(p['valor_original'] for p in parcelas_futuras)
            if total_original > 0:
                for p in parcelas_futuras: p['correcao_acumulada'] += correcao_mes * (p['valor_original'] / total_original)
        
        juros_mes = 0.0
        if fase == 'Pós-Chaves':
            juros_anual = params.get('juros_pos_chaves_anual', 0)
            if juros_anual > 0:
                taxa_juros_mensal = ((1 + juros_anual/100)**(1/12)) - 1
                juros_mes = saldo_devedor * taxa_juros_mensal
        
        saldo_devedor = max(saldo_devedor, 0)
        historico.append({'Mês/Data': f"{mes_atual} - [{data_mes.strftime('%m/%Y')}]", 'Data': data_mes, 'Fase': fase, 'Saldo Devedor': saldo_devedor, 'Parcela Total': pagamento + juros_mes, 'Amortização Base': amortizacao, 'Correção INCC ou IPCA diluída (R$)': correcao_paga, 'Juros (R$)': juros_mes, 'Ajuste INCC (R$)': correcao_mes if fase in ['Entrada','Pré-Chaves'] else 0, 'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós-Chaves' else 0})
        
        if fase == 'Pré-Chaves' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre(params, amortizacao_total_acumulada)
            
    df = pd.DataFrame(historico)
    # Colunas faltantes da lógica original, para consistência
    df['Taxa de Juros (%)'] = df['Juros (R$)'] / df['Saldo Devedor'].shift(fill_value=0) * 100
    return df

def calcular_amortizacao_bancaria(params, saldo_devedor_inicial, data_inicio, prazo_meses, mes_inicio_offset=0, valores_reais=None):
    historico = []
    saldo_devedor = saldo_devedor_inicial
    taxa_juros_mensal = converter_juros_anual_para_mensal(params['cef_taxa_juros_anual'])
    encargos_fixos = params['cef_seguros_mensal'] + params['cef_taxa_admin_mensal']
    if params['cef_sistema_amortizacao'] == 'SAC':
        amortizacao_constante = saldo_devedor_inicial / prazo_meses if prazo_meses > 0 else 0
        for mes in range(1, prazo_meses + 1):
            mes_global = mes_inicio_offset + mes
            data_mes = data_inicio + relativedelta(months=mes-1)
            correcao_saldo = calcular_correcao(saldo_devedor, mes_global, 'Amortização (Banco)', params, valores_reais)
            saldo_corrigido = saldo_devedor + correcao_saldo
            juros_mes = saldo_corrigido * taxa_juros_mensal
            parcela_total = amortizacao_constante + juros_mes + encargos_fixos
            saldo_devedor = saldo_corrigido - amortizacao_constante
            historico.append({'Data': data_mes, 'Fase': 'Amortização (Banco)', 'Saldo Devedor': saldo_devedor, 'Parcela Total': parcela_total, 'Amortização': amortizacao_constante, 'Juros': juros_mes, 'Correção Saldo': correcao_saldo, 'Encargos': encargos_fixos})
    elif params['cef_sistema_amortizacao'] == 'Price':
        parcela_price = np.pmt(taxa_juros_mensal, prazo_meses, -saldo_devedor_inicial) if taxa_juros_mensal > 0 else (saldo_devedor_inicial / prazo_meses if prazo_meses > 0 else 0)
        for mes in range(1, prazo_meses + 1):
            mes_global = mes_inicio_offset + mes
            data_mes = data_inicio + relativedelta(months=mes-1)
            correcao_saldo = calcular_correcao(saldo_devedor, mes_global, 'Amortização (Banco)', params, valores_reais)
            saldo_corrigido = saldo_devedor + correcao_saldo
            juros_mes = saldo_corrigido * taxa_juros_mensal
            amortizacao = parcela_price - juros_mes
            parcela_total = parcela_price + encargos_fixos + correcao_saldo
            saldo_devedor = saldo_corrigido - amortizacao
            historico.append({'Data': data_mes, 'Fase': 'Amortização (Banco)', 'Saldo Devedor': saldo_devedor, 'Parcela Total': parcela_total, 'Amortização': amortizacao, 'Juros': juros_mes, 'Correção Saldo': correcao_saldo, 'Encargos': encargos_fixos})
    return pd.DataFrame(historico)

def simular_cenario_caixa_completo(params, valores_reais=None):
    historico_obra = []
    data_assinatura = datetime.strptime(params['mes_assinatura'], "%m/%Y")
    saldo_devedor = params['valor_total_imovel'] - params['valor_entrada']
    taxa_juros_mensal = converter_juros_anual_para_mensal(params['cef_taxa_juros_anual'])
    encargos_fixos = params['cef_seguros_mensal'] + params['cef_taxa_admin_mensal']
    historico_obra.append({'Data': data_assinatura, 'Fase': 'Assinatura (Banco)', 'Saldo Devedor': saldo_devedor, 'Parcela Total': 0, 'Amortização': 0, 'Juros': 0, 'Correção Saldo': 0, 'Encargos': 0})
    meses_obra = params['num_parcelas_entrada'] + params['meses_pre']
    for mes in range(1, meses_obra + 1):
        data_mes = data_assinatura + relativedelta(months=mes)
        correcao_saldo = calcular_correcao(saldo_devedor, mes, 'Juros de Obra', params, valores_reais)
        saldo_devedor += correcao_saldo
        juros_mes = saldo_devedor * taxa_juros_mensal
        parcela_obra = juros_mes + encargos_fixos
        historico_obra.append({'Data': data_mes, 'Fase': 'Juros de Obra', 'Saldo Devedor': saldo_devedor, 'Parcela Total': parcela_obra, 'Amortização': 0, 'Juros': juros_mes, 'Correção Saldo': correcao_saldo, 'Encargos': encargos_fixos})
    df_obra = pd.DataFrame(historico_obra)
    saldo_final_obra = df_obra.iloc[-1]['Saldo Devedor']
    data_inicio_amortizacao = df_obra.iloc[-1]['Data'] + relativedelta(months=1)
    df_amortizacao = calcular_amortizacao_bancaria(params, saldo_final_obra, data_inicio_amortizacao, params['cef_prazo_meses'], mes_inicio_offset=meses_obra, valores_reais=valores_reais)
    return pd.concat([df_obra, df_amortizacao], ignore_index=True)

def simular_cenario_combinado(params, valores_reais=None):
    df_construtora_completo = simular_cenario_construtora(params, valores_reais)
    if df_construtora_completo.empty: return pd.DataFrame()
    idx_transicao = 1 + params['num_parcelas_entrada'] + params['meses_pre']
    if idx_transicao >= len(df_construtora_completo): return df_construtora_completo
    df_fase_obra = df_construtora_completo.iloc[0:idx_transicao].copy()
    saldo_transicao = df_fase_obra.iloc[-1]['Saldo Devedor']
    data_inicio_amortizacao = df_fase_obra.iloc[-1]['Data'] + relativedelta(months=1)
    df_amortizacao_banco = calcular_amortizacao_bancaria(params, saldo_transicao, data_inicio_amortizacao, params['cef_prazo_meses'], mes_inicio_offset=idx_transicao-1, valores_reais=valores_reais)
    return pd.concat([df_fase_obra, df_amortizacao_banco], ignore_index=True)

def buscar_indices_bc(mes_inicial, meses_total):
    try:
        data_inicio_simulacao = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_inicio_busca = data_inicio_simulacao - relativedelta(months=2)
        data_fim_busca = data_inicio_simulacao + relativedelta(months=meses_total)
        df = sgs.dataframe([192, 433], start=data_inicio_busca.strftime("%d/%m/%Y"), end=data_fim_busca.strftime("%d/%m/%Y"))
        if df.empty: return {}, 0
        df = df.rename(columns={192: 'incc', 433: 'ipca'})
        df['incc'] /= 100
        df['ipca'] /= 100
        indices = {}
        ultimo_mes_com_dado = 0
        dados_por_data = {idx.strftime("%Y-%m-%d"): row.to_dict() for idx, row in df.iterrows()}
        current_date_simulacao = data_inicio_simulacao
        for mes in range(1, meses_total + 1):
            data_referencia_str = (current_date_simulacao - relativedelta(months=2)).strftime("%Y-%m-%d")
            if data_referencia_str in dados_por_data:
                indices[mes] = dados_por_data[data_referencia_str]
                if pd.notna(indices[mes].get('incc')) or pd.notna(indices[mes].get('ipca')):
                    ultimo_mes_com_dado = mes
            current_date_simulacao += relativedelta(months=1)
        return indices, ultimo_mes_com_dado
    except Exception as e:
        st.error(f"Erro ao acessar dados do BC: {str(e)}")
        return {}, 0

# ============================================
# INTERFACE STREAMLIT
# ============================================
def criar_parametros():
    st.sidebar.header("Parâmetros Gerais")
    params = {}
    params['mes_assinatura'] = st.sidebar.text_input("Mês da assinatura (MM/AAAA)", "04/2025")
    params['mes_primeira_parcela'] = st.sidebar.text_input("Mês da 1ª parcela (MM/AAAA)", "05/2025")
    params['valor_total_imovel'] = st.sidebar.number_input("Valor total do imóvel", value=455750.0, format="%.2f")
    params['valor_entrada'] = st.sidebar.number_input("Valor da entrada", value=22270.54, format="%.2f")
    params['tipo_pagamento_entrada'] = st.sidebar.selectbox("Pagamento da entrada", ['Parcelada', 'Paga no ato'])
    if params['tipo_pagamento_entrada'] == 'Parcelada':
        params['num_parcelas_entrada'] = st.sidebar.number_input("Nº de parcelas da entrada", min_value=1, value=3)
        params['entrada_mensal'] = params['valor_entrada'] / params['num_parcelas_entrada'] if params['num_parcelas_entrada'] > 0 else 0
    else:
        params['num_parcelas_entrada'] = 0; params['entrada_mensal'] = 0
    st.sidebar.subheader("Parâmetros de Correção")
    params['inicio_correcao'] = st.sidebar.number_input("Aplicar correção a partir de qual parcela?", min_value=1, value=1)
    params['incc_medio'] = st.sidebar.number_input("INCC médio mensal (%)", value=0.5446, format="%.4f") / 100
    params['ipca_medio'] = st.sidebar.number_input("IPCA médio mensal (%)", value=0.4669, format="%.4f") / 100
    st.sidebar.subheader("Fases de Pagamento (Construtora)")
    col1, col2 = st.sidebar.columns(2)
    params['meses_pre'] = col1.number_input("Meses pré-chaves", value=17)
    params['meses_pos'] = col2.number_input("Meses pós-chaves", value=100)
    col3, col4 = st.sidebar.columns(2)
    params['parcelas_mensais_pre'] = col3.number_input("Valor parcela pré (R$)", value=3983.38, format="%.2f")
    params['valor_amortizacao_pos'] = col4.number_input("Valor parcela pós (R$)", value=3104.62, format="%.2f")
    params['juros_pos_chaves_anual'] = st.sidebar.number_input("Juros Pós-Chaves Anual (%)", value=0.0, format="%.2f")
    st.sidebar.subheader("Parcelas Extras (na fase pré-chaves)")
    params['parcelas_semestrais'] = {}
    params['parcelas_anuais'] = {}
    for i in range(2):
        cs1, cs2 = st.sidebar.columns(2)
        mes_sem = cs1.number_input(f"Mês da {i+1}ª semestral", value=6*(i+1) if i < 1 else 0, key=f"sem_mes_{i}")
        valor_sem = cs2.number_input(f"Valor {i+1} (R$)", value=6000.0 if i < 1 else 0.0, key=f"sem_val_{i}", format="%.2f")
        if mes_sem > 0: params['parcelas_semestrais'][int(mes_sem)] = valor_sem
    ca1, ca2 = st.sidebar.columns(2)
    mes_anu = ca1.number_input("Mês da anual", value=17, key="anu_mes")
    valor_anu = ca2.number_input("Valor anual (R$)", value=43300.0, key="anu_val", format="%.2f")
    if mes_anu > 0: params['parcelas_anuais'][int(mes_anu)] = valor_anu
    st.sidebar.subheader("Parâmetros do Financiamento Bancário")
    params['cef_sistema_amortizacao'] = st.sidebar.selectbox("Sistema de Amortização", ['SAC', 'Price'])
    params['cef_taxa_juros_anual'] = st.sidebar.number_input("Taxa de Juros Anual (%)", value=9.8, format="%.2f")
    params['cef_prazo_meses'] = st.sidebar.number_input("Prazo Financiamento (meses)", value=360)
    params['cef_seguros_mensal'] = st.sidebar.number_input("Seguros (MIP/DFI) Mensal (R$)", value=150.0, format="%.2f")
    params['cef_taxa_admin_mensal'] = st.sidebar.number_input("Taxa de Adm. Mensal (R$)", value=25.0, format="%.2f")
    params['cef_indice_correcao'] = st.sidebar.selectbox("Índice de Correção (Amortização)", ['TR', 'IPCA'])
    params['percentual_minimo_quitacao'] = 0.3
    return params

def mostrar_resultados_construtora(df_resultado):
    st.subheader("Tabela de Simulação Detalhada")
    colunas = ['Mês/Data', 'Fase', 'Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)', 'Correção INCC ou IPCA diluída (R$)', 'Amortização Base', 'Juros (R$)','Parcela Total']
    df_display = df_resultado[colunas].copy()
    for col in colunas[2:]: df_display[col] = df_display[col].apply(format_currency)
    st.dataframe(df_display, use_container_width=True)
    return df_resultado[colunas]

def mostrar_resultados_banco(df_resultado):
    st.subheader("Tabela de Simulação Detalhada")
    df_resultado['Mês'] = df_resultado['Data'].dt.strftime('%m/%Y')
    colunas_banco = ['Mês', 'Fase', 'Saldo Devedor', 'Correção Saldo', 'Amortização', 'Juros', 'Encargos', 'Parcela Total']
    colunas_presentes = [col for col in colunas_banco if col in df_resultado.columns]
    df_display = df_resultado[colunas_presentes].copy()
    for col in colunas_presentes[2:]: df_display[col] = df_display[col].apply(format_currency)
    st.dataframe(df_display, use_container_width=True)
    return df_resultado[colunas_presentes]

def mostrar_grafico_comparativo(df1, df2, df3):
    st.subheader("Gráfico Comparativo: Evolução da Parcela Mensal")
    df1_plot = df1[['Data', 'Parcela Total']].rename(columns={'Parcela Total': 'Cen. 1: Construtora'}).set_index('Data')
    df2_plot = df2[['Data', 'Parcela Total']].rename(columns={'Parcela Total': 'Cen. 2: 100% Caixa'}).set_index('Data')
    df3_plot = df3[['Data', 'Parcela Total']].rename(columns={'Parcela Total': 'Cen. 3: Combinado'}).set_index('Data')
    df_plot = pd.concat([df1_plot, df2_plot, df3_plot], axis=1).fillna(0)
    st.line_chart(df_plot)
    
def main():
    st.set_page_config(layout="wide")
    st.title("Simulador Comparativo de Financiamento Imobiliário")
    
    params = criar_parametros()
    st.header("Opções de Simulação")

    # Restaurando os 4 botões de simulação
    col1, col2, col3, col4 = st.columns(4)
    valores_reais = None
    params_sim = params.copy()

    with col1:
        if st.button("1. Simular com Médias", type="primary", use_container_width=True):
            st.session_state.tipo_simulacao = 'media'
            st.session_state.valores_reais = None
            st.session_state.params_sim = params.copy()

    with col2:
        if st.button("2. Simular Híbrido (BC + Médias)", use_container_width=True):
            total_meses = params['num_parcelas_entrada'] + params['meses_pre'] + params['cef_prazo_meses']
            valores_reais, ultimo_mes = buscar_indices_bc(params['mes_primeira_parcela'], total_meses)
            if ultimo_mes > 0:
                st.info(f"Dados reais do BC aplicados até a parcela {ultimo_mes}. O restante usará as médias.")
                st.session_state.tipo_simulacao = 'hibrido'
                st.session_state.valores_reais = valores_reais
                st.session_state.params_sim = params.copy()
            else:
                st.warning("Nenhum dado do BC encontrado para o período.")
                st.session_state.tipo_simulacao = None

    with col3:
        if st.button("3. Simular Apenas com BC (Puro)", use_container_width=True):
            total_meses = params['num_parcelas_entrada'] + params['meses_pre'] + params['cef_prazo_meses']
            valores_reais, ultimo_mes = buscar_indices_bc(params['mes_primeira_parcela'], total_meses)
            if ultimo_mes > 0:
                params_sim = params.copy()
                params_sim['limite_correcao'] = ultimo_mes
                st.info(f"Dados reais do BC aplicados até a parcela {ultimo_mes}. Após isso, não haverá novas correções.")
                st.session_state.tipo_simulacao = 'puro'
                st.session_state.valores_reais = valores_reais
                st.session_state.params_sim = params_sim
            else:
                st.warning("Nenhum dado do BC encontrado para o período.")
                st.session_state.tipo_simulacao = None

    with col4:
        limite_manual = st.number_input("Limite Manual de Correção", min_value=1, value=params['meses_pre'] + params.get('num_parcelas_entrada', 0))
        if st.button("4. Simular com Limite", use_container_width=True):
            params_sim = params.copy()
            params_sim['limite_correcao'] = limite_manual
            st.session_state.tipo_simulacao = 'limite'
            st.session_state.valores_reais = None
            st.session_state.params_sim = params_sim

    if 'tipo_simulacao' in st.session_state and st.session_state.tipo_simulacao:
        with st.spinner("Calculando todos os cenários..."):
            params_to_run = st.session_state.params_sim
            valores_to_run = st.session_state.valores_reais
            st.session_state.df_construtora = simular_cenario_construtora(params_to_run, valores_to_run)
            st.session_state.df_caixa = simular_cenario_caixa_completo(params_to_run, valores_to_run)
            st.session_state.df_combinado = simular_cenario_combinado(params_to_run, valores_to_run)
        
        if 'df_construtora' in st.session_state and not st.session_state.df_construtora.empty:
            mostrar_grafico_comparativo(st.session_state.df_construtora, st.session_state.df_caixa, st.session_state.df_combinado)

            tab1, tab2, tab3 = st.tabs(["Cenário 1: 100% Construtora", "Cenário 2: 100% Financiamento Bancário", "Cenário 3: Combinado"])

            with tab1:
                df_export_construtora = mostrar_resultados_construtora(st.session_state.df_construtora)
            with tab2:
                df_export_caixa = mostrar_resultados_banco(st.session_state.df_caixa)
            with tab3:
                df_export_combinado = mostrar_resultados_banco(st.session_state.df_combinado)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                # Normalizando colunas para exportação
                if not df_export_construtora.empty: df_export_construtora.to_excel(writer, index=False, sheet_name='Cenario_Construtora')
                if not df_export_caixa.empty: df_export_caixa.to_excel(writer, index=False, sheet_name='Cenario_Caixa_Completo')
                if not df_export_combinado.empty: df_export_combinado.to_excel(writer, index=False, sheet_name='Cenario_Combinado')
            st.download_button(label="💾 Baixar Todas as Planilhas (XLSX)", data=output.getvalue(), file_name='comparativo_financiamento.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', use_container_width=True)
        
        st.session_state.tipo_simulacao = None # Reset after showing results

if __name__ == "__main__":
    main()
