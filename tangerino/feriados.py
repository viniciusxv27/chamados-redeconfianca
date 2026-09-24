"""Feriados nacionais — o calendário que o Tangerino não devolve.

Os lançamentos de FERIADO da API existem, mas são esparsos: cobrem feriado
municipal e ponto facultativo de algumas lojas (29/06 para 36 pessoas, 08/09
para 22). Os nacionais não estão lá. Sem eles, o relatório de ponto cobrava
batida em 03/04 (Sexta-feira Santa), 21/04 (Tiradentes), 01/05 (Trabalho) e
04/06 (Corpus Christi) — quatro "faltas" que não existiram.

Só os nacionais fixos e os móveis derivados da Páscoa. Feriado estadual e
municipal continua vindo do Tangerino, que é onde o RH lança.

Carnaval e Corpus Christi entram, apesar de serem ponto facultativo por lei:
medido no espelho, no 7 de Setembro bateram ponto 15 pessoas contra ~100 de uma
segunda comum — a empresa para no feriado, e quem trabalhou tem a linha do dia
com as batidas (esta lista só decide o dia SEM batida nenhuma). O preço é não
acusar a falta de quem estava escalado num feriado e não apareceu; o troco
seria acusar falta de 85 pessoas por feriado.
"""
from datetime import date, timedelta
from functools import lru_cache

# Dia e mês dos feriados nacionais fixos (Lei 662/1949 e 10.607/2002).
FIXOS = (
    (1, 1),      # Confraternização Universal
    (21, 4),     # Tiradentes
    (1, 5),      # Dia do Trabalho
    (7, 9),      # Independência
    (12, 10),    # Nossa Senhora Aparecida
    (2, 11),     # Finados
    (15, 11),    # Proclamação da República
    (20, 11),    # Consciência Negra (nacional desde 2024, Lei 14.759)
    (25, 12),    # Natal
)


def pascoa(ano):
    """Domingo de Páscoa do ano (algoritmo de Meeus/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


@lru_cache(maxsize=16)
def do_ano(ano):
    """Todos os feriados nacionais do ano, como um conjunto de datas."""
    domingo = pascoa(ano)
    moveis = {
        domingo - timedelta(days=48),   # Carnaval (segunda)
        domingo - timedelta(days=47),   # Carnaval (terça)
        domingo - timedelta(days=2),    # Sexta-feira Santa
        domingo + timedelta(days=60),   # Corpus Christi
    }
    return {date(ano, mes, dia) for dia, mes in FIXOS} | moveis


def e_feriado(dia):
    """O dia é feriado nacional?"""
    return dia in do_ano(dia.year)
