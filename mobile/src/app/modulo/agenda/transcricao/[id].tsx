import * as Haptics from 'expo-haptics';
import { type Href, router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Alert, Platform, ScrollView, View } from 'react-native';

import { dataDoIso, duracao, isoDaData } from '@/components/modulos/agenda/datas';
import { CampoData, COR_NIVEL, COR_STATUS_TRANSCRICAO, EscolhaDePessoas, Folha, Interruptor, ROTULO_NIVEL, Secao } from '@/components/modulos/agenda/pecas';
import { EsqueletoBloco } from '@/components/modulos/comum';
import { BarraDetalhe, Botao, BotaoIcone, Campo, Cartao, EstadoErro, Icone, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { estado, marca } from '@/constants/theme';
import { useAtribuirTarefa, useCompartilharTranscricao, useImportarImpulso, useOpcoesAgenda, useTranscricao } from '@/hooks/modulos/agenda';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { DetalheTranscricao, MetaCurta, TarefaTranscricao } from '@/services/modulos/agenda';
import { comAlfa, dataHora } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const COR_TAREFA: Record<TarefaTranscricao['status'], string> = { PENDING: '#6B7280', DOING: estado.info, DONE: estado.sucesso };
const COR_PRIORIDADE: Record<TarefaTranscricao['prioridade'], string> = { LOW: '#6B7280', MEDIUM: estado.alerta, HIGH: estado.perigo, URGENT: estado.perigo };

function sucesso() {
  if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
}

function abrirMeta(meta: MetaCurta) {
  router.push({ pathname: '/modulo/impulso/[id]', params: { id: String(meta.id) } } as unknown as Href);
}

/** Ata da reunião: resumo, tarefas criadas (com responsável e Impulso), itens de ação, decisões, riscos e seções. */
export default function DetalheTranscricaoTela() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const { data, isPending, isError, error, refetch } = useTranscricao(numero);

  return (
    <Tela>
      <BarraDetalhe
        titulo="Ata da reunião"
        direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data.url_web)} /> : undefined}
      />
      {isPending ? (
        <View className="gap-4 p-5">
          <EsqueletoBloco linhas={4} />
          <EsqueletoBloco linhas={5} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <Conteudo t={data} />
      )}
    </Tela>
  );
}

function Conteudo({ t }: { t: DetalheTranscricao }) {
  const cores = useCores();
  const cor = COR_STATUS_TRANSCRICAO[t.status] ?? '#6B7280';
  return (
    <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 40 }}>
      <Cartao style={{ padding: 16, gap: 10 }}>
        <View className="flex-row flex-wrap items-center gap-2">
          <Selo texto={t.status_nome} cor={cor} />
          {t.tipo_detectado ? <Selo texto={t.tipo_detectado} cor="#8B5CF6" ponto={false} /> : null}
          {t.sentimento_nome ? <Selo texto={t.sentimento_nome} cor={t.sentimento === 'negative' ? estado.perigo : t.sentimento === 'positive' ? estado.sucesso : '#6B7280'} ponto={false} /> : null}
        </View>
        <Texto variante="titulo2" selectable>
          {t.titulo}
        </Texto>
        <Texto variante="legenda" tom="fraco">
          {[dataHora(t.criada_em), duracao(t.duracao_segundos), t.sou_dono ? null : `de ${t.dono}`].filter(Boolean).join(' · ')}
        </Texto>
        {t.evento ? (
          <Pressionavel
            onPress={() => t.evento && router.push({ pathname: '/modulo/agenda/[id]', params: { id: String(t.evento.id) } } as unknown as Href)}
            accessibilityLabel={`Abrir o evento ${t.evento.titulo}`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <Icone nome="calendar-outline" tamanho={15} cor={marca[500]} />
            <Texto variante="legenda" tom="marca" numberOfLines={1} className="flex-1">
              {t.evento.titulo}
            </Texto>
          </Pressionavel>
        ) : null}
        {t.participantes_identificados.length ? (
          <Texto variante="legenda" tom="suave">
            {`Participantes: ${t.participantes_identificados.join(', ')}`}
          </Texto>
        ) : null}
        {t.tags.length ? (
          <View className="flex-row flex-wrap gap-1.5">
            {t.tags.map((tag) => (
              <View key={tag} style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: comAlfa(cores.texto3, 0.14) }}>
                <Texto variante="legenda" tom="suave" style={{ fontSize: 12 }}>
                  {`#${tag}`}
                </Texto>
              </View>
            ))}
          </View>
        ) : null}
      </Cartao>

      {t.status === 'processing' || t.status === 'recording' ? <Andamento t={t} /> : null}
      {t.status === 'error' ? (
        <Cartao style={{ padding: 16, gap: 8, borderColor: comAlfa(estado.perigo, 0.4) }}>
          <View className="flex-row items-center gap-2">
            <Icone nome="alert-circle-outline" tamanho={20} cor={estado.perigo} />
            <Texto variante="corpoForte">O processamento falhou</Texto>
          </View>
          {t.erro ? (
            <Texto variante="legenda" tom="suave">
              {t.erro}
            </Texto>
          ) : null}
          <Texto variante="legenda" tom="fraco">
            {t.sou_dono ? 'Para tentar de novo, use o botão de reprocessar no portal.' : 'Quem gravou pode tentar de novo pelo portal.'}
          </Texto>
        </Cartao>
      ) : null}

      {t.resumo ? (
        <Secao icone="document-text-outline" titulo="Resumo">
          <Texto selectable>{t.resumo}</Texto>
        </Secao>
      ) : null}

      {t.lista_tarefas.length ? <TarefasCriadas t={t} /> : null}

      {t.itens_acao.length ? (
        <Secao icone="flash-outline" titulo={`Itens de ação (${t.itens_acao.length})`} cor={estado.sucesso}>
          {t.itens_acao.map((item, i) => (
            <View key={i} className="flex-row gap-2.5">
              <Icone nome="ellipse" tamanho={9} cor={item.prioridade ? COR_NIVEL[item.prioridade] : cores.texto3} style={{ marginTop: 7 }} />
              <View className="flex-1 gap-0.5">
                <Texto selectable>{item.texto}</Texto>
                <Texto variante="legenda" tom="fraco">
                  {[item.responsavel, item.prazo, item.prioridade ? ROTULO_NIVEL[item.prioridade] : null].filter(Boolean).join(' · ')}
                </Texto>
              </View>
            </View>
          ))}
        </Secao>
      ) : null}

      {t.decisoes.length ? (
        <Secao icone="hammer-outline" titulo={`Decisões (${t.decisoes.length})`} cor={estado.alerta}>
          {t.decisoes.map((d, i) => (
            <View key={i} className="gap-0.5">
              <View className="flex-row items-start gap-2">
                {d.impacto ? <Selo texto={ROTULO_NIVEL[d.impacto]} cor={COR_NIVEL[d.impacto]} ponto={false} /> : null}
                <Texto variante="corpoForte" className="flex-1" selectable>
                  {d.texto}
                </Texto>
              </View>
              {d.contexto ? (
                <Texto variante="legenda" tom="fraco">
                  {d.contexto}
                </Texto>
              ) : null}
            </View>
          ))}
        </Secao>
      ) : null}

      {t.riscos.length ? (
        <Secao icone="shield-outline" titulo={`Riscos e bloqueios (${t.riscos.length})`} cor={estado.perigo}>
          {t.riscos.map((r, i) => (
            <View key={i} className="gap-0.5">
              <View className="flex-row items-start gap-2">
                {r.severidade ? <Selo texto={ROTULO_NIVEL[r.severidade]} cor={COR_NIVEL[r.severidade]} ponto={false} /> : null}
                <Texto variante="corpoForte" className="flex-1" selectable>
                  {r.texto}
                </Texto>
              </View>
              {r.mitigacao ? (
                <Texto variante="legenda" tom="suave">
                  {`Como evitar: ${r.mitigacao}`}
                </Texto>
              ) : null}
              {r.responsavel ? (
                <Texto variante="legenda" tom="fraco">
                  {r.responsavel}
                </Texto>
              ) : null}
            </View>
          ))}
        </Secao>
      ) : null}

      {t.secoes.length ? (
        <View className="gap-3">
          <Texto variante="titulo3">Seções da reunião</Texto>
          {t.secoes.map((s, i) => (
            <SecaoDaReuniao key={i} secao={s} />
          ))}
        </View>
      ) : null}

      {t.pode_compartilhar ? <Compartilhar t={t} /> : null}

      <Botao titulo="Ver a transcrição completa no portal" variante="secundario" icone="open-outline" onPress={() => void abrirNoPortal(t.url_web)} />
    </ScrollView>
  );
}

function Andamento({ t }: { t: DetalheTranscricao }) {
  const cores = useCores();
  const gravando = t.status === 'recording';
  return (
    <Cartao style={{ padding: 16, gap: 10 }}>
      <View className="flex-row items-center gap-2">
        <Icone nome={gravando ? 'radio-button-on' : 'sync-outline'} tamanho={18} cor={gravando ? estado.perigo : estado.info} />
        <Texto variante="corpoForte" className="flex-1">
          {gravando ? 'Gravação em andamento' : t.aguardando_nova_tentativa ? 'Vai tentar de novo em instantes' : (t.etapa_rotulo ?? 'Processando')}
        </Texto>
        <Texto variante="legenda" tom="fraco">{`${t.progresso_pct}%`}</Texto>
      </View>
      <View style={{ height: 8, borderRadius: 4, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
        <View style={{ width: `${Math.max(3, t.progresso_pct)}%`, height: '100%', backgroundColor: gravando ? estado.perigo : estado.info }} />
      </View>
      <Texto variante="legenda" tom="fraco">
        A ata aparece aqui quando terminar. A tela se atualiza sozinha.
      </Texto>
    </Cartao>
  );
}

function SecaoDaReuniao({ secao }: { secao: DetalheTranscricao['secoes'][number] }) {
  const cores = useCores();
  const [aberta, setAberta] = useState(false);
  return (
    <Cartao style={{ padding: 16, gap: 8 }}>
      <Pressionavel onPress={() => setAberta((v) => !v)} accessibilityRole="button" accessibilityState={{ expanded: aberta }} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Texto variante="corpoForte" className="flex-1">
          {secao.titulo}
        </Texto>
        {secao.minutos ? (
          <Texto variante="legenda" tom="fraco">
            {`~${secao.minutos} min`}
          </Texto>
        ) : null}
        <Icone nome={aberta ? 'chevron-up' : 'chevron-down'} tamanho={18} cor={cores.texto3} />
      </Pressionavel>
      <Texto tom="suave" numberOfLines={aberta ? undefined : 3} selectable={aberta}>
        {secao.conteudo}
      </Texto>
      {aberta && secao.destaques.length ? (
        <View className="gap-1">
          {secao.destaques.map((d, i) => (
            <Texto key={i} variante="legenda" tom="suave">
              {`★ ${d}`}
            </Texto>
          ))}
        </View>
      ) : null}
      {aberta && secao.topicos.length ? (
        <View className="flex-row flex-wrap gap-1.5">
          {secao.topicos.map((tp) => (
            <View key={tp} style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: comAlfa(cores.texto3, 0.14) }}>
              <Texto variante="legenda" tom="suave" style={{ fontSize: 12 }}>
                {tp}
              </Texto>
            </View>
          ))}
        </View>
      ) : null}
    </Cartao>
  );
}

function TarefasCriadas({ t }: { t: DetalheTranscricao }) {
  const [importando, setImportando] = useState<TarefaTranscricao | null>(null);
  return (
    <Secao icone="clipboard-outline" titulo={`Tarefas criadas (${t.lista_tarefas.length})`} cor={estado.sucesso}>
      {t.lista_tarefas.map((tarefa, i) => (
        <View key={tarefa.id} className="gap-2" style={i ? { paddingTop: 12, borderTopWidth: 1, borderTopColor: 'rgba(127,127,127,0.15)' } : undefined}>
          <CartaoTarefa transcricao={t} tarefa={tarefa} aoImportar={() => setImportando(tarefa)} />
        </View>
      ))}
      {t.impulso ? <ImportarParaImpulso transcricao={t} tarefa={importando} aoFechar={() => setImportando(null)} /> : null}
    </Secao>
  );
}

function CartaoTarefa({ transcricao, tarefa, aoImportar }: { transcricao: DetalheTranscricao; tarefa: TarefaTranscricao; aoImportar: () => void }) {
  const opcoes = useOpcoesAgenda();
  const atribuir = useAtribuirTarefa(transcricao.id);
  const pessoas = (opcoes.data?.pessoas ?? []).map((p) => ({ id: p.id, nome: p.nome, detalhe: p.setor ?? undefined }));
  // A pessoa atual pode ter saído da lista (inativa): continua aparecendo como escolhida.
  if (tarefa.responsavel && !pessoas.some((p) => p.id === tarefa.responsavel?.id)) {
    pessoas.unshift({ id: tarefa.responsavel.id, nome: tarefa.responsavel.nome, detalhe: tarefa.responsavel.setor ?? undefined });
  }
  const imp = tarefa.impulso;
  const primeiro = tarefa.responsavel?.nome.split(' ')[0] ?? '';

  return (
    <>
      <Texto variante="corpoForte" selectable>
        {tarefa.titulo}
      </Texto>
      {tarefa.descricao && tarefa.descricao !== tarefa.titulo ? (
        <Texto variante="legenda" tom="suave" numberOfLines={4}>
          {tarefa.descricao}
        </Texto>
      ) : null}
      <View className="flex-row flex-wrap items-center gap-2">
        <Selo texto={tarefa.status_nome} cor={COR_TAREFA[tarefa.status] ?? '#6B7280'} />
        <Selo texto={tarefa.prioridade_nome} cor={COR_PRIORIDADE[tarefa.prioridade] ?? '#6B7280'} ponto={false} />
        {tarefa.prazo ? (
          <Texto variante="legenda" tom="fraco">
            {`Prazo ${new Date(tarefa.prazo).toLocaleDateString('pt-BR')}`}
          </Texto>
        ) : null}
      </View>
      {transcricao.pode_atribuir ? (
        <Seletor
          rotulo="Responsável"
          placeholder="Escolher responsável"
          itens={pessoas}
          valor={tarefa.responsavel?.id ?? null}
          carregando={opcoes.isPending || atribuir.isPending}
          aoEscolher={(usuario) => {
            if (usuario === tarefa.responsavel?.id) return;
            atribuir.mutate({ tarefa: tarefa.id, usuario }, { onSuccess: sucesso, onError: (e) => Alert.alert('Não deu para atribuir', mensagemDoErro(e)) });
          }}
        />
      ) : null}
      {imp ? (
        <View className="gap-2">
          {imp.meta_do_responsavel ? (
            <LinkMeta texto={`No Impulso de ${primeiro}${imp.meta_do_responsavel.pendente ? ' — aguardando aprovação' : ' — ver a meta'}`} onPress={() => imp.meta_do_responsavel && abrirMeta(imp.meta_do_responsavel)} />
          ) : imp.enviar_para_responsavel_url ? (
            <LinkMeta texto={`Enviar para o Impulso de ${primeiro} (no portal)`} icone="open-outline" onPress={() => imp.enviar_para_responsavel_url && void abrirNoPortal(imp.enviar_para_responsavel_url)} />
          ) : null}
          {imp.minha_meta ? (
            <LinkMeta texto={imp.minha_meta.pendente ? 'No seu Impulso — aguardando o gestor aprovar' : 'No seu Impulso — ver a meta'} onPress={() => imp.minha_meta && abrirMeta(imp.minha_meta)} />
          ) : (
            <Botao titulo="Importar para o meu Impulso" variante="secundario" icone="flash" tamanho="sm" onPress={aoImportar} />
          )}
        </View>
      ) : null}
    </>
  );
}

function LinkMeta({ texto, onPress, icone = 'flash' }: { texto: string; onPress: () => void; icone?: Parameters<typeof Icone>[0]['nome'] }) {
  return (
    <Pressionavel onPress={onPress} accessibilityRole="link" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <Icone nome={icone} tamanho={15} cor={marca[600]} />
      <Texto variante="legenda" tom="marca" className="flex-1">
        {texto}
      </Texto>
    </Pressionavel>
  );
}

/** O formulário "Importar para o meu Impulso" do portal (título, descrição, prazo, gestor e aprovação). */
function ImportarParaImpulso({ transcricao, tarefa, aoFechar }: { transcricao: DetalheTranscricao; tarefa: TarefaTranscricao | null; aoFechar: () => void }) {
  return (
    <Folha aberta={!!tarefa} titulo="Importar para o meu Impulso" aoFechar={aoFechar}>
      {tarefa && transcricao.impulso ? <FormularioImpulso key={tarefa.id} transcricaoId={transcricao.id} impulso={transcricao.impulso} tarefa={tarefa} aoFechar={aoFechar} /> : null}
    </Folha>
  );
}

function FormularioImpulso({
  transcricaoId,
  impulso,
  tarefa,
  aoFechar,
}: {
  transcricaoId: number;
  impulso: NonNullable<DetalheTranscricao['impulso']>;
  tarefa: TarefaTranscricao;
  aoFechar: () => void;
}) {
  const importar = useImportarImpulso(transcricaoId);
  const [titulo, setTitulo] = useState(tarefa.titulo);
  const [descricao, setDescricao] = useState(tarefa.descricao || tarefa.titulo);
  const prazoTarefa = tarefa.prazo ? tarefa.prazo.slice(0, 10) : '';
  const [prazo, setPrazo] = useState(dataDoIso(prazoTarefa && prazoTarefa >= impulso.hoje ? prazoTarefa : ''));
  const [gestor, setGestor] = useState<number | null>(impulso.sou_gestor ? (impulso.gestores.find((g) => g.sou_eu)?.id ?? null) : null);
  const [aprovacao, setAprovacao] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const semGestor = !impulso.sou_gestor && !impulso.gestores.length;

  function enviar() {
    const prazoIso = isoDaData(prazo);
    if (!titulo.trim() || !descricao.trim()) return setErro('Preencha o título e a descrição.');
    if (!prazoIso) return setErro('Informe o prazo (DD/MM/AAAA).');
    if (prazoIso < impulso.hoje) return setErro('O prazo não pode ser anterior a hoje.');
    if (!gestor) return setErro('Escolha o gestor.');
    setErro(null);
    importar.mutate(
      { tarefa: tarefa.id, campos: { titulo: titulo.trim(), descricao: descricao.trim(), prazo: prazoIso, gestor, precisa_aprovacao: impulso.sou_gestor ? false : aprovacao } },
      {
        onSuccess: (r) => {
          sucesso();
          aoFechar();
          Alert.alert(r.aprovada ? 'Meta criada' : 'Pedido enviado', r.mensagem);
        },
        onError: (e) => setErro(mensagemDoErro(e)),
      },
    );
  }

  return (
    <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, gap: 14, paddingBottom: 12 }} keyboardShouldPersistTaps="handled">
      {semGestor ? (
        <Cartao style={{ padding: 12, borderColor: comAlfa(estado.alerta, 0.5) }}>
          <Texto variante="legenda" tom="suave">
            Ainda não há gestor do Impulso no seu setor, então não há para quem enviar a meta. Fale com o RH para ajustar o cadastro.
          </Texto>
        </Cartao>
      ) : null}
      <Campo rotulo="Título" value={titulo} onChangeText={setTitulo} maxLength={200} />
      <Campo rotulo="Descrição" value={descricao} onChangeText={setDescricao} multiline />
      <CampoData rotulo="Prazo" valor={prazo} aoMudar={setPrazo} />
      <Seletor
        rotulo="Gestor"
        placeholder="Escolher o gestor"
        itens={impulso.gestores.map((g) => ({ id: g.id, nome: g.sou_eu ? `${g.nome} (você)` : g.nome }))}
        valor={gestor}
        aoEscolher={setGestor}
        desativado={semGestor}
      />
      {impulso.sou_gestor ? (
        <Texto variante="legenda" tom="fraco">
          Como gestor do Impulso, a meta já entra no seu Kanban.
        </Texto>
      ) : (
        <Interruptor
          rotulo="Pedir a autorização do gestor"
          ajuda={aprovacao ? 'Entra no Kanban quando o gestor aprovar.' : 'É uma atividade sua: entra no Kanban agora.'}
          valor={aprovacao}
          aoMudar={setAprovacao}
        />
      )}
      {erro ? (
        <Texto variante="legenda" tom="perigo">
          {erro}
        </Texto>
      ) : null}
      <Botao titulo="Importar" icone="flash" carregando={importar.isPending} desativado={semGestor} onPress={enviar} />
    </ScrollView>
  );
}

/** Com quem a transcrição é compartilhada (só o dono muda; quem entra na lista recebe aviso). */
function Compartilhar({ t }: { t: DetalheTranscricao }) {
  const opcoes = useOpcoesAgenda();
  const compartilhar = useCompartilharTranscricao(t.id);
  const atuais = (t.compartilhada_com ?? []).map((p) => p.id);
  const [escolhidos, setEscolhidos] = useState<number[]>(atuais);
  const mudou = escolhidos.length !== atuais.length || escolhidos.some((id) => !atuais.includes(id));
  const pessoas = (opcoes.data?.pessoas ?? []).filter((p) => p.id !== opcoes.data?.eu);

  return (
    <Secao icone="share-social-outline" titulo="Compartilhar" cor={estado.info}>
      <Texto variante="legenda" tom="suave">
        Quem estiver na lista vê esta transcrição no portal e no app.
      </Texto>
      <EscolhaDePessoas pessoas={pessoas} grupos={opcoes.data?.grupos ?? []} escolhidos={escolhidos} aoMudar={setEscolhidos} rotulo="Compartilhada com" acao="Escolher pessoas" />
      {mudou ? (
        <Botao
          titulo="Salvar compartilhamento"
          icone="checkmark"
          carregando={compartilhar.isPending}
          onPress={() =>
            compartilhar.mutate(escolhidos, {
              onSuccess: (r) => (sucesso(), Alert.alert('Pronto', r.mensagem)),
              onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)),
            })
          }
        />
      ) : null}
    </Secao>
  );
}
