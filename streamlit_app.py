import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io
from datetime import datetime, timedelta
import SGS  # Importação corrigida

def format_currency(value):
    """Formata valores no padrão brasileiro R$ 1.234,56"""
    if pd.isna(value) or value == 0:
        return "0,00"
    
    # Formatação para valores absolutos
    abs_value = abs(value)
    formatted = f"{abs_value:,.2f}"
    
    # Substitui decimais e milhares
    parts = formatted.split('.')
    integer_part = parts[0].replace(',', '.')
    decimal_part = parts[1] if len(parts) > 1 else "00"
    
    # Garante 2 dígitos decimais
    decimal_part = decimal_part.ljust(2, '0')[:2]
    
    # Formata sinal negativo
    sign = "-" if value < 0 else ""
    return f"{sign}{integer_part},{decimal_part}"

def simular_financiamento(
    valor_total_imovel,
    valor_entrada,
    entrada_parcelada,
    entrada_mensal,
    meses_pre,
    meses_pos,
    incc_medio,
    ipca_medio,
    juros_mensal,
    parcelas_mensais_pre,
    parcelas_semestrais,
    parcelas_anuais,
    valor_amortizacao_pos,
    percentual_minimo_quitacao=0.3,
    limite_correcao=None,
    valores_reais=None
):
    # Inicialização do saldo devedor
    saldo_devedor = valor_total_imovel - valor_entrada if not entrada_parcelada else valor_total_imovel
    
    # Lista para armazenar todas as parcelas futuras
    parcelas_futuras = []
    historico = []
    total_amortizado_pre = 0
    total_correcao_paga_pre = 0

    # 1. Construir lista de parcelas futuras para fase pré-chaves
    for mes in range(1, meses_pre + 1):
        valor_parcela = parcelas_mensais_pre
        
        # Adicionar parcelas especiais se existirem neste mês
        if mes in parcelas_semestrais:
            valor_parcela += parcelas_semestrais[mes]
        if mes in parcelas_anuais:
            valor_parcela += parcelas_anuais[mes]
        
        # Adicionar parcela de entrada se aplicável
        if entrada_parcelada and mes <= (valor_entrada / entrada_mensal):
            valor_parcela += entrada_mensal
        
        if valor_parcela > 0:
            parcelas_futuras.append({
                'mes': mes,
                'valor_original': valor_parcela,
                'correcao_acumulada': 0.0,  # Inicializa a correção acumulada
                'tipo': 'pre'
            })

    # 2. Adicionar parcelas da fase pós-chaves
    for mes in range(1, meses_pos + 1):
        mes_global = meses_pre + mes
        parcelas_futuras.append({
            'mes': mes_global,
            'valor_original': valor_amortizacao_pos,
            'correcao_acumulada': 0.0,  # Inicializa a correção acumulada
            'tipo': 'pos'
        })

    # 3. Processar todos os meses sequencialmente
    for mes_atual in range(1, meses_pre + meses_pos + 1):
        fase = 'Pré' if mes_atual <= meses_pre else 'Pós'
        
        # Calcular correção do período
        saldo_inicial_mes = saldo_devedor
        correcao_mes = 0
        
        # Lógica de correção modificada
        if valores_reais and mes_atual in valores_reais:
            # Usar valores reais se fornecidos
            if fase == 'Pré':
                correcao_mes = saldo_devedor * valores_reais[mes_atual]['incc']
            else:
                correcao_mes = saldo_devedor * valores_reais[mes_atual]['ipca']
        elif limite_correcao and mes_atual > limite_correcao:
            # Sem correção após o limite
            correcao_mes = 0
        else:
            # Usar médias padrão
            if fase == 'Pré':
                correcao_mes = saldo_devedor * incc_medio
            else:
                correcao_mes = saldo_devedor * ipca_medio

        # ATUALIZAÇÃO: Correção compõe o saldo devedor
        saldo_devedor += correcao_mes

        # Calcular juros remuneratórios (apenas sobre saldo inicial)
        juros_total = 0
        if fase == 'Pós':
            juros_total = saldo_inicial_mes * juros_mensal

        # DISTRIBUIR correção entre todas as parcelas futuras
        if parcelas_futuras:
            total_valor_original = sum(p['valor_original'] for p in parcelas_futuras)
            for parcela in parcelas_futuras:
                proporcao = parcela['valor_original'] / total_valor_original
                parcela['correcao_acumulada'] += correcao_mes * proporcao

        # Verificar parcelas vencidas
        parcelas_vencidas = [p for p in parcelas_futuras if p['mes'] == mes_atual]
        pagamento_total = 0
        amortizacao_total = 0
        correcao_paga_total = 0
        
        for parcela in parcelas_vencidas:
            # O pagamento da parcela inclui valor original + correção acumulada
            pagamento_parcela = parcela['valor_original'] + parcela['correcao_acumulada']
            pagamento_total += pagamento_parcela
            amortizacao_total += parcela['valor_original']
            correcao_paga_total += parcela['correcao_acumulada']
            
            # Remover parcela da lista de futuras
            parcelas_futuras.remove(parcela)
        
        # ATUALIZAÇÃO: Descontar amortização + correção paga do saldo devedor
        saldo_devedor -= (amortizacao_total + correcao_paga_total)
        saldo_devedor = max(saldo_devedor, 0)
        
        # Juros são pagos separadamente e não reduzem o saldo devedor
        pagamento_total += juros_total
        
        # Atualizar totais amortizados na fase pré
        if fase == 'Pré':
            total_amortizado_pre += amortizacao_total
            total_correcao_paga_pre += correcao_paga_total

        # Registrar no histórico
        historico.append({
            'Mês': mes_atual,
            'Fase': fase,
            'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento_total,
            'Amortização Base': amortizacao_total,
            'Correção INCC ou IPCA diluída (R$)': correcao_paga_total,
            'Juros (R$)': juros_total,
            'Ajuste INCC (R$)': correcao_mes if fase == 'Pré' else 0,
            'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós' else 0
        })
        
        # Verificação de quitação mínima no final da fase pré
        if fase == 'Pré' and mes_atual == meses_pre:
            valor_quitado = (0 if entrada_parcelada else valor_entrada) + total_amortizado_pre
            percentual_quitado = valor_quitado / valor_total_imovel
            
            if percentual_quitado < percentual_minimo_quitacao:
                formatted_valor = format_currency(valor_quitado)
                st.warning(f"Atenção: valor quitado na pré ({formatted_valor}) equivale a {percentual_quitado*100:.2f}% do valor do imóvel, abaixo de {percentual_minimo_quitacao*100:.0f}%.")

    return pd.DataFrame(historico)

# FUNÇÃO: Buscar índices do Banco Central usando pysgs
def buscar_indices_bc(mes_inicial, meses_total):
    """
    Busca índices INCC (código 7456) e IPCA (código 433) do Banco Central
    :param mes_inicial: String no formato 'MM/AAAA'
    :param meses_total: Quantidade total de meses necessários
    :return: Dicionário com índices por mês {mês: {'incc': float, 'ipca': float}}
    """
    try:
        # Converter mês inicial para datetime
        data_inicio = datetime.strptime(mes_inicial, "%m/%Y")
        data_fim = data_inicio + timedelta(days=meses_total*31)  # Aproximação
        
        # Criar instância do SGS
        sgs = SGS()
        
        # Buscar dados
        df_ipca = sgs.fetch(433, start=data_inicio, end=data_fim)  # IPCA
        df_incc = sgs.fetch(7456, start=data_inicio, end=data_fim)  # INCC
        
        # Juntar os dados em um único DataFrame
        df = pd.DataFrame({
            'ipca': df_ipca['value'],
            'incc': df_incc['value']
        })
        
        # Converter de percentual para decimal
        df = df / 100
        
        # Preencher dicionário de índices
        indices = {}
        current_date = data_inicio
        for mes in range(1, meses_total + 1):
            month_str = current_date.strftime("%Y-%m-01")  # Formato YYYY-MM-DD
            
            if month_str in df.index:
                row = df.loc[month_str]
                indices[mes] = {
                    'incc': row['incc'],
                    'ipca': row['ipca']
                }
            else:
                indices[mes] = {'incc': 0, 'ipca': 0}
            
            # Avançar para o próximo mês
            current_date = current_date + timedelta(days=32)
            current_date = current_date.replace(day=1)
        
        st.success("Dados carregados do Banco Central!")
        return indices
        
    except Exception as e:
        st.error(f"Erro ao acessar Banco Central: {str(e)}")
        return {}

# ------------------------------
# Interface Streamlit
# ------------------------------

st.title("Simulador de Financiamento Imobiliário 🚧🏠")

st.sidebar.header("Parâmetros Gerais")

# Mês inicial do financiamento
mes_inicial = st.sidebar.text_input("Mês inicial do financiamento (MM/AAAA)", value="01/2023")

valor_total_imovel = st.sidebar.number_input("Valor total do imóvel", value=455750.0)
valor_entrada = st.sidebar.number_input("Valor de entrada total", value=22270.54)

entrada_parcelada = st.sidebar.checkbox("Entrada parcelada?", value=False)
entrada_mensal = 0
if entrada_parcelada:
    entrada_mensal = st.sidebar.number_input("Valor mensal da entrada", value=5000.0)

meses_pre = st.sidebar.number_input("Meses de pré-chaves", value=17)
meses_pos = st.sidebar.number_input("Meses de pós-chaves", value=100)
incc_medio = st.sidebar.number_input("INCC médio mensal", value=0.00544640781, step=0.0001, format="%.4f")
ipca_medio = st.sidebar.number_input("IPCA médio mensal", value=0.00466933642, step=0.0001, format="%.4f")
juros_mensal = st.sidebar.number_input("Juros remuneratórios mensal", value=0.01, step=0.001, format="%.3f")

parcelas_mensais_pre = st.sidebar.number_input("Parcela mensal pré (R$)", value=3983.38)
valor_amortizacao_pos = st.sidebar.number_input("Amortização mensal pós (R$)", value=3104.62)

st.sidebar.subheader("Parcelas Semestrais")
parcelas_semestrais = {}
for i in range(2):  # Exemplo: 2 semestrais
    mes = st.sidebar.number_input(f"Mês semestral {i+1}", value=6 * (i+1), key=f"sem_{i}")
    valor = st.sidebar.number_input(f"Valor semestral {i+1} (R$)", value=6000.0, key=f"sem_val_{i}")
    if mes > 0 and valor > 0:
        parcelas_semestrais[int(mes)] = valor

st.sidebar.subheader("Parcelas Anuais")
parcelas_anuais = {}
for i in range(1):  # Exemplo: 1 anual
    mes = st.sidebar.number_input(f"Mês anual {i+1}", value=17, key=f"anu_{i}")
    valor = st.sidebar.number_input(f"Valor anual {i+1} (R$)", value=43300.0, key=f"anu_val_{i}")
    if mes > 0 and valor > 0:
        parcelas_anuais[int(mes)] = valor

# Seção: Fonte dos índices
st.sidebar.subheader("Fonte dos Índices")
fonte_indices = st.sidebar.radio("Selecione a fonte:", 
                                ['Valores Médios', 'Banco Central'])

# Tabela para valores reais de índices (opcional)
st.subheader("Valores Reais de Índices (opcional)")
st.write("Preencha os valores reais de INCC e IPCA para meses específicos (em decimal):")

total_meses = meses_pre + meses_pos
df_indices = pd.DataFrame(
    index=range(1, total_meses + 1),
    columns=['INCC', 'IPCA']
)
df_indices.index.name = 'Mês'
df_indices = df_indices.fillna(0.0)

edited_df = st.data_editor(
    df_indices, 
    use_container_width=True,
    height=min(300, 35 * total_meses + 40)
)

# Botões de simulação
col1, col2, col3 = st.columns(3)

with col1:
    if st.button("Simular com Parâmetros Médios"):
        df_resultado = simular_financiamento(
            valor_total_imovel,
            valor_entrada,
            entrada_parcelada,
            entrada_mensal,
            meses_pre,
            meses_pos,
            incc_medio,
            ipca_medio,
            juros_mensal,
            parcelas_mensais_pre,
            parcelas_semestrais,
            parcelas_anuais,
            valor_amortizacao_pos
        )
        st.session_state.df_resultado = df_resultado

with col2:
    limite_correcao = st.number_input("Aplicar correção até o mês:", 
                                     min_value=1, 
                                     max_value=total_meses, 
                                     value=meses_pre,
                                     key='limite_input')
    if st.button("Simular Parcial"):
        df_resultado = simular_financiamento(
            valor_total_imovel,
            valor_entrada,
            entrada_parcelada,
            entrada_mensal,
            meses_pre,
            meses_pos,
            incc_medio,
            ipca_medio,
            juros_mensal,
            parcelas_mensais_pre,
            parcelas_semestrais,
            parcelas_anuais,
            valor_amortizacao_pos,
            limite_correcao=limite_correcao
        )
        st.session_state.df_resultado = df_resultado

with col3:
    if st.button("Simular com Valores Reais"):
        if fonte_indices == 'Banco Central':
            valores_reais = buscar_indices_bc(mes_inicial, total_meses)
        else:
            valores_reais = {}
            
        # Se a busca não retornou nada, tentar a tabela editada
        if not valores_reais:
            # Converter DataFrame para dicionário de valores reais
            for mes, row in edited_df.iterrows():
                incc_val = row['INCC']
                ipca_val = row['IPCA']
                if incc_val != 0 or ipca_val != 0:
                    valores_reais[mes] = {'incc': incc_val, 'ipca': ipca_val}
        
        # Se ainda estiver vazio, usar médios
        if not valores_reais:
            st.warning("Usando valores médios para índices.")
            for mes in range(1, total_meses+1):
                fase = 'Pré' if mes <= meses_pre else 'Pós'
                valores_reais[mes] = {
                    'incc': incc_medio if fase=='Pré' else 0,
                    'ipca': ipca_medio if fase=='Pós' else 0
                }

        df_resultado = simular_financiamento(
            valor_total_imovel,
            valor_entrada,
            entrada_parcelada,
            entrada_mensal,
            meses_pre,
            meses_pos,
            incc_medio,
            ipca_medio,
            juros_mensal,
            parcelas_mensais_pre,
            parcelas_semestrais,
            parcelas_anuais,
            valor_amortizacao_pos,
            valores_reais=valores_reais
        )
        st.session_state.df_resultado = df_resultado

# Exibição dos resultados
if 'df_resultado' in st.session_state:
    df_resultado = st.session_state.df_resultado
    
    st.subheader("Tabela de Simulação Detalhada")
    
    # Ordem das colunas
    col_order = [
        'Mês', 'Fase', 'Saldo Devedor', 'Ajuste INCC (R$)', 'Ajuste IPCA (R$)',
        'Correção INCC ou IPCA diluída (R$)', 'Amortização Base','Juros (R$)',  'Parcela Total'
    ]
    
    # Criar cópia para formatação
    df_display = df_resultado[col_order].copy()
    
    # Formatar valores para padrão brasileiro
    for col in col_order[2:]:  # Todas as colunas numéricas
        df_display[col] = df_display[col].apply(lambda x: format_currency(x))
    
    st.dataframe(df_display)

    st.subheader("Gráficos")

    fig, ax = plt.subplots(1, 2, figsize=(16, 6))

    # Gráfico de saldo devedor
    ax[0].plot(df_resultado['Mês'], df_resultado['Saldo Devedor'], label='Saldo Devedor', color='blue')
    ax[0].set_title("Evolução do Saldo Devedor")
    ax[0].set_xlabel("Mês")
    ax[0].set_ylabel("R$")
    ax[0].grid(True)
    ax[0].legend()

    # Gráfico com composição da parcela
    df_resultado['Amortização Base'] = pd.to_numeric(df_resultado['Amortização Base'])
    df_resultado['Correção INCC ou IPCA diluída (R$)'] = pd.to_numeric(df_resultado['Correção INCC ou IPCA diluída (R$)'])
    df_resultado['Juros (R$)'] = pd.to_numeric(df_resultado['Juros (R$)'])
    
    base_amort = df_resultado['Amortização Base']
    base_correcao = base_amort + df_resultado['Correção INCC ou IPCA diluída (R$)']
    
    ax[1].bar(df_resultado['Mês'], df_resultado['Amortização Base'], label='Amortização Base', color='green')
    ax[1].bar(df_resultado['Mês'], df_resultado['Correção INCC ou IPCA diluída (R$)'], 
             bottom=base_amort, 
             label='Correção Diluída', color='orange')
    ax[1].bar(df_resultado['Mês'], df_resultado['Juros (R$)'], 
             bottom=base_correcao, 
             label='Juros', color='red')
    ax[1].set_title("Composição da Parcela Total")
    ax[1].set_xlabel("Mês")
    ax[1].set_ylabel("R$")
    ax[1].grid(True)
    ax[1].legend()

    st.pyplot(fig)

    st.subheader("Resumo da Composição da Parcela")
    st.write("""
    - **Amortização Base**: Valor original da parcela sem correção
    - **Correção Diluída**: Valor da correção (INCC/IPCA) diluída que está sendo paga no mês
    - **Juros**: Juros remuneratórios aplicados (apenas na fase pós)
    """)

    # Exportar para Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_resultado.to_excel(writer, index=False, sheet_name='Simulação')
    excel_data = output.getvalue()

    st.download_button(
        label="💾 Baixar tabela completa (Excel)",
        data=excel_data,
        file_name='simulacao_financiamento.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
