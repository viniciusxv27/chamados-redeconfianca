"""Ponte da Automação SAP (24/11/2026): sempre a versão publicada mais atual.

Pedido: "Sempre faça a ponte usar a versão mais atual publicada, está usando versões
anteriores". A ponte só baixava o app quando alguém clicava em instalar e nunca mais
olhava; uma instalação antiga achada no computador (sem versao_ponte.txt) era usada
como estava.

Cobre (sem rede: o portal é um dublê, o pacote é um zip montado aqui):
- instalação antiga sem número e app fechado: troca pela publicada, na mesma pasta,
  sem mexer em config/ e logs/;
- já na publicada: não baixa de novo; publicada nova: atualiza;
- app aberto: não mexe (atualiza na próxima abertura) e "Abrir"/"Reiniciar" pelo portal
  instalam antes de abrir;
- portal fora do ar, sem chave ou sem portal (teste): segue com o que tem;
- o pedido da página logo depois da ponte não baixa duas vezes;
- a página (ponte.js) instala a publicada antes de abrir com qualquer ponte, e passa no
  node --check.

    py teste_ponte_versao.py
"""
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import zipfile
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, 'ponte_sap'))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='zz-ponte-versao-')

import ponte  # noqa: E402

ok = falhas = 0


def t(nome, cond, extra=''):
    global ok, falhas
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        falhas += 1
        print(f'  FALHA {nome} {extra}')


def pacote(versao):
    """O zip publicado: AutomacaoSAP/ com o .exe, um arquivo do app e uma config "de fábrica"."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('AutomacaoSAP/AutomacaoSAP.exe', f'exe {versao}')
        z.writestr('AutomacaoSAP/app.txt', f'app {versao}')
        z.writestr('AutomacaoSAP/config/sap.json', '{"de_fabrica": true}')
    return buf.getvalue()


class Portal:
    """Dublê do portal: /api/versao/ e o download do zip publicado."""

    def __init__(self, versao):
        self.publicar(versao)
        self.pedidos = []
        self.fora = False

    def publicar(self, versao):
        self.versao, self.zip = versao, pacote(versao)

    def __call__(self, url, chave, tempo=30):
        self.pedidos.append(url)
        if self.fora:
            raise OSError('portal fora do ar')
        assert chave == 'chave-zz'
        if '/api/versao/' in url:
            corpo = json.dumps({'versao': self.versao, 'url': f'https://portal.zz/baixar/{self.versao}/',
                                'sha256': hashlib.sha256(self.zip).hexdigest(), 'tamanho': len(self.zip)}).encode()
            return _Resposta(corpo)
        return _Resposta(self.zip)

    def downloads(self):
        return sum(1 for u in self.pedidos if '/baixar/' in u)


class _Resposta(io.BytesIO):
    def __init__(self, dados):
        super().__init__(dados)
        self.headers = {'Content-Length': str(len(dados))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def nova_config(pasta_app):
    cfg = ponte.Config(os.path.join(os.environ['LOCALAPPDATA'], f'cfg-{os.urandom(3).hex()}.json'),
                       portal='https://portal.zz')
    cfg.dados['chave'] = 'chave-zz'
    cfg.dados['pasta_app'] = pasta_app
    return cfg


# Uma instalação antiga, feita à mão, sem versao_ponte.txt, com a config da loja.
antiga = pathlib.Path(tempfile.mkdtemp(prefix='zz-automacao-antiga-'))
(antiga / 'AutomacaoSAP.exe').write_text('exe 1.3.1')
(antiga / 'app.txt').write_text('app 1.3.1')
(antiga / 'config').mkdir()
(antiga / 'config' / 'sap.json').write_text('{"loja": "Anchieta"}')
(antiga / 'logs').mkdir()
(antiga / 'logs' / 'hoje.log').write_text('log da loja')

portal = Portal('2.1.1')
mock.patch.object(ponte, '_pedir', side_effect=portal).start()
config = nova_config(str(antiga))
app = ponte.Aplicativo(config)
rodando = {'sim': False}
mock.patch.object(app, 'rodando', side_effect=lambda: rodando['sim']).start()

print('== 1) INSTALAÇÃO ANTIGA, APP FECHADO ==')
t('antes: a ponte acha a instalação antiga, sem número', app.pasta() == str(antiga) and app.versao_instalada() is None)
publicada = ponte.garantir_versao_atual(app, config)
t('troca pela publicada (2.1.1)', publicada == '2.1.1' and app.versao_instalada() == '2.1.1'
  and (antiga / 'app.txt').read_text() == 'app 2.1.1', app.versao_instalada())
t('na mesma pasta (a da loja)', app.pasta() == str(antiga))
t('a config e os logs da loja ficam', (antiga / 'config' / 'sap.json').read_text() == '{"loja": "Anchieta"}'
  and (antiga / 'logs' / 'hoje.log').read_text() == 'log da loja')
t('a ponte informa a publicada ao portal', ponte.Ponte(config, app).estado()['app']['publicada'] == '2.1.1')

print('\n== 2) JÁ NA PUBLICADA / PUBLICADA NOVA ==')
antes = portal.downloads()
ponte.garantir_versao_atual(app, config)
t('já na publicada: só pergunta, não baixa', portal.downloads() == antes)
t('pergunta mandando a instalada', portal.pedidos[-1].endswith('?atual=2.1.1'), portal.pedidos[-1])
portal.publicar('2.2.0')
ponte.garantir_versao_atual(app, config)
t('publicaram a 2.2.0: atualiza', app.versao_instalada() == '2.2.0' and (antiga / 'app.txt').read_text() == 'app 2.2.0')

print('\n== 3) APP ABERTO ==')
portal.publicar('2.3.0')
rodando['sim'] = True
ponte.garantir_versao_atual(app, config)
t('app aberto: não mexe nos arquivos', app.versao_instalada() == '2.2.0' and app.publicada == '2.3.0')
abertos = []
mock.patch.object(app, 'iniciar', side_effect=lambda: abertos.append(app.versao_instalada()) or True).start()
mock.patch.object(app, 'encerrar', side_effect=lambda esperar=6: rodando.update(sim=False)).start()
sessao = ponte.Sessao.__new__(ponte.Sessao)
sessao.app, sessao.ponte = app, ponte.Ponte(config, app)
sessao._tratar({'t': 'reiniciar'})
t('"Reiniciar" pelo portal fecha, atualiza e abre a publicada', abertos == ['2.3.0'], abertos)
portal.publicar('2.4.0')
rodando['sim'] = False
sessao._tratar({'t': 'iniciar'})
t('"Abrir" pelo portal com o app fechado: instala a publicada antes de abrir', abertos[-1] == '2.4.0', abertos)

print('\n== 4) SEM PORTAL, SEM CHAVE, FORA DO AR ==')
portal.publicar('2.5.0')
portal.fora = True
t('portal fora do ar: segue com a instalada', ponte.garantir_versao_atual(app, config) is None
  and app.versao_instalada() == '2.4.0')
portal.fora = False
sem_chave = nova_config(str(antiga))
sem_chave.dados['chave'] = ''
n = len(portal.pedidos)
t('ponte ainda não pareada: não pergunta nada', ponte.garantir_versao_atual(app, sem_chave) is None
  and len(portal.pedidos) == n)
teste = nova_config(str(antiga))
teste.sem_portal = True
t('modo de teste (--sem-portal): não pergunta nada', ponte.garantir_versao_atual(app, teste) is None
  and len(portal.pedidos) == n)

print('\n== 5) PÁGINA E PONTE PEDINDO JUNTAS ==')
ponte.garantir_versao_atual(app, config)
antes = portal.downloads()
ponte.instalar_app(app, config)
t('o "instalar" da página logo depois não baixa de novo', portal.downloads() == antes
  and app.versao_instalada() == '2.5.0')
portal.publicar('2.6.0')
ponte.instalar_app(app, config)
t('mas com versão nova publicada, o "instalar" baixa', app.versao_instalada() == '2.6.0')

print('\n== 6) A PÁGINA DO PORTAL (ponte.js) ==')
js = pathlib.Path(AQUI, 'static', 'auditoria_sap', 'ponte.js').read_text(encoding='utf-8')
t('a página compara a instalada com a publicada e instala antes de abrir',
  'function desatualizado(' in js and 'atualizarApp(true)' in js and "'/app/instalar'" in js)
t('não insiste em loop se a instalação falhar', 'falhouEm = PUBLICADA' in js)
html = pathlib.Path(AQUI, 'templates', 'auditoria_sap', 'automacao.html').read_text(encoding='utf-8')
t('o cache do navegador não segura o JS antigo', "ponte.js' %}?v=5" in html)
try:
    chk = subprocess.run(['node', '--check', os.path.join(AQUI, 'static', 'auditoria_sap', 'ponte.js')],
                         capture_output=True, text=True, timeout=30)
    t('ponte.js passa no node --check', chk.returncode == 0, chk.stderr[:300])
except FileNotFoundError:
    print('  (node não encontrado: pulei o --check)')
t('a ponte nova é a 1.1.0 (o instalador gerado no Easypanel leva o número)', ponte.VERSAO == '1.1.0')

print(f'\n{ok} OK, {falhas} falha(s)')
sys.exit(1 if falhas else 0)
