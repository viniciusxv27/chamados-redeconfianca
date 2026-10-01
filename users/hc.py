"""Validação e acompanhamento mensal do quadro (HC) das lojas.

A pergunta que este módulo responde é "o quadro desta loja está certo neste
mês?" — e ela se responde olhando a loja inteira: quem entrou, quem saiu, quem
continua. Por isso a tela é uma **árvore** (loja → pessoas) e não uma lista de
usuários: a lista mostra pessoas, a árvore mostra estrutura.

Duas coisas que o portal já sabe entram de graça aqui:

- **desligado** é `demission_date` preenchida (ou o cadastro inativo) — não é
  preciso o coordenador marcar nada para a pessoa aparecer sinalizada;
- **entrou agora** é `admission_date` dentro do mês conferido.
"""
from datetime import date

from django.db.models import Q

from .models import Sector, User, ValidacaoHC


def mes_de(referencia=None):
    """O 1º dia do mês conferido (a referência é sempre o dia 1º)."""
    hoje = referencia or date.today()
    return hoje.replace(day=1)


def pode_validar(user):
    """Quem confere o HC: coordenação e gestão do portal."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if user.is_superuser or user.hierarchy in ('ADMIN', 'SUPERADMIN'):
        return True
    return user.communication_groups.filter(
        Q(name__icontains='COORDENADOR') | Q(name__icontains='GERENTE')).exists()


def lojas_de(user):
    """As lojas que esta pessoa confere.

    Gestão do portal vê todas; os demais veem as lojas atreladas a eles (o M2M
    `sectors`, mais o setor principal) — é a mesma régua que o resto do portal
    usa para dizer "minhas lojas".
    """
    if user.is_superuser or user.hierarchy in ('ADMIN', 'SUPERADMIN'):
        return Sector.objects.all().order_by('name')

    ids = set(user.sectors.values_list('id', flat=True))
    if user.sector_id:
        ids.add(user.sector_id)
    return Sector.objects.filter(id__in=ids).order_by('name')


def _situacao(pessoa, inicio, fim):
    """O rótulo da pessoa no mês: desligado, novo ou ativo."""
    if pessoa.demission_date and pessoa.demission_date <= fim:
        return 'desligado'
    if not pessoa.is_active:
        return 'inativo'
    if pessoa.admission_date and inicio <= pessoa.admission_date <= fim:
        return 'novo'
    return 'ativo'


def arvore(user, referencia=None):
    """A árvore do HC: uma loja por nó, com as pessoas e a situação de cada uma.

    Devolve também o que a tela precisa para o topo: total de lojas, quantas já
    foram conferidas no mês e o quadro somado.
    """
    inicio = mes_de(referencia)
    fim = (inicio.replace(year=inicio.year + 1, month=1) if inicio.month == 12
           else inicio.replace(month=inicio.month + 1))
    fim = fim.fromordinal(fim.toordinal() - 1)      # último dia do mês

    lojas = list(lojas_de(user))
    validacoes = {v.setor_id: v for v in ValidacaoHC.objects.filter(
        referencia=inicio, setor__in=lojas).select_related('validado_por')}

    # Uma consulta para todas as lojas: a árvore inteira não pode custar uma
    # consulta por loja (são dezenas).
    # Quem aparece na árvore: quem está ativo, quem foi desligado dentro do mês
    # conferido e quem está com o cadastro inativo **sem** data de demissão —
    # este último é o caso que o coordenador precisa ver para regularizar.
    # Desligado de meses anteriores já saiu do quadro e não polui a tela.
    pessoas = (User.objects.filter(sector__in=lojas)
               .filter(Q(is_active=True)
                       | Q(demission_date__gte=inicio, demission_date__lte=fim)
                       | Q(is_active=False, demission_date__isnull=True))
               .select_related('sector')
               .order_by('first_name', 'last_name'))
    por_loja = {}
    for pessoa in pessoas:
        por_loja.setdefault(pessoa.sector_id, []).append(pessoa)

    nos = []
    for loja in lojas:
        equipe = por_loja.get(loja.id, [])
        linhas = [{'pessoa': p, 'situacao': _situacao(p, inicio, fim)} for p in equipe]
        ativos = [l for l in linhas if l['situacao'] in ('ativo', 'novo')]
        nos.append({
            'loja': loja,
            'pessoas': linhas,
            'quadro': len(ativos),
            'novos': sum(1 for l in linhas if l['situacao'] == 'novo'),
            'desligados': sum(1 for l in linhas if l['situacao'] in ('desligado', 'inativo')),
            'validacao': validacoes.get(loja.id),
            'mudou': (validacoes.get(loja.id) is not None
                      and validacoes[loja.id].quantidade != len(ativos)),
        })

    return {
        'referencia': inicio,
        'fim': fim,
        'nos': nos,
        'total_lojas': len(nos),
        'validadas': sum(1 for n in nos if n['validacao']),
        'quadro_total': sum(n['quadro'] for n in nos),
        'desligados_total': sum(n['desligados'] for n in nos),
        'novos_total': sum(n['novos'] for n in nos),
    }
