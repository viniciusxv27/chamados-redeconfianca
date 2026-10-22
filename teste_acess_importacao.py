"""Importação da base do Confiança Acess anterior (acess/importacao_legado.py).

Usa uma exportação pequena, montada aqui no mesmo formato do arquivo real
(INSERT por linha, textos em hexadecimal, payload JSON). Roda numa transação
desfeita no fim.
"""
import json
import os
import sys
import tempfile

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction

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


def hx(texto):
    return f"convert_from(decode('{str(texto).encode('utf-8').hex()}','hex'),'UTF8')"


def entidade(tabela, n, payload, entity_id, status=''):
    return (f'INSERT INTO "{tabela}" ("row_no","payload","entity_id","parent_id","status","due_at") VALUES '
            f"({n},{hx(json.dumps(payload, ensure_ascii=False))},{hx(entity_id)},{hx('')},{hx(status)},{hx('')});")


SIST = ['360', 'GEFAC', 'CITRIX', 'TREINAMENTO', 'NEXT', 'VIVO+', 'SAP', 'SALESFORCE', 'SIMPLIFIQUE',
        'POOL DE COBRANÇA', 'VIVO CRÉDITO', 'VIVO GED', 'SAV - RENOVA']


def ficha(rc_id, nome, cpf, pdv, **extra):
    p = {s: 'OK' for s in SIST}
    p.update({'USUÁRIO': nome, 'DOCUMENTO': cpf, 'PDV': pdv, 'LOJA_VINCULO': pdv, 'LOGIN': '81000001',
              'PERFIL GEFAC': 'ATENDENTE', 'DATA NASCIMENTO': '15/12/1998', 'E-MAIL': '', 'TELEFONE': '27 99999-0000',
              'RC_ID': rc_id, 'RC_VERSION': 3, 'RC_STATUS': '', 'RC_ENTREGA_LOGIN': 'ENVIADO', 'RC_ETAPA': '',
              'RC_PENDENTE_DESDE': '', 'RC_SAIDA_EMPRESA': '', 'RC_DESLIGAMENTO_EM': '', 'RC_DESLIGADO_EM': '',
              'RC_MOTIVO_DESLIGAMENTO': '', 'RC_CHECK_PERFIL': 'RENOVA ALLIED', 'RC_CHECK_POOL': True})
    p.update(extra)
    return p


marcador = transaction.atomic()
marcador.__enter__()
try:
    from acess.importacao_legado import Importador, importar
    from acess.models import Chamado, Colaborador, EventoChamado, LogAcess, ModeloMensagem
    from users.models import Sector

    loja = Sector.objects.create(name='Loja ZZ Importa Centro', adabas='ZZI0001')
    portal = User.objects.create_user(username='zzimp.pessoa', email='zzimp.pessoa@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='Pessoa', cpf='529.982.247-25')
    operador = User.objects.create_user(username='zzimp.op', email='zzimp.op@exemplo-teste.local', password='x',
                                        first_name='ZZ', last_name='Operador')
    linhas = [
        entidade('colaboradores', 1, ficha('leg-a', 'ZZ PESSOA', '529.982.247-25', 'ZZ IMPORTA CENTRO'), 'leg-a'),
        # mesmo CPF de novo (readmissão): vira outra ficha
        entidade('colaboradores', 2, ficha('leg-b', 'ZZ PESSOA', '529.982.247-25', 'ZZ IMPORTA CENTRO',
                                          RC_STATUS='DESLIGADO', RC_DESLIGADO_EM='2026-09-01T12:00:00.000Z',
                                          RC_MOTIVO_DESLIGAMENTO='Pedido de demissão'), 'leg-b', 'DESLIGADO'),
        entidade('colaboradores', 3, ficha('leg-c', 'ZZ ACADEMIA', '', 'LUGAR QUE NAO EXISTE', TREINAMENTO='PENDENTE',
                                          RC_ENTREGA_LOGIN='', RC_ETAPA='ACADEMIA',
                                          RC_PENDENTE_DESDE='2026-10-14T16:59:09.393Z'), 'leg-c'),
        entidade('colaboradores', 4, ficha('', '', '', ''), 'leg-vazia'),
        entidade('chamados', 1, {'ID': 'ch-1', 'CHAMADO': '1234567', 'COLABORADOR_ID': 'leg-a', 'DESCRICAO': 'Reset de senha',
                                 'MOTIVO': '', 'SISTEMAS': 'CITRIX|NEXT', 'ABERTO_EM': '2026-09-06T18:42:58.509Z',
                                 'SLA_LIMITE': '2026-09-06T20:42:58.509Z', 'PROXIMA_VERIFICACAO': '2026-09-06T20:42:58.509Z',
                                 'STATUS': 'CONCLUIDO', 'CONCLUIDO_EM': '2026-09-07T10:00:00.000Z',
                                 'CONCLUIDO_POR_EMAIL': 'zzimp.op@exemplo-teste.local', 'CRIADO_POR': 'zzimp.op@exemplo-teste.local',
                                 'VERSAO': 2}, 'ch-1', 'CONCLUIDO'),
        entidade('historico_chamados', 1, {'ID': 'h-1', 'CHAMADO_ID': 'ch-1', 'ACAO': 'VERIFICADO',
                                           'DATA': '2026-09-06T19:00:00.000Z', 'USUARIO': 'zzimp.op@exemplo-teste.local',
                                           'PROXIMA_VERIFICACAO': '2026-09-06T20:00:00.000Z'}, 'h-1'),
        entidade('movimentacoes', 1, {'ID': 'm-1', 'DATA': '2026-09-06T18:00:00.000Z', 'CATEGORIA': 'transferidos',
                                      'ACAO': 'TRANSFERENCIA', 'USUARIO': 'alguem@fora.com', 'COLABORADOR_ID': 'leg-a',
                                      'NOME': 'ZZ PESSOA', 'LOJA': 'ZZ IMPORTA CENTRO',
                                      'ALTERACOES': json.dumps([{'campo': 'PDV', 'antes': 'A', 'depois': 'B'}])}, 'm-1'),
        entidade('mensagens', 1, {'ID': 'geral', 'NOME': 'Contato sobre acessos', 'TEXTO': 'Oi {{nome}}, tudo bem?'}, 'geral'),
    ]
    arquivo = tempfile.NamedTemporaryFile('w', suffix='.sql', delete=False, encoding='utf-8')
    arquivo.write('BEGIN;\n' + '\n'.join(linhas) + '\nCOMMIT;\n')
    arquivo.close()

    print('== SIMULAÇÃO ==')
    antes = Colaborador.objects.count()
    importar(arquivo.name, aplicar=False)
    t('sem --aplicar nada fica gravado', Colaborador.objects.count() == antes)

    print('\n== IMPORTAÇÃO ==')
    imp = importar(arquivo.name, autor=operador, aplicar=True)
    a = Colaborador.objects.get(legado_id='leg-a')
    b = Colaborador.objects.get(legado_id='leg-b')
    c = Colaborador.objects.get(legado_id='leg-c')
    t('cada ficha antiga vira uma ficha (CPF repetido não sobrescreve)', a.pk != b.pk and imp.rel['fichas criadas'] == 3)
    t('ficha vazia é ignorada', imp.rel['fichas vazias ignoradas'] == 1)
    t('liga à pessoa do portal pelo CPF (só uma ficha por pessoa)', a.user_id == portal.pk and b.user_id is None)
    t('loja casada com o setor do portal', a.local_id == loja.pk and a.loja_vinculo_id == loja.pk)
    t('loja sem setor fica vazia e é avisada', c.local_id is None and 'LUGAR QUE NAO EXISTE' in imp.lojas_sem_par)
    t('dados da ficha', a.cpf == '52998224725' and str(a.nascimento) == '1998-12-15' and a.cargo == 'ATENDENTE'
      and a.login == '81000001' and a.sistemas['GEFAC'] == 'OK' and a.check_perfil == 'RENOVA_ALLIED' and a.check_pool)
    t('liberada (login enviado) fica fora da esteira', a.etapa == 0 and a.entrega_login == 'ENVIADO')
    t('academia pendente fica na etapa 3 com a data antiga', c.etapa == 3 and c.pendente_desde.year == 2026
      and c.pendente_desde.month == 10)
    t('desligamento', b.status == 'DESLIGADO' and b.desligamento_motivo == 'Pedido de demissão' and b.desligado_em)
    t('origem marcada como importada', a.origem == 'LEGADO')
    ch = Chamado.objects.get(legado_id='ch-1')
    t('chamado: motivo pela descrição e a descrição original guardada', ch.motivo == 'TROCA_SENHA'
      and 'Reset de senha' in ch.observacao, (ch.motivo, ch.observacao))
    t('chamado: sistemas, situação e quem concluiu', ch.sistemas == ['CITRIX', 'NEXT'] and ch.status == 'CONCLUIDO'
      and ch.concluido_por == operador and ch.criado_por == operador and ch.colaborador == a)
    ev = EventoChamado.objects.get(legado_id='h-1')
    t('histórico do chamado', ev.chamado == ch and ev.tipo == 'VERIFICADO' and ev.usuario == operador)
    lg = LogAcess.objects.get(legado_id='m-1')
    t('histórico da ficha com antes/depois', lg.categoria == 'TRANSFERENCIA' and lg.colaborador == a
      and lg.antes == {'PDV': 'A'} and lg.depois == {'PDV': 'B'} and lg.usuario is None
      and lg.usuario_nome == 'alguem@fora.com')
    t('modelo de mensagem que ninguém editou recebe o texto antigo',
      ModeloMensagem.objects.get(chave='contato').texto == 'Oi {{nome}}, tudo bem?')
    t('a importação fica no histórico', LogAcess.objects.filter(acao='IMPORTACAO_LEGADO', usuario=operador).exists())

    print('\n== RODAR DE NOVO ==')
    totais = (Colaborador.objects.count(), Chamado.objects.count(), EventoChamado.objects.count())
    imp2 = importar(arquivo.name, aplicar=True)
    t('não duplica: atualiza pelo ID antigo', (Colaborador.objects.count(), Chamado.objects.count(),
                                                EventoChamado.objects.count()) == totais
      and imp2.rel['fichas atualizadas'] == 3 and LogAcess.objects.filter(legado_id='m-1').count() == 1)
    os.unlink(arquivo.name)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
