import streamlit as st
import pandas as pd
import io
from datetime import datetime
import sgs
from dateutil.relativedelta import relativedelta
import numpy as np # Adicionado para cálculos financeiros

# ============================================
# FUNÇÕES UTILITÁRIAS
# ============================================

def format_currency(value):
    """Formata valores no padrão brasileiro R$ 1.234,56"""
    if pd.isna(value) or value == 0:
        return "0,00"
    
    abs_value = abs(value)
    formatted = f"{abs_value:,.2f}"
    parts = formatted.split('.')
    integer_part = parts[0].replace(',', '.')
    decimal_part = parts[1] if len(parts) > 1 else "00"
    decimal_part = decimal_part.ljust(2, '0')[:2]
    sign = "-" if value < 0 else ""
    return f"{sign}{integer_part},{decimal_part}"

# NOVA FUNÇÃO UTILITÁRIA (Plano, Passo 2)
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

def calcular_correcao(saldo, mes, fase, params, valores_reais):
    """
    Calcula correção monetária, respeitando o mês de início e os limites definidos.
    """
    if fase not in ['Assinatura', 'Carência']:
        inicio_correcao = params.get('inicio_correcao', 1)
        if inicio_correcao == 0:
            inicio_correcao = 1
        if mes < inicio_correcao:
            return 0
            
    limite = params.get('limite_correcao')
    if limite is not None and mes > limite:
        return 0
    
    # Se houver valores reais, eles têm prioridade
    if valores_reais is not None and mes in valores_reais:
        idx = valores_reais[mes]
        if fase in ['Entrada','Pré', 'Carência'] and idx.get('incc') is not None:
            return saldo * idx['incc']
        elif fase == 'Pós' and idx.get('ipca') is not None:
            return saldo * idx['ipca']

    # Se a simulação for Híbrida, usa a média como fallback.
    # Se for Pura (com limite_correcao definido pelo BC), não deve chegar aqui se o limite for ultrapassado.
    if fase in ['Entrada','Pré', 'Carência']:
        return saldo * params['incc_medio']
    elif fase == 'Pós':
        return saldo * params['ipca_medio']
    
    return 0

def processar_parcelas_vencidas(parcelas_futuras, mes_atual):
    """
    Processa as parcelas vencidas do mês, retornando os totais.
    """
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
    """
    Verifica se o percentual mínimo de quitação na fase pré-chaves foi atingido.
    """
    percentual = total_amortizado_acumulado / params['valor_total_imovel']
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(total_amortizado_acumulado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

# ============================================
# LÓGICA DE SIMULAÇÃO
# ============================================

# LÓGICA PRINCIPAL (CONSTRUTORA) - Sem alteração na fórmula
def simular_financiamento(params, valores_reais=None):
    """
    Executa a simulação completa do financiamento com a Construtora.
    """
    historico = []
    
    try:
        data_assinatura = datetime.strptime(params['mes_assinatura'], "%m/%Y")
        data_primeira_parcela = datetime.strptime(params['mes_primeira_parcela'], "%m/%Y")
        if data_primeira_parcela < data_assinatura:
            st.error("O mês da primeira parcela não pode ser anterior ao mês de assinatura!")
            return pd.DataFrame()
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
    
    historico.append({
        'Mês/Data': f"Assinatura [{data_assinatura.strftime('%m/%Y')}]", 'Fase': 'Assinatura', 
        'Saldo Devedor': saldo_devedor, 'Parcela Total': amortizacao_assinatura, 
        'Amortização Base': amortizacao_assinatura, 'Correção INCC ou IPCA diluída (R$)': 0, 
        'Taxa de Juros (%)': 0, 'Juros (R$)': 0, 
        'Ajuste INCC (R$)': 0, 'Ajuste IPCA (R$)': 0
    })

    meses_carencia = (data_primeira_parcela.year - data_assinatura.year) * 12 + (data_primeira_parcela.month - data_assinatura.month)
    data_corrente_carencia = data_assinatura
    saldo_temp_carencia = saldo_devedor
    total_correcao_carencia = 0
    for i in range(meses_carencia):
        data_corrente_carencia += relativedelta(months=1)
        correcao_mes_carencia = calcular_correcao(saldo_temp_carencia, 0, 'Carência', params, valores_reais)
        total_correcao_carencia += correcao_mes_carencia
        saldo_temp_carencia += correcao_mes_carencia
        historico.append({
            'Mês/Data': f"Gerou Correção [{data_corrente_carencia.strftime('%m/%Y')}]", 'Fase': 'Carência', 
            'Saldo Devedor': saldo_devedor, 'Parcela Total': 0, 'Amortização Base': 0, 
            'Correção INCC ou IPCA diluída (R$)': 0, 'Taxa de Juros (%)': 0, 'Juros (R$)': 0, 
            'Ajuste INCC (R$)': correcao_mes_carencia, 'Ajuste IPCA (R$)': 0
        })

    parcelas_futuras = construir_parcelas_futuras(params)
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
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']: fase = 'Pré'
        else: fase = 'Pós'
        
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas(parcelas_futuras, mes_atual)
        amortizacao_total_acumulada += amortizacao
        saldo_devedor -= (amortizacao + correcao_paga)
        
        correcao_mes = calcular_correcao(saldo_devedor, mes_atual, fase, params, valores_reais)
        saldo_devedor += correcao_mes
        
        if parcelas_futuras and correcao_mes != 0:
            total_original = sum(p['valor_original'] for p in parcelas_futuras)
            if total_original > 0:
                for p in parcelas_futuras:
                    p['correcao_acumulada'] += correcao_mes * (p['valor_original'] / total_original)
        
        taxa_juros_mes, juros_mes = 0.0, 0.0
        if fase == 'Pós':
            mes_pos_chaves_contador += 1
            taxa_juros_mes = mes_pos_chaves_contador / 100.0
            juros_mes = (amortizacao + correcao_paga) * taxa_juros_mes
        
        saldo_devedor = max(saldo_devedor, 0)
        historico.append({
            'Mês/Data': f"{mes_atual} - [{data_mes.strftime('%m/%Y')}]", 'Fase': fase, 'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento + juros_mes, 'Amortização Base': amortizacao,
            'Correção INCC ou IPCA diluída (R$)': correcao_paga, 'Taxa de Juros (%)': taxa_juros_mes, 
            'Juros (R$)': juros_mes, 'Ajuste INCC (R$)': correcao_mes if fase in ['Entrada','Pré'] else 0,
            'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós' else 0
        })
        
        if fase == 'Pré' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre(params, amortizacao_total_acumulada)
            
    return pd.DataFrame(historico)

# NOVA FUNÇÃO DE LÓGICA (Plano, Passo 2)
def simular_financiamento_cef(params, saldo_devedor_inicial, data_inicio):
    """
    Executa a simulação do financiamento bancário (CEF).
    """
    historico = []
    saldo_devedor = saldo_devedor_inicial
    taxa_juros_mensal = converter_juros_anual_para_mensal(params['cef_taxa_juros_anual'])
    
    # Estimativa simples para TR, pode ser ajustada se necessário.
    tr_mensal_estimada = 0.0005 # 0.05%
    
    if params['cef_sistema_amortizacao'] == 'SAC':
        amortizacao_constante = saldo_devedor_inicial / params['cef_prazo_meses']
        
        for mes in range(1, params['cef_prazo_meses'] + 1):
            data_mes = data_inicio + relativedelta(months=mes-1)
            
            # Correção do Saldo Devedor
            indice_correcao_mes = tr_mensal_estimada if params['cef_indice_correcao'] == 'TR' else params['ipca_medio']
            correcao_saldo = saldo_devedor * indice_correcao_mes
            saldo_corrigido = saldo_devedor + correcao_saldo
            
            # Cálculo de Juros
            juros_mes = saldo_corrigido * taxa_juros_mensal
            
            # Encargos Fixos
            encargos_fixos = params['cef_seguros_mensal'] + params['cef_taxa_admin_mensal']
            
            # Parcela Total
            parcela_total = amortizacao_constante + juros_mes + encargos_fixos
            
            # Atualização do Saldo Devedor
            saldo_devedor = saldo_corrigido - amortizacao_constante
            saldo_devedor = max(saldo_devedor, 0)
            
            historico.append({
                'Mês/Data': data_mes,
                'Parcela Total': parcela_total,
                'Amortização': amortizacao_constante,
                'Juros': juros_mes,
                'Encargos (Seguro/Adm)': encargos_fixos,
                'Correção Saldo': correcao_saldo,
                'Saldo Devedor': saldo_devedor
            })
            
    elif params['cef_sistema_amortizacao'] == 'Price':
        # Lógica para a Tabela Price (cálculo da parcela fixa inicial)
        if taxa_juros_mensal > 0:
            parcela_price = saldo_devedor_inicial * \
                (taxa_juros_mensal * (1 + taxa_juros_mensal) ** params['cef_prazo_meses']) / \
                (((1 + taxa_juros_mensal) ** params['cef_prazo_meses']) - 1)
        else:
            parcela_price = saldo_devedor_inicial / params['cef_prazo_meses']

        for mes in range(1, params['cef_prazo_meses'] + 1):
            data_mes = data_inicio + relativedelta(months=mes-1)

            # Correção do Saldo Devedor
            indice_correcao_mes = tr_mensal_estimada if params['cef_indice_correcao'] == 'TR' else params['ipca_medio']
            correcao_saldo = saldo_devedor * indice_correcao_mes
            saldo_corrigido = saldo_devedor + correcao_saldo

            juros_mes = saldo_corrigido * taxa_juros_mensal
            amortizacao = parcela_price - juros_mes
            
            encargos_fixos = params['cef_seguros_mensal'] + params['cef_taxa_admin_mensal']
            parcela_total = parcela_price + encargos_fixos + correcao_saldo # A parcela principal é fixa, mas encargos e correção são somados

            saldo_devedor = saldo_corrigido - amortizacao
            saldo_devedor = max(saldo_devedor, 0)

            historico.append({
                'Mês/Data': data_mes,
                'Parcela Total': parcela_total,
                'Amortização': amortizacao,
                'Juros': juros_mes,
                'Encargos (Seguro/Adm)': encargos_fixos,
                'Correção Saldo': correcao_saldo,
                'Saldo Devedor': saldo_devedor
            })

    return pd.DataFrame(historico)


# ============================================
# INTEGRAÇÃO COM BANCO CENTRAL (LÓGICA M-2) - Sem Alteração
# ============================================

def buscar_indices_bc(mes_inicial, meses_total):
    try:
        data_inicio_simulacao = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_inicio_busca = data_inicio_simulacao - relativedelta(months=2)
        data_fim_busca = data_inicio_simulacao + relativedelta(months=meses_total)
        start_str = data_inicio_busca.strftime("%d/%m/%Y")
        end_str = data_fim_busca.strftime("%d/%m/%Y")
        
        df = sgs.dataframe([192, 433], start=start_str, end=end_str)
        if df.empty: 
            return {}, 0, pd.DataFrame()

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
# INTERFACE STREAMLIT
# ============================================

# FUNÇÃO DE PARÂMETROS ALTERADA (Plano, Passo 1)
def criar_parametros():
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

    st.sidebar.subheader("Parâmetros de Correção (Construtora)")
    params['inicio_correcao'] = st.sidebar.number_input("Aplicar correção a partir de qual parcela?", min_value=1, value=1)
    params['incc_medio'] = st.sidebar.number_input("INCC médio mensal (%)", value=0.5446, format="%.4f") / 100
    params['ipca_medio'] = st.sidebar.number_input("IPCA médio mensal (%)", value=0.4669, format="%.4f") / 100
    
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
        with cs1:
            mes_sem = cs1.number_input(f"Mês da {i+1}ª semestral", value=6*(i+1) if i<2 else 0, key=f"sem_mes_{i}")
        with cs2:
            valor_sem = cs2.number_input(f"Valor {i+1} (R$)", value=6000.0 if i<2 else 0.0, key=f"sem_val_{i}", format="%.2f")
        if mes_sem > 0 and valor_sem > 0:
            params['parcelas_semestrais'][int(mes_sem)] = valor_sem
            
    st.sidebar.write("Parcelas Anuais:")
    ca1, ca2 = st.sidebar.columns(2)
    with ca1:
        mes_anu = ca1.number_input("Mês da anual", value=17, key="anu_mes")
    with ca2:
        valor_anu = ca2.number_input("Valor anual (R$)", value=43300.0, key="anu_val", format="%.2f")
    if mes_anu > 0 and valor_anu > 0:
        params['parcelas_anuais'][int(mes_anu)] = valor_anu
        
    # NOVA SEÇÃO DE PARÂMETROS (Plano, Passo 1)
    st.sidebar.subheader("Parâmetros do Financiamento Bancário (CEF)")
    st.sidebar.info("Para maior precisão, obtenha estes valores no simulador oficial da Caixa Econômica Federal.")
    params['cef_sistema_amortizacao'] = st.sidebar.selectbox("Sistema de Amortização", ['SAC', 'Price'])
    params['cef_taxa_juros_anual'] = st.sidebar.number_input("Taxa de Juros Anual (%)", value=9.8, format="%.2f")
    params['cef_prazo_meses'] = st.sidebar.number_input("Prazo do Financiamento (meses)", value=360)
    params['cef_seguros_mensal'] = st.sidebar.number_input("Seguros (MIP/DFI) Mensal (R$)", value=150.0, format="%.2f")
    params['cef_taxa_admin_mensal'] = st.sidebar.number_input("Taxa de Administração Mensal (R$)", value=25.0, format="%.2f")
    params['cef_indice_correcao'] = st.sidebar.selectbox("Índice de Correção do Saldo", ['TR', 'IPCA'], help="A TR será estimada. O IPCA usará a média já informada acima.")

    params['percentual_minimo_quitacao'] = 0.3
    params['limite_correcao'] = None
    return params

# FUNÇÃO DE EXIBIÇÃO REFATORADA (Plano, Passo 4)
def mostrar_tabela_detalhada(df, titulo, tipo='construtora'):
    st.subheader(titulo)
    
    if tipo == 'construtora':
        colunas = ['Mês/Data', 'Fase', 'Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)', 'Correção INCC ou IPCA diluída (R$)', 'Amortização Base', 'Taxa de Juros (%)', 'Juros (R$)', 'Parcela Total']
        df_display = df[colunas].copy()
        for col in ['Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)', 'Correção INCC ou IPCA diluída (R$)', 'Amortização Base', 'Juros (R$)', 'Parcela Total']:
            df_display[col] = df_display[col].apply(format_currency)
        df_display['Taxa de Juros (%)'] = df['Taxa de Juros (%)'].apply(lambda x: f"{x:.2%}" if x > 0 else "N/A")
    else: # tipo == 'cef'
        colunas = ['Mês/Data', 'Saldo Devedor', 'Correção Saldo', 'Amortização', 'Juros', 'Encargos (Seguro/Adm)', 'Parcela Total']
        df_display = df[colunas].copy()
        df_display['Mês/Data'] = df_display['Mês/Data'].dt.strftime('%m/%Y')
        for col in ['Saldo Devedor', 'Correção Saldo', 'Amortização', 'Juros', 'Encargos (Seguro/Adm)', 'Parcela Total']:
            df_display[col] = df_display[col].apply(format_currency)

    st.dataframe(df_display, use_container_width=True)
    return df[colunas].copy()

# NOVA FUNÇÃO DE EXIBIÇÃO DE GRÁFICO (Plano, Passo 4)
def mostrar_grafico_comparativo(df_construtora, df_cef):
    st.subheader("Gráfico Comparativo: Evolução da Parcela Mensal")
    
    # Prepara dados da construtora
    df_construtora_plot = df_construtora[df_construtora['Mês/Data'].str.contains(r'\[\d{2}/\d{4}\]')].copy()
    df_construtora_plot['Data'] = pd.to_datetime(df_construtora_plot['Mês/Data'].str.extract(r'\[(.*?)\]')[0], format='%m/%Y')
    df_construtora_plot = df_construtora_plot[['Data', 'Parcela Total']].rename(columns={'Parcela Total': 'Parcela Construtora'})
    
    # Prepara dados da CEF
    df_cef_plot = df_cef[['Mês/Data', 'Parcela Total']].copy()
    df_cef_plot = df_cef_plot.rename(columns={'Mês/Data': 'Data', 'Parcela Total': 'Parcela Financiamento Bancário'})
    
    # Mescla os dois dataframes
    df_plot = pd.merge(df_construtora_plot, df_cef_plot, on='Data', how='outer').set_index('Data').sort_index()
    
    st.line_chart(df_plot)

def main():
    st.set_page_config(layout="wide")
    st.title("Simulador Comparativo: Construtora vs. Financiamento Bancário 🚧🏦")

    # Limpa o session state antigo se existir
    if 'df_resultado' in st.session_state:
        del st.session_state.df_resultado

    params = criar_parametros()
    st.header("Opções de Simulação")
    
    # BOTÃO ÚNICO E LÓGICA CENTRALIZADA (Plano, Passo 3)
    if st.button("Simular Cenários Comparativos", type="primary", use_container_width=True):
        # Passo 1: Simulação da Construtora
        df_construtora = simular_financiamento(params.copy())
        
        if not df_construtora.empty:
            # Passo 2: Capturar Dados de Transição
            saldo_final_construtora = df_construtora.iloc[-1]['Saldo Devedor']
            
            # Extrai a última data da simulação da construtora
            last_date_str = df_construtora[df_construtora['Mês/Data'].str.contains(r'\[\d{2}/\d{4}\]')].iloc[-1]['Mês/Data']
            last_date = datetime.strptime(last_date_str.split('[')[1].strip(']'), "%m/%Y")
            data_inicio_cef = last_date + relativedelta(months=1)
            
            # Passo 3: Simulação do Financiamento Bancário
            st.session_state.df_cef = simular_financiamento_cef(params.copy(), saldo_final_construtora, data_inicio_cef)
            st.session_state.df_construtora = df_construtora
        else:
            st.error("A simulação da construtora falhou. Verifique os parâmetros.")
            # Limpa o estado para evitar mostrar resultados antigos
            if 'df_construtora' in st.session_state: del st.session_state.df_construtora
            if 'df_cef' in st.session_state: del st.session_state.df_cef


    st.info("A simulação será executada para os dois cenários (Construtora e Financiamento Bancário) usando os parâmetros informados na barra lateral. Para uma comparação precisa, obtenha os dados do financiamento (taxa de juros, seguros, prazo) no simulador oficial da Caixa.")

    # LÓGICA DE EXIBIÇÃO (Plano, Passo 4)
    if 'df_construtora' in st.session_state and not st.session_state.df_construtora.empty:
        mostrar_grafico_comparativo(st.session_state.df_construtora, st.session_state.df_cef)
        
        df_export_construtora = mostrar_tabela_detalhada(st.session_state.df_construtora, "Cenário Construtora", tipo='construtora')
        df_export_cef = mostrar_tabela_detalhada(st.session_state.df_cef, "Cenário Financiamento Bancário (CEF)", tipo='cef')
        
        # Lógica para download de ambos os cenários em abas diferentes de um Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_export_construtora.to_excel(writer, index=False, sheet_name='Cenario_Construtora')
            df_export_cef.to_excel(writer, index=False, sheet_name='Cenario_Financiamento_CEF')
        
        st.download_button(
            label="💾 Baixar Planilhas (XLSX)", 
            data=output.getvalue(), 
            file_name='comparativo_financiamento.xlsx', 
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
            use_container_width=True
        )

if __name__ == "__main__":
    main()
