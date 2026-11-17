import * as Haptics from 'expo-haptics';
import { router, useLocalSearchParams, type Href } from 'expo-router';
import { useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { abrirArquivo, EsqueletoBloco } from '@/components/modulos/comum';
import { Alternar, Folha } from '@/components/modulos/ponto/folha';
import { CartaoSolicitacao, COR_SOLICITACAO, dataCurta, Faixa, ICONE_TIPO, SeloSolicitacao } from '@/components/modulos/ponto/pecas';
import { Avatar, BarraDetalhe, Botao, BotaoIcone, Campo, Cartao, Entrada, EstadoErro, Icone, type NomeIcone, Pressionavel, Tela, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useAcaoSolicitacao, useSolicitacao } from '@/hooks/modulos/ponto';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { AcaoSolicitacao, AnexoSolicitacao, EventoSolicitacao, Solicitacao } from '@/services/modulos/ponto';
import { comAlfa, dataHora, tamanhoArquivo, tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

type FolhaAberta = null | 'aprovar' | 'devolver' | 'cancelar';

export default function DetalheSolicitacao() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const { data, isPending, isError, error, refetch } = useSolicitacao(numero);
  const acao = useAcaoSolicitacao(numero);
  const insets = useSafeAreaInsets();
  const cores = useCores();
  const [folha, setFolha] = useState<FolhaAberta>(null);

  function executar(a: AcaoSolicitacao, aoTerminar?: () => void) {
    acao.mutate(a, {
      onSuccess: () => {
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        aoTerminar?.();
      },
      onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)),
    });
  }

  return (
    <Tela>
      <BarraDetalhe
        titulo={`Solicitação #${id}`}
        direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data.url_web)} /> : undefined}
      />
      {isPending ? (
        <View className="gap-4 p-5">
          <EsqueletoBloco linhas={5} />
          <EsqueletoBloco linhas={3} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 24 }} keyboardDismissMode="interactive">
            <Resumo sol={data} />
            {data.tipo === 'AJUSTE' ? <Horarios sol={data} /> : null}
            <Secao icone="chatbox-ellipses-outline" titulo="Justificativa">
              <Texto selectable>{data.justificativa}</Texto>
            </Secao>
            {data.resposta ? (
              <Secao icone="person-circle-outline" titulo="Resposta do DP" cor={COR_SOLICITACAO[data.status]}>
                <Texto selectable>{data.resposta}</Texto>
              </Secao>
            ) : null}
            {data.anexos_lista.length || data.anexos_removidos.length ? <Anexos sol={data} /> : null}
            <Historico eventos={data.eventos} />
            {data.dp && data.outras.length ? (
              <View className="gap-3">
                <Texto variante="titulo3">Outras desta pessoa</Texto>
                {data.outras.map((o) => (
                  <CartaoSolicitacao key={o.id} sol={o} />
                ))}
              </View>
            ) : null}
          </ScrollView>

          <Acoes sol={data} ocupado={acao.isPending} aoAbrir={setFolha} aoExecutar={executar} />
          {data.pode_comentar ? <Comentario id={data.id} /> : <View style={{ height: insets.bottom, backgroundColor: cores.superficie }} />}

          <FolhaAprovar sol={data} aberta={folha === 'aprovar'} ocupado={acao.isPending} aoFechar={() => setFolha(null)} aoEnviar={(a) => executar(a, () => setFolha(null))} />
          <FolhaTexto
            aberta={folha === 'devolver'}
            titulo="Devolver para correção"
            ajuda="Diga ao colaborador o que corrigir. Ele recebe o aviso no portal."
            botao="Devolver"
            minimo={5}
            ocupado={acao.isPending}
            aoFechar={() => setFolha(null)}
            aoEnviar={(texto) => executar({ acao: 'devolver', texto }, () => setFolha(null))}
          />
          <FolhaTexto
            aberta={folha === 'cancelar'}
            titulo="Cancelar a solicitação?"
            ajuda="O DP deixa de analisar. Se quiser, diga o motivo."
            botao="Cancelar solicitação"
            perigo
            ocupado={acao.isPending}
            aoFechar={() => setFolha(null)}
            aoEnviar={(texto) => executar({ acao: 'cancelar', texto }, () => setFolha(null))}
          />
        </KeyboardAvoidingView>
      )}
    </Tela>
  );
}

function Resumo({ sol }: { sol: Solicitacao }) {
  const cor = COR_SOLICITACAO[sol.status];
  const mostrarPessoa = sol.dp && !sol.dono;
  return (
    <Entrada indice={0}>
      <Cartao style={{ padding: 18, gap: 14 }}>
        <View className="flex-row items-center gap-3">
          <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: comAlfa(cor, 0.14), alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome={ICONE_TIPO[sol.tipo]} tamanho={22} cor={cor} />
          </View>
          <View className="flex-1 gap-1">
            <Texto variante="rotulo" tom="fraco">
              {sol.tipo_nome}
            </Texto>
            <SeloSolicitacao status={sol.status} nome={sol.status_nome} />
          </View>
        </View>
        <Texto variante="titulo3" selectable>
          {sol.titulo}
        </Texto>
        <View className="gap-3">
          {mostrarPessoa && sol.colaborador ? (
            <View className="flex-row items-center gap-3">
              <Avatar nome={sol.colaborador.nome} foto={sol.colaborador.foto} tamanho={28} />
              <View className="flex-1">
                <Texto variante="corpoForte">{sol.colaborador.nome}</Texto>
                {sol.colaborador.setor ? (
                  <Texto variante="legenda" tom="fraco">
                    {sol.colaborador.setor}
                  </Texto>
                ) : null}
              </View>
            </View>
          ) : null}
          <Linha icone="calendar-outline" rotulo={sol.tipo === 'AJUSTE' ? 'Dia' : 'Período'} valor={sol.tipo === 'AJUSTE' ? dataCurta(sol.data, true) : sol.periodo} />
          {sol.especie_nome ? <Linha icone="pricetag-outline" rotulo="Documento" valor={sol.especie_nome} /> : null}
          {sol.motivo_nome ? <Linha icone="help-circle-outline" rotulo="O que houve" valor={sol.motivo_nome} /> : null}
          {sol.tipo !== 'AJUSTE' && sol.dias > 1 ? <Linha icone="hourglass-outline" rotulo="Dias" valor={String(sol.dias)} /> : null}
          <Linha icone="paper-plane-outline" rotulo="Enviada" valor={dataHora(sol.enviada_em)} />
          {sol.responsavel ? <Linha icone="person-outline" rotulo="Quem analisa" valor={sol.responsavel.nome} /> : null}
          {sol.tipo === 'AJUSTE' && sol.status === 'APROVADA' ? (
            <Linha
              icone={sol.ajustado_no_ponto ? 'checkmark-done-outline' : 'time-outline'}
              rotulo="No ponto"
              valor={sol.ajustado_no_ponto ? `Lançado ${dataHora(sol.ajustado_em)}` : 'O DP ainda vai lançar'}
              cor={sol.ajustado_no_ponto ? estado.sucesso : estado.alerta}
            />
          ) : null}
        </View>
      </Cartao>
    </Entrada>
  );
}

function Linha({ icone, rotulo, valor, cor }: { icone: NomeIcone; rotulo: string; valor: string; cor?: string }) {
  const cores = useCores();
  return (
    <View className="flex-row items-start gap-3">
      <Icone nome={icone} tamanho={18} cor={cor ?? cores.texto3} style={{ marginTop: 1 }} />
      <Texto variante="legenda" tom="fraco" style={{ width: 96 }}>
        {rotulo}
      </Texto>
      <Texto variante="legenda" className="flex-1" style={{ fontFamily: fontes.seminegrito, color: cor ?? cores.texto }}>
        {valor}
      </Texto>
    </View>
  );
}

function Secao({ icone, titulo, cor, children }: { icone: NomeIcone; titulo: string; cor?: string; children: React.ReactNode }) {
  const cores = useCores();
  return (
    <Entrada indice={1}>
      <Cartao style={{ padding: 18, gap: 10, borderColor: cor ? comAlfa(cor, 0.35) : undefined }}>
        <View className="flex-row items-center gap-2">
          <Icone nome={icone} tamanho={18} cor={cor ?? cores.texto2} />
          <Texto variante="rotulo" tom="suave">
            {titulo}
          </Texto>
        </View>
        {children}
      </Cartao>
    </Entrada>
  );
}

function Horarios({ sol }: { sol: Solicitacao }) {
  return (
    <Secao icone="time-outline" titulo="Horários certos do dia">
      {sol.marcacoes.map((m) => (
        <View key={m.rotulo} className="flex-row items-center justify-between">
          <Texto variante="legenda" tom="suave">
            {m.rotulo}
          </Texto>
          <Texto variante="corpoForte" style={{ fontVariant: ['tabular-nums'] }}>
            {m.hora}
          </Texto>
        </View>
      ))}
      {sol.registrado ? (
        <Texto variante="legenda" tom="fraco">
          O relógio registrou: {sol.registrado.length ? sol.registrado.join(' · ') : 'nenhuma batida'}
        </Texto>
      ) : null}
    </Secao>
  );
}

function Anexos({ sol }: { sol: Solicitacao }) {
  const [verRemovidos, setVerRemovidos] = useState(false);
  return (
    <Secao icone="attach" titulo={`Documentos (${sol.anexos_lista.length})`}>
      <View className="gap-2">
        {sol.anexos_lista.map((a) => (
          <LinhaAnexo key={a.id} anexo={a} />
        ))}
        {sol.anexos_removidos.length ? (
          verRemovidos ? (
            sol.anexos_removidos.map((a) => <LinhaAnexo key={a.id} anexo={a} />)
          ) : (
            <Botao titulo={`Ver ${sol.anexos_removidos.length} retirado(s) na correção`} variante="fantasma" tamanho="sm" larguraTotal={false} onPress={() => setVerRemovidos(true)} />
          )
        ) : null}
      </View>
    </Secao>
  );
}

function LinhaAnexo({ anexo }: { anexo: AnexoSolicitacao }) {
  const cores = useCores();
  return (
    <Pressionavel onPress={() => void abrirArquivo(anexo.url)} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, opacity: anexo.removido ? 0.6 : 1 }} accessibilityLabel={`Abrir ${anexo.nome}`}>
      <View style={{ width: 40, height: 40, borderRadius: 10, backgroundColor: comAlfa(marca[500], 0.12), alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome={anexo.imagem ? 'image-outline' : anexo.pdf ? 'document-text-outline' : 'document-outline'} tamanho={20} cor={marca[500]} />
      </View>
      <View className="flex-1">
        <Texto variante="corpoForte" numberOfLines={1} style={anexo.removido ? { textDecorationLine: 'line-through' } : undefined}>
          {anexo.nome}
        </Texto>
        <Texto variante="legenda" tom="fraco">
          {tamanhoArquivo(anexo.tamanho)} · {tempoRelativo(anexo.enviado_em)}
        </Texto>
      </View>
      <Icone nome="eye-outline" tamanho={18} cor={cores.texto3} />
    </Pressionavel>
  );
}

const ICONE_EVENTO: Record<EventoSolicitacao['acao'], NomeIcone> = {
  CRIADA: 'paper-plane-outline',
  REENVIADA: 'refresh-outline',
  EM_ANALISE: 'search-outline',
  APROVADA: 'checkmark-circle-outline',
  DEVOLVIDA: 'arrow-undo-outline',
  CANCELADA: 'close-circle-outline',
  COMENTARIO: 'chatbubble-outline',
  AJUSTADA: 'checkmark-done-outline',
};

function Historico({ eventos }: { eventos: EventoSolicitacao[] }) {
  const cores = useCores();
  return (
    <View className="gap-3">
      <Texto variante="titulo3">Histórico</Texto>
      {eventos.map((e, i) => (
        <Entrada key={e.id} indice={i < 6 ? i : 0}>
          <View className="flex-row gap-3">
            <View className="items-center">
              <View style={{ width: 30, height: 30, borderRadius: 15, backgroundColor: cores.superficie2, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: cores.borda }}>
                <Icone nome={ICONE_EVENTO[e.acao] ?? 'ellipse-outline'} tamanho={15} cor={cores.texto2} />
              </View>
              {i < eventos.length - 1 ? <View style={{ flex: 1, width: 2, backgroundColor: cores.borda, marginTop: 4 }} /> : null}
            </View>
            <View className="flex-1 gap-1 pb-2">
              <Texto variante="legenda">
                <Texto variante="legenda" style={{ fontFamily: fontes.negrito }}>
                  {e.autor?.primeiro_nome ?? 'Sistema'}
                </Texto>{' '}
                {e.acao_nome.toLowerCase()}
              </Texto>
              <Texto variante="legenda" tom="fraco">
                {dataHora(e.em)}
              </Texto>
              {e.texto ? (
                <Cartao style={{ padding: 12, marginTop: 4 }}>
                  <Texto variante="legenda" selectable>
                    {e.texto}
                  </Texto>
                </Cartao>
              ) : null}
              {e.alteracoes.map((a, j) => (
                <Texto key={j} variante="legenda" tom="suave">
                  {a.campo}: {a.antes} → {a.depois}
                </Texto>
              ))}
            </View>
          </View>
        </Entrada>
      ))}
    </View>
  );
}

/** Botões do rodapé: os mesmos do portal para aquela pessoa (dono ou quem analisa). */
function Acoes({ sol, ocupado, aoAbrir, aoExecutar }: { sol: Solicitacao; ocupado: boolean; aoAbrir: (f: FolhaAberta) => void; aoExecutar: (a: AcaoSolicitacao) => void }) {
  const cores = useCores();
  const botoes: React.ReactNode[] = [];
  if (sol.pode_corrigir) {
    botoes.push(
      <Botao
        key="corrigir"
        titulo={sol.status === 'DEVOLVIDA' ? 'Corrigir e reenviar' : 'Editar'}
        icone="create-outline"
        tamanho="md"
        onPress={() => router.push({ pathname: '/modulo/ponto/nova', params: { corrigir: String(sol.id) } } as unknown as Href)}
      />,
    );
  }
  if (sol.pode_assumir) botoes.push(<Botao key="assumir" titulo="Assumir a análise" icone="hand-left-outline" variante="secundario" tamanho="md" carregando={ocupado} onPress={() => aoExecutar({ acao: 'assumir' })} />);
  if (sol.pode_analisar) {
    botoes.push(
      <View key="decidir" className="flex-row gap-2">
        <View className="flex-1">
          <Botao titulo="Aprovar" icone="checkmark" tamanho="md" onPress={() => aoAbrir('aprovar')} />
        </View>
        <View className="flex-1">
          <Botao titulo="Devolver" icone="arrow-undo" variante="secundario" tamanho="md" onPress={() => aoAbrir('devolver')} />
        </View>
      </View>,
    );
  }
  if (sol.pode_lancar) {
    botoes.push(
      <Botao
        key="lancar"
        titulo="Marcar ajuste lançado no ponto"
        icone="checkmark-done"
        variante="secundario"
        tamanho="md"
        carregando={ocupado}
        onPress={() =>
          Alert.alert('Ajuste lançado?', 'Confirme que o ajuste já foi feito no ponto da Sólides.', [
            { text: 'Voltar', style: 'cancel' },
            { text: 'Já lancei', onPress: () => aoExecutar({ acao: 'lancar' }) },
          ])
        }
      />,
    );
  }
  if (sol.pode_cancelar) botoes.push(<Botao key="cancelar" titulo="Cancelar solicitação" icone="close" variante="fantasma" tamanho="sm" onPress={() => aoAbrir('cancelar')} />);
  if (!botoes.length) return null;
  return <View style={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: 8, gap: 8, borderTopWidth: 1, borderTopColor: cores.borda, backgroundColor: cores.superficie }}>{botoes}</View>;
}

function Comentario({ id }: { id: number }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const acao = useAcaoSolicitacao(id);
  const [texto, setTexto] = useState('');
  const pronto = texto.trim().length >= 2 && !acao.isPending;
  function enviar() {
    if (!pronto) return;
    acao.mutate(
      { acao: 'comentar', texto: texto.trim() },
      { onSuccess: () => setTexto(''), onError: (e) => Alert.alert('Comentário não enviado', mensagemDoErro(e)) },
    );
  }
  return (
    <View className="flex-row items-end gap-2" style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: Math.max(insets.bottom, 12), backgroundColor: cores.superficie, borderTopWidth: 1, borderTopColor: cores.borda }}>
      <TextInput
        value={texto}
        onChangeText={setTexto}
        placeholder="Escreva um comentário"
        placeholderTextColor={cores.texto3}
        multiline
        maxLength={2000}
        style={{ flex: 1, minHeight: 42, maxHeight: 120, borderRadius: 21, paddingHorizontal: 16, paddingTop: 11, paddingBottom: 11, backgroundColor: cores.superficie2, color: cores.texto, fontFamily: fontes.regular, fontSize: 15 }}
        accessibilityLabel="Comentário"
      />
      <BotaoIcone icone="send" rotulo="Enviar comentário" onPress={enviar} cor={pronto ? '#FFFFFF' : cores.texto3} fundo={pronto ? marca[500] : cores.superficie2} />
    </View>
  );
}

function FolhaAprovar({ sol, aberta, ocupado, aoFechar, aoEnviar }: { sol: Solicitacao; aberta: boolean; ocupado: boolean; aoFechar: () => void; aoEnviar: (a: AcaoSolicitacao) => void }) {
  const [texto, setTexto] = useState('');
  const [arquivar, setArquivar] = useState(true);
  const [ajustado, setAjustado] = useState(false);
  return (
    <Folha aberta={aberta} titulo="Aprovar a solicitação" aoFechar={aoFechar}>
      <Campo rotulo="Mensagem ao colaborador (opcional)" value={texto} onChangeText={setTexto} multiline maxLength={2000} placeholder="Ex.: Ajuste aprovado." />
      {sol.tipo === 'AJUSTE' ? (
        <Alternar rotulo="Já lancei o ajuste no ponto" valor={ajustado} aoMudar={setAjustado} />
      ) : (
        <Alternar rotulo={`Guardar nos documentos do colaborador (${sol.tipo === 'ATESTADO' ? 'Atestados' : 'Declarações'})`} valor={arquivar} aoMudar={setArquivar} />
      )}
      <Botao titulo="Aprovar" icone="checkmark" carregando={ocupado} onPress={() => aoEnviar({ acao: 'aprovar', texto: texto.trim(), arquivar, ajustado })} />
    </Folha>
  );
}

function FolhaTexto({
  aberta,
  titulo,
  ajuda,
  botao,
  minimo = 0,
  perigo,
  ocupado,
  aoFechar,
  aoEnviar,
}: {
  aberta: boolean;
  titulo: string;
  ajuda: string;
  botao: string;
  minimo?: number;
  perigo?: boolean;
  ocupado: boolean;
  aoFechar: () => void;
  aoEnviar: (texto: string) => void;
}) {
  const [texto, setTexto] = useState('');
  const curto = texto.trim().length < minimo;
  return (
    <Folha aberta={aberta} titulo={titulo} aoFechar={aoFechar}>
      <Faixa icone="information-circle-outline" texto={ajuda} cor={perigo ? estado.perigo : estado.info} />
      <Campo value={texto} onChangeText={setTexto} multiline maxLength={2000} placeholder={minimo ? 'Escreva aqui' : 'Motivo (opcional)'} />
      <Botao titulo={botao} variante={perigo ? 'perigo' : 'primario'} desativado={curto} carregando={ocupado} onPress={() => aoEnviar(texto.trim())} />
    </Folha>
  );
}
