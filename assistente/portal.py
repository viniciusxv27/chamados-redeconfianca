"""O portal inteiro, do jeito que a pessoa vê — para o assistente ler.

Além das ferramentas próprias de cada módulo, o assistente pode abrir qualquer
página do portal **como a própria pessoa**: a mesma view roda, com o mesmo
usuário, e decide o que ele pode ver. O que voltar vira texto (tabelas viram
linhas com ``|``, links ficam como ``[texto](url)``) para o Claude ler.

Só leitura: só GET, e caminhos com cara de ação (excluir, exportar, baixar,
sincronizar...) são recusados antes de rodar qualquer view. Abrir a página é o
mesmo que a pessoa abri-la — o que a página registra ao ser aberta (ex.: "visto
por") acontece igual.
"""
import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

logger = logging.getLogger(__name__)

LIMITE_POR_PARTE = 20000

# As áreas do portal: por onde começar a procurar. Acesso é conferido ao abrir.
MAPA = [
    ('/', 'Início', 'avisos, comunicados recentes, atalhos e cartões do dia'),
    ('/dashboard/', 'Painel', 'visão geral do colaborador'),
    ('/users/profile/', 'Meu perfil', 'cadastro, setor, cargo, saldo C$'),
    ('/tickets/', 'Chamados', 'chamados abertos, atribuídos e do setor'),
    ('/communications/', 'Comunicados', 'comunicados recebidos e enviados'),
    ('/notifications/', 'Notificações', 'avisos do sino'),
    ('/agenda/', 'Agenda', 'eventos, convites e disponibilidade'),
    ('/reunioes/', 'Reuniões', 'reuniões, atas e transcrições'),
    ('/rotina-gerencial/', 'Rotina Gerencial', 'semana de atividades e avisos'),
    ('/ponto/', 'Ponto', 'marcações, jornada e saldo de horas'),
    ('/ferias/', 'Férias', 'férias marcadas e da equipe'),
    ('/folha-ponto/', 'Folha de ponto', 'folhas mensais para assinar'),
    ('/contracheque/', 'Contracheques', 'holerites'),
    ('/documentos/', 'Documentos', 'documentos para assinatura digital'),
    ('/commission/', 'Comissionamento', 'comissão por pilar, vendas, projeção'),
    ('/simulator/', 'Simulador', 'simulação de comissão e metas'),
    ('/power-bi/', 'Power BI', 'painéis'),
    ('/power-bi/metas/', 'Metas comerciais', 'metas de venda por pilar'),
    ('/vendas/', 'Vendas', 'vendas lançadas, clientes, tabela de preços'),
    ('/contestacao/', 'Contestação', 'exclusões e contestações de comissão'),
    ('/fibras/', 'Fibras', 'acompanhamento de instalações de fibra'),
    ('/validad1/', 'Validação D-1', 'conferência das vendas do dia anterior'),
    ('/sap/', 'Visão SAP', 'auditoria SAP × Vivo GO e painel'),
    ('/contagem-caixa/', 'Contagem de caixa', 'contagens e diferenças'),
    ('/cartoes/', 'Cartões corporativos', 'gastos e prestação de contas'),
    ('/purchases/', 'Compras', 'pedidos de compra'),
    ('/suppliers/', 'Fornecedores', 'cadastro de fornecedores'),
    ('/assets/', 'Inventário / almoxarifado', 'itens, pedidos e ativos'),
    ('/projects/', 'Projetos', 'projetos e tarefas'),
    ('/impulso/', 'Impulso', 'metas do Kanban, ranking, Conectar, Inovar'),
    ('/feedback/', 'Feedbacks', 'feedbacks recebidos e dados'),
    ('/compliments/', 'Elogios', 'elogios recebidos e enviados'),
    ('/prizes/', 'Mercadinho / C$', 'prêmios e saldo de Confianças'),
    ('/benefits/', 'Clube de benefícios', 'benefícios e parceiros'),
    ('/cursos/', 'Cursos Vivo', 'cursos obrigatórios e progresso'),
    ('/trilhas/', 'Trilhas de conhecimento', 'trilhas e progresso'),
    ('/trainings/', 'Treinamentos', 'materiais de treinamento'),
    ('/quiz/', 'Quiz', 'quizzes e resultados'),
    ('/checklists/', 'Checklists ADM', 'checklists das lojas'),
    ('/limpeza/', 'Limpeza', 'checklist de limpeza das lojas'),
    ('/experiencia/', 'Experiência Vivo', 'avaliações de experiência na loja'),
    ('/renova/', 'Vini Renova', 'avaliação de aparelho usado'),
    ('/files/', 'Arquivos', 'biblioteca de arquivos'),
    ('/drive/', 'Drive', 'pastas e arquivos do Drive'),
    ('/apresentacoes/', 'Apresentações', 'apresentações criadas'),
    ('/talentos/', 'Banco de talentos', 'currículos (RH)'),
    ('/users/manage/users/', 'Gestão de usuários', 'cadastro de colaboradores (gestão)'),
    ('/reports/', 'Denúncias', 'canal de denúncias'),
]

# Caminho com cara de ação (ou de arquivo) não roda: o assistente só lê.
_PROIBIDO = re.compile(
    r'(^|/|-|_)(logout|login|delete|deletar|excluir|exclui|remover|remove|apagar|apaga|cancelar|cancela|'
    r'download|baixar|export|exportar|exporta|xlsx|csv|pdf|zip|sync|sincronizar|sincroniza|atualizar|'
    r'marcar|marca|gerar|enviar|envia|aprovar|aprova|recusar|recusa|reprovar|toggle|ligar|desligar|'
    r'confirmar|responder|resolver|reabrir|duplicar|importar|import|upload|restaurar|recuperar|'
    r'liberar|retirar|desconectar|conectar|limpar|bater|registrar|criar|create|nova|novo|new|edit|editar|'
    r'acao|ajustar|salvar|save|assinar|sign|ler-todas|mark|read)(/|-|_|\.|$)',
    re.IGNORECASE)
_PREFIXOS_PROIBIDOS = ('/admin', '/media/', '/static/', '/api-auth', '/assistente/')

# Saem do texto: não é conteúdo da página.
_PULAR = {'script', 'style', 'svg', 'noscript', 'template', 'head', 'iframe', 'canvas', 'video', 'audio'}
_VAZIAS = {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'area', 'base', 'col', 'embed', 'param',
           'track', 'wbr'}
_BLOCOS = {'p', 'div', 'section', 'article', 'header', 'footer', 'aside', 'ul', 'ol', 'dl', 'dt', 'dd',
           'table', 'thead', 'tbody', 'tfoot', 'form', 'fieldset', 'details', 'summary', 'figure',
           'blockquote', 'pre', 'main', 'nav'}


class _Texto(HTMLParser):
    """HTML da página → texto legível (só o miolo, sem menu)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes = []
        self.pilha = []          # (tag, pula)
        self.pulando = 0
        self.dentro_do_miolo = False
        self.achou_miolo = False
        self.miolo_nivel = None
        self.link = None
        self.texto_link = []
        self.celulas = None

    # -- utilidades --
    def _emitir(self, texto):
        if self.pulando or not self._vale():
            return
        if self.link is not None:
            self.texto_link.append(texto)
        elif self.celulas is not None and self.celulas and self.celulas[-1] is not None:
            self.celulas[-1].append(texto)
        else:
            self.partes.append(texto)

    def _vale(self):
        return self.dentro_do_miolo or not self.achou_miolo

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id') == 'mainContent' or (tag == 'main' and not self.achou_miolo):
            self.achou_miolo = True
            self.dentro_do_miolo = True
            self.partes = []           # o que veio antes (menu lateral) não interessa
            self.miolo_nivel = len(self.pilha)
        if tag in _VAZIAS:
            if tag == 'br':
                self._emitir('\n')
            return
        classes = (attrs.get('class') or '').split()
        escondido = ('hidden' in classes or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
                     or 'sr-only' in classes)
        pula = tag in _PULAR or (escondido and tag not in ('tr', 'td', 'th'))
        self.pilha.append((tag, pula))
        if pula:
            self.pulando += 1
            return
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._emitir('\n\n' + '#' * min(int(tag[1]) + 1, 6) + ' ')
        elif tag == 'li':
            self._emitir('\n- ')
        elif tag == 'tr':
            self.celulas = []
        elif tag in ('td', 'th'):
            if self.celulas is not None:
                self.celulas.append([])
        elif tag == 'a':
            href = attrs.get('href') or ''
            if href.startswith('/') and not href.startswith('//'):
                self.link = href
                self.texto_link = []
        elif tag in _BLOCOS:
            self._emitir('\n')

    def handle_endtag(self, tag):
        if tag in _VAZIAS:
            return
        # Fecha até a tag correspondente (HTML mal fechado não desmonta tudo).
        while self.pilha:
            aberta, pula = self.pilha.pop()
            if pula:
                self.pulando -= 1
            if aberta == tag:
                break
        if self.miolo_nivel is not None and len(self.pilha) <= self.miolo_nivel and self.dentro_do_miolo \
                and tag in ('main', 'div'):
            if len(self.pilha) < self.miolo_nivel + 1:
                self.dentro_do_miolo = False
        if self.pulando:
            return
        if tag == 'a' and self.link is not None:
            texto = ' '.join(''.join(self.texto_link).split())
            href, self.link = self.link, None
            if texto:
                self._emitir(f'[{texto}]({href})')
        elif tag == 'tr' and self.celulas is not None:
            linha = [' '.join(''.join(c).split()) for c in self.celulas if c is not None]
            self.celulas = None
            if any(linha):
                self._emitir('\n| ' + ' | '.join(linha) + ' |')
        elif tag in ('td', 'th') and self.celulas is not None and self.celulas:
            pass
        elif tag in _BLOCOS or tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p'):
            self._emitir('\n')

    def handle_data(self, data):
        if data.strip():
            self._emitir(data)
        elif data:
            self._emitir(' ')

    def texto(self):
        bruto = ''.join(self.partes)
        linhas = [' '.join(l.split()) for l in bruto.split('\n')]
        saida, vazias = [], 0
        for linha in linhas:
            if not linha:
                vazias += 1
                if vazias > 1:
                    continue
            else:
                vazias = 0
            saida.append(linha)
        return '\n'.join(saida).strip()


def html_para_texto(html):
    parser = _Texto()
    try:
        parser.feed(html)
        parser.close()
    except Exception:                                           # noqa: BLE001 — HTML estranho não derruba
        logger.warning('Assistente: HTML não pôde ser lido por inteiro', exc_info=True)
    return parser.texto()


def caminho_seguro(caminho):
    """(caminho, query, erro). Recusa o que não é página do portal ou tem cara de ação."""
    caminho = (caminho or '').strip()
    if not caminho:
        return None, None, 'Informe o caminho da página (ex.: /ponto/).'
    partes = urlsplit(caminho)
    if partes.scheme or partes.netloc:
        if partes.netloc and 'redeconfianca' not in partes.netloc:
            return None, None, 'Só páginas do próprio portal.'
    path = partes.path or '/'
    if not path.startswith('/'):
        path = '/' + path
    if any(path.startswith(p) for p in _PREFIXOS_PROIBIDOS):
        return None, None, 'Essa área não é lida pelo assistente.'
    if _PROIBIDO.search(path):
        return None, None, ('Esse endereço executa uma ação (ou baixa um arquivo) — o assistente só lê páginas. '
                            'Para agir, use a ferramenta própria do módulo ou passe o link para a pessoa.')
    return path, dict(parse_qsl(partes.query, keep_blank_values=True)), None


def ler_pagina(user, caminho, parte=1):
    """Abre a página como a pessoa e devolve o texto (ou o JSON) dela, em partes."""
    from django.contrib.sessions.backends.signed_cookies import SessionStore
    from django.core.exceptions import PermissionDenied
    from django.http import Http404, StreamingHttpResponse, FileResponse
    from django.test import RequestFactory
    from django.urls import Resolver404, resolve

    from .comum import _AvisosEmMemoria

    path, query, erro = caminho_seguro(caminho)
    if erro:
        return {'erro': erro}
    try:
        rota = resolve(path)
    except Resolver404:
        if not path.endswith('/'):
            try:
                rota = resolve(path + '/')
                path += '/'
            except Resolver404:
                return {'erro': f'Página não encontrada: {path}. Veja o mapa_do_portal.'}
        else:
            return {'erro': f'Página não encontrada: {path}. Veja o mapa_do_portal.'}

    request = RequestFactory().get(path, query)
    request.user = user
    request.session = SessionStore()            # sessão em memória: nada é gravado no banco
    request._messages = _AvisosEmMemoria(request)
    request.resolver_match = rota
    try:
        resposta = rota.func(request, *rota.args, **rota.kwargs)
        if hasattr(resposta, 'render') and not getattr(resposta, 'is_rendered', True):
            resposta = resposta.render()
    except Http404:
        return {'erro': 'Não encontrado — ou você não tem acesso a isso.'}
    except PermissionDenied:
        return {'erro': 'Você não tem acesso a esta página.'}
    except Exception as exc:                                    # noqa: BLE001
        logger.exception('Assistente: página %s falhou ao abrir', path)
        return {'erro': f'A página não abriu ({type(exc).__name__}).'}

    avisos = [str(m.message) for m in request._messages]
    if isinstance(resposta, (StreamingHttpResponse, FileResponse)):
        return {'erro': 'Isso é um arquivo para baixar, não uma página — passe o link para a pessoa.'}
    if 300 <= resposta.status_code < 400:
        destino = resposta.get('Location') or ''
        return {'redirecionou_para': destino, 'avisos': avisos,
                'observacao': ('A página mandou para outro lugar — em geral é falta de acesso a ela '
                               '(ou é só a entrada de outra página; abra o destino).')}
    if resposta.status_code in (401, 403):
        return {'erro': 'Você não tem acesso a esta página.', 'avisos': avisos}
    if resposta.status_code >= 400:
        return {'erro': f'A página respondeu com erro {resposta.status_code}.', 'avisos': avisos}

    tipo = resposta.get('Content-Type') or ''
    conteudo = resposta.content.decode(resposta.charset or 'utf-8', errors='replace')
    if 'json' in tipo:
        try:
            texto = json.dumps(json.loads(conteudo), ensure_ascii=False, indent=1, default=str)
        except ValueError:
            texto = conteudo
    elif 'html' in tipo:
        texto = html_para_texto(conteudo)
    else:
        return {'erro': f'Conteúdo do tipo {tipo or "desconhecido"} — não é uma página para ler.'}

    try:
        parte = max(1, int(parte or 1))
    except (TypeError, ValueError):
        parte = 1
    total_partes = max(1, -(-len(texto) // LIMITE_POR_PARTE))
    parte = min(parte, total_partes)
    trecho = texto[(parte - 1) * LIMITE_POR_PARTE: parte * LIMITE_POR_PARTE]
    saida = {'caminho': path + (('?' + '&'.join(f'{k}={v}' for k, v in query.items())) if query else ''),
             'parte': parte, 'total_partes': total_partes, 'conteudo': trecho}
    if avisos:
        saida['avisos'] = avisos
    if total_partes > parte:
        saida['continua'] = f'Há mais conteúdo: chame ler_pagina com parte={parte + 1}.'
    return saida


def mapa():
    return [{'caminho': c, 'area': nome, 'o_que_tem': desc} for c, nome, desc in MAPA]
