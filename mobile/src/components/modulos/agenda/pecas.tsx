/** Peças da Agenda: cartão de evento, campos de data/hora, folha de baixo e escolha de pessoas. */
import type { ReactNode } from 'react';
import { useMemo, useState } from 'react';
import { FlatList, KeyboardAvoidingView, Modal, Platform, Pressable, Switch, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Avatar, Campo, Cartao, Chip, Icone, type NomeIcone, Pressionavel, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { Evento, Nivel, PessoaAgenda, StatusTranscricao, TipoEvento } from '@/services/modulos/agenda';
import { comAlfa } from '@/utils/formatar';

import { dataDoIso, faixaDeHorario, isoDaData, isoDoDia, mascaraData, mascaraHora, somarDias } from './datas';

export const ICONE_TIPO: Record<TipoEvento, NomeIcone> = {
  event: 'calendar-outline',
  meeting: 'people-outline',
  call: 'videocam-outline',
  task: 'checkbox-outline',
  reminder: 'notifications-outline',
  block: 'remove-circle-outline',
};

export const COR_STATUS_TRANSCRICAO: Record<StatusTranscricao, string> = {
  recording: estado.perigo,
  processing: estado.info,
  completed: estado.sucesso,
  error: estado.perigo,
};

export const COR_NIVEL: Record<Exclude<Nivel, null>, string> = { high: estado.perigo, medium: estado.alerta, low: '#6B7280' };
export const ROTULO_NIVEL: Record<Exclude<Nivel, null>, string> = { high: 'Alta', medium: 'Média', low: 'Baixa' };

/** Compromisso na lista do dia: faixa na cor do evento, horário, título, tipo e local. */
export function CartaoEvento({ ev, onPress, agora }: { ev: Evento; onPress: () => void; agora?: number }) {
  const cores = useCores();
  const acontecendo = agora != null && new Date(ev.inicio).getTime() <= agora && new Date(ev.fim).getTime() > agora;
  const passou = agora != null && new Date(ev.fim).getTime() <= agora;
  return (
    <Cartao onPress={onPress} acessibilidade={`${ev.titulo}, ${faixaDeHorario(ev.inicio, ev.fim, ev.dia_inteiro)}`} style={{ flexDirection: 'row', overflow: 'hidden', opacity: passou ? 0.62 : 1 }}>
      <View style={{ width: 5, backgroundColor: ev.cor }} />
      <View className="flex-1 gap-1 p-4">
        <View className="flex-row items-center gap-2">
          <Icone nome={ICONE_TIPO[ev.tipo] ?? 'calendar-outline'} tamanho={15} cor={ev.cor} />
          <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
            {faixaDeHorario(ev.inicio, ev.fim, ev.dia_inteiro)}
          </Texto>
          {acontecendo ? <Selo texto="Agora" cor={estado.sucesso} /> : null}
        </View>
        <Texto variante="corpoForte" numberOfLines={2}>
          {ev.titulo}
        </Texto>
        <View className="flex-row flex-wrap items-center gap-x-3 gap-y-1">
          <Texto variante="legenda" tom="fraco">
            {ev.tipo_nome}
          </Texto>
          {ev.local ? (
            <View className="flex-row items-center gap-1" style={{ maxWidth: '100%' }}>
              <Icone nome="location-outline" tamanho={13} cor={cores.texto3} />
              <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                {ev.local}
              </Texto>
            </View>
          ) : null}
          {ev.link ? <Icone nome="link-outline" tamanho={14} cor={cores.texto3} /> : null}
          {ev.recorrente ? <Icone nome="repeat" tamanho={14} cor={cores.texto3} /> : null}
          {!ev.sou_dono && ev.dono ? (
            <Texto variante="legenda" tom="fraco" numberOfLines={1}>
              {`de ${ev.dono.split(' ')[0]}`}
            </Texto>
          ) : null}
        </View>
      </View>
    </Cartao>
  );
}

/** Data digitada com máscara + atalhos (Hoje, Amanhã). */
export function CampoData({ rotulo, valor, aoMudar, erro, atalhos = true }: { rotulo: string; valor: string; aoMudar: (v: string) => void; erro?: string | null; atalhos?: boolean }) {
  const hoje = isoDoDia(new Date());
  const rapidos = [
    { rotulo: 'Hoje', iso: hoje },
    { rotulo: 'Amanhã', iso: somarDias(hoje, 1) },
  ];
  return (
    <View className="gap-2">
      <Campo
        rotulo={rotulo}
        icone="calendar-outline"
        placeholder="DD/MM/AAAA"
        keyboardType="number-pad"
        maxLength={10}
        value={valor}
        onChangeText={(v) => aoMudar(mascaraData(v))}
        erro={erro}
      />
      {atalhos ? (
        <View className="flex-row flex-wrap gap-2">
          {rapidos.map((r) => (
            <Chip key={r.rotulo} rotulo={r.rotulo} ativo={isoDaData(valor) === r.iso} onPress={() => aoMudar(dataDoIso(r.iso))} />
          ))}
        </View>
      ) : null}
    </View>
  );
}

export function CampoHora({ rotulo, valor, aoMudar, erro }: { rotulo: string; valor: string; aoMudar: (v: string) => void; erro?: string | null }) {
  return (
    <Campo
      rotulo={rotulo}
      icone="time-outline"
      placeholder="00:00"
      keyboardType="number-pad"
      maxLength={5}
      value={valor}
      onChangeText={(v) => aoMudar(mascaraHora(v))}
      erro={erro}
    />
  );
}

/** Linha com interruptor (dia inteiro, privado, repetir). */
export function Interruptor({ rotulo, ajuda, valor, aoMudar, desativado }: { rotulo: string; ajuda?: string; valor: boolean; aoMudar: (v: boolean) => void; desativado?: boolean }) {
  const cores = useCores();
  return (
    <View className="flex-row items-center gap-3" style={{ opacity: desativado ? 0.5 : 1 }}>
      <View className="flex-1">
        <Texto variante="corpoForte">{rotulo}</Texto>
        {ajuda ? (
          <Texto variante="legenda" tom="fraco">
            {ajuda}
          </Texto>
        ) : null}
      </View>
      <Switch
        value={valor}
        onValueChange={aoMudar}
        disabled={desativado}
        trackColor={{ true: marca[500], false: cores.superficie3 }}
        thumbColor="#FFFFFF"
        accessibilityLabel={rotulo}
      />
    </View>
  );
}

/** Folha que sobe de baixo (formulários curtos: importar para o Impulso, observação do convite). */
export function Folha({ aberta, titulo, aoFechar, children }: { aberta: boolean; titulo: string; aoFechar: () => void; children: ReactNode }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={aberta} transparent animationType="slide" onRequestClose={aoFechar} statusBarTranslucent>
      <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)' }} onPress={aoFechar} accessibilityLabel="Fechar" />
        <View
          style={{
            maxHeight: '88%',
            backgroundColor: cores.fundo,
            borderTopLeftRadius: raios['2xl'],
            borderTopRightRadius: raios['2xl'],
            paddingBottom: insets.bottom + 12,
          }}>
          <View style={{ alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: cores.borda, marginTop: 10 }} />
          <Texto variante="titulo3" style={{ paddingHorizontal: 20, paddingTop: 14, paddingBottom: 6 }}>
            {titulo}
          </Texto>
          {children}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function semAcento(t: string) {
  return t
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

/** Convidados: chips com quem já está + folha com busca (várias pessoas) e os grupos do portal. */
export function EscolhaDePessoas({
  pessoas,
  grupos,
  escolhidos,
  aoMudar,
  rotulo = 'Convidados',
  acao = 'Convidar pessoas',
}: {
  pessoas: PessoaAgenda[];
  grupos: { id: number; nome: string; membros: number[] }[];
  escolhidos: number[];
  aoMudar: (ids: number[]) => void;
  /** Título do campo e da folha (padrão: "Convidados"). */
  rotulo?: string;
  /** Texto do botão quando ninguém foi escolhido (padrão: "Convidar pessoas"). */
  acao?: string;
}) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const [aberta, setAberta] = useState(false);
  const [busca, setBusca] = useState('');
  const porId = useMemo(() => new Map(pessoas.map((p) => [p.id, p])), [pessoas]);
  const filtradas = useMemo(() => {
    const termo = semAcento(busca.trim());
    if (!termo) return pessoas;
    return pessoas.filter((p) => semAcento(`${p.nome} ${p.setor ?? ''}`).includes(termo));
  }, [busca, pessoas]);
  const marcados = new Set(escolhidos);

  function alternar(id: number) {
    aoMudar(marcados.has(id) ? escolhidos.filter((x) => x !== id) : [...escolhidos, id]);
  }

  return (
    <View className="gap-2">
      <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
        {escolhidos.length ? `${rotulo} (${escolhidos.length})` : rotulo}
      </Texto>
      {escolhidos.length ? (
        <View className="flex-row flex-wrap gap-2">
          {escolhidos.map((id) => {
            const p = porId.get(id);
            return (
              <Pressionavel
                key={id}
                onPress={() => alternar(id)}
                accessibilityLabel={`Tirar ${p?.nome ?? 'pessoa'} do convite`}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingLeft: 4, paddingRight: 10, height: 32, borderRadius: raios.pilula, backgroundColor: comAlfa(marca[500], 0.12) }}>
                <Avatar nome={p?.nome ?? '?'} tamanho={24} />
                <Texto variante="legenda" numberOfLines={1} style={{ maxWidth: 160, color: marca[600], fontFamily: fontes.seminegrito }}>
                  {p?.nome ?? `#${id}`}
                </Texto>
                <Icone nome="close" tamanho={14} cor={marca[600]} />
              </Pressionavel>
            );
          })}
        </View>
      ) : null}
      <Pressionavel
        onPress={() => setAberta(true)}
        escala={0.98}
        accessibilityLabel={`Escolher ${rotulo.toLowerCase()}`}
        style={{ minHeight: 48, borderRadius: raios.md, borderWidth: 1.5, borderStyle: 'dashed', borderColor: cores.borda, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 8 }}>
        <Icone nome="person-add-outline" tamanho={18} cor={marca[500]} />
        <Texto variante="corpoForte" tom="marca">
          {escolhidos.length ? `Mudar ${rotulo.toLowerCase()}` : acao}
        </Texto>
      </Pressionavel>

      <Modal visible={aberta} transparent animationType="slide" onRequestClose={() => setAberta(false)} statusBarTranslucent>
        <View style={{ flex: 1, justifyContent: 'flex-end' }}>
          <Pressable style={{ ...({ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 } as const), backgroundColor: 'rgba(0,0,0,0.45)' }} onPress={() => setAberta(false)} accessibilityLabel="Fechar" />
          <View style={{ height: '85%', backgroundColor: cores.fundo, borderTopLeftRadius: raios['2xl'], borderTopRightRadius: raios['2xl'], paddingBottom: insets.bottom + 8 }}>
            <View style={{ alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: cores.borda, marginTop: 10 }} />
            <View className="flex-row items-center justify-between px-5 pb-2 pt-4">
              <Texto variante="titulo3">{rotulo}</Texto>
              <Pressionavel onPress={() => setAberta(false)} hitSlop={10} accessibilityLabel="Pronto">
                <Texto variante="corpoForte" tom="marca">
                  Pronto
                </Texto>
              </Pressionavel>
            </View>
            <View className="gap-3 px-5 pb-2">
              <Campo icone="search" placeholder="Buscar pessoa ou setor…" value={busca} onChangeText={setBusca} autoCorrect={false} />
              {grupos.length && !busca ? (
                <View className="flex-row flex-wrap gap-2">
                  {grupos.map((g) => {
                    const todos = g.membros.every((id) => marcados.has(id));
                    return (
                      <Chip
                        key={g.id}
                        rotulo={`${g.nome} (${g.membros.length})`}
                        ativo={todos}
                        onPress={() =>
                          aoMudar(todos ? escolhidos.filter((id) => !g.membros.includes(id)) : [...new Set([...escolhidos, ...g.membros.filter((id) => porId.has(id))])])
                        }
                      />
                    );
                  })}
                </View>
              ) : null}
            </View>
            <FlatList
              data={filtradas}
              keyExtractor={(p) => String(p.id)}
              keyboardShouldPersistTaps="handled"
              initialNumToRender={20}
              contentContainerStyle={{ paddingHorizontal: 12, paddingBottom: 12 }}
              renderItem={({ item }) => {
                const ativo = marcados.has(item.id);
                return (
                  <Pressionavel
                    onPress={() => alternar(item.id)}
                    vibrar
                    escala={0.98}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: ativo }}
                    style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 12, paddingVertical: 10, borderRadius: raios.md, backgroundColor: ativo ? comAlfa(marca[500], 0.1) : 'transparent' }}>
                    <Avatar nome={item.nome} tamanho={34} />
                    <View className="flex-1">
                      <Texto variante={ativo ? 'corpoForte' : 'corpo'} numberOfLines={1}>
                        {item.nome}
                      </Texto>
                      {item.setor ? (
                        <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                          {item.setor}
                        </Texto>
                      ) : null}
                    </View>
                    <Icone nome={ativo ? 'checkmark-circle' : 'ellipse-outline'} tamanho={22} cor={ativo ? marca[500] : cores.texto3} />
                  </Pressionavel>
                );
              }}
            />
          </View>
        </View>
      </Modal>
    </View>
  );
}

/** Título de seção com ícone dentro de um cartão (detalhes). */
export function Secao({ icone, titulo, cor = marca[500], children }: { icone: NomeIcone; titulo: string; cor?: string; children: ReactNode }) {
  return (
    <Cartao style={{ padding: 16, gap: 12 }}>
      <View className="flex-row items-center gap-2">
        <View style={{ width: 30, height: 30, borderRadius: 9, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={icone} tamanho={16} cor={cor} />
        </View>
        <Texto variante="corpoForte" className="flex-1">
          {titulo}
        </Texto>
      </View>
      {children}
    </Cartao>
  );
}
