import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io

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
    percentual_minimo_quitacao=0.3
):
    # Inicialização do saldo devedor
    saldo_devedor = valor_total_imovel - valor_entrada if not entrada_parcelada else valor_total_imovel
    
    # Lista para armazenar todas as parcelas futuras
    parcelas_futuras = []
    historico = []
    total_amortizado_pre = 0
    total_correcao_paga_pre = 0  # Novo: para rastrear correção paga na fase pré

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
                'correcao_acumulada': 0.0,
                'tipo': 'pre'
            })

    # 2. Adicionar parcelas da fase pós-chaves
    for mes in range(1, meses_pos + 1):
        mes_global = meses_pre + mes
        parcelas_futuras.append({
            'mes': mes_global,
            'valor_original': valor_amortizacao_pos,
            'correcao_acumulada': 0.0,
            'tipo': 'pos'
        })

    # 3. Processar todos os meses sequencialmente
    for mes_atual in range(1, meses_pre + meses_pos + 1):
        fase = 'Pré' if mes_atual <= meses_pre else 'Pós'
        
        # Calcular correção do período
        if fase == 'Pré':
            correcao_mes = saldo_devedor * incc_medio
            saldo_devedor += correcao_mes
        else:
            correcao_mes = saldo_devedor * ipca_medio
            saldo_devedor += correcao_mes

        # Distribuir correção entre todas as parcelas futuras
        if parcelas_futuras:
            total_valor_original = sum(p['valor_original'] for p in parcelas_futuras)
            for parcela in parcelas_futuras:
                proporcao = parcela['valor_original'] / total_valor_original
                parcela['correcao_acumulada'] += correcao_mes * proporcao

        # Verificar se há parcelas vencendo neste mês
        parcelas_vencidas = [p for p in parcelas_futuras if p['mes'] == mes_atual]
        pagamento_total = 0
        amortizacao_total = 0
        juros_total = 0
        correcao_paga_total = 0
        
        for parcela in parcelas_vencidas:
            # Calcular juros apenas para parcelas pós
            juros_parcela = saldo_devedor * juros_mensal if parcela['tipo'] == 'pos' else 0
            
            # Calcular valor total a pagar
            pagamento_parcela = parcela['valor_original'] + parcela['correcao_acumulada'] + juros_parcela
            pagamento_total += pagamento_parcela
            amortizacao_total += parcela['valor_original']
            juros_total += juros_parcela
            correcao_paga_total += parcela['correcao_acumulada']
            
            # Remover parcela da lista de futuras
            parcelas_futuras.remove(parcela)
        
        # CORREÇÃO IMPORTANTE: Atualizar saldo devedor com todo o pagamento
        saldo_devedor -= pagamento_total  # Deduz todo o valor pago
        saldo_devedor = max(saldo_devedor, 0)
        
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
            # CORREÇÃO: Incluir correção paga no cálculo do valor quitado
            valor_quitado = (0 if entrada_parcelada else valor_entrada) + total_amortizado_pre + total_correcao_paga_pre
            percentual_quitado = valor_quitado / valor_total_imovel
            
            if percentual_quitado < percentual_minimo_quitacao:
                formatted_valor = format_currency(valor_quitado)
                st.warning(f"Atenção: valor quitado na pré ({formatted_valor}) equivale a {percentual_quitado*100:.2f}% do valor do imóvel, abaixo de {percentual_minimo_quitacao*100:.0f}%.")

    return pd.DataFrame(historico)

# O restante do código permanece igual...
