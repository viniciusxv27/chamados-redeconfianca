"""Rotina gerencial: o lembrete no WhatsApp no tempo escolhido em cada atividade (padrão: 5 minutos).

Pedido: "a notificação do whatsapp, devo poder configurar o tempo antes que vai
enviar em cada tarefa individualmente, o padrão deve ser 5 minutos".

- cada atividade (do modelo e da rotina de cada pessoa) tem "Lembrete no
  WhatsApp: minutos antes" — 5 por padrão, 0 = na hora em que começa, até 120;
- o banco também tem o 5 como padrão: o servidor que ainda roda o código
  anterior (sem a coluna) continua gravando atividade;
- a API valida, a gestão muda em qualquer atividade e a pessoa só nas que ela
  mesma criou (nas da gestão ela só mexe no dia e no horário);
- aplicar modelo, copiar rotina e duplicar modelo levam o tempo junto;
- a varredura manda cada lembrete no tempo da sua atividade ("Em 15 min",
  "Em 1h30", "Agora"), uma vez só;
- o formulário e o detalhe da atividade mostram o campo.

Relógio congelado, caches em memória e o envio trocado por um dublê: nenhum
WhatsApp sai. Transação desfeita no fim.
"""
import json
import os
import sys
from datetime import date, datetime, time
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

# Nada deste teste vai para o Redis compartilhado.
settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-rw-minutos'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-rw-minutos-local'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import Client, override_settings
from django.utils import timezone

import core.evolution as evolution
from rotina import servicos, whatsapp
from rotina.models import (
    MINUTOS_WHATSAPP_MAXIMO, MINUTOS_WHATSAPP_PADRAO, AtividadeModelo, AtividadeRotina, AvisoWhatsApp,
    ModeloRotina, RotinaGerencial,
)

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


class Envio:
    """Dublê do envio pela Evolution: guarda o que seria mandado e responde que saiu."""

    def __init__(self):
        self.chamadas = []

    def __call__(self, numero, texto, **kwargs):
        self.chamadas.append((numero, texto))
        return True, '{"key":{"id":"ZZ"}}'


def rede_proibida(*args, **kwargs):
    raise AssertionError('o teste tentou falar com a Evolution de verdade')


def post_json(cliente, url, corpo):
    return cliente.post(url, data=json.dumps(corpo), content_type='application/json')


def inserir_como_codigo_antigo(modelo_cls, instancia, sem=('minutos_whatsapp',)):
    """INSERT do jeito que o código anterior faz: sem as colunas que ele não conhece."""
    campos = [f for f in modelo_cls._meta.concrete_fields if f.name != 'id' and f.name not in sem]
    sql = 'INSERT INTO {} ({}) VALUES ({}) RETURNING id'.format(
        connection.ops.quote_name(modelo_cls._meta.db_table),
        ', '.join(connection.ops.quote_name(f.column) for f in campos), ', '.join(['%s'] * len(campos)))
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql, [f.get_db_prep_save(f.pre_save(instancia, True), connection) for f in campos])
        return cursor.fetchone()[0]


DIA = date(2026, 9, 21)                                   # segunda-feira
FUSO = timezone.get_current_timezone()


def as_(h, m, s=0):
    return timezone.make_aware(datetime.combine(DIA, time(h, m, s)), FUSO)


envio = Envio()
marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(username__startswith='zzrwm.').exists(), 'usuários do teste já existem'
    with mock.patch.object(evolution, 'enviar_texto', envio), \
            mock.patch.object(evolution.urlrequest, 'urlopen', rede_proibida), \
            override_settings(EVOLUTION_API_URL='https://evolution.exemplo', EVOLUTION_API_KEY='zz',
                              EVOLUTION_INSTANCE='zz'):
        # Só as rotinas deste teste pedem WhatsApp (dentro da transação desfeita).
        RotinaGerencial.objects.update(avisar_whatsapp=False)

        def pessoa(apelido, fone='', hierarquia='PADRAO', pode_criar=False):
            u = User.objects.create_user(username=f'zzrwm.{apelido}', email=f'zzrwm.{apelido}@exemplo-teste.local',
                                         password='S3nha!teste', first_name=apelido.title(), last_name='Teste',
                                         phone=fone, hierarchy=hierarquia)
            rotina = RotinaGerencial.objects.create(user=u, ativa=True, avisar_whatsapp=True, pode_criar=pode_criar)
            return u, rotina

        chefe = User.objects.create_user(username='zzrwm.chefe', email='zzrwm.chefe@exemplo-teste.local',
                                         password='S3nha!teste', first_name='Chefe', last_name='Teste',
                                         hierarchy='SUPERADMIN')
        c_chefe = Client()
        c_chefe.force_login(chefe)

        print('== O CAMPO E O PADRÃO ==')
        ana, r_ana = pessoa('ana', '27 99999-1111', pode_criar=True)
        a_padrao = AtividadeRotina.objects.create(rotina=r_ana, dia_semana=0, inicio=time(10, 0), fim=time(10, 30),
                                                  titulo='ZZ Padrão')
        t('atividade nova já nasce com 5 minutos', a_padrao.minutos_whatsapp == MINUTOS_WHATSAPP_PADRAO == 5)
        t('e o teto é de 2 horas', MINUTOS_WHATSAPP_MAXIMO == 120)
        antiga = AtividadeRotina(rotina=r_ana, dia_semana=1, inicio=time(8, 0), fim=time(8, 30), titulo='ZZ Antiga')
        antiga_id = inserir_como_codigo_antigo(AtividadeRotina, antiga)
        t('o servidor com o código anterior (sem a coluna) ainda grava atividade — e ela vem com 5',
          AtividadeRotina.objects.get(pk=antiga_id).minutos_whatsapp == 5)
        modelo_antigo = ModeloRotina.objects.create(nome='ZZ Modelo do código antigo')
        id_modelo = inserir_como_codigo_antigo(
            AtividadeModelo, AtividadeModelo(modelo=modelo_antigo, dia_semana=2, inicio=time(9, 0), fim=time(9, 30),
                                             titulo='ZZ Antiga do modelo'))
        t('o mesmo na atividade de modelo', AtividadeModelo.objects.get(pk=id_modelo).minutos_whatsapp == 5)
        try:
            with transaction.atomic():
                AtividadeRotina.objects.filter(pk=a_padrao.pk).update(minutos_whatsapp=121)
            barrou = False
        except IntegrityError:
            barrou = True
        t('o banco recusa mais de 2 horas, por qualquer caminho', barrou)

        print('\n== API: A GESTÃO ESCOLHE ==')
        url_criar = '/rotina-gerencial/api/rotina/atividades/'
        base = {'usuario': ana.id, 'titulo': 'ZZ Quinze', 'inicio': '10:00', 'fim': '10:30', 'dia_semana': 0,
                'categoria': 'RESULTADO'}
        r = post_json(c_chefe, url_criar, {**base, 'minutos_whatsapp': 15})
        criada = r.json()['atividades'][0] if r.status_code == 201 else {}
        t('criar com 15 minutos grava 15 e a resposta já traz o campo', r.status_code == 201
          and criada.get('minutos_whatsapp') == 15
          and AtividadeRotina.objects.get(pk=criada['id']).minutos_whatsapp == 15, (r.status_code, r.content[:200]))
        r = post_json(c_chefe, url_criar, {**base, 'titulo': 'ZZ Sem escolher'})
        t('sem escolher, fica o padrão de 5', r.status_code == 201 and r.json()['atividades'][0]['minutos_whatsapp'] == 5)
        r = post_json(c_chefe, url_criar, {**base, 'titulo': 'ZZ Texto', 'minutos_whatsapp': '30'})
        t('número vindo como texto também vale', r.status_code == 201 and r.json()['atividades'][0]['minutos_whatsapp'] == 30)
        for valor in (-1, 121, 'abc', True, 12.5, None, [5]):
            r = post_json(c_chefe, url_criar, {**base, 'titulo': f'ZZ Inválido {valor!r}', 'minutos_whatsapp': valor})
            t(f'valor inválido ({valor!r}) é recusado com a explicação', r.status_code == 400
              and 'vai de 0 (na hora em que começa) a 120 minutos antes' in r.json().get('erro', ''),
              (r.status_code, r.content[:160]))
        t('e nenhuma atividade inválida foi gravada',
          not AtividadeRotina.objects.filter(rotina=r_ana, titulo__startswith='ZZ Inválido').exists())
        url_atualizar = f"/rotina-gerencial/api/rotina/atividades/{criada['id']}/"
        r = post_json(c_chefe, url_atualizar, {'minutos_whatsapp': 0})
        t('a gestão muda para 0 (na hora)', r.status_code == 200 and r.json()['atividade']['minutos_whatsapp'] == 0
          and AtividadeRotina.objects.get(pk=criada['id']).minutos_whatsapp == 0)
        r = post_json(c_chefe, url_atualizar, {'inicio': '11:00', 'fim': '11:30'})
        t('mudar só o horário não mexe no tempo do lembrete',
          r.status_code == 200 and AtividadeRotina.objects.get(pk=criada['id']).minutos_whatsapp == 0)
        r = c_chefe.get(f'/rotina-gerencial/api/rotina/?usuario={ana.id}')
        t('a semana (API) traz o tempo de cada atividade',
          r.status_code == 200 and all('minutos_whatsapp' in a for a in r.json()['atividades']))

        print('\n== A PRÓPRIA PESSOA ==')
        c_ana = Client()
        c_ana.force_login(ana)
        r = post_json(c_ana, url_criar, {'titulo': 'ZZ Minha', 'inicio': '14:00', 'fim': '14:30', 'dia_semana': 0,
                                         'minutos_whatsapp': 10})
        minha = r.json()['atividades'][0] if r.status_code == 201 else {}
        t('quem pode criar escolhe o tempo nas atividades dela', r.status_code == 201
          and minha.get('minutos_whatsapp') == 10, (r.status_code, r.content[:200]))
        r = post_json(c_ana, f"/rotina-gerencial/api/rotina/atividades/{minha['id']}/", {'minutos_whatsapp': 20})
        t('e muda depois', r.status_code == 200 and AtividadeRotina.objects.get(pk=minha['id']).minutos_whatsapp == 20)
        r = post_json(c_ana, f'/rotina-gerencial/api/rotina/atividades/{a_padrao.id}/', {'minutos_whatsapp': 30})
        t('na atividade da gestão, não (só o dia e o horário)', r.status_code == 403
          and 'só pode mudar o dia e o horário' in r.json().get('erro', '')
          and AtividadeRotina.objects.get(pk=a_padrao.id).minutos_whatsapp == 5, (r.status_code, r.content[:160]))
        r = post_json(c_ana, f'/rotina-gerencial/api/rotina/atividades/{a_padrao.id}/',
                      {'dia_semana': 0, 'inicio': '10:00', 'fim': '10:30', 'minutos_whatsapp': 5})
        t('mandar o mesmo valor junto com o horário não é "mudar"', r.status_code == 200, (r.status_code, r.content[:160]))

        print('\n== MODELOS E CÓPIAS LEVAM O TEMPO JUNTO ==')
        modelo = ModeloRotina.objects.create(nome='ZZ Modelo com tempos', criado_por=chefe)
        r = post_json(c_chefe, f'/rotina-gerencial/api/modelos/{modelo.id}/atividades/',
                      {'titulo': 'ZZ Do modelo', 'inicio': '09:00', 'fim': '09:30', 'repetir_em': [0, 1],
                       'minutos_whatsapp': 45})
        t('no modelo também se escolhe (e o "repetir em" leva a todos os dias)', r.status_code == 201
          and [a['minutos_whatsapp'] for a in r.json()['atividades']] == [45, 45], (r.status_code, r.content[:200]))
        atividade_modelo = AtividadeModelo.objects.filter(modelo=modelo).first()
        r = post_json(c_chefe, f'/rotina-gerencial/api/modelos/atividades/{atividade_modelo.id}/', {'minutos_whatsapp': 60})
        t('e muda depois no modelo', r.status_code == 200
          and AtividadeModelo.objects.get(pk=atividade_modelo.id).minutos_whatsapp == 60)
        bia, r_bia = pessoa('bia', '27 99999-2222')
        servicos.aplicar_modelo(r_bia, modelo, chefe)
        t('aplicar o modelo leva o tempo de cada atividade',
          sorted(r_bia.atividades.values_list('minutos_whatsapp', flat=True)) == [45, 60])
        caio, r_caio = pessoa('caio', '27 99999-3333')
        servicos.copiar_rotina(r_caio, r_bia, chefe)
        t('copiar a rotina de outra pessoa também',
          sorted(r_caio.atividades.values_list('minutos_whatsapp', flat=True)) == [45, 60])
        copia = servicos.duplicar_modelo(modelo, chefe)
        t('e duplicar o modelo', sorted(copia.atividades.values_list('minutos_whatsapp', flat=True)) == [45, 60])

        print('\n== A VARREDURA MANDA NO TEMPO DE CADA ATIVIDADE ==')
        AtividadeRotina.objects.filter(rotina__in=[r_ana, r_bia, r_caio]).delete()
        dani, r_dani = pessoa('dani', '27 98888-0005')
        eva, r_eva = pessoa('eva', '27 98888-0015')
        fabi, r_fabi = pessoa('fabi', '27 98888-0000')
        gui, r_gui = pessoa('gui', '27 98888-0090')
        hel, r_hel = pessoa('hel', '27 98888-0120')

        def atividade(rotina, titulo, inicio, fim, minutos=None):
            extra = {} if minutos is None else {'minutos_whatsapp': minutos}
            return AtividadeRotina.objects.create(rotina=rotina, dia_semana=DIA.weekday(), inicio=inicio, fim=fim,
                                                  titulo=titulo, **extra)

        atividade(r_dani, 'ZZ Cinco', time(10, 0), time(10, 30))              # padrão
        atividade(r_eva, 'ZZ Quinze', time(10, 0), time(10, 30), 15)
        atividade(r_fabi, 'ZZ Na hora', time(10, 0), time(10, 30), 0)
        atividade(r_gui, 'ZZ Hora e meia', time(11, 30), time(12, 0), 90)
        atividade(r_hel, 'ZZ Bem cedo', time(6, 0), time(6, 30), 120)

        def rodada(h, m, s=0):
            envio.chamadas.clear()
            whatsapp.enviar_pendentes(as_(h, m, s))
            return {numero[-4:]: texto.split('\n')[0] for numero, texto in envio.chamadas}

        t('2 horas antes do das 6:00, ainda não (3:59)', rodada(3, 59, 0) == {})
        t('às 4:00 sai o lembrete das 6:00: "Em 2h"', rodada(4, 0, 30) == {'0120': '⏰ Em 2h: *ZZ Bem cedo*'},
          envio.chamadas)
        t('9:44 ainda é cedo para o de 15 minutos', rodada(9, 44, 50) == {})
        t('às 9:45 sai o de 15 minutos: "Em 15 min"', rodada(9, 45, 5) == {'0015': '⏰ Em 15 min: *ZZ Quinze*'},
          envio.chamadas)
        t('9:54 ainda é cedo para o padrão de 5', rodada(9, 54, 30) == {})
        t('às 9:55 sai o do padrão: "Em 5 min" — e o de 15 não repete',
          rodada(9, 55, 10) == {'0005': '⏰ Em 5 min: *ZZ Cinco*'}, envio.chamadas)
        t('o de "na hora" não manda nada antes de começar', rodada(9, 59, 40) == {})
        saida = rodada(10, 0, 20)
        t('às 10:00 sai o "na hora" ("Agora") e o de 1h30 antes das 11:30 ("Em 1h30")',
          saida == {'0000': '🔔 Agora: *ZZ Na hora*', '0090': '⏰ Em 1h30: *ZZ Hora e meia*'}, saida)
        t('e depois disso ninguém recebe de novo', rodada(10, 5) == {} and rodada(11, 25) == {} and rodada(11, 31) == {})
        avisos = dict(AvisoWhatsApp.objects.filter(data=DIA, user__username__startswith='zzrwm.')
                      .values_list('titulo', 'tipo'))
        t('uma linha por atividade: lembrete para os com antecedência, início para o "na hora"',
          avisos == {'ZZ Bem cedo': 'LEMBRETE', 'ZZ Quinze': 'LEMBRETE', 'ZZ Cinco': 'LEMBRETE',
                     'ZZ Na hora': 'INICIO', 'ZZ Hora e meia': 'LEMBRETE'}, avisos)

        print('\n== NA TELA ==')
        html = c_chefe.get(f'/rotina-gerencial/gestao/{ana.id}/').content.decode()
        t('o formulário da atividade tem o campo, de 0 a 120',
          'name="minutos_whatsapp"' in html and 'max="120"' in html and 'Lembrete no WhatsApp' in html)
        t('com a explicação do padrão e do "0 = na hora"', 'Padrão: 5 min. Use 0 para avisar na hora em que começa' in html)
        t('o detalhe da atividade tem onde mostrar o tempo', 'data-rt="det-whatsapp"' in html)
        t('o JS recebe o padrão e o teto', '"whatsappPadrao": 5' in html and '"whatsappMaximo": 120' in html)
        t('o interruptor do WhatsApp explica que o tempo é de cada atividade',
          'no tempo escolhido em cada uma (padrão: 5 minutos antes)' in html)
        html = c_chefe.get(f'/rotina-gerencial/modelos/{modelo.id}/').content.decode()
        t('o editor de modelo também tem o campo', 'name="minutos_whatsapp"' in html)
        # O selo de avisos só aparece para rotina ativa com alguma atividade (as da Ana foram apagadas acima).
        AtividadeRotina.objects.create(rotina=r_ana, dia_semana=0, inicio=time(15, 0), fim=time(15, 30),
                                       titulo='ZZ Para a tela')
        html = c_ana.get('/rotina-gerencial/').content.decode()
        t('e a tela de quem pode criar atividades próprias', 'name="minutos_whatsapp"' in html)
        t('o topo da "Minha rotina" não promete mais o WhatsApp nos mesmos 5 minutos da tela',
          'no WhatsApp, no tempo de cada atividade' in html and 'também no WhatsApp' not in html)
        html = c_chefe.get('/rotina-gerencial/gestao/').content.decode()
        t('a gestão explica o padrão ao adicionar pessoas',
          'no tempo escolhido nela — o padrão é 5 minutos antes de começar' in html)
        js = open(os.path.join(settings.BASE_DIR, 'static/rotina/rotina-calendario.js'), encoding='utf-8').read()
        t('o JS preenche o campo, valida e manda o valor',
          'corpo.minutos_whatsapp = +minutos' in js and "campos.minutos_whatsapp.value" in js
          and "'Lembrete no WhatsApp ' + duracao(minutosZap) + ' antes'" in js)
        t('nenhum WhatsApp de verdade saiu (tudo pelo dublê)', True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nenhum WhatsApp saiu.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
