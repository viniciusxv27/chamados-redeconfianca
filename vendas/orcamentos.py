"""Orçamento pelo WhatsApp: o modelo da mensagem, a conferência dos campos e o envio."""
from datetime import timedelta

from django.utils import timezone

from core import evolution
from core.telefone import normalizar

from . import slv
from .models import Orcamento

LIMITE_TEXTO = 2000
JANELA_REPETIDO = timedelta(minutes=2)
MODELO = ('Olá, *{nome}*! 👋\n\n'
          'Aqui é *{vendedor}*, da *{empresa}*.\n'
          'Preparamos este orçamento para você:\n\n'
          '📋 *Orçamento · {pilar}*\n'
          '{texto}\n\n'
          'Qualquer dúvida, é só falar comigo na loja. Será um prazer atender você! 😊')


def nome_do_vendedor(user):
    return (user.get_full_name() or user.get_username()).title()


def modelo_do_vendedor(user):
    """O modelo com o vendedor e a loja já preenchidos: a prévia da tela usa este mesmo texto."""
    loja = user.sector
    empresa = f'Rede Confiança · {loja.name}' if loja and loja.name.lower().startswith('loja') else 'Rede Confiança'
    return MODELO.replace('{vendedor}', nome_do_vendedor(user)).replace('{empresa}', empresa)


def ler_campos(post):
    """Devolve (campos, erros). A mensagem só sai com tudo certo."""
    nome = ' '.join((post.get('cliente_nome') or '').split())[:200]
    pilar = post.get('pilar') or ''
    bruto = post.get('numero') or ''
    texto = (post.get('texto') or '').strip()
    erros = []
    if not nome:
        erros.append('Informe o nome do cliente.')
    if pilar not in dict(Orcamento.PILARES):
        erros.append('Escolha o pilar.')
    numero = normalizar(bruto)
    if not numero or slv.numero_fake(bruto):
        erros.append('Número do cliente: use DDD + número de verdade.')
    if not texto:
        erros.append('Escreva o orçamento.')
    elif len(texto) > LIMITE_TEXTO:
        erros.append(f'O orçamento passa de {LIMITE_TEXTO} caracteres.')
    return {'cliente_nome': nome, 'pilar': pilar, 'numero': numero, 'texto': texto}, erros


def ja_enviado(usuario, campos):
    """O mesmo orçamento acabou de sair? (clique duplo, voltar e reenviar). Cliente não recebe duas vezes."""
    return Orcamento.objects.filter(
        vendedor=usuario, numero=campos['numero'], pilar=campos['pilar'], texto=campos['texto'],
        status__in=('ENVIANDO', 'ENVIADO'), criado_em__gte=timezone.now() - JANELA_REPETIDO).exists()


def criar(usuario, campos):
    mensagem = (modelo_do_vendedor(usuario).replace('{nome}', campos['cliente_nome'])
                .replace('{pilar}', dict(Orcamento.PILARES)[campos['pilar']]).replace('{texto}', campos['texto']))
    return Orcamento.objects.create(mensagem=mensagem, vendedor=usuario, vendedor_nome=nome_do_vendedor(usuario)[:200],
                                    loja=usuario.sector, **campos)


def enviar(orcamento):
    """Manda pelo WhatsApp e grava o resultado. Devolve True se saiu."""
    orcamento.tentativas += 1
    try:
        ok, detalhe = evolution.enviar_texto(orcamento.numero, orcamento.mensagem, timeout=15)
    except Exception as exc:  # noqa: BLE001 — a falha fica no registro, para tentar de novo
        ok, detalhe = False, str(exc)
    orcamento.status = 'ENVIADO' if ok else 'FALHA'
    orcamento.erro = '' if ok else str(detalhe)[:255]
    orcamento.enviado_em = timezone.now() if ok else None
    orcamento.save(update_fields=['status', 'erro', 'enviado_em', 'tentativas'])
    return ok
