import streamlit as st
import pandas as pd
import io
from datetime import datetime
import sgs
from dateutil.relativedelta import relativedelta
import numpy as np

# ============================================
# FUNÇÕES UTILITÁRIAS (INTACTAS)
# ============================================

def format_currency(value):
    """Formata valores no padrão brasileiro R$ 1.234,56"""
    if pd.isna(value) or value == 0:
        return "R$ 0,00"
    
    # Adicionando verificação de tipo para evitar erros com strings
    if not isinstance(value, (int, float)):
        return value

    abs_value = abs(value)
    formatted = f"{abs_value:,.2f}"
    parts = formatted.split('.')
    integer_part = parts[0].replace(',', '.')
    decimal_part = parts[1] if len(parts) > 1 else "00"
    decimal_part = decimal_part.ljust(2, '0')[:2]
    sign = "-" if value < 0 else ""
    return f"R$ {sign}{integer_part},{decimal_part}"

def converter_juros_anual_para_mensal(taxa_anual):
    """Converte taxa de juros anual efetiva para mensal."""
    if taxa_anual <= 0:
        return 0
    return ((1 + taxa_anual / 100) ** (1/12)) - 1

def construir_parcelas_futuras(params):
    """
    Cria lista de parcelas futuras com base nos parâmetros
    """
    parcelas = []
    num_parcelas_entrada = params['num_parcelas_entrada'] if params['tipo_pagamento_entrada'] == 'Parcelada' else 0
    
    # Fase de Entrada
    for mes in range(1, num_parcelas_entrada + 1):
        parcelas.append({
            'mes': mes,
            'valor_original': params['entrada_mensal'],
            'correcao_acumulada': 0.0,
            'tipo': 'entrada'
        })
    
    # Fase Pré-chaves
    for mes in range(num_parcelas_entrada + 1, num_parcelas_entrada + 1 + params['meses_pre']):
        valor_parcela = params['parcelas_mensais_pre']
        
        mes_local = mes - num_parcelas_entrada
        for sem_mes in params['parcelas_semestrais']:
            if mes_local == sem_mes:
                valor_parcela += params['parcelas_semestrais'][sem_mes]
        for anu_mes in params['parcelas_anuais']:
            if mes_local == anu_mes:
                valor_parcela += params['parcelas_anuais'][anu_mes]
        
        if valor_parcela > 0:
            parcelas.append({
                'mes': mes,
                'valor_original': valor_parcela,
                'correcao_acumulada': 0.0,
                'tipo': 'pre'
            })
    
    # Fase Pós-chaves
    for mes in range(num_parcelas_entrada + 1 + params['meses_pre'], 
                     num_parcelas_entrada + 1 + params['meses_pre'] + params['meses_pos']):
        parcelas.append({
            'mes': mes,
            'valor_original': params['valor_amortizacao_pos'],
            'correcao_acumulada': 0.0,
            'tipo': 'pos'
        })
    
    return parcelas

def calcular_correcao(saldo, mes, fase, params, valores_reais=None):
    """
    Calcula correção monetária, respeitando o mês de início e os limites definidos.
    (LÓGICA ORIGINAL MANTIDA)
    """
    if fase not in ['Assinatura', 'Carência', 'Juros de Obra']:
        inicio_correcao = params.get('inicio_correcao', 1)
        if inicio_correcao == 0: inicio_correcao = 1
        if mes < inicio_correcao: return 0
            
    limite = params.get('limite_correcao')
    if limite is not None and mes > limite: return 0
    
    if valores_reais is not None and mes in valores_reais:
        idx = valores_reais[mes]
        if fase in ['Entrada','Pré', 'Carência', 'Juros de Obra'] and idx.get('incc') is not None:
            return saldo * idx['incc']
        elif fase in ['Pós', 'Amortização (Banco)'] and idx.get('ipca') is not None:
            return saldo * idx['ipca']

    # Fallback para médias
    if fase in ['Entrada','Pré', 'Carência', 'Juros de Obra']:
        return saldo * params['incc_medio']
    elif fase == 'Pós':
        return saldo * params['ipca_medio']
    elif fase == 'Amortização (Banco)':
        indice = params['ipca_medio']
        if params['cef_indice_correcao'] == 'TR':
            indice = 0.0005 # TR estimada
        return saldo * indice
    
    return 0

def processar_parcelas_vencidas(parcelas_futuras, mes_atual):
    """ Processa as parcelas vencidas do mês, retornando os totais. (INTACTA) """
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
    """ Verifica se o percentual mínimo de quitação na fase pré-chaves foi atingido. (INTACTA) """
    percentual = total_amortizado_acumulado / params['valor_total_imovel']
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(total_amortizado_acumulado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

# ============================================
# LÓGICA DE SIMULAÇÃO - 3 CENÁRIOS
# ============================================

# --- CENÁRIO 1: 100% CONSTRUTORA (LÓGICA ORIGINAL) ---
def simular_cenario_construtora(params, valores_reais=None):
    """
    Executa a simulação completa do financiamento com a Construtora.
    Esta é a função `simular_financiamento` original, renomeada para clareza.
    A LÓGICA DE CÁLCULO ESTÁ 100% PRESERVADA.
    """
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
    
    historico.append({'Data': data_assinatura, 'Fase': 'Assinatura', 'Saldo Devedor': saldo_devedor, 'Parcela Total': amortizacao_assinatura, 'Amortização': amortizacao_assinatura, 'Juros': 0, 'Correção Saldo (Ajuste)': 0, 'Correção Parcela (Diluída)': 0, 'Encargos': 0})

    meses_carencia = (data_primeira_parcela.year - data_assinatura.year) * 12 + (data_primeira_parcela.month - data_assinatura.month)
    data_corrente_carencia = data_assinatura
    saldo_temp_carencia = saldo_devedor
    total_correcao_carencia = 0
    for i in range(meses_carencia):
        data_corrente_carencia += relativedelta(months=1)
        correcao_mes_carencia = calcular_correcao(saldo_temp_carencia, 0, 'Carência', params, valores_reais)
        total_correcao_carencia += correcao_mes_carencia
        saldo_temp_carencia += correcao_mes_carencia
        historico.append({'Data': data_corrente_carencia, 'Fase': 'Carência', 'Saldo Devedor': saldo_devedor, 'Parcela Total': 0, 'Amortização': 0, 'Juros': 0, 'Correção Saldo (Ajuste)': correcao_mes_carencia, 'Correção Parcela (Diluída)': 0, 'Encargos': 0})

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
        
        taxa_juros_mes, juros_mes = 0.0, 0.0
        if fase == 'Pós-Chaves':
            mes_pos_chaves_contador += 1
            taxa_juros_mes = mes_pos_chaves_contador / 100.0
            juros_mes = (amortizacao + correcao_paga) * taxa_juros_mes
        
        saldo_devedor = max(saldo_devedor, 0)
        historico.append({'Data': data_mes, 'Fase': fase, 'Saldo Devedor': saldo_devedor, 'Parcela Total': pagamento + juros_mes, 'Amortização': amortizacao, 'Juros': juros_mes, 'Correção Saldo (Ajuste)': correcao_mes, 'Correção Parcela (Diluída)': correcao_paga, 'Encargos': 0})
        
        if fase == 'Pré-Chaves' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre(params, amortizacao_total_acumulada)
            
    return pd.DataFrame(historico)

# --- MÓDULO DE AMORTIZAÇÃO BANCÁRIA (Motor para cenários 2 e 3) ---
def calcular_amortizacao_bancaria(params, saldo_devedor_inicial, data_inicio, prazo_meses):
    historico = []
    saldo_devedor = saldo_devedor_inicial
    taxa_juros_mensal = converter_juros_anual_para_mensal(params['cef_taxa_juros_anual'])
    encargos_fixos = params['cef_seguros_mensal'] + params['cef_taxa_admin_mensal']

    if params['cef_sistema_amortizacao'] == 'SAC':
        amortizacao_constante = saldo_devedor_inicial / prazo_meses if prazo_meses > 0 else 0
        for mes in range(1, prazo_meses + 1):
            data_mes = data_inicio + relativedelta(months=mes-1)
            correcao_saldo = calcular_correcao(saldo_devedor, mes, 'Amortização (Banco)', params)
            saldo_corrigido = saldo_devedor + correcao_saldo
            juros_mes = saldo_corrigido * taxa_juros_mensal
            parcela_total = amortizacao_constante + juros_mes + encargos_fixos
            saldo_devedor = saldo_corrigido - amortizacao_constante
            saldo_devedor = max(saldo_devedor, 0)
            historico.append({'Data': data_mes, 'Fase': 'Amortização (Banco)', 'Saldo Devedor': saldo_devedor, 'Parcela Total': parcela_total, 'Amortização': amortizacao_constante, 'Juros': juros_mes, 'Correção Saldo (Ajuste)': correcao_saldo, 'Correção Parcela (Diluída)': 0, 'Encargos': encargos_fixos})
    
    elif params['cef_sistema_amortizacao'] == 'Price':
        if taxa_juros_mensal > 0:
            parcela_price = saldo_devedor_inicial * (taxa_juros_mensal * (1 + taxa_juros_mensal) ** prazo_meses) / (((1 + taxa_juros_mensal) ** prazo_meses) - 1)
        else:
            parcela_price = saldo_devedor_inicial / prazo_meses if prazo_meses > 0 else 0
        
        for mes in range(1, prazo_meses + 1):
            data_mes = data_inicio + relativedelta(months=mes-1)
            correcao_saldo = calcular_correcao(saldo_devedor, mes, 'Amortização (Banco)', params)
            saldo_corrigido = saldo_devedor + correcao_saldo
            juros_mes = saldo_corrigido * taxa_juros_mensal
            amortizacao = parcela_price - juros_mes
            parcela_total = parcela_price + encargos_fixos + correcao_saldo
            saldo_devedor = saldo_corrigido - amortizacao
            saldo_devedor = max(saldo_devedor, 0)
            historico.append({'Data': data_mes, 'Fase': 'Amortização (Banco)', 'Saldo Devedor': saldo_devedor, 'Parcela Total': parcela_total, 'Amortização': amortizacao, 'Juros': juros_mes, 'Correção Saldo (Ajuste)': correcao_saldo, 'Correção Parcela (Diluída)': 0, 'Encargos': encargos_fixos})
            
    return pd.DataFrame(historico)

# --- CENÁRIO 2: 100% CAIXA (COM JUROS DE OBRA) ---
def simular_cenario_caixa_completo(params):
    historico_obra = []
    data_assinatura = datetime.strptime(params['mes_assinatura'], "%m/%Y")
    
    saldo_devedor = params['valor_total_imovel'] - params['valor_entrada']
    taxa_juros_mensal = converter_juros_anual_para_mensal(params['cef_taxa_juros_anual'])
    encargos_fixos = params['cef_seguros_mensal'] + params['cef_taxa_admin_mensal']
    
    historico_obra.append({'Data': data_assinatura, 'Fase': 'Assinatura (Banco)', 'Saldo Devedor': saldo_devedor, 'Parcela Total': 0, 'Amortização': 0, 'Juros': 0, 'Correção Saldo (Ajuste)': 0, 'Correção Parcela (Diluída)': 0, 'Encargos': 0})
    
    meses_obra = params['num_parcelas_entrada'] + params['meses_pre']
    for mes in range(1, meses_obra + 1):
        data_mes = data_assinatura + relativedelta(months=mes)
        correcao_saldo = calcular_correcao(saldo_devedor, mes, 'Juros de Obra', params)
        saldo_devedor += correcao_saldo
        juros_mes = saldo_devedor * taxa_juros_mensal
        parcela_obra = juros_mes + encargos_fixos
        
        historico_obra.append({'Data': data_mes, 'Fase': 'Juros de Obra', 'Saldo Devedor': saldo_devedor, 'Parcela Total': parcela_obra, 'Amortização': 0, 'Juros': juros_mes, 'Correção Saldo (Ajuste)': correcao_saldo, 'Correção Parcela (Diluída)': 0, 'Encargos': encargos_fixos})
        
    df_obra = pd.DataFrame(historico_obra)
    
    saldo_final_obra = df_obra.iloc[-1]['Saldo Devedor']
    data_inicio_amortizacao = df_obra.iloc[-1]['Data'] + relativedelta(months=1)
    
    df_amortizacao = calcular_amortizacao_bancaria(params, saldo_final_obra, data_inicio_amortizacao, params['cef_prazo_meses'])
    
    return pd.concat([df_obra, df_amortizacao], ignore_index=True)

# --- CENÁRIO 3: COMBINADO (CONSTRUTORA + CAIXA) ---
def simular_cenario_combinado(params):
    df_construtora_completo = simular_cenario_construtora(params)
    if df_construtora_completo.empty: return pd.DataFrame()

    idx_transicao = 1 + params['num_parcelas_entrada'] + params['meses_pre'] # 1 para a linha de assinatura
    if idx_transicao >= len(df_construtora_completo): return df_construtora_completo

    df_fase_obra = df_construtora_completo.iloc[0:idx_transicao].copy()
    
    saldo_transicao = df_fase_obra.iloc[-1]['Saldo Devedor']
    data_inicio_amortizacao = df_fase_obra.iloc[-1]['Data'] + relativedelta(months=1)
    
    df_amortizacao = calcular_amortizacao_bancaria(params, saldo_transicao, data_inicio_amortizacao, params['cef_prazo_meses'])
    
    return pd.concat([df_fase_obra, df_amortizacao], ignore_index=True)


# ============================================
# INTEGRAÇÃO COM BANCO CENTRAL (INTACTA)
# ============================================
def buscar_indices_bc(mes_inicial, meses_total):
    try:
        data_inicio_simulacao = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_inicio_busca = data_inicio_simulacao - relativedelta(months=2)
        data_fim_busca = data_inicio_simulacao + relativedelta(months=meses_total)
        start_str = data_inicio_busca.strftime("%d/%m/%Y")
        end_str = data_fim_busca.strftime("%d/%m/%Y")
        
        df = sgs.dataframe([192, 433], start=start_str, end=end_str)
        if df.empty: return {}, 0, pd.DataFrame()

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
                if valores.get('incc') is not None or valores.get('ipca') is not None:
                    ultimo_mes_com_dado = mes
                indices[mes] = valores
            else:
                indices[mes] = {'incc': None, 'ipca': None}
            current_date_simulacao += relativedelta(months=1)
            
        return indices, ultimo_mes_com_dado, df
    except Exception as e:
        st.error(f"Erro ao acessar dados do BC: {str(e)}")
        return {}, 0, pd.DataFrame()

# ============================================
# INTERFACE STREAMLIT (ADAPTADA)
# ============================================
def criar_parametros():
    """ Interface do Sidebar. Lógica original mantida. """
    st.sidebar.header("Parâmetros Gerais")
    params = {}
    params['mes_assinatura'] = st.sidebar.text_input("Mês da assinatura (MM/AAAA)", "04/2025")
    params['mes_primeira_parcela'] = st.sidebar.text_input("Mês da 1ª parcela (MM/AAAA)", "05/2025")
    params['valor_total_imovel'] = st.sidebar.number_input("Valor total do imóvel", value=455750.0, format="%.2f")
    params['valor_entrada'] = st.sidebar.number_input("Valor total da entrada", value=22270.54, format="%.2f")
    
    params['tipo_pagamento_entrada'] = st.sidebar.selectbox("Como a entrada é paga?", ['Parcelada', 'Paga no ato'])
    
    if params['tipo_pagamento_entrada'] == 'Parcelada':
        params['num_parcelas_entrada'] = st.sidebar.number_input("Nº de parcelas da entrada", min_value=1, value=3)
        if params['num_parcelas_entrada'] > 0:
            params['entrada_mensal'] = params['valor_entrada'] / params['num_parcelas_entrada']
        else:
             params['entrada_mensal'] = 0
    else:
        params['num_parcelas_entrada'] = 0
        params['entrada_mensal'] = 0

    st.sidebar.subheader("Parâmetros de Correção")
    params['inicio_correcao'] = st.sidebar.number_input("Aplicar correção a partir de qual parcela (Construtora)?", min_value=1, value=1)
    params['incc_medio'] = st.sidebar.number_input("INCC médio mensal (%) - Fase Obra", value=0.5446, format="%.4f") / 100
    params['ipca_medio'] = st.sidebar.number_input("IPCA médio mensal (%) - Pós-Chaves", value=0.4669, format="%.4f") / 100
    
    st.sidebar.subheader("Fases de Pagamento (Construtora)")
    col1, col2 = st.sidebar.columns(2)
    with col1:
        params['meses_pre'] = col1.number_input("Meses pré-chaves", value=17)
    with col2:
        params['meses_pos'] = col2.number_input("Meses pós-chaves", value=100)
    
    col3, col4 = st.sidebar.columns(2)
    with col3:
        params['parcelas_mensais_pre'] = col3.number_input("Valor parcela pré (R$)", value=3983.38, format="%.2f")
    with col4:
        params['valor_amortizacao_pos'] = col4.number_input("Valor parcela pós (R$)", value=3104.62, format="%.2f")
    
    st.sidebar.subheader("Parcelas Extras (na fase pré-chaves)")
    params['parcelas_semestrais'] = {}
    params['parcelas_anuais'] = {}
    st.sidebar.write("Parcelas Semestrais:")
    for i in range(4):
        cs1, cs2 = st.sidebar.columns(2)
        mes_sem = cs1.number_input(f"Mês da {i+1}ª semestral", value=6*(i+1) if i<2 else 0, key=f"sem_mes_{i}")
        valor_sem = cs2.number_input(f"Valor {i+1} (R$)", value=6000.0 if i<2 else 0.0, key=f"sem_val_{i}", format="%.2f")
        if mes_sem > 0 and valor_sem > 0:
            params['parcelas_semestrais'][int(mes_sem)] = valor_sem
            
    st.sidebar.write("Parcelas Anuais:")
    ca1, ca2 = st.sidebar.columns(2)
    mes_anu = ca1.number_input("Mês da anual", value=17, key="anu_mes")
    valor_anu = ca2.number_input("Valor anual (R$)", value=43300.0, key="anu_val", format="%.2f")
    if mes_anu > 0 and valor_anu > 0:
        params['parcelas_anuais'][int(mes_anu)] = valor_anu
        
    st.sidebar.subheader("Parâmetros do Financiamento Bancário")
    st.sidebar.info("Usado para os cenários '100% Caixa' e 'Combinado'.")
    params['cef_sistema_amortizacao'] = st.sidebar.selectbox("Sistema de Amortização", ['SAC', 'Price'])
    params['cef_taxa_juros_anual'] = st.sidebar.number_input("Taxa de Juros Anual (%)", value=9.8, format="%.2f")
    params['cef_prazo_meses'] = st.sidebar.number_input("Prazo do Financiamento (meses)", value=360)
    params['cef_seguros_mensal'] = st.sidebar.number_input("Seguros (MIP/DFI) Mensal (R$)", value=150.0, format="%.2f")
    params['cef_taxa_admin_mensal'] = st.sidebar.number_input("Taxa de Administração Mensal (R$)", value=25.0, format="%.2f")
    params['cef_indice_correcao'] = st.sidebar.selectbox("Índice de Correção (Amortização)", ['TR', 'IPCA'], help="A TR será estimada. O IPCA usará a média já informada.")

    params['percentual_minimo_quitacao'] = 0.3
    params['limite_correcao'] = None
    return params

def mostrar_tabela_detalhada(df):
    """ Função de exibição unificada para qualquer cenário. """
    colunas_formatar = ['Saldo Devedor', 'Parcela Total', 'Amortização', 'Juros', 
                        'Correção Saldo (Ajuste)', 'Correção Parcela (Diluída)', 'Encargos']
    
    # Criando um dicionário de formatação
    format_dict = {col: format_currency for col in colunas_formatar}

    # Aplicando o estilo
    st.dataframe(df.style.format(format_dict, na_rep='-'), use_container_width=True)

def mostrar_grafico_comparativo(df1, df2, df3):
    st.subheader("Gráfico Comparativo: Evolução da Parcela Mensal")
    
    df1_plot = df1[['Data', 'Parcela Total']].rename(columns={'Parcela Total': 'Cenário 1: Construtora'}).set_index('Data')
    df2_plot = df2[['Data', 'Parcela Total']].rename(columns={'Parcela Total': 'Cenário 2: 100% Caixa'}).set_index('Data')
    df3_plot = df3[['Data', 'Parcela Total']].rename(columns={'Parcela Total': 'Cenário 3: Combinado'}).set_index('Data')
    
    df_plot = pd.concat([df1_plot, df2_plot, df3_plot], axis=1)
    st.line_chart(df_plot)

def main():
    st.set_page_config(layout="wide")
    st.title("Simulador de Cenários: Construtora vs. Financiamento Bancário")

    # Limpeza de session_state para garantir nova simulação
    if 'ran_simulation' not in st.session_state:
        st.session_state.ran_simulation = False

    params = criar_parametros()

    if st.button("Simular Cenários", type="primary", use_container_width=True):
        with st.spinner("Calculando Cenário 1: 100% Construtora..."):
            st.session_state.df_construtora = simular_cenario_construtora(params.copy())
        with st.spinner("Calculando Cenário 2: 100% Financiamento Bancário..."):
            st.session_state.df_caixa = simular_cenario_caixa_completo(params.copy())
        with st.spinner("Calculando Cenário 3: Combinado..."):
            st.session_state.df_combinado = simular_cenario_combinado(params.copy())
        st.session_state.ran_simulation = True

    if st.session_state.ran_simulation:
        if 'df_construtora' in st.session_state and 'df_caixa' in st.session_state and 'df_combinado' in st.session_state:
            mostrar_grafico_comparativo(st.session_state.df_construtora, st.session_state.df_caixa, st.session_state.df_combinado)

            tab1, tab2, tab3 = st.tabs(["Cenário 1: 100% Construtora", "Cenário 2: 100% Financiamento Bancário", "Cenário 3: Combinado"])

            with tab1:
                st.header("Detalhes: 100% Construtora")
                st.info("Financiamento direto com a construtora, seguindo as fases pré e pós-chaves definidas.")
                mostrar_tabela_detalhada(st.session_state.df_construtora)

            with tab2:
                st.header("Detalhes: 100% Financiamento Bancário")
                st.info("Simula um financiamento na planta com o banco desde o início, incluindo a fase de 'Juros de Obra' (sem amortização) e depois a fase de amortização (SAC/Price).")
                mostrar_tabela_detalhada(st.session_state.df_caixa)
                
            with tab3:
                st.header("Detalhes: Cenário Combinado")
                st.info("Neste cenário, você paga a fase de obra para a construtora e, na entrega das chaves, quita o saldo devedor com um financiamento bancário.")
                mostrar_tabela_detalhada(st.session_state.df_combinado)
        else:
            st.error("Ocorreu um erro ao calcular um ou mais cenários. Verifique os parâmetros.")


if __name__ == "__main__":
    main()
