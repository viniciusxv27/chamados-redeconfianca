"""E-mail institucional (/email/): de entrar com a senha até enviar.

Pedido: "Crie uma aba de email, onde o usuario logado deve poder inserir email
e senha para entrar e manter seu email institucional no portal atrelado ao
usuario dele. Crie meio que um cliente de email com a identidade visual do
Portal ... tendo visão geral de tudo. virtual host: mail.redeconfianca.com.br
(poste.io)".

O servidor de e-mail aqui é um dublê (``ServidorFalso``) que responde no mesmo
formato cru do imaplib — BODYSTRUCTURE, literais, APPENDUID — para o código de
verdade (webmail/cliente.py) ser exercitado inteiro, sem rede. O SMTP também é
dublê: nada sai daqui.

Caches em memória, transação desfeita no fim: não grava nada no banco.
"""
import os
import re
import sys
from email import message_from_bytes, policy
from email.message import EmailMessage

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-webmail'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-webmail-2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import imaplib
import smtplib
import ssl

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from webmail import cliente, seguranca
from webmail.models import ContaEmail

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


# ── O dublê do servidor ───────────────────────────────────────────────────

def _bs(parte):
    """BODYSTRUCTURE de uma parte, como o Dovecot escreveria."""
    if parte.is_multipart():
        return '(' + ''.join(_bs(p) for p in parte.get_payload()) + f' "{parte.get_content_subtype()}")'
    principal, sub = parte.get_content_maintype(), parte.get_content_subtype()
    params = []
    if parte.get_content_charset():
        params += ['"charset"', f'"{parte.get_content_charset()}"']
    nome = parte.get_param('name')
    if nome:
        params += ['"name"', f'"{nome}"']
    cid = parte.get('Content-ID')
    enc = (parte.get('Content-Transfer-Encoding') or '7bit').lower()
    bruto = parte.get_payload(decode=False)
    bruto = bruto.encode() if isinstance(bruto, str) else (bruto or b'')
    s = (f'("{principal}" "{sub}" ' + (f'({" ".join(params)})' if params else 'NIL')
         + (f' "{cid}"' if cid else ' NIL') + f' NIL "{enc}" {len(bruto)}')
    if principal == 'text':
        s += f' {bruto.count(b"\\n") + 1}'
    s += ' NIL'
    disp = parte.get_content_disposition()
    if disp:
        fn = parte.get_filename()
        s += f' ("{disp}" ' + (f'("filename" "{fn}")' if fn else 'NIL') + ')'
    else:
        s += ' NIL'
    return s + ' NIL NIL)'


def _secao(msg, secao):
    parte = msg
    for n in secao.split('.'):
        n = int(n)
        if parte.is_multipart():
            parte = parte.get_payload()[n - 1]
        elif n != 1:
            return b''
    bruto = parte.get_payload(decode=False)
    return bruto.encode() if isinstance(bruto, str) else (bruto or b'')


class Mensagem:
    def __init__(self, uid, dados, flags=(), data='09-Oct-2026 14:03:11 -0300'):
        self.uid, self.dados, self.flags, self.data = uid, dados, set(flags), data
        self.msg = message_from_bytes(dados, policy=policy.compat32)

    def texto_decodificado(self):
        moderna = message_from_bytes(self.dados, policy=policy.default)
        partes = [str(moderna.get(h, '')) for h in ('From', 'To', 'Cc', 'Subject')]
        for p in moderna.walk():
            if p.get_content_maintype() == 'text':
                partes.append(p.get_content())
        return '\n'.join(partes)

    @property
    def assunto(self):
        return str(message_from_bytes(self.dados, policy=policy.default).get('Subject', ''))


class ServidorFalso:
    def __init__(self):
        self.usuarios = {'ana.souza@redeconfianca.com.br': 'S3nh@-Çerta'}
        self.pastas = {'INBOX': [], 'Sent': [], 'Drafts': [], 'Trash': [], 'Junk': [],
                       'Projetos': [], 'Itens Exclu&AO0-dos antigos': []}
        self.especiais = {'Sent': '\\Sent', 'Drafts': '\\Drafts', 'Trash': '\\Trash', 'Junk': '\\Junk'}
        self.proximo = {p: 1 for p in self.pastas}
        self.conexoes = 0
        self.enviados = []

    def guardar(self, pasta, dados, flags=(), data=None):
        uid = self.proximo[pasta]
        self.proximo[pasta] += 1
        m = Mensagem(uid, dados, flags, data or '09-Oct-2026 14:03:11 -0300')
        self.pastas[pasta].append(m)
        return uid


SERVIDOR = ServidorFalso()


class IMAPFalso:
    abort = imaplib.IMAP4.abort
    error = imaplib.IMAP4.error

    def __init__(self, host, port, ssl_context=None, timeout=None):
        SERVIDOR.conexoes += 1
        self.capabilities = ('IMAP4REV1', 'AUTH=PLAIN', 'IDLE')
        self.literal = None
        self.sel = None
        self.sock = None
        self.usuario = None

    def authenticate(self, mecanismo, fn):
        _, usuario, senha = fn(b'').split(b'\0')
        usuario, senha = usuario.decode(), senha.decode()
        if SERVIDOR.usuarios.get(usuario) != senha:
            raise imaplib.IMAP4.error('AUTHENTICATE failed')
        self.usuario = usuario
        return 'OK', [b'Logged in']

    def capability(self):
        return 'OK', [b'IMAP4rev1 AUTH=PLAIN MOVE UIDPLUS QUOTA PREVIEW SORT']

    def noop(self):
        return 'OK', [b'NOOP completed']

    def shutdown(self):
        pass

    def logout(self):
        return 'BYE', [b'Logging out']

    @staticmethod
    def _nome(q):
        return q[1:-1].replace('\\"', '"').replace('\\\\', '\\') if q.startswith('"') else q

    def list(self, ref, padrao):
        saida = []
        for nome in SERVIDOR.pastas:
            flags = ['\\HasNoChildren'] + ([SERVIDOR.especiais[nome]] if nome in SERVIDOR.especiais else [])
            saida.append(f'({" ".join(flags)}) "/" "{nome}"'.encode())
        return 'OK', saida

    def status(self, pasta, itens):
        nome = self._nome(pasta)
        msgs = SERVIDOR.pastas.get(nome)
        if msgs is None:
            return 'NO', [b'Mailbox does not exist']
        nao = sum(1 for m in msgs if '\\Seen' not in m.flags)
        return 'OK', [f'"{nome}" (MESSAGES {len(msgs)} UNSEEN {nao} UIDNEXT {SERVIDOR.proximo[nome]})'.encode()]

    def select(self, pasta):
        nome = self._nome(pasta)
        if nome not in SERVIDOR.pastas:
            return 'NO', [b'Mailbox does not exist']
        self.sel = nome
        return 'OK', [str(len(SERVIDOR.pastas[nome])).encode()]

    def create(self, pasta):
        nome = self._nome(pasta)
        if nome in SERVIDOR.pastas:
            return 'NO', [b'exists']
        SERVIDOR.pastas[nome] = []
        SERVIDOR.proximo[nome] = 1
        return 'OK', [b'Create completed']

    def subscribe(self, pasta):
        return 'OK', [b'ok']

    def append(self, pasta, flags, data, dados):
        nome = self._nome(pasta)
        uid = SERVIDOR.guardar(nome, dados, re.findall(r'\\\w+', flags or ''))
        return 'OK', [f'[APPENDUID 1 {uid}] Append completed.'.encode()]

    def expunge(self):
        SERVIDOR.pastas[self.sel] = [m for m in SERVIDOR.pastas[self.sel] if '\\Deleted' not in m.flags]
        return 'OK', [b'']

    def getquotaroot(self, pasta):
        return 'OK', [[b'"INBOX" ""'], [b'"" (STORAGE 2048 10240)']]

    def _msgs(self, conjunto):
        uids = {int(u) for u in conjunto.split(',')}
        return [m for m in SERVIDOR.pastas[self.sel] if m.uid in uids]

    def uid(self, comando, *args):
        comando = comando.upper()
        caixa = SERVIDOR.pastas[self.sel]
        if comando == 'SORT':
            # (REVERSE ARRIVAL) UTF-8 <critérios>: a busca e depois a data de chegada.
            _, dados = self.uid('SEARCH', 'CHARSET', *args[1:])
            uids = {int(u) for u in dados[0].split()}
            achadas = [m for m in caixa if m.uid in uids]
            achadas.sort(key=lambda m: (cliente._internaldate(m.data), m.uid), reverse=True)
            return 'OK', [' '.join(str(m.uid) for m in achadas).encode()]
        if comando == 'SEARCH':
            args = list(args)
            texto = None
            if self.literal is not None:
                texto, self.literal = self.literal.decode('utf-8').lower(), None
            if args[:2] == ['CHARSET', 'UTF-8']:
                args = args[2:]
            achadas = list(caixa)
            i = 0
            while i < len(args):
                a = args[i].upper()
                if a == 'UNSEEN':
                    achadas = [m for m in achadas if '\\Seen' not in m.flags]
                elif a == 'FLAGGED':
                    achadas = [m for m in achadas if '\\Flagged' in m.flags]
                elif a == 'SINCE':
                    i += 1
                elif a == 'HEADER':
                    campo, valor = args[i + 1], args[i + 2]
                    achadas = [m for m in achadas if valor.lower() in (m.msg.get(campo) or '').lower()]
                    i += 2
                elif a == 'TEXT':
                    # Como o Dovecot: procura no texto decodificado (assunto =?utf-8?…?= incluído).
                    achadas = [m for m in achadas if texto in m.texto_decodificado().lower()]
                i += 1
            return 'OK', [' '.join(str(m.uid) for m in achadas).encode()]
        if comando == 'FETCH':
            conjunto, itens = args
            saida = []
            for seq, m in enumerate(self._msgs(conjunto), 1):
                partes = [f'{seq} (UID {m.uid}']
                literais = []
                if 'FLAGS' in itens:
                    partes.append(f' FLAGS ({" ".join(sorted(m.flags))})')
                if 'INTERNALDATE' in itens:
                    partes.append(f' INTERNALDATE "{m.data}"')
                if 'RFC822.SIZE' in itens:
                    partes.append(f' RFC822.SIZE {len(m.dados)}')
                if 'BODYSTRUCTURE' in itens:
                    partes.append(' BODYSTRUCTURE ' + _bs(m.msg))
                cabeca = m.dados.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n'
                for pedido in re.findall(r'BODY\.PEEK\[([^\]]*)\]', itens):
                    if pedido.startswith('HEADER.FIELDS'):
                        campos = re.search(r'\(([^)]*)\)', pedido).group(1).split()
                        linhas = [ln for ln in re.split(rb'\r\n(?![ \t])', cabeca)
                                  if ln.split(b':')[0].decode().upper() in campos]
                        literais.append((f'BODY[{pedido}]', b'\r\n'.join(linhas) + b'\r\n\r\n'))
                    elif pedido == 'HEADER':
                        literais.append(('BODY[HEADER]', cabeca))
                    elif pedido == '':
                        literais.append(('BODY[]', m.dados))
                    else:
                        literais.append((f'BODY[{pedido}]', _secao(m.msg, pedido)))
                if ' PREVIEW' in itens:
                    partes.append(' PREVIEW "Prévia de ' + str(m.uid) + '"')
                cabeca_txt = ''.join(partes)
                if not literais:
                    saida.append((cabeca_txt + ')').encode())
                    continue
                primeiro = True
                for nome, dado in literais:
                    prefixo = (cabeca_txt if primeiro else '') + f' {nome} {{{len(dado)}}}'
                    saida.append((prefixo.encode(), dado))
                    primeiro = False
                saida.append(b')')
            return 'OK', saida
        if comando == 'STORE':
            conjunto, modo, flags = args
            for m in self._msgs(conjunto):
                for f in re.findall(r'\\\w+', flags):
                    (m.flags.add if modo.startswith('+') else m.flags.discard)(f)
            return 'OK', [b'']
        if comando in ('MOVE', 'COPY'):
            conjunto, destino = args
            destino = self._nome(destino)
            for m in self._msgs(conjunto):
                SERVIDOR.guardar(destino, m.dados, m.flags)
            if comando == 'MOVE':
                uids = {m.uid for m in self._msgs(conjunto)}
                SERVIDOR.pastas[self.sel] = [m for m in caixa if m.uid not in uids]
            return 'OK', [b'']
        if comando == 'EXPUNGE':
            uids = {m.uid for m in self._msgs(args[0])}
            SERVIDOR.pastas[self.sel] = [m for m in caixa if not (m.uid in uids and '\\Deleted' in m.flags)]
            return 'OK', [b'']
        return 'BAD', [b'?']


class SMTPFalso:
    def __init__(self, host, port, context=None, timeout=None):
        self.sock = None

    def ehlo(self):
        return 250, b'ok'

    def login(self, usuario, senha):
        if SERVIDOR.usuarios.get(usuario) != senha:
            raise smtplib.SMTPAuthenticationError(535, b'auth failed')
        senha.encode('ascii')               # o smtplib real só aceita ASCII aqui

    def docmd(self, cmd, arg):
        return 235, b'ok'

    def send_message(self, msg, from_addr=None, to_addrs=None):
        SERVIDOR.enviados.append((msg, from_addr, list(to_addrs)))
        return {}

    def quit(self):
        pass


def mensagem_html():
    m = EmailMessage()
    m['From'] = '"Carlos Lima" <carlos@fornecedor.com>'
    m['To'] = 'ana.souza@redeconfianca.com.br, joao@redeconfianca.com.br'
    m['Cc'] = 'financeiro@redeconfianca.com.br'
    m['Subject'] = 'Proposta comercial — outubro'
    m['Date'] = 'Fri, 09 Oct 2026 14:03:11 -0300'
    m['Message-ID'] = '<proposta-1@fornecedor.com>'
    m.set_content('Segue a proposta. Veja https://exemplo.com/p')
    m.add_alternative(
        '<html><head><style>p{color:red}</style><script>alert(1)</script></head><body>'
        '<p onclick="roubar()">Olá Ana, segue a <b>proposta</b>.</p>'
        '<img src="https://rastreio.com/pixel.gif" width="1">'
        '<img src="cid:logo123">'
        '<a href="javascript:alert(1)">perigo</a> <a href="https://site.com">site</a>'
        '<form action="https://x.com"><input name="senha"></form>'
        '<iframe src="https://x.com"></iframe></body></html>', subtype='html')
    m.get_payload()[1].add_related(b'\x89PNG\r\n\x1a\nFAKEPNG', maintype='image', subtype='png', cid='<logo123>')
    m.add_attachment(b'%PDF-1.4 conteudo do pdf', maintype='application', subtype='pdf', filename='proposta.pdf')
    m.add_attachment(b'<script>alert(1)</script>', maintype='text', subtype='html', filename='pagina.html')
    return m.as_bytes()


def mensagem_texto(assunto, de='maria@redeconfianca.com.br', corpo='Oi!\n> citado\nVeja https://portal.com'):
    m = EmailMessage()
    m['From'] = de
    m['To'] = 'ana.souza@redeconfianca.com.br'
    m['Subject'] = assunto
    m['Date'] = 'Thu, 08 Oct 2026 09:00:00 -0300'
    m.set_content(corpo)
    return m.as_bytes()


# ── Os testes ─────────────────────────────────────────────────────────────

original_imap, original_smtp = imaplib.IMAP4_SSL, smtplib.SMTP_SSL
imaplib.IMAP4_SSL = IMAPFalso
smtplib.SMTP_SSL = SMTPFalso

marcador = transaction.atomic()
marcador.__enter__()
try:
    ana = User.objects.create_user(
        username='zz.webmail.ana', email='zz.ana.pessoal@exemplo-teste.local', password='S3nha!teste',
        first_name='Ana', last_name='Souza', job_title='Analista')
    beto = User.objects.create_user(
        username='zz.webmail.beto', email='zz.beto.pessoal@exemplo-teste.local', password='S3nha!teste',
        first_name='Beto', last_name='Reis')
    c = Client()
    c.force_login(ana)

    print('== SEGURANÇA ==')
    token = seguranca.cifrar('S3nh@-Çerta')
    t('a senha cifrada não contém a senha', 'S3nh@' not in token and len(token) > 40)
    t('e volta igual', seguranca.decifrar(token) == 'S3nh@-Çerta')
    t('token estragado vira vazio (pede para entrar de novo)', seguranca.decifrar(token[:-4] + 'AAAA') == '')

    class SockFalso:
        def __init__(self, der):
            self.der = der

        def getpeercert(self, binary_form=False):
            return self.der

        def close(self):
            pass

    import hashlib
    der_bom = b'certificado-do-poste'
    settings.WEBMAIL_TLS_SHA256 = hashlib.sha256(der_bom).hexdigest()
    cliente._USA_PINO.clear()

    def recusa(ctx):
        raise ssl.SSLCertVerificationError('self-signed certificate')
    con = cliente._abrir_tls(recusa, lambda ctx: SockFalso(der_bom), lambda con: con, 'h1')
    t('certificado autoassinado com o pino certo: conecta', isinstance(con, SockFalso))
    try:
        cliente._USA_PINO.clear()
        cliente._abrir_tls(recusa, lambda ctx: SockFalso(b'outro-certificado'), lambda con: con, 'h2')
        t('certificado diferente do pino: recusa', False)
    except cliente.ServidorFora:
        t('certificado diferente do pino: recusa (antes de mandar a senha)', True)
    settings.WEBMAIL_TLS_SHA256 = ''
    try:
        cliente._USA_PINO.clear()
        cliente._abrir_tls(recusa, lambda ctx: SockFalso(der_bom), lambda con: con, 'h3')
        t('sem pino configurado: não aceita certificado inválido', False)
    except cliente.ServidorFora:
        t('sem pino configurado: não aceita certificado inválido', True)

    print('\n== ENTRAR ==')
    r = c.get('/email/')
    t('sem conta: mostra a tela de conectar', r.status_code == 200 and 'Conectar meu e-mail' in r.content.decode())
    r = c.post('/email/conectar/', {'email': 'ana@gmail.com', 'senha': 'x'}, follow=True)
    t('domínio de fora: recusa', 'institucional' in r.content.decode() and not ContaEmail.objects.filter(usuario=ana).exists())
    r = c.post('/email/conectar/', {'email': 'ana.souza@redeconfianca.com.br', 'senha': 'errada'}, follow=True)
    t('senha errada: recusa sem gravar', 'não conferem' in r.content.decode() and not ContaEmail.objects.filter(usuario=ana).exists())
    r = c.post('/email/conectar/', {'email': 'Ana.Souza@redeconfianca.com.br', 'senha': 'S3nh@-Çerta'}, follow=True)
    conta = ContaEmail.objects.filter(usuario=ana).first()
    t('senha certa (com acento): grava a conta', conta is not None and conta.email == 'ana.souza@redeconfianca.com.br')
    t('a senha no banco está cifrada', conta and 'S3nh@' not in conta.senha_cifrada and conta.senha() == 'S3nh@-Çerta')
    t('depois de entrar, abre a caixa', 'id="wmApp"' in r.content.decode() and 'wm-config' in r.content.decode())
    t('o menu do portal tem o E-mail', 'href="/email/"' in r.content.decode())

    b = Client()
    b.force_login(beto)
    for _ in range(5):
        b.post('/email/conectar/', {'email': 'beto@redeconfianca.com.br', 'senha': 'chute'})
    r = b.post('/email/conectar/', {'email': 'beto@redeconfianca.com.br', 'senha': 'chute'}, follow=True)
    t('5 senhas erradas seguidas: espera 15 minutos', 'Espere 15 minutos' in r.content.decode())
    r = b.get('/email/api/lista/?pasta=INBOX')
    t('quem não conectou não lê caixa nenhuma (401)', r.status_code == 401 and r.json().get('reconectar'))

    print('\n== CAIXA ==')
    u_html = SERVIDOR.guardar('INBOX', mensagem_html())
    u_txt = SERVIDOR.guardar('INBOX', mensagem_texto('Reunião de segunda'), flags=['\\Seen'])
    SERVIDOR.guardar('INBOX', mensagem_texto('Café amanhã?', de='"João Pedro" <joao@redeconfianca.com.br>'), flags=['\\Flagged'])
    SERVIDOR.guardar('Projetos', mensagem_texto('Projeto X'))

    d = c.get('/email/api/pastas/').json()
    nomes = [p['nome'] for p in d['pastas']]
    t('pastas com nome em português e na ordem certa',
      nomes[:6] == ['Caixa de entrada', 'Rascunhos', 'Enviados', 'Spam', 'Lixeira', 'Itens Excluídos antigos'], nomes)
    entrada = d['pastas'][0]
    t('contagem de não lidas', entrada['nao_lidas'] == 2 and entrada['total'] == 3, entrada)
    t('pasta com acento decodificada (UTF-7)', any(p['nome'] == 'Itens Excluídos antigos' and p['id'] == 'Itens Exclu&AO0-dos antigos' for p in d['pastas']))

    d = c.get('/email/api/lista/?pasta=INBOX').json()
    t('lista mais recente primeiro', [m['uid'] for m in d['mensagens']] == [3, 2, 1], [m['uid'] for m in d['mensagens']])
    m1 = next(m for m in d['mensagens'] if m['uid'] == u_html)
    t('remetente, assunto e prévia', m1['de']['nome'] == 'Carlos Lima' and m1['assunto'] == 'Proposta comercial — outubro'
      and m1['previa'].startswith('Prévia'), m1)
    t('marca de anexo', m1['anexo'] and not next(m for m in d['mensagens'] if m['uid'] == u_txt)['anexo'])
    t('lida/não lida e sinalizada', not m1['lida'] and next(m for m in d['mensagens'] if m['uid'] == 3)['sinalizada'])
    d = c.get('/email/api/lista/?pasta=INBOX&filtro=nao_lidas').json()
    t('filtro não lidas', sorted(m['uid'] for m in d['mensagens']) == [1, 3])
    d = c.get('/email/api/lista/?pasta=INBOX&busca=café').json()
    t('busca com acento (literal UTF-8)', [m['uid'] for m in d['mensagens']] == [3], d.get('mensagens'))
    d = c.get('/email/api/lista/?pasta=INBOX&filtro=anexos').json()
    t('filtro com anexo', [m['uid'] for m in d['mensagens']] == [u_html])

    print('\n== LEITURA ==')
    d = c.get(f'/email/api/mensagem/?pasta=INBOX&uid={u_html}').json()['mensagem']
    doc = d['documento']
    t('script, iframe e formulário removidos', '<script' not in doc and '<iframe' not in doc and '<form' not in doc and '<input' not in doc)
    t('onclick e javascript: removidos', 'onclick' not in doc and 'javascript:' not in doc)
    t('imagem de rastreio bloqueada', not re.search(r'(?<![-\w])src="https://rastreio', doc) and 'data-wm-src="https://rastreio.com/pixel.gif"' in doc
      and d['imagens_bloqueadas'])
    t('imagem do próprio e-mail (cid) embutida', 'src="data:image/png;base64,' in doc)
    t('CSP sem rede para imagens', "img-src data: blob:;" in doc)
    t('link externo abre em outra aba sem referer', 'href="https://site.com"' in doc and 'noopener' in doc)
    nomes_anexos = sorted(a['nome'] for a in d['anexos'])
    t('anexos listados (sem a imagem do corpo)', nomes_anexos == ['pagina.html', 'proposta.pdf'], nomes_anexos)
    t('para, cc e Message-ID', len(d['para']) == 2 and d['cc'][0]['email'] == 'financeiro@redeconfianca.com.br'
      and d['message_id'] == '<proposta-1@fornecedor.com>')
    t('abrir marca como lida no servidor', '\\Seen' in SERVIDOR.pastas['INBOX'][0].flags)
    d2 = c.get(f'/email/api/mensagem/?pasta=INBOX&uid={u_html}&imagens=1').json()['mensagem']
    t('pedir as imagens libera', 'src="https://rastreio.com/pixel.gif"' in d2['documento'] and not d2['imagens_bloqueadas'])
    d3 = c.get(f'/email/api/mensagem/?pasta=INBOX&uid={u_txt}').json()['mensagem']
    t('texto puro: link clicável e citação recuada', 'href="https://portal.com"' in d3['documento'] and 'wm-citado' in d3['documento'])

    pdf = next(a for a in d['anexos'] if a['nome'] == 'proposta.pdf')
    r = c.get(f'/email/anexo/?pasta=INBOX&uid={u_html}&secao={pdf["secao"]}')
    t('baixar anexo devolve o arquivo decodificado', r.status_code == 200 and r.content == b'%PDF-1.4 conteudo do pdf'
      and 'attachment' in r['Content-Disposition'])
    r = c.get(f'/email/anexo/?pasta=INBOX&uid={u_html}&secao={pdf["secao"]}&inline=1')
    t('PDF pode abrir no navegador', 'inline' in r['Content-Disposition'] and r['Content-Type'] == 'application/pdf')
    html_anexo = next(a for a in d['anexos'] if a['nome'] == 'pagina.html')
    r = c.get(f'/email/anexo/?pasta=INBOX&uid={u_html}&secao={html_anexo["secao"]}&inline=1')
    t('anexo HTML nunca abre no portal (só baixa)', 'attachment' in r['Content-Disposition'] and r['X-Content-Type-Options'] == 'nosniff')
    r = c.get(f'/email/original/?pasta=INBOX&uid={u_txt}')
    t('baixar o original (.eml)', r.status_code == 200 and b'Reuni' in r.content)
    r = c.get('/email/api/mensagem/?pasta=INBOX&uid=999')
    t('mensagem que não existe: 404', r.status_code == 404)

    print('\n== AÇÕES ==')
    j = lambda **kw: c.post('/email/api/acao/', data=__import__('json').dumps(kw), content_type='application/json')
    j(pasta='INBOX', uids=[u_txt], acao='nao_lida')
    t('marcar como não lida', '\\Seen' not in next(m for m in SERVIDOR.pastas['INBOX'] if m.uid == u_txt).flags)
    j(pasta='INBOX', uids=[3], acao='mover', destino='Projetos')
    t('mover para pasta', [m.assunto for m in SERVIDOR.pastas['Projetos']] == ['Projeto X', 'Café amanhã?'],
      [m.assunto for m in SERVIDOR.pastas['Projetos']])
    j(pasta='INBOX', uids=[u_txt], acao='lixeira')
    t('excluir vai para a lixeira', len(SERVIDOR.pastas['Trash']) == 1 and len(SERVIDOR.pastas['INBOX']) == 1)
    r = j(pasta='INBOX', uids=[u_html], acao='arquivo')
    t('arquivar cria a pasta Arquivo quando falta', r.status_code == 200 and len(SERVIDOR.pastas.get('Archive', [])) == 1)
    j(pasta='Trash', uids=[1], acao='lixeira')
    t('excluir dentro da lixeira apaga de vez', SERVIDOR.pastas['Trash'] == [])
    r = j(pasta='INBOX', uids=['1;DROP'], acao='lida')
    t('UID que não é número é recusado', r.status_code == 400)

    print('\n== ESCREVER ==')
    from django.core.files.uploadedfile import SimpleUploadedFile
    SERVIDOR.guardar('INBOX', mensagem_html())
    original_uid = SERVIDOR.pastas['INBOX'][-1].uid
    rasc = c.post('/email/api/rascunho/', {'para': 'maria@redeconfianca.com.br', 'assunto': 'Rascunho 1',
                                           'corpo': '<p>meio escrito</p>'}).json()
    t('salvar rascunho guarda em Rascunhos com \\Draft', rasc['ok'] and len(SERVIDOR.pastas['Drafts']) == 1
      and '\\Draft' in SERVIDOR.pastas['Drafts'][0].flags)
    rasc2 = c.post('/email/api/rascunho/', {'para': 'maria@redeconfianca.com.br', 'assunto': 'Rascunho 1',
                                            'corpo': '<p>mais escrito</p>', 'rascunho_uid': rasc['uid']}).json()
    t('salvar de novo troca o rascunho (não duplica)', len(SERVIDOR.pastas['Drafts']) == 1 and rasc2['uid'] != rasc['uid'])

    r = c.post('/email/api/enviar/', {
        'para': '"Maria" <maria@redeconfianca.com.br>, carlos@fornecedor.com', 'cc': 'joao@redeconfianca.com.br',
        'cco': 'chefe@redeconfianca.com.br', 'assunto': 'Re: Proposta comercial — outubro',
        'corpo': '<p>Fechado! <script>x()</script><b>Obrigada</b></p>',
        'anexos': [SimpleUploadedFile('contrato.pdf', b'%PDF contrato', content_type='application/pdf')],
        'responde_a': '<proposta-1@fornecedor.com>', 'respondida_pasta': 'INBOX', 'respondida_uid': str(original_uid),
        'rascunho_uid': str(rasc2['uid']),
    })
    t('enviar responde ok', r.status_code == 200 and r.json()['ok'], r.content[:200])
    enviada, remetente, destinos = SERVIDOR.enviados[-1]
    t('sai com o nome e o e-mail da pessoa', 'Ana Souza' in enviada['From'] and remetente == 'ana.souza@redeconfianca.com.br')
    t('para, cc e cco recebem', sorted(destinos) == sorted(['maria@redeconfianca.com.br', 'carlos@fornecedor.com',
                                                           'joao@redeconfianca.com.br', 'chefe@redeconfianca.com.br']))
    t('cco não aparece para quem recebe', enviada['Bcc'] is None and 'chefe@' not in enviada.as_string())
    t('responde no mesmo fio (In-Reply-To)', enviada['In-Reply-To'] == '<proposta-1@fornecedor.com>')
    corpo_html = enviada.get_body(('html',)).get_content()
    t('o corpo vai limpo, com texto alternativo', '<script' not in corpo_html and '<b>Obrigada</b>' in corpo_html
      and 'Fechado!' in enviada.get_body(('plain',)).get_content())
    t('o anexo vai junto', [p.get_filename() for p in enviada.iter_attachments()] == ['contrato.pdf'])
    copia = SERVIDOR.pastas['Sent'][-1]
    t('cópia em Enviados, lida, com o Cco para a própria pessoa', '\\Seen' in copia.flags and 'chefe@' in (copia.msg['Bcc'] or ''))
    t('o rascunho some depois do envio', SERVIDOR.pastas['Drafts'] == [])
    t('o original fica como respondido', '\\Answered' in SERVIDOR.pastas['INBOX'][-1].flags)

    r = c.post('/email/api/enviar/', {'para': '', 'assunto': 'x', 'corpo': 'y'})
    t('sem destinatário: recusa', r.status_code == 400 and 'destinatário' in r.json()['erro'])
    r = c.post('/email/api/enviar/', {'para': 'nao-e-email', 'assunto': 'x', 'corpo': 'y'})
    t('endereço inválido: recusa', r.status_code == 400 and 'inválido' in r.json()['erro'])

    antes = len(SERVIDOR.enviados)
    r = c.post('/email/api/enviar/', {'para': 'carlos@fornecedor.com', 'assunto': 'Enc: Proposta', 'corpo': '<p>segue</p>',
                                      'encaminhar_pasta': 'INBOX', 'encaminhar_uid': str(original_uid),
                                      'encaminhar_anexo': 'proposta.pdf'})
    enviada = SERVIDOR.enviados[-1][0]
    t('encaminhar leva o anexo escolhido do original', r.status_code == 200 and len(SERVIDOR.enviados) == antes + 1
      and [p.get_filename() for p in enviada.iter_attachments()] == ['proposta.pdf'])

    print('\n== VISÃO GERAL, NOVIDADES E CONTATOS ==')
    d = c.get('/email/api/resumo/').json()
    t('resumo com contagens', d['ok'] and d['total'] == len(SERVIDOR.pastas['INBOX']) and 'serie' in d and len(d['serie']) == 14)
    t('quota do servidor', d['quota'] and d['quota']['limite'] == 10240 * 1024)
    t('quem mais escreveu', d['remetentes'] and d['remetentes'][0]['email'] == 'carlos@fornecedor.com')
    d = c.get('/email/api/novidades/').json()
    t('novidades devolvem o próximo UID', d['uidnext'] == SERVIDOR.proximo['INBOX'])
    ContaEmail.objects.create(usuario=beto, email='beto.reis@redeconfianca.com.br', senha_cifrada=seguranca.cifrar('x'))
    d = c.get('/email/api/contatos/?q=bet').json()
    t('contatos: quem tem e-mail ligado no portal', [x['email'] for x in d['contatos']] == ['beto.reis@redeconfianca.com.br'])

    print('\n== SENHA TROCADA NO SERVIDOR ==')
    conexoes = SERVIDOR.conexoes
    c.get('/email/api/pastas/')
    t('a conexão aberta é reaproveitada', SERVIDOR.conexoes == conexoes)
    SERVIDOR.usuarios['ana.souza@redeconfianca.com.br'] = 'senha-nova'
    cliente.esquecer(conta.pk)
    r = c.get('/email/api/pastas/')
    conta.refresh_from_db()
    t('senha recusada: pede para entrar de novo', r.status_code == 401 and r.json().get('reconectar') and conta.senha_recusada)
    r = c.get('/email/')
    t('e a tela avisa que a senha mudou', 'A senha do e-mail mudou' in r.content.decode())
    c.post('/email/conectar/', {'email': 'ana.souza@redeconfianca.com.br', 'senha': 'senha-nova'})
    conta.refresh_from_db()
    t('entrar com a nova volta a funcionar', not conta.senha_recusada and c.get('/email/api/pastas/').status_code == 200)

    print('\n== PREFERÊNCIAS E SAIR ==')
    r = c.post('/email/preferencias/', {'nome_exibicao': 'Ana S.', 'assinatura': '<p>Ana<img src=x onerror=alert(1)></p>'})
    conta.refresh_from_db()
    t('assinatura salva e limpa', r.json()['ok'] and 'onerror' not in conta.assinatura and conta.nome_exibicao == 'Ana S.')
    c.post('/email/desconectar/')
    t('desconectar apaga a conta e a senha', not ContaEmail.objects.filter(usuario=ana).exists())
finally:
    imaplib.IMAP4_SSL, smtplib.SMTP_SSL = original_imap, original_smtp
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
