"""As regras do Acess. As telas só chamam daqui — e tudo passa pelo histórico."""
import re
import unicodedata
from collections import defaultdict
from datetime import timedelta
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone

from .models import (AvisoInativacao, MOTIVOS_CHAMADO, SISTEMAS, SISTEMAS_CHAMADO, STATUS_SISTEMA, Chamado, Colaborador,
                     EventoChamado, LogAcess, ModeloMensagem, Solicitacao)


class RegraAcess(Exception):
    """Mensagem para a tela."""


class Conflito(RegraAcess):
    """Outra pessoa mudou a ficha depois que esta tela foi aberta."""


# ── apoio ───────────────────────────────────────────────────────────────────
def so_digitos(texto):
    return re.sub(r'\D', '', str(texto or ''))


def norm(texto):
    t = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in t if not unicodedata.combining(c)).upper().split())


def codigo_chamado(texto):
    """Nº do chamado sem o "#" da frente (o Acess anterior gravava "#564695"; a tela já põe o #)."""
    return str(texto or '').strip().lstrip('#').strip()


def cpf_valido(cpf):
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(d[n]):
            return False
    return True


def e_sede(setor):
    """SEDE ADMINISTRATIVA é a única sede: não tem ADABAS e não serve de loja de vínculo."""
    return bool(setor) and 'SEDE ADMINISTRATIVA' in norm(setor.name)


def uf_da_loja(setor):
    """ES, RJ ou SP pelo ADABAS da loja (ESD…/ESLA… → ES, RJD… → RJ, SP… → SP)."""
    if not setor:
        return ''
    cod = norm(setor.adabas)
    for uf in ('SP', 'RJ', 'ES'):
        if cod.startswith(uf):
            return uf
    return ''


def perfil_regional_requerido(c):
    """Assurant para vínculo/local em SP; Renova Allied para os demais estados."""
    uf = uf_da_loja(c.loja_vinculo) or uf_da_loja(c.local)
    return 'ASSURANT' if uf == 'SP' else 'RENOVA_ALLIED'


def rotulo_perfil(chave):
    return {'ASSURANT': 'Assurant', 'RENOVA_ALLIED': 'Renova Allied'}.get(chave, chave)


def _ip(request):
    if request is None:
        return None
    enc = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return enc or request.META.get('REMOTE_ADDR') or None


def registrar(request, acao, categoria, descricao, colaborador=None, antes=None, depois=None, usuario=None):
    usuario = usuario or (getattr(request, 'user', None) if request is not None else None)
    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None
    with transaction.atomic():
        LogAcess.objects.create(
            usuario=usuario,
            usuario_nome=((usuario.get_full_name() or usuario.email) if usuario else 'sistema')[:200],
            acao=acao[:40], categoria=categoria, colaborador=colaborador, descricao=descricao[:400],
            antes=antes, depois=depois, ip=_ip(request))


def conferir_versao(c, versao):
    if versao in (None, ''):
        return
    try:
        v = int(versao)
    except (TypeError, ValueError):
        raise Conflito('Versão inválida: atualize a página.')
    if v != c.versao:
        raise Conflito('Esta ficha foi alterada por outra pessoa depois que você abriu a tela. '
                       'Atualize, confira o estado atual e refaça só o que ainda for necessário.')


def _salvar(c, campos):
    """Grava, sobe a versão e reposiciona a etapa (o tempo de pendência reinicia ao mudar de etapa)."""
    nova = c.etapa_calculada
    if nova != c.etapa:
        c.etapa = nova
        c.pendente_desde = timezone.now()
        campos = list(campos) + ['etapa', 'pendente_desde']
    c.versao += 1
    c.save(update_fields=list(dict.fromkeys(list(campos) + ['versao', 'atualizado_em'])))


def _travar(c):
    return Colaborador.objects.select_for_update().get(pk=c.pk)


# ── cadastro ────────────────────────────────────────────────────────────────
def _limpar_dados(dados):
    nome = ' '.join(str(dados.get('nome') or '').split()).upper()
    if not nome:
        raise RegraAcess('Informe o nome.')
    cpf = so_digitos(dados.get('cpf'))
    if cpf and not cpf_valido(cpf):
        raise RegraAcess('CPF inválido: confira os 11 números (zeros iniciais contam).')
    return {
        'nome': nome[:200], 'cpf': cpf,
        'nascimento': dados.get('nascimento') or None,
        'email': str(dados.get('email') or '').strip()[:254],
        'telefone': str(dados.get('telefone') or '').strip()[:20],
        'cargo': ' '.join(str(dados.get('cargo') or '').split()).upper()[:100],
    }


@transaction.atomic
def criar_colaborador(dados, local, loja_vinculo=None, usuario=None, request=None, origem='MANUAL', user=None):
    campos = _limpar_dados(dados)
    if not campos['cpf']:
        raise RegraAcess('O CPF é obrigatório.')
    if local is None:
        raise RegraAcess('Escolha o local de trabalho.')
    if loja_vinculo is not None and e_sede(loja_vinculo):
        raise RegraAcess('A sede não pode ser loja de vínculo.')
    if user is not None and Colaborador.objects.filter(user=user).exists():
        raise RegraAcess('Esta pessoa do portal já tem ficha no Acess.')
    c = Colaborador.objects.create(
        user=user, local=local, loja_vinculo=loja_vinculo, origem=origem, criado_por=usuario,
        login=str(dados.get('login') or '').strip()[:40],
        sistemas={s: 'PENDENTE' for s in SISTEMAS}, **campos)
    c.etapa = c.etapa_calculada or 1
    c.save(update_fields=['etapa'])
    registrar(request, 'CADASTRO', 'CADASTRO', f'Cadastrou {c.nome} ({c.get_origem_display().lower()})',
              colaborador=c, usuario=usuario, depois={'local': local.name, 'cpf': c.cpf_formatado})
    return c


def dados_do_usuario(u):
    return {'nome': u.get_full_name() or u.username, 'cpf': so_digitos(u.cpf), 'nascimento': u.birth_date,
            'email': u.email, 'telefone': u.phone, 'cargo': u.job_title, 'login': u.login_code}


def entrar_no_fluxo(user, por=None, request=None):
    """Pré-cadastro aprovado: a pessoa entra na esteira (Etapa 1). Idempotente."""
    existente = Colaborador.objects.filter(user=user).first()
    if existente:
        return existente
    dados = dados_do_usuario(user)
    if not cpf_valido(dados['cpf']):
        dados['cpf'] = ''
    with transaction.atomic():
        campos = _limpar_dados(dados)
        c = Colaborador.objects.create(
            user=user, local=user.sector, origem='PRE_CADASTRO', criado_por=por, login='',
            sistemas={s: 'PENDENTE' for s in SISTEMAS}, etapa=1, **campos)
        registrar(request, 'CADASTRO', 'CADASTRO', f'{c.nome} entrou na esteira pelo pré-cadastro aprovado',
                  colaborador=c, usuario=por)
    return c


@transaction.atomic
def editar_dados(c, dados, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    campos = _limpar_dados(dados)
    antes = {k: str(getattr(c, k) or '') for k in campos}
    for k, v in campos.items():
        setattr(c, k, v)
    depois = {k: str(getattr(c, k) or '') for k in campos}
    mudou = {k: depois[k] for k in campos if antes[k] != depois[k]}
    if not mudou:
        return c
    _salvar(c, list(campos))
    registrar(request, 'DADOS', 'DADOS', f'Editou os dados de {c.nome}', colaborador=c, usuario=usuario,
              antes={k: antes[k] for k in mudou}, depois=mudou)
    return c


@transaction.atomic
def transferir(c, local, loja_vinculo, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    if local is None:
        raise RegraAcess('Escolha o local de trabalho.')
    if loja_vinculo is not None and e_sede(loja_vinculo):
        raise RegraAcess('A sede não pode ser loja de vínculo (ela não tem ADABAS).')
    antes = {'local': c.local.name if c.local else '', 'vinculo': c.loja_vinculo.name if c.loja_vinculo else ''}
    c.local, c.loja_vinculo = local, loja_vinculo
    depois = {'local': local.name, 'vinculo': loja_vinculo.name if loja_vinculo else ''}
    if antes == depois:
        return c
    _salvar(c, ['local', 'loja_vinculo'])
    registrar(request, 'TRANSFERENCIA', 'TRANSFERENCIA', f'Transferiu {c.nome}', colaborador=c, usuario=usuario,
              antes=antes, depois=depois)
    return c


@transaction.atomic
def alterar_sistemas(c, novos, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    sistemas = dict(c.sistemas or {})
    mudou_a, mudou_d = {}, {}
    for s, v in novos.items():
        if s not in SISTEMAS:
            raise RegraAcess(f'Sistema desconhecido: {s}.')
        v = (v or '').upper()
        if v not in STATUS_SISTEMA:
            raise RegraAcess(f'Status inválido para {s}.')
        if sistemas.get(s, '') != v:
            mudou_a[s], mudou_d[s] = sistemas.get(s, ''), v
            sistemas[s] = v
    if not mudou_d:
        return c
    c.sistemas = sistemas
    _salvar(c, ['sistemas'])
    registrar(request, 'SISTEMAS', 'CONCLUSAO', f'Alterou sistemas de {c.nome}', colaborador=c, usuario=usuario,
              antes=mudou_a, depois=mudou_d)
    return c


# ── a esteira ───────────────────────────────────────────────────────────────
@transaction.atomic
def concluir_gefac(c, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    if c.etapa_calculada != 1:
        raise RegraAcess('Esta pessoa não está na Etapa 1 (Criação do GEFAC).')
    c.sistemas = dict(c.sistemas or {}, GEFAC='OK')
    _salvar(c, ['sistemas'])
    registrar(request, 'GEFAC_CONCLUIDO', 'CONCLUSAO', f'Concluiu o GEFAC de {c.nome}', colaborador=c, usuario=usuario)
    return c


@transaction.atomic
def salvar_login(c, login, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    login = re.sub(r'\.0$', '', str(login or '').strip())
    if not login or 'PENDENTE' in login.upper():
        raise RegraAcess('Informe a matrícula/login (não pode ficar vazio nem conter PENDENTE).')
    if len(login) > 40:
        raise RegraAcess('Login longo demais.')
    if Colaborador.objects.filter(login__iexact=login, status=Colaborador.ATIVO).exclude(pk=c.pk).exists():
        raise RegraAcess(f'O login {login} já está na ficha de outra pessoa.')
    antes = c.login
    c.login = login
    _salvar(c, ['login'])
    if c.user_id and c.user.login_code != login[:20]:
        # A matrícula do Acess é o mesmo "Login/Código" do cadastro do portal.
        c.user.login_code = login[:20]
        c.user.save(update_fields=['login_code'])
    registrar(request, 'LOGIN', 'CONCLUSAO', f'Preencheu o login de {c.nome}', colaborador=c, usuario=usuario,
              antes={'login': antes}, depois={'login': login})
    return c


@transaction.atomic
def concluir_academia(c, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    if c.etapa_calculada != 3:
        raise RegraAcess('Esta pessoa não está na Etapa 3 (Academia V).')
    c.sistemas = dict(c.sistemas or {}, TREINAMENTO='OK')
    c.entrega_login = 'PENDENTE'
    c.check_pool, c.check_perfil, c.check_login, c.check_sap = False, '', False, False
    _salvar(c, ['sistemas', 'entrega_login', 'check_pool', 'check_perfil', 'check_login', 'check_sap'])
    registrar(request, 'ACADEMIA_CONCLUIDA', 'CONCLUSAO', f'Concluiu a Academia V de {c.nome}', colaborador=c,
              usuario=usuario)
    return c


ITENS_CHECKLIST = {'pool': 'Pool de cobrança', 'perfil': 'Perfil regional', 'login': 'Teste do login', 'sap': 'Senha SAP'}


@transaction.atomic
def salvar_checklist(c, item, marcado, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    if c.etapa_calculada != 4:
        raise RegraAcess('O checklist é da Etapa 4.')
    if item not in ITENS_CHECKLIST:
        raise RegraAcess('Item do checklist desconhecido.')
    if item == 'perfil':
        c.check_perfil = perfil_regional_requerido(c) if marcado else ''
        campo = 'check_perfil'
    else:
        campo = f'check_{item}'
        setattr(c, campo, bool(marcado))
    _salvar(c, [campo])
    registrar(request, 'CHECKLIST', 'CONCLUSAO',
              f'{"Marcou" if marcado else "Desmarcou"} "{ITENS_CHECKLIST[item]}" de {c.nome}', colaborador=c,
              usuario=usuario)
    return c


def checklist_completo(c):
    return c.check_pool and c.check_login and c.check_sap and c.check_perfil == perfil_regional_requerido(c)


def chamado_de_liberacao(c):
    return c.chamados.filter(motivo='LIBERACAO_INICIAL', status=Chamado.ANDAMENTO).order_by('-aberto_em').first()


@transaction.atomic
def finalizar_liberacao(c, versao, usuario=None, request=None, chamado_versao=None):
    """Confirmação de envio: conclui o que estava pendente, fecha o chamado de liberação e marca ENVIADO."""
    c = _travar(c)
    conferir_versao(c, versao)
    if c.etapa_calculada != 4:
        raise RegraAcess('Esta pessoa não está na Etapa 4.')
    if not checklist_completo(c):
        raise RegraAcess('Complete o checklist (os quatro itens, com o perfil regional atual) antes de finalizar.')
    chamado = chamado_de_liberacao(c)
    if chamado is not None:
        chamado = Chamado.objects.select_for_update().get(pk=chamado.pk)
        if chamado_versao not in (None, '') and str(chamado.versao) != str(chamado_versao):
            raise Conflito('O chamado de liberação mudou: recarregue a ficha e o chamado antes de finalizar.')
        _concluir_chamado(chamado, usuario, 'Concluído junto da liberação inicial do login')
    concluidos = [s for s in SISTEMAS if c.status_de(s) in ('', 'PENDENTE')]
    c.sistemas = dict(c.sistemas or {}, **{s: 'OK' for s in concluidos})
    c.entrega_login = 'ENVIADO'
    c.liberado_em = timezone.now()
    _salvar(c, ['sistemas', 'entrega_login', 'liberado_em'])
    registrar(request, 'LOGIN_ENVIO_CONFIRMADO', 'CONCLUSAO',
              f'Confirmou o envio do login de {c.nome}' + (f' e concluiu o chamado #{chamado.codigo}' if chamado else ''),
              colaborador=c, usuario=usuario, depois={'concluidos': concluidos, 'login': c.login})
    return c


@transaction.atomic
def voltar_etapa(c, versao, usuario=None, request=None, pode_chamados=False):
    c = _travar(c)
    conferir_versao(c, versao)
    etapa = c.etapa_calculada
    if etapa not in (2, 3, 4):
        raise RegraAcess('Só dá para voltar das etapas 2, 3 e 4.')
    chamado = chamado_de_liberacao(c)
    if chamado is not None and not pode_chamados:
        raise RegraAcess('Há chamado de liberação inicial ativo: voltar a etapa exige poder tratar chamados.')
    if etapa == 2:
        c.sistemas = dict(c.sistemas or {}, GEFAC='PENDENTE')
    elif etapa == 3:
        c.login = ''
    else:
        c.sistemas = dict(c.sistemas or {}, TREINAMENTO='PENDENTE')
        c.entrega_login = ''
    _salvar(c, ['sistemas', 'login', 'entrega_login'])
    if chamado is not None:
        codigo = chamado.codigo
        chamado.delete()
        registrar(request, 'CHAMADO_REMOVIDO', 'CHAMADO', f'Removeu o chamado #{codigo} ao voltar a etapa de {c.nome}',
                  colaborador=c, usuario=usuario)
    registrar(request, 'ETAPA_VOLTOU', 'CONCLUSAO', f'Voltou {c.nome} da etapa {etapa} para a {etapa - 1}',
              colaborador=c, usuario=usuario)
    return c


def concluir_pendencias_em_lote(ids, usuario=None, request=None):
    """Dashboard: vazio/PENDENTE → OK. BLACKLIST não muda; etapa 4 com entrega pendente fica de fora."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not 1 <= len(ids) <= 500:
        raise RegraAcess('Selecione de 1 a 500 colaboradores.')
    feitos, pulados = 0, []
    for c in Colaborador.objects.filter(pk__in=ids, status=Colaborador.ATIVO):
        with transaction.atomic():
            c = _travar(c)
            if c.entrega_login == 'PENDENTE':
                pulados.append(c.nome)
                continue
            if not c.login_valido:
                pulados.append(c.nome)
                continue
            pend = c.pendencias
            if not pend:
                continue
            c.sistemas = dict(c.sistemas or {}, **{s: 'OK' for s in pend})
            _salvar(c, ['sistemas'])
            registrar(request, 'PENDENCIAS_CONCLUIDAS', 'CONCLUSAO', f'Concluiu {len(pend)} pendência(s) de {c.nome}',
                      colaborador=c, usuario=usuario, depois={'sistemas': pend})
            feitos += 1
    return feitos, pulados


# ── chamados ────────────────────────────────────────────────────────────────
def _prazo(minutos=None, quando=None):
    agora = timezone.now()
    if quando:
        if timezone.is_naive(quando):
            quando = timezone.make_aware(quando)
        if quando <= agora:
            raise RegraAcess('O prazo precisa ser no futuro.')
        if quando > agora + timedelta(minutes=43200):
            raise RegraAcess('O prazo vai até 30 dias.')
        return quando
    try:
        m = int(minutos or 0)
    except (TypeError, ValueError):
        raise RegraAcess('Prazo inválido.')
    if not 1 <= m <= 43200:
        raise RegraAcess('O prazo vai de 1 minuto a 30 dias (43.200 minutos).')
    return agora + timedelta(minutes=m)


@transaction.atomic
def criar_chamado(c, codigo, motivo, sistemas, observacao, minutos=None, quando=None, responsavel=None,
                  usuario=None, request=None):
    codigo = codigo_chamado(codigo)
    if not codigo or len(codigo) > 100:
        raise RegraAcess('Informe o número do chamado (até 100 caracteres).')
    if Chamado.objects.filter(codigo__iexact=codigo, status=Chamado.ANDAMENTO).exists():
        raise RegraAcess(f'Já existe chamado ativo com o número {codigo}.')
    if motivo not in dict(MOTIVOS_CHAMADO):
        raise RegraAcess('Escolha o motivo.')
    sistemas = [s for s in (sistemas or []) if s]
    fora = [s for s in sistemas if s not in SISTEMAS_CHAMADO]
    if fora:
        raise RegraAcess(f'Sistema fora do catálogo: {", ".join(fora)}.')
    if len(observacao or '') > 3000:
        raise RegraAcess('A observação vai até 3.000 caracteres.')
    prazo = _prazo(minutos, quando)
    ch = Chamado.objects.create(colaborador=c, codigo=codigo, motivo=motivo, sistemas=sistemas,
                                observacao=(observacao or '').strip(), sla_original=prazo, proxima_verificacao=prazo,
                                responsavel=responsavel, criado_por=usuario)
    EventoChamado.objects.create(chamado=ch, usuario=usuario, tipo='CRIADO',
                                 detalhe=f'Aberto com prazo para {timezone.localtime(prazo):%d/%m/%Y %H:%M}')
    registrar(request, 'CHAMADO_ABERTO', 'CHAMADO', f'Abriu o chamado #{codigo} ({ch.get_motivo_display()}) de {c.nome}',
              colaborador=c, usuario=usuario)
    return ch


def _concluir_chamado(ch, usuario, detalhe='Concluído'):
    ch.status = Chamado.CONCLUIDO
    ch.concluido_por, ch.concluido_em = usuario, timezone.now()
    ch.versao += 1
    ch.save(update_fields=['status', 'concluido_por', 'concluido_em', 'versao'])
    EventoChamado.objects.create(chamado=ch, usuario=usuario, tipo='CONCLUIDO', detalhe=detalhe)
    for sol in Solicitacao.objects.filter(chamado=ch, status=Solicitacao.EM_CHAMADO):
        sol.status, sol.tratado_em = Solicitacao.RESOLVIDA, timezone.now()
        sol.tratado_por = sol.tratado_por or usuario
        sol.save(update_fields=['status', 'tratado_em', 'tratado_por'])
        avisar_solicitante(sol, f'Chamado #{ch.codigo} concluído',
                           f'O chamado de acesso que você pediu ({sol.get_motivo_display().lower()}) foi concluído.')


@transaction.atomic
def concluir_chamado(ch, versao, usuario=None, request=None):
    ch = Chamado.objects.select_for_update().get(pk=ch.pk)
    if str(ch.versao) != str(versao):
        raise Conflito('O chamado mudou: atualize antes de concluir.')
    if ch.status != Chamado.ANDAMENTO:
        raise RegraAcess('O chamado já está concluído.')
    _concluir_chamado(ch, usuario)
    registrar(request, 'CHAMADO_CONCLUIDO', 'CHAMADO', f'Concluiu o chamado #{ch.codigo} de {ch.colaborador.nome}',
              colaborador=ch.colaborador, usuario=usuario)
    return ch


@transaction.atomic
def reagendar_chamado(ch, versao, minutos=None, quando=None, usuario=None, request=None):
    ch = Chamado.objects.select_for_update().get(pk=ch.pk)
    if str(ch.versao) != str(versao):
        raise Conflito('O chamado mudou: atualize antes de reagendar.')
    if ch.status != Chamado.ANDAMENTO:
        raise RegraAcess('Chamado concluído não se reagenda.')
    prazo = _prazo(minutos, quando)
    ch.proxima_verificacao = prazo
    ch.versao += 1
    ch.save(update_fields=['proxima_verificacao', 'versao'])
    EventoChamado.objects.create(chamado=ch, usuario=usuario, tipo='REAGENDADO',
                                 detalhe=f'Ainda em andamento · próxima verificação {timezone.localtime(prazo):%d/%m/%Y %H:%M}')
    registrar(request, 'CHAMADO_REAGENDADO', 'CHAMADO', f'Reagendou o chamado #{ch.codigo}', colaborador=ch.colaborador,
              usuario=usuario, depois={'proxima_verificacao': timezone.localtime(prazo).isoformat()})
    return ch


@transaction.atomic
def atribuir_chamado(ch, versao, responsavel, usuario=None, request=None):
    from .permissoes import trata_chamados
    ch = Chamado.objects.select_for_update().get(pk=ch.pk)
    if str(ch.versao) != str(versao):
        raise Conflito('O chamado mudou: atualize antes de atribuir.')
    if ch.status != Chamado.ANDAMENTO:
        raise RegraAcess('Chamado concluído não se reatribui.')
    if responsavel is not None and not (responsavel.is_active and trata_chamados(responsavel)):
        raise RegraAcess('O responsável precisa ser alguém ativo que trata chamados.')
    antes = ch.responsavel.get_full_name() if ch.responsavel else 'sem responsável'
    ch.responsavel = responsavel
    ch.versao += 1
    ch.save(update_fields=['responsavel', 'versao'])
    depois = responsavel.get_full_name() if responsavel else 'sem responsável'
    EventoChamado.objects.create(chamado=ch, usuario=usuario, tipo='ATRIBUIDO', detalhe=f'{antes} → {depois}')
    registrar(request, 'CHAMADO_ATRIBUIDO', 'CHAMADO', f'Responsável do chamado #{ch.codigo}: {depois}',
              colaborador=ch.colaborador, usuario=usuario, antes={'responsavel': antes}, depois={'responsavel': depois})
    return ch


# ── desligamentos e arquivamento ────────────────────────────────────────────
@transaction.atomic
def agendar_desligamento(c, saida, execucao, motivo, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    if c.status != Colaborador.ATIVO:
        raise RegraAcess('A ficha já está arquivada.')
    if not saida or not execucao:
        raise RegraAcess('Informe a saída da empresa e a execução do desligamento.')
    if execucao < timezone.localdate():
        raise RegraAcess('A execução do desligamento precisa ser hoje ou depois.')
    if execucao < saida:
        raise RegraAcess('A execução não pode ser antes da saída da empresa.')
    antes = {'saida': str(c.saida_empresa or ''), 'execucao': str(c.desligamento_em or '')}
    c.saida_empresa, c.desligamento_em, c.desligamento_motivo = saida, execucao, (motivo or '').strip()[:1000]
    _salvar(c, ['saida_empresa', 'desligamento_em', 'desligamento_motivo'])
    registrar(request, 'DESLIGAMENTO_AGENDADO', 'DESLIGAMENTO',
              f'Agendou o desligamento de {c.nome} para {execucao:%d/%m/%Y}', colaborador=c, usuario=usuario,
              antes=antes, depois={'saida': str(saida), 'execucao': str(execucao), 'motivo': c.desligamento_motivo})
    return c


@transaction.atomic
def cancelar_desligamento(c, versao, usuario=None, request=None):
    c = _travar(c)
    conferir_versao(c, versao)
    if not c.desligamento_em or c.status != Colaborador.ATIVO:
        raise RegraAcess('Não há desligamento agendado.')
    c.saida_empresa = c.desligamento_em = None
    c.desligamento_motivo = ''
    _salvar(c, ['saida_empresa', 'desligamento_em', 'desligamento_motivo'])
    registrar(request, 'DESLIGAMENTO_CANCELADO', 'DESLIGAMENTO', f'Cancelou o desligamento de {c.nome}',
              colaborador=c, usuario=usuario)
    return c


@transaction.atomic
def arquivar(c, versao, usuario=None, request=None, motivo='', exige_agendamento=False):
    c = _travar(c)
    conferir_versao(c, versao)
    if c.status != Colaborador.ATIVO:
        raise RegraAcess('A ficha já está arquivada.')
    if exige_agendamento and not c.desligamento_em:
        raise RegraAcess('Confirmar desligamento exige um agendamento.')
    if not exige_agendamento and c.chamados.filter(status=Chamado.ANDAMENTO).exists():
        raise RegraAcess('Há chamado ativo para esta pessoa: conclua antes de excluir.')
    c.status = Colaborador.DESLIGADO
    c.desligado_em = timezone.now()
    _salvar(c, ['status', 'desligado_em'])
    acao = 'DESLIGAMENTO_CONFIRMADO' if exige_agendamento else 'COLABORADOR_ARQUIVADO'
    registrar(request, acao, 'DESLIGAMENTO', f'{"Confirmou o desligamento de" if exige_agendamento else "Arquivou"} {c.nome}'
              + (f' — {motivo}' if motivo else '') + ' (não revoga os acessos externos)', colaborador=c, usuario=usuario)
    return c


# ── mensagens ───────────────────────────────────────────────────────────────
VARIAVEIS = ('nome', 'login', 'cpf3', 'loja', 'senha')
MODELOS_PADRAO = [
    ('contato', 'Contato geral sobre acessos',
     'Olá, {{nome}}! Aqui é da equipe de acessos da Rede Confiança. Podemos falar sobre os seus acessos?'),
    ('boas_vindas', 'Matrícula criada / boas-vindas à Academia V',
     'Olá, {{nome}}! Sua matrícula {{login}} foi criada. Conclua a Academia V em até 72h para seguirmos com a '
     'liberação dos seus acessos.'),
    ('cobranca', 'Cobrança da Academia V',
     'Olá, {{nome}}! Ainda não identificamos a conclusão da Academia V da matrícula {{login}}. Conclua em até 48h, '
     'por favor.'),
    ('chamado_concluido', 'Chamado concluído',
     'Olá, {{nome}}! O seu chamado de acesso foi concluído. Teste o seu login {{login}} e nos avise se precisar.'),
    ('liberacao', 'Liberação inicial do login',
     'Olá, {{nome}}! Seus acessos foram liberados. Login: {{login}} · senha inicial: {{senha}}. '
     'No primeiro acesso, troque a senha. Loja: {{loja}}.'),
]


def garantir_modelos():
    if ModeloMensagem.objects.exists():
        return
    for chave, titulo, texto in MODELOS_PADRAO:
        ModeloMensagem.objects.get_or_create(chave=chave, defaults={'titulo': titulo, 'texto': texto})


def validar_texto(texto):
    texto = (texto or '').strip()
    if not 1 <= len(texto) <= 4000:
        raise RegraAcess('O texto tem de 1 a 4.000 caracteres.')
    usadas = set(re.findall(r'\{\{\s*([^}]+?)\s*\}\}', texto))
    estranhas = usadas - set(VARIAVEIS)
    if estranhas:
        raise RegraAcess('Variável não reconhecida: ' + ', '.join('{{' + v + '}}' for v in sorted(estranhas)))
    return texto


def montar_mensagem(texto, c, senha=''):
    usadas = set(re.findall(r'\{\{\s*([^}]+?)\s*\}\}', texto))
    if 'login' in usadas and not c.login_valido:
        raise RegraAcess('O modelo usa o login, e esta ficha ainda não tem matrícula válida.')
    if 'cpf3' in usadas and not cpf_valido(c.cpf):
        raise RegraAcess('O modelo usa o CPF, e o CPF da ficha não é válido.')
    if 'senha' in usadas and not senha:
        raise RegraAcess('Informe a senha que vai na mensagem.')
    valores = {'nome': c.nome.split(' ')[0].title() if c.nome else '', 'login': c.login, 'cpf3': (c.cpf or '')[:3],
               'loja': c.local.name if c.local else '', 'senha': senha}
    return re.sub(r'\{\{\s*([^}]+?)\s*\}\}', lambda m: str(valores.get(m.group(1), '')), texto)


def telefone_whatsapp(telefone):
    d = so_digitos(telefone)
    if len(d) in (10, 11):
        d = '55' + d
    return d if len(d) in (12, 13) and d.startswith('55') else ''


def link_whatsapp(telefone, texto):
    numero = telefone_whatsapp(telefone)
    if not numero:
        raise RegraAcess('Telefone ausente ou inválido: corrija na ficha (DDD + número).')
    return f'https://wa.me/{numero}?text={quote(texto)}'


# ── consultas ───────────────────────────────────────────────────────────────
def duplicados(qs):
    """IDs que compartilham CPF, login, e-mail ou nome+nascimento (para revisão humana)."""
    grupos = defaultdict(set)
    for c in qs.values('id', 'cpf', 'login', 'email', 'nome', 'nascimento'):
        if len(c['cpf'] or '') == 11:
            grupos[('cpf', c['cpf'])].add(c['id'])
        login = (c['login'] or '').strip().upper()
        if login and 'PENDENTE' not in login:
            grupos[('login', login)].add(c['id'])
        if (c['email'] or '').strip():
            grupos[('email', c['email'].strip().lower())].add(c['id'])
        if c['nascimento']:
            grupos[('nome', norm(c['nome']), c['nascimento'])].add(c['id'])
    ids = set()
    for g in grupos.values():
        if len(g) > 1:
            ids |= g
    return ids


# ── Solicitações pela janela de Suporte do portal ──────────────────────────
ABERTAS_POR_PESSOA = 5


def ficha_do_usuario(user):
    """A ficha do Acess de quem pediu: ligada ao usuário ou, se não houver, pelo CPF."""
    c = Colaborador.objects.filter(user=user).order_by('status', '-id').first()
    if c is None and so_digitos(getattr(user, 'cpf', '')):
        c = Colaborador.objects.filter(cpf=so_digitos(user.cpf)).order_by('status', '-id').first()
    return c


def _notificar(usuarios, titulo, mensagem, url):
    try:
        from core.models import Notification
        Notification.objects.bulk_create([Notification(user=u, title=titulo[:200], message=mensagem,
                                                       notification_type='TICKET', related_url=url) for u in usuarios])
    except Exception:  # noqa: BLE001 — aviso nunca derruba o pedido
        pass


def avisar_solicitante(sol, titulo, mensagem):
    _notificar([sol.solicitante], titulo, mensagem, '/')


def quem_trata_chamados():
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from .permissoes import trata_chamados
    gente = get_user_model().objects.filter(is_active=True).filter(
        Q(acesso_acess__trata_chamados=True, acesso_acess__ativo=True)).distinct()
    return [u for u in gente if trata_chamados(u)]


@transaction.atomic
def criar_solicitacao(user, motivo, sistemas, descricao, contato='', request=None):
    if motivo not in dict(MOTIVOS_CHAMADO):
        raise RegraAcess('Escolha o motivo.')
    sistemas = [s for s in dict.fromkeys(sistemas or []) if s]
    fora = [s for s in sistemas if s not in SISTEMAS_CHAMADO]
    if fora:
        raise RegraAcess(f'Sistema fora do catálogo: {", ".join(fora)}.')
    descricao = str(descricao or '').strip()
    if len(descricao) < 5:
        raise RegraAcess('Conte em poucas palavras o que está acontecendo.')
    if len(descricao) > 2000:
        raise RegraAcess('A descrição vai até 2.000 caracteres.')
    if Solicitacao.objects.filter(solicitante=user, status=Solicitacao.ABERTA).count() >= ABERTAS_POR_PESSOA:
        raise RegraAcess('Você já tem pedidos aguardando o time de Acessos. Aguarde o retorno deles.')
    c = ficha_do_usuario(user)
    sol = Solicitacao.objects.create(solicitante=user, colaborador=c, motivo=motivo, sistemas=sistemas,
                                     descricao=descricao, contato=str(contato or '').strip()[:40])
    registrar(request, 'SOLICITACAO_ABERTA', 'CHAMADO',
              f'{user.get_full_name() or user.email} pediu chamado pelo Suporte ({sol.numero}: '
              f'{sol.get_motivo_display()})', colaborador=c, usuario=user)
    _notificar(quem_trata_chamados(), f'Pedido de acesso {sol.numero}',
               f'{user.get_full_name() or user.email}: {sol.get_motivo_display()}'
               + (f' · {", ".join(sistemas)}' if sistemas else ''), '/acess/solicitacoes/')
    return sol


@transaction.atomic
def vincular_solicitacao(sol, chamado, usuario=None):
    sol = Solicitacao.objects.select_for_update().get(pk=sol.pk)
    if sol.status != Solicitacao.ABERTA:
        raise RegraAcess(f'O pedido {sol.numero} já foi tratado.')
    sol.status, sol.chamado = Solicitacao.EM_CHAMADO, chamado
    sol.colaborador = sol.colaborador or chamado.colaborador
    sol.tratado_por, sol.tratado_em = usuario, timezone.now()
    sol.save(update_fields=['status', 'chamado', 'colaborador', 'tratado_por', 'tratado_em'])
    avisar_solicitante(sol, f'Chamado #{chamado.codigo} aberto',
                       f'O time de Acessos abriu o chamado #{chamado.codigo} para o seu pedido '
                       f'({sol.get_motivo_display().lower()}).')
    return sol


@transaction.atomic
def responder_solicitacao(sol, status, resposta, usuario=None, request=None):
    sol = Solicitacao.objects.select_for_update().get(pk=sol.pk)
    if sol.status != Solicitacao.ABERTA:
        raise RegraAcess(f'O pedido {sol.numero} já foi tratado.')
    if status not in (Solicitacao.RESOLVIDA, Solicitacao.RECUSADA):
        raise RegraAcess('Ação inválida.')
    resposta = str(resposta or '').strip()
    if not resposta:
        raise RegraAcess('Escreva a resposta para quem pediu.')
    sol.status, sol.resposta = status, resposta[:1000]
    sol.tratado_por, sol.tratado_em = usuario, timezone.now()
    sol.save(update_fields=['status', 'resposta', 'tratado_por', 'tratado_em'])
    registrar(request, 'SOLICITACAO_' + status, 'CHAMADO', f'{sol.numero} {sol.get_status_display().lower()}: {resposta[:200]}',
              colaborador=sol.colaborador, usuario=usuario)
    avisar_solicitante(sol, f'Pedido de acesso {sol.get_status_display().lower()}', resposta[:500])
    return sol


# ── Usuário inativado no portal → decidir o desligamento nos acessos ───────
def quem_cuida_desligamentos():
    """Quem tem "Gerenciar desligamentos" liberado no Acess; sem ninguém, os SUPERADMIN."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from .permissoes import pode
    User = get_user_model()
    gente = [u for u in User.objects.filter(is_active=True, acesso_acess__ativo=True) if pode(u, 'desligamentos.gerenciar')]
    return gente or list(User.objects.filter(is_active=True).filter(Q(hierarchy='SUPERADMIN') | Q(is_superuser=True)))


def ficha_ativa_do_usuario(user):
    c = Colaborador.objects.filter(user=user, status=Colaborador.ATIVO).order_by('-id').first()
    if c is None and so_digitos(getattr(user, 'cpf', '')):
        c = Colaborador.objects.filter(cpf=so_digitos(user.cpf), status=Colaborador.ATIVO).order_by('-id').first()
    return c


def avisar_inativacao(user):
    """O usuário ficou inativo no portal: o Acess pergunta se o desligamento é imediato ou agendado (PPL)."""
    c = ficha_ativa_do_usuario(user)
    if c is None or c.desligamento_em:
        return None  # sem ficha ativa, ou o desligamento já está agendado
    if AvisoInativacao.objects.filter(colaborador=c, status=AvisoInativacao.PENDENTE).exists():
        return None
    aviso = AvisoInativacao.objects.create(colaborador=c, user=user)
    registrar(None, 'USUARIO_INATIVADO', 'DESLIGAMENTO',
              f'{c.nome} foi inativado no portal: decidir se o desligamento é imediato ou agendado', colaborador=c)
    _notificar(quem_cuida_desligamentos(), f'{c.nome} inativado no portal',
               'O desligamento nos acessos vai ser imediato ou agendado (para não impactar o PPL)? Decida no Acess.',
               '/acess/desligamentos/#inativados')
    return aviso


def descartar_avisos_do_reativado(user):
    return AvisoInativacao.objects.filter(colaborador__user=user, status=AvisoInativacao.PENDENTE).update(
        status=AvisoInativacao.DESCARTADO, observacao='Usuário reativado no portal', decidido_em=timezone.now())


def avisar_desligamentos_de_hoje(colaboradores):
    if not colaboradores:
        return
    nomes = ', '.join(c.nome for c in colaboradores[:8]) + (f' e mais {len(colaboradores) - 8}' if len(colaboradores) > 8 else '')
    _notificar(quem_cuida_desligamentos(), 'Desligamento de hoje no Acess' if len(colaboradores) == 1
               else f'{len(colaboradores)} desligamentos de hoje no Acess',
               f'{nomes}: revogue os acessos nos sistemas e confirme em Desligamentos.', '/acess/desligamentos/')


def avisar_desligamentos_do_dia(hoje=None):
    """Tarefa diária (core/agendador_diario.py): no dia escolhido, chega a notificação."""
    hoje = hoje or timezone.localdate()
    gente = list(Colaborador.objects.filter(status=Colaborador.ATIVO, desligamento_em=hoje).order_by('nome'))
    avisar_desligamentos_de_hoje(gente)
    return f'{len(gente)} desligamento(s) de hoje avisado(s)'


@transaction.atomic
def decidir_inativacao(aviso, decisao, execucao=None, usuario=None, request=None, observacao=''):
    aviso = AvisoInativacao.objects.select_for_update().select_related('colaborador').get(pk=aviso.pk)
    if aviso.status != AvisoInativacao.PENDENTE:
        raise RegraAcess('Este aviso já foi decidido.')
    c, hoje = aviso.colaborador, timezone.localdate()
    observacao = str(observacao or '').strip()[:300]
    if decisao == 'descartar':
        if not observacao:
            raise RegraAcess('Diga por que não vai haver desligamento (ex.: inativado por engano).')
        aviso.status = AvisoInativacao.DESCARTADO
        registrar(request, 'INATIVACAO_DESCARTADA', 'DESLIGAMENTO', f'Sem desligamento para {c.nome}: {observacao}',
                  colaborador=c, usuario=usuario)
    elif decisao in ('imediato', 'agendar'):
        if decisao == 'imediato':
            execucao = hoje
        elif not execucao:
            raise RegraAcess('Escolha o dia do desligamento.')
        inativado = timezone.localdate(aviso.inativado_em)
        motivo = f'Usuário inativado no portal em {inativado:%d/%m/%Y}' + (f' — {observacao}' if observacao else '')
        agendar_desligamento(c, min(inativado, execucao), execucao, motivo, None, usuario, request)
        aviso.status = AvisoInativacao.IMEDIATO if decisao == 'imediato' else AvisoInativacao.AGENDADO
        aviso.execucao = execucao
        if execucao == hoje:
            c.refresh_from_db()
            avisar_desligamentos_de_hoje([c])
    else:
        raise RegraAcess('Decisão inválida.')
    aviso.observacao, aviso.decidido_por, aviso.decidido_em = observacao, usuario, timezone.now()
    aviso.save(update_fields=['status', 'execucao', 'observacao', 'decidido_por', 'decidido_em'])
    return aviso
