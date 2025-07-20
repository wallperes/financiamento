import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io
from datetime import datetime, timedelta
import sgs
from dateutil.relativedelta import relativedelta

# ============================================
# FUNÇÕES UTILITÁRIAS (ATUALIZADAS)
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
    
    # Fase de Entrada (todas as parcelas)
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
        if mes_local in params['parcelas_semestrais']:
            valor_parcela += params['parcelas_semestrais'][mes_local]
        if mes_local in params['parcelas_anuais']:
            valor_parcela += params['parcelas_anuais'][mes_local]
        
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
    Corrigida para:
    - Aplicar correção apenas quando houver índice real disponível
    - Não usar médias após dados reais
    - Respeitar limite de correção
    """
    # Verificar limite de correção
    limite = params.get('limite_correcao')
    if limite is not None and mes > limite:
        return 0
    
    # Se temos valores reais, tentar usá-los
    if valores_reais is not None:
        if mes in valores_reais:
            idx = valores_reais[mes]
            # Usar índice real apenas se disponível para a fase
            if fase in ['Entrada','Pré'] and idx.get('incc') is not None:
                return saldo * idx['incc']
            elif fase == 'Pós' and idx.get('ipca') is not None:
                return saldo * idx['ipca']
    
    # Se não temos valores reais, usar a média
    if fase in ['Entrada','Pré']:
        return saldo * params['incc_medio']
    elif fase == 'Pós':
        return saldo * params['ipca_medio']
    
    return 0

def processar_parcelas_vencidas(parcelas_futuras, mes_atual):
    """
    Processas parcelas vencidas e atualiza saldos
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
    Verifica se quitacao mínima foi atingida no final do pré
    """
    valor_quitado = params['valor_entrada'] + total_amortizado
    percentual = valor_quitado / params['valor_total_imovel']
    
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(valor_quitado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

# ============================================
# LÓGICA PRINCIPAL DE SIMULAÇÃO (ATUALIZADA)
# ============================================

def simular_financiamento(params, valores_reais=None):
    """
    Executa a simulação completa do financiamento
    """
    # Inicialização
    num_parcelas_entrada = params['num_parcelas_entrada']
    
    # Saldo devedor inicial é o valor total do imóvel
    saldo_devedor = params['valor_total_imovel']
    
    # Total de meses da simulação (incluindo todas as fases)
    total_meses = num_parcelas_entrada + params['meses_pre'] + params['meses_pos']
    
    # Construir todas as parcelas futuras (incluindo todas as de entrada)
    parcelas_futuras = construir_parcelas_futuras(params)
    historico = []
    total_amortizado_pre = 0

    for mes_atual in range(1, total_meses + 1):
        # Determinar a fase atual
        if mes_atual <= num_parcelas_entrada:
            fase = 'Entrada'
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']:
            fase = 'Pré'
        else:
            fase = 'Pós'
        
        saldo_inicial = saldo_devedor
        
        # Calcular correção monetária
        correcao_mes = calcular_correcao(
            saldo_devedor, 
            mes_atual, 
            fase, 
            params, 
            valores_reais
        )
        saldo_devedor += correcao_mes
        
        # Aplicar correção nas parcelas futuras
        if parcelas_futuras and correcao_mes != 0:
            total_original = sum(p['valor_original'] for p in parcelas_futuras)
            if total_original > 0:  # Evitar divisão por zero
                for p in parcelas_futuras:
                    p['correcao_acumulada'] += correcao_mes * (p['valor_original'] / total_original)
        
        # Calcular juros (apenas pós-obra)
        juros_mes = saldo_inicial * params['juros_mensal'] if fase == 'Pós' else 0
        
        # Processar parcelas vencidas
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas(parcelas_futuras, mes_atual)
        saldo_devedor -= (amortizacao + correcao_paga)
        saldo_devedor = max(saldo_devedor, 0)
        
        # Atualizar totais
        if fase == 'Pré':
            total_amortizado_pre += amortizacao
        
        # Registrar histórico
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
        
        # Verificar quitacao mínima ao final do pré
        if fase == 'Pré' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre(params, total_amortizado_pre)
    
    return pd.DataFrame(historico)

# ============================================
# INTEGRAÇÃO COM BANCO CENTRAL (SGS)
# ============================================
def buscar_indices_bc(mes_inicial, meses_total):
    try:
        # Converter para objetos datetime
        data_inicio = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_fim = data_inicio + relativedelta(months=meses_total)
        
        # Formatar datas para o padrão SGS
        start_str = data_inicio.strftime("%d/%m/%Y")
        end_str = data_fim.strftime("%d/%m/%Y")

        # Buscar dados do BC
        df = sgs.dataframe([7456, 433], start=start_str, end=end_str)
        
        # Verificar se obteve resultados
        if df.empty:
            st.warning("⚠️ Nenhum dado retornado pelo Banco Central")
            return {}, 0
        
        # Renomear colunas
        df = df.rename(columns={7456: 'incc', 433: 'ipca'})
        
        # Converter para decimal (valores vêm como porcentagem)
        df['incc'] = df['incc'] / 100
        df['ipca'] = df['ipca'] / 100
        
        # Criar dicionário por número de mês sequencial
        indices = {}
        ultimo_mes_com_dado = 0
        current_date = data_inicio
        
        # Criar um dicionário rápido para acesso por data
        dados_por_data = {}
        for idx, row in df.iterrows():
            # Converter a data para formato YYYY-MM-DD
            data_str = idx.strftime("%Y-%m-%d")
            dados_por_data[data_str] = {
                'incc': row['incc'],
                'ipca': row['ipca']
            }
        
        for mes in range(1, meses_total + 1):
            # Formatar a data no mesmo padrão usado no índice
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

        st.subheader("Dados Capturados do Banco Central")
        if not df.empty:
            st.write(f"Período: {start_str} a {end_str}")
            st.write(f"📊 Índices reais disponíveis até o mês {ultimo_mes_com_dado}")
            
            # Formatar e exibir dados
            df_display = df.copy()
            df_display.index = df_display.index.strftime('%b/%Y')
            df_display = df_display.rename_axis('Data')
            st.dataframe(df_display.tail().style.format({'incc': '{:.4%}', 'ipca': '{:.4%}'}))
        else:
            st.warning("Nenhum dado encontrado para o período")

        return indices, ultimo_mes_com_dado
        
    except Exception as e:
        st.error(f"Erro ao acessar dados do BC: {str(e)}")
        st.info("Verifique: 1) Conexão com internet 2) Formato da data (MM/AAAA)")
        return {}, 0

# ============================================
# INTERFACE STREAMLIT (ATUALIZADA)
# ============================================

def criar_parametros():
    """
    Cria sidebar com parâmetros de simulação
    """
    st.sidebar.header("Parâmetros Gerais")
    params = {
        'mes_inicial': st.sidebar.text_input("Mês inicial (MM/AAAA)", value="01/2023"),
        'valor_total_imovel': st.sidebar.number_input("Valor total do imóvel", value=455750.0),
        'valor_entrada': st.sidebar.number_input("Valor de entrada", value=22270.54),
        'num_parcelas_entrada': st.sidebar.number_input("Número de parcelas da entrada", min_value=1, value=1, step=1),
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
    
    # Calcular valor mensal da entrada
    params['entrada_mensal'] = params['valor_entrada'] / params['num_parcelas_entrada']
    
    # Parcelas extras
    st.sidebar.subheader("Parcelas Semestrais")
    for i in range(2):
        mes = st.sidebar.number_input(f"Mês semestral {i+1}", value=6 * (i+1), key=f"sem_{i}")
        valor = st.sidebar.number_input(f"Valor semestral {i+1} (R$)", value=6000.0, key=f"sem_val_{i}")
        if mes > 0 and valor > 0:
            params['parcelas_semestrais'][int(mes)] = valor

    st.sidebar.subheader("Parcelas Anuais")
    for i in range(1):
        mes = st.sidebar.number_input(f"Mês anual {i+1}", value=17, key=f"anu_{i}")
        valor = st.sidebar.number_input(f"Valor anual {i+1} (R$)", value=43300.0, key=f"anu_val_{i}")
        if mes > 0 and valor > 0:
            params['parcelas_anuais'][int(mes)] = valor

    return params

def mostrar_resultados(df_resultado):
    """
    Exibe resultados da simulação
    """
    st.subheader("Tabela de Simulação Detalhada")
    colunas = ['Mês', 'Fase', 'Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)', 
               'Correção INCC ou IPCA diluída (R$)', 'Amortização Base', 'Juros (R$)', 'Parcela Total']
    
    df_display = df_resultado[colunas].copy()
    for col in colunas[2:]:
        df_display[col] = df_display[col].apply(format_currency)
    st.dataframe(df_display)

    st.subheader("Gráficos")
    fig, axs = plt.subplots(1, 2, figsize=(16, 6))
    
    # Gráfico Saldo Devedor
    axs[0].plot(df_resultado['Mês'], df_resultado['Saldo Devedor'], 'b-', label='Saldo Devedor')
    axs[0].set_title("Evolução do Saldo Devedor")
    axs[0].set_xlabel("Mês")
    axs[0].set_ylabel("R$")
    axs[0].grid(True)
    
    # Gráfico Composição das Parcelas
    base_amort = df_resultado['Amortização Base']
    base_correcao = base_amort + df_resultado['Correção INCC ou IPCA diluída (R$)']
    
    axs[1].bar(df_resultado['Mês'], df_resultado['Amortização Base'], label='Amortização')
    axs[1].bar(df_resultado['Mês'], df_resultado['Correção INCC ou IPCA diluída (R$)'], 
             bottom=base_amort, label='Correção')
    axs[1].bar(df_resultado['Mês'], df_resultado['Juros (R$)'], 
             bottom=base_correcao, label='Juros')
    axs[1].set_title("Composição das Parcelas")
    axs[1].set_xlabel("Mês")
    axs[1].set_ylabel("R$")
    axs[1].legend()
    axs[1].grid(True)
    
    st.pyplot(fig)
    
    # Botão de download
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
    
    # Carregar parâmetros
    params = criar_parametros()
    total_meses = params['num_parcelas_entrada'] + params['meses_pre'] + params['meses_pos']
    
    # Botões de simulação
    col1, col2, col3 = st.columns(3)
    valores_reais = None
    ultimo_mes_com_dado = 0  # Armazenar último mês com dados reais

    with col1:
        if st.button("Simular com Parâmetros Médios"):
            # Resetar limite e usar médias
            params_sim = params.copy()
            params_sim['limite_correcao'] = None
            st.session_state.df_resultado = simular_financiamento(params_sim)

    with col2:
        # Definir limite de correção
        limite_correcao = st.number_input(
            "Aplicar correção até o mês:", 
            min_value=1, max_value=total_meses, value=params['meses_pre']
        )
        if st.button("Simular Parcial"):
            params_sim = params.copy()
            params_sim['limite_correcao'] = limite_correcao
            st.session_state.df_resultado = simular_financiamento(params_sim)

    with col3:
        if st.button("Simular com Valores Reais"):
            valores_reais, ultimo_mes_com_dado = buscar_indices_bc(params['mes_inicial'], total_meses)
            params_sim = params.copy()
            # Usar apenas dados reais disponíveis
            params_sim['limite_correcao'] = ultimo_mes_com_dado
            st.session_state.df_resultado = simular_financiamento(params_sim, valores_reais)
            st.info(f"⚠️ Correção aplicada apenas até o mês {ultimo_mes_com_dado} (dados reais disponíveis)")

    # Exibir resultados
    if 'df_resultado' in st.session_state:
        mostrar_resultados(st.session_state.df_resultado)

if __name__ == "__main__":
    main()
