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
    num_parcelas_entrada = params['num_parcelas_entrada']
    
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
    Calcula correção monetária
    """
    limite = params.get('limite_correcao')
    if limite is not None and mes > limite:
        return 0
    
    if valores_reais is not None:
        if mes in valores_reais:
            idx = valores_reais[mes]
            if fase in ['Entrada','Pré'] and idx.get('incc') is not None:
                return saldo * idx['incc']
            elif fase == 'Pós' and idx.get('ipca') is not None:
                return saldo * idx['ipca']
    
    if fase in ['Entrada','Pré']:
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

def verificar_quitacao_pre(params, total_amortizado):
    """
    Verifica quitacao mínima
    """
    valor_quitado = params['valor_entrada'] + total_amortizado
    percentual = valor_quitado / params['valor_total_imovel']
    
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(valor_quitado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

# ============================================
# LÓGICA PRINCIPAL DE SIMULAÇÃO (CORREÇÃO FINAL)
# ============================================

def simular_financiamento(params, valores_reais=None):
    """
    Executa a simulação completa com lógica corrigida
    """
    num_parcelas_entrada = params['num_parcelas_entrada']
    saldo_devedor = params['valor_total_imovel']
    total_meses = num_parcelas_entrada + params['meses_pre'] + params['meses_pos']
    parcelas_futuras = construir_parcelas_futuras(params)
    historico = []
    total_amortizado_pre = 0
    
    # Converter data inicial para objeto datetime
    try:
        data_inicial = datetime.strptime(params['mes_inicial'], "%m/%Y")
    except:
        st.error("Data inicial inválida! Use o formato MM/AAAA (ex: 04/2025)")
        return pd.DataFrame()
    
    datas_formatadas = []

    for mes_atual in range(1, total_meses + 1):
        # Calcular data correspondente ao mês atual
        data_mes = data_inicial + relativedelta(months=mes_atual-1)
        data_str = data_mes.strftime("%m/%Y")
        datas_formatadas.append(f"{mes_atual} - [{data_str}]")
        
        if mes_atual <= num_parcelas_entrada:
            fase = 'Entrada'
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']:
            fase = 'Pré'
        else:
            fase = 'Pós'
        
        saldo_inicial = saldo_devedor
        
        # 1. Pagar as parcelas do mês atual
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas(parcelas_futuras, mes_atual)
        
        # CORREÇÃO: Descontar a correção paga do saldo devedor
        saldo_devedor -= (amortizacao + correcao_paga)
        
        # 2. Calcular a correção sobre o saldo remanescente APÓS o pagamento
        correcao_mes = calcular_correcao(
            saldo_devedor, 
            mes_atual, 
            fase, 
            params, 
            valores_reais
        )
        
        # 3. Aplicar a correção ao saldo devedor
        saldo_devedor += correcao_mes
        
        # 4. Diluir a correção para TODOS os meses futuros
        if parcelas_futuras and correcao_mes != 0:
            total_original = sum(p['valor_original'] for p in parcelas_futuras)
            if total_original > 0:
                for p in parcelas_futuras:
                    p['correcao_acumulada'] += correcao_mes * (p['valor_original'] / total_original)
        
        juros_mes = saldo_inicial * params['juros_mensal'] if fase == 'Pós' else 0
        
        if fase == 'Pré':
            total_amortizado_pre += amortizacao
        
        # Garantir que o saldo não fique negativo
        saldo_devedor = max(saldo_devedor, 0)
        
        historico.append({
            'Mês': mes_atual,
            'Fase': fase,
            'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento + juros_mes,
            'Amortização Base': amortizacao,
            'Correção INCC ou IPCA diluída (R$)': correcao_paga,
            'Juros (R$)': juros_mes,
            'Ajuste INCC (R$)': correcao_mes if fase in ['Entrada','Pré'] else 0,
            'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós' else 0
        })
        
        if fase == 'Pré' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre(params, total_amortizado_pre)
    
    df_resultado = pd.DataFrame(historico)
    # Adicionar coluna com datas formatadas
    df_resultado['Mês/Data'] = datas_formatadas
    return df_resultado

# ============================================
# INTEGRAÇÃO COM BANCO CENTRAL
# ============================================
def buscar_indices_bc(mes_inicial, meses_total):
    try:
        data_inicio = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_fim = data_inicio + relativedelta(months=meses_total)
        start_str = data_inicio.strftime("%d/%m/%Y")
        end_str = data_fim.strftime("%d/%m/%Y")

        df = sgs.dataframe([7456, 433], start=start_str, end=end_str)
        
        if df.empty:
            return {}, 0, pd.DataFrame()
        
        df = df.rename(columns={7456: 'incc', 433: 'ipca'})
        df['incc'] = df['incc'] / 100
        df['ipca'] = df['ipca'] / 100
        
        indices = {}
        ultimo_mes_com_dado = 0
        current_date = data_inicio
        dados_por_data = {}
        
        for idx, row in df.iterrows():
            data_str = idx.strftime("%Y-%m-%d")
            dados_por_data[data_str] = {
                'incc': row['incc'],
                'ipca': row['ipca']
            }
        
        for mes in range(1, meses_total + 1):
            data_str = current_date.strftime("%Y-%m-%d")
            
            if data_str in dados_por_data:
                valores = dados_por_data[data_str]
                incc_val = valores['incc']
                ipca_val = valores['ipca']
                
                if incc_val is not None or ipca_val is not None:
                    ultimo_mes_com_dado = mes
                    
                indices[mes] = {'incc': incc_val, 'ipca': ipca_val}
            else:
                indices[mes] = {'incc': None, 'ipca': None}
            
            current_date += relativedelta(months=1)

        # Formatar dataframe para exibição
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
# INTERFACE STREAMLIT (ATUALIZADA)
# ============================================

def criar_parametros():
    """
    Cria sidebar com parâmetros de simulação
    """
    st.sidebar.header("Parâmetros Gerais")
    params = {
        'mes_inicial': st.sidebar.text_input("Mês inicial (MM/AAAA)", value="04/2025",
                                            help="Mês de início do financiamento"),
        'valor_total_imovel': st.sidebar.number_input("Valor total do imóvel", value=455750.0,
                                                    help="Valor total do imóvel a ser financiado."),
        'valor_entrada': st.sidebar.number_input("Valor de entrada", value=22270.54,
                                               help="Valor total de entrada pago pelo comprador"),
        'num_parcelas_entrada': st.sidebar.number_input("Número de parcelas da entrada", min_value=1, value=3, step=1,
                                                      help="Número de meses em que a entrada será parcelada"),
    }
    
    # Inicializar dicionários vazios para parcelas extras
    params['parcelas_semestrais'] = {}
    params['parcelas_anuais'] = {}
    
    # Grupo de campos para fases pré e pós
    st.sidebar.subheader("Fases de Pagamento")
    col1, col2 = st.sidebar.columns(2)
    
    with col1:
        params['meses_pre'] = col1.number_input("Meses pré-chaves", value=17,
                                              help="Quantidade de meses da fase pré-chaves (durante a obra)")
    with col2:
        params['meses_pos'] = col2.number_input("Meses pós-chaves", value=100,
                                               help="Quantidade de meses da fase pós-chaves (após a entrega das chaves)")
    
    col3, col4 = st.sidebar.columns(2)
    with col3:
        params['parcelas_mensais_pre'] = col3.number_input("Valor parcela pré (R$)", value=3983.38,
                                                          help="Valor mensal durante a fase pré-chaves")
    with col4:
        params['valor_amortizacao_pos'] = col4.number_input("Valor parcela pós (R$)", value=3104.62,
                                                           help="Valor mensal durante a fase pós-chaves")
    
    # Parcelas extras (até 4 semestrais)
    st.sidebar.subheader("Parcelas Extras")
    
    # Parcelas Semestrais (até 4)
    st.sidebar.write("Parcelas Semestrais:")
    semestrais = []
    for i in range(4):
        col_sem1, col_sem2 = st.sidebar.columns(2)
        with col_sem1:
            mes_sem = col_sem1.number_input(f"Semestral {i+1}", min_value=0, value=6*(i+1) if i<4 else 0, key=f"sem_mes_{i}")
        with col_sem2:
            valor_sem = col_sem2.number_input(f"Valor {i+1} (R$)", min_value=0.0, value=6000.0 if i<2 else 0.0, key=f"sem_val_{i}")
        if mes_sem > 0 and valor_sem > 0:
            semestrais.append((mes_sem, valor_sem))
    
    # Adicionar todas as semestrais ao dicionário
    for mes, valor in semestrais:
        if mes > 0 and valor > 0:
            params['parcelas_semestrais'][int(mes)] = valor

    # Parcelas Anuais
    st.sidebar.write("Parcelas Anuais:")
    col_anu1, col_anu2 = st.sidebar.columns(2)
    with col_anu1:
        mes_anu = col_anu1.number_input("Mês", min_value=0, value=17, key="anu_mes")
    with col_anu2:
        valor_anu = col_anu2.number_input("Valor (R$)", min_value=0.0, value=43300.0, key="anu_val")
    if mes_anu > 0 and valor_anu > 0:
        params['parcelas_anuais'][int(mes_anu)] = valor_anu

    # Parâmetros de correção (última seção)
    st.sidebar.subheader("Parâmetros de Correção")
    params['incc_medio'] = st.sidebar.number_input("INCC médio mensal", value=0.00544640781, step=0.0001, format="%.4f",
                                                 help="Taxa média mensal de correção pelo INCC (usada na fase pré-chaves)")
    params['ipca_medio'] = st.sidebar.number_input("IPCA médio mensal", value=0.00466933642, step=0.0001, format="%.4f",
                                                 help="Taxa média mensal de correção pelo IPCA (usada na fase pós-chaves)")
    params['juros_mensal'] = st.sidebar.number_input("Juros mensal", value=0.01, step=0.001, format="%.3f",
                                                   help="Taxa de juros mensal aplicada na fase pós-chaves")
    
    # Calcular valor mensal da entrada
    params['entrada_mensal'] = params['valor_entrada'] / params['num_parcelas_entrada']
    
    params['percentual_minimo_quitacao'] = 0.3
    params['limite_correcao'] = None

    return params

def mostrar_resultados(df_resultado):
    """
    Exibe resultados da simulação (sem gráficos)
    """
    st.subheader("Tabela de Simulação Detalhada")
    
    # Definir ordem e seleção de colunas para visualização e exportação
    colunas = [
        'Mês/Data', 
        'Fase', 
        'Saldo Devedor', 
        'Ajuste INCC (R$)', 
        'Ajuste IPCA (R$)', 
        'Correção INCC ou IPCA diluída (R$)', 
        'Amortização Base', 
        'Juros (R$)', 
        'Parcela Total'
    ]
    
    # Criar DataFrame para visualização (com formatação)
    df_display = df_resultado[colunas].copy()
    
    # Formatar colunas numéricas para visualização
    for col in colunas[2:]:
        df_display[col] = df_display[col].apply(format_currency)
    
    st.dataframe(df_display)
    
    # Armazenar DataFrame para exportação (sem formatação, mesmo conjunto de colunas)
    st.session_state.df_export = df_resultado[colunas].copy()

def main():
    st.title("Simulador/Estimativa de Financiamento Imobiliário 🚧🏠")
    
    # Inicializar variáveis de sessão
    if 'df_indices' not in st.session_state:
        st.session_state.df_indices = None
    
    # Carregar parâmetros
    params = criar_parametros()
    total_meses = params['num_parcelas_entrada'] + params['meses_pre'] + params['meses_pos']
    
    # Botões de simulação
    col1, col2, col3 = st.columns(3)
    valores_reais = None

    with col1:
        if st.button("Simular com Parâmetros Médios", 
                    help="Usa taxas médias de inflação (INCC e IPCA) para todo o período do financiamento. Mostra uma projeção completa baseada nas estimativas fornecidas nos campos de 'INCC médio mensal' e 'IPCA médio mensal'. Ideal para ter uma visão geral do financiamento."):
            params_sim = params.copy()
            params_sim['limite_correcao'] = None
            st.session_state.df_resultado = simular_financiamento(params_sim)

    with col2:
        limite_correcao = st.number_input(
            "Aplicar correção até o mês:", 
            min_value=1, max_value=total_meses, value=params['meses_pre'],
            help="Define o limite de meses para aplicação da correção monetária na simulação parcial. Por exemplo: se colocar '24', a inflação só será aplicada nos primeiros 2 anos do financiamento."
        )
        if st.button("Simular Parcial", 
                    help="Simula apenas parte do financiamento, aplicando correção monetária até o mês específico que você definir. Após esse mês, o saldo não será mais corrigido. Use para ver como ficaria seu financiamento se a correção parasse em determinado momento."):
            params_sim = params.copy()
            params_sim['limite_correcao'] = limite_correcao
            st.session_state.df_resultado = simular_financiamento(params_sim)

    with col3:
        if st.button("Simular com Valores Reais", 
                    help="Busca automaticamente as taxas de inflação reais (INCC e IPCA) registradas pelo Banco Central. A correção será aplicada apenas até o último mês com dados disponíveis. Requer conexão com internet e mostra valores oficiais históricos."):
            valores_reais, ultimo_mes_com_dado, df_indices = buscar_indices_bc(params['mes_inicial'], total_meses)
            params_sim = params.copy()
            params_sim['limite_correcao'] = ultimo_mes_com_dado
            st.session_state.df_resultado = simular_financiamento(params_sim, valores_reais)
            st.session_state.df_indices = df_indices
            st.info(f"⚠️ Correção aplicada apenas até o mês {ultimo_mes_com_dado} (dados reais disponíveis)")

    # Exibir resultados
    if 'df_resultado' in st.session_state:
        mostrar_resultados(st.session_state.df_resultado)
        
        # Botão de download da planilha (usando o mesmo DataFrame formatado)
        if 'df_export' in st.session_state:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                st.session_state.df_export.to_excel(writer, index=False)
            st.download_button(
                label="💾 Baixar planilha de simulação",
                data=output.getvalue(),
                file_name='simulacao_financiamento.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )

if __name__ == "__main__":
    main()
