import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io
from datetime import datetime, timedelta
import sgs  # Biblioteca correta para acesso ao SGS do Banco Central

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
    valor_entrada = params['valor_entrada']
    entrada_mensal = params['entrada_mensal']
    
    # Pré-chaves
    for mes in range(1, params['meses_pre'] + 1):
        valor_parcela = params['parcelas_mensais_pre']
        
        if mes in params['parcelas_semestrais']:
            valor_parcela += params['parcelas_semestrais'][mes]
        if mes in params['parcelas_anuais']:
            valor_parcela += params['parcelas_anuais'][mes]
        if params['entrada_parcelada'] and mes <= (valor_entrada / entrada_mensal):
            valor_parcela += entrada_mensal
        
        if valor_parcela > 0:
            parcelas.append({
                'mes': mes,
                'valor_original': valor_parcela,
                'correcao_acumulada': 0.0,
                'tipo': 'pre'
            })
    
    # Pós-chaves
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
    Calcula a correção monetária do mês
    """
    # Verificar se temos valores reais para usar
    if valores_reais and mes in valores_reais:
        idx = valores_reais[mes]
        # Usa valor real somente se disponível
        if fase == 'Pré' and idx['incc'] is not None:
            return saldo * idx['incc']
        elif fase == 'Pós' and idx['ipca'] is not None:
            return saldo * idx['ipca']
    
    # Verificar limite de correção (se aplicável)
    limite_correcao = params.get('limite_correcao')
    if limite_correcao is not None and mes > limite_correcao:
        return 0
    
    # Se não há valor real e não há limite de correção, não aplicamos correção
    return 0

def processar_parcelas_vencidas(parcelas_futuras, mes_atual):
    """
    Processa parcelas vencidas e atualiza saldos
    """
    vencidas = [p for p in parcelas_futuras if p['mes'] == mes_atual]
    pagamento = amortizacao = correcao_paga = 0
    
    for parcela in vencidas:
        pagamento += parcela['valor_original'] + parcela['correcao_acumulada']
        amortizacao += parcela['valor_original']
        correcao_paga += parcela['correcao_acumulada']
        parcelas_futuras.remove(parcela)
    
    return pagamento, amortizacao, correcao_paga

def verificar_quitacao_pre(params, total_amortizado):
    """
    Verifica se quitacao mínima foi atingida no final do pré
    """
    valor_quitado = (0 if params['entrada_parcelada'] else params['valor_entrada']) + total_amortizado
    percentual = valor_quitado / params['valor_total_imovel']
    
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(valor_quitado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

# ============================================
# LÓGICA PRINCIPAL DE SIMULAÇÃO
# ============================================

def simular_financiamento(params, valores_reais=None):
    """
    Executa a simulação completa do financiamento
    """
    # Inicialização
    saldo_devedor = params['valor_total_imovel'] - params['valor_entrada']
    if params['entrada_parcelada']:
        saldo_devedor = params['valor_total_imovel']
    
    parcelas_futuras = construir_parcelas_futuras(params)
    historico = []
    total_amortizado_pre = 0
    total_meses = params['meses_pre'] + params['meses_pos']

    for mes_atual in range(1, total_meses + 1):
        fase = 'Pré' if mes_atual <= params['meses_pre'] else 'Pós'
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
            'Ajuste INCC (R$)': correcao_mes if fase == 'Pré' else 0,
            'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós' else 0
        })
        
        # Verificar quitacao mínima ao final do pré
        if fase == 'Pré' and mes_atual == params['meses_pre']:
            verificar_quitacao_pre(params, total_amortizado_pre)
    
    return pd.DataFrame(historico)

# ============================================
# INTEGRAÇÃO SIMPLIFICADA COM BANCO CENTRAL (SGS)
# ============================================

def buscar_indices_bc(mes_inicial, meses_total):
    """
    Busca índices INCC-M (192) e IPCA (433) usando a abordagem direta da documentação
    """
    try:
        # Converter para objetos datetime
        data_inicio = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_fim = data_inicio + timedelta(days=meses_total * 31)
        
        # Formatar datas no padrão DD/MM/YYYY que o SGS espera
        start_str = data_inicio.strftime("%d/%m/%Y")
        end_str = data_fim.strftime("%d/%m/%Y")
        
        # Buscar dados diretamente com sgs.dataframe()
        df = sgs.dataframe([192, 433], start=start_str, end=end_str)
        
        # Renomear colunas para facilitar
        df = df.rename(columns={192: 'incc', 433: 'ipca'})
        
        # Converter para decimal (valores vêm como porcentagem)
        df['incc'] = df['incc'] / 100
        df['ipca'] = df['ipca'] / 100
        
        # Criar dicionário por número de mês
        indices = {}
        current_date = data_inicio
        
        for mes in range(1, meses_total + 1):
            # Verificar se temos dados para este mês
            if current_date in df.index:
                row = df.loc[current_date]
                incc_val = row['incc'] if not pd.isna(row['incc']) else None
                ipca_val = row['ipca'] if not pd.isna(row['ipca']) else None
            else:
                incc_val = None
                ipca_val = None
            
            indices[mes] = {'incc': incc_val, 'ipca': ipca_val}
            
            # Avançar para o próximo mês
            next_month = current_date.replace(day=28) + timedelta(days=4)
            current_date = next_month.replace(day=1)
        
        # Depuração: mostrar dados capturados
        st.subheader("Dados Capturados do Banco Central")
        if not df.empty:
            st.write(f"Período: {start_str} a {end_str}")
            st.write(f"{len(df)} registros encontrados")
            
            # Mostrar primeiros e últimos registros
            st.write("Primeiros registros:")
            st.dataframe(df.head().style.format({
                'incc': '{:.4%}',
                'ipca': '{:.4%}'
            }))
            
            st.write("Últimos registros:")
            st.dataframe(df.tail().style.format({
                'incc': '{:.4%}',
                'ipca': '{:.4%}'
            }))
        else:
            st.warning("Nenhum dado encontrado para o período")
        
        return indices
        
    except Exception as e:
        st.error(f"Erro ao acessar dados do BC: {str(e)}")
        st.info("Verifique: 1) Conexão com internet 2) Formato da data inicial (MM/AAAA)")
        return {}
# ============================================
# INTERFACE STREAMLIT
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
        'limite_correcao': None  # Inicializado como None
    }
    
    if params['entrada_parcelada']:
        params['entrada_mensal'] = st.sidebar.number_input("Valor mensal da entrada", value=5000.0)

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

    params['fonte_indices'] = st.sidebar.radio("Fonte dos índices:", ['Valores Médios', 'Banco Central'])
    return params

def criar_editor_indices(total_meses):
    """
    Cria editor para valores reais de índices
    """
    st.subheader("Valores Reais de Índices")
    df = pd.DataFrame(index=range(1, total_meses + 1), columns=['INCC', 'IPCA'])
    df.index.name = 'Mês'
    return st.data_editor(df.fillna(0.0), use_container_width=True, height=min(300, 35 * total_meses + 40))

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
    total_meses = params['meses_pre'] + params['meses_pos']
    
    # Editor de índices
    edited_df = criar_editor_indices(total_meses)
    
    # Botões de simulação
    col1, col2, col3 = st.columns(3)
    valores_reais = None

    with col1:
        if st.button("Simular com Parâmetros Médios"):
            st.session_state.df_resultado = simular_financiamento(params)

    with col2:
        # Definir limite de correção
        limite_correcao = st.number_input(
            "Aplicar correção até o mês:", 
            min_value=1, max_value=total_meses, value=params['meses_pre']
        )
        if st.button("Simular Parcial"):
            # Atualizar o parâmetro antes da simulação
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

    # Exibir resultados
    if 'df_resultado' in st.session_state:
        mostrar_resultados(st.session_state.df_resultado)

if __name__ == "__main__":
    main()
