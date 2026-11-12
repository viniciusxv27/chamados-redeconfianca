"""Segunda olhada nos alertas: IA de visão sobre os quadros-chave dos 3 minutos gravados.

O detector da loja é rápido mas raso (caixas de "pessoa" e "celular"). Aqui um modelo de visão
olha a sequência de quadros (antes, durante e depois), com o que o detector mediu e com o
histórico de decisões da mesma câmera (ver ``aprendizado``), e devolve risco, linha do tempo,
sinais a favor e contra e o que conferir no vídeo.

Usa a mesma chave da OpenAI do resto do portal. Sem chave, ou com erro, o alerta segue sem a
análise (a tela mostra o motivo e dá para tentar de novo). Nunca identifica pessoas.
"""
import base64
import json
import logging
import re
import threading

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import aprendizado
from .models import AlertaCamera

logger = logging.getLogger(__name__)

MODELO = getattr(settings, 'CAMERAS_IA_MODELO', 'gpt-4o')
EM_THREAD = True            # os testes desligam para rodar na hora
QUADROS_MAX = 8
CLASSIFICACOES = {'furto_provavel': 'Furto provável', 'suspeito': 'Suspeito', 'normal': 'Parece normal',
                  'inconclusivo': 'Inconclusivo'}

SISTEMA = (
    'Você confere alertas de possível furto das câmeras das lojas da Rede Confiança (lojas de telefonia: '
    'celulares, acessórios e eletrônicos). Recebe, em ordem, quadros de um vídeo de cerca de 3 minutos, cada um '
    'com o segundo em relação ao momento do alerta (negativo = antes, 0 = o alerta, positivo = depois), o que o '
    'detector automático da loja mediu e o histórico de decisões humanas sobre alertas parecidos desta câmera.\n'
    'Regras:\n'
    '- Descreva só ações e objetos: pegar, guardar no bolso ou na bolsa, devolver ao mostruário, entregar ao '
    'vendedor, sair da loja, atender no balcão, arrumar ou repor. Nunca identifique pessoas e nunca descreva rosto, '
    'cor da pele, etnia, idade, gênero ou físico; para diferenciar use "pessoa 1", "pessoa 2" e a posição ou a roupa.\n'
    '- É uma suspeita para uma pessoa conferir no vídeo, nunca uma acusação.\n'
    '- Use o histórico: se esta câmera costuma dar falso alarme por um motivo (por exemplo, funcionário arrumando '
    'o mostruário), procure esse padrão e diga se ele se repete; se os casos confirmados tinham um padrão, diga se '
    'ele aparece.\n'
    '- Se os quadros não mostram o suficiente, use "inconclusivo" e diga em que segundos conferir o vídeo.\n'
    'Responda só com JSON, assim: {"risco": 0 a 100, "classificacao": "furto_provavel" | "suspeito" | "normal" | '
    '"inconclusivo", "resumo": "2 a 3 frases", "linha_do_tempo": [{"segundo": -30, "o_que": "..."}], '
    '"sinais_de_risco": ["..."], "sinais_normais": ["..."], "conferir_no_video": [{"segundo": 0, "o_que": "..."}], '
    '"recomendacao": "o que fazer agora, em 1 frase"}'
)


def _texto(valor, maximo):
    return ' '.join(str(valor or '').split())[:maximo]


def _inteiro(valor, minimo, maximo, padrao=0):
    try:
        return max(minimo, min(maximo, int(round(float(valor)))))
    except (TypeError, ValueError):
        return padrao


def _json(texto):
    try:
        return json.loads(texto)
    except (TypeError, ValueError):
        achado = re.search(r'\{.*\}', texto or '', re.S)
        if achado:
            try:
                return json.loads(achado.group(0))
            except ValueError:
                pass
    return None


def limpar_resposta(dados):
    """Só o que a tela usa, nos tamanhos que ela aguenta."""
    if not isinstance(dados, dict):
        raise ValueError('A IA não respondeu em JSON.')
    classificacao = dados.get('classificacao') if dados.get('classificacao') in CLASSIFICACOES else 'inconclusivo'

    def momentos(lista, n):
        saida = []
        for item in (lista if isinstance(lista, list) else [])[:n]:
            if isinstance(item, dict) and item.get('o_que'):
                saida.append({'segundo': _inteiro(item.get('segundo'), -600, 600), 'o_que': _texto(item['o_que'], 200)})
        return sorted(saida, key=lambda m: m['segundo'])

    def frases(lista, n):
        return [_texto(x, 160) for x in (lista if isinstance(lista, list) else [])[:n] if _texto(x, 160)]

    return {
        'risco': _inteiro(dados.get('risco'), 0, 100, 50), 'classificacao': classificacao,
        'resumo': _texto(dados.get('resumo'), 600), 'recomendacao': _texto(dados.get('recomendacao'), 300),
        'linha_do_tempo': momentos(dados.get('linha_do_tempo'), 12), 'conferir_no_video': momentos(dados.get('conferir_no_video'), 5),
        'sinais_de_risco': frases(dados.get('sinais_de_risco'), 6), 'sinais_normais': frases(dados.get('sinais_normais'), 6),
    }


def _imagens(alerta):
    """[(segundo, bytes)] dos quadros-chave; sem eles, a foto do alerta (segundo 0)."""
    saida = []
    for q in sorted(alerta.quadros or [], key=lambda q: q.get('segundo', 0))[:QUADROS_MAX]:
        try:
            with AlertaCamera._meta.get_field('video').storage.open(q['arquivo'], 'rb') as arquivo:
                saida.append((int(q.get('segundo', 0)), arquivo.read()))
        except (KeyError, OSError, ValueError):
            continue
    if not saida and alerta.foto:
        try:
            with alerta.foto.open('rb') as arquivo:
                saida.append((0, arquivo.read()))
        except OSError:
            pass
    return saida


def contexto(alerta):
    """O texto que acompanha os quadros: o alerta, o que o detector mediu e o que já foi decidido."""
    g, canal = alerta.gravador, alerta.canal
    local = timezone.localtime(alerta.criado_em)
    zonas = ', '.join(z.get('nome', 'área') for z in (canal.zonas if canal else []) if isinstance(z, dict)) or 'nenhuma marcada'
    linhas = [
        f'Loja: {g.loja}. Câmera: {canal.titulo if canal else "?"}. Áreas marcadas: {zonas}.',
        f'Alerta: {alerta.get_tipo_display()} — {alerta.motivo or "sem motivo"}. Nota do detector: {alerta.confianca}%.',
        f'Quando: {local:%d/%m/%Y %H:%M} (loja abre {g.abre_as:%H:%M}, fecha {g.fecha_as:%H:%M}).',
    ]
    medidas = {k: v for k, v in (alerta.detalhes or {}).items() if not str(k).startswith('_')}
    if medidas:
        linhas.append('O detector mediu: ' + '; '.join(f'{k} = {v}' for k, v in medidas.items()) + '.')
    a = aprendizado.avaliar(alerta.tipo, alerta.gravador_id, alerta.canal_id)
    linhas.append(f'Histórico: alertas assim nesta câmera se confirmaram em cerca de {a["chance"]}% das vezes '
                  f'({a["decisoes_canal"]} decisões nesta câmera, {a["decisoes_loja"]} nesta loja).')
    motivos = aprendizado.motivos_de_falso(alerta.tipo, alerta.canal_id)
    if motivos:
        linhas.append('Motivos dos falsos alarmes desta câmera: ' + ', '.join(f'{m} ({n})' for m, n in motivos[:4]) + '.')
    casos = aprendizado.exemplos(alerta)
    if casos:
        linhas.append('Casos já conferidos por pessoas (mais recentes primeiro):')
        for c in casos:
            decisao = 'FURTO CONFIRMADO' if c.status == AlertaCamera.Status.CONFIRMADO else 'FALSO ALARME'
            if c.motivo_falso:
                decisao += f' ({c.get_motivo_falso_display()})'
            partes = [f'- {decisao}, {timezone.localtime(c.criado_em):%d/%m %H:%M}']
            if c.ia_resumo:
                partes.append(f'a análise disse: "{_texto(c.ia_resumo, 220)}"')
            if c.observacao:
                partes.append(f'quem conferiu anotou: "{_texto(c.observacao, 220)}"')
            linhas.append('; '.join(partes) + '.')
    return '\n'.join(linhas)


def _cliente():
    import openai
    return openai.OpenAI(api_key=settings.OPENAI_API_KEY, timeout=90)


def perguntar(alerta):
    """Chama o modelo de visão. Devolve o dicionário limpo (levanta exceção se não deu)."""
    imagens = _imagens(alerta)
    if not imagens:
        raise ValueError('O alerta não tem imagem para analisar.')
    partes = [{'type': 'text', 'text': contexto(alerta)}]
    for segundo, conteudo in imagens:
        partes.append({'type': 'text', 'text': f'Quadro em {segundo:+d} s:'})
        partes.append({'type': 'image_url', 'image_url': {
            'url': f'data:image/jpeg;base64,{base64.b64encode(conteudo).decode("ascii")}',
            # Perto do acontecimento vale a imagem cheia; longe, a visão geral basta.
            'detail': 'high' if abs(segundo) <= 10 else 'low'}})
    resposta = _cliente().chat.completions.create(
        model=MODELO, temperature=.1, max_tokens=1200, response_format={'type': 'json_object'},
        messages=[{'role': 'system', 'content': SISTEMA}, {'role': 'user', 'content': partes}])
    return limpar_resposta(_json(resposta.choices[0].message.content))


def analisar(alerta_id):
    """Roda a análise e grava no alerta. Nunca levanta: o erro fica registrado no alerta."""
    alerta = AlertaCamera.objects.select_related('gravador', 'canal').filter(pk=alerta_id).first()
    if alerta is None:
        return None
    if not settings.OPENAI_API_KEY:
        AlertaCamera.objects.filter(pk=alerta_id).update(ia_status=AlertaCamera.Ia.ERRO, ia_erro='IA sem chave configurada no portal.')
        return None
    try:
        dados = perguntar(alerta)
    except Exception as erro:  # noqa: BLE001 — rede, chave, cota, resposta torta: o alerta segue sem a IA
        logger.warning('Análise de IA do alerta %s falhou: %s', alerta_id, erro)
        AlertaCamera.objects.filter(pk=alerta_id).update(ia_status=AlertaCamera.Ia.ERRO, ia_erro=_texto(f'{type(erro).__name__}: {erro}', 200))
        return None
    alerta.ia_status, alerta.ia_erro = AlertaCamera.Ia.FEITA, ''
    alerta.ia_risco, alerta.ia_classificacao = dados['risco'], dados['classificacao']
    alerta.ia_resumo, alerta.ia_analise = dados['resumo'], dados
    alerta.ia_modelo, alerta.ia_em = MODELO, timezone.now()
    alerta.prioridade = aprendizado.prioridade(alerta)
    alerta.save(update_fields=['ia_status', 'ia_erro', 'ia_risco', 'ia_classificacao', 'ia_resumo', 'ia_analise',
                               'ia_modelo', 'ia_em', 'prioridade'])
    # Com a IA, a prioridade pode ter subido: o aviso fora da tela sai agora (uma vez só).
    from . import avisos
    avisos.avisar_alerta(alerta.id)
    return dados


def _em_segundo_plano(alerta_id):
    close_old_connections()
    try:
        analisar(alerta_id)
    finally:
        close_old_connections()


def disparar(alerta_id):
    """Marca como "analisando" e roda em segundo plano (a loja e a tela não esperam a IA)."""
    AlertaCamera.objects.filter(pk=alerta_id).update(ia_status=AlertaCamera.Ia.PENDENTE, ia_erro='')
    if EM_THREAD:
        threading.Thread(target=_em_segundo_plano, args=(alerta_id,), daemon=True).start()
    else:
        analisar(alerta_id)
