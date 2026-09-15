"""Assistente: acesso completo às metas comerciais do Power BI (/power-bi/manage/metas/).

Pedido: o assistente precisa ter acesso completo ao módulo de metas. Aqui se
confere que ele lê o mesmo recorte da tela para cada perfil (consultor, gerente,
rede) e que a gestão das competências (excluir e sincronizar com o MySQL do
painel) só vale para SUPERADMIN, sempre com confirmação numa mensagem seguinte.

Nada sai daqui: o MySQL do painel é um dublê, o cache é de memória e tudo roda
numa transação desfeita no fim.
"""
import os
import sys
from decimal import Decimal
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.db import transaction

from assistente import ferramentas
from communications.models import CommunicationGroup
from power_bi.models import GoalEntry, GoalUpload
from users.models import Sector

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def roda(nome, args, user, turno='turno-1'):
    return ferramentas.executar(nome, args, user, turno=turno)


marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not GoalUpload.objects.filter(year=2099).exists(), 'já existe competência de 2099'
    with mock.patch.object(ferramentas, 'cache', LocMemCache('zz-metas-comerciais', {})):
        centro = Sector.objects.create(name='LOJA ZZ CENTRO')
        norte = Sector.objects.create(name='LOJA ZZ NORTE')

        def novo(apelido, primeiro, ultimo, **extra):
            return User.objects.create_user(
                username=f'zzmc.{apelido}', email=f'zzmc.{apelido}@exemplo-teste.local', password='S3nha!teste',
                first_name=primeiro, last_name=ultimo, **extra)

        admin = novo('admin', 'ZZMC', 'Admin', hierarchy='SUPERADMIN')
        consultor = novo('alfa', 'ZZCN', 'ALFA', hierarchy='PADRAO', sector=centro)
        gerente = novo('gerente', 'ZZGER', 'NORTE', hierarchy='PADRAO', sector=norte)
        grupo = CommunicationGroup.objects.filter(name__icontains='GERENTES').first()
        assert grupo is not None, 'o grupo GERENTES não existe no banco'
        grupo.members.add(gerente)

        upload = GoalUpload.objects.create(year=2099, month=1, fixa_as_percentage=True,
                                           source_file_name='ZZ METAS.xlsx', uploaded_by=admin)
        PDV, CN = GoalEntry.SHEET_PDV_REAL, GoalEntry.SHEET_CN_REAL
        for sheet, loja, pessoa, pilar, valor in (
                (PDV, 'LOJA ZZ CENTRO', '', 'MOVEL', '1000'), (PDV, 'LOJA ZZ CENTRO', '', 'FIXA', '10'),
                (PDV, 'LOJA ZZ NORTE', '', 'MOVEL', '2000'),
                (CN, 'LOJA ZZ CENTRO', 'ZZCN ALFA', 'MOVEL', '400'), (CN, 'LOJA ZZ CENTRO', 'ZZCN ALFA', 'SEGURO', '100'),
                (CN, 'LOJA ZZ NORTE', 'ZZCN BETA', 'MOVEL', '800')):
            GoalEntry.objects.create(upload=upload, sheet_type=sheet, store_name=loja, user_name=pessoa,
                                     pilar=pilar, goal_value=Decimal(valor))

        print('== AS FERRAMENTAS ==')
        esquema = {x['name'] for x in ferramentas.tools_schema()}
        t('leitura e gestão das metas comerciais estão no assistente', {
            'metas_comerciais', 'metas_comerciais_competencias', 'metas_comerciais_excluir',
            'metas_comerciais_sincronizar'} <= esquema)
        t('excluir e sincronizar são ações com confirmação', ferramentas.TOOLS['metas_comerciais_excluir'].get('acao')
          and ferramentas.TOOLS['metas_comerciais_sincronizar'].get('acao'))

        print('\n== O RECORTE DE CADA PERFIL ==')
        rede = roda('metas_comerciais', {'ano': 2099, 'mes': 1}, admin)
        t('SUPERADMIN vê a rede', 'visão da rede' in rede and '01/2099' in rede, rede[:300])
        t('total das lojas sem a FIXA (que é quantidade)', 'Total das lojas (planilha PDV): R$ 3.000,00' in rede, rede)
        t('e a FIXA aparece como quantidade', 'FIXA: 10 (quantidade)' in rede)
        t('com as lojas e os consultores', 'LOJA ZZ NORTE: R$ 2.000,00' in rede and 'ZZCN BETA' in rede)
        centro_txt = roda('metas_comerciais', {'ano': 2099, 'mes': 1, 'loja': 'zz centro'}, admin)
        t('filtro de loja na visão da rede', 'R$ 1.000,00' in centro_txt and 'ZZCN BETA' not in centro_txt, centro_txt)

        do_consultor = roda('metas_comerciais', {'ano': 2099, 'mes': 1}, consultor)
        t('consultor vê as próprias metas e as da loja', 'consultor(a)' in do_consultor and 'ZZCN ALFA' in do_consultor
          and 'R$ 1.000,00' in do_consultor, do_consultor)
        t('e não vê outra loja nem outro consultor', 'ZZCN BETA' not in do_consultor and 'R$ 2.000,00' not in do_consultor)
        t('filtros de loja não abrem outra loja para o consultor',
          'ZZCN BETA' not in roda('metas_comerciais', {'ano': 2099, 'mes': 1, 'loja': 'zz norte'}, consultor))

        do_gerente = roda('metas_comerciais', {'ano': 2099, 'mes': 1}, gerente)
        t('gerente vê a loja dele inteira', 'gerente' in do_gerente and 'R$ 2.000,00' in do_gerente
          and 'ZZCN BETA' in do_gerente, do_gerente)
        t('e não a loja dos outros', 'ZZCN ALFA' not in do_gerente and 'R$ 1.000,00' not in do_gerente)

        invalida = roda('metas_comerciais', {'ano': 2098, 'mes': 5}, admin)
        t('competência que não existe lista as que existem', 'Não há metas importadas para 05/2098' in invalida
          and '01/2099' in invalida, invalida)

        print('\n== COMPETÊNCIAS E GESTÃO ==')
        t('SUPERADMIN vê id, arquivo e linhas', f'competência #{upload.pk}' in roda('metas_comerciais_competencias', {}, admin)
          and 'ZZ METAS.xlsx' in roda('metas_comerciais_competencias', {}, admin))
        lista_consultor = roda('metas_comerciais_competencias', {}, consultor)
        t('os outros veem só os meses', '01/2099' in lista_consultor and 'ZZ METAS.xlsx' not in lista_consultor)
        t('quem não é SUPERADMIN não prepara exclusão', 'Só SUPERADMIN' in roda(
            'metas_comerciais_excluir', {'competencia_id': upload.pk}, gerente))

        conexao = mock.MagicMock()
        with mock.patch('pymysql.connect', return_value=conexao) as conectar:
            previa = roda('metas_comerciais_sincronizar', {'competencia_id': upload.pk}, admin, 'turno-1')
            t('sincronizar só prepara e explica o que muda no painel', previa.startswith('AÇÃO PREPARADA')
              and 'metas_cn' in previa and not conectar.called, previa)
            feito = roda('confirmar_acao', {'acao': 'metas_comerciais_sincronizar'}, admin, 'turno-2')
            t('confirmado, roda a sincronização da tela (MySQL dublê)', conectar.called and 'falhou' not in feito.lower(), feito)

        previa = roda('metas_comerciais_excluir', {'competencia_id': upload.pk}, admin, 'turno-1')
        t('excluir avisa que não dá para desfazer', 'Não dá para desfazer' in previa and GoalUpload.objects.filter(pk=upload.pk).exists())
        t('na mesma pergunta não exclui', roda('confirmar_acao', {'acao': 'metas_comerciais_excluir'}, admin, 'turno-1').startswith('Ainda não')
          and GoalUpload.objects.filter(pk=upload.pk).exists())
        feito = roda('confirmar_acao', {'acao': 'metas_comerciais_excluir'}, admin, 'turno-2')
        t('numa pergunta seguinte, exclui pela view da tela', not GoalUpload.objects.filter(pk=upload.pk).exists(), feito)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
