"""Ponte entre a agenda e as tarefas do portal.

Quando alguém marca uma **Tarefa** na agenda, o esperado é que ela vire tarefa
de verdade — a mesma que aparece em /users/tasks/, com status, chat, anexos e
subtarefas. Não um compromisso no calendário que só parece uma tarefa.

Mesmo desenho da ponte de chamada (reunioes/servicos.py): a agenda pede, o
outro módulo resolve, e falhar aqui nunca derruba a criação do evento. Perder o
compromisso por causa da tarefa seria o pior dos dois mundos.
"""
import difflib
import json
import logging
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Tipo de evento da agenda que vira tarefa de verdade.
TIPOS_COM_TAREFA = ('task',)


def precisa_de_tarefa(evento):
    return getattr(evento, 'event_type', '') in TIPOS_COM_TAREFA


def tarefa_para_evento(evento, convidados=(), autor=None):
    """Garante a tarefa do evento e devolve ela (ou None se algo falhar).

    Idempotente: chamar de novo no mesmo evento não cria uma segunda tarefa,
    só atualiza título, descrição e prazo — evento remarcado não pode virar
    duas tarefas iguais na lista de ninguém.

    O prazo é o **início** do evento, não o fim: é a hora em que a pessoa
    combinou de fazer aquilo, e é por ela que /users/tasks/ separa o dia.
    """
    from core.models import TaskActivity

    try:
        dono = getattr(evento, 'owner', None)
        if dono is None:
            return None

        dados = {
            'title': (evento.title or 'Tarefa')[:200],
            'description': evento.description or '',
            'due_date': evento.start,
        }

        tarefa = getattr(evento, 'tarefa', None)
        if tarefa is None:
            tarefa = TaskActivity.objects.create(
                assigned_to=dono, created_by=(autor or dono),
                priority='MEDIUM', status='PENDING', **dados)
            evento.tarefa = tarefa
            evento.save(update_fields=['tarefa'])
        else:
            for campo, valor in dados.items():
                setattr(tarefa, campo, valor)
            tarefa.save(update_fields=list(dados))

        # Cada convidado ganha a própria tarefa: tarefa é de quem faz, e uma
        # só, compartilhada, não teria como ter status por pessoa.
        _tarefas_dos_convidados(evento, tarefa, convidados, autor or dono)
        return tarefa
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Tarefa do evento %s não criada: %s',
                       getattr(evento, 'pk', '?'), exc)
        return None


def _tarefas_dos_convidados(evento, tarefa_dona, convidados, autor):
    """Uma cópia da tarefa para cada convidado, sem duplicar em remarcação."""
    from core.models import TaskActivity

    for pessoa in convidados or ():
        if pessoa is None or pessoa.pk == tarefa_dona.assigned_to_id:
            continue
        existente = TaskActivity.objects.filter(
            assigned_to=pessoa, created_by=autor,
            title=tarefa_dona.title, due_date=evento.start).first()
        if existente:
            continue
        TaskActivity.objects.create(
            assigned_to=pessoa, created_by=autor,
            title=tarefa_dona.title, description=tarefa_dona.description,
            due_date=evento.start, priority='MEDIUM', status='PENDING')


DIAS_DA_SEMANA = ('segunda', 'terça', 'quarta', 'quinta',
                  'sexta', 'sábado', 'domingo')


def rotulo_repeticao(data):
    """"Repetir toda segunda" — o dia da semana da data escolhida.

    O rótulo genérico "Semanal" obrigava a pessoa a conferir no calendário em
    que dia ela estava marcando.
    """
    if data is None:
        return 'Repetir toda semana'
    nome = DIAS_DA_SEMANA[data.weekday()]
    artigo = 'todo' if data.weekday() >= 5 else 'toda'
    return f'Repetir {artigo} {nome}'


# =========================================================================
# TAREFAS A PARTIR DA TRANSCRIÇÃO DE REUNIÃO (16/11/2026)
# =========================================================================
#
# Antes as tarefas saíam da mesma chamada que escreve o resumo, as seções e as
# decisões, e disputavam o mesmo orçamento de resposta: reunião de 180 mil
# caracteres virava 3 tarefas. Agora há uma etapa só para elas, que percorre a
# transcrição inteira em pedaços (com uma pequena sobreposição) procurando
# todo compromisso dito. Se essa etapa falhar, valem os itens de ação da
# análise geral, como antes — a ata nunca fica sem tarefas por causa dela.

PEDACO_TAREFAS = 20000          # caracteres por chamada (~5,5 mil tokens)
SOBREPOSICAO_TAREFAS = 1200     # compromisso dito na emenda aparece nos dois lados
MAX_TAREFAS = 60
SIMILARIDADE_DUPLICATA = 0.85
CHAMADAS_EM_PARALELO = 4
MODELO_TAREFAS = 'gpt-4o'       # o mesmo da análise geral
SEM_RESPONSAVEL = 'A definir'
PRIORIDADES = ('high', 'medium', 'low')
_PRIORIDADE_PT = {'alta': 'high', 'urgente': 'high', 'media': 'medium', 'média': 'medium',
                  'normal': 'medium', 'baixa': 'low'}
_SEM_NOME = {'', 'a definir', 'n/a', 'na', 'null', 'none', 'ninguem', 'ninguém', '-', '?',
             'nao definido', 'não definido', 'indefinido', 'desconhecido'}
_NEGACOES = {'sem', 'nao', 'nunca', 'nenhum', 'nenhuma'}
_PALAVRAS_VAZIAS = set(
    'a o as os de da do das dos e em no na nos nas num numa para pra pro pros pras por pelo pela '
    'pelos pelas com um uma uns umas ao aos que se sobre como mais ja todo toda todos todas '
    'esse essa isso este esta isto aquele aquela seu sua seus suas dele dela ate'.split())


def pedacos_para_tarefas(texto, tamanho=PEDACO_TAREFAS, sobreposicao=SOBREPOSICAO_TAREFAS):
    """Corta a transcrição em pedaços de ~`tamanho` com `sobreposicao` entre eles.

    A transcrição do Whisper quase não tem quebra de linha (30 mil caracteres,
    4 linhas), então o corte procura fim de parágrafo, depois fim de frase e,
    por último, um espaço — nunca no meio de uma palavra. O resto pequeno
    (até 15% de um pedaço) vai junto com o último em vez de virar uma chamada.
    """
    texto = (texto or '').strip()
    if not texto:
        return []
    folga = int(tamanho * 1.15)
    pedacos = []
    inicio = 0
    while inicio < len(texto):
        if len(texto) - inicio <= folga:
            pedacos.append(texto[inicio:].strip())
            break
        fim = _ponto_de_corte(texto, inicio + tamanho, inicio + int(tamanho * 0.6))
        pedacos.append(texto[inicio:fim].strip())
        recomeco = _ponto_de_corte(texto, fim - sobreposicao // 4, fim - sobreposicao)
        inicio = recomeco if inicio < recomeco < fim else fim
    return [p for p in pedacos if p]


def _ponto_de_corte(texto, alvo, minimo):
    """Posição logo depois do melhor separador entre `minimo` e `alvo`."""
    minimo = max(0, minimo)
    for marcas in (('\n',), ('. ', '? ', '! ', '… '), (' ',)):
        melhor = max(texto.rfind(marca, minimo, alvo) for marca in marcas)
        if melhor != -1:
            return melhor + 1
    return alvo


def _prompt_tarefas(data_reuniao, hoje):
    return (
        "Você é um secretário executivo meticuloso. Sua ÚNICA missão é encontrar, neste trecho da "
        "transcrição de uma reunião, TODOS os compromissos e encaminhamentos — tudo o que alguém precisa "
        "FAZER depois da reunião. Não resuma a reunião: liste tarefas. Português do Brasil.\n\n"
        "Leia frase a frase e procure:\n"
        "- pedidos e delegações (\"fulano, vê isso\", \"preciso que você...\", \"alguém tem que...\");\n"
        "- promessas e autocompromissos (\"vou mandar\", \"eu fico de\", \"deixa comigo\", \"te passo\", "
        "\"vou ver\");\n"
        "- decisões que exigem execução (algo decidido que alguém precisa pôr em prática);\n"
        "- pendências e problemas que alguém ficou de resolver, cobranças a fazer;\n"
        "- follow-ups, retornos e próximas conversas (\"marcar com\", \"voltar nesse ponto\", "
        "\"retomar na próxima\");\n"
        "- verificações, conferências, levantamentos de dados e análises a fazer;\n"
        "- comunicações a fazer (avisar, enviar, divulgar, cobrar, alinhar com alguém);\n"
        "- prazos combinados e entregas com data.\n\n"
        "Regras de cada tarefa:\n"
        "- UMA ação por tarefa. Fala com várias ações (\"vou ligar pro fornecedor e mandar a planilha\") "
        "vira tarefas SEPARADAS.\n"
        "- \"task\": começa com verbo no infinitivo, específica e acionável: 6 a 18 palavras com o objeto, o "
        "assunto e para quem/com quem (ex.: \"Enviar ao Bruno a planilha de metas de dezembro das 12 lojas\", "
        "não \"Enviar planilha\"). Proibido genérico como \"Ver questões\" ou \"Olhar o assunto\".\n"
        "- \"description\": 2 a 4 frases de contexto: por que a tarefa existe, detalhes, e os números, "
        "valores, lojas, produtos, sistemas e nomes citados.\n"
        "- \"responsible\": nome como foi falado na reunião; \"A definir\" se ninguém foi nomeado ou não dá "
        "para saber quem disse \"eu vou\".\n"
        f"- \"deadline\": data ISO (AAAA-MM-DD). A reunião foi em {data_reuniao} (hoje é {hoje}): converta "
        "\"amanhã\", \"até sexta\", \"semana que vem\", \"fim do mês\" a partir da data da reunião. null se "
        "nenhum prazo foi dito.\n"
        "- \"priority\": \"high\" (urgente, prazo até uma semana, cliente, dinheiro, problema em aberto), "
        "\"medium\" (importante sem urgência) ou \"low\" (melhoria, ideia já aceita, sem pressa). Diferencie: "
        "não marque tudo igual.\n"
        "- \"success_criteria\": 1 frase — como saber que está feito.\n"
        "- \"topic\": assunto/pauta de onde a tarefa saiu (frase curta).\n"
        "- \"evidence\": trecho LITERAL e curto (até ~200 caracteres) da fala que originou a tarefa, "
        "copiado da transcrição.\n\n"
        "Fidelidade:\n"
        "- PROIBIDO inventar: só entra o que foi dito, pedido, prometido ou claramente decidido. Ideia solta, "
        "sem ninguém se comprometer e sem decisão, não entra.\n"
        "- Na dúvida sobre um compromisso REAL, inclua. Reunião longa costuma ter dezenas de encaminhamentos.\n"
        "- O trecho pode começar ou terminar no meio de uma fala; use o que está nele.\n\n"
        'Responda APENAS com JSON válido: {"tasks": [{"task": "...", "description": "...", '
        '"responsible": "...", "deadline": null, "priority": "medium", "success_criteria": "...", '
        '"topic": "...", "evidence": "..."}]}. Sem tarefas no trecho: {"tasks": []}.'
    )


def _ler_json_tarefas(conteudo):
    """A lista de tarefas da resposta; se veio cortada, aproveita as completas."""
    texto = (conteudo or '').strip()
    if texto.startswith('```'):
        texto = re.sub(r'^```[a-zA-Z]*\s*|\s*```$', '', texto)
    try:
        dados = json.loads(texto)
    except ValueError:
        dados = None
    if isinstance(dados, dict):
        for chave in ('tasks', 'action_items', 'tarefas', 'items'):
            if isinstance(dados.get(chave), list):
                return dados[chave]
        raise ValueError('JSON sem a lista "tasks".')
    if isinstance(dados, list):
        return dados
    # Resposta cortada no limite de tokens: lê objeto por objeto até onde der.
    abre = texto.find('[')
    if abre == -1:
        raise ValueError('Resposta da IA sem JSON de tarefas.')
    leitor = json.JSONDecoder()
    itens, pos = [], abre + 1
    while True:
        while pos < len(texto) and texto[pos] in ' \t\r\n,':
            pos += 1
        if pos >= len(texto) or texto[pos] != '{':
            break
        try:
            obj, pos = leitor.raw_decode(texto, pos)
        except ValueError:
            break
        itens.append(obj)
    if not itens:
        raise ValueError('Resposta da IA sem tarefas legíveis.')
    return itens


def _tarefas_do_pedaco(client, titulo, pedaco, indice, total, data_reuniao, hoje, contexto=''):
    """Uma chamada (duas, se a primeira falhar) para um pedaço da transcrição."""
    conteudo_usuario = (f"Reunião: '{titulo}'. Trecho {indice} de {total} da transcrição"
                        + (' (os trechos se sobrepõem um pouco nas emendas)' if total > 1 else '')
                        + f':\n\n{pedaco}')
    if contexto:
        conteudo_usuario = f'{contexto}\n\n{conteudo_usuario}'
    ultimo_erro = None
    for max_tokens in (8000, 8000):
        try:
            resp = client.chat.completions.create(
                model=MODELO_TAREFAS,
                messages=[{'role': 'system', 'content': _prompt_tarefas(data_reuniao, hoje)},
                          {'role': 'user', 'content': conteudo_usuario}],
                temperature=0.1,
                max_tokens=max_tokens,
                response_format={'type': 'json_object'},
            )
            conteudo = resp.choices[0].message.content
            if not isinstance(conteudo, str) or not conteudo.strip():
                raise ValueError('Resposta vazia da IA.')
            itens = [normalizar_item(item, origem='extracao') for item in _ler_json_tarefas(conteudo)]
            return [item for item in itens if item]
        except Exception as exc:                                # noqa: BLE001
            ultimo_erro = exc
    raise ultimo_erro


def extrair_tarefas(client, titulo, texto, data_reuniao=None, contexto='', parar=None):
    """Todas as tarefas da transcrição, pedaço a pedaço. Devolve (itens, pedaços, falhas).

    Os pedaços vão em paralelo (até CHAMADAS_EM_PARALELO); a ordem do texto é
    mantida no resultado. Pedaço que falhou duas vezes conta em `falhas`.
    """
    pedacos = pedacos_para_tarefas(texto)
    if not pedacos:
        return [], 0, 0
    hoje = timezone.localdate()
    data_reuniao = data_reuniao or hoje
    total = len(pedacos)

    def um(i):
        if parar is not None and parar.is_set():
            raise RuntimeError('extração abandonada (a análise geral falhou)')
        return _tarefas_do_pedaco(client, titulo, pedacos[i], i + 1, total,
                                  data_reuniao.isoformat(), hoje.isoformat(), contexto)

    resultados = [None] * total
    falhas = 0
    if total == 1:
        try:
            resultados[0] = um(0)
        except Exception as exc:                                # noqa: BLE001
            logger.warning('Extração de tarefas de "%s" falhou: %s', titulo, exc)
            falhas = 1
    else:
        with ThreadPoolExecutor(max_workers=min(CHAMADAS_EM_PARALELO, total)) as executor:
            futuros = {executor.submit(um, i): i for i in range(total)}
            for futuro in as_completed(futuros):
                i = futuros[futuro]
                try:
                    resultados[i] = futuro.result()
                except Exception as exc:                        # noqa: BLE001
                    logger.warning('Extração de tarefas de "%s", trecho %s/%s, falhou: %s',
                                   titulo, i + 1, total, exc)
                    falhas += 1
    itens = [item for parte in resultados if parte for item in parte]
    return itens, total, falhas


def _texto(valor, limite=None):
    if valor is None or isinstance(valor, (dict, list)):
        return ''
    texto = re.sub(r'\s+', ' ', str(valor)).strip()
    return texto[:limite] if limite else texto


def _data_iso(valor):
    texto = _texto(valor)[:10]
    if not texto:
        return None
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        return None


def normalizar_item(item, origem='extracao'):
    """Um item de ação no formato guardado em `action_items` (None se não for tarefa).

    Chaves antigas (task, responsible, deadline, priority, success_criteria) —
    a tela e o assistente leem essas — mais description, topic, evidence e
    origem ('extracao' = etapa dedicada; 'analise' = análise geral).
    """
    if isinstance(item, str):
        item = {'task': item}
    if not isinstance(item, dict):
        return None
    tarefa = _texto(item.get('task') or item.get('title') or item.get('tarefa')).strip(' -•*;')
    if len(tarefa) < 3:
        return None
    responsavel = _texto(item.get('responsible') or item.get('responsavel'), 120)
    if responsavel.lower().strip(' .') in _SEM_NOME:
        responsavel = SEM_RESPONSAVEL
    prioridade = _texto(item.get('priority') or item.get('prioridade')).lower()
    prioridade = _PRIORIDADE_PT.get(prioridade, prioridade)
    if prioridade not in PRIORIDADES:
        prioridade = 'medium'
    return {
        'task': tarefa[:300],
        'responsible': responsavel,
        'deadline': _data_iso(item.get('deadline') or item.get('prazo')),
        'priority': prioridade,
        'success_criteria': _texto(item.get('success_criteria'), 400),
        'description': _texto(item.get('description') or item.get('descricao'), 1200),
        'topic': _texto(item.get('topic') or item.get('section'), 160),
        'evidence': _texto(item.get('evidence'), 400).strip('"“” '),
        'origem': _texto(item.get('origem')) or origem,
    }


def _normal(texto):
    """Sem acento, minúsculo, só letras/números e espaço simples."""
    texto = unicodedata.normalize('NFKD', str(texto or '').lower())
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', texto).strip()


def _palavras(texto_normal):
    return {p for p in texto_normal.split() if p not in _PALAVRAS_VAZIAS}


def _parecidas(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _tem_numero(palavra):
    return any(c.isdigit() for c in palavra)


def _sem_par(sobra, outra):
    """Palavras de `sobra` sem parecida em `outra` (com número, só a idêntica: L220 ≠ L222)."""
    return [p for p in sobra
            if not any(p == q or (not _tem_numero(p) and not _tem_numero(q) and _parecidas(p, q) >= 0.75)
                       for q in outra)]


def _marcantes(texto):
    """Nomes próprios e números do texto original (fora a 1ª palavra, que é o verbo)."""
    palavras = str(texto or '').split()[1:]
    return {_normal(p) for p in palavras if p[:1].isupper() or _tem_numero(p)}


def mesma_tarefa(a, b, limiar=SIMILARIDADE_DUPLICATA):
    """Quase-iguais: texto ≥ limiar (normalizado, também com as palavras em ordem).

    Não junta "Ligar para o fornecedor Alfa" com "... Beta": se cada lado tem uma
    palavra que o outro não tem (nem parecida — plural/erro de digitação passa),
    são tarefas diferentes. E a mesma fala (evidence) com textos parecidos
    (≥ 0,6) é a mesma tarefa vista por dois pedaços.
    """
    na, nb = _normal(a.get('task')), _normal(b.get('task'))
    if not na or not nb:
        return False
    if na == nb:
        return True
    pa, pb = _palavras(na), _palavras(nb)
    semelhanca = max(_parecidas(na, nb), _parecidas(' '.join(sorted(pa)), ' '.join(sorted(pb))))
    so_a, so_b = _sem_par(pa - pb, pb - pa), _sem_par(pb - pa, pa - pb)
    if _NEGACOES & (set(so_a) | set(so_b)):
        return False        # "com nota" × "sem nota"
    ea, eb = _normal(a.get('evidence')), _normal(b.get('evidence'))
    mesma_fala = bool(ea and eb and len(min(ea, eb, key=len)) >= 20
                      and (ea in eb or eb in ea or _parecidas(ea, eb) >= SIMILARIDADE_DUPLICATA))
    if mesma_fala and semelhanca >= 0.6:
        # Uma fala pode render duas tarefas ("ligo pro Alfa e pro Beta"): nome/número diferente separa.
        return not (set(so_a) & _marcantes(a.get('task')) and set(so_b) & _marcantes(b.get('task')))
    if semelhanca < limiar:
        return False
    return not (so_a and so_b)


def _mesclar(a, b):
    """Junta duas versões da mesma tarefa ficando com o mais específico de cada campo."""
    m = dict(a)
    if len(b.get('task') or '') > len(m.get('task') or ''):
        m['task'] = b['task']
    da, db = m.get('description') or '', b.get('description') or ''
    if db and _normal(db) not in _normal(da):
        if da and _parecidas(_normal(da), _normal(db)) < 0.6 and len(da) + len(db) < 1100:
            m['description'] = f'{da} {db}'
        elif len(db) > len(da):
            m['description'] = db
    if m.get('responsible') in (None, '', SEM_RESPONSAVEL) and b.get('responsible') not in (None, '', SEM_RESPONSAVEL):
        m['responsible'] = b['responsible']
    prazos = [p for p in (m.get('deadline'), b.get('deadline')) if p]
    m['deadline'] = min(prazos) if prazos else None
    m['priority'] = min((m.get('priority'), b.get('priority')),
                        key=lambda p: PRIORIDADES.index(p) if p in PRIORIDADES else 1)
    if len(b.get('success_criteria') or '') > len(m.get('success_criteria') or ''):
        m['success_criteria'] = b['success_criteria']
    m['topic'] = m.get('topic') or b.get('topic') or ''
    ea, eb = m.get('evidence') or '', b.get('evidence') or ''
    if eb and _normal(eb) not in _normal(ea):
        m['evidence'] = (f'{ea} […] {eb}' if ea else eb)[:400]
    return m


def deduplicar_tarefas(itens):
    """Junta as quase-iguais mantendo a ordem da primeira aparição."""
    unicas = []
    for item in itens:
        for i, existente in enumerate(unicas):
            if mesma_tarefa(existente, item):
                unicas[i] = _mesclar(existente, item)
                break
        else:
            unicas.append(item)
    return unicas


def organizar_tarefas(itens, limite=MAX_TAREFAS, rotulo=''):
    """Deduplica, ordena por prioridade e prazo e corta no teto (com log do que ficou de fora)."""
    unicas = deduplicar_tarefas(itens)
    ordem = {id(item): n for n, item in enumerate(unicas)}
    unicas.sort(key=lambda item: (PRIORIDADES.index(item['priority']),
                                  item.get('deadline') or '9999-12-31', ordem[id(item)]))
    if len(unicas) > limite:
        fora = unicas[limite:]
        logger.info('%s: %s tarefa(s) ficaram de fora do teto de %s: %s', rotulo or 'Transcrição',
                    len(fora), limite, ' | '.join(item['task'][:80] for item in fora))
    return unicas[:limite]


class ExtracaoEmParalelo:
    """Começa a extração dedicada numa thread, para correr junto com a análise geral.

    Assim a etapa nova quase não soma tempo: os pedaços rodam enquanto o resumo
    e as seções são escritos. `abandonar()` (a análise falhou) não deixa os
    pedaços que ainda não começaram gastarem chamada.
    """

    def __init__(self, client, titulo, texto, data_reuniao=None, contexto=''):
        self.ativa = client is not None and getattr(settings, 'AGENDA_EXTRACAO_TAREFAS', True)
        self.titulo = titulo
        self._parar = threading.Event()
        self._executor = self._futuro = None
        if self.ativa:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tarefas-reuniao')
            self._futuro = self._executor.submit(extrair_tarefas, client, titulo, texto, data_reuniao,
                                                 contexto, self._parar)

    def resultado(self):
        """(itens, pedaços, falhas) — espera a extração terminar."""
        try:
            return self._futuro.result()
        finally:
            self._executor.shutdown(wait=False)

    def abandonar(self):
        self._parar.set()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)


def tarefas_da_reuniao(itens_da_analise, extracao=None, rotulo=''):
    """A lista final de `action_items` da transcrição.

    Vale a etapa dedicada (`extracao`, uma ExtracaoEmParalelo). Caem nos itens
    da análise geral, como antes, quando ela está desligada, falhou inteira ou
    não achou nada; se só alguns pedaços falharam, os itens da análise
    completam o que faltou.
    """
    reserva = [item for item in (normalizar_item(x, origem='analise') for x in (itens_da_analise or [])) if item]
    if extracao is None or not extracao.ativa:
        return organizar_tarefas(reserva, rotulo=rotulo)
    try:
        extraidas, pedacos, falhas = extracao.resultado()
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('%s: extração dedicada de tarefas falhou; ficam as da análise: %s', rotulo, exc)
        return organizar_tarefas(reserva, rotulo=rotulo)
    if not extraidas:
        if pedacos:
            logger.warning('%s: extração dedicada sem tarefas (%s trecho(s), %s falha(s)); '
                           'ficam as da análise.', rotulo, pedacos, falhas)
        return organizar_tarefas(reserva, rotulo=rotulo)
    if falhas:
        extraidas = extraidas + reserva
    final = organizar_tarefas(extraidas, rotulo=rotulo)
    logger.info('%s: %s tarefa(s) da etapa dedicada (%s trecho(s), %s falha(s); a análise geral tinha %s).',
                rotulo, len(final), pedacos, falhas, len(reserva))
    return final


def _data_br(iso):
    try:
        return date.fromisoformat(str(iso)[:10]).strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return ''


def descricao_da_tarefa(item, transcricao):
    """Descrição da TaskActivity: contexto, critério, responsável citado, fala e reunião."""
    if not isinstance(item, dict):
        item = {'task': str(item)}
    blocos = []
    if item.get('description'):
        blocos.append(item['description'])
    linhas = []
    if item.get('success_criteria'):
        linhas.append(f"Critério de sucesso: {item['success_criteria']}")
    linhas.append(f"Responsável mencionado: {item.get('responsible') or SEM_RESPONSAVEL}")
    if _data_br(item.get('deadline')):
        linhas.append(f"Prazo combinado: {_data_br(item.get('deadline'))}")
    if item.get('topic'):
        linhas.append(f"Assunto: {item['topic']}")
    if item.get('evidence'):
        linhas.append(f"Trecho da fala: “{item['evidence']}”")
    blocos.append('\n'.join(linhas))
    quando = ''
    if getattr(transcricao, 'created_at', None):
        quando = ' (' + timezone.localtime(transcricao.created_at).strftime('%d/%m/%Y') + ')'
    origem = f'Tarefa gerada automaticamente da transcrição: {transcricao.title}{quando}'
    if getattr(transcricao, 'pk', None):
        origem += f'\nVer a reunião: /agenda/transcricoes/{transcricao.pk}/'
    blocos.append(origem)
    return '\n\n'.join(blocos)


def prazo_da_tarefa(item):
    """`deadline` ISO → datetime com fuso (meia-noite local, como antes)."""
    iso = _data_iso(item.get('deadline')) if isinstance(item, dict) else None
    if not iso:
        return None
    return timezone.make_aware(datetime.combine(date.fromisoformat(iso), datetime.min.time()))


def _tarefa_intocada(tarefa, dono):
    """Tarefa gerada que ninguém mexeu: pendente, com o dono, sem edição e sem nada pendurado."""
    from agenda.models import MeetingTranscription

    if tarefa.status != 'PENDING' or tarefa.assigned_to_id != dono.pk or tarefa.created_by_id != dono.pk:
        return False
    if tarefa.created_at and tarefa.updated_at and tarefa.updated_at - tarefa.created_at > timedelta(seconds=2):
        return False
    for rel in type(tarefa)._meta.related_objects:
        if rel.related_model is MeetingTranscription:
            continue
        try:
            if rel.related_model._base_manager.filter(**{rel.field.name: tarefa}).exists():
                return False
        except Exception:                                       # noqa: BLE001 — na dúvida, preserva
            return False
    return True


def criar_tarefas_da_transcricao(transcricao, dono):
    """Cria uma TaskActivity por item de ação, atribuída ao dono (como antes).

    Reprocessar não duplica: as tarefas antigas que ninguém tocou são trocadas
    pelas novas; as que alguém já mexeu (status, responsável, edição, chat,
    anexo, Impulso…) ficam, e item novo quase igual a uma delas não é recriado.
    Chamar dentro de transaction.atomic.
    """
    from core.models import TaskActivity

    prioridade = {'high': 'HIGH', 'medium': 'MEDIUM', 'low': 'LOW'}
    mantidas = []
    removidas = 0
    for tarefa in list(transcricao.tasks_created.all()):
        if _tarefa_intocada(tarefa, dono):
            tarefa.delete()
            removidas += 1
        else:
            mantidas.append({'task': tarefa.title})
    criadas = 0
    for bruto in transcricao.action_items or []:
        item = normalizar_item(bruto, origem='analise')
        if not item or any(mesma_tarefa(m, item) for m in mantidas):
            continue
        tarefa = TaskActivity.objects.create(
            title=item['task'][:200],
            description=descricao_da_tarefa(item, transcricao),
            assigned_to=dono,
            created_by=dono,
            priority=prioridade.get(item['priority'], 'MEDIUM'),
            due_date=prazo_da_tarefa(item),
            status='PENDING',
        )
        transcricao.tasks_created.add(tarefa)
        mantidas.append(item)
        criadas += 1
    if removidas or len(mantidas) > criadas:
        logger.info('Transcrição %s: %s tarefa(s) criada(s), %s antiga(s) intocada(s) trocada(s), %s mantida(s).',
                    transcricao.pk, criadas, removidas, len(mantidas) - criadas)
    return criadas
