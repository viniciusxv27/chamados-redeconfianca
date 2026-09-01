"""Sala de reunião: marca, idioma, fundo e link público de visitante.

Roda dentro de uma transação desfeita no fim: não grava nada no banco.
"""
import base64
import json
import os
import sys
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from reunioes.models import (ConfiguracaoReunioes, ParticipanteReuniao, Reuniao,
                             VisitanteReuniao)
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


def corpo_do_jwt(token):
    meio = token.split('.')[1]
    meio += '=' * (-len(meio) % 4)
    return json.loads(base64.urlsafe_b64decode(meio))


marcador = transaction.atomic()
marcador.__enter__()
try:
    # A configuração é uma linha só e é a de verdade: guardo o estado e devolvo.
    # (o rollback já cobre, mas o teste não deve depender só disso)
    cfg = ConfiguracaoReunioes.get()
    area = Sector.objects.create(name='ZZ Area Sala')

    def novo(username):
        return User.objects.create_user(
            username=username, email=f'{username}@exemplo-teste.local',
            first_name=username.split('.')[1].title(), last_name='Teste',
            password='S3nha!teste', sector=area)

    dono = novo('sp.dono')
    convidado = novo('sp.convidado')
    estranho = novo('sp.estranho')

    reuniao = Reuniao.objects.create(
        titulo='ZZ Reunião com visitante', inicio=timezone.now() + timedelta(hours=1),
        organizador=dono)
    ParticipanteReuniao.objects.create(reuniao=reuniao, user=convidado)

    cd = Client(); cd.force_login(dono)
    cc = Client(); cc.force_login(convidado)
    ce = Client(); ce.force_login(estranho)
    anon = Client()

    print('== TODA REUNIÃO NASCE FECHADA ==')
    t('sem link público ao criar', not reuniao.tem_link_publico)
    r = anon.get('/reunioes/convidado/qualquer-coisa/')
    t('token inventado devolve 404', r.status_code == 404, r.status_code)

    print('\n== SÓ O ORGANIZADOR ABRE O LINK ==')
    r = cc.post(f'/reunioes/{reuniao.id}/link-publico/', {'acao': 'abrir'}, follow=True)
    reuniao.refresh_from_db()
    t('convidado não cria link', not reuniao.tem_link_publico)
    t('e a tela explica', 'Só o organizador' in r.content.decode())
    r = ce.post(f'/reunioes/{reuniao.id}/link-publico/', {'acao': 'abrir'}, follow=True)
    reuniao.refresh_from_db()
    t('estranho também não', not reuniao.tem_link_publico)

    r = cd.get(f'/reunioes/{reuniao.id}/link-publico/')
    t('GET não cria link (405)', r.status_code == 405, r.status_code)

    r = cd.post(f'/reunioes/{reuniao.id}/link-publico/', {'acao': 'abrir'}, follow=True)
    reuniao.refresh_from_db()
    t('organizador cria', reuniao.tem_link_publico)
    t('o token é longo o bastante para não ser adivinhado',
      len(reuniao.token_publico) >= 30, len(reuniao.token_publico))
    t('guarda quando foi criado', reuniao.publico_em is not None)
    t('o endereço aparece na tela', reuniao.token_publico in r.content.decode())

    print('\n== O VISITANTE PRECISA DIZER O NOME ==')
    url = f'/reunioes/convidado/{reuniao.token_publico}/'
    r = anon.get(url)
    t('a porta abre sem login', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('pede o nome', 'name="nome"' in html)
    t('mostra o tema da reunião', reuniao.titulo in html)
    t('ainda não carrega o vídeo', 'JitsiMeetExternalAPI' not in html)

    r = anon.post(url, {'nome': 'Jo'})
    t('nome curto demais é recusado', 'pelo menos 3 letras' in r.content.decode())
    t('e não vira visitante', VisitanteReuniao.objects.filter(reuniao=reuniao).count() == 0)

    r = anon.post(url, {'nome': '   Maria   das   Dores  '}, follow=True)
    t('nome válido entra na sala', 'JitsiMeetExternalAPI' in r.content.decode())
    v = VisitanteReuniao.objects.filter(reuniao=reuniao).first()
    t('fica registrado quem entrou', v is not None)
    t('o nome vem limpo de espaços', v and v.nome == 'Maria das Dores', v.nome if v else '')

    html = anon.get(url).content.decode()
    t('não pede o nome de novo', 'name="nome"' not in html)
    t('a tela diz que é visitante', 'como visitante' in html)
    t('o nome usado é o digitado', '"Maria das Dores"' in html)

    print('\n== O VISITANTE NÃO GANHA PODER ==')
    t('não tem botão de gravar ata', 'id="rn-gravar"' not in html)
    t('nem o código que sobe a gravação', 'transcricoes/upload' not in html)
    t('não vê link para os detalhes da reunião',
      f'/reunioes/{reuniao.id}/' not in html)
    t('não entra na página de detalhes',
      anon.get(f'/reunioes/{reuniao.id}/').status_code in (302, 403),
      anon.get(f'/reunioes/{reuniao.id}/').status_code)
    t('não entra na sala interna',
      anon.get(f'/reunioes/{reuniao.id}/sala/').status_code in (302, 403))
    t('não abre a lista de reuniões', anon.get('/reunioes/').status_code in (302, 403))

    print('\n== O TOKEN DE VÍDEO DO VISITANTE ==')
    from reunioes.jaas import gerar_token, gerar_token_visitante
    falso = ConfiguracaoReunioes(
        jaas_app_id='vpaas-magic-cookie-teste', jaas_api_key_id='teste/abc123',
        jaas_chave_privada=__import__('subprocess').run(
            ['openssl', 'genrsa', '2048'], capture_output=True, text=True).stdout)
    tv = gerar_token_visitante(falso, 'Maria das Dores', reuniao.sala)
    tu = gerar_token(falso, dono, reuniao.sala)
    t('o visitante recebe token', bool(tv))
    if tv:
        c = corpo_do_jwt(tv)['context']['user']
        t('visitante nunca é moderador', c['moderator'] == 'false', c['moderator'])
        t('sem e-mail: ele não tem cadastro', c['email'] == '')
        t('id anônimo, não se mistura com colaborador', c['id'].startswith('visitante-'), c['id'])
        t('o nome no token é o que ele digitou', c['name'] == 'Maria das Dores')
    if tu:
        t('o colaborador continua moderador',
          corpo_do_jwt(tu)['context']['user']['moderator'] == 'true')
    t('dois visitantes não recebem o mesmo id',
      corpo_do_jwt(gerar_token_visitante(falso, 'A', 'x'))['context']['user']['id']
      != corpo_do_jwt(gerar_token_visitante(falso, 'B', 'x'))['context']['user']['id'])

    print('\n== TROCAR E DESLIGAR O LINK ==')
    antigo = reuniao.token_publico
    cd.post(f'/reunioes/{reuniao.id}/link-publico/', {'acao': 'trocar'})
    reuniao.refresh_from_db()
    t('trocar gera outro endereço', reuniao.token_publico != antigo)
    t('o endereço antigo morre na hora',
      Client().get(f'/reunioes/convidado/{antigo}/').status_code == 404)

    cd.post(f'/reunioes/{reuniao.id}/link-publico/', {'acao': 'fechar'})
    reuniao.refresh_from_db()
    t('desligar limpa o token', not reuniao.tem_link_publico)
    t('e a porta fecha', Client().get(url).status_code == 404)

    print('\n== A REUNIÃO ACABANDO, O LINK ACABA JUNTO ==')
    reuniao.abrir_link_publico()
    novo_url = f'/reunioes/convidado/{reuniao.token_publico}/'
    t('com a reunião viva, entra', Client().get(novo_url).status_code == 200)
    Reuniao.objects.filter(id=reuniao.id).update(status=Reuniao.CANCELADA)
    t('reunião cancelada fecha o link', Client().get(novo_url).status_code == 404)
    Reuniao.objects.filter(id=reuniao.id).update(status=Reuniao.ENCERRADA)
    t('reunião encerrada também', Client().get(novo_url).status_code == 404)
    Reuniao.objects.filter(id=reuniao.id).update(status=Reuniao.AGENDADA)

    print('\n== A CHAVE GERAL DA CONFIGURAÇÃO ==')
    ConfiguracaoReunioes.objects.filter(pk=1).update(permitir_link_publico=False)
    t('desligado no módulo, o link para de valer',
      Client().get(novo_url).status_code == 404)
    html = cd.get(f'/reunioes/{reuniao.id}/').content.decode()
    t('e o painel some da tela de detalhes', 'Criar link público' not in html)
    ConfiguracaoReunioes.objects.filter(pk=1).update(permitir_link_publico=True)
    t('religado, volta a valer', Client().get(novo_url).status_code == 200)

    print('\n== MARCA, IDIOMA E FUNDO ==')
    palco = open('templates/reunioes/_palco.html', encoding='utf-8').read()
    t('o idioma vai como opção do construtor', "lang: 'ptBR'" in palco)
    t('e também no config', "defaultLanguage: 'ptBR'" in palco)
    t('o navegador não escolhe o idioma', 'LANG_DETECTION: false' in palco)
    t('a logo da rede é a marca d\'água', "DEFAULT_LOGO_URL: '{{ url_logo }}'" in palco)
    t('o branding é buscado pelo servidor de vídeo', 'dynamicBrandingUrl' in palco)
    t('o fundo é aplicado ao entrar', "setVirtualBackground" in palco)
    t('e quando a pessoa liga a câmera', 'videoMuteStatusChanged' in palco)
    t('mas só uma vez: quem trocou de propósito não perde a escolha',
      'if (fundoAplicado) return;' in palco)
    t('servidor sem o comando não derruba a sala', 'catch (e) {' in palco)

    r = Client().get('/reunioes/branding.json')
    t('o branding abre sem login', r.status_code == 200, r.status_code)
    marca = r.json()
    t('responde a qualquer origem', r['Access-Control-Allow-Origin'] == '*')
    t('logo da rede', 'logo-t' in marca['logoImageUrl'])
    t('a logo pedida no fundo', marca['backgroundImageUrl'].endswith('logo.png'))
    t('fundo da tela de entrada', 'premeetingBackground' in marca)
    t('o fundo da rede na lista de fundos',
      marca['virtualBackgrounds'] and 'reuniao-fundo' in marca['virtualBackgrounds'][0])
    t('as cores do portal', marca['customTheme']['palette']['action01'] == '#FF6B35')
    t('tudo em endereço absoluto: quem baixa é outro servidor',
      all(str(marca[k]).startswith('http') for k in
          ('logoImageUrl', 'backgroundImageUrl', 'logoClickUrl')))

    import pathlib
    t('a imagem de fundo está no projeto',
      pathlib.Path('static/images/reuniao-fundo.jpg').exists())

    print('\n== A CONFIGURAÇÃO MOSTRA O QUE FALTA FAZER NO 8x8 ==')
    su = User.objects.filter(is_superuser=True, is_active=True).first()
    cs = Client(); cs.force_login(su)
    html = cs.get('/reunioes/configuracao/').content.decode()
    t('mostra a URL do branding', '/reunioes/branding.json' in html)
    t('avisa que no 8x8 é manual', 'Advanced branding' in html)
    t('deixa trocar o fundo', 'name="fundo_sala_url"' in html)
    t('deixa desligar o fundo automático', 'name="aplicar_fundo_padrao"' in html)
    t('deixa desligar o link público', 'name="permitir_link_publico"' in html)

    print('\n== AS TELAS DE VISITANTE SE VIRAM SOZINHAS ==')
    for nome, url_ in [('entrar', novo_url), ('link morto', '/reunioes/convidado/xxxx/'),
                       ('saiu', f'/reunioes/convidado/{reuniao.token_publico}/saiu/')]:
        h = Client().get(url_).content.decode()
        t(f'{nome}: é uma página inteira, sem o menu do portal',
          '<!DOCTYPE html>' in h and 'id="sidebar"' not in h)
        t(f'{nome}: não é indexada por buscador', 'noindex' in h)

    print('\n== A SALA DE QUEM TEM CONTA CONTINUA IGUAL ==')
    r = cc.get(f'/reunioes/{reuniao.id}/sala/')
    t('convidado entra', r.status_code == 200, r.status_code)
    html = r.content.decode()
    t('com o nome do cadastro travado', 'readOnlyName: true' in html)
    t('e o menu do portal', 'id="sidebar"' in html)
    t('estranho não entra', ce.get(f'/reunioes/{reuniao.id}/sala/').status_code == 302)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
