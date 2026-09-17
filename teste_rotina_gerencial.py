"""Rotina Gerencial: modelo padrão, telas, API com travas, avisos e sino.

O que se confere:
- o modelo padrão (migração de dados) com a semana da planilha;
- aplicar o modelo copia as atividades para a pessoa;
- a tela da pessoa (com rotina, sem rotina, pausada) e o destaque vindo do aviso;
- as telas de gestão só abrem para SUPERADMIN, que edita a rotina de cada pessoa: botão
  "Editar rotina" em cada linha, o seletor de pessoa (em Minha rotina, Pessoas e no
  editor) e, para quem ainda não tem rotina, a tela de criar e já editar;
- a API respeita as travas: a pessoa não move atividade travada, move a livre
  e não cria sem permissão; a gestão cria, trava e apaga; horário inválido é
  recusado;
- `api/hoje/` devolve as atividades do dia e o relógio do servidor;
- `api/avisos/<id>/` é idempotente e põe exatamente uma notificação no sino,
  com o link certo e SEM web push;
- `api/lembretes/<id>/` (minutos antes) segue as mesmas regras, na janela dele,
  e convive com o aviso de início (um de cada tipo por atividade e dia);
- o cartão da home: só para quem tem rotina, uma consulta por renderização,
  cada estado do dia (agora, antes, intervalo, concluída, domingo, vazio),
  avisos ligados/sino desligado, falha silenciosa com log e o `api/hoje/cartao/`;
- a tela da pessoa traz os ganchos do layout novo (lista do dia no celular,
  abas presas, selo de avisos) e os JS passam no `node --check`;
- o parcial do notificador renderiza e o context processor liga os menus certos.

Nada é gravado: roda dentro de uma transação desfeita no fim. O relógio da
rotina (`rotina.servicos.agora`) fica parado numa quarta-feira, 12:05.
Nenhum aviso sai do servidor: o código da rotina não chama push, e ainda assim
todas as funções de envio do app de notificações viram dublês que contam
chamadas — o teste falha se alguma for chamada. O agendador do Tangerino, que
o middleware acorda a cada requisição, também vira dublê: nada de thread.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, time
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from notifications.models import NotificationPreference, UserNotification
from rotina import servicos
from rotina.context_processors import rotina_menu
from rotina.models import (
    AtividadeModelo, AtividadeRotina, AvisoRotina, ModeloRotina, RotinaGerencial, TipoAviso,
)
from users.models import Sector

User = get_user_model()
ok = fail = 0

NOME_PADRAO = 'Rotina gerencial de loja (padrão)'
FUSO = timezone.get_current_timezone()
QUARTA_MEIO_DIA = timezone.make_aware(datetime(2026, 9, 16, 12, 5), FUSO)     # quarta-feira
DOMINGO = timezone.make_aware(datetime(2026, 9, 20, 10, 0), FUSO)
ROTULOS = ('Tem influência sobre o resultado a curto e médio prazo',
           'Tem influência no dia a dia da equipe',
           'Atividade complementar')

ALVOS_DE_ENVIO = [
    'notifications.push_utils.send_push_notification_to_user',
    'notifications.push_utils.send_push_notification_to_users',
    'notifications.push_utils.push_service.send_to_user',
    'notifications.push_utils.push_service.send_to_users',
    'notifications.models.PushNotification.send_notification',
]


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def js(resposta):
    try:
        return json.loads(resposta.content.decode())
    except ValueError:
        return {}


def json_script(html, elemento):
    achado = re.search(r'<script id="%s" type="application/json">(.*?)</script>' % re.escape(elemento),
                       html, flags=re.S)
    return json.loads(achado.group(1)) if achado else None


def post_json(cliente, url, corpo, **extra):
    return cliente.post(url, data=json.dumps(corpo), content_type='application/json', **extra)


def hm(valor):
    return valor.strftime('%H:%M')


def assinatura(a):
    return (a.dia_semana, hm(a.inicio), hm(a.fim), a.titulo, a.descricao, a.categoria, a.bloqueada)


def contigua(atividades, inicio, fim):
    """O dia começa em `inicio`, termina em `fim` e cada atividade começa quando a anterior acaba."""
    if not atividades:
        return False
    ordenadas = sorted(atividades, key=lambda a: a.inicio)
    emendadas = all(ordenadas[i].inicio == ordenadas[i - 1].fim for i in range(1, len(ordenadas)))
    return hm(ordenadas[0].inicio) == inicio and hm(ordenadas[-1].fim) == fim and emendadas


def url_atividade(atividade, excluir=False):
    return f'/rotina-gerencial/api/rotina/atividades/{atividade.id}/' + ('excluir/' if excluir else '')


def quando(dia, hora, minuto, segundo=0):
    """Instante no fuso do portal, em setembro de 2026 (14 = segunda … 20 = domingo)."""
    return timezone.make_aware(datetime(2026, 9, dia, hora, minuto, segundo), FUSO)


def js_valido(codigo, rotulo):
    """`node --check` num trecho de JS; sem node, só avisa."""
    node = shutil.which('node')
    if not node:
        print(f'  (node não encontrado: sintaxe de {rotulo} não conferida)')
        return
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as arquivo:
        arquivo.write(codigo)
    verificacao = subprocess.run([node, '--check', arquivo.name], capture_output=True, text=True, timeout=30)
    os.unlink(arquivo.name)
    t(f'o JS de {rotulo} é válido (node --check)', verificacao.returncode == 0, verificacao.stderr[-400:])


marcador = transaction.atomic()
marcador.__enter__()
pilha = ExitStack()
try:
    # --- dublês: nenhum envio de verdade e relógio parado --------------------
    dubles = {alvo: pilha.enter_context(mock.patch(alvo)) for alvo in ALVOS_DE_ENVIO}
    import notifications.push_utils as push_utils
    import notifications.services as servicos_notificacao
    if hasattr(push_utils, 'webpush'):
        dubles['push_utils.webpush'] = pilha.enter_context(mock.patch.object(push_utils, 'webpush'))
    classe_servico = getattr(servicos_notificacao, 'NotificationService', None)
    for metodo in ('_send_push', '_send_onesignal', '_send_truepush'):
        if classe_servico is not None and hasattr(classe_servico, metodo):
            dubles[f'NotificationService.{metodo}'] = pilha.enter_context(mock.patch.object(classe_servico, metodo))
    try:
        import pywebpush
        dubles['pywebpush.webpush'] = pilha.enter_context(mock.patch.object(pywebpush, 'webpush'))
    except ImportError:
        pass
    import tangerino.middleware as middleware_tangerino
    if hasattr(middleware_tangerino, 'disparar_se_esta_na_hora'):
        pilha.enter_context(mock.patch.object(middleware_tangerino, 'disparar_se_esta_na_hora', return_value=False))
    relogio = pilha.enter_context(mock.patch('rotina.servicos.agora', return_value=QUARTA_MEIO_DIA))

    # --- pessoas do teste -----------------------------------------------------
    loja = Sector.objects.create(name='ZZ Loja Teste Rotina')

    def novo(apelido, **extra):
        return User.objects.create_user(
            username=f'zzrotina.{apelido}', email=f'zzrotina.{apelido}@exemplo-teste.local',
            password='S3nha!teste', first_name='ZZRotina', last_name=apelido.title(), sector=loja, **extra)

    superadmin = novo('superadmin', hierarchy='SUPERADMIN')
    superusuario = novo('superusuario', is_superuser=True)
    gerente = novo('gerente')              # rotina do modelo, sem poder criar
    criadora = novo('criadora')            # rotina do modelo, pode criar
    pausada = novo('pausada')              # rotina desativada
    vazia = novo('vazia')                  # rotina ativa, sem atividades
    ninguem = novo('ninguem')              # nunca terá rotina
    adicionada = novo('adicionada')        # entra pela tela de gestão

    def cliente(user=None, **opcoes):
        c = Client(**opcoes)
        if user is not None:
            c.force_login(user)
        return c

    print('== MODELO PADRÃO (MIGRAÇÃO DE DADOS) ==')
    modelo = ModeloRotina.objects.filter(nome=NOME_PADRAO).first()
    if modelo is None:
        # No banco de verdade o modelo da migração pode ter sido renomeado e editado pela gestão
        # (em 17/09/2026 virou "Rotina lojas de rua", com outras atividades). O teste confere a
        # migração: recria o modelo original com a própria função dela, dentro da transação desfeita.
        from importlib import import_module

        from django.apps import apps as registro_de_apps
        import_module('rotina.migrations.0002_modelo_padrao_loja').criar_modelo_padrao(registro_de_apps, None)
        modelo = ModeloRotina.objects.filter(nome=NOME_PADRAO).first()
        print('  (o modelo padrão foi renomeado no banco: recriado da migração só para este teste)')
    t('o modelo padrão existe', modelo is not None)
    do_modelo = list(modelo.atividades.all()) if modelo else []
    por_dia = {d: [a for a in do_modelo if a.dia_semana == d] for d in range(6)}

    def achar(dia, inicio):
        return next((a for a in por_dia.get(dia, []) if hm(a.inicio) == inicio), None)

    contagem = [len(por_dia[d]) for d in range(6)]
    t('64 atividades: 11 por dia útil, 13 na quinta e 7 no sábado',
      len(do_modelo) == 64 and contagem == [11, 11, 11, 13, 11, 7], (len(do_modelo), contagem))
    matinal = achar(0, '08:40')
    t('segunda 08:40–09:00 é o "Matinal com o DIRETOR", travado',
      matinal is not None and matinal.titulo == 'Matinal com o DIRETOR' and hm(matinal.fim) == '09:00'
      and matinal.bloqueada and matinal.categoria == 'RESULTADO', matinal)
    t('nos outros dias o matinal é "Matinal de alinhamento"',
      all(achar(d, '08:40') and achar(d, '08:40').titulo == 'Matinal de alinhamento' for d in (1, 2, 3, 4, 5)))
    t('terça e quinta a abertura de caixa vem com a conferência de preços',
      all(achar(d, '08:20').titulo == 'Conferência/ajuste de preços · Abertura de caixa' for d in (1, 3))
      and achar(2, '08:20').titulo == 'Abertura de caixa')
    quiz = achar(3, '09:00')
    t('quinta 09:00–09:15 é o quiz semanal, com o link, complementar e livre',
      quiz is not None and quiz.titulo == 'Quiz semanal' and hm(quiz.fim) == '09:15'
      and quiz.descricao == 'https://girovquiz.com.br/' and quiz.categoria == 'COMPLEMENTAR' and not quiz.bloqueada, quiz)
    curso = achar(3, '09:15')
    t('quinta 09:15–09:30 é o curso foco, travado',
      curso is not None and curso.titulo == 'Curso foco (se houver)' and hm(curso.fim) == '09:30' and curso.bloqueada)
    t('quinta o salão da manhã começa às 09:30', achar(3, '09:30') and achar(3, '09:30').titulo == 'Atuação no salão')
    ultimo = por_dia[5][-1] if por_dia[5] else None
    t('sábado termina com o fechamento das 13:00–13:30',
      ultimo is not None and (hm(ultimo.inicio), hm(ultimo.fim)) == ('13:00', '13:30')
      and ultimo.titulo == 'Fechamento (lojas de rua) · Fechamento de caixa · Conferência SAP x Vivo Go'
      and ultimo.bloqueada, ultimo)
    t('vermelho (resultado) entra travado, azul-claro livre, e nenhum amarelo',
      all(a.bloqueada == (a.categoria == 'RESULTADO') for a in do_modelo)
      and not any(a.categoria == 'EQUIPE' for a in do_modelo))
    t('dias úteis emendados das 08:00 às 18:30', all(contigua(por_dia[d], '08:00', '18:30') for d in range(5)))
    t('sábado emendado das 08:00 às 13:30', contigua(por_dia[5], '08:00', '13:30'))

    print('\n== APLICAR O MODELO ==')
    rotina_gerente = RotinaGerencial.objects.create(user=gerente, criado_por=superadmin)
    total = servicos.aplicar_modelo(rotina_gerente, modelo, superadmin)
    copiadas = list(rotina_gerente.atividades.all())
    t('copia as 64 atividades', total == 64 and len(copiadas) == 64, (total, len(copiadas)))
    t('mesmos dias, horários, títulos, descrições, categorias e travas',
      sorted(map(assinatura, copiadas)) == sorted(map(assinatura, do_modelo)))
    rotina_gerente.refresh_from_db()
    t('guarda de qual modelo veio e quem aplicou',
      rotina_gerente.modelo_origem_id == modelo.id and rotina_gerente.atualizado_por_id == superadmin.id)
    t('aplicar de novo substitui, não duplica',
      servicos.aplicar_modelo(rotina_gerente, modelo, superadmin) == 64 and rotina_gerente.atividades.count() == 64)
    t('nada copiado conta como "criado pela pessoa"',
      not rotina_gerente.atividades.filter(criada_pela_pessoa=True).exists())
    uma = rotina_gerente.atividades.get(dia_semana=4, inicio='17:00')
    uma.titulo = 'Conferência D-1 (ajustada)'
    uma.save()
    t('mexer na cópia não mexe no modelo', achar(4, '17:00').titulo == 'Conferência D-1'
      and AtividadeModelo.objects.get(pk=achar(4, '17:00').pk).titulo == 'Conferência D-1')

    rotina_criadora = RotinaGerencial.objects.create(user=criadora, pode_criar=True)
    servicos.aplicar_modelo(rotina_criadora, modelo, superadmin)
    rotina_pausada = RotinaGerencial.objects.create(user=pausada, ativa=False)
    servicos.aplicar_modelo(rotina_pausada, modelo, superadmin)
    rotina_vazia = RotinaGerencial.objects.create(user=vazia)

    c_gerente = cliente(gerente)
    c_admin = cliente(superadmin)

    print('\n== TELA DA PESSOA ==')
    r = c_gerente.get('/rotina-gerencial/')
    html = r.content.decode()
    t('abre (200) para quem tem rotina', r.status_code == 200, r.status_code)
    t('traz o calendário e os dados da semana',
      'id="rt-calendario"' in html and 'id="rt-config"' in html and 'id="rt-dados"' in html)
    dados = json_script(html, 'rt-dados') or {}
    semana = dados.get('semana', {})
    t('com as 64 atividades e a semana de segunda (14/09) a sábado (19/09)',
      len(dados.get('atividades', [])) == 64 and (semana.get('inicio'), semana.get('fim')) == ('2026-09-14', '2026-09-19'),
      (len(dados.get('atividades', [])), semana.get('inicio'), semana.get('fim')))
    t('hoje (quarta) marcado e o relógio do servidor junto',
      [d['dia_semana'] for d in semana.get('dias', []) if d['hoje']] == [2]
      and semana.get('agora') == '2026-09-16T12:05:00')
    t('a legenda tem as três categorias', all(rotulo in html for rotulo in ROTULOS))
    t('tem o cartão "Agora" e a lista de hoje', 'data-rt="agora"' in html and 'data-rt="hoje-lista"' in html)
    t('carrega o FullCalendar 6.1.11 e o calendário do módulo',
      'fullcalendar@6.1.11/index.global.min.js' in html and 'rotina/rotina-calendario.js' in html)
    cfg = json_script(html, 'rt-config') or {}
    t('modo pessoa: não trava e (sem permissão) não cria',
      cfg.get('modo') == 'pessoa' and cfg.get('podeCriar') is False and cfg.get('podeTravar') is False, cfg)
    t('pessoa comum não vê as abas de gestão', '/rotina-gerencial/gestao/' not in html)
    travada_json = next(a for a in dados['atividades'] if a['bloqueada'])
    livre_json = next(a for a in dados['atividades'] if not a['bloqueada'])
    t('travada chega sem poder mover; livre chega podendo mover (mas não renomear)',
      travada_json['pode_mover'] is False and livre_json['pode_mover'] is True and livre_json['pode_editar'] is False)
    t('layout do dia no celular: abas presas, cabeçalho do dia, lista e a troca lista/grade',
      all(gancho in html for gancho in ('data-rt="dias-barra"', 'data-rt="dia-titulo"', 'data-rt="lista"',
                                        'data-rt-vista="lista"', 'data-rt-vista="grade"')))
    t('cartão "Agora" já vem com a área principal (a próxima fica ao lado na tela larga)',
      'class="rt-agora-principal"' in html and 'rt-card-hoje' in html and 'rt-legenda-itens' in html)
    t('o topo mostra que os avisos estão ligados (5 min antes e no início)',
      'rt-hero-selo' in html and 'Avisos ligados' in html and '5 min antes e no início' in html)
    t('quem só vê a própria rotina não ganha a barra de abas (uma aba sozinha é ruído)',
      'class="rt-abas"' not in html and 'rt-hero-sem-abas' in html)
    html_admin_minha = c_admin.get('/rotina-gerencial/').content.decode()
    t('a gestão continua com as abas Minha rotina / Pessoas / Modelos',
      'class="rt-abas"' in html_admin_minha and 'rt-hero-sem-abas' not in html_admin_minha)
    with open(os.path.join(settings.BASE_DIR, 'static', 'rotina', 'rotina-calendario.js'), encoding='utf-8') as arquivo:
        calendario_js = arquivo.read()
    t('o calendário decide celular pelo matchMedia (o mesmo corte do CSS) e lembra lista/grade',
      "window.matchMedia('(max-width: '" in calendario_js and "'rotina-vista-dia'" in calendario_js
      and 'touchend' in calendario_js)
    js_valido(calendario_js, 'static/rotina/rotina-calendario.js')

    destacada = rotina_gerente.atividades.get(dia_semana=2, inicio='12:00')
    cfg = json_script(c_gerente.get('/rotina-gerencial/', {'dia': 2, 'atividade': destacada.id}).content.decode(),
                      'rt-config') or {}
    t('o link do aviso chega com a atividade em destaque',
      cfg.get('destaque') == {'atividade': destacada.id, 'dia': 2}, cfg.get('destaque'))
    alheia = rotina_criadora.atividades.first()
    cfg = json_script(c_gerente.get('/rotina-gerencial/', {'atividade': alheia.id}).content.decode(), 'rt-config') or {}
    t('atividade de outra pessoa na URL não vira destaque', (cfg.get('destaque') or {}).get('atividade') is None)

    r = cliente(ninguem).get('/rotina-gerencial/')
    html = r.content.decode()
    t('sem rotina: abre (200) com o aviso amigável e sem calendário',
      r.status_code == 200 and 'Você ainda não tem uma rotina gerencial' in html and 'id="rt-calendario"' not in html)
    t('sem rotina e sem ser SUPERADMIN: nenhum link de gestão', '/rotina-gerencial/gestao/' not in html)
    html = cliente(pausada).get('/rotina-gerencial/').content.decode()
    t('rotina desativada: aviso de pausa, sem calendário',
      'Sua rotina gerencial está pausada' in html and 'id="rt-calendario"' not in html)
    html = cliente(vazia).get('/rotina-gerencial/').content.decode()
    t('rotina ativa sem atividades (e sem poder criar): aviso de rotina vazia', 'Sua rotina ainda está vazia' in html)
    r = c_admin.get('/rotina-gerencial/')
    html = r.content.decode()
    t('SUPERADMIN sem rotina: aviso e link para gerenciar',
      r.status_code == 200 and 'Você ainda não tem uma rotina gerencial' in html
      and 'href="/rotina-gerencial/gestao/"' in html)
    t('e, em Minha rotina, o botão para editar a rotina de qualquer pessoa (seletor com busca)',
      'data-rt-abrir="rt-modal-pessoa"' in html and 'id="rt-modal-pessoa"' in html
      and 'data-rt-filtro=".rt-escolha"' in html
      and f'href="/rotina-gerencial/gestao/{gerente.id}/"' in html and f'href="/rotina-gerencial/gestao/{ninguem.id}/"' in html)
    escolhas = re.findall(r'<a href="/rotina-gerencial/gestao/(\d+)/" class="rt-escolha"', html)
    t('no seletor vêm todas as pessoas ativas, quem já tem rotina primeiro',
      len(escolhas) == User.objects.filter(is_active=True).count()
      and escolhas.index(str(gerente.id)) < escolhas.index(str(ninguem.id))
      and 'sem rotina' in html and '64 atividades' in html, (len(escolhas),))
    t('pessoa comum não ganha o seletor', 'rt-modal-pessoa' not in c_gerente.get('/rotina-gerencial/').content.decode())
    r = Client().get('/rotina-gerencial/')
    t('sem login: vai para o login', r.status_code == 302 and '/login' in r.get('Location', ''),
      (r.status_code, r.get('Location')))

    print('\n== TELAS DE GESTÃO: SÓ SUPERADMIN ==')
    telas = ['/rotina-gerencial/gestao/', f'/rotina-gerencial/gestao/{gerente.id}/',
             '/rotina-gerencial/modelos/', f'/rotina-gerencial/modelos/{modelo.id}/']
    for url in telas:
        r = c_gerente.get(url)
        t(f'pessoa comum em {url}: volta para a própria rotina',
          r.status_code == 302 and r.get('Location') == '/rotina-gerencial/', (r.status_code, r.get('Location')))
    for url in telas:
        r = c_admin.get(url)
        t(f'SUPERADMIN (hierarquia) abre {url}', r.status_code == 200, r.status_code)
    r = cliente(superusuario).get('/rotina-gerencial/gestao/')
    t('superusuário do Django também abre a gestão', r.status_code == 200, r.status_code)
    html = c_admin.get('/rotina-gerencial/gestao/').content.decode()

    def lista_de_pessoas(pagina):
        achado = re.search(r'<ul class="rt-pessoas">(.*?)</ul>', pagina, re.S)
        return achado.group(1) if achado else ''

    lista = lista_de_pessoas(html)
    t('a lista traz quem tem rotina, com a contagem de atividades',
      f'href="/rotina-gerencial/gestao/{gerente.id}/"' in lista and '64 atividades' in lista)
    t('cada pessoa tem o botão "Editar rotina" (com texto) e o nome também abre a semana dela',
      lista.count('<span>Editar rotina</span>') == RotinaGerencial.objects.count()
      and f'<a href="/rotina-gerencial/gestao/{gerente.id}/" class="rt-pessoa-nome rt-pessoa-link"' in lista)
    t('a tela Pessoas também tem o seletor para editar a rotina de qualquer pessoa',
      'data-rt-abrir="rt-modal-pessoa"' in html and f'href="/rotina-gerencial/gestao/{ninguem.id}/" class="rt-escolha"' in html)
    t('e o seletor da casa para adicionar pessoas (busca + caixas)',
      'data-rt-filtro=".rt-candidato"' in html and f'name="usuarios" value="{ninguem.id}"' in html)
    lista = lista_de_pessoas(c_admin.get('/rotina-gerencial/gestao/', {'q': 'zzrotina criadora'}).content.decode())
    t('a busca filtra a lista de pessoas',
      f'href="/rotina-gerencial/gestao/{criadora.id}/"' in lista and f'href="/rotina-gerencial/gestao/{gerente.id}/"' not in lista)
    lista = lista_de_pessoas(c_admin.get('/rotina-gerencial/gestao/', {'q': 'ZZ Loja Teste Rotina'}).content.decode())
    t('a busca por setor também', f'href="/rotina-gerencial/gestao/{gerente.id}/"' in lista)
    html = c_admin.get(f'/rotina-gerencial/gestao/{gerente.id}/').content.decode()
    cfg = json_script(html, 'rt-config') or {}
    t('editor da pessoa: calendário que cria e trava, apontando para a pessoa certa',
      cfg.get('modo') == 'gestao' and cfg.get('podeCriar') is True and cfg.get('podeTravar') is True
      and cfg.get('usuario') == gerente.id, cfg)
    t('no editor dá para trocar de pessoa sem voltar para a lista',
      'Trocar de pessoa' in html and 'id="rt-modal-pessoa"' in html and f'href="/rotina-gerencial/gestao/{criadora.id}/" class="rt-escolha"' in html)
    t('o editor de modelo não traz o seletor de pessoa', 'rt-modal-pessoa' not in c_admin.get(f'/rotina-gerencial/modelos/{modelo.id}/').content.decode())
    r = c_admin.get(f'/rotina-gerencial/gestao/{ninguem.id}/')
    html = r.content.decode()
    t('quem não tem rotina: a tela oferece criar ali mesmo (com modelo ou vazia), sem calendário',
      r.status_code == 200 and 'ainda não tem rotina gerencial' in html and 'action="/rotina-gerencial/gestao/adicionar/"' in html
      and f'name="usuarios" value="{ninguem.id}"' in html and f'<option value="{modelo.id}"' in html
      and 'Criar a rotina e editar a semana' in html and 'id="rt-calendario"' not in html, r.status_code)
    with transaction.atomic():
        r = c_admin.post('/rotina-gerencial/gestao/adicionar/', {'usuarios': [ninguem.id], 'modelo': modelo.id,
                                                                  'avisar_whatsapp': 'on'})
        criada = RotinaGerencial.objects.filter(user=ninguem).first()
        t('criar por ela já abre a semana da pessoa, com as atividades do modelo',
          r.status_code == 302 and r.get('Location') == f'/rotina-gerencial/gestao/{ninguem.id}/'
          and criada is not None and criada.atividades.count() == modelo.atividades.count() > 0,
          (r.status_code, r.get('Location')))
        transaction.set_rollback(True)
    t('(desfeito: a pessoa "ninguém" continua sem rotina para o resto do teste)',
      not RotinaGerencial.objects.filter(user=ninguem).exists())
    inativo = novo('inativo', is_active=False)
    r = c_admin.get(f'/rotina-gerencial/gestao/{inativo.id}/')
    t('pessoa inativa sem rotina volta para a lista', r.status_code == 302 and r.get('Location') == '/rotina-gerencial/gestao/',
      (r.status_code, r.get('Location')))
    inativo.delete()
    html = c_admin.get('/rotina-gerencial/modelos/').content.decode()
    t('lista de modelos com a miniatura da semana (números com ponto no CSS)',
      NOME_PADRAO in html and 'rt-mini-bloco' in html and not re.search(r'top: \d+,\d+%', html))
    antes = RotinaGerencial.objects.count()
    r = c_gerente.post('/rotina-gerencial/gestao/adicionar/', {'usuarios': [ninguem.id], 'modelo': modelo.id})
    t('pessoa comum não adiciona ninguém', r.status_code == 302 and RotinaGerencial.objects.count() == antes)
    c_gerente.post(f'/rotina-gerencial/gestao/{criadora.id}/acao/', {'acao': 'limpar'})
    t('nem limpa a rotina de outra pessoa', rotina_criadora.atividades.count() == 64)

    print('\n== API: LISTAR ==')
    r = c_gerente.get('/rotina-gerencial/api/rotina/')
    t('a pessoa lista a própria semana', r.status_code == 200 and js(r).get('ok') is True
      and len(js(r).get('atividades', [])) == 64, r.status_code)
    r = c_gerente.get('/rotina-gerencial/api/rotina/', {'usuario': criadora.id})
    t('não lista a de outra pessoa (403, no formato de erro)',
      r.status_code == 403 and js(r).get('ok') is False and bool(js(r).get('erro')), (r.status_code, js(r)))
    r = c_admin.get('/rotina-gerencial/api/rotina/', {'usuario': gerente.id})
    t('SUPERADMIN lista a de qualquer pessoa, com tudo liberado',
      r.status_code == 200 and len(js(r).get('atividades', [])) == 64
      and all(a['pode_mover'] and a['pode_editar'] for a in js(r)['atividades']))
    t('rotina desativada não lista (403)', cliente(pausada).get('/rotina-gerencial/api/rotina/').status_code == 403)
    r = Client().get('/rotina-gerencial/api/rotina/')
    t('sem login: 401 em JSON', r.status_code == 401 and js(r).get('ok') is False and bool(js(r).get('erro')))

    print('\n== API: TRAVAS DA PESSOA ==')
    travada = rotina_gerente.atividades.get(dia_semana=0, inicio='12:00')      # Parcial Gerentes (travada)
    livre = rotina_gerente.atividades.get(dia_semana=0, inicio='09:00')        # Atuação no salão (livre)
    r = post_json(c_gerente, url_atividade(travada), {'dia_semana': 1, 'inicio': '13:00', 'fim': '13:30'})
    travada.refresh_from_db()
    t('não move atividade travada (403) e nada muda',
      r.status_code == 403 and js(r).get('ok') is False and (travada.dia_semana, hm(travada.inicio)) == (0, '12:00'),
      (r.status_code, js(r)))
    t('nem estica a travada (403)', post_json(c_gerente, url_atividade(travada), {'fim': '12:45'}).status_code == 403)
    r = post_json(c_gerente, url_atividade(livre), {'dia_semana': 5, 'inicio': '14:00', 'fim': '16:30'})
    livre.refresh_from_db()
    t('move a livre para outro dia e horário',
      r.status_code == 200 and js(r).get('ok') is True
      and (livre.dia_semana, hm(livre.inicio), hm(livre.fim)) == (5, '14:00', '16:30'), (r.status_code, js(r)))
    t('a resposta já traz a atividade atualizada',
      (js(r).get('atividade') or {}).get('dia_semana') == 5 and js(r)['atividade']['inicio'] == '14:00')
    rotina_gerente.refresh_from_db()
    t('a rotina guarda quem mexeu por último', rotina_gerente.atualizado_por_id == gerente.id)
    r = post_json(c_gerente, url_atividade(livre), {'titulo': 'Outro nome'})
    t('não renomeia atividade da gestão (403)',
      r.status_code == 403 and AtividadeRotina.objects.get(pk=livre.pk).titulo == 'Atuação no salão')
    r = post_json(c_gerente, url_atividade(livre), {'bloqueada': True})
    t('não trava atividade (403)', r.status_code == 403 and not AtividadeRotina.objects.get(pk=livre.pk).bloqueada)
    r = post_json(c_gerente, '/rotina-gerencial/api/rotina/atividades/',
                  {'titulo': 'Minha', 'dia_semana': 2, 'inicio': '19:00', 'fim': '19:30'})
    t('sem "pode criar": não cria (403)',
      r.status_code == 403 and not rotina_gerente.atividades.filter(titulo='Minha').exists())
    r = c_gerente.post(url_atividade(livre, excluir=True))
    t('não exclui atividade da gestão (403)', r.status_code == 403 and AtividadeRotina.objects.filter(pk=livre.pk).exists())
    alheia_livre = rotina_criadora.atividades.filter(bloqueada=False).first()
    t('atividade de outra pessoa: 404',
      post_json(c_gerente, url_atividade(alheia_livre), {'inicio': '09:05'}).status_code == 404)
    livre_pausada = rotina_pausada.atividades.filter(bloqueada=False).first()
    t('rotina desativada: não move nem a livre (403)',
      post_json(cliente(pausada), url_atividade(livre_pausada), {'inicio': '08:25'}).status_code == 403)
    r = c_gerente.get(url_atividade(livre))
    t('método errado: 405 em JSON', r.status_code == 405 and js(r).get('ok') is False)
    r = c_gerente.post(url_atividade(livre), data='isto não é json', content_type='application/json')
    t('JSON inválido: 400', r.status_code == 400 and js(r) == {'ok': False, 'erro': 'JSON inválido.'}, js(r))

    print('\n== API: QUEM PODE CRIAR ==')
    c_criadora = cliente(criadora)
    r = post_json(c_criadora, '/rotina-gerencial/api/rotina/atividades/', {
        'titulo': 'Visita à loja vizinha', 'descricao': 'Roteiro em https://exemplo-teste.local/roteiro',
        'categoria': 'EQUIPE', 'repetir_em': [1, 3], 'inicio': '19:00', 'fim': '19:45'})
    proprias = list(rotina_criadora.atividades.filter(titulo='Visita à loja vizinha').order_by('dia_semana'))
    t('com "pode criar": cria (201) em cada dia pedido',
      r.status_code == 201 and len(js(r).get('atividades', [])) == 2 and [a.dia_semana for a in proprias] == [1, 3],
      (r.status_code, js(r)))
    t('as criadas são da pessoa, livres, e ela pode editar e excluir',
      all(a.criada_pela_pessoa and not a.bloqueada for a in proprias)
      and all(x['pode_editar'] and x['pode_excluir'] for x in js(r).get('atividades', [])))
    r = post_json(c_criadora, '/rotina-gerencial/api/rotina/atividades/',
                  {'titulo': 'Travada?', 'dia_semana': 1, 'inicio': '20:00', 'fim': '20:30', 'bloqueada': True})
    t('mas não cria atividade travada (403)', r.status_code == 403)
    r = post_json(c_criadora, url_atividade(proprias[0]), {'titulo': 'Visita à loja parceira', 'categoria': 'COMPLEMENTAR'})
    t('renomeia a própria', r.status_code == 200
      and AtividadeRotina.objects.get(pk=proprias[0].pk).titulo == 'Visita à loja parceira', (r.status_code, js(r)))
    r = c_criadora.post(url_atividade(proprias[0], excluir=True))
    t('exclui a própria', r.status_code == 200 and js(r) == {'ok': True}
      and not AtividadeRotina.objects.filter(pk=proprias[0].pk).exists())
    da_gestao = rotina_criadora.atividades.filter(bloqueada=False, criada_pela_pessoa=False).first()
    t('mesmo podendo criar, não renomeia as da gestão (403)',
      post_json(c_criadora, url_atividade(da_gestao), {'titulo': 'Não pode'}).status_code == 403)
    rotina_criadora.pode_criar = False
    rotina_criadora.save()
    t('sem o "pode criar", não exclui nem as próprias (403)',
      c_criadora.post(url_atividade(proprias[1], excluir=True)).status_code == 403)
    rotina_criadora.pode_criar = True
    rotina_criadora.save()

    print('\n== API: GESTÃO ==')
    r = post_json(c_admin, '/rotina-gerencial/api/rotina/atividades/', {
        'usuario': gerente.id, 'titulo': 'Reunião com o supervisor', 'categoria': 'EQUIPE', 'bloqueada': True,
        'repetir_em': [0, 2, 4], 'inicio': '07:30', 'fim': '08:00'})
    criadas = AtividadeRotina.objects.filter(rotina=rotina_gerente, titulo='Reunião com o supervisor')
    t('SUPERADMIN cria na rotina da pessoa, em seg/qua/sex, já travada',
      r.status_code == 201 and sorted(criadas.values_list('dia_semana', flat=True)) == [0, 2, 4]
      and all(a.bloqueada and not a.criada_pela_pessoa for a in criadas), (r.status_code, js(r)))
    alvo = rotina_gerente.atividades.get(dia_semana=1, inicio='09:00')
    r = post_json(c_admin, url_atividade(alvo), {'bloqueada': True})
    t('SUPERADMIN trava uma atividade livre', r.status_code == 200 and AtividadeRotina.objects.get(pk=alvo.pk).bloqueada)
    t('e aí a pessoa já não move (403)', post_json(c_gerente, url_atividade(alvo), {'inicio': '09:30'}).status_code == 403)
    r = post_json(c_admin, url_atividade(travada), {'inicio': '12:10', 'fim': '12:40'})
    t('SUPERADMIN muda o horário de atividade travada',
      r.status_code == 200 and hm(AtividadeRotina.objects.get(pk=travada.pk).inicio) == '12:10')
    r = c_admin.post(url_atividade(alvo, excluir=True))
    t('SUPERADMIN exclui', r.status_code == 200 and not AtividadeRotina.objects.filter(pk=alvo.pk).exists())
    r = post_json(c_admin, '/rotina-gerencial/api/rotina/atividades/',
                  {'usuario': ninguem.id, 'titulo': 'X', 'dia_semana': 0, 'inicio': '09:00', 'fim': '10:00'})
    t('criar para quem não tem rotina: 404', r.status_code == 404)
    r = post_json(c_admin, f'/rotina-gerencial/api/gestao/{criadora.id}/opcoes/', {'pode_criar': False})
    t('interruptor "pode criar" salva na hora',
      r.status_code == 200 and RotinaGerencial.objects.get(pk=rotina_criadora.pk).pode_criar is False)
    post_json(c_admin, f'/rotina-gerencial/api/gestao/{criadora.id}/opcoes/', {'pode_criar': True})
    r = post_json(c_gerente, f'/rotina-gerencial/api/gestao/{gerente.id}/opcoes/', {'ativa': False})
    t('pessoa comum não mexe nos interruptores, nem nos da própria rotina (403)',
      r.status_code == 403 and RotinaGerencial.objects.get(pk=rotina_gerente.pk).ativa)
    t('interruptor com valor que não é verdadeiro/falso: 400',
      post_json(c_admin, f'/rotina-gerencial/api/gestao/{criadora.id}/opcoes/', {'ativa': 'sim'}).status_code == 400)

    print('\n== VALIDAÇÃO ==')
    base_valida = {'usuario': gerente.id, 'titulo': 'Teste de validação', 'dia_semana': 3, 'inicio': '10:00', 'fim': '11:00'}
    casos = [
        ('fim igual ao início', {'inicio': '10:00', 'fim': '10:00'}, 'O fim precisa ser depois do início.'),
        ('fim antes do início', {'inicio': '11:00', 'fim': '10:00'}, 'O fim precisa ser depois do início.'),
        ('antes das 05:00', {'inicio': '04:30', 'fim': '06:00'}, 'Os horários precisam ficar entre 05:00 e 23:00.'),
        ('depois das 23:00', {'inicio': '22:30', 'fim': '23:30'}, 'Os horários precisam ficar entre 05:00 e 23:00.'),
        ('domingo', {'dia_semana': 6}, 'Escolha um dia de segunda a sábado.'),
        ('hora que não existe', {'inicio': '25:00'}, 'Horário de início inválido: use o formato HH:MM.'),
        ('título em branco', {'titulo': '   '}, 'Informe o título da atividade.'),
        ('categoria que não existe', {'categoria': 'URGENTE'}, 'Categoria inválida.'),
    ]
    for rotulo, mudanca, mensagem in casos:
        r = post_json(c_admin, '/rotina-gerencial/api/rotina/atividades/', {**base_valida, **mudanca})
        t(f'recusa {rotulo} (400)', r.status_code == 400 and js(r) == {'ok': False, 'erro': mensagem},
          (r.status_code, js(r)))
    t('nada foi criado nos casos recusados', not AtividadeRotina.objects.filter(titulo='Teste de validação').exists())
    r = post_json(c_gerente, url_atividade(livre), {'fim': '13:00'})         # a livre está em sáb 14:00–16:30
    t('mudar só o fim para antes do início também é recusado (400)',
      r.status_code == 400 and js(r).get('erro') == 'O fim precisa ser depois do início.', (r.status_code, js(r)))
    try:
        AtividadeRotina(rotina=rotina_gerente, dia_semana=0, inicio=time(10), fim=time(9), titulo='x').full_clean()
        recusou = False
    except ValidationError:
        recusou = True
    t('o modelo recusa fim antes do início (full_clean)', recusou)
    try:
        with transaction.atomic():
            AtividadeRotina.objects.create(rotina=rotina_gerente, dia_semana=0, inicio=time(10), fim=time(9), titulo='x')
        barrou = False
    except IntegrityError:
        barrou = True
    t('e o banco também (check constraint)', barrou)

    print('\n== API: MODELOS ==')
    url_modelo = f'/rotina-gerencial/api/modelos/{modelo.id}/atividades/'
    t('pessoa comum não lê modelo (403)', c_gerente.get(url_modelo).status_code == 403)
    r = c_admin.get(url_modelo)
    t('SUPERADMIN lê o modelo', r.status_code == 200 and len(js(r).get('atividades', [])) == 64)
    r = post_json(c_admin, url_modelo, {'titulo': 'Ligação para clientes', 'categoria': 'EQUIPE',
                                        'repetir_em': [5], 'inicio': '13:30', 'fim': '14:00'})
    nova = AtividadeModelo.objects.filter(modelo=modelo, titulo='Ligação para clientes').first()
    t('SUPERADMIN cria no modelo', r.status_code == 201 and nova is not None and nova.dia_semana == 5, (r.status_code, js(r)))
    r = post_json(c_admin, f'/rotina-gerencial/api/modelos/atividades/{nova.id}/',
                  {'inicio': '14:00', 'fim': '14:30', 'bloqueada': True})
    nova.refresh_from_db()
    t('SUPERADMIN edita a atividade do modelo', r.status_code == 200 and hm(nova.inicio) == '14:00' and nova.bloqueada)
    t('pessoa comum não edita modelo (403)',
      post_json(c_gerente, f'/rotina-gerencial/api/modelos/atividades/{nova.id}/', {'inicio': '15:00'}).status_code == 403)
    r = c_admin.post(f'/rotina-gerencial/api/modelos/atividades/{nova.id}/excluir/')
    t('SUPERADMIN exclui a atividade do modelo',
      r.status_code == 200 and not AtividadeModelo.objects.filter(pk=nova.pk).exists() and modelo.atividades.count() == 64)

    print('\n== CSRF ==')
    c_csrf = cliente(gerente, enforce_csrf_checks=True)
    r = post_json(c_csrf, url_atividade(livre), {'inicio': '14:05'})
    t('POST sem token CSRF é barrado (403) e nada muda',
      r.status_code == 403 and hm(AtividadeRotina.objects.get(pk=livre.pk).inicio) == '14:00', r.status_code)
    c_csrf.get('/rotina-gerencial/')
    token = c_csrf.cookies['csrftoken'].value if 'csrftoken' in c_csrf.cookies else ''
    r = post_json(c_csrf, url_atividade(livre), {'inicio': '14:05'}, HTTP_X_CSRFTOKEN=token)
    t('com o token do cookie (como o calendário manda), passa',
      r.status_code == 200 and hm(AtividadeRotina.objects.get(pk=livre.pk).inicio) == '14:05', (r.status_code, js(r)))

    print('\n== NOTIFICADOR: api/hoje/ ==')
    r = c_gerente.get('/rotina-gerencial/api/hoje/')
    d = js(r)
    t('responde (200) com ok', r.status_code == 200 and d.get('ok') is True, r.status_code)
    t('com a data e o dia de hoje (quarta, 16/09)', d.get('data') == '2026-09-16' and d.get('dia_semana') == 2)
    t('com a hora do servidor em ISO e em milissegundos',
      str(d.get('agora', '')).startswith('2026-09-16T12:05:00')
      and d.get('agora_ts') == int(QUARTA_MEIO_DIA.timestamp() * 1000), (d.get('agora'), d.get('agora_ts')))
    de_hoje = d.get('atividades', [])
    t('só as de quarta da própria rotina, em ordem de horário',
      len(de_hoje) == rotina_gerente.atividades.filter(dia_semana=2).count()
      and [a['inicio'] for a in de_hoje] == sorted(a['inicio'] for a in de_hoje), len(de_hoje))
    parcial = next((a for a in de_hoje if a['inicio'] == '12:00'), None)
    t('cada uma com o instante de início, o link para a rotina e se já foi avisada',
      parcial is not None and parcial['titulo'] == 'Parcial Gerentes'
      and parcial['inicio_ts'] == int(timezone.make_aware(datetime(2026, 9, 16, 12, 0), FUSO).timestamp() * 1000)
      and parcial['url'] == f"/rotina-gerencial/?dia=2&atividade={parcial['id']}" and parcial['avisada'] is False, parcial)
    relogio.return_value = DOMINGO
    t('domingo: nada para avisar', js(c_gerente.get('/rotina-gerencial/api/hoje/')).get('atividades') == [])
    relogio.return_value = QUARTA_MEIO_DIA
    d = js(cliente(ninguem).get('/rotina-gerencial/api/hoje/'))
    t('quem não tem rotina recebe lista vazia, sem erro',
      d.get('ok') is True and d.get('atividades') == [] and d.get('ativa') is False, d)
    t('rotina desativada também não avisa', js(cliente(pausada).get('/rotina-gerencial/api/hoje/')).get('atividades') == [])
    t('sem login: 401', Client().get('/rotina-gerencial/api/hoje/').status_code == 401)

    print('\n== AVISO DE INÍCIO: SINO UMA VEZ, SEM PUSH ==')
    atividade_aviso = rotina_gerente.atividades.get(dia_semana=2, inicio='12:00')
    url_aviso = f'/rotina-gerencial/api/avisos/{atividade_aviso.id}/'
    sino_antes = UserNotification.objects.filter(user=gerente).count()
    r1 = c_gerente.post(url_aviso)
    r2 = c_gerente.post(url_aviso)
    t('primeira vez: novo', r1.status_code == 200 and js(r1).get('ok') is True and js(r1).get('novo') is True,
      (r1.status_code, js(r1)))
    t('segunda vez: não é novo', r2.status_code == 200 and js(r2).get('novo') is False, js(r2))
    t('outra aba ou outro aparelho também não duplica', js(cliente(gerente).post(url_aviso)).get('novo') is False)
    t('um único registro de aviso da atividade no dia',
      AvisoRotina.objects.filter(atividade=atividade_aviso, data='2026-09-16').count() == 1)
    do_sino = [n for n in UserNotification.objects.filter(user=gerente).select_related('notification')
               if (n.notification.extra_data or {}).get('origem') == 'rotina_gerencial']
    t('exatamente uma notificação no sino',
      UserNotification.objects.filter(user=gerente).count() == sino_antes + 1 and len(do_sino) == 1, len(do_sino))
    notificacao = do_sino[0].notification if do_sino else None
    t('com o link direto para a atividade na rotina',
      notificacao is not None and notificacao.action_url == f'/rotina-gerencial/?dia=2&atividade={atividade_aviso.id}',
      notificacao and notificacao.action_url)
    t('título e texto com a atividade e o horário',
      notificacao is not None and notificacao.title == 'Agora: Parcial Gerentes' and '12:00–12:30' in notificacao.message)
    t('não lida e só para a pessoa',
      bool(do_sino) and not do_sino[0].is_read and notificacao.user_notifications.count() == 1)
    recentes = js(c_gerente.get('/notifications/api/recent/')).get('notifications', [])
    t('aparece na API do sino do cabeçalho, com o link',
      notificacao is not None and any(n.get('action_url') == notificacao.action_url for n in recentes))
    atividade_hoje = next((a for a in js(c_gerente.get('/rotina-gerencial/api/hoje/')).get('atividades', [])
                           if a['id'] == atividade_aviso.id), {})
    t('o api/hoje/ passa a marcar a atividade como avisada', atividade_hoje.get('avisada') is True)
    futura = rotina_gerente.atividades.get(dia_semana=2, inicio='15:00')
    r = c_gerente.post(f'/rotina-gerencial/api/avisos/{futura.id}/')
    t('atividade que ainda não começou: 400 e nenhum aviso',
      r.status_code == 400 and not AvisoRotina.objects.filter(atividade=futura).exists(), (r.status_code, js(r)))
    de_terca = rotina_gerente.atividades.get(dia_semana=1, inicio='12:00')
    r = c_gerente.post(f'/rotina-gerencial/api/avisos/{de_terca.id}/')
    t('atividade de outro dia: 400', r.status_code == 400 and js(r).get('erro') == 'Esta atividade não é de hoje.')
    de_outra = rotina_criadora.atividades.get(dia_semana=2, inicio='12:00')
    t('atividade de outra pessoa: 404', c_gerente.post(f'/rotina-gerencial/api/avisos/{de_outra.id}/').status_code == 404)
    t('GET no aviso: 405', c_gerente.get(url_aviso).status_code == 405)
    NotificationPreference.objects.create(user=criadora, in_app_enabled=False)
    sino_criadora = UserNotification.objects.filter(user=criadora).count()
    r = c_criadora.post(f'/rotina-gerencial/api/avisos/{de_outra.id}/')
    t('quem desligou as notificações do portal: aviso registrado, sino quieto',
      js(r).get('novo') is True and UserNotification.objects.filter(user=criadora).count() == sino_criadora)
    chamados = {nome: duble.call_count for nome, duble in dubles.items() if duble.call_count}
    t('nenhuma função de envio (web push/OneSignal) foi chamada', not chamados, chamados)

    print('\n== LEMBRETE ANTES DO INÍCIO: SINO UMA VEZ, SEM PUSH ==')
    # `futura` é a Parcial Gerentes de quarta, 15:00–15:30; ninguém avisou nada dela ainda.
    url_lembrete = f'/rotina-gerencial/api/lembretes/{futura.id}/'
    relogio.return_value = quando(16, 14, 56)
    sino_antes = UserNotification.objects.filter(user=gerente).count()
    ultimo_do_sino = UserNotification.objects.filter(user=gerente).order_by('-id').values_list('id', flat=True).first() or 0
    r1 = c_gerente.post(url_lembrete)
    r2 = c_gerente.post(url_lembrete)
    t('4 min antes: lembrete registrado (novo)', r1.status_code == 200 and js(r1).get('novo') is True,
      (r1.status_code, js(r1)))
    t('de novo, e de outra aba: não é novo',
      js(r2).get('novo') is False and js(cliente(gerente).post(url_lembrete)).get('novo') is False, js(r2))
    t('um registro de lembrete e nenhum de início para a atividade no dia',
      AvisoRotina.objects.filter(atividade=futura, data='2026-09-16', tipo=TipoAviso.LEMBRETE).count() == 1
      and not AvisoRotina.objects.filter(atividade=futura, data='2026-09-16', tipo=TipoAviso.INICIO).exists())
    novas = list(UserNotification.objects.filter(user=gerente, id__gt=ultimo_do_sino).select_related('notification'))
    lembrete_sino = novas[0].notification if len(novas) == 1 else None
    t('exatamente uma notificação nova no sino', len(novas) == 1, len(novas))
    t('"Em 4 min: Parcial Gerentes", com o horário, o link da atividade e marcada como lembrete',
      lembrete_sino is not None and lembrete_sino.title == 'Em 4 min: Parcial Gerentes'
      and 'Começa às 15:00 e vai até 15:30' in lembrete_sino.message
      and lembrete_sino.action_url == f'/rotina-gerencial/?dia=2&atividade={futura.id}'
      and (lembrete_sino.extra_data or {}).get('tipo') == 'lembrete',
      lembrete_sino and (lembrete_sino.title, lembrete_sino.message, lembrete_sino.extra_data))
    d = js(c_gerente.get('/rotina-gerencial/api/hoje/'))
    item = next((a for a in d.get('atividades', []) if a['id'] == futura.id), {})
    t('api/hoje/ diz quantos minutos antes e marca lembrada, mas não avisada',
      d.get('lembrete_minutos') == servicos.MINUTOS_LEMBRETE == 5
      and item.get('lembrada') is True and item.get('avisada') is False, (d.get('lembrete_minutos'), item))
    relogio.return_value = quando(16, 15, 0, 20)
    r = c_gerente.post(f'/rotina-gerencial/api/avisos/{futura.id}/')
    t('na hora, o aviso de início entra também (novo) — um de cada tipo',
      js(r).get('novo') is True
      and AvisoRotina.objects.filter(atividade=futura, data='2026-09-16').count() == 2
      and UserNotification.objects.filter(user=gerente).count() == sino_antes + 2, (r.status_code, js(r)))
    relogio.return_value = quando(16, 14, 50)
    r = c_gerente.post(url_lembrete)
    t('cedo demais (10 min antes): 400', r.status_code == 400
      and js(r).get('erro') == 'Ainda é cedo para o lembrete desta atividade.', (r.status_code, js(r)))
    relogio.return_value = quando(16, 15, 2)
    r = c_gerente.post(url_lembrete)
    t('depois de começar: 400', r.status_code == 400 and js(r).get('erro') == 'Esta atividade já começou.',
      (r.status_code, js(r)))

    def aceita_lembrete(momento):
        try:
            servicos.conferir_hora_do_aviso(futura, momento, TipoAviso.LEMBRETE)
            return True
        except servicos.ErroValidacao:
            return False

    t('janela do lembrete: de 7 min antes (5 + 2 de relógio adiantado) até 1 min depois do início',
      [aceita_lembrete(quando(16, 14, 52, 59)), aceita_lembrete(quando(16, 14, 53)),
       aceita_lembrete(quando(16, 15, 1)), aceita_lembrete(quando(16, 15, 1, 1))] == [False, True, True, False])
    t('texto do sino conta os minutos que faltam de verdade (14:55:30 → 5 min)',
      servicos.textos_do_sino(futura, TipoAviso.LEMBRETE, quando(16, 14, 55, 30))[0] == 'Em 5 min: Parcial Gerentes')
    relogio.return_value = quando(16, 11, 57)
    r = c_gerente.post(f'/rotina-gerencial/api/lembretes/{de_terca.id}/')
    t('lembrete de atividade de outro dia: 400', r.status_code == 400 and js(r).get('erro') == 'Esta atividade não é de hoje.')
    t('lembrete de atividade de outra pessoa: 404',
      c_gerente.post(f'/rotina-gerencial/api/lembretes/{de_outra.id}/').status_code == 404)
    t('GET no lembrete: 405; sem login: 401',
      c_gerente.get(url_lembrete).status_code == 405 and Client().post(url_lembrete).status_code == 401)
    de_pausada = rotina_pausada.atividades.get(dia_semana=2, inicio='12:00')
    t('rotina desativada não recebe lembrete (403)',
      cliente(pausada).post(f'/rotina-gerencial/api/lembretes/{de_pausada.id}/').status_code == 403)
    try:
        with transaction.atomic():
            AvisoRotina.objects.create(user=gerente, atividade=futura, data='2026-09-16', tipo=TipoAviso.LEMBRETE)
        barrou = False
    except IntegrityError:
        barrou = True
    t('o banco barra dois lembretes da mesma atividade no mesmo dia (constraint)', barrou)
    relogio.return_value = QUARTA_MEIO_DIA
    chamados = {nome: duble.call_count for nome, duble in dubles.items() if duble.call_count}
    t('lembrete também não chama nenhuma função de envio', not chamados, chamados)

    print('\n== AÇÕES DA GESTÃO ==')
    r = c_admin.post('/rotina-gerencial/gestao/adicionar/',
                     {'usuarios': [adicionada.id, vazia.id], 'modelo': modelo.id, 'pode_criar': 'on'})
    nova_rotina = RotinaGerencial.objects.filter(user=adicionada).first()
    t('adicionar pessoas com o modelo: nasce a rotina com as 64 atividades',
      r.status_code == 302 and nova_rotina is not None and nova_rotina.atividades.count() == 64
      and nova_rotina.pode_criar and nova_rotina.modelo_origem_id == modelo.id)
    t('quem já tinha rotina fica como estava (sem "substituir")', rotina_vazia.atividades.count() == 0)
    r = c_admin.post('/rotina-gerencial/gestao/adicionar/', {'usuarios': [vazia.id], 'modelo': modelo.id, 'substituir': 'on'})
    t('com "substituir", quem já tinha recebe o modelo (e uma pessoa só abre o editor dela)',
      rotina_vazia.atividades.count() == 64 and r.get('Location') == f'/rotina-gerencial/gestao/{vazia.id}/',
      r.get('Location'))
    url_acao = f'/rotina-gerencial/gestao/{adicionada.id}/acao/'
    c_admin.post(url_acao, {'acao': 'limpar'})
    t('limpar tira todas as atividades', nova_rotina.atividades.count() == 0)
    c_admin.post(url_acao, {'acao': 'copiar', 'origem': criadora.id})
    t('copiar de outra pessoa traz a semana dela, sem nada "criado pela pessoa"',
      nova_rotina.atividades.count() == rotina_criadora.atividades.count()
      and not nova_rotina.atividades.filter(criada_pela_pessoa=True).exists())
    c_admin.post(url_acao, {'acao': 'aplicar_modelo', 'modelo': modelo.id})
    t('aplicar modelo substitui a semana', nova_rotina.atividades.count() == 64)
    c_admin.post(url_acao, {'acao': 'desativar'})
    t('desativar', RotinaGerencial.objects.get(pk=nova_rotina.pk).ativa is False)
    c_admin.post(url_acao, {'acao': 'ativar'})
    t('ativar de novo', RotinaGerencial.objects.get(pk=nova_rotina.pk).ativa is True)
    r = c_admin.post('/rotina-gerencial/modelos/novo/', {'nome': 'ZZ Modelo teste rotina', 'base': modelo.id})
    copia = ModeloRotina.objects.filter(nome='ZZ Modelo teste rotina').first()
    t('novo modelo a partir do padrão já vem com as 64 atividades',
      copia is not None and copia.atividades.count() == 64 and r.get('Location') == f'/rotina-gerencial/modelos/{copia.id}/')
    c_admin.post(f'/rotina-gerencial/modelos/{copia.id}/acao/', {'acao': 'excluir'})
    t('excluir modelo', not ModeloRotina.objects.filter(nome='ZZ Modelo teste rotina').exists())
    c_admin.post(url_acao, {'acao': 'remover'})
    t('remover a pessoa da rotina', not RotinaGerencial.objects.filter(user=adicionada).exists())

    print('\n== PARCIAL DO NOTIFICADOR ==')
    parcial_html = render_to_string('rotina/_notificador.html', {'csrf_token': 'token-de-teste'})
    t('renderiza com o objeto único window.RotinaNotificador', 'window.RotinaNotificador = {' in parcial_html)
    t('aponta para api/hoje/ e api/avisos/',
      "'/rotina-gerencial/api/hoje/'" in parcial_html and "'/rotina-gerencial/api/avisos/0/'" in parcial_html)
    t('lembra o aviso dado em localStorage, por atividade e dia',
      "'rotina-aviso:'" in parcial_html and 'localStorage' in parcial_html)
    t('cartão com "Agora · início–fim", "Abrir rotina" e fechar',
      "'Agora · '" in parcial_html and 'Abrir rotina' in parcial_html and 'Fechar aviso' in parcial_html)
    t('acerta o relógio pelo servidor e busca de novo (10 min e ao voltar para a aba)',
      'agora_ts' in parcial_html and 'visibilitychange' in parcial_html and '10 * 60 * 1000' in parcial_html)
    t('atualiza o contador do sino quando o aviso é novo', 'updateUnreadCount' in parcial_html)
    t('usa o token CSRF da página como reserva', "'token-de-teste'" in parcial_html)
    t('também avisa minutos antes: rota do lembrete, chave própria e contagem regressiva',
      "'/rotina-gerencial/api/lembretes/0/'" in parcial_html and "chave: 'lembrete:'" in parcial_html
      and 'lembrete_minutos' in parcial_html and "'Em ' + minutosAte(" in parcial_html
      and 'Lembrete da rotina' in parcial_html)
    t('o lembrete sai quando a atividade começa (o aviso de início toma o lugar)',
      'data-rtn-tipo="lembrete"' in parcial_html and "agora() >= a.inicio_ts" in parcial_html)
    t('nada de web push no parcial (só o sino do portal)',
      not re.search(r'serviceWorker|PushManager|OneSignal|pushManager', parcial_html))
    scripts = re.findall(r'<script>(.*?)</script>', parcial_html, flags=re.S)
    t('o JS fica todo dentro de uma função (nenhuma variável global solta)',
      len(scripts) == 1 and scripts[0].strip().startswith('(function () {') and scripts[0].strip().endswith('})();'))
    node = shutil.which('node')
    if node and scripts:
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as arquivo:
            arquivo.write(scripts[0])
        verificacao = subprocess.run([node, '--check', arquivo.name], capture_output=True, text=True, timeout=30)
        os.unlink(arquivo.name)
        t('o JS do parcial é válido (node --check)', verificacao.returncode == 0, verificacao.stderr[-400:])
    else:
        print('  (node não encontrado: sintaxe do JS não conferida)')

    print('\n== CONTEXT PROCESSOR ==')
    fabrica = RequestFactory()

    def bandeiras(user):
        pedido = fabrica.get('/')
        pedido.user = user
        return rotina_menu(pedido)

    sem_atividades = novo('sematividades')
    RotinaGerencial.objects.create(user=sem_atividades)
    t('anônimo: tudo desligado', bandeiras(AnonymousUser()) == {'rotina_liberada': False, 'rotina_admin': False})
    t('rotina ativa com atividades: menu liberado, sem gestão',
      bandeiras(gerente) == {'rotina_liberada': True, 'rotina_admin': False}, bandeiras(gerente))
    t('sem rotina: nada', bandeiras(ninguem) == {'rotina_liberada': False, 'rotina_admin': False})
    t('rotina desativada: o menu some', bandeiras(pausada)['rotina_liberada'] is False)
    t('rotina ativa sem nenhuma atividade: sem menu (nada a avisar)', bandeiras(sem_atividades)['rotina_liberada'] is False)
    t('SUPERADMIN sem rotina: só a gestão', bandeiras(superadmin) == {'rotina_liberada': False, 'rotina_admin': True})
    t('superusuário também é gestão', bandeiras(superusuario)['rotina_admin'] is True)

    print('\n== MENU E NOTIFICADOR NO PORTAL ==')
    # A fiação no base.html entrou depois deste teste: confira na página renderizada.
    html_gerente = cliente(gerente).get('/rotina-gerencial/').content.decode()
    t('quem tem rotina ganha o item no menu e o notificador de avisos',
      'data-rota="/rotina-gerencial/"' in html_gerente and 'window.RotinaNotificador' in html_gerente)
    html_admin = cliente(superadmin).get('/rotina-gerencial/').content.decode()
    t('SUPERADMIN vê o menu mesmo sem rotina própria, e sem notificador',
      'data-rota="/rotina-gerencial/"' in html_admin and 'window.RotinaNotificador' not in html_admin)
    html_ninguem = cliente(ninguem).get('/rotina-gerencial/').content.decode()
    t('quem não tem rotina não vê nem o menu nem o notificador',
      'data-rota="/rotina-gerencial/"' not in html_ninguem and 'window.RotinaNotificador' not in html_ninguem)
    html_pausada = cliente(pausada).get('/rotina-gerencial/').content.decode()
    t('rotina pausada tira os dois do portal',
      'data-rota="/rotina-gerencial/"' not in html_pausada and 'window.RotinaNotificador' not in html_pausada)
    with mock.patch('rotina.models.RotinaGerencial.objects') as quebrado:
        quebrado.filter.side_effect = RuntimeError('banco fora do ar')
        resultado = bandeiras(gerente)
    t('erro no banco não derruba a página', resultado == {'rotina_liberada': False, 'rotina_admin': False}, resultado)

    print('\n== CARTÃO DA HOME ==')
    from rotina.home import html_do_cartao
    # A home manda PADRÃO com curso pendente para /cursos/; aqui interessa só a home.
    pilha.enter_context(mock.patch('cursos.permissions.deve_ir_para_cursos', return_value=False))
    relogio.return_value = QUARTA_MEIO_DIA
    parcial_quarta = rotina_gerente.atividades.get(dia_semana=2, inicio='12:00')
    r = cliente(gerente).get('/')
    home = r.content.decode()
    t('a home abre (200) com o cartão, antes do cartão do perfil',
      r.status_code == 200 and 'id="rth"' in home and 'rth-estado-agora' in home
      and home.find('id="rth"') < home.find('User Info Card'), r.status_code)
    t('a atividade de agora: título, horário, categoria e quanto falta',
      'Parcial Gerentes' in home and '12:00–12:30 · <span' in home and 'termina em 25 min' in home
      and 'Agora · Resultado' in home)
    t('o destaque leva direto à atividade na rotina, como o aviso',
      f'href="/rotina-gerencial/?dia=2&amp;atividade={parcial_quarta.id}"' in home)
    t('a próxima ("em 25 min") e a lista curta de hoje, com o link para o dia inteiro',
      'A seguir' in home and 'em 25 min' in home and 'Mais 3 até as 18:30' in home
      and 'href="/rotina-gerencial/?dia=2"' in home)
    t('deixa claro que os avisos estão ligados (5 min antes e no início)',
      'Avisos ligados:' in home and '5 min antes e no início' in home)
    t('link para a rotina e a rota que redesenha o cartão quando o dia muda',
      'class="rth-link" href="/rotina-gerencial/"' in home and 'data-url="/rotina-gerencial/api/hoje/cartao/"' in home)
    for pessoa, rotulo in ((ninguem, 'sem rotina'), (pausada, 'rotina pausada'), (superadmin, 'SUPERADMIN sem rotina própria')):
        html_home = cliente(pessoa).get('/').content.decode()
        t(f'{rotulo}: a home não mostra o cartão', 'id="rth"' not in html_home and 'rth-cartao' not in html_home)

    pedido = RequestFactory().get('/')
    pedido.user = gerente
    with CaptureQueriesContext(connection) as consultas:
        parcial_home = render_to_string('rotina/_home.html', {'rotina_liberada': True, 'request': pedido})
    t('uma consulta só por renderização do cartão',
      len(consultas.captured_queries) == 1 and 'rth-cartao' in parcial_home,
      [c['sql'][:160] for c in consultas.captured_queries])
    with CaptureQueriesContext(connection) as consultas:
        sem_cartao = render_to_string('rotina/_home.html', {'rotina_liberada': False, 'request': pedido})
    t('sem rotina liberada: nenhuma consulta e nada na página',
      not consultas.captured_queries and not sem_cartao.strip(), len(consultas.captured_queries))
    anonimo = RequestFactory().get('/')
    anonimo.user = AnonymousUser()
    t('anônimo: nada', not render_to_string('rotina/_home.html', {'rotina_liberada': True, 'request': anonimo}).strip())
    with mock.patch('rotina.servicos.cartao_da_home', side_effect=RuntimeError('banco fora do ar')), \
            mock.patch('rotina.home.logger') as registro:
        falhou = render_to_string('rotina/_home.html', {'rotina_liberada': True, 'request': pedido})
        r = cliente(gerente).get('/')
    t('erro no cartão: some em silêncio, fica no log e a home abre normalmente',
      not falhou.strip() and registro.exception.called and r.status_code == 200 and 'id="rth"' not in r.content.decode())
    scripts_home = re.findall(r'<script>(.*?)</script>', parcial_home, flags=re.S)
    t('o JS do cartão fica numa função só (window.RotinaHome) e só pede o cartão de novo em data-mudanca',
      len(scripts_home) == 1 and scripts_home[0].strip().startswith('(function () {')
      and scripts_home[0].strip().endswith('})();') and 'window.RotinaHome' in scripts_home[0]
      and 'data-mudanca' in scripts_home[0])
    if scripts_home:
        js_valido(scripts_home[0], 'rotina/_home.html')

    cartao = servicos.cartao_da_home(gerente, QUARTA_MEIO_DIA)
    t('agora: progresso com ponto decimal (vai para o CSS), 5 de 12 concluídas, próxima mudança às 12:30',
      cartao['estado'] == 'agora' and cartao['atual']['progresso'] == '16.67'
      and (cartao['feitas'], cartao['total'], cartao['restantes']) == (5, 12, 3)
      and cartao['proxima_mudanca_ts'] == int(quando(16, 12, 30).timestamp() * 1000)
      and cartao['faixa']['blocos'][0]['esquerda'] == '0.00' and cartao['avisos_ligados'] and not cartao['sino_desligado'],
      {k: cartao[k] for k in ('estado', 'feitas', 'total', 'restantes', 'proxima_mudanca_ts')})
    cartao = servicos.cartao_da_home(gerente, quando(16, 7, 0))
    t('antes de começar: a primeira do dia em destaque ("começa em 30 min") e as seguintes em "Depois"',
      cartao['estado'] == 'antes' and cartao['proxima']['titulo'] == 'Reunião com o supervisor'
      and cartao['proxima']['comeca_em'] == '30 min' and cartao['faixa']['agora'] is None
      and [i['inicio'] for i in cartao['seguintes']] == ['08:00', '08:20', '08:40'], cartao['estado'])
    cartao = servicos.cartao_da_home(criadora, quando(17, 18, 45))
    t('intervalo livre: a próxima em destaque, e o sino desligado nas preferências aparece',
      cartao['estado'] == 'intervalo' and cartao['proxima']['titulo'] == 'Visita à loja vizinha'
      and cartao['proxima']['comeca_em'] == '15 min' and not cartao['seguintes'] and cartao['sino_desligado'] is True,
      (cartao['estado'], cartao['proxima'] and cartao['proxima']['titulo'], cartao['sino_desligado']))
    cartao = servicos.cartao_da_home(gerente, quando(16, 19, 0))
    t('depois da última: rotina concluída, sem avisos pendentes nem próxima mudança',
      cartao['estado'] == 'concluida' and cartao['feitas'] == 12 and not cartao['avisos_ligados']
      and cartao['proxima_mudanca_ts'] is None)
    cartao = servicos.cartao_da_home(gerente, DOMINGO)
    t('domingo: conta como a semana começa (primeira atividade de segunda)',
      cartao['estado'] == 'domingo' and cartao['amanha']['titulo'] == 'Reunião com o supervisor'
      and cartao['amanha']['url'].startswith('/rotina-gerencial/?dia=0&') and not cartao['avisos_ligados'])
    so_segunda = novo('sosegunda')
    RotinaGerencial.objects.create(user=so_segunda).atividades.create(
        dia_semana=0, inicio=time(9), fim=time(10), titulo='Só na segunda')
    cartao = servicos.cartao_da_home(so_segunda, QUARTA_MEIO_DIA)
    t('dia sem atividade: estado "vazio", sem avisos', cartao['estado'] == 'vazio' and not cartao['avisos_ligados'])
    t('o HTML de cada estado diz o que está acontecendo',
      'Primeira do dia' in html_do_cartao(gerente, quando(16, 7, 0))
      and 'Rotina de hoje concluída' in html_do_cartao(gerente, quando(16, 19, 0))
      and 'Sua semana começa amanhã' in html_do_cartao(gerente, DOMINGO)
      and 'Avisos só na tela' in html_do_cartao(criadora, quando(17, 18, 45))
      and 'Nenhuma atividade hoje' in html_do_cartao(so_segunda, QUARTA_MEIO_DIA))

    r = c_gerente.get('/rotina-gerencial/api/hoje/cartao/')
    t('api/hoje/cartao/: o cartão pronto em HTML, sem cache',
      r.status_code == 200 and r['Content-Type'].startswith('text/html') and 'rth-cartao' in r.content.decode()
      and r.get('Cache-Control') == 'no-store', (r.status_code, r.get('Content-Type')))
    t('api/hoje/cartao/ para quem não tem (ou pausou) a rotina: 204, e a home tira o cartão',
      cliente(ninguem).get('/rotina-gerencial/api/hoje/cartao/').status_code == 204
      and cliente(pausada).get('/rotina-gerencial/api/hoje/cartao/').status_code == 204)
    t('api/hoje/cartao/ sem login: 401', Client().get('/rotina-gerencial/api/hoje/cartao/').status_code == 401)
    chamados = {nome: duble.call_count for nome, duble in dubles.items() if duble.call_count}
    t('nada disso chamou função de envio', not chamados, chamados)

    print('\n== ADMIN DO DJANGO ==')
    t('os cinco modelos estão registrados',
      all(admin.site.is_registered(m) for m in (ModeloRotina, AtividadeModelo, RotinaGerencial, AtividadeRotina, AvisoRotina)))

finally:
    pilha.close()
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
