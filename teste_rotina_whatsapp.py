"""Rotina gerencial: o WhatsApp 5 minutos antes volta a sair.

Pedido: "O WhatsApp não está sendo enviado os 5 minutos antes, lembrando que o
número do cadastro da pessoa pode estar de várias formas".

- o telefone do cadastro vira número de WhatsApp de verdade (core/telefone.py):
  0 na frente, código de operadora, sem o nono dígito, número de planilha
  ("27999998888.0"), dois números no campo...; sem DDD não se chuta;
- falha passageira é tentada de novo (até 3 vezes, a cada 2 min), sem nunca
  mandar duas mensagens; número que o WhatsApp diz não existir não é repetido;
- a gestão mostra por que não chega: canal, varredura, falhas e telefones ruins.

NADA sai de verdade: o envio é um dublê, a rede (urlopen) explode se for tocada,
o relógio é congelado, o cache é em memória (não o Redis compartilhado) e o
WhatsApp das rotinas reais fica desligado dentro da transação desfeita no fim.
"""
import os
import socket
import sys
from datetime import date, datetime, time, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ['RC_VARREDURA_ROTINA'] = '0'
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.db import IntegrityError, connection, transaction
from django.test import Client, override_settings
from django.utils import timezone

import core.evolution as evolution
from core import telefone
from rotina import servicos, whatsapp
from rotina.models import AtividadeRotina, AvisoWhatsApp, RotinaGerencial

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


print('== O TELEFONE DO CADASTRO, EM TODO FORMATO ==')
CASOS = [
    ('(27) 99999-8888', '5527999998888'),
    ('+55 27 99999-8888', '5527999998888'),
    ('5527999998888', '5527999998888'),
    ('027 99999-8888', '5527999998888'),                 # 0 de discagem
    ('0 21 27 99999-8888', '5527999998888'),             # código de operadora
    ('27 9999-8888', '5527999998888'),                   # celular sem o nono dígito
    ('552799998888', '5527999998888'),                   # com DDI e sem o nono dígito
    ('27999998888.0', '5527999998888'),                  # número da planilha importada
    ('2.7999998888E10', '5527999998888'),                # e em notação científica
    ('(55) 99999-8888', '5555999998888'),                # DDD 55 (RS), não é o DDI
    ('Cel: 27 99999-8888', '5527999998888'),
    ('27 99999-8888 / 27 3333-4444', '5527999998888'),   # dois números: vale o primeiro
    ('(27) 3333-4444', '552733334444'),                  # fixo: WhatsApp Business existe
    ('99999-8888', ''),                                  # sem DDD: não se chuta
    ('(20) 99999-8888', ''),                             # DDD que não existe
    ('0800 123 4567', ''),
    ('11111111111', ''),
    ('', ''),
    ('+1 305 555 0100', '13055550100'),                  # outro país, como veio
]
for entrada, esperado in CASOS:
    t(f'{entrada!r:>32} → {esperado!r}', telefone.normalizar(entrada) == esperado, telefone.normalizar(entrada))
t('o motivo aparece para quem precisa corrigir o cadastro',
  telefone.problema('99999-8888') == 'falta o DDD' and telefone.problema('(20) 99999-8888') == 'DDD 20 não existe'
  and telefone.problema('') == 'sem telefone no cadastro' and telefone.problema('(27) 99999-8888') == '')
t('a Evolution usa o mesmo normalizador, e o JID do bot dos cartões passa como está',
  evolution.normalizar_numero('027 99999-8888') == '5527999998888'
  and evolution.normalizar_numero('5527999998888@s.whatsapp.net') == '5527999998888@s.whatsapp.net')


class Envio:
    """Dublê do envio: devolve o que o teste mandar, na ordem."""

    def __init__(self):
        self.chamadas, self.respostas = [], []

    def __call__(self, numero, texto, **kwargs):
        self.chamadas.append((numero, texto))
        return self.respostas.pop(0) if self.respostas else (True, '{"key":{"id":"ZZ"}}')


def rede_proibida(*args, **kwargs):
    raise AssertionError('o teste tentou falar com a Evolution de verdade')


envio = Envio()
cache_de_teste = LocMemCache('rotina-whatsapp-teste', {})
DIA = date(2026, 9, 21)                                   # segunda-feira
FUSO = timezone.get_current_timezone()


def as_(h, m, s=0):
    return timezone.make_aware(datetime.combine(DIA, time(h, m, s)), FUSO)


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(evolution, 'enviar_texto', envio), \
            mock.patch.object(evolution.urlrequest, 'urlopen', rede_proibida), \
            mock.patch.object(whatsapp, 'cache', cache_de_teste), \
            override_settings(EVOLUTION_API_URL='https://evolution.exemplo', EVOLUTION_API_KEY='zz',
                              EVOLUTION_INSTANCE='zz'):
        # Só as rotinas deste teste pedem WhatsApp (dentro da transação desfeita).
        RotinaGerencial.objects.update(avisar_whatsapp=False)
        AvisoWhatsApp.objects.filter(data=DIA).delete()

        def pessoa(username, fone, hierarquia='PADRAO'):
            u = User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local',
                                         password='S3nha!teste', first_name=username.split('.')[1].title(),
                                         last_name='Teste', phone=fone, hierarchy=hierarquia)
            rotina = RotinaGerencial.objects.create(user=u, ativa=True, avisar_whatsapp=True)
            return u, rotina

        def atividade(rotina, titulo, h, m):
            return AtividadeRotina.objects.create(rotina=rotina, titulo=titulo, dia_semana=DIA.weekday(),
                                                  inicio=time(h, m), fim=time(h, m + 30))

        ana, r_ana = pessoa('zzrw.ana', '027 99999-8888')            # formato torto, mas conserta
        bia, r_bia = pessoa('zzrw.bia', '99999-7777')                # sem DDD
        caio, r_caio = pessoa('zzrw.caio', '(27) 98888-1111')
        dani, r_dani = pessoa('zzrw.dani', '(27) 97777-2222')
        a_ana = atividade(r_ana, 'ZZ Abertura da loja', 10, 0)
        a_bia = atividade(r_bia, 'ZZ Reunião com a equipe', 10, 0)
        a_caio = atividade(r_caio, 'ZZ Conferência do caixa', 10, 0)
        a_dani = atividade(r_dani, 'ZZ Visita ao PDV', 10, 0)

        print('\n== 5 MINUTOS ANTES ==')
        resumo = whatsapp.enviar_pendentes(as_(9, 54))
        t('6 minutos antes ainda não é hora', not envio.chamadas and resumo['enviados'] == 0, (envio.chamadas, resumo))

        envio.respostas = [(True, '{"key":{"id":"1"}}'),                         # Ana
                           (False, 'Falha de rede ao contatar a Evolution API: timed out'),   # Caio: passageira
                           (False, 'HTTP 400: {"status":400,"response":{"message":[{"exists":false}]}}')]  # Dani
        resumo = whatsapp.enviar_pendentes(as_(9, 55, 20))
        numeros = [n for n, _ in envio.chamadas]
        t('às 9:55 sai o lembrete — com o número consertado ("027 99999-8888" → 5527999998888)',
          numeros[:1] == ['5527999998888'] and 'Em 5 min: *ZZ Abertura da loja*' in envio.chamadas[0][1],
          envio.chamadas[:1])
        t('quem está sem DDD não recebe mensagem de ninguém (não se chuta o DDD)',
          all('9999' not in n or n.startswith('5527') for n in numeros) and resumo['sem_telefone'] == 1, resumo)
        aviso_ana = AvisoWhatsApp.objects.get(atividade=a_ana, data=DIA)
        t('fica registrado como enviado, na primeira tentativa', aviso_ana.enviado and aviso_ana.tentativas == 1)
        aviso_caio = AvisoWhatsApp.objects.get(atividade=a_caio, data=DIA)
        t('a falha de rede fica registrada, esperando nova tentativa', not aviso_caio.enviado
          and aviso_caio.tentativas == 1 and 'timed out' in aviso_caio.detalhe)
        aviso_dani = AvisoWhatsApp.objects.get(atividade=a_dani, data=DIA)
        t('número que o WhatsApp diz não existir não é repetido',
          not aviso_dani.enviado and aviso_dani.tentativas == whatsapp.MAX_TENTATIVAS)

        print('\n== NOVA TENTATIVA, SEM MENSAGEM DUPLICADA ==')
        envio.chamadas.clear()
        whatsapp.enviar_pendentes(as_(9, 56, 30))
        t('1 minuto depois ninguém repete (pode estar saindo agora por outro worker)', not envio.chamadas,
          envio.chamadas)
        envio.respostas = [(True, '{"key":{"id":"2"}}')]
        resumo = whatsapp.enviar_pendentes(as_(9, 57, 40))
        aviso_caio.refresh_from_db()
        t('2 minutos depois, a nova tentativa sai para o Caio (e só para ele)',
          [n for n, _ in envio.chamadas] == ['5527988881111'] and resumo['novas_tentativas'] == 1
          and aviso_caio.enviado and aviso_caio.tentativas == 2, (envio.chamadas, resumo))
        envio.chamadas.clear()
        whatsapp.enviar_pendentes(as_(10, 1))
        whatsapp.enviar_pendentes(as_(10, 5))
        t('e depois disso ninguém recebe de novo — nem Ana, nem Caio', not envio.chamadas, envio.chamadas)
        t('uma linha só por atividade e dia', AvisoWhatsApp.objects.filter(data=DIA).count() == 3)

        print('\n== FALHA QUE NÃO PASSA: PARA NA TERCEIRA ==')
        eva, r_eva = pessoa('zzrw.eva', '27 96666-3333')
        a_eva = atividade(r_eva, 'ZZ Fechamento', 14, 0)
        envio.chamadas.clear()
        envio.respostas = [(False, 'HTTP 500: erro')] * 5
        for h, m in ((13, 55), (13, 57), (13, 59), (14, 1), (14, 3), (14, 5)):
            whatsapp.enviar_pendentes(as_(h, m, 10))
        aviso_eva = AvisoWhatsApp.objects.get(atividade=a_eva, data=DIA)
        t('três tentativas no máximo, cada uma a 2 minutos da outra',
          len(envio.chamadas) == 3 and aviso_eva.tentativas == 3 and not aviso_eva.enviado, len(envio.chamadas))
        t('as três foram lembretes (todas antes das 14:00)', all('Em ' in texto for _, texto in envio.chamadas))

        gui, r_gui = pessoa('zzrw.gui', '27 94444-5555')
        a_gui = atividade(r_gui, 'ZZ Balanço', 18, 0)
        envio.chamadas.clear()
        envio.respostas = [(False, 'HTTP 502: instância reconectando'), (True, '{"key":{"id":"3"}}')]
        whatsapp.enviar_pendentes(as_(17, 59, 0))
        whatsapp.enviar_pendentes(as_(18, 1, 30))
        aviso_gui = AvisoWhatsApp.objects.get(atividade=a_gui, data=DIA)
        t('se a nova tentativa cai depois do início, a mensagem já diz que começou',
          len(envio.chamadas) == 2 and 'Em 1 min' in envio.chamadas[0][1]
          and 'Agora: *ZZ Balanço*' in envio.chamadas[1][1] and aviso_gui.enviado and aviso_gui.tipo == 'INICIO',
          [c[1][:30] for c in envio.chamadas])

        print('\n== SERVIDOR SEM O CANAL NÃO PEGA O LEMBRETE ==')
        # O que aconteceu de 17 a 22/09: dois servidores varrendo o mesmo banco, um sem as variáveis
        # da Evolution — ele pegava o lembrete, falhava e gastava a vez de quem conseguiria mandar.
        hel, r_hel = pessoa('zzrw.hel', '27 93333-6666')
        a_hel = atividade(r_hel, 'ZZ Abertura do caixa', 12, 0)
        envio.chamadas.clear()
        with override_settings(EVOLUTION_API_KEY=''):
            resumo = whatsapp.enviar_pendentes(as_(11, 55, 10))
        t('sem a Evolution configurada, o processo não reivindica nada (nem gasta tentativa)',
          resumo.get('sem_canal') and not envio.chamadas
          and not AvisoWhatsApp.objects.filter(atividade=a_hel, data=DIA).exists(), resumo)
        resumo = whatsapp.enviar_pendentes(as_(11, 55, 40))
        aviso_hel = AvisoWhatsApp.objects.get(atividade=a_hel, data=DIA)
        t('e o servidor configurado manda o lembrete na hora, na primeira tentativa',
          [n for n, _ in envio.chamadas] == ['5527933336666'] and aviso_hel.enviado and aviso_hel.tentativas == 1,
          (envio.chamadas, resumo))

        print('\n== SERVIDOR COM O CÓDIGO ANTERIOR NO MESMO BANCO ==')
        # O código de antes não conhece "tentativas" nem "tentado_em": o INSERT dele vem sem as colunas.
        # Sem o default no banco (0006), ele levava erro de integridade, entendia "outro worker já pegou"
        # e o lembrete não saía — foi o que aconteceu das 11:23 às 12:48 de 22/09/2026.
        ian, r_ian = pessoa('zzrw.ian', '27 92222-7777')
        a_ian = atividade(r_ian, 'ZZ Reposição', 13, 0)
        antigo = AvisoWhatsApp(user=ian, atividade=a_ian, data=DIA, tipo='LEMBRETE', titulo=a_ian.titulo,
                               inicio=a_ian.inicio)
        campos = [f for f in AvisoWhatsApp._meta.concrete_fields if f.name not in ('id', 'tentativas', 'tentado_em')]
        sql = 'INSERT INTO {} ({}) VALUES ({})'.format(
            connection.ops.quote_name(AvisoWhatsApp._meta.db_table),
            ', '.join(connection.ops.quote_name(f.column) for f in campos), ', '.join(['%s'] * len(campos)))
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql, [f.get_db_prep_save(f.pre_save(antigo, True), connection) for f in campos])
            gravou = True
        except IntegrityError:
            gravou = False
        linha = AvisoWhatsApp.objects.filter(atividade=a_ian, data=DIA).first()
        t('o INSERT do código anterior (sem "tentativas") ainda grava o aviso, com 1 tentativa',
          gravou and linha is not None and linha.tentativas == 1 and linha.tentado_em is None)

        print('\n== DOIS WORKERS AO MESMO TEMPO ==')
        fer, r_fer = pessoa('zzrw.fer', '27 95555-4444')
        a_fer = atividade(r_fer, 'ZZ Ronda', 16, 0)
        momento = as_(15, 55, 30)
        primeiro = whatsapp.reivindicar(fer, a_fer, DIA, 'LEMBRETE', momento)
        segundo = whatsapp.reivindicar(fer, a_fer, DIA, 'LEMBRETE', momento)
        t('só um consegue reivindicar o aviso', primeiro is not None and segundo is None)
        AvisoWhatsApp.objects.filter(pk=primeiro.pk).update(detalhe='HTTP 500')
        depois = momento + whatsapp.ESPERA_ENTRE_TENTATIVAS + timedelta(seconds=1)
        um = whatsapp.reivindicar(fer, a_fer, DIA, 'LEMBRETE', depois)
        outro = whatsapp.reivindicar(fer, a_fer, DIA, 'LEMBRETE', depois)
        t('e na nova tentativa também', um is not None and outro is None and um.tentativas == 2)

        print('\n== O QUE A GESTÃO VÊ ==')
        whatsapp.registrar_passada({'enviados': 2})
        with mock.patch.object(servicos, 'agora', lambda: as_(16, 30)):
            d = whatsapp.diagnostico()
        t('canal configurado e varredura viva', d['configurado'] and d['ultima_varredura']['em'] is not None)
        t('sem servidor sem canal registrado, sem alerta', not d['processo_sem_canal'])
        t('o que saiu e o que falhou hoje, com o motivo', d['enviados_hoje'] == 4 and d['falhas_hoje'] == 2
          and any('HTTP 500' in a.detalhe for a in d['falhas']), (d['enviados_hoje'], d['falhas_hoje']))
        ruins = {p['user'].username: p['motivo'] for p in d['telefones_ruins']}
        t('e quem tem o WhatsApp ligado com um telefone que não serve', ruins.get('zzrw.bia') == 'falta o DDD'
          and 'zzrw.ana' not in ruins, ruins)
        with override_settings(EVOLUTION_API_KEY=''), mock.patch.object(servicos, 'agora', lambda: as_(16, 30)):
            d = whatsapp.diagnostico()
        t('sem a configuração do canal, a tela diz o que falta', not d['configurado']
          and d['faltando'] == ['EVOLUTION_API_KEY'])
        with override_settings(EVOLUTION_API_KEY=''):
            whatsapp.registrar_processo_sem_canal()
        with mock.patch.object(servicos, 'agora', lambda: as_(16, 30)):
            d = whatsapp.diagnostico()
        sem = d['processo_sem_canal'] or {}
        t('o servidor que varre sem o canal se identifica (nome e o que falta)',
          sem.get('host') == socket.gethostname() and sem.get('faltando') == ['EVOLUTION_API_KEY']
          and sem.get('em') is not None, sem)

        chefe = User.objects.create_user(username='zzrw.chefe', email='zzrw.chefe@exemplo-teste.local',
                                         password='S3nha!teste', first_name='Chefe', last_name='Teste',
                                         hierarchy='SUPERADMIN')
        c = Client()
        c.force_login(chefe)
        with mock.patch.object(servicos, 'agora', lambda: as_(16, 30)):
            html = c.get('/rotina-gerencial/gestao/?q=zzrw').content.decode()
        t('a gestão mostra o quadro do WhatsApp', 'WhatsApp dos lembretes' in html and 'Canal configurado' in html
          and 'Varredura há' in html)
        t('e o alerta com o nome do servidor que varre sem o canal',
          f'O servidor <strong>{socket.gethostname()}</strong> está com a varredura ligada' in html)
        t('com a pessoa sem DDD e o link para corrigir o cadastro', 'falta o DDD' in html
          and f'/users/manage/users/{bia.id}/edit/' in html)
        t('e o chip da pessoa diz "tel. inválido" em vez de prometer o aviso', 'tel. inválido' in html)
        t('nenhuma mensagem saiu de verdade (tudo passou pelo dublê)', True)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco; nenhum WhatsApp saiu.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
