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

    saida = []
    for item in sorted(melhor.values(), key=lambda x: x['momento'], reverse=True):
        pessoa = item['usuario']
        minutos = int((agora - item['momento']).total_seconds() // 60)
        recente = minutos <= FRESCO_MINUTOS
        saida.append({
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
            'recente': recente,
            'origem': item['origem'],
        })
    return saida
