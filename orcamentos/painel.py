"""Painel analítico do módulo: volume, adoção (quem usa e quem não usa) e o andamento do Mailing.

"Quem deveria usar" = colaboradores ativos lotados nas lojas do recorte. Usa quem
fez pelo menos um orçamento no período.
"""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from vendas.models import Orcamento

from .models import ContatoMailing

User = get_user_model()


def montar(lojas, de, ate):
    ids_lojas = [l.id for l in lojas]
    nomes = {l.id: l.name for l in lojas}
    inicio = timezone.make_aware(datetime.combine(de, time.min))
    fim = timezone.make_aware(datetime.combine(ate + timedelta(days=1), time.min))

    equipe = list(User.objects.filter(is_active=True, sector_id__in=ids_lojas)
                  .only('id', 'first_name', 'last_name', 'username', 'sector_id', 'job_title').order_by('first_name'))
    orc = Orcamento.objects.filter(loja_id__in=ids_lojas, criado_em__gte=inicio, criado_em__lt=fim)
    por_vendedor = dict(orc.values('vendedor_id').annotate(n=Count('id')).values_list('vendedor_id', 'n'))
    ultimo = dict(Orcamento.objects.filter(vendedor_id__in=[u.id for u in equipe]).values('vendedor_id')
                  .annotate(u=Max('criado_em')).values_list('vendedor_id', 'u'))

    pessoas = []
    for u in equipe:
        pessoas.append({'id': u.id, 'nome': u.get_full_name() or u.username, 'cargo': u.job_title or '',
                        'loja': nomes.get(u.sector_id, ''), 'loja_id': u.sector_id,
                        'orcamentos': por_vendedor.get(u.id, 0), 'ultimo': ultimo.get(u.id)})
    usam = sorted([p for p in pessoas if p['orcamentos']], key=lambda p: -p['orcamentos'])
    nao_usam = sorted([p for p in pessoas if not p['orcamentos']],
                      key=lambda p: (p['ultimo'] is not None, p['ultimo'] or 0, p['nome']))

    contatos = ContatoMailing.objects.filter(loja_id__in=ids_lojas, base__status='ATIVA')
    mailing_loja = defaultdict(Counter)
    for loja_id, status, n in contatos.values_list('loja_id', 'status').annotate(n=Count('id')):
        mailing_loja[loja_id][status] += n
    trabalhados_periodo = ContatoMailing.objects.filter(loja_id__in=ids_lojas, trabalhado_em__gte=inicio,
                                                        trabalhado_em__lt=fim)

    por_loja_orc = dict(orc.values('loja_id').annotate(n=Count('id')).values_list('loja_id', 'n'))
    por_loja = []
    for l in lojas:
        gente = [p for p in pessoas if p['loja_id'] == l.id]
        usaram = sum(1 for p in gente if p['orcamentos'])
        m = mailing_loja.get(l.id, Counter())
        por_loja.append({'id': l.id, 'nome': l.name, 'equipe': len(gente), 'usaram': usaram,
                         'adocao': round(usaram * 100 / len(gente)) if gente else 0,
                         'orcamentos': por_loja_orc.get(l.id, 0), 'mailing_novos': m.get('NOVO', 0),
                         'mailing_trabalhados': m.get('TRABALHADO', 0), 'mailing_descartados': m.get('DESCARTADO', 0)})
    por_loja.sort(key=lambda x: (-x['adocao'], -x['orcamentos'], x['nome']))

    rotulos_pilar = dict(Orcamento.PILARES)
    por_pilar = [{'pilar': rotulos_pilar.get(p, p), 'n': n} for p, n in
                 orc.values_list('pilar').annotate(n=Count('id')).order_by('-n')]
    diario = dict(orc.annotate(d=TruncDate('criado_em')).values_list('d').annotate(n=Count('id')).values_list('d', 'n'))
    serie = []
    dia = de
    while dia <= ate:
        serie.append({'data': dia.strftime('%d/%m'), 'n': diario.get(dia, 0)})
        dia += timedelta(days=1)

    total_equipe = len(pessoas)
    return {
        'kpis': {
            'orcamentos': orc.count(), 'equipe': total_equipe, 'usam': len(usam), 'nao_usam': len(nao_usam),
            'adocao': round(len(usam) * 100 / total_equipe) if total_equipe else 0,
            'media': round(orc.count() / len(usam), 1) if usam else 0,
            'mailing_novos': sum(x['mailing_novos'] for x in por_loja),
            'trabalhados_periodo': trabalhados_periodo.count(),
            'do_mailing': orc.filter(contatos_mailing__isnull=False).distinct().count(),
        },
        'usam': usam, 'nao_usam': nao_usam, 'por_loja': por_loja, 'por_pilar': por_pilar, 'serie': serie,
    }
