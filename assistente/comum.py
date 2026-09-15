"""Utilitários comuns das ferramentas do assistente.

Formatação de datas e nomes, leitura dos argumentos que o Claude manda, a view
do módulo chamada como se fosse a pessoa (chamar_view) e os atalhos de schema
das ferramentas. Ficam aqui para Agenda/Reuniões, Metas comerciais e Impulso
usarem as mesmas regras.
"""
import json
import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.base import BaseStorage
from django.utils import timezone

logger = logging.getLogger(__name__)
User = get_user_model()


DIAS = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')


class Invalido(Exception):
    """Pedido que não passa na validação; a mensagem vai para o Claude repassar."""


def _local(dt):
    return timezone.localtime(dt) if dt and timezone.is_aware(dt) else dt


def _dia(d):
    return f'{DIAS[d.weekday()]} {d:%d/%m/%Y}'


def _dh(dt):
    dt = _local(dt)
    return f'{_dia(dt)} {dt:%H:%M}' if dt else '—'


def _periodo(inicio, fim=None, dia_inteiro=False):
    ini, fim = _local(inicio), _local(fim)
    if not ini:
        return '—'
    if dia_inteiro:
        ultimo = (fim - timedelta(seconds=1)).date() if fim and fim > ini else ini.date()
        if ultimo == ini.date():
            return f'{_dia(ini)} (dia inteiro)'
        return f'{_dia(ini)} a {_dia(ultimo)} (dia inteiro)'
    if not fim:
        return _dh(ini)
    if fim.date() == ini.date():
        return f'{_dh(ini)}–{fim:%H:%M}'
    return f'{_dh(ini)} até {_dh(fim)}'


def _nome(u):
    if not u:
        return '—'
    return getattr(u, 'full_name', '') or getattr(u, 'email', '') or f'#{u.pk}'


def _nomes(usuarios, maximo=20):
    lista = [f'{_nome(u)} (id {u.pk})' for u in usuarios]
    if len(lista) > maximo:
        return ', '.join(lista[:maximo]) + f' e mais {len(lista) - maximo}'
    return ', '.join(lista)


def _url(caminho):
    return (getattr(settings, 'BASE_URL', '') or '').rstrip('/') + caminho


def _cortar(texto, limite):
    texto = (texto or '').strip()
    return texto if len(texto) <= limite else texto[:limite].rstrip() + '…'


def _data_hora(valor, campo):
    """'AAAA-MM-DDTHH:MM' no horário de Brasília → datetime com fuso."""
    from django.utils.dateparse import parse_datetime

    texto = str(valor or '').strip().replace(' ', 'T', 1)
    try:
        dt = parse_datetime(texto)
    except ValueError:
        dt = None
    if dt is None:
        raise Invalido(f'{campo} inválido ("{valor}"). Use AAAA-MM-DDTHH:MM, no horário de Brasília.')
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _data(valor, campo):
    from django.utils.dateparse import parse_date

    try:
        d = parse_date(str(valor or '').strip()[:10])
    except ValueError:
        d = None
    if d is None:
        raise Invalido(f'{campo} inválida ("{valor}"). Use AAAA-MM-DD.')
    return d


def _meia_noite(d):
    return timezone.make_aware(datetime.combine(d, time.min))


def _inteiro(valor, campo):
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise Invalido(f'{campo} inválido: informe o número (id).') from None


def _ids(valores, campo):
    if valores in (None, ''):
        return []
    if not isinstance(valores, (list, tuple)):
        valores = [valores]
    return [_inteiro(v, campo) for v in valores]


def _pessoas(ids, eu=None):
    """Colaboradores ativos pelos ids, na ordem pedida; id que não existe é erro."""
    ids = [i for i in dict.fromkeys(ids) if not (eu and i == eu.pk)]
    achados = {u.pk: u for u in User.objects.filter(pk__in=ids, is_active=True)}
    faltando = [str(i) for i in ids if i not in achados]
    if faltando:
        raise Invalido('Não achei colaborador ativo com o id ' + ', '.join(faltando)
                       + '. Use buscar_pessoas para achar o id certo.')
    return [achados[i] for i in ids]


def _limite(args, padrao, maximo=50):
    try:
        return max(1, min(int(args.get('limite') or padrao), maximo))
    except (TypeError, ValueError):
        return padrao


def _sim(valor, padrao=False):
    if valor is None:
        return padrao
    if isinstance(valor, str):
        return valor.strip().lower() in ('1', 'true', 'sim', 'yes', 'on')
    return bool(valor)


class _AvisosEmMemoria(BaseStorage):
    """Os avisos (messages) que a view der ficam aqui para virar resposta."""

    def _get(self, *args, **kwargs):
        return [], True

    def _store(self, messages, response, *args, **kwargs):
        return []


def chamar_view(view, user, caminho, *, dados=None, json_corpo=False, aceita_json=False, **kwargs):
    """Executa a view do módulo com a pessoa logada, sem passar pelo navegador.

    É o que faz o assistente agir exatamente como a tela: a mesma view decide
    permissão, grava, avisa e cria o que nasce junto. Devolve um dict com
    ``status``, ``json`` (quando a view responde JSON), ``avisos`` [(nível,
    texto)] e ``destino`` (quando redireciona).
    """
    from django.core.exceptions import PermissionDenied
    from django.http import Http404
    # RequestFactory só monta a requisição; nada aqui depende do ambiente de teste.
    from django.test import RequestFactory

    extra = {'HTTP_ACCEPT': 'application/json'} if aceita_json else {}
    fabrica = RequestFactory()
    if json_corpo:
        request = fabrica.post(caminho, data=json.dumps(dados or {}),
                               content_type='application/json', **extra)
    else:
        request = fabrica.post(caminho, data={k: v for k, v in (dados or {}).items() if v is not None},
                               **extra)
    request.user = user
    request._messages = _AvisosEmMemoria(request)

    resultado = {'status': 500, 'json': None, 'avisos': [], 'destino': None}
    try:
        resposta = view(request, **kwargs)
    except Http404:
        resultado['status'] = 404
        return resultado
    except PermissionDenied:
        resultado['status'] = 403
        return resultado
    except Exception:                                           # noqa: BLE001
        logger.exception('Assistente: view %s falhou', getattr(view, '__name__', view))
        return resultado

    resultado['status'] = resposta.status_code
    if 'application/json' in (resposta.get('Content-Type') or ''):
        try:
            resultado['json'] = json.loads(resposta.content or b'null')
        except ValueError:
            pass
    resultado['avisos'] = [(m.level_tag, str(m.message)) for m in request._messages]
    resultado['destino'] = resposta.get('Location')
    return resultado


def _falha(r, padrao='não deu certo.'):
    """O motivo da recusa, do jeito que a tela mostraria."""
    corpo = r.get('json')
    if isinstance(corpo, dict) and (corpo.get('error') or corpo.get('erro')):
        return corpo.get('error') or corpo.get('erro')
    erros = [texto for nivel, texto in r.get('avisos') or [] if nivel == 'error']
    if erros:
        return ' '.join(erros)
    if r.get('status') == 404:
        return 'não encontrado — ou você não tem permissão para isso.'
    if r.get('status') == 403:
        return 'você não tem permissão para isso.'
    return padrao


def _sem_erro(r, status=302):
    return r['status'] == status and not any(n == 'error' for n, _ in r['avisos'])


def _normal(texto):
    import unicodedata

    base = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(c for c in base if not unicodedata.combining(c)).lower().strip()


def _titulo(args, campo='titulo', obrigatorio='Informe o título.'):
    titulo = ' '.join(str(args.get(campo) or '').split())
    if not titulo:
        raise Invalido(obrigatorio)
    return titulo


def _ok_json(r, status=200):
    return r['status'] == status and isinstance(r['json'], dict) and bool(r['json'].get('ok', True))


DATA_HORA = 'AAAA-MM-DDTHH:MM, horário de Brasília'


def _obj(obrigatorios=(), **propriedades):
    return {'type': 'object', 'properties': propriedades, 'required': list(obrigatorios)}


def _int(descricao):
    return {'type': 'integer', 'description': descricao}


def _txt(descricao):
    return {'type': 'string', 'description': descricao}


def _bool(descricao):
    return {'type': 'boolean', 'description': descricao}


def _lista_ids(descricao):
    return {'type': 'array', 'items': {'type': 'integer'}, 'description': descricao}


def _lista_txt(descricao):
    return {'type': 'array', 'items': {'type': 'string'}, 'description': descricao}


def _acao(previa, execucao, descricao, schema):
    return {'acao': True, 'previa': previa, 'fn': execucao,
            'description': descricao + ' Só PREPARA: a ação roda com confirmar_acao, depois que o '
                                       'usuário aprovar o resumo numa nova mensagem.',
            'input_schema': schema}
