"""As regras que transformam o que a IA viu em alerta. Sem câmera nem rede, para dar para testar.

deteccao: {'classe': 'person' | 'cell phone', 'caixa': (x, y, w, h) em fração da imagem, 'conf': 0-1}
zona:     {'nome': ..., 'x': ..., 'y': ..., 'w': ..., 'h': ...}, em fração da imagem

Não há reconhecimento facial nem identificação de pessoa: só "tem alguém aqui" e "o aparelho sumiu".
"""
from dataclasses import dataclass, field

CONF_PESSOA = 0.5
CONF_APARELHO = 0.35
QUADROS_FORA_HORARIO = 3      # uma detecção só pode ser sombra ou reflexo
SEGUNDOS_PERMANENCIA = 300
SEGUNDOS_APARELHO_FIRME = 30  # aparelho "é do mostruário" depois de ficar parado este tempo
SEGUNDOS_APARELHO_SUMIDO = 20
GRADE = 12                    # posição do aparelho arredondada, para a caixa tremida ser o mesmo aparelho
ESPERA_REPETIR = {'FORA_HORARIO': 600, 'PERMANENCIA': 600, 'ITEM_SUMIU': 120}


@dataclass
class Evento:
    tipo: str
    confianca: int
    motivo: str


def _centro(caixa):
    x, y, w, h = caixa
    return x + w / 2, y + h / 2


def _dentro(ponto, zona):
    return zona['x'] <= ponto[0] <= zona['x'] + zona['w'] and zona['y'] <= ponto[1] <= zona['y'] + zona['h']


def _cobre(caixa, ponto, folga=0.03):
    x, y, w, h = caixa
    return x - folga <= ponto[0] <= x + w + folga and y - folga <= ponto[1] <= y + h + folga


@dataclass
class _Aparelho:
    centro: tuple
    zona: str
    primeiro: float
    ultimo: float
    firme: bool = False
    pessoa_perto_em: float = 0.0


@dataclass
class Analisador:
    zonas: list = field(default_factory=list)
    permanencia_segundos: int = SEGUNDOS_PERMANENCIA
    _quadros_fechada: int = 0
    _zona_desde: dict = field(default_factory=dict)
    _aparelhos: dict = field(default_factory=dict)
    _ultimo_evento: dict = field(default_factory=dict)

    def _emitir(self, eventos, agora, chave, evento):
        if agora - self._ultimo_evento.get(chave, -1e12) >= ESPERA_REPETIR[evento.tipo]:
            self._ultimo_evento[chave] = agora
            eventos.append(evento)

    def analisar(self, deteccoes, agora, aberta=True):
        """`agora` em segundos. Devolve os eventos deste quadro."""
        pessoas = [d['caixa'] for d in deteccoes if d['classe'] == 'person' and d['conf'] >= CONF_PESSOA]
        aparelhos = [d['caixa'] for d in deteccoes if d['classe'] == 'cell phone' and d['conf'] >= CONF_APARELHO]
        eventos = []
        self._fora_do_horario(eventos, pessoas, agora, aberta)
        if aberta:
            self._permanencia(eventos, pessoas, agora)
        else:
            self._zona_desde.clear()
        self._aparelhos_sumidos(eventos, aparelhos, pessoas, agora)
        return eventos

    def _fora_do_horario(self, eventos, pessoas, agora, aberta):
        self._quadros_fechada = self._quadros_fechada + 1 if pessoas and not aberta else 0
        if self._quadros_fechada >= QUADROS_FORA_HORARIO:
            self._emitir(eventos, agora, 'FORA_HORARIO', Evento('FORA_HORARIO', 85, 'Pessoa detectada com a loja fechada'))

    def _permanencia(self, eventos, pessoas, agora):
        for zona in self.zonas:
            nome = zona.get('nome', 'área')
            if any(_dentro(_centro(p), zona) for p in pessoas):
                desde = self._zona_desde.setdefault(nome, agora)
                minutos = (agora - desde) / 60
                if agora - desde >= self.permanencia_segundos:
                    self._emitir(eventos, agora, f'PERMANENCIA:{nome}',
                                 Evento('PERMANENCIA', 45, f'Pessoa há {minutos:.0f} min em {nome}'))
            else:
                self._zona_desde.pop(nome, None)

    def _aparelhos_sumidos(self, eventos, aparelhos, pessoas, agora):
        vistos = set()
        for caixa in aparelhos:
            centro = _centro(caixa)
            zona = next((z for z in self.zonas if _dentro(centro, z)), None)
            if not zona:
                continue
            chave = (round(centro[0] * GRADE), round(centro[1] * GRADE))
            vistos.add(chave)
            item = self._aparelhos.get(chave) or _Aparelho(centro, zona.get('nome', 'área'), agora, agora)
            item.ultimo = agora
            item.firme = item.firme or agora - item.primeiro >= SEGUNDOS_APARELHO_FIRME
            self._aparelhos[chave] = item

        for chave, item in list(self._aparelhos.items()):
            if chave in vistos:
                continue
            # Com alguém na frente, o aparelho pode só estar encoberto: o relógio começa quando a pessoa sai.
            if any(_cobre(p, item.centro) for p in pessoas):
                item.pessoa_perto_em = agora
                continue
            if not item.firme:
                if agora - item.ultimo > SEGUNDOS_APARELHO_SUMIDO:
                    del self._aparelhos[chave]
                continue
            if item.pessoa_perto_em >= item.ultimo and agora - item.pessoa_perto_em >= SEGUNDOS_APARELHO_SUMIDO:
                self._emitir(eventos, agora, f'ITEM_SUMIU:{chave}',
                             Evento('ITEM_SUMIU', 70, f'Aparelho saiu de {item.zona} depois de uma pessoa passar por ali'))
                del self._aparelhos[chave]
            elif item.pessoa_perto_em < item.ultimo and agora - item.ultimo > SEGUNDOS_APARELHO_SUMIDO * 3:
                # Sumiu sem ninguém por perto: foi a detecção que falhou, não é furto.
                del self._aparelhos[chave]
        return eventos


# ---------------------------------------------------------------------------
# Medições da loja: entradas pela porta, matinal e tempo na mesa do vendedor.
# Sem identificar ninguém: o rastreador só sabe que o ponto de agora é o mesmo de um instante atrás.
# ---------------------------------------------------------------------------
ZONA_MOSTRUARIO, ZONA_PORTA, ZONA_MESA, ZONA_TELA = 'MOSTRUARIO', 'PORTA', 'MESA', 'TELA'
DISTANCIA_MAXIMA = 0.18       # fração da imagem: mais longe que isso, entre dois quadros, é outra pessoa
TRACO_EXPIRA = 3              # segundos sem ver a pessoa
MESA_MINIMO = 60              # menos que isso é alguém de passagem, não atendimento
MESA_FOLGA = 45               # o detector perde a pessoa por instantes: não encerra o atendimento por isso
MATINAL_PESSOAS = 3           # "mais de 2 pessoas"
MATINAL_FOLGA = 60


def zonas_do_tipo(zonas, tipo):
    # Área marcada antes de existirem os tipos era sempre mostruário.
    return [z for z in zonas if z.get('tipo', ZONA_MOSTRUARIO) == tipo]


def pes(caixa):
    x, y, w, h = caixa
    return x + w / 2, y + h


@dataclass
class Rastreador:
    """Liga cada pessoa à posição do quadro anterior, pela distância. Devolve {id: ponto}."""
    _proximo: int = 1
    _tracos: dict = field(default_factory=dict)

    def atualizar(self, pontos, agora):
        for traco_id in [i for i, (_p, visto) in self._tracos.items() if agora - visto > TRACO_EXPIRA]:
            del self._tracos[traco_id]
        pares = sorted(((abs(p[0] - t[0][0]) + abs(p[1] - t[0][1]), n, i) for n, p in enumerate(pontos)
                        for i, t in self._tracos.items()), key=lambda d: d[0])
        ligados, usados = {}, set()
        for distancia, n, i in pares:
            if distancia <= DISTANCIA_MAXIMA and n not in ligados and i not in usados:
                ligados[n], _ = i, usados.add(i)
        saida = {}
        for n, ponto in enumerate(pontos):
            traco_id = ligados.get(n)
            if traco_id is None:
                traco_id, self._proximo = self._proximo, self._proximo + 1
            self._tracos[traco_id] = (ponto, agora)
            saida[traco_id] = ponto
        return saida


def cruzou_para_dentro(zona, antes, depois):
    """A linha é o meio da área, e o sentido é o do arraste ao marcar: de fora para dentro da loja."""
    sentido = zona.get('sentido') or 'baixo'
    if sentido in ('direita', 'esquerda'):
        meio, a, b, lateral, inicio, tamanho = zona['x'] + zona['w'] / 2, antes[0], depois[0], depois[1], zona['y'], zona['h']
    else:
        meio, a, b, lateral, inicio, tamanho = zona['y'] + zona['h'] / 2, antes[1], depois[1], depois[0], zona['x'], zona['w']
    if not inicio <= lateral <= inicio + tamanho:
        return False
    return a < meio <= b if sentido in ('direita', 'baixo') else a > meio >= b


@dataclass
class _Ocupacao:
    inicio: float
    visto: float
    pessoas: int = 0


@dataclass
class Metricas:
    zonas: list = field(default_factory=list)
    matinal_inicio: str = '05:00'
    matinal_fim: str = '09:00'
    matinal_minimo: int = 300     # segundos
    _rastreador: Rastreador = field(default_factory=Rastreador)
    _anteriores: dict = field(default_factory=dict)
    _contados: set = field(default_factory=set)
    _mesas: dict = field(default_factory=dict)
    _matinais: dict = field(default_factory=dict)
    _hora_coberta: str = ''

    def analisar(self, pessoas, agora, hora, aberta=True):
        """`pessoas` são caixas; `hora` é "HH:MM". Devolve os eventos prontos deste quadro."""
        eventos = []
        if hora[:2] != self._hora_coberta:
            # A prova de que a câmera estava analisando nesta hora: sem ela, zero seria mentira.
            self._hora_coberta = hora[:2]
            eventos.append({'tipo': 'COBERTURA', 'zona': '', 'inicio': agora, 'fim': agora, 'duracao': 0, 'pessoas': 0})
        self._entradas(eventos, pessoas, agora, aberta)
        self._nas_mesas(eventos, pessoas, agora)
        self._matinal(eventos, pessoas, agora, hora)
        return eventos

    def _entradas(self, eventos, pessoas, agora, aberta):
        portas = zonas_do_tipo(self.zonas, ZONA_PORTA)
        if not portas:
            return
        atuais = self._rastreador.atualizar([pes(p) for p in pessoas], agora)
        for traco_id, ponto in atuais.items():
            antes = self._anteriores.get(traco_id)
            if antes is None or traco_id in self._contados or not aberta:
                continue
            for zona in portas:
                if cruzou_para_dentro(zona, antes, ponto):
                    self._contados.add(traco_id)
                    eventos.append({'tipo': 'ENTRADA', 'zona': zona.get('nome', 'Porta'), 'inicio': agora, 'fim': agora,
                                    'duracao': 0, 'pessoas': 1})
                    break
        self._anteriores = atuais

    def _nas_mesas(self, eventos, pessoas, agora):
        for zona in zonas_do_tipo(self.zonas, ZONA_MESA):
            nome = zona.get('nome', 'Mesa')
            aberta = self._mesas.get(nome)
            if any(_dentro(_centro(p), zona) for p in pessoas):
                if aberta:
                    aberta.visto = agora
                else:
                    self._mesas[nome] = _Ocupacao(agora, agora)
            elif aberta and agora - aberta.visto > MESA_FOLGA:
                del self._mesas[nome]
                duracao = aberta.visto - aberta.inicio
                if duracao >= MESA_MINIMO:
                    eventos.append({'tipo': 'ATENDIMENTO', 'zona': nome, 'inicio': aberta.inicio, 'fim': aberta.visto,
                                    'duracao': round(duracao), 'pessoas': 1})

    def _matinal(self, eventos, pessoas, agora, hora):
        na_janela = self.matinal_inicio <= hora < self.matinal_fim
        for zona in zonas_do_tipo(self.zonas, ZONA_TELA):
            nome = zona.get('nome', 'Tela')
            aberta = self._matinais.get(nome)
            quantas = sum(1 for p in pessoas if _dentro(_centro(p), zona))
            if na_janela and quantas >= MATINAL_PESSOAS:
                if not aberta:
                    aberta = self._matinais[nome] = _Ocupacao(agora, agora)
                aberta.visto = agora
                aberta.pessoas = max(aberta.pessoas, quantas)
            elif aberta and (not na_janela or agora - aberta.visto > MATINAL_FOLGA):
                del self._matinais[nome]
                duracao = aberta.visto - aberta.inicio
                if duracao >= self.matinal_minimo:
                    eventos.append({'tipo': 'MATINAL', 'zona': nome, 'inicio': aberta.inicio, 'fim': aberta.visto,
                                    'duracao': round(duracao), 'pessoas': aberta.pessoas})
