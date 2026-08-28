"""Testa (1) a recuperação de senha por e-mail via Resend e (2) a exclusão de
comentários de tarefa no Impulso.

Só apaga o que este arquivo cria. Nenhum e-mail real é disparado: a função de
envio é substituída por um espião.
"""
import os, sys, django, re, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.test import Client
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

import users.views as uv
from impulso.models import Meta, MetaComentario

User = get_user_model()
ok = fail = 0
criados = {'users': [], 'metas': [], 'comentarios': []}


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1; print(f'  OK   {nome}')
    else:
        fail += 1; print(f'  FALHA {nome} {extra}')


# --- espião no lugar do envio real -----------------------------------------
enviados = []
def espiao(para, assunto, html, texto=''):
    enviados.append({'para': para, 'assunto': assunto, 'html': html, 'texto': texto})
    return True, ''

import users.resend_email as re_mod
_envio_real = re_mod.enviar
re_mod.enviar = espiao
re_mod.configurada = lambda: True

try:
    # ---------------- dados ----------------
    alvo = User.objects.create_user(
        username='teste.recup', email='teste.recup@exemplo-teste.local',
        password='SenhaAntiga123!', first_name='Fulano', last_name='Teste')
    criados['users'].append(alvo)
    inativo = User.objects.create_user(
        username='teste.inativo.cmt', email='teste.inativo@exemplo-teste.local',
        password='x', is_active=False)
    criados['users'].append(inativo)

    caches['local'].delete(f"recuperacao:{alvo.email.lower()}")
    c = Client()
    url = '/users/forgot-password/'

    print('\n== RECUPERAÇÃO DE SENHA (Resend) ==')
    r = c.get(url)
    t('tela abre', r.status_code == 200, r.status_code)
    corpo = r.content.decode()
    t('não fala mais em WhatsApp', 'WhatsApp' not in corpo and 'whatsapp' not in corpo)
    t('fala em e-mail', 'e-mail' in corpo.lower())

    enviados.clear()
    r = c.post(url, {'email': alvo.email}, follow=True)
    t('POST responde 200', r.status_code == 200, r.status_code)
    t('um e-mail enviado', len(enviados) == 1, len(enviados))
    t('enviado para o endereço certo', enviados and enviados[0]['para'] == alvo.email)

    link = ''
    if enviados:
        m = re.search(r'https?://[^\s"\'<]+/users/reset-password/[^\s"\'<]+', enviados[0]['html'])
        link = m.group(0) if m else ''
    t('e-mail traz link de redefinição', bool(link), link[:60])
    t('e-mail traz o nome da pessoa', enviados and 'Fulano' in enviados[0]['html'])

    # o link precisa funcionar de verdade
    caminho = '/users/' + link.split('/users/', 1)[1] if link else ''
    r2 = Client().get(caminho) if caminho else None
    t('link abre a tela de nova senha', r2 is not None and r2.status_code == 200,
      getattr(r2, 'status_code', '-'))

    # e trocar a senha de fato
    c2 = Client()
    c2.get(caminho)
    r3 = c2.post(caminho, {'new_password': 'NovaSenhaForte#2026',
                           'confirm_password': 'NovaSenhaForte#2026'}, follow=True)
    alvo.refresh_from_db()
    t('senha trocada pelo link', alvo.check_password('NovaSenhaForte#2026'),
      'ainda a antiga' if alvo.check_password('SenhaAntiga123!') else '?')

    # --- não vaza quem existe ---
    caches['local'].delete('recuperacao:naoexiste@exemplo-teste.local')
    enviados.clear()
    r = c.post(url, {'email': 'naoexiste@exemplo-teste.local'}, follow=True)
    txt_inexistente = r.content.decode()
    t('e-mail inexistente: nada enviado', len(enviados) == 0, len(enviados))
    # Olha só a área de mensagens; o base.html tem console.error com
    # "não encontrado" que não tem nada a ver com a recuperação.
    def area_msg(html):
        m = re.search(r'{% raw %}', html)
        blocos = re.findall(r'<div[^>]*role="alert"[^>]*>.*?</div>', html, re.S)
        return ' '.join(blocos) or ' '.join(re.findall(r'Se este e-mail[^<]*', html))
    vaza = [frase for frase in ('email não encontrado', 'e-mail não encontrado',
                                'não existe', 'não cadastrado')
            if frase in area_msg(txt_inexistente).lower()]
    t('e-mail inexistente: não revela que não existe', not vaza, vaza)

    caches['local'].delete(f'recuperacao:{alvo.email.lower()}')
    enviados.clear()
    r = c.post(url, {'email': alvo.email}, follow=True)
    txt_existente = r.content.decode()
    def msg(html):
        m = re.findall(r'Se este e-mail estiver cadastrado[^<]*', html)
        return m[0] if m else ''
    t('mesma mensagem para existente e inexistente',
      msg(txt_existente) and msg(txt_existente) == msg(txt_inexistente),
      f'{msg(txt_existente)[:40]!r} vs {msg(txt_inexistente)[:40]!r}')

    # --- inativo não recebe ---
    caches['local'].delete(f'recuperacao:{inativo.email.lower()}')
    enviados.clear()
    c.post(url, {'email': inativo.email}, follow=True)
    t('usuário inativo não recebe link', len(enviados) == 0, len(enviados))

    # --- anti-flood ---
    enviados.clear()
    c.post(url, {'email': alvo.email}, follow=True)   # ainda dentro da janela
    t('segundo pedido seguido não reenvia', len(enviados) == 0, len(enviados))
    caches['local'].delete(f'recuperacao:{alvo.email.lower()}')
    enviados.clear()
    c.post(url, {'email': alvo.email}, follow=True)
    t('depois da janela reenvia', len(enviados) == 1, len(enviados))

    # --- e-mail vazio ---
    enviados.clear()
    r = c.post(url, {'email': ''}, follow=True)
    t('e-mail vazio: nada enviado', len(enviados) == 0)
    t('e-mail vazio: avisa', 'Informe seu e-mail' in r.content.decode())

    # --- caixa alta funciona ---
    caches['local'].delete(f'recuperacao:{alvo.email.lower()}')
    enviados.clear()
    c.post(url, {'email': alvo.email.upper()}, follow=True)
    t('e-mail em CAIXA ALTA encontra a pessoa', len(enviados) == 1, len(enviados))

    # --- chave não aparece em lugar nenhum do código ---
    fontes = [f for pad in ('*.py', '*/*.py', 'templates/**/*.html')
              for f in glob.glob(pad, recursive=True)]
    marca = 're_' + '27jXH5f1'          # partido para o próprio teste não casar
    vazou = [f for f in fontes
             if not os.path.basename(f).startswith('teste_')
             and marca in open(f, encoding='utf-8', errors='ignore').read()]
    t('APIKEY não está no código', not vazou, vazou)

    print('\n== EXCLUIR COMENTÁRIO DA TAREFA ==')
    gestor = User.objects.create_user(username='teste.gestor.cmt',
                                      email='teste.gestor.cmt@exemplo-teste.local',
                                      password='S3nha!teste', first_name='Gestor')
    colab = User.objects.create_user(username='teste.colab.cmt',
                                     email='teste.colab.cmt@exemplo-teste.local',
                                     password='S3nha!teste', first_name='Colab')
    outro = User.objects.create_user(username='teste.outro.cmt',
                                     email='teste.outro.cmt@exemplo-teste.local',
                                     password='S3nha!teste', first_name='Outro')
    criados['users'] += [gestor, colab, outro]

    # Sem estar no grupo do Impulso o portal nem deixa abrir a tela.
    from impulso.models import GRUPO_ADM
    from communications.models import CommunicationGroup
    grupo_adm = CommunicationGroup.objects.filter(name__iexact=GRUPO_ADM).first()
    assert grupo_adm, 'grupo do Impulso não encontrado'
    for u in (gestor, colab, outro):
        u.communication_groups.add(grupo_adm)

    meta = Meta.objects.create(titulo='META TESTE COMENTARIO', colaborador=colab,
                               gestor=gestor, prazo=(timezone.now() + timedelta(days=5)).date())
    criados['metas'].append(meta)

    def novo_comentario(autor, txt):
        cm = MetaComentario.objects.create(meta=meta, autor=autor, mensagem=txt)
        criados['comentarios'].append(cm.id)
        return cm

    cg = Client(); cg.force_login(gestor)
    cc = Client(); cc.force_login(colab)
    co = Client(); co.force_login(outro)

    detalhe = f'/impulso/metas/{meta.id}/'
    def excluir(cli, cid):
        return cli.post(f'/impulso/metas/comentario/{cid}/excluir/', follow=True)

    # autor apaga o próprio
    cm = novo_comentario(colab, 'comentário do colaborador')
    excluir(cc, cm.id)
    t('autor apaga o próprio comentário', not MetaComentario.objects.filter(id=cm.id).exists())

    # gestor apaga o de outro (moderação)
    cm = novo_comentario(colab, 'outro do colaborador')
    excluir(cg, cm.id)
    t('gestor apaga comentário do colaborador', not MetaComentario.objects.filter(id=cm.id).exists())

    # colaborador NÃO apaga o do gestor
    cm = novo_comentario(gestor, 'comentário do gestor')
    excluir(cc, cm.id)
    t('colaborador não apaga o do gestor', MetaComentario.objects.filter(id=cm.id).exists())

    # estranho não apaga nada
    r = excluir(co, cm.id)
    t('pessoa de fora não apaga', MetaComentario.objects.filter(id=cm.id).exists())

    # GET não apaga (só POST)
    r = cc.get(f'/impulso/metas/comentario/{cm.id}/excluir/')
    t('GET não apaga (405)', r.status_code == 405 and MetaComentario.objects.filter(id=cm.id).exists(),
      r.status_code)

    # anônimo não apaga
    r = Client().post(f'/impulso/metas/comentario/{cm.id}/excluir/')
    t('anônimo não apaga', MetaComentario.objects.filter(id=cm.id).exists())

    # id inexistente não explode
    r = excluir(cg, 99999999)
    t('id inexistente devolve 404', r.status_code == 404, r.status_code)

    # botão aparece só para quem pode
    cm_g = novo_comentario(gestor, 'do gestor visível')
    cm_c = novo_comentario(colab, 'do colaborador visível')
    html_colab = cc.get(detalhe).content.decode()
    alvo_g = f'/impulso/metas/comentario/{cm_g.id}/excluir/'
    alvo_c = f'/impulso/metas/comentario/{cm_c.id}/excluir/'
    t('colaborador vê a lixeira no comentário dele', alvo_c in html_colab)
    t('colaborador NÃO vê lixeira no do gestor', alvo_g not in html_colab)
    html_gestor = cg.get(detalhe).content.decode()
    t('gestor vê lixeira nos dois', alvo_g in html_gestor and alvo_c in html_gestor)

    # comentário some da tela após excluir
    excluir(cg, cm_c.id)
    t('comentário sai da tela', alvo_c not in cg.get(detalhe).content.decode())

    # apagar comentário não apaga a meta nem os outros
    t('meta continua existindo', Meta.objects.filter(id=meta.id).exists())
    t('outros comentários continuam', MetaComentario.objects.filter(id=cm_g.id).exists())

    # nenhum template imprime comentário {# #} de várias linhas
    ruins = []
    for f in glob.glob('templates/**/*.html', recursive=True):
        txt = open(f, encoding='utf-8', errors='ignore').read()
        for m in re.finditer(r'\{#', txt):
            resto = txt[m.start():]
            fim = resto.find('#}')
            if fim == -1 or '\n' in resto[:fim]:
                ruins.append(f)
                break
    t('nenhum comentário {# #} de várias linhas', not ruins, ruins[:3])

finally:
    re_mod.enviar = _envio_real
    MetaComentario.objects.filter(id__in=criados['comentarios']).delete()
    for m in criados['metas']:
        Meta.objects.filter(id=m.id).delete()
    for u in criados['users']:
        User.objects.filter(id=u.id).delete()
    print('\nlimpeza: só o que este teste criou foi removido.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
