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
