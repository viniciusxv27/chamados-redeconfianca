"""Onde cada pessoa foi vista pela última vez.

Junta duas fontes e fica com a mais recente de cada pessoa:

* **marcação de ponto pelo portal** — o navegador pede a autorização na hora de
  bater, então a pessoa sabe que aquela posição foi registrada;
* **posição ao vivo** (``PosicaoRegistrada`` com origem ``APP``), enviada pelo
  navegador da pessoa enquanto ela usa o portal — só existe se a coleta estiver
  ligada e se ela tiver autorizado no próprio navegador;
* **posição registrada** manualmente, para envios fora do ponto.

A posição ao vivo tem prioridade enquanto está fresca; sem ela, o mapa cai para
a última marcação de ponto.

A posição carrega sempre **quando** foi capturada e **de onde veio**. Um ponto
no mapa sem essa informação mente por omissão: dá a entender que a pessoa está
ali agora, quando pode ser de três dias atrás.
"""
from datetime import timedelta

from django.utils import timezone

# Acima disto o ponto no mapa deixa de ser "onde a pessoa está" e passa a ser
# "onde a pessoa esteve".
FRESCO_MINUTOS = 90
DIAS_PADRAO = 7

# Janela em que uma posição enviada pelo navegador ainda vale como "ao vivo".
# Depois disso ela é só mais um registro antigo, e dizer "ao vivo" seria mentira.
AO_VIVO_MINUTOS = 10

# Até onde uma posição ainda diz algo sobre um momento do passado. Mais longe que
# isso, "onde ela estava" viraria palpite, e a tela prefere dizer que não há posição.
JANELA_MOMENTO_MINUTOS = 30


def foto_de(pessoa):
    """URL da foto da pessoa, ou ``None`` — aí o mapa desenha as iniciais.

    O arquivo pode ter sumido do storage; nesse caso não vale derrubar o mapa
    inteiro por causa de uma imagem.
    """
    for campo in ('profile_picture', 'avatar'):
        arquivo = getattr(pessoa, campo, None)
        if not arquivo:
            continue
        try:
            return arquivo.url
        except Exception:
            continue
    return None


def _idade(momento, agora):
    minutos = int((agora - momento).total_seconds() // 60)
    if minutos < 1:
        return 'agora'
    if minutos < 60:
        return f'há {minutos} min'
    horas = minutos // 60
    if horas < 24:
        return f'há {horas}h'
    dias = horas // 24
    return f'há {dias} dia{"s" if dias > 1 else ""}'


def posicoes(usuarios=None, dias=DIAS_PADRAO, agora=None):
    """Última posição conhecida de cada pessoa, da mais recente para a mais antiga."""
    from tangerino.models import RegistroPontoPortal

    from .models import PosicaoRegistrada

    agora = agora or timezone.localtime()
    desde = agora - timedelta(days=dias)

    do_ponto = (RegistroPontoPortal.objects
                .filter(latitude__isnull=False, longitude__isnull=False,
                        momento__gte=desde, sucesso=True)
                .select_related('usuario', 'usuario__sector')
                .order_by('-momento'))
    manuais = (PosicaoRegistrada.objects
               .filter(momento__gte=desde)
               .select_related('usuario', 'usuario__sector')
               .order_by('-momento'))

    if usuarios is not None:
        ids = [u.id for u in usuarios]
        do_ponto = do_ponto.filter(usuario_id__in=ids)
        manuais = manuais.filter(usuario_id__in=ids)

    # Fica a mais recente por pessoa, venha de onde vier. Como a posição ao
    # vivo é reenviada de tempos em tempos, ela naturalmente ganha da última
    # batida de ponto enquanto a pessoa está com o portal aberto; quando ela
    # para de enviar, o ponto volta a ser a referência.
    melhor = {}
    for registro in list(do_ponto) + list(manuais):
        atual = melhor.get(registro.usuario_id)
        if atual is None or registro.momento > atual['momento']:
            melhor[registro.usuario_id] = {
                'usuario': registro.usuario,
                'latitude': registro.latitude,
                'longitude': registro.longitude,
                'momento': registro.momento,
                'origem': getattr(registro, 'origem', 'PONTO'),
                'precisao': getattr(registro, 'precisao_metros', None),
            }

    return [_item_do_mapa(item, agora)
            for item in sorted(melhor.values(), key=lambda x: x['momento'], reverse=True)]


def _item_do_mapa(item, agora):
    """O formato que a tela e a API do mapa leem, venha a posição de onde vier."""
    pessoa = item['usuario']
    minutos = int((agora - item['momento']).total_seconds() // 60)
    return {
        'foto': foto_de(pessoa),
        'ao_vivo': item['origem'] == 'APP' and minutos <= AO_VIVO_MINUTOS,
        'id': pessoa.id,
        'nome': pessoa.get_full_name() or pessoa.email,
        'cargo': pessoa.job_title or '',
        'setor': pessoa.sector.name if pessoa.sector_id else '',
        'setor_id': pessoa.sector_id,
        'latitude': item['latitude'],
        'longitude': item['longitude'],
        'precisao': item['precisao'],
        'momento': item['momento'],
        'quando': _idade(item['momento'], agora),
        'minutos': minutos,
        'recente': minutos <= FRESCO_MINUTOS,
        'origem': item['origem'],
    }


ROTULO_ORIGEM = {'PONTO': 'marcação de ponto', 'MANUAL': 'envio manual', 'APP': 'localização ao vivo'}


def posicoes_perto(pedidos, janela_minutos=JANELA_MOMENTO_MINUTOS):
    """Posição mais próxima de cada (chave, id da pessoa, momento). Uma consulta por fonte.

    Devolve {chave: posição}; quem não tem posição dentro da janela fica de fora. As
    janelas de cada pessoa viram um OR sobre (usuario, momento), o índice da tabela
    de posições, então uma página do histórico não carrega o trajeto do mês inteiro.
    """
    from bisect import bisect_left

    from django.db.models import Q
    from tangerino.models import RegistroPontoPortal

    from .models import PosicaoRegistrada

    pedidos = list(pedidos)
    if not pedidos:
        return {}

    janela = timedelta(minutes=janela_minutos)
    faixas = Q()
    for usuario_id, momento in {(u, m) for _c, u, m in pedidos}:
        faixas |= Q(usuario_id=usuario_id, momento__gte=momento - janela, momento__lte=momento + janela)

    colunas = ('usuario_id', 'latitude', 'longitude', 'momento')
    achadas = {}
    for usuario_id, lat, lon, quando in (
            RegistroPontoPortal.objects
            .filter(faixas, latitude__isnull=False, longitude__isnull=False, sucesso=True)
            .order_by().values_list(*colunas)):
        achadas.setdefault(usuario_id, []).append((quando, lat, lon, 'PONTO', None))
    for usuario_id, lat, lon, quando, origem, precisao in (
            PosicaoRegistrada.objects.filter(faixas)
            .order_by().values_list(*colunas, 'origem', 'precisao_metros')):
        achadas.setdefault(usuario_id, []).append((quando, lat, lon, origem, precisao))

    for lista in achadas.values():
        lista.sort(key=lambda x: x[0])

    saida = {}
    for chave, usuario_id, momento in pedidos:
        lista = achadas.get(usuario_id)
        if not lista:
            continue
        i = bisect_left(lista, momento, key=lambda x: x[0])
        vizinhas = lista[max(0, i - 1):i + 1]
        quando, lat, lon, origem, precisao = min(vizinhas, key=lambda x: abs(x[0] - momento))
        distancia = abs(quando - momento)
        if distancia > janela:
            continue
        minutos = int(distancia.total_seconds() // 60)
        saida[chave] = {
            'latitude': lat, 'longitude': lon, 'momento': quando,
            'origem': origem, 'origem_rotulo': ROTULO_ORIGEM.get(origem, origem),
            'precisao': precisao, 'minutos': minutos,
            'antes': quando <= momento,
        }
    return saida


def posicao_no_momento(pessoa, momento, agora=None):
    """A posição de uma pessoa num momento do passado, no formato do mapa (lista de 0 ou 1)."""
    achada = posicoes_perto([(pessoa.id, pessoa.id, momento)]).get(pessoa.id)
    if not achada:
        return []
    item = {'usuario': pessoa, **{c: achada[c] for c in
                                  ('latitude', 'longitude', 'momento', 'origem', 'precisao')}}
    return [_item_do_mapa(item, agora or timezone.localtime())]


def ultima_posicao(usuario_id):
    """Quando e de onde veio a última posição da pessoa, em qualquer data (ou ``None``)."""
    from tangerino.models import RegistroPontoPortal

    from .models import PosicaoRegistrada

    do_ponto = (RegistroPontoPortal.objects
                .filter(usuario_id=usuario_id, latitude__isnull=False,
                        longitude__isnull=False, sucesso=True)
                .order_by('-momento').values_list('momento', flat=True).first())
    manual = (PosicaoRegistrada.objects.filter(usuario_id=usuario_id)
              .order_by('-momento').values_list('momento', 'origem').first())

    if manual and (not do_ponto or manual[0] > do_ponto):
        quando, origem = manual
    elif do_ponto:
        quando, origem = do_ponto, 'PONTO'
    else:
        return None
    return {'momento': quando, 'origem': origem, 'origem_rotulo': ROTULO_ORIGEM.get(origem, origem),
            'em_param': timezone.localtime(quando).strftime('%Y-%m-%dT%H:%M:%S')}
