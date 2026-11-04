"""As páginas do portal que o modo colaborador pode sugerir, cada uma com a sua porta.

A regra de cada entrada é a mesma do menu (templates/base.html): o context
processor que acende o item, ``tem_acesso`` de users/module_access.py, ou a
função de permissão do módulo. Nada é decidido aqui de novo, e a regra só roda
para as páginas que a busca achou, dentro da pergunta — nenhuma tela do portal
paga por isto. Regra que quebra fecha a porta: página na dúvida não é sugerida.

``teste_assistente_colaborador.py`` abre cada página liberada como a própria
pessoa, para provar que o que é sugerido abre mesmo.

``passos`` só existe onde o passo a passo foi conferido na tela; é o que sobra
para orientar quando a IA está fora do ar, e entra de contexto quando ela está.
"""
from dataclasses import dataclass
from importlib import import_module

from django.urls import NoReverseMatch, reverse

from users.module_access import tem_acesso


class Contexto:
    """Quem pergunta. Guarda o resultado dos context processors já chamados."""

    def __init__(self, request):
        self.request = request
        self.user = request.user
        self._menus = {}

    def menu(self, caminho, chave):
        if caminho not in self._menus:
            modulo, funcao = caminho.rsplit('.', 1)
            try:
                self._menus[caminho] = getattr(import_module(modulo), funcao)(self.request) or {}
            except Exception:  # noqa: BLE001
                self._menus[caminho] = {}
        return bool(self._menus[caminho].get(chave))


def _menu(caminho, chave):
    return lambda ctx: ctx.menu(caminho, chave)


def _chave(chave):
    return lambda ctx: tem_acesso(ctx.user, chave)


def _funcao(caminho):
    def gate(ctx):
        modulo, funcao = caminho.split(':')
        return bool(getattr(import_module(modulo), funcao)(ctx.user))
    return gate


def _superadmin(ctx):
    return bool(ctx.user.is_superuser or getattr(ctx.user, 'hierarchy', '') == 'SUPERADMIN')


@dataclass(frozen=True)
class Pagina:
    rota: str
    titulo: str
    descricao: str
    palavras: str = ''
    gate: object = None     # None = todo mundo logado
    passos: tuple = ()

    @property
    def url(self):
        return reverse(self.rota)

    def liberada(self, ctx):
        try:
            self.url
        except NoReverseMatch:
            return False
        if self.gate is None:
            return True
        try:
            return bool(self.gate(ctx))
        except Exception:  # noqa: BLE001
            return False


PAGINAS = (
    # ── Para todo mundo ──────────────────────────────────────────────────
    Pagina('ticket_create', 'Abrir um chamado', 'Formulário de novo chamado para qualquer setor.',
           'abrir abertura novo chamado ticket solicitar pedido suporte setor', passos=(
               'Abra a tela de novo chamado.',
               'Preencha o título e escolha o setor e a categoria.',
               'Escreva a mensagem explicando o que precisa (se quiser, anexe arquivos).',
               'Clique em "Abrir Chamado".',
           )),
    Pagina('tickets_list', 'Meus chamados', 'Acompanhar os chamados abertos e atribuídos.',
           'chamado ticket acompanhar status andamento consultar lista'),
    Pagina('tickets_history', 'Histórico de chamados', 'Chamados já encerrados.',
           'chamado ticket historico antigo encerrado fechado'),
    Pagina('contracheque:my_payslips', 'Meus contracheques', 'Holerites para ver e baixar.',
           'contracheque holerite salario pagamento folha recibo'),
    Pagina('folhaponto:my_folhas', 'Folhas de ponto', 'Folhas mensais de ponto para ver e assinar.',
           'folha ponto assinar mensal espelho'),
    Pagina('documentos:my_documents', 'Meus documentos', 'Documentos enviados para a sua assinatura digital.',
           'documento assinar assinatura termo digital'),
    Pagina('communications:communications_list', 'Comunicados', 'Comunicados recebidos.',
           'comunicado aviso informativo recado'),
    Pagina('notifications:dashboard', 'Notificações', 'Avisos do sino.', 'notificacao aviso sino alerta'),
    Pagina('benefits:list', 'Clube de benefícios', 'Benefícios e parceiros da rede.',
           'beneficio clube parceiro desconto convenio'),
    Pagina('marketplace', 'Mercadinho', 'Prêmios para resgatar com Confianças (C$).',
           'mercadinho premio resgate resgatar confianca moeda loja produto'),
    Pagina('compliments:dashboard', 'Elogios', 'Elogios recebidos e enviados.', 'elogio reconhecimento parabens'),
    Pagina('feedback:dashboard', 'Feedbacks', 'Feedbacks recebidos e dados.', 'feedback avaliacao retorno devolutiva'),
    Pagina('trainings_list', 'Treinamentos', 'Vídeos de treinamento da rede.',
           'treinamento video aula tutorial capacitacao aprender'),
    Pagina('knowledge_trails:dashboard', 'Trilhas de conhecimento', 'Trilhas com aulas, quiz e certificado.',
           'trilha conhecimento aula curso certificado capacitacao aprender'),
    Pagina('quiz:inicio', 'Quiz', 'Salas de quiz para jogar.', 'quiz jogo pergunta sala partida'),
    Pagina('agenda:calendar', 'Agenda', 'Eventos, convites e disponibilidade.',
           'agenda evento calendario compromisso convite marcar'),
    Pagina('reunioes:lista', 'Reuniões', 'Reuniões, atas e transcrições.',
           'reuniao ata transcricao sala video encontro'),
    Pagina('files:files_list', 'Arquivos', 'Biblioteca de arquivos do portal.',
           'arquivo biblioteca documento manual material baixar'),
    Pagina('webmail:caixa', 'E-mail institucional', 'Caixa de e-mail da rede.', 'email correio caixa mensagem institucional'),
    Pagina('simulator:dashboard', 'Simulador de comissão', 'Simulação de comissão e metas.',
           'simulador simular simulacao comissao meta'),
    Pagina('power_bi:list', 'Power BI', 'Painéis de resultados.', 'power painel indicador resultado dashboard relatorio'),
    Pagina('power_bi:goals_list', 'Metas comerciais', 'Metas de venda por pilar.', 'meta comercial venda pilar objetivo'),
    Pagina('facilitador:inicio', 'Facilitador', 'Links e atalhos úteis do dia a dia.', 'facilitador link atalho ferramenta sistema'),
    Pagina('tasks_dashboard', 'Tarefas', 'Tarefas atribuídas a você.', 'tarefa pendencia atividade fazer'),
    Pagina('help_tutorials', 'Ajuda e tutoriais', 'Tutoriais de uso do portal.', 'ajuda tutorial duvida usar manual portal'),
    Pagina('core:anonymous_report', 'Denúncia anônima', 'Canal de denúncia sem identificação.',
           'denuncia anonima assedio etica reclamacao canal'),
    Pagina('settings', 'Configurações', 'Preferências da conta, tema e senha.',
           'configuracao preferencia tema escuro senha conta perfil'),

    # ── Dependem do acesso, como no menu ─────────────────────────────────
    Pagina('commission', 'Comissionamento', 'Comissão por pilar, vendas e projeção.',
           'comissao comissionamento pilar venda ganho remuneracao',
           gate=_chave('comissionamento')),
    Pagina('cursos:meus_cursos', 'Cursos Vivo', 'Cursos obrigatórios e envio de comprovante.',
           'curso vivo obrigatorio comprovante certificado',
           gate=_menu('cursos.context_processors.cursos_menu', 'pode_ver_cursos')),
    Pagina('tangerino:meu_ponto', 'Meu ponto', 'Marcações, jornada e saldo de horas.',
           'ponto batida bater marcacao jornada hora saldo banco atraso ajuste',
           gate=_menu('tangerino.context_processors.tangerino_gate', 'tangerino_liberado')),
    Pagina('tangerino:minhas_ferias', 'Minhas férias', 'Férias marcadas e períodos.',
           'ferias descanso recesso periodo programar',
           gate=_menu('tangerino.context_processors.tangerino_gate', 'tangerino_ferias_liberado')),
    Pagina('tangerino:escala', 'Escala', 'Escala de trabalho.', 'escala folga turno horario plantao domingo feriado',
           gate=_menu('tangerino.context_processors.tangerino_gate', 'tangerino_liberado')),
    Pagina('contagem_caixa:sangrias', 'Sangrias', 'Registrar, conferir e acompanhar as sangrias da loja.',
           'sangria retirada caixa gasto despesa dinheiro comprovante',
           gate=_funcao('contagem_caixa.permissions:pode_ver_sangrias'), passos=(
               'Abra a tela de Sangrias.',
               'Clique em "Registrar sangria".',
               'Preencha Loja, Data da sangria, Valor, Categoria e Descrição. '
               '"Pago a" e o comprovante (foto ou PDF, até 10 MB) não são obrigatórios.',
               'Clique em "Registrar".',
           )),
    Pagina('contagem_caixa:dashboard', 'Contagem de caixa', 'Contagens do caixa da loja e diferenças.',
           'caixa contagem fechamento conferencia diferenca dinheiro saldo',
           gate=_menu('contagem_caixa.context_processors.caixa_menu', 'caixa_liberado')),
    Pagina('cartoes:dashboard', 'Cartões corporativos', 'Gastos e prestação de contas dos cartões.',
           'cartao credito corporativo gasto fatura prestacao comprovante',
           gate=_menu('cartoes.context_processors.cartoes_menu', 'can_access_cartoes')),
    Pagina('financeiro:painel', 'Financeiro', 'Módulo financeiro da rede.', 'financeiro titulo pagar receber conciliacao rateio',
           gate=_menu('financeiro.context_processors.financeiro_menu', 'financeiro_liberado')),
    Pagina('vendas:dashboard', 'Vendas', 'Vendas, clientes e orçamentos.', 'venda cliente orcamento pdv lancar plano preco',
           gate=_menu('vendas.context_processors.vendas_menu', 'vendas_liberado')),
    Pagina('fibras:kanban', 'Fibras', 'Acompanhamento de instalações de fibra.', 'fibra instalacao kanban banda larga',
           gate=_menu('fibras.context_processors.fibras_menu', 'fibras_liberado')),
    Pagina('renova:inicio', 'Vini Renova', 'Avaliação de aparelho usado.', 'renova aparelho usado avaliacao trade celular',
           gate=_menu('renova.context_processors.renova_menu', 'renova_liberado')),
    Pagina('contestacao:exclusion_list', 'Contestação', 'Exclusões e contestações de comissão.',
           'contestacao contestar exclusao comissao venda',
           gate=_menu('contestacao.context_processors.contestacao_menu_context', 'can_access_contestacao_menu')),
    Pagina('checklists:dashboard', 'Checklists ADM', 'Checklists das lojas.', 'checklist adm tarefa loja rotina',
           gate=_chave('checklists')),
    Pagina('limpeza:dashboard', 'Limpeza', 'Checklist de limpeza das lojas.', 'limpeza faxina checklist loja', gate=_chave('limpeza')),
    Pagina('experiencia:dashboard', 'Experiência Vivo', 'Avaliações de experiência na loja.',
           'experiencia vivo avaliacao loja atendimento', gate=_chave('experiencia')),
    Pagina('rotina:minha', 'Rotina Gerencial', 'Semana de atividades e avisos.', 'rotina gerencial atividade semana comprovante',
           gate=lambda ctx: (ctx.menu('rotina.context_processors.rotina_menu', 'rotina_no_menu')
                             or ctx.menu('rotina.context_processors.rotina_menu', 'rotina_admin'))),
    Pagina('rotina_adm:painel', 'Rotina ADM', 'Central do ADM de loja.', 'rotina adm loja nota checklist',
           gate=_menu('rotina_adm.context_processors.rotina_adm_menu', 'rotina_adm_liberada')),
    Pagina('impulso:dashboard', 'Impulso', 'Metas do Kanban, ranking, Conectar e Inovar.',
           'impulso meta kanban ranking inovar ideia pontuacao',
           gate=_menu('impulso.context_processors.impulso_menu', 'can_access_impulso')),
    Pagina('impulso:conectar_list', 'Conectar (POPs e vídeos)', 'Cursos, vídeos e POPs do Impulso.',
           'conectar pop procedimento video curso impulso',
           gate=_menu('impulso.context_processors.impulso_menu', 'can_access_impulso')),
    Pagina('drive:index', 'Drive', 'Pastas e arquivos do Drive.', 'drive pasta arquivo google compartilhado',
           gate=_menu('drive.context_processors.drive_menu', 'drive_liberado')),
    Pagina('projects:dashboard', 'Projetos', 'Projetos e tarefas.', 'projeto tarefa sprint', gate=_chave('projetos')),
    Pagina('purchases:purchase_list', 'Compras', 'Pedidos de compra.', 'compra pedido cotacao forma pagamento', gate=_chave('compras')),
    Pagina('suppliers:supplier_list', 'Fornecedores', 'Cadastro de fornecedores.', 'fornecedor cadastro parceiro', gate=_chave('fornecedores')),
    Pagina('auditoria_sap:lista', 'Visão SAP', 'Auditoria SAP × Vivo Go.', 'sap auditoria nota fiscal vivo',
           gate=_chave('sap')),
    Pagina('acess:dashboard', 'Acess', 'Esteira de acessos.', 'acess acesso login senha sistema liberar',
           gate=_menu('acess.context_processors.acess_menu', 'acess_liberado')),
    Pagina('manage_users', 'Gestão de usuários', 'Cadastro de colaboradores.', 'usuario colaborador cadastro gestao novo',
           gate=_chave('usuarios.gerenciar')),
    Pagina('quadro_hc:quadro', 'Quadro de HC', 'Quadro de pessoas por loja.', 'hc headcount quadro pessoas vaga', gate=_superadmin),
)
