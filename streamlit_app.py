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
# LÓGICA DE CÁLCULO DA CONSTRUTORA
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

def simular_financiamento_construtora(params, valores_reais=None):
    historico = []
    try:
        data_assinatura = datetime.strptime(params['mes_assinatura'], "%m/%Y")
        data_primeira_parcela = datetime.strptime(params['mes_primeira_parcela'], "%m/%Y")
    except ValueError:
        st.error("Datas inválidas! Use o formato MM/AAAA.")
        return pd.DataFrame()

    saldo_devedor = params['valor_total_imovel']
    if params['tipo_pagamento_entrada'] == 'Paga no ato':
        saldo_devedor -= params['valor_entrada']
    
    meses_carencia = (data_primeira_parcela.year - data_assinatura.year) * 12 + (data_primeira_parcela.month - data_assinatura.month)
    data_corrente_carencia = data_assinatura
    saldo_temp_carencia = saldo_devedor
    total_correcao_carencia = 0
    for i in range(meses_carencia):
        data_corrente_carencia += relativedelta(months=1)
        correcao_mes_carencia = calcular_correcao_original(saldo_temp_carencia, 0, 'Carência', params, valores_reais)
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

    for mes_atual in range(1, total_meses_pagamento + 1):
        data_mes = data_primeira_parcela + relativedelta(months=mes_atual-1)
        fase = 'Pós-Chaves'
        if mes_atual <= num_parcelas_entrada: fase = 'Entrada'
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']: fase = 'Pré-Chaves'
        
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas_original(parcelas_futuras, mes_atual)
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
            'DataObj': data_mes, 'Fase': fase, 'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento + juros_mes, 'Amortização': amortizacao,
            'Juros': juros_mes, 'Encargos': correcao_paga
        })
            
    return pd.DataFrame(historico)

# ============================================
# LÓGICA DE CÁLCULO DO FINANCIAMENTO BANCÁRIO
# ============================================
def simular_financiamento_bancario(params_caixa):
    historico = []
    taxa_juros_mensal = converter_juros_anual_para_mensal(params_caixa['taxa_juros_anual'] / 100)
    saldo_devedor = params_caixa['valor_financiado']
    data_corrente = params_caixa['data_inicio']
    
    amortizacao_constante = saldo_devedor / params_caixa['prazo_meses']
    
    for _ in range(1, params_caixa['prazo_meses'] + 1):
        juros = saldo_devedor * taxa_juros_mensal
        
        seguro_dfi = (params_caixa['taxa_dfi'] / 100) * params_caixa['valor_avaliacao_imovel']
        seguro_mip = (params_caixa['taxa_mip'] / 100) * saldo_devedor
        encargos = seguro_dfi + seguro_mip + params_caixa['taxa_admin_mensal']
        
        parcela_total = amortizacao_constante + juros + encargos
        saldo_devedor_final = saldo_devedor - amortizacao_constante
        
        historico.append({
            'DataObj': data_corrente, 'Fase': 'Financiamento Bancário', 'Parcela Total': parcela_total,
            'Amortização': amortizacao_constante, 'Juros': juros, 'Encargos': encargos,
            'Saldo Devedor': saldo_devedor_final
        })
        
        saldo_devedor = max(saldo_devedor_final, 0)
        data_corrente += relativedelta(months=1)
        
    return pd.DataFrame(historico)

# ============================================
# COMPONENTES DA INTERFACE
# ============================================
def inicializar_session_state():
    if 'df_unificado' not in st.session_state: 
        st.session_state.df_unificado = pd.DataFrame()

def criar_parametros_sidebar():
    st.sidebar.header("Parâmetros da Fase Construtora")
    params = {}
    params['mes_assinatura'] = st.sidebar.text_input("Mês da assinatura (MM/AAAA)", "09/2025")
    params['mes_primeira_parcela'] = st.sidebar.text_input("Mês da 1ª parcela (MM/AAAA)", "10/2025")
    params['valor_total_imovel'] = st.sidebar.number_input("Valor total do imóvel", value=500000.0, format="%.2f")
    params['valor_entrada'] = st.sidebar.number_input("Valor total da entrada", value=25000.0, format="%.2f")
    params['tipo_pagamento_entrada'] = st.sidebar.selectbox("Como a entrada é paga?", ['Parcelada', 'Paga no ato'])
    
    if params['tipo_pagamento_entrada'] == 'Parcelada':
        params['num_parcelas_entrada'] = st.sidebar.number_input("Nº de parcelas da entrada", min_value=1, value=4)
        params['entrada_mensal'] = params['valor_entrada'] / params['num_parcelas_entrada'] if params['num_parcelas_entrada'] > 0 else 0
    else:
        params['num_parcelas_entrada'] = 0; params['entrada_mensal'] = 0

    st.sidebar.subheader("Parâmetros de Correção")
    params['incc_medio'] = st.sidebar.number_input("INCC médio mensal (%)", value=0.55, format="%.4f") / 100
    params['ipca_medio'] = st.sidebar.number_input("IPCA médio mensal (%)", value=0.45, format="%.4f") / 100
    
    st.sidebar.subheader("Fases de Pagamento (Construtora)")
    c1, c2 = st.sidebar.columns(2)
    params['meses_pre'] = c1.number_input("Meses pré-chaves", value=24)
    params['meses_pos'] = c2.number_input("Meses pós-chaves", value=120)
    c3, c4 = st.sidebar.columns(2)
    params['parcelas_mensais_pre'] = c3.number_input("Parcela mensal pré (R$)", value=2500.0, format="%.2f")
    params['valor_amortizacao_pos'] = c4.number_input("Amortização pós (R$)", value=1500.0, format="%.2f")
    
    params['parcelas_semestrais'] = {}
    params['parcelas_anuais'] = {}
    return params

def criar_parametros_banco_main():
    params_caixa = {}
    with st.expander("🏦 Parâmetros do Financiamento Bancário (Pós-Chaves)", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            params_caixa['taxa_juros_anual'] = st.number_input("Taxa de Juros Efetiva (% a.a.)", value=10.5, format="%.4f")
            params_caixa['prazo_meses'] = st.number_input("Prazo do financiamento (meses)", value=420, step=12)
        with col2:
            params_caixa['taxa_dfi'] = st.number_input("Taxa DFI (% vlr. avaliação)", value=0.012, format="%.4f", help="Seguro de Danos Físicos ao Imóvel.")
            params_caixa['taxa_mip'] = st.number_input("Taxa MIP (% saldo devedor)", value=0.025, format="%.4f", help="Seguro de Morte e Invalidez Permanente.")
        with col3:
            params_caixa['taxa_admin_mensal'] = st.number_input("Taxa de Administração (R$)", value=25.0, format="%.2f")
            # Esses campos serão preenchidos pela simulação
            params_caixa['valor_avaliacao_imovel'] = 0
            params_caixa['valor_financiado'] = 0
            params_caixa['data_inicio'] = datetime.now()
    return params_caixa

def mostrar_resultados(df_unificado):
    st.subheader("Resultado da Simulação Unificada")
    st.dataframe(
        df_unificado,
        column_config={
            "DataObj": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "Parcela Total": st.column_config.NumberColumn(format="R$ %.2f"),
            "Amortização": st.column_config.NumberColumn(format="R$ %.2f"),
            "Juros": st.column_config.NumberColumn(format="R$ %.2f"),
            "Encargos": st.column_config.NumberColumn(format="R$ %.2f"),
            "Saldo Devedor": st.column_config.NumberColumn(format="R$ %.2f"),
        },
        use_container_width=True, height=400
    )

    st.header("📊 Gráficos Comparativos")
    df_unificado['Tipo'] = np.where(df_unificado['Fase'] == 'Financiamento Bancário', 'Financiamento Bancário', 'Fluxo Construtora')
    df_plot_parcela = df_unificado.pivot_table(index='DataObj', columns='Tipo', values='Parcela Total').reset_index()

    st.subheader("Evolução da Parcela (Construtora vs. Banco)")
    colunas_para_plotar = [col for col in df_plot_parcela.columns if col != 'DataObj']
    if colunas_para_plotar:
        st.line_chart(df_plot_parcela, x='DataObj', y=colunas_para_plotar)

    st.subheader("Composição da Parcela (Financiamento Bancário)")
    df_banco = df_unificado[df_unificado['Tipo'] == 'Financiamento Bancário']
    if not df_banco.empty:
        df_banco_composicao = df_banco[['DataObj', 'Amortização', 'Juros', 'Encargos']].set_index('DataObj')
        st.area_chart(df_banco_composicao)

    st.subheader("Evolução do Saldo Devedor")
    st.line_chart(df_unificado.set_index('DataObj'), y='Saldo Devedor')


# ============================================
# APLICAÇÃO PRINCIPAL
# ============================================
def main():
    st.set_page_config(layout="wide", page_title="Simulador de Financiamento Ponta a Ponta")
    st.title("Simulador de Financiamento Imobiliário Ponta a Ponta 🏗️ 🏦")
    
    inicializar_session_state()
    
    # --- NOVO BLOCO DE AVISO ---
    st.info(
        """
        **Lembrete Importante:** Esta simulação utiliza os dados preenchidos nos formulários à esquerda e abaixo. 
        Os campos já vêm com valores padrão para uma demonstração rápida.
        
        Para obter um resultado preciso e alinhado à sua realidade, **verifique e ajuste cuidadosamente cada parâmetro.**
        
        💡 **Dica:** Para os parâmetros do financiamento bancário (taxa de juros, seguros DFI/MIP), recomendamos consultar o 
        **[Simulador Habitacional da CAIXA](https://www8.caixa.gov.br/siopi/simulacao-financiamento/imobiliario/dados-iniciais.asp)** para obter os valores mais atuais para o seu perfil.
        """,
        icon="ℹ️"
    )

    params_construtora = criar_parametros_sidebar()
    params_caixa = criar_parametros_banco_main()
    
    if st.button("Simular Cenário Completo 🏗️➡️🏦", type="primary", use_container_width=True):
        with st.spinner("Executando simulação completa..."):
            # 1. Simular fase da construtora
            df_construtora = simular_financiamento_construtora(params_construtora)
            
            if not df_construtora.empty:
                # 2. Extrair dados finais da fase construtora
                saldo_final_construtora = df_construtora['Saldo Devedor'].iloc[-1]
                data_final_construtora = df_construtora['DataObj'].iloc[-1]
                
                # 3. Preparar parâmetros para a simulação do banco
                params_caixa['valor_financiado'] = saldo_final_construtora
                params_caixa['data_inicio'] = data_final_construtora + relativedelta(months=1)
                params_caixa['valor_avaliacao_imovel'] = params_construtora['valor_total_imovel']
                
                # 4. Simular fase do banco
                df_banco = simular_financiamento_bancario(params_caixa)
                
                # 5. Unificar resultados e salvar no estado da sessão
                st.session_state.df_unificado = pd.concat([df_construtora, df_banco], ignore_index=True)
            else:
                st.error("A simulação da fase da construtora falhou. Verifique os parâmetros.")
                st.session_state.df_unificado = pd.DataFrame()

    # Exibe os resultados se existirem
    if not st.session_state.df_unificado.empty:
        mostrar_resultados(st.session_state.df_unificado)

if __name__ == "__main__":
    main()
