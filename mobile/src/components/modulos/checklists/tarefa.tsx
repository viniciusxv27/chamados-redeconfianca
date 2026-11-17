/**
 * Uma tarefa do checklist: preencher (dono, enquanto aberto) ou só ver.
 * As regras são as do portal (checklists/views.py, view_execution): tarefa normal
 * marcada como feita precisa de observação OU evidência; Sim/Não e menu suspenso, não.
 * Evidência sobe na hora; o resto vai junto no "Enviar".
 */
import { Image } from 'expo-image';
import { ActivityIndicator, Platform, View } from 'react-native';
import Animated, { FadeIn, FadeOut, LinearTransition } from 'react-native-reanimated';

import { abrirArquivo } from '@/components/modulos/comum';
import { Campo, Cartao, Icone, type NomeIcone, Pressionavel, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { Evidencia, Situacao, Tarefa } from '@/services/modulos/checklists';
import { comAlfa } from '@/utils/formatar';

import { COR_CHECKLIST, Escolha, NOME_SITUACAO, OPCOES_FEITO, OPCOES_SIM_NAO, OPCOES_SITUACAO } from './pecas';

export type Valor = { feita: boolean; resposta: 'yes' | 'no' | null; situacao: Situacao | null; observacao: string };

export type Origem = 'camera' | 'fotos' | 'arquivo';

/** A tarefa está "respondida" (conta no progresso da tela)? */
export function respondida(tarefa: Tarefa, v: Valor) {
  if (tarefa.tipo === 'yes_no') return !!v.resposta;
  if (tarefa.tipo === 'dropdown') return !!v.situacao;
  return v.feita;
}

/** O que falta para o portal aceitar esta tarefa (null = ok). Espelha a validação da web só para avisar antes. */
export function pendencia(tarefa: Tarefa, v: Valor) {
  if (tarefa.obrigatoria && !respondida(tarefa, v)) {
    return tarefa.tipo === 'normal' ? 'Obrigatória: marque como feita.' : 'Obrigatória: escolha uma resposta.';
  }
  if (tarefa.tipo === 'normal' && v.feita && !v.observacao.trim() && !tarefa.evidencias.length) {
    return 'Para marcar como feita, escreva uma observação ou anexe uma evidência.';
  }
  return null;
}

const COR_APROVACAO = { approved: estado.sucesso, rejected: estado.perigo, pending: '#6B7280' } as const;

export function CartaoTarefa({
  tarefa,
  numero,
  valor,
  editavel,
  aviso,
  aoMudar,
  aoAnexar,
  aoExcluir,
  subindo,
  excluindo,
}: {
  tarefa: Tarefa;
  numero: number;
  valor: Valor;
  editavel: boolean;
  aviso?: string | null;
  aoMudar: (parcial: Partial<Valor>) => void;
  aoAnexar: (origem: Origem) => void;
  aoExcluir: (ev: Evidencia) => void;
  subindo: boolean;
  excluindo: number | null;
}) {
  const cores = useCores();
  const ok = respondida(tarefa, valor);
  const corBorda = aviso ? estado.perigo : ok ? comAlfa(estado.sucesso, 0.5) : undefined;

  return (
    <Cartao style={{ padding: 16, gap: 14, ...(corBorda ? { borderColor: corBorda } : null) }}>
      <View className="flex-row items-start gap-3">
        <View
          style={{
            width: 30,
            height: 30,
            borderRadius: 15,
            backgroundColor: ok ? estado.sucesso : comAlfa(COR_CHECKLIST, 0.13),
            alignItems: 'center',
            justifyContent: 'center',
          }}>
          {ok ? (
            <Icone nome="checkmark" tamanho={17} cor="#FFFFFF" />
          ) : (
            <Texto variante="legenda" style={{ color: COR_CHECKLIST, fontFamily: fontes.negrito }}>
              {numero}
            </Texto>
          )}
        </View>
        <View className="flex-1 gap-1.5">
          <Texto variante="corpoForte">{tarefa.titulo}</Texto>
          <View className="flex-row flex-wrap gap-1.5">
            {tarefa.obrigatoria ? <Selo texto="Obrigatória" cor={estado.perigo} ponto={false} /> : <Selo texto="Opcional" cor={cores.texto3} ponto={false} />}
            {tarefa.tipo === 'yes_no' ? <Selo texto="Pergunta" cor={estado.info} ponto={false} /> : null}
            {tarefa.tipo === 'dropdown' ? <Selo texto="Escolha" cor="#8B5CF6" ponto={false} /> : null}
            {tarefa.pontos > 0 ? <Selo texto={`${tarefa.pontos} pts`} cor={estado.alerta} ponto={false} /> : null}
          </View>
        </View>
      </View>

      {tarefa.descricao ? (
        <Texto variante="legenda" tom="suave">
          {tarefa.descricao}
        </Texto>
      ) : null}

      {tarefa.instrucoes.length ? (
        <View className="flex-row flex-wrap gap-2">
          {tarefa.instrucoes.map((ins, i) => (
            <Pressionavel
              key={`${ins.url}-${i}`}
              onPress={() => void abrirArquivo(ins.url)}
              accessibilityLabel={`Abrir instrução: ${ins.titulo}`}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 7, borderRadius: raios.md, backgroundColor: comAlfa(estado.info, 0.1) }}>
              <Icone nome={ins.tipo === 'image' ? 'image-outline' : ins.tipo === 'video' ? 'videocam-outline' : 'book-outline'} tamanho={15} cor={estado.info} />
              <Texto variante="legenda" style={{ color: estado.info, fontFamily: fontes.seminegrito }}>
                {ins.titulo}
              </Texto>
            </Pressionavel>
          ))}
        </View>
      ) : null}

      {editavel ? (
        <Edicao tarefa={tarefa} valor={valor} aoMudar={aoMudar} />
      ) : (
        <Leitura tarefa={tarefa} valor={valor} />
      )}

      <Evidencias tarefa={tarefa} editavel={editavel} aoAnexar={aoAnexar} aoExcluir={aoExcluir} subindo={subindo} excluindo={excluindo} />

      {tarefa.aprovacao.status !== 'pending' ? (
        <View className="gap-1">
          <Selo texto={tarefa.aprovacao.nome} cor={COR_APROVACAO[tarefa.aprovacao.status]} />
          {tarefa.aprovacao.observacao ? (
            <Texto variante="legenda" tom="suave">
              {tarefa.aprovacao.observacao}
            </Texto>
          ) : null}
        </View>
      ) : null}

      {aviso ? (
        <Animated.View entering={FadeIn} exiting={FadeOut} className="flex-row items-center gap-1.5">
          <Icone nome="alert-circle" tamanho={16} cor={estado.perigo} />
          <Texto variante="legenda" tom="perigo" className="flex-1">
            {aviso}
          </Texto>
        </Animated.View>
      ) : null}
    </Cartao>
  );
}

function Rotulo({ children }: { children: string }) {
  return (
    <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
      {children}
    </Texto>
  );
}

function Edicao({ tarefa, valor, aoMudar }: { tarefa: Tarefa; valor: Valor; aoMudar: (p: Partial<Valor>) => void }) {
  return (
    <View className="gap-3">
      {tarefa.tipo === 'normal' ? (
        <Escolha opcoes={OPCOES_FEITO} valor={valor.feita ? 'sim' : 'nao'} aoEscolher={(v) => aoMudar({ feita: v === 'sim' })} />
      ) : null}
      {tarefa.tipo === 'yes_no' ? (
        <Escolha opcoes={OPCOES_SIM_NAO} valor={valor.resposta} aoEscolher={(v) => aoMudar({ resposta: v })} limpavel />
      ) : null}
      {/* Na web, toda tarefa tem o campo "Situação"; na de menu suspenso ele é a própria resposta. */}
      <View className="gap-1.5">
        {tarefa.tipo !== 'dropdown' ? <Rotulo>Situação (opcional)</Rotulo> : null}
        <Escolha opcoes={OPCOES_SITUACAO} valor={valor.situacao} aoEscolher={(v) => aoMudar({ situacao: v })} limpavel />
      </View>
      <Campo
        rotulo="Observação"
        placeholder={tarefa.tipo === 'normal' ? 'O que foi feito (ou anexe uma foto)' : 'Algo a acrescentar?'}
        value={valor.observacao}
        onChangeText={(v) => aoMudar({ observacao: v })}
        multiline
        maxLength={2000}
      />
    </View>
  );
}

function Leitura({ tarefa, valor }: { tarefa: Tarefa; valor: Valor }) {
  const linhas: [string, string][] = [];
  if (tarefa.tipo === 'normal') linhas.push(['Feita', valor.feita ? 'Sim' : 'Não']);
  if (tarefa.tipo === 'yes_no') linhas.push(['Resposta', valor.resposta ? NOME_SITUACAO[valor.resposta] : 'Não respondida']);
  linhas.push([tarefa.tipo === 'dropdown' ? 'Resposta' : 'Situação', valor.situacao ? NOME_SITUACAO[valor.situacao] : '—']);
  return (
    <View className="gap-1.5">
      {linhas.map(([r, v]) => (
        <View key={r} className="flex-row justify-between gap-3">
          <Texto variante="legenda" tom="suave">
            {r}
          </Texto>
          <Texto variante="legenda" style={{ fontFamily: fontes.seminegrito }}>
            {v}
          </Texto>
        </View>
      ))}
      {valor.observacao ? (
        <Texto variante="legenda" className="mt-1">
          “{valor.observacao}”
        </Texto>
      ) : null}
    </View>
  );
}

function Evidencias({
  tarefa,
  editavel,
  aoAnexar,
  aoExcluir,
  subindo,
  excluindo,
}: {
  tarefa: Tarefa;
  editavel: boolean;
  aoAnexar: (origem: Origem) => void;
  aoExcluir: (ev: Evidencia) => void;
  subindo: boolean;
  excluindo: number | null;
}) {
  const cores = useCores();
  if (!editavel && !tarefa.evidencias.length) return null;
  return (
    <View className="gap-2">
      <Rotulo>{`Evidências${tarefa.evidencias.length ? ` · ${tarefa.evidencias.length}` : ''}`}</Rotulo>
      {tarefa.evidencias.length ? (
        <View className="flex-row flex-wrap gap-2">
          {tarefa.evidencias.map((ev, i) => (
            <Animated.View key={ev.id ?? `antiga-${i}`} entering={FadeIn} exiting={FadeOut} layout={LinearTransition}>
              <Pressionavel
                onPress={() => void abrirArquivo(ev.url)}
                accessibilityLabel={`Abrir ${ev.nome}`}
                style={{ width: 76, height: 76, borderRadius: raios.md, overflow: 'hidden', backgroundColor: cores.superficie2, borderWidth: 1, borderColor: cores.borda }}>
                {ev.imagem ? (
                  <Image source={{ uri: ev.url }} style={{ width: '100%', height: '100%' }} contentFit="cover" transition={150} cachePolicy="memory-disk" accessibilityLabel={ev.nome} />
                ) : (
                  <View className="flex-1 items-center justify-center gap-1 px-1">
                    <Icone nome={ev.tipo === 'video' ? 'videocam-outline' : 'document-text-outline'} tamanho={22} cor={marca[500]} />
                    <Texto variante="legenda" tom="fraco" numberOfLines={1} style={{ fontSize: 10 }}>
                      {ev.nome}
                    </Texto>
                  </View>
                )}
                {excluindo === ev.id ? (
                  <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.35)', alignItems: 'center', justifyContent: 'center' }}>
                    <ActivityIndicator color="#FFFFFF" />
                  </View>
                ) : null}
              </Pressionavel>
              {editavel && ev.removivel && ev.id ? (
                <Pressionavel
                  onPress={() => aoExcluir(ev)}
                  hitSlop={8}
                  accessibilityLabel={`Excluir ${ev.nome}`}
                  style={{ position: 'absolute', top: -6, right: -6, width: 24, height: 24, borderRadius: 12, backgroundColor: cores.superficie, alignItems: 'center', justifyContent: 'center' }}>
                  <Icone nome="close-circle" tamanho={22} cor={estado.perigo} />
                </Pressionavel>
              ) : null}
            </Animated.View>
          ))}
        </View>
      ) : null}
      {editavel ? (
        subindo ? (
          <View className="flex-row items-center gap-2 py-2">
            <ActivityIndicator color={marca[500]} />
            <Texto variante="legenda" tom="suave">
              Enviando para o portal…
            </Texto>
          </View>
        ) : (
          <View className="flex-row gap-2">
            {Platform.OS !== 'web' ? <BotaoAnexo icone="camera-outline" rotulo="Câmera" onPress={() => aoAnexar('camera')} /> : null}
            <BotaoAnexo icone="images-outline" rotulo="Fotos" onPress={() => aoAnexar('fotos')} />
            <BotaoAnexo icone="document-attach-outline" rotulo="Arquivo" onPress={() => aoAnexar('arquivo')} />
          </View>
        )
      ) : null}
    </View>
  );
}

function BotaoAnexo({ icone, rotulo, onPress }: { icone: NomeIcone; rotulo: string; onPress: () => void }) {
  const cores = useCores();
  return (
    <Pressionavel
      onPress={onPress}
      vibrar
      escala={0.95}
      accessibilityLabel={`Anexar evidência: ${rotulo}`}
      style={{ flex: 1, height: 56, borderRadius: raios.md, borderWidth: 1.5, borderStyle: 'dashed', borderColor: cores.borda, alignItems: 'center', justifyContent: 'center', gap: 2, backgroundColor: cores.superficie }}>
      <Icone nome={icone} tamanho={19} cor={marca[500]} />
      <Texto variante="legenda" tom="suave" style={{ fontSize: 12 }}>
        {rotulo}
      </Texto>
    </Pressionavel>
  );
}
