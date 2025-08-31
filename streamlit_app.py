import streamlit as st
import pandas as pd
import numpy as np
import numpy_financial as npf
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import sgs

# ============================================
# UTILITÁRIAS (Sem alterações)
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

def calcular_cet(valor_financiado, pagamentos):
    """Calcula o Custo Efetivo Total (CET) anual a partir de um fluxo de caixa."""
    if valor_financiado <= 0 or not any(p > 0 for p in pagamentos):
        return 0.0
    fluxo_de_caixa = [valor_financiado] + [-p for p in pagamentos]
    try:
        taxa_mensal = npf.irr(fluxo_de_caixa)
        if np.isnan(taxa_mensal) or np.isinf(taxa_mensal):
            return 0.0
        taxa_anual = (1 + taxa_mensal)**12 - 1
        return taxa_anual * 100
    except Exception:
        return 0.0

# ============================================
# LÓGICA DA CONSTRUTORA (Sem alterações)
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
            
    for mes in range(num_parcelas_entrada + 1 + params['meses_pre'], num_parcelas_entrada + 1 + params['meses_pre'] + params['meses_pos']):
        parcelas.append({'mes': mes, 'valor_original': params['valor_amortizacao_pos'], 'correcao_acumulada': 0.0, 'tipo': 'pos'})
        
    return parcelas

def calcular_correcao(saldo, mes, fase, params, valores_reais):
    if fase not in ['Assinatura', 'Carência']:
        inicio_correcao = params.get('inicio_correcao', 1)
        if inicio_correcao == 0:
            inicio_correcao = 1
        if mes < inicio_correcao:
            return 0, 'N/A'
            
        limite = params.get('limite_correcao')
        if limite is not None and mes > limite:
            return 0, 'N/A'
            
        if valores_reais is not None and mes in valores_reais:
            idx = valores_reais[mes]
            if fase in ['Entrada','Pré', 'Carência'] and idx.get('incc') is not None and pd.notna(idx.get('incc')):
                return saldo * idx['incc'], 'INCC'
            elif fase == 'Pós' and idx.get('ipca') is not None and pd.notna(idx.get('ipca')):
                return saldo * idx['ipca'], 'IPCA'
                
        if fase in ['Entrada','Pré', 'Carência']:
            return saldo * params.get('incc_medio', 0), 'INCC (Médio)'
        elif fase == 'Pós':
            return saldo * params.get('ipca_medio', 0), 'IPCA (Médio)'
            
    return 0, 'N/A'

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
        'DataObj': data_assinatura,
        'Mês/Data': f"Assinatura [{data_assinatura.strftime('%m/%Y')}]",
        'Fase': 'Assinatura',
        'Saldo Devedor': saldo_devedor,
        'Parcela Total (R$)': amortizacao_assinatura,
        'Amortização Base (R$)': amortizacao_assinatura,
        'Correção Monetária Paga (R$)': 0,
        'Taxa de Juros (%)': 0,
        'Juros (R$)': 0,
        'Correção Monetária Gerada (R$)': 0,
        'Índice Correção': 'N/A',
        'Encargos (R$)': 0
    })
    
    meses_carencia = (data_primeira_parcela.year - data_assinatura.year) * 12 + (data_primeira_parcela.month - data_assinatura.month)
    data_corrente_carencia = data_assinatura
    saldo_temp_carencia = saldo_devedor
    total_correcao_carencia = 0
    
    for i in range(meses_carencia):
        data_corrente_carencia += relativedelta(months=1)
        correcao_mes_carencia, indice_carencia = calcular_correcao(saldo_temp_carencia, 0, 'Carência', params, valores_reais)
        total_correcao_carencia += correcao_mes_carencia
        saldo_temp_carencia += correcao_mes_carencia
        historico.append({
            'DataObj': data_corrente_carencia,
            'Mês/Data': f"Gerou Correção [{data_corrente_carencia.strftime('%m/%Y')}]",
            'Fase': 'Carência',
            'Saldo Devedor': saldo_devedor,
            'Parcela Total (R$)': 0,
            'Amortização Base (R$)': 0,
            'Correção Monetária Paga (R$)': 0,
            'Taxa de Juros (%)': 0,
            'Juros (R$)': 0,
            'Correção Monetária Gerada (R$)': correcao_mes_carencia,
            'Índice Correção': indice_carencia,
            'Encargos (R$)': 0
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
        
        fase = 'Pós'
        if mes_atual <= num_parcelas_entrada:
            fase = 'Entrada'
        elif mes_atual <= num_parcelas_entrada + params['meses_pre']:
            fase = 'Pré'
            
        pagamento, amortizacao, correcao_paga = processar_parcelas_vencidas(parcelas_futuras, mes_atual)
        amortizacao_total_acumulada += amortizacao
        saldo_devedor -= (amortizacao + correcao_paga)
        
        correcao_mes, indice_mes = calcular_correcao(saldo_devedor, mes_atual, fase, params, valores_reais)
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
            'Fase': fase,
            'Saldo Devedor': saldo_devedor,
            'Parcela Total (R$)': pagamento + juros_mes,
            'Amortização Base (R$)': amortizacao,
            'Correção Monetária Paga (R$)': correcao_paga,
            'Taxa de Juros (%)': taxa_juros_mes * 100 if fase == 'Pós' else 0,
            'Juros (R$)': juros_mes,
            'Correção Monetária Gerada (R$)': correcao_mes,
            'Índice Correção': indice_mes,
            'Encargos (R$)': 0
        })
        
        if fase == 'Pré' and mes_atual == num_parcelas_entrada + params['meses_pre']:
            verificar_quitacao_pre(params, amortizacao_total_acumulada)
            
    return pd.DataFrame(historico)

# ============================================
# BUSCAR ÍNDICES BC (Sem alterações)
# ============================================

def buscar_indices_bc(mes_inicial, meses_total):
    try:
        data_inicio_simulacao = datetime.strptime(mes_inicial, "%m/%Y").replace(day=1)
        data_inicio_busca = data_inicio_simulacao - relativedelta(months=2)
        data_fim_busca = data_inicio_simulacao + relativedelta(months=meses_total)
        start_str = data_inicio_busca.strftime("%d/%m/%Y")
        end_str = data_fim_busca.strftime("%d/%m/%Y")
        
        df = sgs.dataframe([192, 433, 226], start=start_str, end=end_str)
        if df.empty:
            return {}, 0, pd.DataFrame()
            
        df = df.rename(columns={192: 'incc', 433: 'ipca', 226: 'tr'})
        df['incc'] /= 100
        df['ipca'] /= 100
        df['tr'] /= 100
        
        indices = {}
        ultimo_mes_com_dado = 0
        dados_por_data = {idx.strftime("%Y-%m-%d"): row.to_dict() for idx, row in df.iterrows()}
        current_date_simulacao = data_inicio_simulacao
        
        for mes in range(1, meses_total + 1):
            data_referencia_str = (current_date_simulacao - relativedelta(months=2)).strftime("%Y-%m-%d")
            if data_referencia_str in dados_por_data:
                valores = dados_por_data[data_referencia_str]
                if pd.notna(valores.get('incc')) or pd.notna(valores.get('ipca')) or pd.notna(valores.get('tr')):
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
# LÓGICA DE JUROS DE OBRA (Sem alterações)
# ============================================

def _obter_percentual_obra(mes_obra_atual, prazo_obra_total, metodo, marcos={}):
    """Calcula o percentual de conclusão da obra para um determinado mês."""
    if mes_obra_atual <= 0:
        return 0.0
    if mes_obra_atual >= prazo_obra_total:
        return 1.0
        
    percentual = 0.0
    if metodo == 'Linear':
        percentual = mes_obra_atual / prazo_obra_total
    elif metodo == 'Progressiva (S-Curve)':
        t = mes_obra_atual / prazo_obra_total
        percentual = (3 * t**2) - (2 * t**3)
    elif metodo == 'Manual':
        if not marcos:
            return 0.0
        meses_ordenados = sorted(marcos.keys())
        if mes_obra_atual < meses_ordenados[0]:
            fator = mes_obra_atual / meses_ordenados[0]
            percentual = marcos[meses_ordenados[0]] * fator
        else:
            mes_anterior, perc_anterior = 0, 0
            encontrou_intervalo = False
            for mes_marco in meses_ordenados:
                if mes_obra_atual >= mes_marco:
                    mes_anterior, perc_anterior = mes_marco, marcos[mes_marco]
                else:
                    mes_seguinte, perc_seguinte = mes_marco, marcos[mes_marco]
                    if (mes_seguinte - mes_anterior) > 0:
                        fator = (mes_obra_atual - mes_anterior) / (mes_seguinte - mes_anterior)
                        percentual = perc_anterior + fator * (perc_seguinte - perc_anterior)
                    else:
                        percentual = perc_anterior
                    encontrou_intervalo = True
                    break
            if not encontrou_intervalo:
                mes_seguinte, perc_seguinte = prazo_obra_total, 100
                if (mes_seguinte - mes_anterior) > 0:
                    fator = (mes_obra_atual - mes_anterior) / (mes_seguinte - mes_anterior)
                    percentual = perc_anterior + fator * (perc_seguinte - perc_anterior)
                else:
                    percentual = perc_anterior
        percentual /= 100.0
        
    return min(1.0, max(0.0, percentual))


def calcular_juros_obra_detalhado(params_gerais, params_banco, params_construtora, valor_financiado):
    historico = []
    data_assinatura_banco = datetime.strptime(params_gerais['mes_assinatura'], "%m/%Y")
    data_inicio_obra = datetime.combine(params_construtora['data_inicio_obra'], datetime.min.time())

    meses_obra_ate_contrato = (data_assinatura_banco.year - data_inicio_obra.year) * 12 + (data_assinatura_banco.month - data_inicio_obra.month)
    prazo_restante_obra_usuario = params_construtora.get('num_parcelas_entrada', 0) + params_construtora['meses_pre']
    prazo_obra_total_meses = meses_obra_ate_contrato + prazo_restante_obra_usuario
    metodo_calculo = params_banco['metodo_calculo_juros']

    if prazo_obra_total_meses <= 0:
        return pd.DataFrame()
        
    marcos = {}
    if metodo_calculo == 'Manual':
        try:
            items = params_banco['marcos_liberacao'].replace(" ", "").split(',')
            for item in items:
                mes, perc = item.split(':')
                marcos[int(mes)] = float(perc)
        except Exception:
            st.error("Formato dos marcos de liberação inválido. Use: 'mes:percentual, mes:percentual'. Ex: '6:20, 12:50'")
            return pd.DataFrame()
            
    meses_restantes_obra = prazo_obra_total_meses - meses_obra_ate_contrato
    if meses_restantes_obra <= 0:
        return pd.DataFrame()
        
    taxa_juros_mensal = (params_banco['taxa_juros_anual'] / 100) / 12
    taxa_admin_mensal_valor = params_banco.get('taxa_admin_mensal', 0)
    seguro_total_inicial = params_banco.get('seguro_total_primeira_parcela', 0)
    percentual_dfi = params_banco.get('percentual_dfi_estimado', 30.0) / 100
    valor_seguro_dfi = seguro_total_inicial * percentual_dfi
    valor_seguro_mip_inicial = seguro_total_inicial - valor_seguro_dfi
    taxa_mip = valor_seguro_mip_inicial / valor_financiado if valor_financiado > 0 else 0
    
    for i in range(meses_restantes_obra):
        data_corrente = data_assinatura_banco + relativedelta(months=i)
        mes_total_obra_atual = meses_obra_ate_contrato + i + 1
        
        percentual_conclusao_acumulado = _obter_percentual_obra(
            mes_obra_atual=mes_total_obra_atual,
            prazo_obra_total=prazo_obra_total_meses,
            metodo=metodo_calculo,
            marcos=marcos
        )
        
        perc_obra_inicio_contrato = _obter_percentual_obra(
            mes_obra_atual=meses_obra_ate_contrato,
            prazo_obra_total=prazo_obra_total_meses,
            metodo=metodo_calculo,
            marcos=marcos
        )
        
        percentual_conclusao_acumulado = max(perc_obra_inicio_contrato, percentual_conclusao_acumulado)
        
        saldo_liberado_obra = valor_financiado * percentual_conclusao_acumulado
        juros_obra = saldo_liberado_obra * taxa_juros_mensal
        
        seguro_mip_obra = taxa_mip * saldo_liberado_obra
        seguro_obra = seguro_mip_obra + valor_seguro_dfi
        
        encargos_obra = taxa_admin_mensal_valor + seguro_obra
        parcela_obra = juros_obra + encargos_obra
        
        historico.append({
            'DataObj': data_corrente,
            'Mês/Data': f"Obra {i+1} - [{data_corrente.strftime('%m/%Y')}]",
            'Fase': 'Juros de Obra',
            'Saldo Devedor': valor_financiado,
            'Amortização Base (R$)': 0,
            'Juros (R$)': juros_obra,
            'Correção Monetária Paga (R$)': 0,
            'Encargos (R$)': encargos_obra,
            'Parcela Total (R$)': parcela_obra,
            'Correção Monetária Gerada (R$)': 0,
            'Índice Correção': f'{percentual_conclusao_acumulado:.2%} concluído',
            'Taxa de Juros (%)': taxa_juros_mensal * 100
        })
        
    return pd.DataFrame(historico)

# ============================================
# SIMULAÇÃO BANCÁRIA (Sem alterações)
# ============================================

def simular_financiamento_bancario_completo(params_gerais, params_banco, params_construtora, valores_reais=None, offset_mes=0, include_obra=True, valor_financiado_override=None, prazo_amort_override=None):
    historico_df = pd.DataFrame()
    valor_financiado = valor_financiado_override if valor_financiado_override is not None else (params_gerais['valor_total_imovel'] - params_gerais['valor_entrada'])
    taxa_juros_mensal = (params_banco['taxa_juros_anual'] / 100) / 12
    taxa_admin_mensal_valor = params_banco.get('taxa_admin_mensal', 0)
    
    seguro_total_inicial = params_banco.get('seguro_total_primeira_parcela', 0)
    percentual_dfi = params_banco.get('percentual_dfi_estimado', 30.0) / 100
    valor_seguro_dfi = seguro_total_inicial * percentual_dfi
    valor_seguro_mip_inicial = seguro_total_inicial - valor_seguro_dfi
    taxa_mip = valor_seguro_mip_inicial / valor_financiado if valor_financiado > 0 else 0

    indexador = params_banco.get('indexador', 'TR')
    tr_medio = params_banco.get('tr_medio', 0.0)
    ipca_medio = params_banco.get('ipca_medio', 0.0)
    sistema = params_banco.get('sistema_amortizacao', 'PRICE')
    
    if include_obra:
        df_juros_obra = calcular_juros_obra_detalhado(params_gerais, params_banco, params_construtora, valor_financiado)
        if not df_juros_obra.empty:
            historico_df = pd.concat([historico_df, df_juros_obra], ignore_index=True)
            
    saldo_devedor = valor_financiado
    prazo_amort = prazo_amort_override if prazo_amort_override is not None else params_construtora['meses_pos']
    
    if prazo_amort <= 0:
        return historico_df
        
    data_inicio_amortizacao = datetime.strptime(params_gerais['mes_assinatura'], "%m/%Y")
    if not historico_df.empty:
        data_inicio_amortizacao = historico_df['DataObj'].max() + relativedelta(months=1)
        
    historico_amort = []
    for i in range(prazo_amort):
        data_corrente = data_inicio_amortizacao + relativedelta(months=i)
        
        taxa_index, indice_aplicado = 0, 'Fixa'
        if indexador in ['TR', 'IPCA']:
            chave_mes = offset_mes + i + 1
            if valores_reais and chave_mes in valores_reais and pd.notna(valores_reais[chave_mes].get(indexador.lower())):
                taxa_index = valores_reais[chave_mes].get(indexador.lower(), 0) or 0
                indice_aplicado = indexador
            else:
                taxa_index = tr_medio if indexador == 'TR' else ipca_medio
                indice_aplicado = f'{indexador} (Médio)'
                
        juros = saldo_devedor * taxa_juros_mensal
        ajuste_index = saldo_devedor * taxa_index
        
        seguro_mip = taxa_mip * saldo_devedor
        seguro_mensal = seguro_mip + valor_seguro_dfi

        encargos = seguro_mensal + taxa_admin_mensal_valor
        amortizacao = 0
        
        if sistema == 'PRICE':
            r, n = taxa_juros_mensal, prazo_amort
            parcela_fix = (r * valor_financiado) / (1 - (1 + r) ** (-n)) if r > 0 else valor_financiado / n
            amortizacao = parcela_fix - juros
        elif sistema == 'SAC':
            amortizacao = valor_financiado / prazo_amort
        else:
            st.error(f'Sistema de amortização desconhecido: {sistema}. Use SAC ou PRICE.')
            return pd.DataFrame()
            
        parcela_total = amortizacao + juros + encargos + ajuste_index
        saldo_devedor = max(saldo_devedor - amortizacao, 0)
        
        historico_amort.append({
            'DataObj': data_corrente,
            'Mês/Data': f"{i+1} - [{data_corrente.strftime('%m/%Y')}]",
            'Fase': f'Amortização {sistema}',
            'Saldo Devedor': saldo_devedor,
            'Amortização Base (R$)': amortizacao,
            'Juros (R$)': juros,
            'Correção Monetária Paga (R$)': 0,
            'Encargos (R$)': encargos,
            'Parcela Total (R$)': parcela_total,
            'Correção Monetária Gerada (R$)': ajuste_index,
            'Índice Correção': indice_aplicado,
            'Taxa de Juros (%)': taxa_juros_mensal * 100
        })
        
    df_amort = pd.DataFrame(historico_amort)
    return pd.concat([historico_df, df_amort], ignore_index=True) if not df_amort.empty else historico_df

# ============================================
# SIMULAÇÃO COMBINADA (CORRIGIDA)
# ============================================

def simular_cenario_combinado(params_construtora, params_banco, valores_reais=None):
    df_full_constructor = simular_financiamento(params_construtora, valores_reais)
    if df_full_constructor.empty:
        return pd.DataFrame()
        
    df_pre = df_full_constructor[df_full_constructor['Fase'] != 'Pós'].copy()
    if df_pre.empty:
        return pd.DataFrame()
        
    valor_financiado_para_banco = df_pre['Saldo Devedor'].iloc[-1]
    num_parcelas_entrada = params_construtora.get('num_parcelas_entrada', 0)
    total_meses_pre_chaves = num_parcelas_entrada + params_construtora['meses_pre']
    
    try:
        data_primeira_parcela = datetime.strptime(params_construtora['mes_primeira_parcela'], "%m/%Y")
        data_inicio_banco = data_primeira_parcela + relativedelta(months=total_meses_pre_chaves)
    except Exception:
        data_inicio_banco = datetime.now()
        
    params_gerais_banco = {
        'mes_assinatura': data_inicio_banco.strftime("%m/%Y"),
        'valor_total_imovel': params_construtora['valor_total_imovel'],
        'valor_entrada': params_construtora['valor_entrada']
    }
    
    # --- CORREÇÃO DA DURAÇÃO ---
    # O prazo de amortização do banco deve ser o mesmo do cenário da construtora para uma comparação justa.
    prazo_amort_para_banco = params_construtora['meses_pos']
    
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
    
    if df_banco.empty:
        return df_pre
        
    df_comb = pd.concat([df_pre, df_banco], ignore_index=True, sort=False)
    df_comb = df_comb.sort_values('DataObj').reset_index(drop=True)
    return df_comb

# ============================================
# SIMULAÇÃO ASSOCIATIVA (Sem alterações)
# ============================================

def simular_cenario_associativo(params_construtora, params_banco, valores_reais=None):
    df_full_constructor = simular_financiamento(params_construtora, valores_reais)
    if df_full_constructor.empty:
        return pd.DataFrame()
        
    df_pre_construtora = df_full_constructor[df_full_constructor['Fase'] != 'Pós'].copy()
    if df_pre_construtora.empty:
        return pd.DataFrame()
        
    valor_financiado_banco_inicial = df_pre_construtora['Saldo Devedor'].iloc[-1]
    data_assinatura_construtora = datetime.strptime(params_construtora['mes_assinatura'], "%m/%Y")
    params_gerais_banco = {'mes_assinatura': data_assinatura_construtora.strftime("%m/%Y")}
    
    df_juros_obra = calcular_juros_obra_detalhado(
        params_gerais=params_gerais_banco,
        params_banco=params_banco,
        params_construtora=params_construtora,
        valor_financiado=valor_financiado_banco_inicial
    )
    
    if not df_juros_obra.empty:
        df_pre_combinado = pd.merge(df_pre_construtora, df_juros_obra, on='DataObj', how='outer', suffixes=('_c', '_b'))
        
        df_pre_combinado['Parcela Total (R$)_c'] = df_pre_combinado['Parcela Total (R$)_c'].fillna(0)
        df_pre_combinado['Parcela Total (R$)_b'] = df_pre_combinado['Parcela Total (R$)_b'].fillna(0)
        df_pre_combinado['Juros (R$)_b'] = df_pre_combinado['Juros (R$)_b'].fillna(0)
        df_pre_combinado['Encargos (R$)_b'] = df_pre_combinado['Encargos (R$)_b'].fillna(0)
        df_pre_combinado['Parcela Total (R$)'] = df_pre_combinado['Parcela Total (R$)_c'] + df_pre_combinado['Parcela Total (R$)_b']
        df_pre_combinado['Juros (R$)'] = df_pre_combinado['Juros (R$)_b']
        df_pre_combinado['Encargos (R$)'] = df_pre_combinado['Encargos (R$)_b']
        
        for col in ['Mês/Data', 'Fase', 'Saldo Devedor', 'Amortização Base (R$)', 'Correção Monetária Paga (R$)', 'Taxa de Juros (%)', 'Correção Monetária Gerada (R$)', 'Índice Correção']:
            df_pre_combinado[col] = df_pre_combinado[f'{col}_c'].fillna(df_pre_combinado[f'{col}_b'])
            
        df_pre_combinado['Fase'] = 'Pré (Construtora + J. Obra)'
        
        colunas_finais = list(df_pre_construtora.columns)
        df_pre_final = df_pre_combinado[colunas_finais]
    else:
        df_pre_final = df_pre_construtora
        
    data_inicio_amortizacao = df_pre_final['DataObj'].max() + relativedelta(months=1)
    params_gerais_banco_pos = {'mes_assinatura': data_inicio_amortizacao.strftime("%m/%Y")}
    prazo_amort_para_banco = params_construtora['meses_pos']
    total_meses_pre_chaves = params_construtora.get('num_parcelas_entrada', 0) + params_construtora['meses_pre']
    
    df_banco_pos = simular_financiamento_bancario_completo(
        params_gerais=params_gerais_banco_pos,
        params_banco=params_banco,
        params_construtora=params_construtora,
        valores_reais=valores_reais,
        offset_mes=total_meses_pre_chaves,
        include_obra=False,
        valor_financiado_override=valor_financiado_banco_inicial,
        prazo_amort_override=prazo_amort_para_banco
    )
    
    if df_banco_pos.empty:
        return df_pre_final
        
    df_final = pd.concat([df_pre_final, df_banco_pos], ignore_index=True, sort=False)
    return df_final.sort_values('DataObj').reset_index(drop=True)

# ============================================
# FUNÇÕES DE INTERFACE (MODIFICADAS)
# ============================================

def display_detailed_table(df, title):
    with st.expander(f"👁️ Ver Tabela Detalhada - {title}"):
        df_display = df.drop(columns=['DataObj'], errors='ignore')
        currency_cols = [col for col in df_display.columns if '(R$)' in col or 'Devedor' in col]
        format_dict = {col: format_currency for col in currency_cols}
        
        if 'Taxa de Juros (%)' in df_display.columns:
            format_dict['Taxa de Juros (%)'] = "{:.2f}%"
            
        st.dataframe(df_display.style.format(format_dict), use_container_width=True, height=400)

def mostrar_comparacao(df_c, df_b, df_comb, df_assoc, cet_c, cet_b, cet_comb, cet_assoc, user_scenario_pre, user_scenario_pos):
    st.header("Resultados da Comparação")

    # --- LÓGICA PARA IDENTIFICAR O CENÁRIO DO USUÁRIO ---
    cenario_usuario = None
    if user_scenario_pre == "Construtora" and user_scenario_pos == "Construtora":
        cenario_usuario = "Direto com a Construtora"
    elif user_scenario_pre == "Construtora" and user_scenario_pos == "Banco (Caixa, etc.)":
        cenario_usuario = "Financiamento Pós-Chaves (Sequencial)"
    elif user_scenario_pre == "Banco (Caixa, etc.)":
        cenario_usuario = "Financiamento Associativo (Simultâneo)"

    with st.expander("Clique aqui para entender os cenários de comparação"):
        st.markdown("""
            - **🏗️ Direto com a Construtora:** O modelo mais simples. Você paga todas as parcelas, do início ao fim, diretamente para a construtora, que financia o saldo devedor após as chaves com suas próprias regras de juros.

            - **🏦 Financiamento Total no Banco (Hipotético):** Este é um cenário **"E se?"**. Ele ignora os pagamentos da fase de obra e simula como seria se você financiasse o valor total do imóvel (menos a entrada) diretamente com o banco desde o primeiro dia, pagando juros de obra e amortização pelo mesmo prazo total. Útil para comparar o "custo do dinheiro" do banco.
            
            - **🔑 Financiamento Pós-Chaves (Sequencial):** Um processo em duas etapas. **1)** Você paga toda a fase de obra (entrada, mensais, extras) para a construtora. **2)** No dia em que recebe as chaves, o saldo devedor restante é quitado através de um novo financiamento com o banco. **Neste modelo não há pagamento de Juros de Obra**.

            - **🌱 Financiamento Associativo (Simultâneo):** O modelo mais comum no mercado. Você assina o financiamento com o banco logo no início. Durante a fase de obra, você tem **duas obrigações mensais**: pagar a parcela para a construtora E pagar os **Juros de Obra** para o banco. Após receber as chaves, os pagamentos para a construtora cessam e você começa a pagar apenas a parcela de amortização (principal + juros) para o banco.
        """)
        
    c_custo_total = df_c['Parcela Total (R$)'].sum() if not df_c.empty else 0
    b_custo_total = df_b['Parcela Total (R$)'].sum() if not df_b.empty else 0
    comb_custo_total = df_comb['Parcela Total (R$)'].sum() if not df_comb.empty else 0
    assoc_custo_total = df_assoc['Parcela Total (R$)'].sum() if not df_assoc.empty else 0
    
    res1, res2, res3, res4 = st.columns(4)
    
    def display_scenario(container, title, df, cet, base_cost, user_scenario_name):
        with container:
            header = f"⭐ {title}" if title == user_scenario_name else title
            st.subheader(header)
            if not df.empty:
                cost = df['Parcela Total (R$)'].sum()
                delta = cost - base_cost if base_cost > 0 and title != "Direto com a Construtora" else None
                st.metric("Custo Total", format_currency(cost), delta=format_currency(delta) if delta is not None else None)
                st.metric("Maior Parcela", format_currency(df['Parcela Total (R$)'].max()))
                st.metric("Término", df['DataObj'].iloc[-1].strftime("%m/%Y"))
                st.metric("CET (Custo Efetivo Total)", f"{cet:.2f}% a.a.")

    display_scenario(res1, "Direto com a Construtora", df_c, cet_c, c_custo_total, cenario_usuario)
    display_scenario(res2, "Financiamento Total no Banco (Hipotético)", df_b, cet_b, c_custo_total, cenario_usuario)
    display_scenario(res3, "Financiamento Pós-Chaves (Sequencial)", df_comb, cet_comb, c_custo_total, cenario_usuario)
    display_scenario(res4, "Financiamento Associativo (Simultâneo)", df_assoc, cet_assoc, c_custo_total, cenario_usuario)

    all_dfs = [
        df[['DataObj', 'Parcela Total (R$)']].rename(columns={'Parcela Total (R$)': name})
        for df, name in [
            (df_c, 'Construtora'), (df_b, 'Banco (Hipotético)'),
            (df_comb, 'Pós-Chaves (Sequencial)'), (df_assoc, 'Associativo (Simultâneo)')
        ] if not df.empty
    ]
    
    if all_dfs:
        df_merged = all_dfs[0]
        for df_to_merge in all_dfs[1:]:
            df_merged = pd.merge(df_merged, df_to_merge, on='DataObj', how='outer')
        df_merged = df_merged.sort_values('DataObj').fillna(0)
        st.subheader("Evolução Comparativa das Parcelas")
        st.line_chart(df_merged.set_index('DataObj'))
        
    st.subheader("Análise Detalhada dos Fluxos de Pagamento")
    if not df_c.empty: display_detailed_table(df_c, "Direto com a Construtora")
    if not df_b.empty: display_detailed_table(df_b, "Financiamento Total no Banco (Hipotético)")
    if not df_comb.empty: display_detailed_table(df_comb, "Financiamento Pós-Chaves (Sequencial)")
    if not df_assoc.empty: display_detailed_table(df_assoc, "Financiamento Associativo (Simultâneo)")

# ============================================
# NOVA INTERFACE STREAMLIT (REESTRUTURADA E CORRIGIDA)
# ============================================

def setup_ui():
    """Cria e gerencia a interface do usuário para coletar todos os parâmetros."""
    
    st.sidebar.header("Parâmetros Gerais do Contrato")
    st.sidebar.date_input("Início da obra (empreendimento)", value=date(2024, 10, 1), key="data_inicio_obra")
    st.sidebar.text_input("Mês da assinatura do seu contrato (MM/AAAA)", "04/2025", key="mes_assinatura")
    st.sidebar.text_input("Mês da 1ª parcela (MM/AAAA)", "05/2025", key="mes_primeira_parcela")
    st.sidebar.number_input("Valor total do imóvel", value=455750.0, format="%.2f", key="valor_total_imovel")
    st.sidebar.number_input("Valor total da entrada", value=22270.54, format="%.2f", key="valor_entrada")
    st.sidebar.selectbox("Como a entrada é paga?", ['Parcelada', 'Paga no ato'], key="tipo_pagamento_entrada")

    if st.session_state.tipo_pagamento_entrada == 'Parcelada':
        st.sidebar.number_input("Nº de parcelas da entrada", min_value=1, value=3, key="num_parcelas_entrada")
    else:
        if 'num_parcelas_entrada' not in st.session_state:
            st.session_state.num_parcelas_entrada = 0

    st.header("Definição das Fases do Financiamento")

    # --- FASE PRÉ-CHAVES ---
    with st.container(border=True):
        st.subheader("Fase 1: Período Pré-Chaves")
        st.radio("Quem financia esta fase?", ["Construtora", "Banco (Caixa, etc.)"], key="financiador_pre", horizontal=True)
        
        col1, col2 = st.columns(2)
        valor_a_financiar_pre = col1.number_input("Valor total a ser pago na fase pré-chaves", value=123217.46, format="%.2f", key="valor_a_financiar_pre")
        col2.number_input("Nº de meses na fase pré-chaves", value=17, key="meses_pre")
        
        parcelas_semestrais, parcelas_anuais = {}, {}
        if st.session_state.financiador_pre == "Construtora":
            with st.expander("Adicionar Parcelas Extras (Semestrais/Anuais)"):
                st.write("**Parcelas Semestrais:**")
                for i in range(4):
                    cs1, cs2 = st.columns(2)
                    mes_sem = cs1.number_input(f"Mês da {i+1}ª semestral", value=6*(i+1) if i<2 else 0, key=f"sem_mes_{i}")
                    valor_sem = cs2.number_input(f"Valor {i+1} (R$)", value=6000.0 if i<2 else 0.0, key=f"sem_val_{i}", format="%.2f")
                    if mes_sem > 0 and valor_sem > 0:
                        parcelas_semestrais[int(mes_sem)] = valor_sem

                st.write("**Parcelas Anuais:**")
                ca1, ca2 = st.columns(2)
                mes_anu = ca1.number_input("Mês da anual", value=17, key="anu_mes")
                valor_anu = ca2.number_input("Valor anual (R$)", value=43300.0, key="anu_val", format="%.2f")
                if mes_anu > 0 and valor_anu > 0:
                    parcelas_anuais[int(mes_anu)] = valor_anu
        
        st.session_state.parcelas_semestrais = parcelas_semestrais
        st.session_state.parcelas_anuais = parcelas_anuais

        soma_extras = sum(st.session_state.parcelas_semestrais.values()) + sum(st.session_state.parcelas_anuais.values())
        valor_remanescente_pre = valor_a_financiar_pre - soma_extras
        
        if valor_remanescente_pre < 0:
            st.error("A soma das parcelas extras é maior que o valor total a ser pago na fase pré-chaves!")
        
        parcela_base_pre = valor_remanescente_pre / st.session_state.meses_pre if st.session_state.meses_pre > 0 else 0
        st.session_state.parcelas_mensais_pre = parcela_base_pre
        st.info(f"O valor base da parcela mensal nesta fase é de {format_currency(parcela_base_pre)}. Este valor foi calculado subtraindo {format_currency(soma_extras)} (parcelas extras) do total da fase e dividindo pelo nº de meses.")

    # --- FASE PÓS-CHAVES ---
    with st.container(border=True):
        st.subheader("Fase 2: Período Pós-Chaves")
        st.radio("Quem financia esta fase?", ["Construtora", "Banco (Caixa, etc.)"], index=1, key="financiador_pos", horizontal=True)

        col3, col4 = st.columns(2)
        saldo_devedor_estimado = st.session_state.valor_total_imovel - st.session_state.valor_entrada - valor_a_financiar_pre
        valor_a_financiar_pos = col3.number_input("Valor total a ser pago na fase pós-chaves (saldo devedor)", value=saldo_devedor_estimado, format="%.2f", key="valor_a_financiar_pos")
        col4.number_input("Nº de meses na fase pós-chaves (amortização)", value=100, key="meses_pos")

        parcela_base_pos = valor_a_financiar_pos / st.session_state.meses_pos if st.session_state.meses_pos > 0 else 0
        st.session_state.valor_amortizacao_pos = parcela_base_pos
        st.info(f"O valor base da parcela mensal nesta fase é de **{format_currency(parcela_base_pos)}** (divisão simples). Juros e correções serão aplicados sobre este valor.")

    # --- SUA REALIDADE ---
    with st.container(border=True):
        st.subheader("Sua Realidade")
        st.write(f"Você está informando que seu financiamento foi feito com **{st.session_state.financiador_pre}** na fase pré-chaves e **{st.session_state.financiador_pos}** na fase pós-chaves. O resultado correspondente será destacado abaixo.")

    st.divider()
    if st.button("Carregar Parâmetros Padrão", help="Preenche as seções abaixo com valores comuns de mercado para agilizar a simulação."):
        st.session_state.inicio_correcao = 1
        st.session_state.incc_medio_percent = 0.5446
        st.session_state.ipca_medio_percent = 0.4669
        st.session_state.taxa_juros_anual = 10.0
        st.session_state.indexador = 'TR'
        st.session_state.sistema_amortizacao = 'PRICE'
        st.session_state.taxa_admin_mensal = 25.0
        st.session_state.seguro_total_primeira_parcela = 94.92
        st.session_state.percentual_dfi_estimado = 30.0
        st.session_state.tr_medio = 0.0
        st.session_state.ipca_medio_banco = 0.004669
        st.rerun()

    st.header("Parâmetros Detalhados")

    with st.expander("Parâmetros de Correção (Construtora)", expanded=True):
        pcol1, pcol2, pcol3 = st.columns(3)
        pcol1.number_input("Aplicar correção a partir de qual parcela?", min_value=1, key="inicio_correcao")
        incc_percent = pcol2.number_input("INCC médio mensal (%)", format="%.4f", key="incc_medio_percent")
        ipca_percent = pcol3.number_input("IPCA médio mensal (%)", format="%.4f", key="ipca_medio_percent")
        
        st.session_state.incc_medio = incc_percent / 100
        st.session_state.ipca_medio = ipca_percent / 100
        
        st.sidebar.number_input("Juros Pós-Chaves (% a.a.)", value=12.0, format="%.2f", disabled=True, help="Na lógica de cálculo atual da construtora, os juros são progressivos e não baseados nesta taxa fixa.")

    if st.session_state.financiador_pre == "Banco (Caixa, etc.)" or st.session_state.financiador_pos == "Banco (Caixa, etc.)":
        with st.expander("Parâmetros do Financiamento Bancário", expanded=True):
            st.info("Para replicar uma simulação da Caixa, use o sistema PRICE e a taxa de juros NOMINAL, mesmo que o documento indique SAC.", icon="💡")
            
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                st.subheader("Condições Gerais")
                st.number_input("Taxa de Juros Nominal (% a.a.)", format="%.4f", key="taxa_juros_anual")
                st.selectbox("Indexador (pós)", ['TR', 'IPCA', 'Fixa'], key="indexador")
                st.selectbox("Sistema de amortização", ['PRICE', 'SAC'], key="sistema_amortizacao")
            
            with bcol2:
                st.subheader("Taxas e Seguros")
                st.number_input("Taxa de Admin Mensal (R$)", format="%.2f", key="taxa_admin_mensal")
                st.number_input("Valor Total do Seguro na 1ª Parcela (R$)", format="%.2f", key="seguro_total_primeira_parcela", help="Informe o valor total do seguro (DFI+MIP) que aparece na primeira parcela da sua simulação.")
                st.slider("Percentual estimado do DFI sobre o seguro total (%)", min_value=0.0, max_value=100.0, step=0.5, help="O seguro é composto de uma parte fixa (DFI) e uma variável (MIP). Ajuste aqui a proporção que você estima ser a parte fixa.", key="percentual_dfi_estimado")
            
            st.subheader("Juros de Obra e Correções Futuras")
            st.selectbox("Método de Evolução da Obra", ['Progressiva (S-Curve)', 'Linear', 'Manual'], index=0, help="Define como o percentual de conclusão da obra evolui.", key="metodo_calculo_juros")
            if 'metodo_calculo_juros' in st.session_state and st.session_state.metodo_calculo_juros == 'Manual':
                 st.text_area("Defina os marcos (mês da obra: % concluído)", "6:20, 12:50, 18:90", help="Formato: mes_da_obra:percentual_total, ...", key="marcos_liberacao")
            
            jcol1, jcol2 = st.columns(2)
            jcol1.number_input("TR média mensal (decimal)", format="%.6f", help="Usado se não houver dados do SGS", key="tr_medio")
            jcol2.number_input("IPCA média mensal (decimal)", format="%.6f", help="Usado se não houver dados do SGS", key="ipca_medio_banco")

def main():
    st.set_page_config(layout="wide", page_title="Simulador e Comparador de Financiamento")
    st.title("Simulador de Financiamento Imobiliário 🚧🏗️")
    
    for key in ['df_resultado', 'df_banco', 'df_combinado', 'df_associativo', 'cet_construtora', 'cet_banco', 'cet_combinado', 'cet_associativo']:
        if key not in st.session_state:
            st.session_state[key] = pd.DataFrame() if 'df' in key else 0.0
            
    setup_ui()

    params = {
        'data_inicio_obra': st.session_state.data_inicio_obra,
        'mes_assinatura': st.session_state.mes_assinatura,
        'mes_primeira_parcela': st.session_state.mes_primeira_parcela,
        'valor_total_imovel': st.session_state.valor_total_imovel,
        'valor_entrada': st.session_state.valor_entrada,
        'tipo_pagamento_entrada': st.session_state.tipo_pagamento_entrada,
        'num_parcelas_entrada': st.session_state.get('num_parcelas_entrada', 0),
        'entrada_mensal': (st.session_state.valor_entrada / st.session_state.num_parcelas_entrada) if st.session_state.get('num_parcelas_entrada', 0) > 0 else 0,
        'inicio_correcao': st.session_state.get('inicio_correcao', 1),
        'incc_medio': st.session_state.get('incc_medio', 0.0),
        'ipca_medio': st.session_state.get('ipca_medio', 0.0),
        'meses_pre': st.session_state.get('meses_pre', 0),
        'meses_pos': st.session_state.get('meses_pos', 0),
        'parcelas_mensais_pre': st.session_state.get('parcelas_mensais_pre', 0),
        'valor_amortizacao_pos': st.session_state.get('valor_amortizacao_pos', 0),
        'parcelas_semestrais': st.session_state.get('parcelas_semestrais', {}),
        'parcelas_anuais': st.session_state.get('parcelas_anuais', {}),
        'percentual_minimo_quitacao': 0.3, 'limite_correcao': None
    }
    
    params_banco = {
        'taxa_juros_anual': st.session_state.get('taxa_juros_anual', 0.0),
        'indexador': st.session_state.get('indexador', 'TR'),
        'sistema_amortizacao': st.session_state.get('sistema_amortizacao', 'PRICE'),
        'taxa_admin_mensal': st.session_state.get('taxa_admin_mensal', 0.0),
        'seguro_total_primeira_parcela': st.session_state.get('seguro_total_primeira_parcela', 0.0),
        'percentual_dfi_estimado': st.session_state.get('percentual_dfi_estimado', 30.0),
        'tr_medio': st.session_state.get('tr_medio', 0.0),
        'ipca_medio': st.session_state.get('ipca_medio_banco', 0.0),
        'metodo_calculo_juros': st.session_state.get('metodo_calculo_juros', 'Progressiva (S-Curve)'),
        'marcos_liberacao': st.session_state.get('marcos_liberacao', '')
    }

    st.header("Gerar Simulação e Comparar Cenários")
    
    def run_full_simulation(sim_params, real_values=None):
        st.session_state.df_resultado = simular_financiamento(sim_params, real_values)
        st.session_state.df_banco = pd.DataFrame()
        st.session_state.df_combinado = pd.DataFrame()
        st.session_state.df_associativo = pd.DataFrame()
        st.session_state.cet_construtora, st.session_state.cet_banco, st.session_state.cet_combinado, st.session_state.cet_associativo = 0.0, 0.0, 0.0, 0.0
        
        if not st.session_state.df_resultado.empty:
            params_gerais = {'valor_total_imovel': params['valor_total_imovel'], 'valor_entrada': params['valor_entrada'], 'mes_assinatura': params['mes_assinatura']}
            st.session_state.df_banco = simular_financiamento_bancario_completo(params_gerais, params_banco, params, real_values)
            st.session_state.df_combinado = simular_cenario_combinado(params.copy(), params_banco, real_values)
            st.session_state.df_associativo = simular_cenario_associativo(params.copy(), params_banco, real_values)
            
            for scenario in ['construtora', 'banco', 'combinado', 'associativo']:
                df_key = {'construtora': 'df_resultado', 'banco': 'df_banco', 'combinado': 'df_combinado', 'associativo': 'df_associativo'}[scenario]
                cet_key = f'cet_{scenario}'
                df = st.session_state[df_key]
                if not df.empty:
                    if scenario == 'banco':
                        valor_financiado_liquido = params_gerais['valor_total_imovel'] - params_gerais['valor_entrada']
                        pagamentos_futuros = df['Parcela Total (R$)'].tolist()
                    else:
                        pagamento_t0 = df['Parcela Total (R$)'].iloc[0] if not df[df['Fase'] == 'Assinatura'].empty else 0
                        valor_financiado_liquido = sim_params['valor_total_imovel'] - pagamento_t0
                        pagamentos_futuros = df['Parcela Total (R$)'][df['Fase'] != 'Assinatura'].tolist()
                    st.session_state[cet_key] = calcular_cet(valor_financiado_liquido, pagamentos_futuros)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("1. Simular com Médias", type="primary", use_container_width=True):
            run_full_simulation(params.copy())
            
    with col2:
        if st.button("2. Simular Híbrido (BC + Médias)", use_container_width=True):
            total_meses = params.get('num_parcelas_entrada', 0) + params['meses_pre'] + params['meses_pos']
            valores_reais, ultimo_mes, _ = buscar_indices_bc(params['mes_primeira_parcela'], total_meses)
            if ultimo_mes > 0:
                st.info(f"Dados reais do BC aplicados até a parcela {ultimo_mes}.")
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
        
    with col4:
        limite_manual = st.number_input("Limite Manual de Correção", min_value=0, value=params['meses_pre'] + params.get('num_parcelas_entrada', 0))
        if st.button("4. Simular com Limite", use_container_width=True):
            params_sim = params.copy()
            params_sim['limite_correcao'] = limite_manual
            run_full_simulation(params_sim)
            
    if not st.session_state.df_resultado.empty:
        mostrar_comparacao(
            st.session_state.df_resultado,
            st.session_state.df_banco,
            st.session_state.df_combinado,
            st.session_state.df_associativo,
            st.session_state.cet_construtora,
            st.session_state.cet_banco,
            st.session_state.cet_combinado,
            st.session_state.cet_associativo,
            st.session_state.financiador_pre,
            st.session_state.financiador_pos
        )

if __name__ == "__main__":
    main()
