import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import sgs
from dateutil.relativedelta import relativedelta

# ============================================
# UTILITÃRIAS
# ============================================
def format_currency(value):
    """Formata valores no padrão brasileiro R$"""
    if pd.isna(value) or not isinstance(value, (int, float)):
        return "R$ 0,00"
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def converter_juros_anual_para_mensal(taxa_anual):
    """Converte taxa anual (ex: 0.12) para taxa efetiva mensal."""
    if taxa_anual <= -1:
        return -1
    return (1 + taxa_anual)**(1/12) - 1

# ============================================
# LÓGICA DA CONSTRUTORA (MANTIDA)
# ============================================
def construir_parcelas_futuras(params):
    parcelas = []
    num_parcelas_entrada = params['num_parcelas_entrada'] if params['tipo_pagamento_entrada'] == 'Parcelada' else 0

    for mes in range(1, num_parcelas_entrada + 1):
        parcelas.append({'mes': mes, 'valor_original': params['entrada_mensal'], 'correcao_acumulada': 0.0, 'tipo': 'entrada'})

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
            parcelas.append({'mes': mes, 'valor_original': valor_parcela, 'correcao_acumulada': 0.0, 'tipo': 'pre'})

    for mes in range(num_parcelas_entrada + 1 + params['meses_pre'],
                      num_parcelas_entrada + 1 + params['meses_pre'] + params['meses_pos']):
        parcelas.append({'mes': mes, 'valor_original': params['valor_amortizacao_pos'], 'correcao_acumulada': 0.0, 'tipo': 'pos'})

    return parcelas

def calcular_correcao(saldo, mes, fase, params, valores_reais):
    # Regra de início de correção
    if fase not in ['Assinatura', 'Carência']:
        inicio_correcao = params.get('inicio_correcao', 1)
        if inicio_correcao == 0:
            inicio_correcao = 1
        if mes < inicio_correcao:
            return 0

    # Limite manual (opcional)
    limite = params.get('limite_correcao')
    if limite is not None and mes > limite:
        return 0

    # Se houver séries reais fornecidas (valores_reais[mes] = {'incc':..., 'ipca':..., 'tr':...})
    if valores_reais is not None and mes in valores_reais:
        idx = valores_reais[mes]
        if fase in ['Entrada','Pré', 'Carência'] and idx.get('incc') is not None and pd.notna(idx.get('incc')):
            return saldo * idx['incc']
        elif fase == 'Pós' and idx.get('ipca') is not None and pd.notna(idx.get('ipca')):
            return saldo * idx['ipca']

    # fallbacks para médias
    if fase in ['Entrada','Pré', 'Carência']:
        return saldo * params.get('incc_medio', 0)
    elif fase == 'Pós':
        return saldo * params.get('ipca_medio', 0)
    return 0

def processar_parcelas_vencidas(parcelas_futuras, mes_atual):
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
    percentual = total_amortizado_acumulado / params['valor_total_imovel']
    if percentual < params['percentual_minimo_quitacao']:
        valor_fmt = format_currency(total_amortizado_acumulado)
        st.warning(f"Atenção: valor quitado na pré ({valor_fmt}) equivale a {percentual*100:.2f}% do valor do imóvel, abaixo de {params['percentual_minimo_quitacao']*100:.0f}%.")

def simular_financiamento(params, valores_reais=None):
    """Simulação da construtora (pré + carência + pós interno) -- referência para a fase pré."""
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

    historico.append({'DataObj': data_assinatura, 'Mês/Data': f"Assinatura [{data_assinatura.strftime('%m/%Y')}]", 'Fase': 'Assinatura', 'Saldo Devedor': saldo_devedor, 'Parcela Total': amortizacao_assinatura, 'Amortização Base': amortizacao_assinatura, 'Correção INCC ou IPCA diluída (R$)': 0, 'Taxa de Juros (%)': 0, 'Juros (R$)': 0, 'Ajuste INCC (R$)': 0, 'Ajuste IPCA (R$)': 0})

    meses_carencia = (datetime.strptime(params['mes_primeira_parcela'], "%m/%Y").year - data_assinatura.year) * 12 + (datetime.strptime(params['mes_primeira_parcela'], "%m/%Y").month - data_assinatura.month)
    data_corrente_carencia = data_assinatura
    saldo_temp_carencia = saldo_devedor
    total_correcao_carencia = 0
    for i in range(meses_carencia):
        data_corrente_carencia += relativedelta(months=1)
        correcao_mes_carencia = calcular_correcao(saldo_temp_carencia, 0, 'Carência', params, valores_reais)
        total_correcao_carencia += correcao_mes_carencia
        saldo_temp_carencia += correcao_mes_carencia
        historico.append({'DataObj': data_corrente_carencia, 'Mês/Data': f"Gerou Correção [{data_corrente_carencia.strftime('%m/%Y')}]", 'Fase': 'Carência', 'Saldo Devedor': saldo_devedor, 'Parcela Total': 0, 'Amortização Base': 0, 'Correção INCC ou IPCA diluída (R$)': 0, 'Taxa de Juros (%)': 0, 'Juros (R$)': 0, 'Ajuste INCC (R$)': correcao_mes_carencia, 'Ajuste IPCA (R$)': 0})

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
        data_mes = datetime.strptime(params['mes_primeira_parcela'], "%m/%Y") + relativedelta(months=mes_atual-1)
        fase = 'Pós'
        if mes_atual <= num_parcelas_entrada:
            fase = 'Entrada'
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']:
            fase = 'Pré'

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
            'DataObj': data_mes,
            'Mês/Data': f"{mes_atual} - [{data_mes.strftime('%m/%Y')}]",
            'Fase': fase, 'Saldo Devedor': saldo_devedor,
            'Parcela Total': pagamento + juros_mes,
            'Amortização Base': amortizacao,
            'Correção INCC ou IPCA diluída (R$)': correcao_paga,
            'Taxa de Juros (%)': taxa_juros_mes * 100 if fase == 'Pós' else 0,
            'Juros (R$)': juros_mes,
            'Ajuste INCC (R$)': correcao_mes if fase in ['Entrada','Pré'] else 0,
            'Ajuste IPCA (R$)': correcao_mes if fase == 'Pós' else 0
        })

        if fase == 'Pré' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre(params, amortizacao_total_acumulada)

    return pd.DataFrame(historico)

# ============================================
# BUSCAR ÍNDICES BC (INCC, IPCA, TR)
# ============================================
def buscar_indices_bc(mes_inicial, meses_total):
    try:
        data_inicio_simulacao = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_inicio_busca = data_inicio_simulacao - relativedelta(months=2)
        data_fim_busca = data_inicio_simulacao + relativedelta(months=meses_total)
        start_str = data_inicio_busca.strftime("%d/%m/%Y")
        end_str = data_fim_busca.strftime("%d/%m/%Y")

        # Séries: 192 = INCC, 433 = IPCA, 226 = TR
        df = sgs.dataframe([192, 433, 226], start=start_str, end=end_str)
        if df.empty:
            return {}, 0, pd.DataFrame()

        df = df.rename(columns={192: 'incc', 433: 'ipca', 226: 'tr'})
        df['incc'] = df['incc'] / 100
        df['ipca'] = df['ipca'] / 100
        df['tr'] = df['tr'] / 100

        indices = {}
        ultimo_mes_com_dado = 0
        dados_por_data = {idx.strftime("%Y-%m-%d"): {'incc': row['incc'], 'ipca': row['ipca'], 'tr': row['tr']} for idx, row in df.iterrows()}

        current_date_simulacao = data_inicio_simulacao
        for mes in range(1, meses_total + 1):
            # Usa a convenção de defasagem de 2 meses (mesmo comportamento anterior)
            data_referencia_str = (current_date_simulacao - relativedelta(months=2)).strftime("%Y-%m-%d")
            if data_referencia_str in dados_por_data:
                valores = dados_por_data[data_referencia_str]
                if valores.get('incc') is not None or valores.get('ipca') is not None or valores.get('tr') is not None:
                    ultimo_mes_com_dado = mes
                indices[mes] = valores
            else:
                indices[mes] = {'incc': None, 'ipca': None, 'tr': None}
            current_date_simulacao += relativedelta(months=1)

        return indices, ultimo_mes_com_dado, df
    except Exception as e:
        st.error(f"Erro ao acessar dados do BC: {str(e)}")
        return {}, 0, pd.DataFrame()

# ============================================
# SIMULAÇÃO BANCÁRIA AJUSTADA (CAIXA: indexador + SAC/PRICE + seguros mensais)
# ============================================
def simular_financiamento_bancario_completo(params_gerais, params_banco, params_construtora, valores_reais=None, offset_mes=0, include_obra=True, valor_financiado_override=None, prazo_amort_override=None):
    """Simulação bancária alinhada às práticas da CAIXA."""
    historico = []
    try:
        data_assinatura = datetime.strptime(params_gerais['mes_assinatura'], "%m/%Y")
    except Exception:
        st.error("Data de assinatura inválida para o cenário bancário!")
        return pd.DataFrame()

    # Determina o valor financiado (padrão ou override)
    if valor_financiado_override is not None:
        valor_financiado = valor_financiado_override
    else:
        valor_financiado = params_gerais['valor_total_imovel'] - params_gerais['valor_entrada']

    # taxa de juros efetiva mensal
    taxa_juros_mensal = converter_juros_anual_para_mensal(params_banco['taxa_juros_anual'] / 100)

    # seguros: converter percentuais anuais para taxa mensal
    taxa_dfi_mensal = (params_banco.get('taxa_dfi', 0) / 100) / 12
    taxa_mip_mensal = (params_banco.get('taxa_mip', 0) / 100) / 12
    taxa_admin_mensal_valor = params_banco.get('taxa_admin_mensal', 0)

    # escolha do indexador e fallbacks médios
    indexador = params_banco.get('indexador', 'TR')  # 'TR' | 'IPCA' | 'Fixa'
    tr_medio = params_banco.get('tr_medio', 0.0)
    ipca_medio = params_banco.get('ipca_medio', 0.0)
    sistema = params_banco.get('sistema_amortizacao', 'SAC')

    # fase de obra do banco (apenas se include_obra=True)
    prazo_obra_meses = params_construtora.get('num_parcelas_entrada', 0) + params_construtora['meses_pre']
    saldo_liberado_obra = 0.0
    if include_obra and prazo_obra_meses > 0:
        liberacao_mensal = valor_financiado / prazo_obra_meses if prazo_obra_meses > 0 else 0
        for i in range(prazo_obra_meses):
            data_corrente = data_assinatura + relativedelta(months=i+1)
            saldo_liberado_obra += liberacao_mensal
            juros_obra = saldo_liberado_obra * taxa_juros_mensal
            encargos_obra = taxa_admin_mensal_valor + (taxa_dfi_mensal * params_gerais['valor_total_imovel']) + (taxa_mip_mensal * saldo_liberado_obra)
            parcela_obra = juros_obra + encargos_obra
            historico.append({'DataObj': data_corrente, 'Fase': 'Juros de Obra', 'Parcela Total': parcela_obra, 'Saldo Liberado': saldo_liberado_obra})

    # fase de amortização
    saldo_devedor = valor_financiado
    prazo_amort = prazo_amort_override if prazo_amort_override is not None else params_construtora['meses_pos']
    if prazo_amort <= 0:
        return pd.DataFrame(historico)

    if sistema == 'SAC':
        amortizacao_constante = saldo_devedor / prazo_amort
        for i in range(prazo_amort):
            months_after = (prazo_obra_meses + i + 1) if include_obra else (i + 1)
            data_corrente = data_assinatura + relativedelta(months=months_after)

            taxa_index = 0
            if indexador in ['TR', 'IPCA']:
                chave_mes = offset_mes + i + 1
                if valores_reais is not None and chave_mes in valores_reais:
                    taxa_index = valores_reais[chave_mes].get(indexador.lower(), 0) or 0
                else:
                    taxa_index = tr_medio if indexador == 'TR' else ipca_medio

            juros = saldo_devedor * taxa_juros_mensal
            ajuste_index = saldo_devedor * taxa_index
            seguro_dfi = taxa_dfi_mensal * params_gerais['valor_total_imovel']
            seguro_mip = taxa_mip_mensal * saldo_devedor
            encargos = seguro_dfi + seguro_mip + taxa_admin_mensal_valor

            parcela_total = amortizacao_constante + juros + encargos + ajuste_index
            saldo_devedor = max(saldo_devedor - amortizacao_constante + ajuste_index, 0)

            historico.append({'DataObj': data_corrente, 'Fase': 'Amortização SAC', 'Parcela Total': parcela_total, 'Saldo Devedor': saldo_devedor})

    elif sistema == 'PRICE':
        r = taxa_juros_mensal
        n = prazo_amort
        if r == 0:
            parcela_fix = saldo_devedor / n
        else:
            parcela_fix = (r * saldo_devedor) / (1 - (1 + r) ** (-n))

        for i in range(n):
            months_after = (prazo_obra_meses + i + 1) if include_obra else (i + 1)
            data_corrente = data_assinatura + relativedelta(months=months_after)

            taxa_index = 0
            if indexador in ['TR','IPCA']:
                chave_mes = offset_mes + i + 1
                if valores_reais is not None and chave_mes in valores_reais:
                    taxa_index = valores_reais[chave_mes].get(indexador.lower(), 0) or 0
                else:
                    taxa_index = tr_medio if indexador == 'TR' else ipca_medio

            juros = saldo_devedor * r
            amortizacao = parcela_fix - juros
            ajuste_index = saldo_devedor * taxa_index

            seguro_dfi = taxa_dfi_mensal * params_gerais['valor_total_imovel']
            seguro_mip = taxa_mip_mensal * saldo_devedor
            encargos = seguro_dfi + seguro_mip + taxa_admin_mensal_valor

            parcela_total = amortizacao + juros + encargos + ajuste_index
            saldo_devedor = max(saldo_devedor - amortizacao + ajuste_index, 0)

            historico.append({'DataObj': data_corrente, 'Fase': 'Amortização PRICE', 'Parcela Total': parcela_total, 'Saldo Devedor': saldo_devedor})

    else:
        st.error('Sistema de amortização desconhecido. Use SAC ou PRICE.')
        return pd.DataFrame()

    return pd.DataFrame(historico)

# ============================================
# SIMULAÇÃO COMBINADA (CONSTRUTORA + BANCO) — FASE PÓS ALINHADA AO FIM DA CONSTRUTORA
# ============================================
def simular_cenario_combinado(params_construtora, params_banco, valores_reais=None):
    """Gera PRÉ usando a referência da construtora (exatamente) e faz o PÓS pela Caixa
       usando o saldo remanescente e um prazo de amortização tal que o fim do financiamento
       coincide com o fim do cenário da construtora (mesma DataObj final).
    """
    # gera o ciclo completo da construtora para obter datas e fim alvo
    df_full_constructor = simular_financiamento(params_construtora, valores_reais)
    if df_full_constructor.empty:
        return pd.DataFrame()

    # extrai apenas a parte PRÉ (todas as linhas até a última que não seja fase 'Pós')
    df_pre = df_full_constructor[df_full_constructor['Fase'] != 'Pós'].copy()
    if df_pre.empty:
        return pd.DataFrame()

    # o saldo remanescente que a Caixa financiaria é o saldo devedor ao final da PRÉ
    if 'Saldo Devedor' in df_pre.columns:
        valor_financiado_para_banco = df_pre['Saldo Devedor'].iloc[-1]
    else:
        valor_financiado_para_banco = params_construtora['valor_total_imovel'] - df_pre.get('Amortização Base', pd.Series([0])).sum()

    # data de início do financiamento bancário: mês imediatamente após o fim da PRÉ
    num_parcelas_entrada = params_construtora.get('num_parcelas_entrada', 0)
    total_meses_pre_chaves = num_parcelas_entrada + params_construtora['meses_pre']
    try:
        data_primeira_parcela = datetime.strptime(params_construtora['mes_primeira_parcela'], "%m/%Y")
        data_inicio_banco = data_primeira_parcela + relativedelta(months=total_meses_pre_chaves)
        params_gerais_banco = {'mes_assinatura': data_inicio_banco.strftime("%m/%Y"), 'valor_total_imovel': params_construtora['valor_total_imovel'], 'valor_entrada': params_construtora['valor_entrada']}
    except Exception:
        params_gerais_banco = {'mes_assinatura': params_construtora['mes_assinatura'], 'valor_total_imovel': params_construtora['valor_total_imovel'], 'valor_entrada': params_construtora['valor_entrada']}
        data_inicio_banco = datetime.strptime(params_construtora['mes_assinatura'], "%m/%Y")

    # fim alvo: última DataObj do fluxo completo da construtora
    end_date_target = df_full_constructor['DataObj'].iloc[-1]

    # calcula o prazo de amortização que fará o financiamento bancário terminar em end_date_target
    months_between = (end_date_target.year - data_inicio_banco.year) * 12 + (end_date_target.month - data_inicio_banco.month) + 1
    prazo_amort_para_banco = months_between if months_between > 0 else params_construtora['meses_pos']

    # chama simulação do banco com override do saldo, sem repetir obra, alinhando índices com offset
    df_banco = simular_financiamento_bancario_completo(
        params_gerais=params_gerais_banco,
        params_banco=params_banco,
        params_construtora=params_construtora,
        valores_reais=valores_reais,
        offset_mes=total_meses_pre_chaves,
        include_obra=False,
        valor_financiado_override=valor_financiado_para_banco,
        prazo_amort_override=prazo_amort_para_banco
    )

    # concatena PRÉ com o PÓS (banco) e ordena por DataObj
    if df_banco.empty:
        return df_pre

    df_comb = pd.concat([df_pre, df_banco], ignore_index=True, sort=False)
    df_comb = df_comb.sort_values('DataObj').reset_index(drop=True)
    return df_comb

# ============================================
# INTERFACE STREAMLIT (com novos campos para banco)
# ============================================
def criar_parametros():
    st.sidebar.header("Parâmetros Gerais (Construtora)")
    params = {}
    params['mes_assinatura'] = st.sidebar.text_input("Mês da assinatura (MM/AAAA)", "04/2025")
    params['mes_primeira_parcela'] = st.sidebar.text_input("Mês da 1ª parcela (MM/AAAA)", "05/2025")
    params['valor_total_imovel'] = st.sidebar.number_input("Valor total do imóvel", value=455750.0, format="%.2f")
    params['valor_entrada'] = st.sidebar.number_input("Valor total da entrada", value=22270.54, format="%.2f")
    params['tipo_pagamento_entrada'] = st.sidebar.selectbox("Como a entrada é paga?", ['Parcelada', 'Paga no ato'])
    if params['tipo_pagamento_entrada'] == 'Parcelada':
        params['num_parcelas_entrada'] = st.sidebar.number_input("Nº de parcelas da entrada", min_value=1, value=3)
        params['entrada_mensal'] = params['valor_entrada'] / params['num_parcelas_entrada'] if params['num_parcelas_entrada'] > 0 else 0
    else:
        params['num_parcelas_entrada'] = 0; params['entrada_mensal'] = 0
    st.sidebar.subheader("Parâmetros de Correção e Juros")
    params['inicio_correcao'] = st.sidebar.number_input("Aplicar correção a partir de qual parcela?", min_value=1, value=1)
    params['incc_medio'] = st.sidebar.number_input("INCC médio mensal (%)", value=0.5446, format="%.4f") / 100
    params['ipca_medio'] = st.sidebar.number_input("IPCA médio mensal (%)", value=0.4669, format="%.4f") / 100
    st.sidebar.number_input("Juros Pós-Chaves (% a.a.)", value=12.0, format="%.2f", disabled=True, help="Na lógica de cálculo atual, os juros são progressivos e não baseados nesta taxa fixa.")

    st.sidebar.subheader("Fases de Pagamento")
    col1, col2 = st.sidebar.columns(2)
    params['meses_pre'] = col1.number_input("Meses pré-chaves", value=17)
    params['meses_pos'] = col2.number_input("Meses pós-chaves", value=100)
    col3, col4 = st.sidebar.columns(2)
    params['parcelas_mensais_pre'] = col3.number_input("Valor parcela pré (R$)", value=3983.38, format="%.2f")
    params['valor_amortizacao_pos'] = col4.number_input("Valor parcela pós (R$)", value=3104.62, format="%.2f")
    st.sidebar.subheader("Parcelas Extras (na fase pré-chaves)")
    params['parcelas_semestrais'] = {}
    params['parcelas_anuais'] = {}
    st.sidebar.write("Parcelas Semestrais:")
    for i in range(4):
        cs1, cs2 = st.sidebar.columns(2)
        mes_sem = cs1.number_input(f"Mês da {i+1}ª semestral", value=6*(i+1) if i<2 else 0, key=f"sem_mes_{i}")
        valor_sem = cs2.number_input(f"Valor {i+1} (R$)", value=6000.0 if i<2 else 0.0, key=f"sem_val_{i}", format="%.2f")
        if mes_sem > 0 and valor_sem > 0: params['parcelas_semestrais'][int(mes_sem)] = valor_sem
    st.sidebar.write("Parcelas Anuais:")
    ca1, ca2 = st.sidebar.columns(2)
    mes_anu = ca1.number_input("Mês da anual", value=17, key="anu_mes")
    valor_anu = ca2.number_input("Valor anual (R$)", value=43300.0, key="anu_val", format="%.2f")
    if mes_anu > 0 and valor_anu > 0: params['parcelas_anuais'][int(mes_anu)] = valor_anu
    params['percentual_minimo_quitacao'] = 0.3
    params['limite_correcao'] = None
    return params

def criar_parametros_banco(params_construtora):
    st.info("Os prazos do financiamento bancário são sincronizados com os da construtora para uma comparação justa.", icon="💡")
    params_banco = {}
    pcol1, pcol2 = st.columns(2)
    with pcol1:
        st.metric("Prazo de obra (meses)", params_construtora.get('num_parcelas_entrada', 0) + params_construtora['meses_pre'])
        st.metric("Prazo de amortização (meses)", params_construtora['meses_pos'])
        params_banco['taxa_juros_anual'] = st.number_input("Taxa de Juros Efetiva (% a.a.)", value=9.75, format="%.4f", key="b_juros")
        params_banco['indexador'] = st.selectbox("Indexador (pós)", ['TR', 'IPCA', 'Fixa'], index=0)
        params_banco['sistema_amortizacao'] = st.selectbox("Sistema de amortização", ['SAC', 'PRICE'], index=0)
    with pcol2:
        params_banco['taxa_admin_mensal'] = st.number_input("Taxa de Admin Mensal (R$)", value=25.0, format="%.2f", key="b_admin")
        params_banco['taxa_dfi'] = st.number_input("Taxa DFI (% ao ano)", value=0.0118, format="%.4f", key="b_dfi")
        params_banco['taxa_mip'] = st.number_input("Taxa MIP (% ao ano)", value=0.0248, format="%.4f", key="b_mip")
        params_banco['tr_medio'] = st.number_input("TR média mensal (decimal)", value=0.0, format="%.6f", help="Usado se não houver dados do SGS")
        params_banco['ipca_medio'] = st.number_input("IPCA média mensal (decimal)", value=0.004669, format="%.6f", help="Usado se não houver dados do SGS")
    return params_banco

def mostrar_resultados(df_resultado):
    st.subheader("Tabela de Simulação Detalhada (Construtora)")
    st.dataframe(df_resultado.style.format({"Saldo Devedor": format_currency, "Parcela Total": format_currency, "Amortização Base": format_currency, "Correção INCC ou IPCA diluída (R$)": format_currency, "Juros (R$)": format_currency, "Ajuste INCC (R$)": format_currency, "Ajuste IPCA (R$)": format_currency, "Taxa de Juros (%)": "{:.2f}%"}), use_container_width=True, height=350)

def mostrar_comparacao(df_c, df_b, df_comb):
    st.header("Resultados da Comparação")

    df_c['Custo Acumulado'] = df_c['Parcela Total'].cumsum()
    df_b['Custo Acumulado'] = df_b['Parcela Total'].cumsum()
    df_comb['Custo Acumulado'] = df_comb['Parcela Total'].cumsum()

    c_custo_total = df_c['Custo Acumulado'].iloc[-1]
    b_custo_total = df_b['Custo Acumulado'].iloc[-1]
    comb_custo_total = df_comb['Custo Acumulado'].iloc[-1]

    res1, res2, res3 = st.columns(3)
    with res1:
        st.subheader("🏗️ Construtora")
        st.metric("Custo Total", format_currency(c_custo_total))
        st.metric("Maior Parcela", format_currency(df_c['Parcela Total'].max()))
        st.metric("Término", df_c['DataObj'].iloc[-1].strftime("%m/%Y"))
    with res2:
        st.subheader("🏦 Banco (Início)")
        st.metric("Custo Total", format_currency(b_custo_total), delta=format_currency(c_custo_total - b_custo_total), delta_color="inverse")
        st.metric("Maior Parcela", format_currency(df_b['Parcela Total'].max()))
        st.metric("Término", df_b['DataObj'].iloc[-1].strftime("%m/%Y"))
    with res3:
        st.subheader("🤝 Combinado")
        st.metric("Custo Total", format_currency(comb_custo_total), delta=format_currency(c_custo_total - comb_custo_total), delta_color="inverse")
        st.metric("Maior Parcela", format_currency(df_comb['Parcela Total'].max()))
        st.metric("Término", df_comb['DataObj'].iloc[-1].strftime("%m/%Y"))

    df_c_chart = df_c[['DataObj', 'Parcela Total']].rename(columns={'Parcela Total': 'Construtora'})
    df_b_chart = df_b[['DataObj', 'Parcela Total']].rename(columns={'Parcela Total': 'Banco (Início)'})
    df_comb_chart = df_comb[['DataObj', 'Parcela Total']].rename(columns={'Parcela Total': 'Combinado'})

    df_merged = pd.merge(df_c_chart, df_b_chart, on='DataObj', how='outer')
    df_merged = pd.merge(df_merged, df_comb_chart, on='DataObj', how='outer').sort_values('DataObj').fillna(0)

    st.subheader("Evolução Comparativa das Parcelas")
    st.line_chart(df_merged.set_index('DataObj'))

    st.subheader("Tabelas Detalhadas dos Cenários Comparativos")
    with st.expander("🏦 Ver Tabela - Banco (Início)"):
        df_b_display = df_b[['DataObj', 'Fase', 'Parcela Total', 'Custo Acumulado']].copy()
        df_b_display['DataObj'] = df_b_display['DataObj'].dt.strftime('%m/%Y')
        st.dataframe(df_b_display.style.format({
            "Parcela Total": format_currency,
            "Custo Acumulado": format_currency
        }), use_container_width=True, height=350)

    with st.expander("🤝 Ver Tabela - Combinado"):
        df_comb_display = df_comb[['DataObj', 'Fase', 'Parcela Total', 'Custo Acumulado']].copy()
        df_comb_display['DataObj'] = df_comb_display['DataObj'].dt.strftime('%m/%Y')
        st.dataframe(df_comb_display.style.format({
            "Parcela Total": format_currency,
            "Custo Acumulado": format_currency
        }), use_container_width=True, height=350)

def main():
    st.set_page_config(layout="wide", page_title="Simulador e Comparador de Financiamento")
    st.title("Simulador de Financiamento Imobiliário 🚧🏗️")

    for key in ['df_resultado', 'df_banco', 'df_combinado']:
        if key not in st.session_state:
            st.session_state[key] = pd.DataFrame()

    params = criar_parametros()
    st.header("⚖️ Parâmetros para Financiamento Bancário")
    params_banco = criar_parametros_banco(params)
    st.header("Gerar Simulação e Comparar Cenários")

    def run_full_simulation(sim_params, real_values=None):
        st.session_state.df_resultado = simular_financiamento(sim_params, real_values)

        if not st.session_state.df_resultado.empty:
            params_gerais = {'valor_total_imovel': params['valor_total_imovel'], 'valor_entrada': params['valor_entrada'], 'mes_assinatura': params['mes_assinatura']}
            st.session_state.df_banco = simular_financiamento_bancario_completo(params_gerais, params_banco, params)
            st.session_state.df_combinado = simular_cenario_combinado(params.copy(), params_banco, real_values)
        else:
            st.session_state.df_banco = pd.DataFrame()
            st.session_state.df_combinado = pd.DataFrame()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("1. Simular com Médias", type="primary", use_container_width=True):
            run_full_simulation(params.copy())
    with col2:
        if st.button("2. Simular Híbrido (BC + Médias)", use_container_width=True):
            total_meses = params.get('num_parcelas_entrada', 0) + params['meses_pre'] + params['meses_pos']
            valores_reais, ultimo_mes, _ = buscar_indices_bc(params['mes_primeira_parcela'], total_meses)
            if ultimo_mes > 0: st.info(f"Dados reais do BC aplicados até a parcela {ultimo_mes}.")
            run_full_simulation(params.copy(), valores_reais)
    with col3:
        if st.button("3. Simular Apenas com BC (Puro)", use_container_width=True):
            total_meses = params.get('num_parcelas_entrada', 0) + params['meses_pre'] + params['meses_pos']
            valores_reais, ultimo_mes, _ = buscar_indices_bc(params['mes_primeira_parcela'], total_meses)
            if ultimo_mes > 0:
                params_sim = params.copy()
                params_sim['limite_correcao'] = ultimo_mes
                st.info(f"Dados reais do BC aplicados até a parcela {ultimo_mes}.")
                run_full_simulation(params_sim, valores_reais)
            else:
                st.warning("Nenhum dado histórico encontrado para essa data.")
                st.session_state.df_resultado = st.session_state.df_banco = st.session_state.df_combinado = pd.DataFrame()
    with col4:
        limite_manual = st.number_input("Limite Manual de Correção", min_value=0, value=params['meses_pre'] + params.get('num_parcelas_entrada', 0))
        if st.button("4. Simular com Limite", use_container_width=True):
            params_sim = params.copy()
            params_sim['limite_correcao'] = limite_manual
            run_full_simulation(params_sim)

    if not st.session_state.df_resultado.empty:
        mostrar_resultados(st.session_state.df_resultado)
        if not st.session_state.df_banco.empty and not st.session_state.df_combinado.empty:
            st.divider()
            mostrar_comparacao(st.session_state.df_resultado, st.session_state.df_banco, st.session_state.df_combinado)

if __name__ == "__main__":
    main()
