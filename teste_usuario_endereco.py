"""Endereço no cadastro do usuário (/users/manage/users/): rua, número e CEP.

Pedido: incluir campos de endereço (rua, número, CEP) no cadastro do usuário.
Os campos já existiam no modelo, preenchidos só pelo pré-cadastro
(`address`, `address_number`, `address_complement`, `state`, `cep`); agora as
telas de criar e editar têm a seção "Endereço", o CEP sai normalizado
(00000-000), o que não tem 8 dígitos é recusado, o perfil mostra o bloco e o
histórico de alterações registra cada campo.

NADA SAI DAQUI: criar/editar usuário não chama envio nenhum, e ainda assim
WhatsApp (Z-API/Evolution), e-mail (Resend e backend do Django), push
(pywebpush/OneSignal/TruePush) e a rede (`urlopen`/`requests`) viram dublês que
contam chamadas — o teste falha se algum for chamado. O agendador do Tangerino,
que o middleware acorda a cada requisição, também vira dublê. Tudo roda numa
transação desfeita no fim.
"""
import os
import sys
from contextlib import ExitStack
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import Client, RequestFactory
from django.test.utils import override_settings

from users.models import Sector, UserChangeLog
from users.views import _parse_cep, _read_address

User = get_user_model()
ok = fail = 0

ALVOS_DE_ENVIO = [
    'core.zapi.send_whatsapp_message',
    'core.evolution.enviar_texto',
    'users.resend_email.enviar',
    'notifications.push_utils.send_push_notification_to_user',
    'notifications.push_utils.send_push_notification_to_users',
    'notifications.push_utils.push_service.send_to_user',
    'notifications.push_utils.push_service.send_to_users',
    'notifications.models.PushNotification.send_notification',
    'urllib.request.urlopen',
    'requests.Session.request',
]


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def trecho(html, inicio, fim):
    """O pedaço do HTML entre dois marcadores (vazio se algum faltar)."""
    a = html.find(inicio)
    b = html.find(fim, a + 1) if a >= 0 else -1
    return html[a:b] if a >= 0 and b > a else ''


print('== CEP: NORMALIZAÇÃO E RECUSA ==')
t('com máscara fica igual', _parse_cep('29100-000') == ('29100-000', None), _parse_cep('29100-000'))
t('sem máscara ganha o hífen', _parse_cep('29100000') == ('29100-000', None), _parse_cep('29100000'))
t('com ponto e espaços também', _parse_cep(' 29.100-000 ') == ('29100-000', None), _parse_cep(' 29.100-000 '))
t('em branco passa sem erro (quem exige confere depois)', _parse_cep('') == ('', None) and _parse_cep(None) == ('', None))
for ruim in ('1234567', '123456789', '29100-00', 'abcde-fgh', '29100-00a', '٢٩١٠٠٠٠٠'):
    cep, erro = _parse_cep(ruim)
    t(f'recusa {ruim!r}', cep == '' and erro and 'CEP inválido' in erro and '8 dígitos' in erro, (cep, erro))

fabrica = RequestFactory()
valores, erro = _read_address(fabrica.post('/', {'cep': '29135000', 'state': 'es', 'address': '  Rua A  '}).POST)
t('leitura só traz os campos que vieram no POST',
  valores == {'cep': '29135-000', 'address': 'Rua A', 'state': 'ES'} and erro is None, (valores, erro))
valores, erro = _read_address(fabrica.post('/', {'state': 'XX', 'address_number': 'S/N'}).POST)
t('UF que não existe vira vazio', valores == {'address_number': 'S/N', 'state': ''} and erro is None, (valores, erro))
valores, erro = _read_address(fabrica.post('/', {'cep': '999', 'address': 'Rua B'}).POST)
t('CEP inválido devolve erro e nenhum valor', valores == {} and erro and 'CEP inválido' in erro, (valores, erro))


marcador = transaction.atomic()
marcador.__enter__()
pilha = ExitStack()
try:
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
    pilha.enter_context(override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'))
    mail.outbox = []

    area = Sector.objects.create(name='ZZ Loja Endereço')
    admin = User.objects.create_user(
        username='zz.endadmin', email='zz.endadmin@exemplo-teste.local', password='S3nha!teste',
        sector=area, first_name='End', last_name='Admin', hierarchy='SUPERADMIN',
        is_superuser=True, is_staff=True)
    c = Client()
    c.force_login(admin)

    print('\n== TELA DE CRIAR ==')
    r = c.get('/users/manage/users/create/')
    html = r.content.decode()
    t('abre', r.status_code == 200, r.status_code)
    secao = trecho(html, '<!-- Endereço -->', 'Setor Principal')
    t('tem a seção "Endereço"', '<h3' in secao and 'Endereço' in trecho(secao, '<h3', '</h3>'), secao[:200])
    for nome, rotulo in (('cep', 'CEP *'), ('address', 'Rua'), ('address_number', 'Número'),
                         ('address_complement', 'Complemento'), ('neighborhood', 'Bairro *'),
                         ('city', 'Cidade *'), ('state', 'Estado')):
        t(f'"{rotulo}" ({nome}) está dentro da seção',
          f'name="{nome}"' in secao and f'>{rotulo}</label>' in secao)
    t('o CEP continua obrigatório na criação', 'name="cep"' in secao and 'required' in trecho(secao, 'id="cep"', '</div>'))
    t('o select de UF lista os estados', '<option value="ES">ES</option>' in secao and '<option value="SP">SP</option>' in secao)
    rh = trecho(html, 'Dados Pessoais / RH', '<!-- Endereço -->')
    t('Bairro/Cidade/CEP saíram de "Dados Pessoais / RH" (e o PDV ficou)',
      rh and 'name="pdv"' in rh and not any(f'name="{n}"' in rh for n in ('cep', 'neighborhood', 'city')))
    t('a máscara de CEP está na página', "getElementById('cep')" in html and 'CEP: só dígitos' in html)

    def criar(**extra):
        """POST como a tela de criar manda (todo campo vai, mesmo vazio)."""
        dados = {
            'full_name': 'ZZ Endereco Novo', 'email': 'zz.endnovo@exemplo-teste.local',
            'username': 'zz.endnovo', 'password': 'S3nha!teste', 'hierarchy': 'PADRAO',
            'sector': str(area.id), 'sectors': [str(area.id)], 'status': 'ATIVO', 'is_active': 'on',
            'cpf': '39053344705', 'rg': '1234567', 'pis': '12345678901', 'job_title': 'VENDEDOR',
            'birth_date': '1990-01-01', 'admission_date': '2026-01-05', 'demission_date': '',
            'phone': '', 'disc_profile': '', 'login_code': '', 'pdv': '', 'contract_type': '',
            'branch_cnpj': '', 'salary': '', 'pix_key': '',
            'uniform_size_shirt': 'M', 'uniform_size_pants': 'M',
            'neighborhood': 'Centro', 'city': 'Vitória',
            'cep': '29100000', 'address': 'Rua das Flores', 'address_number': '123',
            'address_complement': 'Apto 2', 'state': 'ES',
        }
        dados.update(extra)
        return c.post('/users/manage/users/create/', dados)

    r = criar(cep='1234')
    t('CEP com 4 dígitos não cria o usuário', not User.objects.filter(username='zz.endnovo').exists())
    t('e a tela explica', 'CEP inválido' in r.content.decode() and '8 dígitos' in r.content.decode())

    r = criar(cep='')
    t('sem CEP também não cria (regra que já existia)', not User.objects.filter(username='zz.endnovo').exists())
    t('e pede o CEP', 'Informe o CEP.' in r.content.decode())

    r = criar()
    novo = User.objects.filter(username='zz.endnovo').first()
    t('com tudo certo cria e volta para a lista', novo is not None and r.status_code == 302
      and r.url.endswith('/users/manage/users/'), (r.status_code, getattr(r, 'url', '')))
    if novo is None:
        raise SystemExit('sem o usuário criado o resto do teste não tem o que conferir')
    t('grava o CEP digitado sem máscara já normalizado', novo.cep == '29100-000', novo.cep)
    t('grava rua, número e complemento',
      (novo.address, novo.address_number, novo.address_complement) == ('Rua das Flores', '123', 'Apto 2'),
      (novo.address, novo.address_number, novo.address_complement))
    t('grava UF, bairro e cidade', (novo.state, novo.neighborhood, novo.city) == ('ES', 'Centro', 'Vitória'),
      (novo.state, novo.neighborhood, novo.city))

    criar(username='zz.endnovo2', email='zz.endnovo2@exemplo-teste.local',
          address='', address_number='', address_complement='', state='')
    novo2 = User.objects.filter(username='zz.endnovo2').first()
    t('rua, número, complemento e UF são opcionais',
      novo2 is not None and novo2.cep == '29100-000'
      and (novo2.address, novo2.address_number, novo2.address_complement, novo2.state) == ('', '', '', ''),
      novo2 and (novo2.cep, novo2.address, novo2.address_number, novo2.state))

    print('\n== TELA DE EDITAR ==')
    alvo = User.objects.create_user(
        username='zz.endalvo', email='zz.endalvo@exemplo-teste.local', password='S3nha!teste',
        sector=area, first_name='End', last_name='Alvo', cpf='39053344705', rg='7654321',
        neighborhood='Centro', city='Viana', cep='29135000',
        address='Av. Antiga', address_number='10', address_complement='Casa', state='ES')
    url_editar = f'/users/manage/users/{alvo.id}/edit/'
    html = c.get(url_editar).content.decode()
    secao = trecho(html, '<!-- Endereço -->', 'Setor Principal')
    t('tem a seção "Endereço"', bool(secao))
    t('mostra os valores gravados',
      all(v in secao for v in ('value="Av. Antiga"', 'value="10"', 'value="Casa"',
                                 'value="Centro"', 'value="Viana"', 'value="29135000"')))
    t('com a UF selecionada', '<option value="ES" selected>ES</option>' in secao)
    t('na edição o CEP não é obrigatório (348 cadastros ainda não têm)',
      'required' not in trecho(secao, 'id="cep"', '</div>'))
    t('a máscara de CEP está na página', 'CEP: só dígitos' in html)

    def editar(seguir=False, **extra):
        """POST da tela de edição com o que está gravado, trocando só `extra`."""
        dados = {
            'full_name': 'End Alvo', 'email': alvo.email, 'username': alvo.username,
            'hierarchy': 'PADRAO', 'sector': str(area.id), 'sectors': [str(area.id)],
            'status': 'ATIVO', 'is_active': 'on', 'cpf': alvo.cpf, 'pis': alvo.pis, 'job_title': 'VENDEDOR',
            'birth_date': '1990-01-01', 'admission_date': '2026-01-05', 'demission_date': '',
            'phone': alvo.phone, 'disc_profile': '', 'login_code': '', 'pdv': '', 'contract_type': '',
            'branch_cnpj': '', 'salary': '', 'pix_key': '',
            'uniform_size_shirt': 'M', 'uniform_size_pants': 'M',
            'neighborhood': alvo.neighborhood, 'city': alvo.city,
            'cep': alvo.cep, 'address': alvo.address, 'address_number': alvo.address_number,
            'address_complement': alvo.address_complement, 'state': alvo.state,
        }
        dados.update(extra)
        dados = {k: v for k, v in dados.items() if v is not None}   # None = campo fora do POST
        return c.post(url_editar, dados, follow=seguir)

    UserChangeLog.objects.filter(target=alvo).delete()
    r = editar(cep='29.160 161', address='Rua Nova', address_number='S/N',
               address_complement='', state='sp')
    alvo.refresh_from_db()
    t('salva e volta para a lista', r.status_code == 302 and r.url.endswith('/users/manage/users/'),
      (r.status_code, getattr(r, 'url', '')))
    t('CEP com ponto e espaço sai normalizado', alvo.cep == '29160-161', alvo.cep)
    t('rua, número e complemento atualizados',
      (alvo.address, alvo.address_number, alvo.address_complement) == ('Rua Nova', 'S/N', ''),
      (alvo.address, alvo.address_number, alvo.address_complement))
    t('UF em minúscula vira maiúscula', alvo.state == 'SP', alvo.state)
    logs = {log.field: log for log in UserChangeLog.objects.filter(target=alvo)}
    t('o histórico registra CEP, rua, número, complemento e UF',
      {'cep', 'address', 'address_number', 'address_complement', 'state'} <= set(logs), sorted(logs))
    t('com rótulo e antes/depois legíveis',
      logs.get('cep') and (logs['cep'].field_label, logs['cep'].old_value, logs['cep'].new_value)
      == ('CEP', '29135000', '29160-161')
      and logs.get('address') and (logs['address'].field_label, logs['address'].new_value) == ('Rua', 'Rua Nova'),
      logs.get('cep') and (logs['cep'].field_label, logs['cep'].old_value, logs['cep'].new_value))

    r = editar(seguir=True, full_name='Nome Que Nao Pode Salvar', cep='2916016', address='Rua Errada')
    alvo.refresh_from_db()
    t('CEP com 7 dígitos não salva nada', alvo.cep == '29160-161' and alvo.address == 'Rua Nova'
      and alvo.first_name == 'End', (alvo.cep, alvo.address, alvo.first_name))
    t('volta para a edição explicando', r.redirect_chain and r.redirect_chain[-1][0].endswith(url_editar)
      and 'CEP inválido' in r.content.decode(), r.redirect_chain)

    editar(cep='')
    alvo.refresh_from_db()
    t('na edição dá para apagar o CEP', alvo.cep == '' and alvo.address == 'Rua Nova', (alvo.cep, alvo.address))

    editar(cep='29160161')
    alvo.refresh_from_db()
    t('e preencher de novo', alvo.cep == '29160-161', alvo.cep)

    editar(cep=None, address=None, address_number=None, address_complement=None, state=None,
           pis='99999999999')
    alvo.refresh_from_db()
    t('envio sem a seção (tela antiga aberta) salva o resto', alvo.pis == '99999999999', alvo.pis)
    t('e não apaga o endereço',
      (alvo.cep, alvo.address, alvo.address_number, alvo.state) == ('29160-161', 'Rua Nova', 'S/N', 'SP'),
      (alvo.cep, alvo.address, alvo.address_number, alvo.state))

    print('\n== PERFIL (DETALHE) E FICHA ==')
    r = c.get(f'/users/manage/users/{alvo.id}/profile/')
    html = r.content.decode()
    t('o perfil abre', r.status_code == 200, r.status_code)
    bloco = trecho(html, '<!-- Endereço -->', '<!-- Documentos -->')
    t('tem o bloco "Endereço"', bool(bloco) and 'Endereço' in bloco)
    for rotulo, valor in (('CEP', '29160-161'), ('Rua', 'Rua Nova'), ('Número', 'S/N'),
                          ('Bairro', 'Centro'), ('Cidade', 'Viana'), ('Estado', 'SP')):
        t(f'mostra {rotulo}', f'>{rotulo}</p>' in bloco and f'>{valor}</p>' in bloco)
    t('complemento vazio aparece como "—"', '>Complemento</p>' in bloco and '—' in bloco)
    t('o CEP não aparece duas vezes no perfil', html.count('>CEP</p>') == 1, html.count('>CEP</p>'))
    ficha = c.get(f'/users/manage/users/{alvo.id}/profile/print/')
    corpo = ficha.content.decode()
    t('a ficha impressa continua trazendo o endereço',
      ficha.status_code == 200 and 'Rua Nova' in corpo and '29160-161' in corpo, ficha.status_code)

    print('\n== CEP ANTIGO GRAVADO SEM HÍFEN (29 CADASTROS REAIS) ==')
    for gravado, exibido in (('29090000', '29090-000'), ('29090-000', '29090-000'),
                             ('1234567', '1234567'), ('', '')):
        t(f'cep_display de {gravado!r} é {exibido!r}', User(cep=gravado).cep_display == exibido,
          User(cep=gravado).cep_display)
    User.objects.filter(pk=alvo.pk).update(cep='29090000')       # como veio da importação
    bloco = trecho(c.get(f'/users/manage/users/{alvo.id}/profile/').content.decode(),
                   '<!-- Endereço -->', '<!-- Documentos -->')
    t('o perfil mostra com hífen', '>29090-000</p>' in bloco and '29090000' not in bloco)
    corpo = c.get(f'/users/manage/users/{alvo.id}/profile/print/').content.decode()
    t('a ficha impressa também', '29090-000' in corpo and '29090000' not in corpo)
    alvo.refresh_from_db()
    t('sem regravar o que está no banco', alvo.cep == '29090000', alvo.cep)

    print('\n== NADA SAIU DAQUI ==')
    chamados = {nome: d.call_count for nome, d in dubles.items() if d.call_count}
    t('nenhuma função de envio nem a rede foi chamada', not chamados, chamados)
    t('nenhum e-mail na caixa de saída', len(mail.outbox) == 0, len(mail.outbox))
finally:
    pilha.close()
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
