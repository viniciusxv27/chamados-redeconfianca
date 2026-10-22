"""Importa a base do Confiança Acess anterior para o modelo do portal.

Entra:
- colaboradores (fichas)      → ``Colaborador`` (sistemas, etapa, checklist, desligamento);
- chamados                    → ``Chamado``;
- historico_chamados          → ``EventoChamado``;
- movimentacoes (histórico)   → ``LogAcess`` (com o antes/depois de cada campo);
- mensagens (WhatsApp)        → ``ModeloMensagem`` (só os que ninguém editou no portal).

Fica de fora (é do sistema antigo, não da operação): usuários e senhas, cargos e
permissões, sessões, notificações e leituras, revisões, tarefas internas e a
auditoria de login/logout — a "auditoria" repete as movimentações. Anexos não
vêm no arquivo (estavam no R2).

Cada registro guarda o ID antigo em ``legado_id``: rodar de novo atualiza em vez
de duplicar. As lojas são casadas com os setores do portal pelo nome; as pessoas
do portal, pelo CPF (depois login e e-mail).
"""
import json
import re
from collections import Counter
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import dump_legado
from .models import (SISTEMAS, SISTEMAS_CHAMADO, Chamado, Colaborador, EventoChamado, LogAcess, ModeloMensagem)
from .servicos import codigo_chamado, cpf_valido, norm, so_digitos

User = get_user_model()

# Nome no Acess antigo → nome do setor no portal, quando não bate sozinho.
APELIDOS_LOJA = {
    'ADMINISTRATIVO': 'ESCRITORIO', 'SEDE ADMINISTRATIVA': 'ESCRITORIO',
    'BOM JESUS DO I.': 'LOJA BOM JESUS DO ITABAPOANA', 'PADUA': 'LOJA SANTO ANTONIO DE PADUA',
}
CATEGORIAS = {'chamados': 'CHAMADO', 'dados': 'DADOS', 'conclusoes': 'CONCLUSAO', 'cadastros': 'CADASTRO',
              'transferidos': 'TRANSFERENCIA', 'administracao': 'ADMIN', 'desligados': 'DESLIGAMENTO',
              'agendamentos': 'DESLIGAMENTO'}
MENSAGENS = {'geral': 'contato', 'academia_boas_vindas': 'boas_vindas', 'academia_cobranca': 'cobranca',
             'chamado_concluido': 'chamado_concluido', 'etapa4_liberacao': 'liberacao'}
ACOES_LEGIVEIS = {
    'updateTicket': 'Chamado verificado', 'closeTicket': 'Chamado concluído', 'addTicket': 'Chamado aberto',
    'add': 'Cadastro', 'update': 'Dados alterados', 'delete': 'Registro excluído',
    'CHECKLIST_LIBERACAO_ATUALIZADO': 'Checklist de liberação atualizado', 'LOGIN_SALVO': 'Login salvo',
    'GEFAC_OK': 'GEFAC concluído', 'SISTEMAS_OK': 'Sistemas OK', 'TRANSFERENCIA': 'Transferência',
    'ACADEMIA_CONCLUIDA_AGUARDANDO_ENVIO': 'Academia concluída — aguardando envio',
    'LOGIN_ENVIO_CONFIRMADO': 'Envio do login confirmado', 'ACADEMIA_OK': 'Academia concluída',
    'DESLIGAMENTO_AGENDADO': 'Desligamento agendado', 'DESLIGAMENTO_VENCIDO': 'Desligamento vencido',
    'DESLIGAMENTO_EFETIVADO': 'Desligamento efetivado', 'PERFIL_ATUALIZADO': 'Perfil atualizado',
}


def _data_hora(texto):
    if not texto:
        return None
    dt = parse_datetime(str(texto).replace('Z', '+00:00'))
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _data(texto):
    texto = str(texto or '').strip()
    if not texto:
        return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(texto[:10], fmt).date()
        except ValueError:
            pass
    dt = _data_hora(texto)
    return dt.date() if dt else None


def _bool(v):
    return v is True or str(v).strip().lower() in ('true', '1', 'sim')


class Importador:
    def __init__(self, caminho, autor=None):
        self.t = dump_legado.ler(caminho)
        self.autor = autor
        self.rel = Counter()
        self.avisos = []
        self.lojas_sem_par = Counter()
        self._setores = None
        self._usuarios_email = {u.email.lower(): u for u in User.objects.exclude(email='')}
        self.colab_por_legado = {}
        self.chamado_por_legado = {}

    # ── apoio ───────────────────────────────────────────────────────────────
    def setor(self, nome):
        nome = norm(nome)
        if not nome:
            return None
        if self._setores is None:
            from users.models import Sector
            self._setores = list(Sector.objects.all())
        alvo = APELIDOS_LOJA.get(nome, nome)
        for s in self._setores:
            if norm(s.name) == alvo:
                return s
        sem_loja = [(norm(s.name).removeprefix('LOJA '), s) for s in self._setores]
        for n, s in sem_loja:
            if n == alvo.removeprefix('LOJA '):
                return s
        for n, s in sem_loja:
            if len(alvo) >= 5 and (alvo in n or n in alvo):
                return s
        self.lojas_sem_par[nome] += 1
        return None

    def usuario(self, email):
        return self._usuarios_email.get(str(email or '').strip().lower())

    def pessoa_do_portal(self, cpf, login, email):
        """Pessoa do portal ainda sem ficha: pelo CPF, depois login e e-mail."""
        if not hasattr(self, '_livres'):
            ocupados = set(Colaborador.objects.exclude(user__isnull=True).values_list('user_id', flat=True))
            self._livres = {'cpf': {}, 'login': {}, 'email': {}}
            for u in User.objects.exclude(pk__in=ocupados).only('id', 'cpf', 'login_code', 'email', 'first_name', 'last_name', 'username'):
                if so_digitos(u.cpf):
                    self._livres['cpf'].setdefault(so_digitos(u.cpf), u)
                if u.login_code:
                    self._livres['login'].setdefault(re.sub(r'\.0$', '', u.login_code.strip()), u)
                if u.email:
                    self._livres['email'].setdefault(u.email.lower(), u)
        u = ((cpf and self._livres['cpf'].get(cpf)) or (login and self._livres['login'].get(login))
             or (email and self._livres['email'].get(email.lower())) or None)
        if u:
            for chave in self._livres.values():
                for k in [k for k, v in chave.items() if v.pk == u.pk]:
                    chave.pop(k)
        return u

    # ── fichas ──────────────────────────────────────────────────────────────
    def colaboradores(self):
        todas = list(Colaborador.objects.all())
        self._fichas_legado = {c.legado_id: c for c in todas if c.legado_id}
        # CPF só casa com ficha que já estava no portal (não importada): o Acess antigo tem CPF
        # repetido (readmissões, cadastros em dobro) e cada ficha antiga vira uma ficha aqui.
        self._fichas_cpf = {c.cpf: c for c in todas if c.cpf and not c.legado_id}
        for linha in self.t.get('colaboradores', []):
            p = linha['payload']
            legado = p.get('RC_ID') or linha.get('entity_id')
            nome = ' '.join(str(p.get('USUÁRIO') or '').split()).upper()
            cpf = so_digitos(p.get('DOCUMENTO'))
            if not nome and not cpf:
                self.rel['fichas vazias ignoradas'] += 1
                continue
            if cpf and not cpf_valido(cpf):
                self.avisos.append(f'CPF inválido mantido em branco: ficha {legado[:8]} ({nome})')
                cpf = ''
            login = re.sub(r'\.0$', '', str(p.get('LOGIN') or '').strip())[:40]
            email = str(p.get('E-MAIL') or '').strip()[:254]
            sistemas = {s: (str(p.get(s) or '').strip().upper() if str(p.get(s) or '').strip().upper()
                            in ('OK', 'PENDENTE', 'BLACKLIST') else '') for s in SISTEMAS}
            desligado = str(p.get('RC_STATUS') or '').upper() == 'DESLIGADO'
            perfil = str(p.get('RC_CHECK_PERFIL') or '').strip().upper().replace(' ', '_')
            campos = {
                'nome': nome[:200], 'cpf': cpf, 'nascimento': _data(p.get('DATA NASCIMENTO')),
                'email': email, 'telefone': str(p.get('TELEFONE') or '').strip()[:20],
                'cargo': ' '.join(str(p.get('PERFIL GEFAC') or '').split()).upper()[:100],
                'local': self.setor(p.get('PDV')), 'loja_vinculo': self.setor(p.get('LOJA_VINCULO')),
                'login': login, 'sistemas': sistemas,
                'entrega_login': str(p.get('RC_ENTREGA_LOGIN') or '').upper() if str(p.get('RC_ENTREGA_LOGIN') or '').upper() in ('PENDENTE', 'ENVIADO') else '',
                'check_pool': _bool(p.get('RC_CHECK_POOL')), 'check_login': _bool(p.get('RC_CHECK_LOGIN')),
                'check_sap': _bool(p.get('RC_CHECK_SAP')),
                'check_perfil': perfil if perfil in ('ASSURANT', 'RENOVA_ALLIED') else '',
                'status': Colaborador.DESLIGADO if desligado else Colaborador.ATIVO,
                'saida_empresa': _data(p.get('RC_SAIDA_EMPRESA')),
                'desligamento_em': _data(p.get('RC_DESLIGAMENTO_EM')),
                'desligamento_motivo': str(p.get('RC_MOTIVO_DESLIGAMENTO') or '').strip()[:1000],
                'desligado_em': _data_hora(p.get('RC_DESLIGADO_EM')),
                'pendente_desde': _data_hora(p.get('RC_PENDENTE_DESDE')) or timezone.now(),
                'origem': 'LEGADO', 'versao': int(p.get('RC_VERSION') or 1),
            }
            c = self._fichas_legado.get(legado) or (self._fichas_cpf.pop(cpf, None) if cpf else None)
            novo = c is None
            if novo:
                c = Colaborador(legado_id=legado, criado_por=self.autor)
            else:
                campos.pop('origem')  # quem já estava no portal mantém a origem
                c.legado_id = legado
            for k, v in campos.items():
                setattr(c, k, v)
            if c.user_id is None:
                c.user = self.pessoa_do_portal(cpf, login, email)
                if c.user:
                    self.rel['fichas ligadas a pessoa do portal'] += 1
            c.etapa = c.etapa_calculada
            c.save()
            etapa_antiga = {'GEFAC': 1, 'LOGIN': 2, 'ACADEMIA': 3, 'LIBERACAO': 4}.get(str(p.get('RC_ETAPA') or '').upper(), 0)
            if c.status == Colaborador.ATIVO and etapa_antiga != c.etapa:
                self.rel[f'etapa recalculada (antes {etapa_antiga}, agora {c.etapa})'] += 1
            self.colab_por_legado[legado] = c
            self.rel['fichas criadas' if novo else 'fichas atualizadas'] += 1
            self.rel[f'fichas {c.status.lower()}'] += 1
            if c.status == Colaborador.ATIVO and c.etapa:
                self.rel[f'na esteira: etapa {c.etapa}'] += 1

    # ── chamados ────────────────────────────────────────────────────────────
    @staticmethod
    def motivo(texto):
        n = norm(texto)
        if 'INICIAL' in n:
            return 'LIBERACAO_INICIAL'
        if 'FALHA' in n:
            return 'FALHA'
        if 'DESBLOQUE' in n:
            return 'DESBLOQUEIO'
        if 'SENHA' in n:
            return 'TROCA_SENHA'
        if 'TOKEN' in n:
            return 'TOKEN'
        if 'E-MAIL' in n or 'EMAIL' in n:
            return 'TROCA_EMAIL'
        return 'LIBERACAO'

    def chamados(self):
        self._chamados_existentes = {c.legado_id: c for c in Chamado.objects.exclude(legado_id='')}
        for linha in self.t.get('chamados', []):
            p = linha['payload']
            c = self.colab_por_legado.get(p.get('COLABORADOR_ID'))
            if c is None:
                self.rel['chamados sem ficha (ignorados)'] += 1
                continue
            descricao = str(p.get('MOTIVO') or p.get('DESCRICAO') or '').strip()
            motivo = self.motivo(descricao)
            obs = str(p.get('OBSERVACAO') or '').strip()
            if descricao and norm(descricao) != norm(dict(Chamado._meta.get_field('motivo').choices)[motivo]):
                obs = (f'Descrição no Acess anterior: {descricao}' + (f'\n{obs}' if obs else ''))
            sistemas = [s for s in str(p.get('SISTEMAS') or '').split('|') if s in SISTEMAS_CHAMADO]
            aberto = _data_hora(p.get('ABERTO_EM')) or timezone.now()
            limite = _data_hora(p.get('SLA_LIMITE')) or aberto
            concluido = str(p.get('STATUS') or '').upper().startswith('CONCLU')
            campos = {
                'colaborador': c, 'codigo': codigo_chamado(p.get('CHAMADO'))[:100] or '—', 'motivo': motivo,
                'sistemas': sistemas, 'observacao': obs[:3000], 'aberto_em': aberto, 'sla_original': limite,
                'proxima_verificacao': _data_hora(p.get('PROXIMA_VERIFICACAO')) or limite,
                'status': Chamado.CONCLUIDO if concluido else Chamado.ANDAMENTO,
                'concluido_em': _data_hora(p.get('CONCLUIDO_EM')) if concluido else None,
                'concluido_por': self.usuario(p.get('CONCLUIDO_POR_EMAIL')) if concluido else None,
                'criado_por': self.usuario(p.get('CRIADO_POR')), 'versao': int(p.get('VERSAO') or 1),
            }
            legado = p.get('ID')
            ch = self._chamados_existentes.get(legado)
            novo = ch is None
            ch = ch or Chamado(legado_id=legado)
            for k, v in campos.items():
                setattr(ch, k, v)
            ch.save()
            self.chamado_por_legado[legado] = ch
            self.rel['chamados criados' if novo else 'chamados atualizados'] += 1
            self.rel['chamados em andamento' if not concluido else 'chamados concluídos'] += 1

        existentes = {e.legado_id: e for e in EventoChamado.objects.exclude(legado_id='')}
        novos, mudados = [], []
        for linha in self.t.get('historico_chamados', []):
            p = linha['payload']
            ch = self.chamado_por_legado.get(p.get('CHAMADO_ID'))
            if ch is None:
                continue
            acao = str(p.get('ACAO') or '').upper()
            prox = _data_hora(p.get('PROXIMA_VERIFICACAO'))
            detalhe = {'CRIADO': 'Aberto no Acess anterior', 'CONCLUIDO': 'Concluído',
                       'VERIFICADO': 'Verificado'}.get(acao, acao.title())
            if prox and acao != 'CONCLUIDO':
                detalhe += f' — próxima verificação {timezone.localtime(prox):%d/%m/%Y %H:%M}'
            quem = self.usuario(p.get('USUARIO'))
            if not quem and p.get('USUARIO'):
                detalhe += f' (por {p["USUARIO"]})'
            campos = {'chamado': ch, 'quando': _data_hora(p.get('DATA')) or ch.aberto_em,
                      'usuario': quem, 'tipo': acao[:12], 'detalhe': detalhe[:500]}
            e = existentes.get(p.get('ID'))
            if e is None:
                novos.append(EventoChamado(legado_id=p.get('ID'), **campos))
            else:
                for k, v in campos.items():
                    setattr(e, k, v)
                mudados.append(e)
            self.rel['eventos de chamado'] += 1
        EventoChamado.objects.bulk_create(novos, batch_size=500)
        EventoChamado.objects.bulk_update(mudados, ['chamado', 'quando', 'usuario', 'tipo', 'detalhe'], batch_size=500)

    # ── histórico ───────────────────────────────────────────────────────────
    def historico(self):
        existentes = {l.legado_id: l for l in LogAcess.objects.exclude(legado_id='')}
        novos, mudados = [], []
        for linha in self.t.get('movimentacoes', []):
            p = linha['payload']
            acao = str(p.get('ACAO') or '')
            try:
                alteracoes = json.loads(p.get('ALTERACOES') or '[]')
            except ValueError:
                alteracoes = []
            antes = {a.get('campo'): a.get('antes') for a in alteracoes if isinstance(a, dict) and a.get('campo')} or None
            depois = {a.get('campo'): a.get('depois') for a in alteracoes if isinstance(a, dict) and a.get('campo')} or None
            quem = self.usuario(p.get('USUARIO'))
            alvo = ' · '.join(x for x in (p.get('NOME'), p.get('LOJA')) if x)
            descricao = ACOES_LEGIVEIS.get(acao, acao.replace('_', ' ').capitalize()) + (f' — {alvo}' if alvo else '')
            campos = {
                'quando': _data_hora(p.get('DATA')) or timezone.now(), 'usuario': quem,
                'usuario_nome': ((quem.get_full_name() if quem else '') or str(p.get('USUARIO') or 'Acess anterior'))[:200],
                'acao': acao[:40], 'categoria': CATEGORIAS.get(str(p.get('CATEGORIA') or ''), 'DADOS'),
                'colaborador': self.colab_por_legado.get(p.get('COLABORADOR_ID')),
                'descricao': descricao[:400], 'antes': antes, 'depois': depois}
            l = existentes.get(p.get('ID'))
            if l is None:
                novos.append(LogAcess(legado_id=p.get('ID'), **campos))
            else:
                for k, v in campos.items():
                    setattr(l, k, v)
                mudados.append(l)
            self.rel['registros de histórico'] += 1
        LogAcess.objects.bulk_create(novos, batch_size=500)
        LogAcess.objects.bulk_update(mudados, list(campos), batch_size=500)

    def mensagens(self):
        for linha in self.t.get('mensagens', []):
            p = linha['payload']
            chave = MENSAGENS.get(p.get('ID'))
            if not chave:
                continue
            atual = ModeloMensagem.objects.filter(chave=chave).first()
            if atual and atual.atualizado_por_id:
                self.rel['modelos de mensagem mantidos (editados no portal)'] += 1
                continue
            ModeloMensagem.objects.update_or_create(chave=chave, defaults={
                'titulo': str(p.get('NOME') or chave)[:100], 'texto': str(p.get('TEXTO') or '')[:4000]})
            self.rel['modelos de mensagem do Acess anterior'] += 1

    def operadores(self):
        """Quem operava o Acess antigo e quem é no portal (o acesso ao módulo é dado pelo SUPERADMIN)."""
        saida = []
        for linha in self.t.get('usuarios', []):
            p = linha['payload']
            u = self.usuario(p.get('Email'))
            saida.append({'nome': p.get('Nome'), 'papel': p.get('Role'), 'trata_chamados': bool(p.get('TrataChamados')),
                          'no_portal': (u.get_full_name() or u.username) if u else None,
                          'tem_acesso': bool(u and hasattr(u, 'acesso_acess'))})
        return saida

    def rodar(self):
        with transaction.atomic():
            self.colaboradores()
            self.chamados()
            self.historico()
            self.mensagens()
            from .servicos import registrar
            registrar(None, 'IMPORTACAO_LEGADO', 'ADMIN',
                      f'Importou a base do Acess anterior: {self.rel["fichas criadas"]} fichas novas, '
                      f'{self.rel["chamados criados"]} chamados, {self.rel["registros de histórico"]} registros de histórico',
                      usuario=self.autor)
        return self.rel


def importar(caminho, autor=None, aplicar=False):
    """Roda a importação. Sem ``aplicar``, desfaz tudo no fim (simulação)."""
    imp = Importador(caminho, autor)
    with transaction.atomic():
        imp.rodar()
        if not aplicar:
            transaction.set_rollback(True)
    return imp
