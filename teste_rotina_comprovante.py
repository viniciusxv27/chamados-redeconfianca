"""Rotina Gerencial: "necessita comprovante" e a conclusão da atividade do dia.

Pedido: "em /rotina-gerencial/gestao/ — devo poder marcar 'Necessita
comprovante', e quando isso tiver marcado, as atividades que tiverem um check
em 'necessita comprovante' deve ser enviado quando a pessoa finalizar aquela
atividade".

Antes não havia como dar uma atividade por concluída: a rotina era só o
relógio (passou do horário, contava como feita). Agora a atividade pode exigir
comprovante, a pessoa conclui o dia dela enviando o arquivo, e fica registrado
quem concluiu, quando e com qual comprovante.

O que este teste cobre:

- o interruptor na tela da gestão (formulário da atividade) e na API, e a cópia
  do modelo para a pessoa levando o campo junto;
- concluir: sem comprovante quando não exige, com comprovante quando exige, e a
  recusa (no servidor) de concluir sem o arquivo;
- o arquivo indo mesmo para o MinIO, com o mesmo conteúdo, e a URL na resposta;
- o dia: não dá para concluir dia futuro nem dia de outro dia da semana;
- quem pode: só a dona conclui; a gestão vê e pode desfazer; estranho não mexe;
- concluir de novo no mesmo dia troca o comprovante em vez de duplicar;
- desfazer tira a conclusão e o arquivo;
- a semana devolve a conclusão do dia certo para a tela.

O relógio fica parado numa quarta-feira. O que sobe para o MinIO é apagado no
fim; o banco roda em transação desfeita.
"""
import os
import sys
from datetime import datetime
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-rt-comp'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-rt-comp-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client
from django.utils import timezone

from rotina import servicos
from rotina.models import (AtividadeModelo, AtividadeRotina, ConclusaoAtividade,
                           ModeloRotina, RotinaGerencial)
from users.models import Sector

User = get_user_model()
ok = fail = 0
subidos = []

FUSO = timezone.get_current_timezone()
QUARTA = timezone.make_aware(datetime(2026, 9, 16, 12, 5), FUSO)   # quarta-feira
QUARTA_DIA = '2026-09-16'
QUINTA_DIA = '2026-09-17'                                          # ainda não chegou
SEGUNDA_DIA = '2026-09-14'
IMAGEM = (b'\xff\xd8\xff\xe0zz-comprovante-de-teste' + bytes(range(256)))


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def arquivo(nome='comprovante.jpg', dados=IMAGEM, tipo='image/jpeg'):
    return SimpleUploadedFile(nome, dados, content_type=tipo)


marcador = transaction.atomic()
marcador.__enter__()
relogio = mock.patch('rotina.servicos.agora', return_value=QUARTA)
relogio.start()
try:
    setor = Sector.objects.create(name='ZZ Loja do comprovante')
    chefe = User.objects.create_user(
        username='zzrc.chefe', email='zzrc.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
    gerente = User.objects.create_user(
        username='zzrc.gerente', email='zzrc.gerente@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Gerente', sector=setor)
    outra = User.objects.create_user(
        username='zzrc.outra', email='zzrc.outra@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Outra', sector=setor)

    rotina = RotinaGerencial.objects.create(user=gerente, criado_por=chefe)
    RotinaGerencial.objects.create(user=outra, criado_por=chefe)

    # quarta-feira (dia 2), uma que exige comprovante e outra que não
    com_comprovante = AtividadeRotina.objects.create(
        rotina=rotina, dia_semana=2, inicio='09:00', fim='10:00',
        titulo='ZZ Conferir a vitrine', exige_comprovante=True)
    sem_comprovante = AtividadeRotina.objects.create(
        rotina=rotina, dia_semana=2, inicio='10:00', fim='10:30', titulo='ZZ Alinhamento')
    outro_dia = AtividadeRotina.objects.create(
        rotina=rotina, dia_semana=4, inicio='09:00', fim='09:30',
        titulo='ZZ Fechamento da semana', exige_comprovante=True)
    # Quinta-feira: o dia dela ainda não chegou nesta quarta.
    amanha = AtividadeRotina.objects.create(
        rotina=rotina, dia_semana=3, inicio='09:00', fim='09:30',
        titulo='ZZ Amanhã', exige_comprovante=True)

    cg = Client(); cg.force_login(gerente)
    cc = Client(); cc.force_login(chefe)
    co = Client(); co.force_login(outra)

    print('== A GESTÃO MARCA "NECESSITA COMPROVANTE" ==')
    html = cc.get(f'/rotina-gerencial/gestao/{gerente.id}/').content.decode()
    t('o interruptor está no formulário da atividade',
      'name="exige_comprovante"' in html and 'Necessita comprovante' in html)
    t('e explica o que acontece', 'precisa enviar uma foto' in html)

    import json
    r = cc.post('/rotina-gerencial/api/rotina/atividades/',
                data=json.dumps({'usuario': gerente.id, 'titulo': 'ZZ Nova com comprovante',
                                 'inicio': '14:00', 'fim': '14:30', 'dia_semana': 2,
                                 'exige_comprovante': True}),
                content_type='application/json')
    dados = r.json()
    nova = AtividadeRotina.objects.filter(titulo='ZZ Nova com comprovante').first()
    t('a gestão cria atividade já exigindo comprovante',
      nova is not None and nova.exige_comprovante, r.content[:120])
    t('e a resposta diz isso para a tela',
      dados['atividades'][0]['exige_comprovante'] is True, dados['atividades'][0])

    r = cc.post(f'/rotina-gerencial/api/rotina/atividades/{sem_comprovante.id}/',
                data=json.dumps({'exige_comprovante': True}), content_type='application/json')
    sem_comprovante.refresh_from_db()
    t('e consegue ligar depois numa atividade que já existia', sem_comprovante.exige_comprovante)
    sem_comprovante.exige_comprovante = False
    sem_comprovante.save(update_fields=['exige_comprovante'])

    modelo = ModeloRotina.objects.create(nome='ZZ Modelo comprovante', criado_por=chefe)
    AtividadeModelo.objects.create(modelo=modelo, dia_semana=1, inicio='08:00', fim='08:30',
                                   titulo='ZZ Do modelo', exige_comprovante=True)
    servicos.aplicar_modelo(RotinaGerencial.objects.get(user=outra), modelo, chefe)
    copiada = AtividadeRotina.objects.filter(rotina__user=outra, titulo='ZZ Do modelo').first()
    t('aplicar o modelo leva o "necessita comprovante" junto',
      copiada is not None and copiada.exige_comprovante, copiada)

    print('\n== A PESSOA CONCLUI ==')
    r = cg.post(f'/rotina-gerencial/api/rotina/atividades/{sem_comprovante.id}/concluir/',
                {'data': QUARTA_DIA, 'observacao': 'tudo certo'})
    dados = r.json()
    t('atividade sem exigência conclui sem arquivo', r.status_code == 200 and dados['ok'], r.content[:120])
    t('a resposta diz quem concluiu e quando',
      dados['conclusao']['quem'] == gerente.full_name and dados['conclusao']['quando'],
      dados.get('conclusao'))
    t('e guarda a observação', dados['conclusao']['observacao'] == 'tudo certo')
    t('sem comprovante, porque essa não pede', not dados['conclusao']['comprovante'])

    r = cg.post(f'/rotina-gerencial/api/rotina/atividades/{com_comprovante.id}/concluir/',
                {'data': QUARTA_DIA})
    t('a que exige comprovante é recusada sem o arquivo', r.status_code == 400, r.status_code)
    t('com a mensagem certa', 'precisa de comprovante' in r.json().get('erro', ''), r.json())
    t('e nada foi gravado',
      not ConclusaoAtividade.objects.filter(atividade=com_comprovante).exists())

    r = cg.post(f'/rotina-gerencial/api/rotina/atividades/{com_comprovante.id}/concluir/',
                {'data': QUARTA_DIA, 'comprovante': arquivo(), 'observacao': 'foto da vitrine'})
    dados = r.json()
    t('com o arquivo, conclui', r.status_code == 200 and dados['ok'], r.content[:160])
    conclusao = ConclusaoAtividade.objects.filter(atividade=com_comprovante).first()
    if conclusao and conclusao.comprovante:
        subidos.append(conclusao.comprovante.name)
    t('a conclusão fica no banco, com o dia certo',
      conclusao is not None and conclusao.data.isoformat() == QUARTA_DIA, conclusao)
    t('e com quem enviou', conclusao.user_id == gerente.id)

    print('\n== O COMPROVANTE VAI PARA O S3 ==')
    t('o arquivo está no MinIO', conclusao.comprovante.storage.exists(conclusao.comprovante.name))
    with conclusao.comprovante.storage.open(conclusao.comprovante.name, 'rb') as arq:
        voltou = arq.read()
    t('com o mesmo conteúdo que subiu', voltou == IMAGEM, (len(voltou), len(IMAGEM)))
    t('na pasta da rotina', conclusao.comprovante.name.startswith('rotina/comprovantes/2026/09/'),
      conclusao.comprovante.name)
    t('e a tela recebe o link do MinIO',
      dados['conclusao']['comprovante'] and 'media/rotina/comprovantes' in dados['conclusao']['comprovante'],
      dados['conclusao'].get('comprovante'))
    t('com o nome do arquivo original', conclusao.comprovante_nome == 'comprovante.jpg')

    print('\n== O DIA IMPORTA ==')
    r = cg.post(f'/rotina-gerencial/api/rotina/atividades/{amanha.id}/concluir/',
                {'data': QUINTA_DIA, 'comprovante': arquivo()})
    t('dia que ainda não chegou é recusado', r.status_code == 400
      and 'ainda não chegou' in r.json().get('erro', ''), r.json())
    r = cg.post(f'/rotina-gerencial/api/rotina/atividades/{com_comprovante.id}/concluir/',
                {'data': SEGUNDA_DIA, 'comprovante': arquivo()})
    t('data que cai noutro dia da semana também', r.status_code == 400
      and 'não é o da atividade' in r.json().get('erro', ''), r.json())
    r = cg.post(f'/rotina-gerencial/api/rotina/atividades/{com_comprovante.id}/concluir/',
                {'data': 'ontem', 'comprovante': arquivo()})
    t('data sem sentido é recusada', r.status_code == 400, r.status_code)

    print('\n== QUEM PODE ==')
    r = co.post(f'/rotina-gerencial/api/rotina/atividades/{sem_comprovante.id}/concluir/',
                {'data': QUARTA_DIA})
    t('colega não conclui a atividade de outra pessoa', r.status_code == 404, r.status_code)
    r = cc.post(f'/rotina-gerencial/api/rotina/atividades/{sem_comprovante.id}/concluir/',
                {'data': QUARTA_DIA})
    t('nem a gestão conclui no lugar dela', r.status_code == 403
      and 'a pessoa da rotina' in r.json().get('erro', ''), r.json())

    print('\n== REENVIAR E DESFAZER ==')
    antes = conclusao.comprovante.name
    r = cg.post(f'/rotina-gerencial/api/rotina/atividades/{com_comprovante.id}/concluir/',
                {'data': QUARTA_DIA, 'comprovante': arquivo('outra.png', b'zz-outra-foto', 'image/png')})
    conclusao.refresh_from_db()
    if conclusao.comprovante:
        subidos.append(conclusao.comprovante.name)
    t('reenviar troca o comprovante, sem duplicar a conclusão',
      r.status_code == 200 and ConclusaoAtividade.objects.filter(atividade=com_comprovante).count() == 1,
      ConclusaoAtividade.objects.filter(atividade=com_comprovante).count())
    t('o arquivo novo é outro', conclusao.comprovante.name != antes, conclusao.comprovante.name)
    t('e o antigo saiu do MinIO', not conclusao.comprovante.storage.exists(antes))

    r = cc.post(f'/rotina-gerencial/api/rotina/atividades/{com_comprovante.id}/desfazer/',
                {'data': QUARTA_DIA})
    t('a gestão desfaz para corrigir', r.status_code == 200, r.content[:120])
    t('a conclusão sai do banco',
      not ConclusaoAtividade.objects.filter(atividade=com_comprovante).exists())
    t('e o comprovante sai do MinIO', not conclusao.comprovante.storage.exists(conclusao.comprovante.name))

    print('\n== A SEMANA CONTA PARA A TELA ==')
    r = cg.get('/rotina-gerencial/api/rotina/')
    semana = r.json()
    por_id = {a['id']: a for a in semana['atividades']}
    t('cada atividade vem com o dia dela na semana',
      por_id[sem_comprovante.id]['data'] == QUARTA_DIA, por_id[sem_comprovante.id].get('data'))
    t('a concluída vem com a conclusão', por_id[sem_comprovante.id]['conclusao'] is not None)
    t('a que exige comprovante diz isso', por_id[com_comprovante.id]['exige_comprovante'] is True)
    t('e a que foi desfeita volta sem conclusão', por_id[com_comprovante.id]['conclusao'] is None)
    t('a de outro dia da semana traz a data dela',
      por_id[outro_dia.id]['data'] == '2026-09-18', por_id[outro_dia.id].get('data'))

    html = cg.get('/rotina-gerencial/').content.decode()
    t('a tela da pessoa tem o bloco de concluir',
      'data-rt="det-conclusao"' in html and 'Concluir esta atividade' in html)
    t('com o campo do comprovante', 'name="comprovante"' in html and 'accept="image/*,application/pdf"' in html)
finally:
    apagados = 0
    for nome in subidos:
        try:
            from django.core.files.storage import default_storage
            if default_storage.exists(nome):
                default_storage.delete(nome)
                apagados += 1
        except Exception as exc:                                  # noqa: BLE001
            print(f'  ATENÇÃO: não deu para apagar {nome}: {exc}')
    relogio.stop()
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print(f'\nrollback: nada gravado no banco; {apagados} comprovante(s) de teste apagados do MinIO.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
