import streamlit as st
import pandas as pd
import io
from datetime import datetime
import sgs
from dateutil.relativedelta import relativedelta

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
        # Adicionar parcelas semestrais
        for sem_mes in params['parcelas_semestrais']:
            if mes_local == sem_mes:
                valor_parcela += params['parcelas_semestrais'][sem_mes]
        # Adicionar parcelas anuais
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
    Calcula correção monetária, respeitando o mês de início definido pelo usuário.
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
    
    if valores_reais is not None:
        if mes in valores_reais:
            idx = valores_reais[mes]
            if fase in ['Entrada','Pré', 'Carência'] and idx.get('incc') is not None:
                return saldo * idx['incc']
            elif fase == 'Pós' and idx.get('ipca') is not None:
                return saldo * idx['ipca']
    
    if fase in ['Entrada','Pré', 'Carência']:
        return saldo * params['incc_medio']
    elif fase == 'Pós':
        return saldo * params['ipca_medio']
    
    return 0

def processar_parcelas_vencidas(parcelas_futuras, mes_atual):
    """
    Processas parcelas vencidas
    """
    vencidas = [p for p in parcelas_futuras if p['mes'] == mes_atual]
    pagamento_total = 0
    amortizacao_total = 0
    correcao_paga_total = 0
    
    for parcela in vencidas:
        pagamento_parcela = parcela['valor_original'] + parcela['correcao_acumulada']
        pagamento_total += pagamento_parcela
        amortizacao_total += parcela['valor_original']
        correcao_paga_total += parcela['correcao_acumulada']
        parcelas_futuras.remove(parcela)
    
    return pagamento_total, amortizacao_total, correcao_paga_total

def verificar_quitacao_pre(params, total_amortizado_acumulado):
    """
    Verifica quitacao mínima
    """
    percentual = total_amortizado_acumulado / params['valor_total_imovel']
    
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(total_amortizado_acumulado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

# ============================================
# LÓGICA PRINCIPAL DE SIMULAÇÃO
# ============================================

def simular_financiamento(params, valores_reais=None):
    """
    Executa a simulação completa com a nova lógica de assinatura e carência.
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
        'Taxa de Juros (%)': 0, 'Juros (R$)': 0, 'Ajuste INCC (R$)': 0, 'Ajuste IPCA (R$)': 0
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

    num_parcelas_entrada = params['num_parcelas_entrada'] if params['tipo_pagamento_entrada'] == 'Parcelada' else 0
    total_meses_pagamento = num_parcelas_entrada + params['meses_pre'] + params['meses_pos']
    
    for mes_atual in range(1, total_meses_pagamento + 1):
        data_mes = data_primeira_parcela + relativedelta(months=mes_atual-1)
        data_str = data_mes.strftime("%m/%Y")
        
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
            'Mês/Data': f"{mes_atual} - [{data_str}]", 'Fase': fase, 'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento + juros_mes, 'Amortização Base': amortizacao,
            'Correção INCC ou IPCA diluída (R$)': correcao_paga, 'Taxa de Juros (%)': taxa_juros_mes,
            'Juros (R$)': juros_mes, 'Ajuste INCC (R$)': correcao_mes if fase in ['Entrada','Pré'] else 0,
            'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós' else 0
        })
        
        if fase == 'Pré' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre(params, amortizacao_total_acumulada)
            
    return pd.DataFrame(historico)

# ============================================
# INTEGRAÇÃO COM BANCO CENTRAL (LÓGICA M-2)
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
        dados_por_data = {}
        for idx, row in df.iterrows():
            dados_por_data[idx.strftime("%Y-%m-%d")] = {'incc': row['incc'], 'ipca': row['ipca']}

        current_date_simulacao = data_inicio_simulacao
        for mes in range(1, meses_total + 1):
            data_referencia_indice = current_date_simulacao - relativedelta(months=2)
            data_referencia_str = data_referencia_indice.strftime("%Y-%m-%d")

            if data_referencia_str in dados_por_data:
                valores = dados_por_data[data_referencia_str]
                incc_val = valores['incc']
                ipca_val = valores['ipca']
                if incc_val is not None or ipca_val is not None:
                    ultimo_mes_com_dado = mes
                indices[mes] = {'incc': incc_val, 'ipca': ipca_val}
            else:
                indices[mes] = {'incc': None, 'ipca': None}
            current_date_simulacao += relativedelta(months=1)

        df_display = df.copy()
        df_display.index = df_display.index.strftime('%b/%Y')
        df_display = df_display.rename_axis('Data')
        df_display['incc'] = df_display['incc'].apply(lambda x: f"{x:.4%}" if pd.notnull(x) else "")
        df_display['ipca'] = df_display['ipca'].apply(lambda x: f"{x:.4%}" if pd.notnull(x) else "")
        return indices, ultimo_mes_com_dado, df_display

    except Exception as e:
        st.error(f"Erro ao acessar dados do BC: {str(e)}")
        st.info("Verifique: 1) Conexão com internet 2) Formato da data (MM/AAAA)")
        return {}, 0, pd.DataFrame()

# ============================================
# INTERFACE STREAMLIT
# ============================================

def criar_parametros():
    st.sidebar.header("Parâmetros Gerais")

    params = {
        'mes_assinatura': st.sidebar.text_input(
            "Mês da assinatura (MM/AAAA)", value="04/2025", help="Mês de assinatura do contrato."
        ),
        'mes_primeira_parcela': st.sidebar.text_input(
            "Mês da 1ª parcela (MM/AAAA)", value="05/2025", help="Mês de vencimento do primeiro boleto."
        ),
        'valor_total_imovel': st.sidebar.number_input(
            "Valor total do imóvel", value=455750.0, step=1000.0, format="%.2f"
        ),
        'valor_entrada': st.sidebar.number_input(
            "Valor total da entrada", value=22270.54, step=100.0, format="%.2f"
        ),
    }

    params['tipo_pagamento_entrada'] = st.sidebar.selectbox(
        "Como a entrada é paga?", ['Parcelada', 'Paga no ato'],
        help="'Paga no ato': O valor total da entrada é pago na assinatura. 'Parcelada': A entrada é dividida em boletos a partir do mês da 1ª parcela."
    )

    if params['tipo_pagamento_entrada'] == 'Parcelada':
        params['num_parcelas_entrada'] = st.sidebar.number_input(
            "Nº de parcelas da entrada", min_value=1, value=3, step=1
        )
        if params['num_parcelas_entrada'] > 0:
            params['entrada_mensal'] = params['valor_entrada'] / params['num_parcelas_entrada']
        else:
            params['entrada_mensal'] = 0
    else:
        params['num_parcelas_entrada'] = 0
        params['entrada_mensal'] = 0

    st.sidebar.subheader("Parâmetros de Correção")
    params['inicio_correcao'] = st.sidebar.number_input(
        label="Aplicar correção a partir de qual parcela?", min_value=1, value=1, step=1,
        help="Define o número da parcela a partir da qual a correção (INCC/IPCA) começa. Ex: Se a entrada tem 3x, '1' inicia a correção na 1ª da entrada; '4' inicia na 1ª pós-entrada."
    )
    params['incc_medio'] = st.sidebar.number_input(
        "INCC médio mensal (%)", value=0.5446, step=0.01, format="%.4f", help="Taxa média mensal estimada para o INCC."
    ) / 100
    params['ipca_medio'] = st.sidebar.number_input(
        "IPCA médio mensal (%)", value=0.4669, step=0.01, format="%.4f", help="Taxa média mensal estimada para o IPCA."
    ) / 100

    st.sidebar.subheader("Fases de Pagamento")
    col1, col2 = st.sidebar.columns(2)
    with col1:
        params['meses_pre'] = col1.number_input("Meses pré-chaves", value=17, min_value=0, step=1)
    with col2:
        params['meses_pos'] = col2.number_input("Meses pós-chaves", value=100, min_value=0, step=1)

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
        col_sem1, col_sem2 = st.sidebar.columns(2)
        with col_sem1:
            mes_sem = col_sem1.number_input(f"Mês da {i+1}ª semestral", min_value=0, value=6*(i+1) if i<2 else 0, key=f"sem_mes_{i}")
        with col_sem2:
            valor_sem = col_sem2.number_input(f"Valor {i+1} (R$)", min_value=0.0, value=6000.0 if i<2 else 0.0, key=f"sem_val_{i}", format="%.2f")
        if mes_sem > 0 and valor_sem > 0:
            params['parcelas_semestrais'][int(mes_sem)] = valor_sem

    st.sidebar.write("Parcelas Anuais:")
    col_anu1, col_anu2 = st.sidebar.columns(2)
    with col_anu1:
        mes_anu = col_anu1.number_input("Mês da anual", min_value=0, value=17, key="anu_mes")
    with col_anu2:
        valor_anu = col_anu2.number_input("Valor anual (R$)", min_value=0.0, value=43300.0, key="anu_val", format="%.2f")
    if mes_anu > 0 and valor_anu > 0:
        params['parcelas_anuais'][int(mes_anu)] = valor_anu

    params['percentual_minimo_quitacao'] = 0.3
    params['limite_correcao'] = None
    return params

def mostrar_resultados(df_resultado):
    st.subheader("Tabela de Simulação Detalhada")
    colunas = ['Mês/Data', 'Fase', 'Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)', 'Correção INCC ou IPCA diluída (R$)', 'Amortização Base', 'Taxa de Juros (%)', 'Juros (R$)', 'Parcela Total']
    df_display = df_resultado[colunas].copy()
    
    cols_to_format = ['Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)', 'Correção INCC ou IPCA diluída (R$)', 'Amortização Base', 'Juros (R$)', 'Parcela Total']
    for col in cols_to_format:
        df_display[col] = df_display[col].apply(format_currency)
    
    df_display['Taxa de Juros (%)'] = df_resultado['Taxa de Juros (%)'].apply(
        lambda x: f"{x:.2%}" if x > 0 else "N/A"
    )
    st.dataframe(df_display, use_container_width=True)
    st.session_state.df_export = df_resultado[colunas].copy()

def main():
    st.set_page_config(layout="wide")
    st.title("Simulador de Financiamento Imobiliário 🚧🏗️")

    if 'df_resultado' not in st.session_state:
        st.session_state.df_resultado = pd.DataFrame()

    params = criar_parametros()
    st.header("Opções de Simulação")
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Simular com Médias Estimadas", type="primary", use_container_width=True, help="Usa as taxas de INCC e IPCA médias para todo o período. Ideal para uma projeção geral."):
            st.session_state.df_resultado = simular_financiamento(params.copy())

    with col2:
        if st.button("Simular com Valores Reais do BC", use_container_width=True, help="Busca os índices reais no Banco Central e os aplica onde houver dados. Usa as médias para o período futuro."):
            num_parcelas_entrada = params['num_parcelas_entrada'] if params['tipo_pagamento_entrada'] == 'Parcelada' else 0
            total_meses = num_parcelas_entrada + params['meses_pre'] + params['meses_pos']
            valores_reais, ultimo_mes, _ = buscar_indices_bc(params['mes_primeira_parcela'], total_meses)
            if ultimo_mes > 0:
                st.info(f"Dados reais do BC encontrados e aplicados até a parcela {ultimo_mes}. O restante da simulação usará as médias estimadas.")
            st.session_state.df_resultado = simular_financiamento(params.copy(), valores_reais)

    with col3:
        num_parcelas_entrada = params.get('num_parcelas_entrada', 0)
        limite_correcao = st.number_input(
            "Aplicar correção só até a parcela:", min_value=1, 
            value=params['meses_pre'] + num_parcelas_entrada, 
            step=1, help="Simula um cenário onde a correção (INCC/IPCA) para de ser aplicada após um certo número de parcelas."
        )
        if st.button("Simular com Limite de Correção", use_container_width=True):
            params_sim = params.copy()
            params_sim['limite_correcao'] = limite_correcao
            st.session_state.df_resultado = simular_financiamento(params_sim)

    if not st.session_state.df_resultado.empty:
        mostrar_resultados(st.session_state.df_resultado)
        if 'df_export' in st.session_state:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                st.session_state.df_export.to_excel(writer, index=False, sheet_name='Simulacao')
            st.download_button(
                label="💾 Baixar Planilha (XLSX)", data=output.getvalue(),
                file_name='simulacao_financiamento.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                use_container_width=True
            )

if __name__ == "__main__":
    main()
