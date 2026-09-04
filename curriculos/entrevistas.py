"""Reunião do tipo Entrevista ↔ banco de talentos.

Marcar uma entrevista na agenda e depois cadastrar o candidato à mão em outro
lugar é o tipo de trabalho duplicado que faz o RH parar de usar as duas
ferramentas. Aqui, criar a reunião já abre a ficha no banco de talentos, e o
link da IA do RH fica pendurado nela.

O que **não** é feito aqui, de propósito: transcrever, analisar perfil ou casar
com loja. Isso é do sistema de perfil, que já faz bem — duplicar seria manter
duas IAs divergindo. O portal entra com o banco de currículos e a agenda.
"""
import logging

logger = logging.getLogger(__name__)


def e_entrevista(reuniao):
    from reunioes.models import Reuniao
    return getattr(reuniao, 'tipo', '') == Reuniao.ENTREVISTA


def ficha_da_entrevista(reuniao, autor=None, arquivo=None):
    """Garante a ficha do candidato no banco de talentos.

    Idempotente: remarcar a entrevista não cria uma segunda ficha. Falhar aqui
    nunca derruba a criação da reunião — a entrevista acontece de qualquer
    jeito, e uma ficha faltando é bem menos grave do que perder o compromisso.
    """
    from .models import Curriculo

    if not e_entrevista(reuniao):
        return None
    try:
        ficha = Curriculo.objects.filter(reuniao=reuniao).first()
        if ficha is None:
            ficha = Curriculo.objects.create(
                reuniao=reuniao,
                # O tema da reunião costuma ser o nome do candidato
                # ("Entrevista — Maria Silva"). Serve de ponto de partida; o RH
                # corrige na ficha.
                nome=_nome_do_tema(reuniao.titulo),
                situacao=Curriculo.Situacao.ENTREVISTA,
                enviado_por=autor or reuniao.organizador,
            )
        elif ficha.situacao == Curriculo.Situacao.NOVO:
            ficha.situacao = Curriculo.Situacao.ENTREVISTA
            ficha.save(update_fields=['situacao', 'busca'])

        if arquivo is not None:
            anexar_pdf(ficha, arquivo)
        return ficha
    except Exception as exc:                                 # noqa: BLE001
        logger.warning('Ficha da entrevista %s não criada: %s',
                       getattr(reuniao, 'pk', '?'), exc)
        return None


SEPARADORES = ('—', '-', ':', '|')


def _nome_do_tema(titulo):
    """"Entrevista — Maria Silva" vira "Maria Silva"."""
    texto = (titulo or '').strip()
    baixo = texto.lower()
    # Do mais longo para o mais curto: com 'entrevista' antes de
    # 'entrevista com', "Entrevista com Joao" virava "com Joao".
    for prefixo in ('entrevista com', 'entrevista de', 'entrevista'):
        if baixo.startswith(prefixo):
            texto = texto[len(prefixo):].strip()
            break
    for sep in SEPARADORES:
        if texto.startswith(sep):
            texto = texto[len(sep):].strip()
    return texto[:180]


def anexar_pdf(ficha, arquivo):
    """Lê o PDF do currículo e completa a ficha com o que der.

    Só preenche campo vazio: o que o RH corrigiu na mão não pode ser
    sobrescrito por uma releitura.
    """
    from users.models import Sector

    from .extrator import extrair

    try:
        arquivo.seek(0)
        dados = arquivo.read()
        arquivo.seek(0)
        cidades = {s for s in Sector.objects.values_list('name', flat=True) if s}
        lido = extrair(dados, cidades_conhecidas=cidades)
    except Exception as exc:                                 # noqa: BLE001
        logger.warning('PDF da entrevista não pôde ser lido: %s', exc)
        return ficha

    ficha.arquivo = arquivo
    ficha.nome_arquivo = (getattr(arquivo, 'name', '') or '')[:255]
    ficha.texto = lido['texto']
    for campo in ('nome', 'endereco', 'cidade', 'bairro', 'telefone', 'email',
                  'experiencia'):
        if not getattr(ficha, campo):
            setattr(ficha, campo, lido[campo])
    if not ficha.cargos and lido['cargos']:
        ficha.cargos = '\n'.join(lido['cargos'])
    ficha.save()
    return ficha
