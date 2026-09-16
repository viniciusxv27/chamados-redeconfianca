"""Tarefas da IA em segundo plano: gerar, editar, imagem, vídeo, narração, template, Canva.

Uma requisição cria a `TarefaIA` e dispara uma thread depois do commit; a tela
acompanha por `GET tarefas/<id>/`. Quando a IA precisa perguntar algo, a
tarefa para em PERGUNTAS e continua quando a resposta chega (`responder`).

Nos testes, `executar` é chamado direto (sem thread) e a IA é um dublê.
"""
import copy
import json
import logging
import subprocess
import tempfile
import threading
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import close_old_connections, transaction
from django.utils import timezone

from . import formato, ia, montagem, roteiro
from .models import (Apresentacao, ConfiguracaoApresentacoes, MensagemIA, Midia, TarefaIA, TemplateApresentacao,
                     VersaoApresentacao)

logger = logging.getLogger(__name__)

MAX_RODADAS_DE_PERGUNTAS = 2
MAX_IMAGENS_IA = 6
MAX_TAREFAS_ABERTAS_POR_PESSOA = 4
PARADA_APOS = {TarefaIA.Tipo.VIDEO: timedelta(minutes=30), 'padrao': timedelta(minutes=15)}
# A thread tira a tarefa da fila (PENDENTE → RODANDO) segundos depois do commit. Na fila por mais
# que isto, ninguém vai pegá-la (o servidor reiniciou entre o pedido e o início): não faz sentido a
# tela esperar os 15 minutos de uma tarefa que está de fato rodando.
FILA_PARADA_APOS = timedelta(minutes=5)


class TarefaRecusada(Exception):
    """Pedido que não pode virar tarefa (limite, dado inválido)."""


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
def criar(tipo, usuario, *, apresentacao=None, template=None, parametros=None, iniciar_agora=True):
    abertas = TarefaIA.objects.filter(usuario=usuario, status__in=[TarefaIA.Status.PENDENTE, TarefaIA.Status.RODANDO])
    for tarefa in abertas:
        conferir_parada(tarefa)
    if abertas.filter(status__in=[TarefaIA.Status.PENDENTE, TarefaIA.Status.RODANDO]).count() >= \
            MAX_TAREFAS_ABERTAS_POR_PESSOA:
        raise TarefaRecusada('Você já tem pedidos à IA em andamento. Espere um terminar.')
    tarefa = TarefaIA.objects.create(tipo=tipo, usuario=usuario, apresentacao=apresentacao, template=template,
                                     parametros=parametros or {}, progresso={'etapa': 'Na fila'})
    if iniciar_agora:
        disparar(tarefa)
    return tarefa


def disparar(tarefa):
    """Roda depois do commit: a thread precisa enxergar a tarefa gravada."""
    transaction.on_commit(lambda: threading.Thread(
        target=_rodar_em_thread, args=(tarefa.pk,), daemon=True, name=f'apresentacoes-tarefa-{tarefa.pk}').start())


def _rodar_em_thread(tarefa_id):
    close_old_connections()
    try:
        executar(tarefa_id)
    finally:
        close_old_connections()


def conferir_parada(tarefa):
    """Tarefa RODANDO sem sinal de vida (servidor reiniciou no meio) vira ERRO."""
    if tarefa.status not in (TarefaIA.Status.PENDENTE, TarefaIA.Status.RODANDO):
        return tarefa
    if tarefa.status == TarefaIA.Status.PENDENTE:
        limite = FILA_PARADA_APOS
    else:
        limite = PARADA_APOS.get(tarefa.tipo, PARADA_APOS['padrao'])
    if tarefa.atualizado_em and timezone.now() - tarefa.atualizado_em > limite:
        _falhar(tarefa, 'O pedido foi interrompido (o servidor reiniciou no meio). Tente de novo.')
    return tarefa


def responder(tarefa, respostas):
    if tarefa.status != TarefaIA.Status.PERGUNTAS:
        raise TarefaRecusada('Esta tarefa não está esperando respostas.')
    limpas = {}
    for pergunta in tarefa.perguntas or []:
        valor = (respostas or {}).get(pergunta.get('id'))
        if isinstance(valor, list):
            valor = [formato.texto(v, 500) for v in valor if isinstance(v, str)][:10]
        else:
            valor = formato.texto(valor if isinstance(valor, str) else '', 1000)
        limpas[pergunta.get('id')] = valor
    historico = list((tarefa.parametros or {}).get('historico') or [])
    historico.append({'perguntas': tarefa.perguntas, 'respostas': limpas})
    tarefa.parametros = {**(tarefa.parametros or {}), 'historico': historico}
    tarefa.respostas = limpas
    tarefa.perguntas = []
    tarefa.status = TarefaIA.Status.PENDENTE
    tarefa.progresso = {'etapa': 'Continuando com as suas respostas'}
    tarefa.save(update_fields=['parametros', 'respostas', 'perguntas', 'status', 'progresso', 'atualizado_em'])
    if tarefa.apresentacao_id:
        MensagemIA.objects.create(apresentacao_id=tarefa.apresentacao_id, papel=MensagemIA.Papel.USUARIO,
                                  texto=roteiro.respostas_em_texto(historico[-1]['perguntas'], limpas).split('\nAgora')[0],
                                  tarefa=tarefa)
        if tarefa.tipo == TarefaIA.Tipo.GERAR:
            Apresentacao.objects.filter(pk=tarefa.apresentacao_id).update(status=Apresentacao.Status.GERANDO)
    disparar(tarefa)
    return tarefa


def executar(tarefa_id):
    tarefa = TarefaIA.objects.select_related('apresentacao', 'template', 'usuario').get(pk=tarefa_id)
    if tarefa.status not in (TarefaIA.Status.PENDENTE, TarefaIA.Status.RODANDO):
        return tarefa
    tarefa.status = TarefaIA.Status.RODANDO
    tarefa.erro = ''
    tarefa.save(update_fields=['status', 'erro', 'atualizado_em'])
    executor = EXECUTORES.get(tarefa.tipo)
    try:
        if executor is None:
            raise TarefaRecusada('Tipo de tarefa desconhecido.')
        executor(tarefa)
    except Exception as exc:                                    # noqa: BLE001 — o erro vai para a tela
        if isinstance(exc, (ia.IAIndisponivel, TarefaRecusada)):
            mensagem = str(exc)
        else:
            logger.exception('Tarefa %s (%s) falhou', tarefa.pk, tarefa.tipo)
            mensagem = _mensagem_de_erro(exc)
        _falhar(tarefa, mensagem)
        return tarefa
    if tarefa.status == TarefaIA.Status.RODANDO:
        tarefa.status = TarefaIA.Status.CONCLUIDA
        tarefa.concluida_em = timezone.now()
        tarefa.progresso = {**(tarefa.progresso or {}), 'etapa': 'Concluído'}
        tarefa.save(update_fields=['status', 'concluida_em', 'progresso', 'resultado', 'atualizado_em'])
    return tarefa


def _mensagem_de_erro(exc):
    try:
        from . import canva
        if isinstance(exc, (getattr(canva, 'CanvaErro', ()), getattr(canva, 'CanvaNaoConectado', ()),
                            getattr(canva, 'CanvaNaoConfigurado', ()))):
            return str(exc)
    except Exception:                                           # noqa: BLE001
        pass
    return ia.mensagem_de_erro(exc)


def _falhar(tarefa, mensagem):
    tarefa.status = TarefaIA.Status.ERRO
    tarefa.erro = mensagem[:2000]
    tarefa.progresso = {**(tarefa.progresso or {}), 'etapa': 'Erro'}
    tarefa.save(update_fields=['status', 'erro', 'progresso', 'atualizado_em'])
    if tarefa.tipo == TarefaIA.Tipo.GERAR and tarefa.apresentacao_id:
        Apresentacao.objects.filter(pk=tarefa.apresentacao_id).update(status=Apresentacao.Status.ERRO,
                                                                     erro=mensagem[:2000])
        MensagemIA.objects.create(apresentacao_id=tarefa.apresentacao_id, papel=MensagemIA.Papel.IA,
                                  texto=f'Não consegui gerar: {mensagem}', tarefa=tarefa)
    if tarefa.tipo == TarefaIA.Tipo.ANALISAR_TEMPLATE and tarefa.template_id:
        TemplateApresentacao.objects.filter(pk=tarefa.template_id).update(
            status=TemplateApresentacao.Status.ERRO, erro=mensagem[:2000])


def _progresso(tarefa, etapa, feito=None, total=None):
    tarefa.progresso = {'etapa': etapa, 'feito': feito, 'total': total}
    tarefa.save(update_fields=['progresso', 'atualizado_em'])


def _perguntar(tarefa, perguntas, texto='Antes de continuar, preciso saber:'):
    tarefa.perguntas = perguntas
    tarefa.status = TarefaIA.Status.PERGUNTAS
    tarefa.progresso = {'etapa': 'Aguardando suas respostas'}
    tarefa.save(update_fields=['perguntas', 'status', 'progresso', 'atualizado_em'])
    if tarefa.apresentacao_id:
        MensagemIA.objects.create(apresentacao_id=tarefa.apresentacao_id, papel=MensagemIA.Papel.IA, texto=texto,
                                  dados={'perguntas': perguntas}, tarefa=tarefa)
        if tarefa.tipo == TarefaIA.Tipo.GERAR:
            Apresentacao.objects.filter(pk=tarefa.apresentacao_id).update(status=Apresentacao.Status.PERGUNTAS)


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------
def _ler_midia(midia):
    with midia.arquivo.open('rb') as arquivo:
        return arquivo.read()


def _anexos(ids, tipos=(Midia.Tipo.IMAGEM,)):
    por_id = {m.pk: m for m in Midia.objects.filter(pk__in=ids or [], tipo__in=tipos)}
    return [por_id[i] for i in ids or [] if i in por_id]


def _imagens_para_ia(anexos):
    imagens = []
    for indice, midia in enumerate(anexos):
        try:
            imagens.append((f'Anexo {indice}: {midia.nome}', _ler_midia(midia)))
        except Exception as exc:                                # noqa: BLE001
            logger.warning('Anexo %s ilegível: %s', midia.pk, exc)
    return imagens


def _historico_em_mensagens(parametros):
    mensagens = []
    for rodada in (parametros or {}).get('historico') or []:
        mensagens.append({'role': 'assistant', 'content': json.dumps(
            {'tipo': 'perguntas', 'perguntas': rodada.get('perguntas')}, ensure_ascii=False)})
        mensagens.append({'role': 'user', 'content': roteiro.respostas_em_texto(rodada.get('perguntas'),
                                                                                  rodada.get('respostas'))})
    return mensagens


def salvar_midia(conteudo, nome, *, dono, tipo, origem, apresentacao=None, template=None, mime='', prompt='',
                 largura=None, altura=None, duracao=None):
    if tipo == Midia.Tipo.IMAGEM and (largura is None or altura is None):
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(conteudo)) as img:
                largura, altura = img.size
        except Exception:                                       # noqa: BLE001
            pass
    midia = Midia(dono=dono, apresentacao=apresentacao, template=template, tipo=tipo, origem=origem, nome=nome[:200],
                  mime=mime, tamanho=len(conteudo), largura=largura, altura=altura, duracao=duracao,
                  prompt=prompt[:4000])
    # O nome de exibição pode vir sem extensão ("Início", da captura); o arquivo no storage não pode.
    import mimetypes
    import os
    nome_arquivo = nome if os.path.splitext(nome)[1] else nome + (
        {'image/jpeg': '.jpg', 'audio/mpeg': '.mp3'}.get(mime) or mimetypes.guess_extension(mime or '') or '')
    midia.arquivo.save(nome_arquivo, ContentFile(conteudo), save=False)
    midia.save()
    return midia


def duracao_de_audio(conteudo, extensao='.mp3'):
    """Segundos do áudio pelo ffprobe (vem no Dockerfile); sem ele, None."""
    try:
        with tempfile.NamedTemporaryFile(suffix=extensao) as temporario:
            temporario.write(conteudo)
            temporario.flush()
            saida = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1',
                 temporario.name], capture_output=True, text=True, timeout=30)
        return round(float(saida.stdout.strip()), 2) if saida.returncode == 0 and saida.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _prompt_de_imagem(prompt, template):
    estilo = (template.estilo if template else '') or ''
    return (f'{prompt}\n\nVisual style: {estilo}\nWide 3:2 composition for a presentation slide, cinematic '
            f'lighting, high detail. Absolutely no text, letters, numbers, logos or watermarks in the image.')


def _avisar_no_sino(usuario, titulo, mensagem, url):
    try:
        from notifications.services import NotificationType, notification_service
        notification_service._send_in_app([usuario], titulo, mensagem, NotificationType.SYSTEM, url, 'NORMAL',
                                          'fas fa-chalkboard-user', {}, usuario)
    except Exception as exc:                                    # noqa: BLE001 — aviso não derruba a geração
        logger.warning('Aviso das apresentações não foi para o sino: %s', exc)


# ---------------------------------------------------------------------------
# GERAR
# ---------------------------------------------------------------------------
def _gerar(tarefa):
    apresentacao = tarefa.apresentacao
    cfg = ConfiguracaoApresentacoes.get()
    parametros = tarefa.parametros or {}
    opcoes = apresentacao.opcoes or {}
    template = apresentacao.template
    anexos = _anexos(parametros.get('anexos'))

    _progresso(tarefa, 'Lendo o pedido e os anexos')
    contexto = ''
    if apresentacao.origem == Apresentacao.Origem.MODULO and apresentacao.modulo:
        from .modulos import contexto_do_modulo
        contexto = contexto_do_modulo(apresentacao.modulo, opcoes.get('nome_modulo', ''))
    maximo_ia = MAX_IMAGENS_IA if opcoes.get('imagens_ia') else 0
    texto_pedido = roteiro.pedido_de_geracao(apresentacao, anexos, contexto, opcoes.get('texto_material', ''))
    mensagens = [
        {'role': 'system', 'content': roteiro.sistema(template, opcoes, maximo_ia)},
        {'role': 'user', 'content': ia.conteudo_com_imagens(texto_pedido, _imagens_para_ia(anexos))},
    ] + _historico_em_mensagens(parametros)
    rodadas = len(parametros.get('historico') or [])
    if rodadas >= MAX_RODADAS_DE_PERGUNTAS:
        mensagens.append({'role': 'user', 'content': 'Gere a apresentação agora, sem novas perguntas.'})

    _progresso(tarefa, 'Escrevendo o roteiro')
    resposta = ia.chamar_json(mensagens, roteiro.SCHEMA_ROTEIRO, 'roteiro_apresentacao', cfg.modelo_texto)
    if resposta.get('tipo') == 'perguntas' and resposta.get('perguntas') and rodadas < MAX_RODADAS_DE_PERGUNTAS:
        _perguntar(tarefa, resposta['perguntas'][:4])
        return
    slides = [s for s in resposta.get('slides') or [] if isinstance(s, dict)]
    if not slides:
        raise ia.IAIndisponivel('A IA não devolveu nenhum slide. Detalhe um pouco mais o pedido.')

    avisos = []
    imagens = {}
    for indice, item in enumerate(slides):
        pedido_imagem = item.get('imagem') or {}
        if pedido_imagem.get('fonte') == 'anexo' and isinstance(pedido_imagem.get('indice'), int) \
                and 0 <= pedido_imagem['indice'] < len(anexos):
            imagens[indice] = anexos[pedido_imagem['indice']]
    pedidos_ia = [(i, (s.get('imagem') or {}).get('prompt')) for i, s in enumerate(slides)
                  if (s.get('imagem') or {}).get('fonte') == 'ia' and (s.get('imagem') or {}).get('prompt')
                  and i not in imagens][:maximo_ia]
    for numero, (indice, prompt) in enumerate(pedidos_ia, start=1):
        _progresso(tarefa, 'Gerando imagens com IA', numero, len(pedidos_ia))
        try:
            png = ia.gerar_imagem(_prompt_de_imagem(prompt, template), cfg.modelo_imagem)
            imagens[indice] = salvar_midia(png, f'ia-slide-{indice + 1}.png', dono=tarefa.usuario,
                                           tipo=Midia.Tipo.IMAGEM, origem=Midia.Origem.IA_IMAGEM,
                                           apresentacao=apresentacao, mime='image/png', prompt=prompt)
        except Exception as exc:                                # noqa: BLE001 — sem a imagem, o slide sai sem ela
            logger.warning('Imagem da IA falhou no slide %s: %s', indice + 1, exc)
            aviso = f'Imagem do slide {indice + 1} não foi gerada: {ia.mensagem_de_erro(exc)}'
            if aviso not in avisos:
                avisos.append(aviso)

    _progresso(tarefa, 'Montando os slides no template')
    documento = montagem.montar_documento(resposta, template, imagens)
    # A fonte escolhida no pedido vale sobre a do template (o resto da identidade fica).
    for chave in ('fonte_titulo', 'fonte_texto'):
        if formato.fonte(opcoes.get(chave)):
            documento['tema'][chave] = formato.fonte(opcoes[chave])
    titulo = formato.texto(resposta.get('titulo') or apresentacao.titulo, 200) or apresentacao.titulo

    if opcoes.get('video_abertura'):
        documento = _video_de_abertura(tarefa, apresentacao, documento, cfg, template, avisos)
    if opcoes.get('narracao'):
        documento, _ = _narrar(tarefa, apresentacao, documento, cfg, avisos)

    with transaction.atomic():
        atual = Apresentacao.objects.select_for_update().get(pk=apresentacao.pk)
        if atual.documento and atual.documento.get('slides'):
            VersaoApresentacao.objects.create(apresentacao=atual, documento=atual.documento, titulo=atual.titulo,
                                              motivo='Antes de gerar de novo com a IA', criado_por=tarefa.usuario)
        atual.documento = documento
        atual.titulo = titulo
        atual.status = Apresentacao.Status.PRONTA
        atual.erro = ''
        atual.revisao += 1
        atual.save(update_fields=['documento', 'titulo', 'status', 'erro', 'revisao', 'atualizado_em'])
        VersaoApresentacao.objects.create(apresentacao=atual, documento=documento, titulo=titulo,
                                          motivo='Gerada pela IA', criado_por=tarefa.usuario)
    texto = f'Pronto: {len(documento["slides"])} slides montados no template.'
    if avisos:
        texto += ' Avisos: ' + ' '.join(avisos)
    MensagemIA.objects.create(apresentacao=apresentacao, papel=MensagemIA.Papel.IA, texto=texto, tarefa=tarefa,
                              dados={'avisos': avisos})
    tarefa.resultado = {'slides': len(documento['slides']), 'avisos': avisos}
    from django.urls import reverse
    _avisar_no_sino(tarefa.usuario, 'Sua apresentação ficou pronta', f'"{titulo}" — {len(documento["slides"])} slides.',
                    reverse('apresentacoes:editor', args=[apresentacao.pk]))


def _video_de_abertura(tarefa, apresentacao, documento, cfg, template, avisos):
    _progresso(tarefa, 'Gerando o vídeo de abertura (pode levar alguns minutos)')
    titulo = apresentacao.titulo or 'apresentação'
    prompt = (f'Opening cinematic shot for a corporate presentation about "{titulo}". '
              f'{(template.estilo if template else "")} Slow camera move, no text on screen.')
    try:
        mp4 = ia.gerar_video(prompt, cfg.modelo_video, segundos='8', tamanho='1280x720',
                             progresso=lambda p: _progresso(tarefa, f'Gerando o vídeo de abertura ({p}%)'))
        midia = salvar_midia(mp4, 'abertura.mp4', dono=tarefa.usuario, tipo=Midia.Tipo.VIDEO,
                             origem=Midia.Origem.IA_VIDEO, apresentacao=apresentacao, mime='video/mp4',
                             prompt=prompt, largura=1280, altura=720, duracao=8)
    except Exception as exc:                                    # noqa: BLE001
        avisos.append(f'Vídeo de abertura não foi gerado: {ia.mensagem_de_erro(exc)}')
        return documento
    documento = copy.deepcopy(documento)
    if documento['slides']:
        documento['slides'][0]['elementos'].insert(0, {
            'id': formato.novo_id('e'), 'tipo': 'video', 'slot': '', 'x': 0, 'y': 0, 'w': 1920, 'h': 1080,
            'rotacao': 0, 'opacidade': 0.55, 'bloqueado': True, 'link': '', 'src': midia.url, 'midia': midia.pk,
            'poster': '', 'autoplay': True, 'loop': True, 'mudo': True, 'controles': False, 'raio': 0,
            'ajuste': 'cover', 'animacao': {'tipo': 'fade', 'duracao': 1200, 'atraso': 0, 'gatilho': 'junto'}})
    return formato.sanear_documento(documento)


def _narrar(tarefa, apresentacao, documento, cfg, avisos, somente=None):
    """Grava a narração de cada slide (das notas). Devolve (documento, {slide_id: narração})."""
    documento = copy.deepcopy(documento)
    slides = [s for s in documento['slides'] if not s.get('oculto') and (somente is None or s['id'] in somente)]
    sem_notas = [s for s in slides if not (s.get('notas') or '').strip()]
    if sem_notas:
        _progresso(tarefa, 'Escrevendo o roteiro da narração')
        resposta = ia.chamar_json([
            {'role': 'system', 'content': 'Você escreve narrações curtas (2 a 4 frases, português do Brasil, tom de '
                                          'apresentação) para slides. Responda no JSON pedido.'},
            {'role': 'user', 'content': 'Escreva as notas faladas destes slides:\n' + roteiro.resumo_documento(
                {'slides': sem_notas})},
        ], roteiro.SCHEMA_NOTAS, 'notas_narracao', cfg.modelo_texto, max_tokens=6000)
        por_id = {n['slide_id']: n['texto'] for n in resposta.get('notas') or []}
        for slide in sem_notas:
            slide['notas'] = formato.texto(por_id.get(slide['id'], ''), formato.MAX_NOTAS)
    narracoes = {}
    total = len([s for s in slides if s.get('notas')])
    feito = 0
    for slide in slides:
        if not (slide.get('notas') or '').strip():
            continue
        feito += 1
        _progresso(tarefa, 'Gravando a narração', feito, total)
        try:
            mp3 = ia.gerar_voz(slide['notas'], cfg.modelo_voz, cfg.voz)
            duracao = duracao_de_audio(mp3) or round(len(slide['notas'].split()) / 2.6, 1)
            midia = salvar_midia(mp3, f'narracao-{slide["id"]}.mp3', dono=tarefa.usuario, tipo=Midia.Tipo.AUDIO,
                                 origem=Midia.Origem.IA_VOZ, apresentacao=apresentacao, mime='audio/mpeg',
                                 duracao=duracao, prompt=slide['notas'])
        except Exception as exc:                                # noqa: BLE001
            avisos.append(f'Narração não foi gravada: {ia.mensagem_de_erro(exc)}')
            break
        slide['narracao'] = {'midia': midia.pk, 'url': midia.url, 'duracao': duracao}
        narracoes[slide['id']] = slide['narracao']
    return formato.sanear_documento(documento), narracoes


# ---------------------------------------------------------------------------
# EDITAR, REFAZER_SLIDE, NOVO_SLIDE, TEXTO
# ---------------------------------------------------------------------------
def _documento_de_trabalho(tarefa):
    enviado = (tarefa.parametros or {}).get('documento')
    if isinstance(enviado, dict) and enviado.get('slides') is not None:
        return formato.sanear_documento(enviado)
    return formato.sanear_documento(tarefa.apresentacao.documento)


def _editar(tarefa):
    cfg = ConfiguracaoApresentacoes.get()
    apresentacao = tarefa.apresentacao
    parametros = tarefa.parametros or {}
    documento = _documento_de_trabalho(tarefa)
    anexos = _anexos(parametros.get('midias'))
    _progresso(tarefa, 'Lendo a apresentação')
    texto = ('Apresentação atual:\n' + roteiro.resumo_documento(documento) + '\n\nPedido de alteração: '
             + formato.texto(parametros.get('instrucao'), 4000) + '\n\n' + roteiro.descricao_anexos(anexos))
    mensagens = [
        {'role': 'system', 'content': roteiro.sistema_edicao(apresentacao.template, 2)},
        {'role': 'user', 'content': ia.conteudo_com_imagens(texto, _imagens_para_ia(anexos))},
    ] + _historico_em_mensagens(parametros)
    rodadas = len(parametros.get('historico') or [])
    _progresso(tarefa, 'Pensando nas alterações')
    resposta = ia.chamar_json(mensagens, roteiro.SCHEMA_EDICAO, 'edicao_apresentacao', cfg.modelo_texto)
    if resposta.get('perguntas') and not resposta.get('operacoes') and rodadas < MAX_RODADAS_DE_PERGUNTAS:
        _perguntar(tarefa, resposta['perguntas'][:4])
        return
    _progresso(tarefa, 'Aplicando as alterações')
    novo, aplicadas = aplicar_operacoes(documento, resposta, apresentacao.template, anexos)
    MensagemIA.objects.create(apresentacao=apresentacao, papel=MensagemIA.Papel.IA, tarefa=tarefa,
                              texto=formato.texto(resposta.get('resumo'), 1000) or 'Alterações prontas.')
    tarefa.resultado = {'documento': novo, 'resumo': formato.texto(resposta.get('resumo'), 1000),
                        'operacoes': aplicadas}


def aplicar_operacoes(documento, resposta, template, anexos=()):
    """Aplica as operações da IA numa cópia. Devolve (documento saneado, quantas valeram)."""
    doc = copy.deepcopy(documento)
    template_doc = (template.documento if template else None) or {}
    tema = doc.get('tema') or formato.TEMA_PADRAO
    slides = doc['slides']
    aplicadas = 0

    def achar(slide_id):
        return next((s for s in slides if s['id'] == slide_id), None)

    for op in resposta.get('operacoes') or []:
        tipo = op.get('op')
        slide = achar(op.get('slide_id'))
        if tipo == 'editar_texto' and slide:
            el = next((e for e in slide['elementos'] if e['id'] == op.get('elemento_id')), None)
            if el and el['tipo'] == 'texto' and op.get('texto') is not None:
                el['html'] = formato.texto_para_html(op['texto'])
                aplicadas += 1
        elif tipo == 'notas' and slide and op.get('texto') is not None:
            slide['notas'] = op['texto']
            slide['narracao'] = None
            aplicadas += 1
        elif tipo == 'remover_slide' and slide:
            slides.remove(slide)
            aplicadas += 1
        elif tipo in ('ocultar_slide', 'mostrar_slide') and slide:
            slide['oculto'] = tipo == 'ocultar_slide'
            aplicadas += 1
        elif tipo == 'mover_slide' and slide and isinstance(op.get('posicao'), int):
            slides.remove(slide)
            slides.insert(max(0, min(len(slides), op['posicao'] - 1)), slide)
            aplicadas += 1
        elif tipo == 'refazer_slide' and slide and isinstance(op.get('slide'), dict):
            imagem = _imagem_do_item(op['slide'], anexos)
            novo = montagem.montar_slide(op['slide'], template_doc, tema, imagem, slide_id=slide['id'])
            slides[slides.index(slide)] = novo
            aplicadas += 1
        elif tipo == 'novo_slide' and isinstance(op.get('slide'), dict):
            imagem = _imagem_do_item(op['slide'], anexos)
            novo = montagem.montar_slide(op['slide'], template_doc, tema, imagem)
            posicao = op.get('posicao') if isinstance(op.get('posicao'), int) else len(slides)
            slides.insert(max(0, min(len(slides), posicao)), novo)
            aplicadas += 1
    mudanca_tema = resposta.get('tema') or {}
    if mudanca_tema:
        if formato.fonte(mudanca_tema.get('fonte_titulo')):
            tema['fonte_titulo'] = formato.fonte(mudanca_tema['fonte_titulo'])
        if formato.fonte(mudanca_tema.get('fonte_texto')):
            tema['fonte_texto'] = formato.fonte(mudanca_tema['fonte_texto'])
        for chave in ('primaria', 'destaque'):
            valor = formato.cor(mudanca_tema.get(chave))
            if valor and valor.startswith('#'):
                tema['cores'][chave] = valor
        doc['tema'] = tema
        aplicadas += 1
    return formato.sanear_documento(doc), aplicadas


def _imagem_do_item(item, anexos):
    pedido = item.get('imagem') or {}
    if pedido.get('fonte') == 'anexo' and isinstance(pedido.get('indice'), int) and 0 <= pedido['indice'] < len(anexos):
        return anexos[pedido['indice']]
    return None


def _slide_unico(tarefa, instrucao_extra):
    cfg = ConfiguracaoApresentacoes.get()
    apresentacao = tarefa.apresentacao
    parametros = tarefa.parametros or {}
    documento = _documento_de_trabalho(tarefa)
    anexos = _anexos(parametros.get('midias'))
    slide_atual = next((s for s in documento['slides'] if s['id'] == parametros.get('slide_id')), None)
    texto = ('Apresentação atual:\n' + roteiro.resumo_documento(documento, 20000)
             + (f'\n\nSlide em foco: id={slide_atual["id"]}' if slide_atual else '')
             + f'\n\n{instrucao_extra}\nPedido: ' + formato.texto(parametros.get('instrucao'), 4000)
             + '\n\n' + roteiro.descricao_anexos(anexos))
    gerar_imagem = bool((parametros.get('opcoes') or {}).get('imagens_ia'))
    mensagens = [
        {'role': 'system', 'content': roteiro.sistema(apresentacao.template, apresentacao.opcoes or {},
                                                      1 if gerar_imagem else 0)},
        {'role': 'user', 'content': ia.conteudo_com_imagens(texto, _imagens_para_ia(anexos))},
    ] + _historico_em_mensagens(parametros)
    rodadas = len(parametros.get('historico') or [])
    _progresso(tarefa, 'Escrevendo o slide')
    resposta = ia.chamar_json(mensagens, roteiro.SCHEMA_ROTEIRO, 'slide_apresentacao', cfg.modelo_texto, 8000)
    if resposta.get('tipo') == 'perguntas' and resposta.get('perguntas') and rodadas < MAX_RODADAS_DE_PERGUNTAS:
        _perguntar(tarefa, resposta['perguntas'][:4])
        return None, None, None
    itens = [s for s in resposta.get('slides') or [] if isinstance(s, dict)]
    if not itens:
        raise ia.IAIndisponivel('A IA não devolveu o slide. Detalhe o pedido.')
    template_doc = (apresentacao.template.documento if apresentacao.template else None) or {}
    tema = documento.get('tema') or formato.TEMA_PADRAO
    montados = []
    for item in itens[:5]:
        imagem = _imagem_do_item(item, anexos)
        pedido = item.get('imagem') or {}
        if imagem is None and gerar_imagem and pedido.get('fonte') == 'ia' and pedido.get('prompt'):
            _progresso(tarefa, 'Gerando a imagem do slide')
            try:
                png = ia.gerar_imagem(_prompt_de_imagem(pedido['prompt'], apresentacao.template), cfg.modelo_imagem)
                imagem = salvar_midia(png, 'ia-slide.png', dono=tarefa.usuario, tipo=Midia.Tipo.IMAGEM,
                                      origem=Midia.Origem.IA_IMAGEM, apresentacao=apresentacao, mime='image/png',
                                      prompt=pedido['prompt'])
            except Exception as exc:                            # noqa: BLE001
                logger.warning('Imagem do slide não foi gerada: %s', exc)
        montados.append((item, imagem))
    return documento, template_doc, (tema, montados, slide_atual)


def _refazer_slide(tarefa):
    documento, template_doc, extra = _slide_unico(
        tarefa, 'Refaça SOMENTE o slide em foco seguindo o pedido: devolva tipo "apresentacao" com exatamente 1 slide.')
    if documento is None:
        return
    tema, montados, slide_atual = extra
    if not slide_atual:
        raise TarefaRecusada('O slide não existe mais nesta apresentação.')
    item, imagem = montados[0]
    novo = montagem.montar_slide(item, template_doc, tema, imagem, slide_id=slide_atual['id'])
    novo = formato.sanear_slide(novo)
    tarefa.resultado = {'slide': novo, 'slide_id': slide_atual['id']}


def _novo_slide(tarefa):
    documento, template_doc, extra = _slide_unico(
        tarefa, 'Crie o(s) slide(s) NOVO(S) pedido(s) para entrar depois do slide em foco: tipo "apresentacao" com '
                '1 slide (no máximo 3 se o pedido pedir mais), sem capa e sem encerramento.')
    if documento is None:
        return
    tema, montados, slide_atual = extra
    novos = [formato.sanear_slide(montagem.montar_slide(item, template_doc, tema, imagem)) for item, imagem in montados]
    tarefa.resultado = {'slides': novos, 'depois_de': slide_atual['id'] if slide_atual else None}


def _texto(tarefa):
    cfg = ConfiguracaoApresentacoes.get()
    parametros = tarefa.parametros or {}
    atual = formato.html_para_texto(formato.sanear_html(parametros.get('html')))
    _progresso(tarefa, 'Reescrevendo o texto')
    resposta = ia.chamar_json([
        {'role': 'system', 'content': 'Você reescreve textos de slides em português do Brasil: curto, claro, sem '
                                      'inventar dados. Mantenha as quebras de linha que fizerem sentido. Responda '
                                      'no JSON pedido.'},
        {'role': 'user', 'content': f'Texto atual:\n{atual}\n\nComo reescrever: '
                                    f'{formato.texto(parametros.get("instrucao"), 1000) or "melhore"}'},
    ], roteiro.SCHEMA_TEXTO, 'texto_slide', cfg.modelo_texto, 2000)
    tarefa.resultado = {'html': formato.texto_para_html(formato.texto(resposta.get('texto'), 5000)),
                        'elemento_id': parametros.get('elemento_id'), 'slide_id': parametros.get('slide_id')}


# ---------------------------------------------------------------------------
# IMAGEM, VIDEO, NARRACAO
# ---------------------------------------------------------------------------
FORMATOS_IMAGEM = {'paisagem': '1536x1024', 'quadrado': '1024x1024', 'retrato': '1024x1536'}


def _imagem(tarefa):
    cfg = ConfiguracaoApresentacoes.get()
    parametros = tarefa.parametros or {}
    prompt = formato.texto(parametros.get('instrucao'), 3000).strip()
    if not prompt:
        raise TarefaRecusada('Descreva a imagem que você quer.')
    tamanho = FORMATOS_IMAGEM.get((parametros.get('opcoes') or {}).get('formato'), '1536x1024')
    _progresso(tarefa, 'Gerando a imagem')
    png = ia.gerar_imagem(_prompt_de_imagem(prompt, tarefa.apresentacao.template if tarefa.apresentacao else None),
                          cfg.modelo_imagem, tamanho)
    midia = salvar_midia(png, 'imagem-ia.png', dono=tarefa.usuario, tipo=Midia.Tipo.IMAGEM,
                         origem=Midia.Origem.IA_IMAGEM, apresentacao=tarefa.apresentacao, mime='image/png',
                         prompt=prompt)
    tarefa.resultado = {'midia': midia.como_json(), 'elemento_id': parametros.get('elemento_id'),
                        'slide_id': parametros.get('slide_id')}


def _video(tarefa):
    cfg = ConfiguracaoApresentacoes.get()
    parametros = tarefa.parametros or {}
    prompt = formato.texto(parametros.get('instrucao'), 3000).strip()
    if not prompt:
        raise TarefaRecusada('Descreva o vídeo que você quer.')
    segundos = str((parametros.get('opcoes') or {}).get('segundos') or '8')
    if segundos not in ('4', '8', '12', '16', '20'):
        segundos = '8'
    _progresso(tarefa, 'Gerando o vídeo (pode levar alguns minutos)')
    mp4 = ia.gerar_video(prompt, cfg.modelo_video, segundos=segundos, tamanho='1280x720',
                         progresso=lambda p: _progresso(tarefa, f'Gerando o vídeo ({p}%)'))
    midia = salvar_midia(mp4, 'video-ia.mp4', dono=tarefa.usuario, tipo=Midia.Tipo.VIDEO, origem=Midia.Origem.IA_VIDEO,
                         apresentacao=tarefa.apresentacao, mime='video/mp4', prompt=prompt, largura=1280, altura=720,
                         duracao=float(segundos))
    tarefa.resultado = {'midia': midia.como_json(), 'slide_id': parametros.get('slide_id')}


def _narracao(tarefa):
    cfg = ConfiguracaoApresentacoes.get()
    documento = _documento_de_trabalho(tarefa)
    avisos = []
    somente = (tarefa.parametros or {}).get('slides')
    novo, narracoes = _narrar(tarefa, tarefa.apresentacao, documento, cfg, avisos,
                              somente=set(somente) if isinstance(somente, list) and somente else None)
    if not narracoes and avisos:
        raise ia.IAIndisponivel(avisos[0])
    notas = {s['id']: s['notas'] for s in novo['slides']}
    tarefa.resultado = {'narracoes': narracoes, 'notas': notas, 'avisos': avisos}


# ---------------------------------------------------------------------------
# Template e Canva
# ---------------------------------------------------------------------------
def _analisar_template(tarefa):
    from .importacao import analisar_template
    analisar_template(tarefa, _progresso)


def leitor_de_midia(usuario, apresentacao):
    """Leitor de mídia da exportação: só entra o que é da apresentação ou que a pessoa pode ver."""
    from . import exportar_pptx
    from .permissoes import pode_ver_midia

    def permitir(midia):
        return midia.apresentacao_id == apresentacao.pk or pode_ver_midia(usuario, midia)
    return lambda src: exportar_pptx.ler_midia_padrao(src, permitir=permitir)


def _canva(tarefa):
    from . import canva, exportar_pptx
    apresentacao = tarefa.apresentacao
    _progresso(tarefa, 'Gerando o arquivo PowerPoint')
    conteudo = exportar_pptx.gerar_pptx(apresentacao.documento, apresentacao.titulo,
                                        ler_midia=leitor_de_midia(tarefa.usuario, apresentacao))
    _progresso(tarefa, 'Enviando ao Canva')
    job = canva.iniciar_importacao(tarefa.usuario, apresentacao.titulo, conteudo)
    _progresso(tarefa, 'O Canva está importando os slides')
    job = canva.aguardar_importacao(tarefa.usuario, job['id'])
    design = ((job.get('result') or {}).get('designs') or [{}])[0]
    urls = design.get('urls') or {}
    if not urls.get('edit_url'):
        raise ia.IAIndisponivel('O Canva não devolveu o link do design importado.')
    apresentacao.canva_design_id = formato.texto(design.get('id'), 80)
    apresentacao.canva_edit_url = urls['edit_url'][:1000]
    apresentacao.save(update_fields=['canva_design_id', 'canva_edit_url', 'atualizado_em'])
    tarefa.resultado = {'edit_url': urls['edit_url'], 'view_url': urls.get('view_url', '')}


EXECUTORES = {
    TarefaIA.Tipo.GERAR: _gerar,
    TarefaIA.Tipo.EDITAR: _editar,
    TarefaIA.Tipo.REFAZER_SLIDE: _refazer_slide,
    TarefaIA.Tipo.NOVO_SLIDE: _novo_slide,
    TarefaIA.Tipo.TEXTO: _texto,
    TarefaIA.Tipo.IMAGEM: _imagem,
    TarefaIA.Tipo.VIDEO: _video,
    TarefaIA.Tipo.NARRACAO: _narracao,
    TarefaIA.Tipo.ANALISAR_TEMPLATE: _analisar_template,
    TarefaIA.Tipo.CANVA: _canva,
}
