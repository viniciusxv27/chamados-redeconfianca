"""Regras da Rotina Gerencial: a semana exibida, a validação das atividades,
quem pode mexer em quê, a cópia de modelos e o aviso de início.

A tela esconde botões, mas quem decide é daqui: a API e as views passam por
estas funções, então cada regra existe num lugar só.
"""
import logging
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import (
    CORES_CATEGORIA, DIAS_SEMANA, AtividadeModelo, AtividadeRotina, AvisoRotina, Categoria,
    ModeloRotina, erros_de_horario,
)
from .permissoes import e_superadmin

logger = logging.getLogger(__name__)

MESES = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
         'setembro', 'outubro', 'novembro', 'dezembro')
DIAS_CURTOS = ('Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb')

LIMITE_TITULO = 150
LIMITE_DESCRICAO = 2000
CAMPOS_TEXTO = ('titulo', 'descricao', 'categoria')
CAMPOS_COPIA = ('dia_semana', 'inicio', 'fim', 'titulo', 'descricao', 'categoria', 'bloqueada')

# O aviso de início vale do começo da atividade até ela terminar (nas bem
# curtas, até 10 minutos depois de começar). Um pouco antes também passa: o
# relógio de quem pede pode estar adiantado.
JANELA_AVISO = timedelta(minutes=10)
ADIANTAMENTO_ACEITO = timedelta(minutes=2)

CATEGORIA_SINO = 'Rotina Gerencial'


class ErroValidacao(Exception):
    """Dado recusado: vira HTTP 400 com a mensagem para a pessoa."""


class SemPermissao(Exception):
    """Ação proibida para quem pediu: vira HTTP 403."""


class NaoEncontrado(Exception):
    """Objeto inexistente ou fora do alcance de quem pediu: vira HTTP 404."""


# ---------------------------------------------------------------------------
# Relógio e semana
# ---------------------------------------------------------------------------
def agora():
    """Hora local do portal. Função própria para os testes poderem fixar o relógio."""
    return timezone.localtime()


def segunda_da_semana(dia):
    """Segunda-feira da semana exibida.

    No domingo a semana de trabalho já acabou: mostra a que começa amanhã.
    """
    if dia.weekday() == 6:
        return dia + timedelta(days=1)
    return dia - timedelta(days=dia.weekday())


def rotulo_semana(segunda, sabado):
    if segunda.month == sabado.month:
        return f'{segunda.day} a {sabado.day} de {MESES[sabado.month - 1]} de {sabado.year}'
    if segunda.year == sabado.year:
        return (f'{segunda.day} de {MESES[segunda.month - 1]} a '
                f'{sabado.day} de {MESES[sabado.month - 1]} de {sabado.year}')
    return (f'{segunda.day} de {MESES[segunda.month - 1]} de {segunda.year} a '
            f'{sabado.day} de {MESES[sabado.month - 1]} de {sabado.year}')


def dados_da_semana(momento=None):
    momento = momento or agora()
    hoje = momento.date()
    segunda = segunda_da_semana(hoje)
    sabado = segunda + timedelta(days=5)
    dias = []
    for numero, nome in DIAS_SEMANA:
        data = segunda + timedelta(days=numero)
        dias.append({
            'dia_semana': numero, 'data': data.isoformat(), 'nome': nome,
            'curto': DIAS_CURTOS[numero], 'numero': data.day, 'hoje': data == hoje,
        })
    return {
        'inicio': segunda.isoformat(),
        'fim': sabado.isoformat(),
        'rotulo': rotulo_semana(segunda, sabado),
        'proxima': hoje.weekday() == 6,
        'dias': dias,
        # Relógio do servidor para o navegador: a hora "de parede" (sem fuso),
        # que é o que o calendário desenha, e o instante exato, para contar o
        # tempo sem depender do relógio do aparelho.
        'agora': momento.strftime('%Y-%m-%dT%H:%M:%S'),
        'agora_ts': int(momento.timestamp() * 1000),
    }


def categorias():
    """A legenda, na ordem da planilha: vermelho, amarelo, azul-claro."""
    return [{'valor': valor, 'rotulo': rotulo, **CORES_CATEGORIA[valor]}
            for valor, rotulo in Categoria.choices]


def _instante(dia, hora):
    return timezone.make_aware(datetime.combine(dia, hora), timezone.get_current_timezone())


def url_da_atividade(atividade):
    """Para onde o aviso leva: a rotina, já no dia e com a atividade em destaque."""
    return f"{reverse('rotina:minha')}?dia={atividade.dia_semana}&atividade={atividade.id}"


def nome_de(user):
    if user is None:
        return ''
    return getattr(user, 'full_name', '') or user.get_username()


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------
def permissoes_da_atividade(user, atividade, rotina=None):
    """O que `user` pode fazer com uma atividade de rotina.

    - SUPERADMIN: tudo.
    - A dona da rotina (ativa): move e muda a duração das que não estão
      travadas; renomeia e apaga só as que ela mesma criou, e só enquanto a
      rotina permitir criar.
    - Qualquer outra pessoa: nada.
    """
    if e_superadmin(user):
        return {'pode_mover': True, 'pode_editar': True, 'pode_excluir': True}
    rotina = rotina or atividade.rotina
    dona = bool(user and user.is_authenticated and rotina.user_id == user.id and rotina.ativa)
    livre = dona and not atividade.bloqueada
    propria = livre and atividade.criada_pela_pessoa and rotina.pode_criar
    return {'pode_mover': livre, 'pode_editar': propria, 'pode_excluir': propria}


def conferir_rotina_ativa(rotina):
    if not rotina.ativa:
        raise SemPermissao('Sua rotina gerencial está desativada.')


def conferir_edicao_da_pessoa(atividade, limpos=None):
    """Barra o que a dona da rotina não pode mudar numa atividade.

    Sem `limpos`, confere só o que independe do pedido (rotina ativa,
    atividade travada) — dá para chamar antes de validar os dados, e quem
    tenta mexer no que está travado recebe "travada", não "horário inválido".
    """
    rotina = atividade.rotina
    conferir_rotina_ativa(rotina)
    if atividade.bloqueada:
        raise SemPermissao('Esta atividade tem horário travado pela gestão e não pode ser mudada.')
    if limpos is None:
        return
    if 'bloqueada' in limpos and limpos['bloqueada'] != atividade.bloqueada:
        raise SemPermissao('Só a gestão pode travar ou destravar uma atividade.')
    mudou_texto = any(campo in limpos and limpos[campo] != getattr(atividade, campo)
                      for campo in CAMPOS_TEXTO)
    if mudou_texto and not (atividade.criada_pela_pessoa and rotina.pode_criar):
        raise SemPermissao('Nesta atividade você só pode mudar o dia e o horário.')


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------
def _texto(corpo, campo):
    valor = corpo.get(campo)
    if valor is None:
        return ''
    if not isinstance(valor, str):
        raise ErroValidacao('Dados inválidos.')
    return valor.strip()


def _dia(valor):
    if isinstance(valor, bool) or (isinstance(valor, float) and not valor.is_integer()):
        raise ErroValidacao('Dia da semana inválido.')
    try:
        dia = int(valor)
    except (TypeError, ValueError):
        raise ErroValidacao('Dia da semana inválido.') from None
    if not 0 <= dia <= 5:
        raise ErroValidacao('Escolha um dia de segunda a sábado.')
    return dia


def _hora(valor, rotulo):
    if isinstance(valor, str):
        partes = valor.strip().split(':')
        if len(partes) in (2, 3) and all(parte.isdigit() for parte in partes):
            try:
                return time(int(partes[0]), int(partes[1]))
            except ValueError:
                pass
    raise ErroValidacao(f'Horário de {rotulo} inválido: use o formato HH:MM.')


def ler_dados_atividade(corpo, atual=None):
    """Valida o JSON de uma atividade e devolve só os campos limpos.

    Criando (`atual` None), título, início e fim são obrigatórios e o dia pode
    vir como `repetir_em` (lista) — devolvido em `dias`. Editando, só o que
    foi enviado muda, mas a checagem de horário olha o resultado final: mandar
    só um início que passa do fim atual também é recusado.
    """
    if not isinstance(corpo, dict):
        raise ErroValidacao('Dados inválidos.')
    criando = atual is None
    limpos = {}

    if criando or 'titulo' in corpo:
        titulo = _texto(corpo, 'titulo')
        if not titulo:
            raise ErroValidacao('Informe o título da atividade.')
        if len(titulo) > LIMITE_TITULO:
            raise ErroValidacao(f'O título pode ter no máximo {LIMITE_TITULO} caracteres.')
        limpos['titulo'] = titulo

    if criando or 'descricao' in corpo:
        descricao = _texto(corpo, 'descricao')
        if len(descricao) > LIMITE_DESCRICAO:
            raise ErroValidacao(f'A descrição pode ter no máximo {LIMITE_DESCRICAO} caracteres.')
        limpos['descricao'] = descricao

    if criando or 'categoria' in corpo:
        categoria = corpo.get('categoria') or (Categoria.COMPLEMENTAR if criando else None)
        if not isinstance(categoria, str) or categoria not in Categoria.values:
            raise ErroValidacao('Categoria inválida.')
        limpos['categoria'] = str(categoria)

    if 'bloqueada' in corpo:
        if not isinstance(corpo['bloqueada'], bool):
            raise ErroValidacao('Valor inválido para "horário travado".')
        limpos['bloqueada'] = corpo['bloqueada']
    elif criando:
        limpos['bloqueada'] = False

    if criando:
        repetir = corpo.get('repetir_em')
        if repetir not in (None, '', []):
            if not isinstance(repetir, list):
                raise ErroValidacao('Dias para repetir inválidos.')
            limpos['dias'] = sorted({_dia(valor) for valor in repetir})
        elif 'dia_semana' in corpo:
            limpos['dias'] = [_dia(corpo['dia_semana'])]
        else:
            raise ErroValidacao('Escolha pelo menos um dia da semana.')
    elif 'dia_semana' in corpo:
        limpos['dia_semana'] = _dia(corpo['dia_semana'])

    if criando and ('inicio' not in corpo or 'fim' not in corpo):
        raise ErroValidacao('Informe o horário de início e de fim.')
    if 'inicio' in corpo:
        limpos['inicio'] = _hora(corpo['inicio'], 'início')
    if 'fim' in corpo:
        limpos['fim'] = _hora(corpo['fim'], 'fim')

    inicio = limpos.get('inicio', getattr(atual, 'inicio', None))
    fim = limpos.get('fim', getattr(atual, 'fim', None))
    dias = limpos.get('dias') or [limpos.get('dia_semana', getattr(atual, 'dia_semana', None))]
    for dia in dias:
        erros = erros_de_horario(dia, inicio, fim)
        if erros:
            raise ErroValidacao(erros[0])
    return limpos


# ---------------------------------------------------------------------------
# Serialização
# ---------------------------------------------------------------------------
def serializar_atividade(atividade, permissoes=None):
    cor = atividade.cor
    dados = {
        'id': atividade.id,
        'titulo': atividade.titulo,
        'descricao': atividade.descricao,
        'categoria': atividade.categoria,
        'categoria_rotulo': atividade.get_categoria_display(),
        'categoria_nome': cor['nome'],
        'cor': cor['cor'],
        'dia_semana': atividade.dia_semana,
        'inicio': f'{atividade.inicio:%H:%M}',
        'fim': f'{atividade.fim:%H:%M}',
        'bloqueada': atividade.bloqueada,
        'criada_pela_pessoa': getattr(atividade, 'criada_pela_pessoa', False),
    }
    dados.update(permissoes or {'pode_mover': True, 'pode_editar': True, 'pode_excluir': True})
    return dados


def dados_da_rotina(rotina):
    return {
        'id': rotina.id,
        'usuario': {'id': rotina.user_id, 'nome': nome_de(rotina.user)},
        'ativa': rotina.ativa,
        'pode_criar': rotina.pode_criar,
    }


def payload_rotina(rotina, quem_ve, momento=None):
    return {
        'ok': True,
        'rotina': dados_da_rotina(rotina),
        'semana': dados_da_semana(momento),
        'atividades': [serializar_atividade(a, permissoes_da_atividade(quem_ve, a, rotina))
                       for a in rotina.atividades.all()],
    }


def payload_modelo(modelo, momento=None):
    return {
        'ok': True,
        'modelo': {'id': modelo.id, 'nome': modelo.nome, 'descricao': modelo.descricao,
                   'ativo': modelo.ativo},
        'semana': dados_da_semana(momento),
        'atividades': [serializar_atividade(a) for a in modelo.atividades.all()],
    }


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------
def marcar_atualizacao(rotina, por):
    rotina.atualizado_por = por
    rotina.save(update_fields=['atualizado_por', 'atualizado_em'])


def _copias(atividades, **extra):
    return [{**{campo: getattr(a, campo) for campo in CAMPOS_COPIA}, **extra} for a in atividades]


@transaction.atomic
def criar_atividades_rotina(rotina, limpos, por, criada_pela_pessoa=False):
    """Cria a atividade em cada dia pedido (o "repetir em" do formulário)."""
    campos = dict(limpos)
    dias = campos.pop('dias')
    criadas = [AtividadeRotina.objects.create(rotina=rotina, dia_semana=dia,
                                              criada_pela_pessoa=criada_pela_pessoa, **campos)
               for dia in dias]
    marcar_atualizacao(rotina, por)
    return criadas


@transaction.atomic
def criar_atividades_modelo(modelo, limpos):
    campos = dict(limpos)
    dias = campos.pop('dias')
    criadas = [AtividadeModelo.objects.create(modelo=modelo, dia_semana=dia, **campos) for dia in dias]
    modelo.save(update_fields=['atualizado_em'])
    return criadas


@transaction.atomic
def aplicar_modelo(rotina, modelo, por=None):
    """Troca todas as atividades da rotina pelas do modelo. Devolve quantas entraram.

    É substituição, não soma: aplicar duas vezes não duplica nada. O que a
    pessoa tinha mudado (ou criado) some — a tela avisa antes.
    """
    rotina.atividades.all().delete()
    novas = AtividadeRotina.objects.bulk_create(
        [AtividadeRotina(rotina=rotina, **campos) for campos in _copias(modelo.atividades.all())])
    rotina.modelo_origem = modelo
    rotina.atualizado_por = por
    rotina.save(update_fields=['modelo_origem', 'atualizado_por', 'atualizado_em'])
    return len(novas)


@transaction.atomic
def copiar_rotina(destino, origem, por=None):
    """Troca as atividades de `destino` por uma cópia das de `origem`.

    O que a outra pessoa tinha criado entra como atividade da gestão: quem
    recebe a cópia não "criou" nada.
    """
    if destino.pk == origem.pk:
        raise ErroValidacao('Escolha outra pessoa para copiar a rotina.')
    destino.atividades.all().delete()
    novas = AtividadeRotina.objects.bulk_create(
        [AtividadeRotina(rotina=destino, **campos)
         for campos in _copias(origem.atividades.all(), criada_pela_pessoa=False)])
    destino.modelo_origem = origem.modelo_origem
    destino.atualizado_por = por
    destino.save(update_fields=['modelo_origem', 'atualizado_por', 'atualizado_em'])
    return len(novas)


@transaction.atomic
def limpar_rotina(rotina, por=None):
    total = rotina.atividades.count()
    rotina.atividades.all().delete()
    marcar_atualizacao(rotina, por)
    return total


@transaction.atomic
def duplicar_modelo(modelo, por=None):
    copia = ModeloRotina.objects.create(
        nome=f'{modelo.nome} (cópia)'[:120], descricao=modelo.descricao, ativo=True, criado_por=por)
    AtividadeModelo.objects.bulk_create(
        [AtividadeModelo(modelo=copia, **campos) for campos in _copias(modelo.atividades.all())])
    return copia


# ---------------------------------------------------------------------------
# Avisos de início
# ---------------------------------------------------------------------------
def atividades_de_hoje(user, momento=None):
    """O que o notificador precisa: as atividades de hoje de `user` e o relógio do servidor.

    Os instantes vão em milissegundos desde a época (`*_ts`), já calculados no
    fuso do portal: o navegador só compara números e não importa em que fuso
    o aparelho está.
    """
    from .models import RotinaGerencial

    momento = momento or agora()
    hoje = momento.date()
    resposta = {
        'ok': True,
        'agora': momento.isoformat(),
        'agora_ts': int(momento.timestamp() * 1000),
        'data': hoje.isoformat(),
        'dia_semana': hoje.weekday(),
        'ativa': False,
        'atividades': [],
    }
    rotina = RotinaGerencial.objects.filter(user=user).first()
    if rotina is None or not rotina.ativa:
        return resposta
    resposta['ativa'] = True
    if hoje.weekday() > 5:
        return resposta

    avisadas = set(AvisoRotina.objects.filter(user=user, data=hoje)
                   .values_list('atividade_id', flat=True))
    for atividade in rotina.atividades.filter(dia_semana=hoje.weekday()).order_by('inicio', 'fim', 'id'):
        cor = atividade.cor
        resposta['atividades'].append({
            'id': atividade.id,
            'titulo': atividade.titulo,
            'categoria': atividade.categoria,
            'categoria_nome': cor['nome'],
            'cor': cor['cor'],
            'bloqueada': atividade.bloqueada,
            'inicio': f'{atividade.inicio:%H:%M}',
            'fim': f'{atividade.fim:%H:%M}',
            'inicio_ts': int(_instante(hoje, atividade.inicio).timestamp() * 1000),
            'fim_ts': int(_instante(hoje, atividade.fim).timestamp() * 1000),
            'url': url_da_atividade(atividade),
            'avisada': atividade.id in avisadas,
        })
    return resposta


def registrar_aviso(user, atividade, momento=None):
    """Registra o aviso de início de hoje e põe a notificação no sino — uma vez só.

    Devolve `(aviso, novo)`. `novo` é False quando outra aba, outro aparelho
    ou outro worker já tinha registrado. Levanta ErroValidacao fora da hora.
    """
    momento = momento or agora()
    hoje = momento.date()
    if atividade.dia_semana != hoje.weekday():
        raise ErroValidacao('Esta atividade não é de hoje.')
    inicio = _instante(hoje, atividade.inicio)
    fim = _instante(hoje, atividade.fim)
    if momento < inicio - ADIANTAMENTO_ACEITO:
        raise ErroValidacao('Ainda não chegou a hora desta atividade.')
    if momento > max(fim, inicio + JANELA_AVISO):
        raise ErroValidacao('Esta atividade já terminou.')

    existente = AvisoRotina.objects.filter(atividade=atividade, data=hoje).first()
    if existente is not None:
        return existente, False
    try:
        with transaction.atomic():
            aviso = AvisoRotina.objects.create(
                user=user, atividade=atividade, data=hoje,
                titulo=atividade.titulo, inicio=atividade.inicio)
            notificar_no_sino(user, atividade)
    except IntegrityError:
        # Corrida: outra aba (ou outro worker) gravou entre a consulta e a inserção.
        existente = AvisoRotina.objects.filter(atividade=atividade, data=hoje).first()
        if existente is None:
            raise
        return existente, False
    return aviso, True


def notificar_no_sino(user, atividade):
    """Notificação no sino do portal — e só nele.

    Não passa por `PushNotification.send_notification()` nem pelo
    `NotificationService`: os dois também mandam web push para os aparelhos
    da pessoa. Aqui se gravam só os dois registros que o sino lê
    (PushNotification + UserNotification); nenhum envio sai do servidor.
    Quem desligou as notificações do portal (preferência "in-app") fica sem.
    """
    from notifications.models import (
        NotificationCategory, NotificationPreference, PushNotification, UserNotification,
    )

    if NotificationPreference.objects.filter(user=user, in_app_enabled=False).exists():
        return None
    categoria = NotificationCategory.objects.filter(name=CATEGORIA_SINO).order_by('id').first()
    if categoria is None:
        categoria = NotificationCategory.objects.create(
            name=CATEGORIA_SINO, icon='fas fa-calendar-check', color='orange')
    notificacao = PushNotification.objects.create(
        title=f'Agora: {atividade.titulo}'[:200],
        message=f'{atividade.horario} · {atividade.cor["nome"]}. Toque para abrir sua rotina gerencial.',
        category=categoria,
        notification_type='TASK',
        priority='NORMAL',
        icon='fas fa-calendar-check',
        action_url=url_da_atividade(atividade),
        action_text='Abrir rotina',
        created_by=user,
        is_sent=True,
        sent_at=timezone.now(),
        extra_data={'origem': 'rotina_gerencial', 'atividade_id': atividade.id},
    )
    return UserNotification.objects.create(notification=notificacao, user=user)
