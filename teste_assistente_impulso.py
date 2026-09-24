"""Assistente: acesso completo ao Impulso (/impulso/).

Pedido: o assistente do portal precisa de acesso completo ao Impulso. Aqui se
confere que ele lê o que cada pessoa vê nas telas do módulo (Kanban,
solicitações, pontuação, feedbacks, Conectar, projetos, ideias e ciclos), age
pelas views do próprio Impulso — mesmas permissões, mesmos avisos — e que
nenhuma ação roda sem a confirmação do usuário numa mensagem seguinte.

Nada sai daqui: a OpenAI é dublê (impulso.ai._chamar_openai), as faltas do
Tangerino também (sem HTTP), o cache das ações é de memória e o fechamento do
mês só calcula as pessoas do teste. Tudo roda dentro de uma transação desfeita
no fim.
"""
import os
import sys
from datetime import date, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone

from assistente import ferramentas
from communications.models import CommunicationGroup
from core.models import Notification
from impulso import utils as impulso_utils
from impulso.models import (GRUPO_ADM, GRUPO_GESTOR, Ciclo, CicloMes, ConclusaoConteudo, ConteudoConectar,
                            ExcecaoAssiduidade, Ideia, ImpulsoFeedback, Meta, MetaAnexo, MetaComentario, MetaItem,
                            MetaVisualizacao, PontuacaoMensal, ProjetoAnexo, ProjetoFoco, TarefaProjeto)
from users.models import Sector

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {str(extra)[:2000]}')


HOJE = timezone.localdate()
PRAZO = HOJE + timedelta(days=7)
MES_DO_PRAZO = f'{PRAZO:%Y-%m}'
OUTRO_MES = f'{PRAZO.replace(day=1) + timedelta(days=62):%Y-%m}'
MES_ATUAL = f'{HOJE:%Y-%m}'


def roda(nome, args, user, turno='turno-1'):
    return ferramentas.executar(nome, args, user, turno=turno)


def prepara_e_confirma(nome, args, user):
    """Como no chat: a ação é preparada numa pergunta e confirmada na seguinte."""
    previa = roda(nome, args, user, 'turno-1')
    return previa, roda('confirmar_acao', {'acao': nome}, user, 'turno-2')


def recusa(nome, args, user):
    """A prévia recusa — e nada fica esperando confirmação."""
    saida = roda(nome, args, user, 'turno-1')
    pendente = roda('confirmar_acao', {'acao': nome}, user, 'turno-2')
    return saida.startswith('Não dá para fazer isso') and 'Não há ação' in pendente, saida


def avisado(user, titulo):
    return Notification.objects.filter(user=user, title=titulo).exists()


def pk(obj):
    return obj.pk if obj is not None else 0


marcador = transaction.atomic()
marcador.__enter__()
try:
    assert not User.objects.filter(username__startswith='zzimp.').exists(), 'usuários do teste já existem'
    openai = mock.MagicMock(return_value={'resumo': 'ZZ resumo da IA.', 'pontos_a_melhorar': ['ZZ organizar a agenda'],
                                          'nota': 8.5, 'justificativa_nota': 'ZZ justificativa.'})
    ajustes = mock.MagicMock(return_value=[])
    with mock.patch.object(ferramentas, 'cache', LocMemCache('zz-assistente-impulso', {})), \
            mock.patch('impulso.ai._chamar_openai', openai), \
            mock.patch('impulso.assiduidade_ponto.faltas_injustificadas', return_value=[]), \
            mock.patch('tangerino.client.listar_ajustes', ajustes), \
            override_settings(OPENAI_API_KEY='zz-chave-de-teste'):

        loja = Sector.objects.create(name='ZZ Loja Assistente Impulso')
        outra_loja = Sector.objects.create(name='ZZ Outra Área Assistente Impulso')

        def novo(apelido, setor=loja, **extra):
            u = User.objects.create_user(
                username=f'zzimp.{apelido}', email=f'zzimp.{apelido}@exemplo-teste.local', password='S3nha!teste',
                first_name='ZZAssImp', last_name=apelido.title(), sector=setor, **extra)
            if setor:
                u.sectors.add(setor)
            return u

        admin = novo('sara', setor=None, is_superuser=True, is_staff=True)

        def grupo(nome):
            return (CommunicationGroup.objects.filter(impulso_utils._q_grupo('name', nome)).first()
                    or CommunicationGroup.objects.create(name=nome, created_by=admin))

        ana, bia, gil = novo('ana'), novo('bia'), novo('gil')
        caio, dani, enzo = novo('caio', setor=outra_loja), novo('dani', setor=outra_loja), novo('enzo', setor=outra_loja)
        otto = novo('otto', setor=outra_loja)
        edu = novo('edu')                     # do setor da loja, mas fora dos grupos do Impulso
        grupo(GRUPO_ADM).members.add(ana, bia, caio, dani, enzo)
        grupo(GRUPO_GESTOR).members.add(gil, otto)

        print('== AS FERRAMENTAS ==')
        esquema = {x['name']: x for x in ferramentas.tools_schema()}
        acoes = ['impulso_criar_meta', 'impulso_alterar_meta', 'impulso_andamento_meta', 'impulso_decidir_meta',
                 'impulso_copiar_ou_excluir_meta', 'impulso_itens_meta', 'impulso_conversa_meta', 'impulso_feedback',
                 'impulso_conectar', 'impulso_projeto', 'impulso_ideia', 'impulso_administrar']
        do_impulso = [n for n in ferramentas.TOOLS if n.startswith('impulso_')]
        t('13 ferramentas do Impulso: 1 leitura e 12 ações', sorted(do_impulso) == sorted(acoes + ['impulso_consultar']),
          do_impulso)
        t('cada ação tem prévia e está no esquema', all(ferramentas.TOOLS[n].get('acao') and ferramentas.TOOLS[n].get('previa')
                                                        and n in esquema for n in acoes))
        t('a leitura não é ação', not ferramentas.TOOLS['impulso_consultar'].get('acao'))
        t('todo esquema é válido (required dentro de properties)', all(
            s['input_schema']['type'] == 'object'
            and set(s['input_schema']['required']) <= set(s['input_schema']['properties']) for s in esquema.values()))
        t('as ferramentas pessoais continuam', all(n in esquema for n in ('minhas_metas', 'meus_feedbacks')))
        t('nada de régua de pesos, encerrar ciclo ou progresso de vídeo',
          not any(p in n for n in do_impulso for p in ('pesos', 'encerrar', 'progresso')))

        print('\n== QUEM É DO IMPULSO ==')
        t('quem não é do Impulso não lê', 'não participa do Impulso' in roda('impulso_consultar', {'o_que': 'painel'}, edu))
        recusou, saida = recusa('impulso_criar_meta', {'titulo': 'ZZ X', 'descricao': 'x', 'prazo': f'{PRAZO}',
                                                       'gestor_id': gil.pk}, edu)
        t('nem prepara ação, e nada fica pendente', recusou and 'não participa do Impulso' in saida, saida)
        saida = roda('impulso_consultar', {'o_que': 'painel'}, ana)
        t('o painel da colaboradora abre, com a pontuação do mês', 'Pontuação de' in saida and '/impulso/' in saida, saida)
        t('o_que inválido explica as opções', 'o_que inválido' in roda('impulso_consultar', {'o_que': 'xyz'}, ana))
        saida = roda('impulso_consultar', {'o_que': 'pessoas'}, ana)
        t('a colaboradora vê o gestor do setor, com o id', f'id {gil.pk} ' in saida and 'Gestores do seu setor' in saida, saida)
        saida = roda('impulso_consultar', {'o_que': 'pessoas', 'busca': 'ZZAssImp'}, gil)
        linha_caio = next((linha for linha in saida.splitlines() if f'id {caio.pk} ' in linha), '')
        t('o gestor vê a equipe e quem é de outra área, com o aprovador',
          f'id {ana.pk} ' in saida and 'de outra área' in linha_caio and 'Otto' in linha_caio, saida)

        print('\n== META: A COLABORADORA PEDE, O GESTOR APROVA ==')
        pedido = {'titulo': 'ZZ Relatório semanal', 'descricao': 'ZZ números da loja', 'prazo': f'{PRAZO}',
                  'gestor_id': gil.pk, 'recorrencia': 'semanal', 'itens': ['ZZ Levantar números', 'ZZ Montar planilha']}
        previa = roda('impulso_criar_meta', pedido, ana, 'turno-1')
        t('a ferramenta só prepara', previa.startswith('AÇÃO PREPARADA')
          and not Meta.objects.filter(titulo='ZZ Relatório semanal').exists(), previa)
        t('o resumo diz quem aprova e traz o to-do', 'Gil' in previa and 'aprovar' in previa and 'ZZ Montar planilha' in previa,
          previa)
        t('na mesma pergunta a confirmação é recusada',
          roda('confirmar_acao', {'acao': 'impulso_criar_meta'}, ana, 'turno-1').startswith('Ainda não')
          and not Meta.objects.filter(titulo='ZZ Relatório semanal').exists())
        t('confirmar outra ação não executa a preparada',
          'A ação preparada é impulso_criar_meta' in roda('confirmar_acao', {'acao': 'impulso_projeto'}, ana, 'turno-2'))
        feito = roda('confirmar_acao', {'acao': 'impulso_criar_meta'}, ana, 'turno-2')
        meta = Meta.objects.filter(colaborador=ana, titulo='ZZ Relatório semanal').first()
        t('numa pergunta seguinte cria, pela view do Impulso, com o link', meta is not None
          and feito.startswith('Meta criada') and f'/impulso/metas/{meta.pk}/' in feito, feito)
        t('pendente, pedida por ela, com o gestor e o to-do', meta is not None
          and meta.aprovacao == Meta.Aprovacao.PENDENTE and meta.solicitada_por_id == ana.pk
          and meta.gestor_id == gil.pk and meta.itens.count() == 2, meta and (meta.aprovacao, meta.itens.count()))
        t('o gestor é avisado como na tela', avisado(gil, 'Nova solicitação de meta'))
        t('confirmar de novo não cria outra',
          'Não há ação' in roda('confirmar_acao', {'acao': 'impulso_criar_meta'}, ana, 'turno-3')
          and Meta.objects.filter(titulo='ZZ Relatório semanal').count() == 1)
        recusou, saida = recusa('impulso_criar_meta', dict(pedido, prazo=f'{HOJE - timedelta(days=1)}'), ana)
        t('prazo no passado é recusado já na prévia', recusou and 'anterior a hoje' in saida, saida)
        recusou, saida = recusa('impulso_criar_meta', dict(pedido, colaborador_id=bia.pk), ana)
        t('colaboradora não cria meta para outra pessoa', recusou and 'Só gestor' in saida, saida)
        roda('impulso_criar_meta', dict(pedido, titulo='ZZ Descartada'), ana, 'turno-1')
        t('descartar_acao desiste da ação preparada', 'descartada' in roda('descartar_acao', {}, ana, 'turno-2')
          and not Meta.objects.filter(titulo='ZZ Descartada').exists())

        saida = roda('impulso_consultar', {'o_que': 'metas', 'mes': MES_DO_PRAZO}, ana)
        t('pedido pendente não entra no Kanban', f'meta #{pk(meta)} ' not in saida and 'Solicitações pendentes: 1' in saida,
          saida)
        saida = roda('impulso_consultar', {'o_que': 'solicitacoes'}, ana)
        t('ela vê o pedido e que pode cancelar', f'meta #{pk(meta)} ' in saida and 'você pode cancelar' in saida, saida)
        saida = roda('impulso_consultar', {'o_que': 'solicitacoes'}, gil)
        t('o gestor vê o pedido para decidir', f'meta #{pk(meta)} ' in saida and 'você pode aprovar ou recusar' in saida,
          saida)
        recusou, saida = recusa('impulso_decidir_meta', {'acao': 'aprovar', 'meta_id': pk(meta)}, ana)
        t('quem pediu não aprova o próprio pedido', recusou and 'Quem pediu não decide' in saida, saida)
        _, feito = prepara_e_confirma('impulso_decidir_meta', {'acao': 'aprovar', 'meta_id': pk(meta)}, gil)
        meta.refresh_from_db()
        t('o gestor aprova e ela é avisada', meta.aprovacao == Meta.Aprovacao.APROVADA
          and avisado(ana, 'Solicitação aprovada') and 'aprovada' in feito, feito)

        print('\n== O KANBAN COMO NA TELA ==')
        saida = roda('impulso_consultar', {'o_que': 'metas', 'mes': MES_DO_PRAZO}, ana)
        t('agora a meta está no Kanban dela, no mês do prazo', f'meta #{meta.pk} ' in saida and 'to-do 0/2' in saida, saida)
        t('em outro mês ela some',
          f'meta #{meta.pk} ' not in roda('impulso_consultar', {'o_que': 'metas', 'mes': OUTRO_MES}, ana))
        t('com mes=todos ela volta', f'meta #{meta.pk} ' in roda('impulso_consultar', {'o_que': 'metas', 'mes': 'todos'}, ana))
        t('o gestor vê a meta da equipe',
          f'meta #{meta.pk} ' in roda('impulso_consultar', {'o_que': 'metas', 'mes': MES_DO_PRAZO}, gil))
        t('a colega que não participa não vê nem abre',
          f'meta #{meta.pk} ' not in roda('impulso_consultar', {'o_que': 'metas', 'mes': 'todos'}, bia)
          and 'não encontrada' in roda('impulso_consultar', {'o_que': 'meta', 'meta_id': meta.pk}, bia))
        t('o gestor de outra área também não abre',
          'não encontrada' in roda('impulso_consultar', {'o_que': 'meta', 'meta_id': meta.pk}, otto))

        print('\n== TO-DO, COMENTÁRIOS E LINKS ==')
        _, feito = prepara_e_confirma('impulso_itens_meta', {'acao': 'adicionar', 'meta_id': meta.pk,
                                                             'textos': ['ZZ Revisar com o gestor']}, ana)
        item = MetaItem.objects.filter(meta=meta, texto='ZZ Revisar com o gestor').first()
        t('ela acrescenta um passo', item is not None and f'item #{item.pk}' in feito, feito)
        _, feito = prepara_e_confirma('impulso_itens_meta', {'acao': 'concluir', 'item_id': pk(item)}, ana)
        item.refresh_from_db()
        t('e marca como feito (view JSON do to-do)', item.concluido and item.concluido_por_id == ana.pk
          and 'to-do 1/3' in feito, feito)
        recusou, saida = recusa('impulso_itens_meta', {'acao': 'concluir', 'item_id': item.pk}, ana)
        t('marcar de novo é recusado na prévia', recusou and 'já está feito' in saida, saida)
        recusou, saida = recusa('impulso_itens_meta', {'acao': 'reabrir', 'item_id': item.pk}, bia)
        t('quem não responde pela meta não mexe no to-do', recusou and 'não pode marcar' in saida, saida)
        prepara_e_confirma('impulso_itens_meta', {'acao': 'reabrir', 'item_id': item.pk}, ana)
        item.refresh_from_db()
        t('reabre o passo', not item.concluido)
        prepara_e_confirma('impulso_itens_meta', {'acao': 'editar', 'item_id': item.pk, 'texto': 'ZZ Revisar com o Gil'}, ana)
        item.refresh_from_db()
        t('corrige o texto do passo', item.texto == 'ZZ Revisar com o Gil')

        _, feito = prepara_e_confirma('impulso_conversa_meta', {'acao': 'comentar', 'meta_id': meta.pk,
                                                                'mensagem': 'ZZ Comecei hoje.'}, ana)
        comentario = MetaComentario.objects.filter(meta=meta, autor=ana, mensagem='ZZ Comecei hoje.').first()
        t('comenta e o gestor é avisado', comentario is not None and avisado(gil, 'Novo comentário em meta')
          and f'#{comentario.pk}' in feito, feito)
        _, feito = prepara_e_confirma('impulso_conversa_meta', {
            'acao': 'anexar_link', 'meta_id': meta.pk, 'url': 'https://exemplo-teste.local/planilha',
            'titulo': 'ZZ Planilha'}, ana)
        anexo = MetaAnexo.objects.filter(meta=meta, url='https://exemplo-teste.local/planilha').first()
        t('anexa um link', anexo is not None and anexo.tipo == MetaAnexo.Tipo.LINK and anexo.titulo == 'ZZ Planilha', feito)
        recusou, saida = recusa('impulso_conversa_meta', {'acao': 'anexar_link', 'meta_id': meta.pk,
                                                          'url': 'planilha no drive'}, ana)
        t('link sem http é recusado', recusou and 'http' in saida, saida)
        prepara_e_confirma('impulso_conversa_meta', {'acao': 'renomear_anexo', 'anexo_id': pk(anexo),
                                                     'titulo': 'ZZ Planilha final'}, ana)
        anexo.refresh_from_db()
        t('renomeia o anexo', anexo.titulo == 'ZZ Planilha final')
        vistas = MetaVisualizacao.objects.filter(meta=meta).count()
        saida = roda('impulso_consultar', {'o_que': 'meta', 'meta_id': meta.pk}, ana)
        t('o detalhe traz to-do, anexo, comentário e o que ela pode fazer', all(
            x in saida for x in (f'item #{item.pk}', f'anexo #{anexo.pk}', f'comentário #{pk(comentario)}', 'entregar',
                                 'ZZ números da loja', 'ZZ Planilha final')), saida)
        t('ler o detalhe não marca a meta como vista', MetaVisualizacao.objects.filter(meta=meta).count() == vistas)
        recusou, saida = recusa('impulso_conversa_meta', {'acao': 'excluir_anexo', 'anexo_id': anexo.pk}, bia)
        t('quem não vê a meta não apaga anexo', recusou and 'não encontrado' in saida, saida)
        prepara_e_confirma('impulso_conversa_meta', {'acao': 'excluir_anexo', 'anexo_id': anexo.pk}, ana)
        t('quem anexou exclui o anexo', not MetaAnexo.objects.filter(pk=anexo.pk).exists())
        recusou, saida = recusa('impulso_conversa_meta', {'acao': 'excluir_comentario', 'comentario_id': pk(comentario)}, bia)
        t('colega não apaga comentário de outra pessoa', recusou and 'seus próprios comentários' in saida, saida)
        prepara_e_confirma('impulso_itens_meta', {'acao': 'excluir', 'item_id': item.pk}, gil)
        t('o gestor da meta remove um passo', not MetaItem.objects.filter(pk=item.pk).exists())

        print('\n== ALTERAR, RESPONSÁVEIS E ANDAMENTO ==')
        novo_prazo = HOJE + timedelta(days=10)
        recusou, saida = recusa('impulso_alterar_meta', {'meta_id': meta.pk, 'prazo': f'{novo_prazo}'}, ana)
        t('colaboradora não edita a meta', recusou and 'não pode editar' in saida, saida)
        previa, feito = prepara_e_confirma('impulso_alterar_meta', {'meta_id': meta.pk, 'prazo': f'{novo_prazo}',
                                                                    'adicionar_participantes': [bia.pk]}, gil)
        meta.refresh_from_db()
        t('o gestor muda o prazo e inclui a Bia', meta.prazo == novo_prazo
          and meta.participantes.filter(pk=bia.pk).exists(), (previa, feito))
        t('a prévia disse quem recebe aviso', 'Bia' in previa and 'recebem aviso' in previa, previa)
        t('a mudança vira comentário e a Bia é avisada', MetaComentario.objects.filter(
            meta=meta, autor=gil, mensagem__contains='editou a atividade').exists()
          and avisado(bia, 'Você foi incluído em uma meta'))
        t('o que não mudou continua igual (a tela salva o formulário inteiro)', meta.titulo == 'ZZ Relatório semanal'
          and meta.recorrencia == Meta.Recorrencia.SEMANAL and meta.descricao == 'ZZ números da loja')
        t('agora a Bia vê a meta', f'meta #{meta.pk} ' in roda('impulso_consultar', {'o_que': 'metas', 'mes': 'todos'}, bia))

        _, feito = prepara_e_confirma('impulso_andamento_meta', {'acao': 'mover', 'meta_id': meta.pk,
                                                                 'status': 'em andamento'}, ana)
        meta.refresh_from_db()
        t('ela move para Em Andamento (view JSON do Kanban)', meta.status == Meta.Status.EM_ANDAMENTO, feito)
        recusou, saida = recusa('impulso_andamento_meta', {'acao': 'mover', 'meta_id': meta.pk, 'status': 'CONCLUIDA'}, ana)
        t('concluir pelo Kanban é recusado', recusou and 'avaliação do gestor' in saida, saida)
        recusou, saida = recusa('impulso_andamento_meta', {'acao': 'avaliar', 'meta_id': meta.pk, 'nota_qualidade': 5,
                                                           'nota_prazo': 5}, ana)
        t('colaboradora não se avalia', recusou and 'Apenas o gestor' in saida, saida)
        _, feito = prepara_e_confirma('impulso_andamento_meta', {'acao': 'entregar', 'meta_id': meta.pk,
                                                                 'entrega_link': 'https://exemplo-teste.local/entrega'}, ana)
        meta.refresh_from_db()
        t('entrega com link e o gestor é avisado', meta.status == Meta.Status.ENTREGUE
          and meta.entrega_link == 'https://exemplo-teste.local/entrega' and avisado(gil, 'Meta entregue'), feito)
        recusou, saida = recusa('impulso_andamento_meta', {'acao': 'avaliar', 'meta_id': meta.pk, 'nota_qualidade': 7,
                                                           'nota_prazo': 5}, gil)
        t('nota fora de 0 a 5 é recusada', recusou and '0 a 5' in saida, saida)
        previa, feito = prepara_e_confirma('impulso_andamento_meta', {
            'acao': 'avaliar', 'meta_id': meta.pk, 'nota_qualidade': 4, 'nota_prazo': 5, 'comentario': 'ZZ Bom trabalho'}, gil)
        meta.refresh_from_db()
        proxima = meta.ocorrencias.first()
        t('o gestor avalia: concluída, com as notas, e ela é avisada', meta.status == Meta.Status.CONCLUIDA
          and (meta.nota_qualidade, meta.nota_prazo) == (4, 5) and avisado(ana, 'Meta avaliada'), feito)
        t('a prévia anunciou a próxima ocorrência', 'próxima ocorrência' in previa
          and f'{novo_prazo + timedelta(days=7):%d/%m/%Y}' in previa, previa)
        t('e ela nasceu, com aviso', proxima is not None and proxima.prazo == novo_prazo + timedelta(days=7)
          and f'meta #{proxima.pk}' in feito and avisado(ana, 'Tarefa recorrente reaberta'), feito)
        recusou, saida = recusa('impulso_andamento_meta', {'acao': 'mover', 'meta_id': meta.pk, 'status': 'A_FAZER'}, ana)
        t('meta concluída não volta pelo Kanban', recusou and 'concluída' in saida, saida)

        print('\n== DUPLICAR, PEDIR CÓPIA, CANCELAR E EXCLUIR ==')
        _, feito = prepara_e_confirma('impulso_copiar_ou_excluir_meta', {'acao': 'duplicar', 'meta_id': meta.pk}, gil)
        copia = Meta.objects.filter(duplicada_de=meta, created_by=gil).first()
        t('o gestor duplica: cópia aprovada, com to-do e responsáveis', copia is not None
          and copia.aprovacao == Meta.Aprovacao.APROVADA and copia.itens.count() == meta.itens.count()
          and copia.participantes.filter(pk=bia.pk).exists() and f'meta #{copia.pk}' in feito, feito)
        recusou, saida = recusa('impulso_copiar_ou_excluir_meta', {'acao': 'duplicar', 'meta_id': meta.pk}, ana)
        t('quem não edita é orientado a pedir a cópia', recusou and 'solicitar_duplicacao' in saida, saida)
        _, feito = prepara_e_confirma('impulso_copiar_ou_excluir_meta', {'acao': 'solicitar_duplicacao',
                                                                         'meta_id': meta.pk}, ana)
        pedido_copia = Meta.objects.filter(duplicada_de=meta, solicitada_por=ana,
                                           aprovacao=Meta.Aprovacao.PENDENTE).first()
        t('ela pede a cópia ao gestor do setor', pedido_copia is not None and pedido_copia.gestor_id == gil.pk
          and avisado(gil, 'Pedido de duplicação de meta'), feito)
        recusou, saida = recusa('impulso_copiar_ou_excluir_meta', {'acao': 'solicitar_duplicacao', 'meta_id': meta.pk}, ana)
        t('não empilha dois pedidos iguais', recusou and 'já pediu' in saida, saida)
        _, feito = prepara_e_confirma('impulso_decidir_meta', {'acao': 'cancelar_solicitacao',
                                                               'meta_id': pk(pedido_copia)}, ana)
        t('e desiste do pedido: apagado, com o gestor avisado', not Meta.objects.filter(pk=pk(pedido_copia)).exists()
          and avisado(gil, 'Solicitação cancelada'), feito)
        recusou, saida = recusa('impulso_copiar_ou_excluir_meta', {'acao': 'excluir', 'meta_id': pk(copia)}, ana)
        t('colaboradora nunca exclui meta', recusou and 'não pode excluir' in saida, saida)
        previa, feito = prepara_e_confirma('impulso_copiar_ou_excluir_meta', {'acao': 'excluir', 'meta_id': pk(copia)}, gil)
        t('o gestor exclui a cópia e ela é avisada', not Meta.objects.filter(pk=pk(copia)).exists()
          and avisado(ana, 'Meta removida') and 'Não dá para desfazer' in previa, feito)

        print('\n== DEMANDA PARA OUTRA ÁREA ==')
        previa, feito = prepara_e_confirma('impulso_criar_meta', {
            'titulo': 'ZZ Apoio no inventário', 'descricao': 'ZZ ajuda', 'prazo': f'{PRAZO}', 'colaborador_id': caio.pk}, gil)
        demanda = Meta.objects.filter(colaborador=caio, titulo='ZZ Apoio no inventário').first()
        t('a prévia avisa que é de outra área e quem aprova', 'outra área' in previa and 'Otto' in previa, previa)
        t('nasce pendente, com o gestor de lá avisado', demanda is not None
          and demanda.aprovacao == Meta.Aprovacao.PENDENTE and demanda.gestor_id == otto.pk
          and demanda.solicitada_por_id == gil.pk and avisado(otto, 'Demanda de outra área para aprovar'), feito)
        _, feito = prepara_e_confirma('impulso_decidir_meta', {'acao': 'recusar', 'meta_id': pk(demanda),
                                                               'motivo': 'ZZ sem braço agora'}, otto)
        demanda.refresh_from_db()
        t('o gestor de lá recusa com motivo', demanda.aprovacao == Meta.Aprovacao.RECUSADA
          and demanda.motivo_recusa == 'ZZ sem braço agora' and avisado(caio, 'Solicitação recusada'), feito)
        t('a recusa aparece nas solicitações de quem pediu',
          f'meta #{demanda.pk} ' in roda('impulso_consultar', {'o_que': 'solicitacoes'}, gil))

        print('\n== PONTUAÇÃO, RANKING E PAINEL ==')
        saida = roda('impulso_consultar', {'o_que': 'ranking', 'setor_id': loja.pk}, gil)
        t('ranking do setor, ao vivo', 'Ranking do Impulso' in saida and 'Ana' in saida and 'Bia' in saida
          and 'Faixas:' in saida, saida)
        saida = roda('impulso_consultar', {'o_que': 'pontuacao'}, ana)
        t('a própria pontuação, item a item', 'Sua pontuação' in saida and 'Item a item' in saida and 'CONFIAR' in saida,
          saida)
        t('colaboradora não vê o detalhe de outra pessoa', 'só pode ver o seu próprio' in roda(
            'impulso_consultar', {'o_que': 'pontuacao', 'colaborador_id': bia.pk}, ana))
        t('o gestor vê o de quem quiser',
          'Pontuação de' in roda('impulso_consultar', {'o_que': 'pontuacao', 'colaborador_id': ana.pk}, gil))
        t('atividades em aberto da equipe', f'meta #{pk(proxima)} ' in roda('impulso_consultar', {'o_que': 'atividades'}, gil))
        saida = roda('impulso_consultar', {'o_que': 'painel'}, gil)
        t('painel do gestor', 'gestor do Impulso' in saida and 'esperando a sua avaliação' in saida, saida)

        print('\n== FEEDBACK (A IA É DUBLÊ) ==')
        recusou, saida = recusa('impulso_feedback', {'acao': 'criar', 'colaborador_id': bia.pk, 'pontos_fortes': 'x',
                                                     'pontos_melhoria': 'y'}, ana)
        t('só gestor registra feedback', recusou and 'gestores do Impulso' in saida, saida)
        previa, feito = prepara_e_confirma('impulso_feedback', {
            'acao': 'criar', 'colaborador_id': ana.pk, 'mes': MES_ATUAL, 'pontos_fortes': 'ZZ Organizada',
            'pontos_melhoria': 'ZZ Delegar mais'}, gil)
        fb = ImpulsoFeedback.objects.filter(colaborador=ana, gestor=gil, pontos_fortes='ZZ Organizada').first()
        t('a prévia avisa da IA e de quem é avisado', 'OpenAI' in previa and 'recebe aviso' in previa, previa)
        t('registra pela view, com a análise da IA (dublê)', fb is not None and openai.called
          and fb.nota_ia is not None and float(fb.nota_ia) == 8.5 and 'nota IA 8.5' in feito, feito)
        t('a colaboradora é avisada', avisado(ana, 'Novo feedback mensal'))
        t('ela vê o feedback na lista', f'feedback #{pk(fb)} ' in roda('impulso_consultar', {'o_que': 'feedbacks'}, ana))
        t('e o detalhe com a análise',
          'ZZ resumo da IA' in roda('impulso_consultar', {'o_que': 'feedback', 'feedback_id': pk(fb)}, ana))
        t('a colega não abre o feedback dela',
          'não encontrado' in roda('impulso_consultar', {'o_que': 'feedback', 'feedback_id': pk(fb)}, bia))
        sem_analise = ImpulsoFeedback.objects.create(gestor=gil, colaborador=bia, referencia_mes=HOJE.replace(day=1),
                                                     pontos_fortes='ZZ a', pontos_melhoria='ZZ b')
        openai.reset_mock()
        saida = roda('impulso_consultar', {'o_que': 'feedback', 'feedback_id': sem_analise.pk}, bia)
        t('ler feedback sem análise não chama a IA', 'ainda não gerada' in saida and not openai.called, saida)
        openai.return_value = {'resumo': 'ZZ novo resumo.', 'pontos_a_melhorar': ['ZZ foco'], 'nota': 6,
                               'justificativa_nota': 'ZZ ok.'}
        _, feito = prepara_e_confirma('impulso_feedback', {'acao': 'regenerar_ia', 'feedback_id': pk(fb)}, gil)
        fb.refresh_from_db()
        t('quem deu o feedback refaz a análise', openai.called and float(fb.nota_ia) == 6.0 and 'nota IA 6.0' in feito, feito)

        print('\n== CONECTAR ==')
        _, feito = prepara_e_confirma('impulso_conectar', {
            'acao': 'criar_conteudo', 'grupo': 'curso', 'titulo': 'ZZ Curso de atendimento',
            'url': 'https://exemplo-teste.local/curso', 'pessoas': [ana.pk]}, gil)
        curso = ConteudoConectar.objects.filter(titulo='ZZ Curso de atendimento').first()
        t('o gestor publica um curso só com link, direcionado à Ana', curso is not None
          and curso.tipo == ConteudoConectar.Tipo.CURSO and list(curso.obrigatorio_para.all()) == [ana]
          and avisado(ana, 'Novo curso obrigatório'), feito)
        recusou, saida = recusa('impulso_conectar', {'acao': 'criar_conteudo', 'grupo': 'curso', 'titulo': 'ZZ X'}, ana)
        t('a equipe não publica curso', recusou and 'só pode subir POPs' in saida, saida)
        t('a Bia não vê o curso direcionado à Ana',
          f'conteúdo #{pk(curso)} ' not in roda('impulso_consultar', {'o_que': 'conectar'}, bia))
        _, feito = prepara_e_confirma('impulso_conectar', {'acao': 'concluir', 'conteudo_id': pk(curso)}, ana)
        conclusao = ConclusaoConteudo.objects.filter(conteudo=curso, user=ana).first()
        t('ela conclui e vai para a conferência', conclusao is not None and conclusao.concluido
          and conclusao.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE
          and avisado(gil, 'Conteúdo aguardando conferência'), feito)
        t('o gestor vê na fila de conferência',
          f'conclusão #{pk(conclusao)} ' in roda('impulso_consultar', {'o_que': 'conectar'}, gil))
        recusou, saida = recusa('impulso_conectar', {'acao': 'aprovar_conclusao', 'conclusao_id': pk(conclusao)}, ana)
        t('ninguém confere a própria conclusão', recusou and 'própria' in saida, saida)
        recusou, saida = recusa('impulso_conectar', {'acao': 'recusar_conclusao', 'conclusao_id': pk(conclusao)}, gil)
        t('recusa sem motivo é barrada', recusou and 'motivo' in saida, saida)
        prepara_e_confirma('impulso_conectar', {'acao': 'recusar_conclusao', 'conclusao_id': pk(conclusao),
                                                'observacao': 'ZZ faltou o certificado'}, gil)
        conclusao.refresh_from_db()
        t('o gestor recusa com motivo e ela é avisada', conclusao.aprovacao == ConclusaoConteudo.Aprovacao.RECUSADA
          and avisado(ana, 'Conteúdo recusado'))
        previa, _ = prepara_e_confirma('impulso_conectar', {'acao': 'concluir', 'conteudo_id': pk(curso)}, ana)
        conclusao.refresh_from_db()
        t('ela reenvia e volta para a fila', conclusao.aprovacao == ConclusaoConteudo.Aprovacao.PENDENTE
          and 'reenvio' in previa, previa)
        prepara_e_confirma('impulso_conectar', {'acao': 'aprovar_conclusao', 'conclusao_id': pk(conclusao)}, gil)
        conclusao.refresh_from_db()
        t('e o gestor aprova', conclusao.aprovacao == ConclusaoConteudo.Aprovacao.APROVADA
          and avisado(ana, 'Conteúdo aprovado'))
        video = ConteudoConectar.objects.create(tipo=ConteudoConectar.Tipo.VIDEO, titulo='ZZ Vídeo do POP',
                                                video='impulso/conectar/video/zz-teste.mp4', criado_por=gil)
        recusou, saida = recusa('impulso_conectar', {'acao': 'concluir', 'conteudo_id': video.pk}, ana)
        t('vídeo do portal só se conclui assistindo na tela', recusou and 'assistir até o fim' in saida, saida)
        prepara_e_confirma('impulso_conectar', {'acao': 'editar_conteudo', 'conteudo_id': pk(curso),
                                                'titulo': 'ZZ Curso de atendimento 2'}, gil)
        curso.refresh_from_db()
        t('o gestor edita sem perder direcionamento, link nem obrigatoriedade',
          curso.titulo == 'ZZ Curso de atendimento 2' and list(curso.obrigatorio_para.all()) == [ana]
          and curso.url == 'https://exemplo-teste.local/curso' and curso.obrigatorio)
        previa, _ = prepara_e_confirma('impulso_conectar', {'acao': 'excluir_conteudo', 'conteudo_id': pk(curso)}, gil)
        t('e exclui, depois de mostrar o estrago', not ConteudoConectar.objects.filter(pk=pk(curso)).exists()
          and '1 de quem já tinha concluído' in previa and avisado(ana, 'Conteúdo removido do Conectar'), previa)

        print('\n== CICLO: FECHAR E REABRIR O MÊS ==')
        # Antes das tarefas de projeto: com tarefa de Projeto FOCO no mês, o fechamento quebra na própria view
        # (impulso/ciclos.fechar_mes grava detalhes com Decimal no JSONField) — defeito do Impulso, não do assistente.
        ciclo = Ciclo.objects.create(nome='ZZ Ciclo do assistente', inicio=HOJE.replace(day=1), fim=HOJE, criado_por=gil)
        mes_ciclo = CicloMes.objects.create(ciclo=ciclo, referencia=HOJE.replace(day=1))
        recusou, saida = recusa('impulso_administrar', {'acao': 'fechar_mes', 'mes_id': mes_ciclo.pk}, ana)
        t('colaboradora não fecha mês', recusou and 'gestores do Impulso' in saida, saida)
        so_do_teste = User.objects.filter(pk__in=[ana.pk, bia.pk, caio.pk])
        with mock.patch('impulso.ciclos.get_colaboradores', lambda: so_do_teste):
            previa, feito = prepara_e_confirma('impulso_administrar', {'acao': 'fechar_mes', 'mes_id': mes_ciclo.pk}, gil)
        mes_ciclo.refresh_from_db()
        t('o gestor fecha o mês: pontuação congelada pela view', mes_ciclo.is_fechado
          and PontuacaoMensal.objects.filter(mes=mes_ciclo).count() == 3 and 'congela a pontuação' in previa,
          (previa, feito))
        saida = roda('impulso_consultar', {'o_que': 'ciclos', 'mes_id': mes_ciclo.pk}, ana)
        t('o mês fechado aparece em ciclos, para qualquer membro', 'Ana' in saida and 'fechado em' in saida, saida)
        t('fechar de novo é recusado', recusa('impulso_administrar', {'acao': 'fechar_mes', 'mes_id': mes_ciclo.pk}, gil)[0])
        prepara_e_confirma('impulso_administrar', {'acao': 'reabrir_mes', 'mes_id': mes_ciclo.pk}, gil)
        mes_ciclo.refresh_from_db()
        t('e reabre para recálculo', not mes_ciclo.is_fechado)
        t('a lista de ciclos mostra o ciclo', f'ciclo #{ciclo.pk} ' in roda('impulso_consultar', {'o_que': 'ciclos'}, gil))

        print('\n== PROJETO FOCO ==')
        recusou, saida = recusa('impulso_projeto', {'acao': 'criar', 'nome': 'ZZ X'}, ana)
        t('só gestor cria projeto', recusou and 'gestores do Impulso' in saida, saida)
        _, feito = prepara_e_confirma('impulso_projeto', {'acao': 'criar', 'nome': 'ZZ Projeto Vitrine',
                                                          'descricao': 'ZZ vitrine nova', 'membros': [ana.pk, bia.pk]}, gil)
        projeto = ProjetoFoco.objects.filter(nome='ZZ Projeto Vitrine').first()
        t('o gestor cria o projeto e a equipe é avisada', projeto is not None
          and set(projeto.membros.values_list('pk', flat=True)) == {ana.pk, bia.pk}
          and avisado(bia, 'Adicionado a projeto foco'), feito)
        recusou, saida = recusa('impulso_projeto', {'acao': 'criar_tarefa', 'projeto_id': pk(projeto), 'titulo': 'ZZ T',
                                                    'responsavel_id': caio.pk}, gil)
        t('responsável de fora da equipe é recusado', recusou and 'equipe do projeto' in saida, saida)
        _, feito = prepara_e_confirma('impulso_projeto', {
            'acao': 'criar_tarefa', 'projeto_id': pk(projeto), 'titulo': 'ZZ Montar vitrine', 'responsavel_id': ana.pk,
            'prazo': f'{PRAZO}'}, gil)
        tarefa = TarefaProjeto.objects.filter(projeto=projeto, titulo='ZZ Montar vitrine').first()
        t('cria a tarefa para a Ana, que é avisada', tarefa is not None and tarefa.responsavel_id == ana.pk
          and tarefa.prazo == PRAZO and avisado(ana, 'Nova tarefa em projeto foco'), feito)
        recusou, saida = recusa('impulso_projeto', {'acao': 'status_tarefa', 'tarefa_id': pk(tarefa), 'status': 'CONCLUIDA'},
                                bia)
        t('a Bia não mexe na tarefa da Ana', recusou and 'Sem permissão' in saida, saida)
        prepara_e_confirma('impulso_projeto', {'acao': 'status_tarefa', 'tarefa_id': pk(tarefa), 'status': 'concluida'}, ana)
        tarefa.refresh_from_db()
        t('a Ana conclui a tarefa dela', tarefa.status == TarefaProjeto.Status.CONCLUIDA)
        _, feito = prepara_e_confirma('impulso_projeto', {'acao': 'anexar_link', 'projeto_id': pk(projeto),
                                                          'url': 'https://exemplo-teste.local/vitrine',
                                                          'titulo': 'ZZ Fotos'}, bia)
        anexo_projeto = ProjetoAnexo.objects.filter(projeto=projeto, url='https://exemplo-teste.local/vitrine').first()
        t('membro anexa link no projeto', anexo_projeto is not None, feito)
        saida = roda('impulso_consultar', {'o_que': 'projeto', 'projeto_id': pk(projeto)}, bia)
        t('a Bia abre o projeto e vê só as tarefas dela', 'ZZ Projeto Vitrine' in saida
          and f'tarefa #{tarefa.pk} ' not in saida and f'anexo #{pk(anexo_projeto)} ' in saida, saida)
        t('quem não é da equipe não abre',
          'não faz parte' in roda('impulso_consultar', {'o_que': 'projeto', 'projeto_id': pk(projeto)}, caio))
        recusou, saida = recusa('impulso_projeto', {'acao': 'excluir_anexo', 'anexo_id': pk(anexo_projeto)}, ana)
        t('colega não apaga o anexo de outro membro', recusou and 'não pode excluir' in saida, saida)
        prepara_e_confirma('impulso_projeto', {'acao': 'excluir_anexo', 'anexo_id': pk(anexo_projeto)}, bia)
        t('quem anexou exclui', not ProjetoAnexo.objects.filter(pk=pk(anexo_projeto)).exists())
        prepara_e_confirma('impulso_projeto', {'acao': 'editar', 'projeto_id': pk(projeto), 'remover_membros': [bia.pk]}, gil)
        projeto.refresh_from_db()
        t('o gestor tira a Bia e o resto do projeto fica', list(projeto.membros.all()) == [ana] and projeto.ativo
          and projeto.descricao == 'ZZ vitrine nova' and avisado(bia, 'Removido de projeto foco'))
        previa, feito = prepara_e_confirma('impulso_projeto', {'acao': 'concluir', 'projeto_id': pk(projeto)}, gil)
        projeto.refresh_from_db()
        t('conclui o projeto, que vai para a aprovação do SUPERADMIN',
          projeto.aguardando_aprovacao and 'aprovação de um SUPERADMIN' in previa
          and 'metade dos pontos' in previa, feito)
        t('quem tem tarefa ainda não é avisado (os pontos não entraram)',
          not avisado(ana, 'Projeto foco concluído'))
        t('e quem decide é', avisado(admin, 'Conclusão de projeto para aprovar'))
        t('a consulta conta em que pé está',
          'aguardando aprovação' in roda('impulso_consultar', {'o_que': 'projeto', 'projeto_id': pk(projeto)}, gil))
        prepara_e_confirma('impulso_projeto', {'acao': 'reabrir', 'projeto_id': pk(projeto)}, gil)
        projeto.refresh_from_db()
        t('e reabre', not projeto.concluido)

        print('\n== INOVAR ==')
        _, feito = prepara_e_confirma('impulso_ideia', {
            'acao': 'criar', 'descricao': 'ZZ Ideia de fila única', 'setor_impacto': 'ZZ Loja',
            'motivo': 'ZZ menos espera', 'participantes': [bia.pk]}, ana)
        ideia = Ideia.objects.filter(autor=ana, descricao='ZZ Ideia de fila única').first()
        t('ela envia a ideia com a Bia, que é avisada', ideia is not None and list(ideia.participantes.all()) == [bia]
          and avisado(bia, 'Você entrou em uma ideia'), feito)
        recusou, saida = recusa('impulso_ideia', {'acao': 'criar', 'descricao': 'ZZ x', 'setor_impacto': 'ZZ',
                                                  'motivo': 'ZZ', 'participantes': [bia.pk, caio.pk, dani.pk, enzo.pk]}, ana)
        t('mais de 3 participantes é recusado', recusou and 'no máximo 3' in saida, saida)
        saida = roda('impulso_consultar', {'o_que': 'ideias', 'status': 'NOVA'}, gil)
        linha = next((linha for linha in saida.splitlines() if f'ideia #{pk(ideia)} ' in linha), '')
        t('o gestor vê a ideia sem saber de quem é', bool(linha) and 'Ana' not in linha and 'Bia' not in linha
          and 'autoria oculta' in saida, linha or saida)
        saida = roda('impulso_consultar', {'o_que': 'ideias'}, ana)
        t('a autora vê que é dela e com quem', 'sua, com' in saida and 'Bia' in saida, saida)
        prepara_e_confirma('impulso_ideia', {'acao': 'editar', 'ideia_id': pk(ideia), 'motivo': 'ZZ menos espera no caixa'},
                           ana)
        ideia.refresh_from_db()
        t('ela edita sem perder a Bia (formulário inteiro)', ideia.motivo == 'ZZ menos espera no caixa'
          and list(ideia.participantes.all()) == [bia])
        recusou, saida = recusa('impulso_ideia', {'acao': 'decidir', 'ideia_id': pk(ideia), 'status': 'APROVADA'}, ana)
        t('só gestor decide ideia', recusou and 'gestores do Impulso' in saida, saida)
        previa, feito = prepara_e_confirma('impulso_ideia', {'acao': 'decidir', 'ideia_id': pk(ideia), 'status': 'aprovada',
                                                             'resposta': 'ZZ vamos testar'}, gil)
        ideia.refresh_from_db()
        t('o gestor aprova, com retorno, e a autora é avisada', ideia.status == Ideia.Status.APROVADA
          and ideia.resposta_gestor == 'ZZ vamos testar' and avisado(ana, 'Atualização na sua ideia'), feito)
        t('sem revelar a autoria na prévia', 'Ana' not in previa and 'oculta' in previa, previa)
        recusou, saida = recusa('impulso_ideia', {'acao': 'editar', 'ideia_id': pk(ideia), 'motivo': 'ZZ outro'}, ana)
        t('ideia decidida não se edita mais', recusou and 'não pode mais ser editada' in saida, saida)

        print('\n== ADMINISTRAÇÃO ==')
        dia_livre = date(2099, 12, 24)
        while ExcecaoAssiduidade.objects.filter(data=dia_livre).exists():
            dia_livre -= timedelta(days=1)
        recusou, saida = recusa('impulso_administrar', {'acao': 'excecao_assiduidade_adicionar', 'data': f'{dia_livre}',
                                                        'motivo': 'ZZ relógio'}, gil)
        t('gestor não cria exceção de assiduidade', recusou and 'superadmin' in saida, saida)
        _, feito = prepara_e_confirma('impulso_administrar', {'acao': 'excecao_assiduidade_adicionar',
                                                              'data': f'{dia_livre}', 'motivo': 'ZZ relógio fora do ar'},
                                      admin)
        excecao = ExcecaoAssiduidade.objects.filter(data=dia_livre).first()
        t('o superadmin cria a exceção pela view', excecao is not None and excecao.motivo == 'ZZ relógio fora do ar'
          and excecao.criado_por_id == admin.pk, feito)
        saida = roda('impulso_consultar', {'o_que': 'assiduidade', 'mes': f'{dia_livre:%Y-%m}', 'setor_id': loja.pk}, admin)
        t('a exceção aparece na assiduidade do mês', f'exceção #{pk(excecao)} ' in saida, saida)
        recusou, saida = recusa('impulso_administrar', {'acao': 'excecao_assiduidade_adicionar', 'data': f'{dia_livre}',
                                                        'motivo': 'ZZ de novo'}, admin)
        t('o mesmo dia não vira exceção duas vezes', recusou and 'já é uma exceção' in saida, saida)
        prepara_e_confirma('impulso_administrar', {'acao': 'excecao_assiduidade_remover', 'excecao_id': pk(excecao)}, admin)
        t('e desfaz a exceção', not ExcecaoAssiduidade.objects.filter(pk=pk(excecao)).exists())

        print('\n== NADA SAIU DAQUI ==')
        t('o Tangerino não foi chamado', not ajustes.called)

finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
