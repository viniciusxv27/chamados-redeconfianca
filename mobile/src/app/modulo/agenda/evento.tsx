import * as Haptics from 'expo-haptics';
import { type Href, router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { dataDoIso, faixaDeHorario, horaCurta, horaValida, isoDaData, isoDoDia, momento, rotuloDoDia } from '@/components/modulos/agenda/datas';
import { CampoData, CampoHora, EscolhaDePessoas, Interruptor } from '@/components/modulos/agenda/pecas';
import { EsqueletoBloco } from '@/components/modulos/comum';
import { Botao, BotaoIcone, Campo, Cartao, Chip, EstadoErro, Icone, Pressionavel, Tela, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useConflitos, useEvento, useOpcoesAgenda, useSalvarEvento } from '@/hooks/modulos/agenda';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { CamposEvento, DetalheEvento, Opcoes, TipoEvento } from '@/services/modulos/agenda';
import { comAlfa } from '@/utils/formatar';

/** Novo evento (?dia=AAAA-MM-DD) ou edição (?id=N, só o dono). Salva pelas mesmas funções do calendário do portal. */
export default function FormularioEvento() {
  const params = useLocalSearchParams<{ id?: string; dia?: string }>();
  const id = Number(params.id) || 0;
  const original = useEvento(id);
  const opcoes = useOpcoesAgenda();

  const carregando = opcoes.isPending || (id > 0 && original.isPending);
  const erro = opcoes.error ?? (id > 0 ? original.error : null);

  return (
    <Tela>
      <Topo titulo={id ? 'Editar evento' : 'Novo evento'} />
      {carregando ? (
        <View className="gap-4 p-5">
          <EsqueletoBloco linhas={4} />
          <EsqueletoBloco linhas={3} />
        </View>
      ) : erro || !opcoes.data || (id > 0 && !original.data) ? (
        <EstadoErro
          mensagem={mensagemDoErro(erro)}
          aoTentar={() => {
            void opcoes.refetch();
            if (id) void original.refetch();
          }}
        />
      ) : id > 0 && original.data && !original.data.pode_editar ? (
        <EstadoErro mensagem="Só quem marcou o evento pode editar." />
      ) : (
        <Formulario key={id || 'novo'} opcoes={opcoes.data} original={id ? original.data : undefined} diaInicial={params.dia} />
      )}
    </Tela>
  );
}

function Topo({ titulo }: { titulo: string }) {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-row items-center justify-between px-5 pb-3" style={{ paddingTop: insets.top + 10 }}>
      <View className="flex-1">
        <Texto variante="rotulo" tom="fraco">
          Agenda
        </Texto>
        <Texto variante="titulo2">{titulo}</Texto>
      </View>
      <BotaoIcone icone="close" rotulo="Fechar" onPress={() => router.back()} />
    </View>
  );
}

function valoresIniciais(original: DetalheEvento | undefined, diaInicial: string | undefined) {
  if (original) {
    const a = new Date(original.inicio);
    const b = new Date(original.fim);
    return {
      dia: isoDoDia(a),
      diaFim: isoDoDia(b),
      hora: horaCurta(original.inicio),
      horaFim: horaCurta(original.fim),
    };
  }
  const agora = new Date();
  const hoje = isoDoDia(agora);
  const dia = diaInicial && /^\d{4}-\d{2}-\d{2}$/.test(diaInicial) ? diaInicial : hoje;
  // Hoje: a próxima hora cheia; outro dia: 9h. Uma hora de duração.
  const h = dia === hoje ? Math.min(agora.getHours() + 1, 22) : 9;
  const dois = (n: number) => String(n).padStart(2, '0');
  return { dia, diaFim: dia, hora: `${dois(h)}:00`, horaFim: `${dois(h + 1)}:00` };
}

function Formulario({ opcoes, original, diaInicial }: { opcoes: Opcoes; original?: DetalheEvento; diaInicial?: string }) {
  const cores = useCores();
  const salvar = useSalvarEvento(original?.id);
  const inicial = valoresIniciais(original, diaInicial);

  const [titulo, setTitulo] = useState(original?.titulo ?? '');
  const [tipo, setTipo] = useState<TipoEvento>(original?.tipo ?? 'event');
  const [dia, setDia] = useState(dataDoIso(inicial.dia));
  const [diaFim, setDiaFim] = useState(dataDoIso(inicial.diaFim));
  const [outroDia, setOutroDia] = useState(inicial.diaFim !== inicial.dia);
  const [hora, setHora] = useState(inicial.hora);
  const [horaFim, setHoraFim] = useState(inicial.horaFim);
  const [diaInteiro, setDiaInteiro] = useState(original?.dia_inteiro ?? false);
  const [local, setLocal] = useState(original?.local ?? '');
  const [link, setLink] = useState(original?.link_do_portal ? '' : (original?.link ?? ''));
  const [descricao, setDescricao] = useState(original?.descricao ?? '');
  const [cor, setCor] = useState(original?.cor ?? '#4f46e5');
  const [privado, setPrivado] = useState(original?.privado ?? false);
  const [participantes, setParticipantes] = useState<number[]>(original?.participantes.map((p) => p.id) ?? []);
  const [repetir, setRepetir] = useState(false);
  const [repetirAte, setRepetirAte] = useState('');
  const [erros, setErros] = useState<Record<string, string>>({});

  const pessoas = opcoes.pessoas.filter((p) => p.id !== opcoes.eu);
  const diaIso = isoDaData(dia);
  const diaFimIso = outroDia ? isoDaData(diaFim) : diaIso;
  const inicioIso = diaIso && (diaInteiro || horaValida(hora)) ? momento(diaIso, diaInteiro ? '00:00' : hora) : null;
  const fimIso = diaFimIso && (diaInteiro || horaValida(horaFim)) ? momento(diaFimIso, diaInteiro ? '23:59' : horaFim) : null;
  const periodoOk = !!inicioIso && !!fimIso && new Date(fimIso) > new Date(inicioIso);
  const conflitos = useConflitos(periodoOk ? inicioIso : null, periodoOk ? fimIso : null, original?.id);

  function limpar(campo: string) {
    if (erros[campo]) setErros(({ [campo]: _, ...resto }) => resto);
  }

  function enviar() {
    const e: Record<string, string> = {};
    if (!titulo.trim()) e.titulo = 'Dê um título ao evento.';
    if (!diaIso) e.dia = 'Informe o dia (DD/MM/AAAA).';
    if (outroDia && !isoDaData(diaFim)) e.diaFim = 'Informe o dia do fim (DD/MM/AAAA).';
    if (!diaInteiro && !horaValida(hora)) e.hora = 'Hora inválida.';
    if (!diaInteiro && !horaValida(horaFim)) e.horaFim = 'Hora inválida.';
    if (!e.dia && !e.diaFim && !e.hora && !e.horaFim && !periodoOk) e.horaFim = 'O fim precisa ser depois do início.';
    if (repetir && repetirAte && !isoDaData(repetirAte)) e.repetirAte = 'Data inválida (DD/MM/AAAA).';
    setErros(e);
    if (Object.keys(e).length || !inicioIso || !fimIso) {
      if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      return;
    }
    const campos: CamposEvento = {
      titulo: titulo.trim(),
      descricao: descricao.trim(),
      inicio: inicioIso,
      fim: fimIso,
      tipo,
      cor,
      dia_inteiro: diaInteiro,
      local: local.trim(),
      // A sala da Chamada é do portal: o link dela não vai no pedido (o portal mantém/cria a sala).
      ...(tipo === 'call' && original?.link_do_portal ? {} : { link: link.trim() }),
      privado,
      participantes,
      ...(original ? {} : { repetir_semanal: repetir, repetir_ate: repetir && repetirAte ? isoDaData(repetirAte) : undefined }),
    };
    salvar.mutate(campos, {
      onSuccess: (r) => {
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        if (r.sala && !original) Alert.alert('Chamada marcada', 'A sala de vídeo do portal já foi criada. O link está no evento.');
        if (original) router.back();
        else router.replace({ pathname: '/modulo/agenda/[id]', params: { id: String(r.evento.id) } } as unknown as Href);
      },
      onError: () => {
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      },
    });
  }

  return (
    <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={{ padding: 20, gap: 18, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
        <Campo rotulo="Título" placeholder="Ex.: Reunião com a loja" value={titulo} onChangeText={(v) => (setTitulo(v), limpar('titulo'))} erro={erros.titulo} maxLength={255} />

        <View className="gap-2">
          <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
            Tipo
          </Texto>
          <View className="flex-row flex-wrap gap-2">
            {opcoes.tipos.map((t) => (
              <Chip key={t.valor} rotulo={t.rotulo} ativo={tipo === t.valor} onPress={() => setTipo(t.valor)} />
            ))}
          </View>
          {tipo === 'call' ? (
            <Texto variante="legenda" tom="fraco">
              {original?.link_do_portal ? 'A sala de vídeo do portal continua a mesma.' : 'Ao salvar, o portal cria a sala de vídeo da chamada.'}
            </Texto>
          ) : tipo === 'task' ? (
            <Texto variante="legenda" tom="fraco">
              Vira também uma tarefa no portal, com status e responsáveis.
            </Texto>
          ) : null}
        </View>

        <CampoData rotulo="Dia" valor={dia} aoMudar={(v) => (setDia(v), limpar('dia'), limpar('horaFim'))} erro={erros.dia} />
        <Interruptor rotulo="Dia inteiro" valor={diaInteiro} aoMudar={(v) => (setDiaInteiro(v), limpar('horaFim'))} />
        {!diaInteiro ? (
          <View className="flex-row gap-3">
            <View className="flex-1">
              <CampoHora rotulo="Começa" valor={hora} aoMudar={(v) => (setHora(v), limpar('hora'), limpar('horaFim'))} erro={erros.hora} />
            </View>
            <View className="flex-1">
              <CampoHora rotulo="Termina" valor={horaFim} aoMudar={(v) => (setHoraFim(v), limpar('horaFim'))} erro={erros.horaFim} />
            </View>
          </View>
        ) : erros.horaFim ? (
          <Texto variante="legenda" tom="perigo">
            {erros.horaFim}
          </Texto>
        ) : null}
        <Interruptor
          rotulo="Termina em outro dia"
          valor={outroDia}
          aoMudar={(v) => {
            setOutroDia(v);
            if (v && !isoDaData(diaFim)) setDiaFim(dia);
            limpar('horaFim');
          }}
        />
        {outroDia ? <CampoData rotulo="Dia do fim" valor={diaFim} aoMudar={(v) => (setDiaFim(v), limpar('diaFim'), limpar('horaFim'))} erro={erros.diaFim} atalhos={false} /> : null}

        {periodoOk && conflitos.data?.length ? (
          <Cartao style={{ padding: 14, gap: 8, borderColor: comAlfa(estado.alerta, 0.5) }}>
            <View className="flex-row items-center gap-2">
              <Icone nome="warning-outline" tamanho={18} cor={estado.alerta} />
              <Texto variante="corpoForte" className="flex-1">
                Você já tem compromisso nesse horário
              </Texto>
            </View>
            {conflitos.data.map((c) => (
              <Texto key={c.id} variante="legenda" tom="suave" numberOfLines={1}>
                {`${faixaDeHorario(c.inicio, c.fim, c.dia_inteiro)} · ${c.titulo}`}
              </Texto>
            ))}
            <Texto variante="legenda" tom="fraco">
              Dá para salvar mesmo assim.
            </Texto>
          </Cartao>
        ) : null}

        <Campo rotulo="Local (opcional)" icone="location-outline" placeholder="Sala, loja, endereço…" value={local} onChangeText={setLocal} maxLength={255} />
        {tipo !== 'call' ? (
          <Campo
            rotulo="Link (opcional)"
            icone="link-outline"
            placeholder="https://…"
            value={link}
            onChangeText={setLink}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            maxLength={500}
          />
        ) : null}

        <EscolhaDePessoas pessoas={pessoas} grupos={opcoes.grupos} escolhidos={participantes} aoMudar={setParticipantes} />

        <Campo rotulo="Descrição (opcional)" placeholder="Pauta, observações…" value={descricao} onChangeText={setDescricao} multiline />

        <View className="gap-2">
          <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
            Cor
          </Texto>
          <View className="flex-row flex-wrap gap-3">
            {opcoes.cores.map((c) => (
              <Pressionavel
                key={c.valor}
                onPress={() => setCor(c.valor)}
                vibrar
                escala={0.9}
                accessibilityRole="radio"
                accessibilityState={{ selected: cor === c.valor }}
                accessibilityLabel={c.rotulo}
                style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: c.valor, alignItems: 'center', justifyContent: 'center', borderWidth: 3, borderColor: cor === c.valor ? cores.texto : 'transparent' }}>
                {cor === c.valor ? <Icone nome="checkmark" tamanho={16} cor="#FFFFFF" /> : null}
              </Pressionavel>
            ))}
          </View>
        </View>

        <Interruptor rotulo="Evento privado" ajuda="Para os outros aparece só como ocupado." valor={privado} aoMudar={setPrivado} />

        {original ? (
          original.recorrente ? (
            <Texto variante="legenda" tom="fraco">
              A repetição semanal não muda na edição (como no portal): esta alteração vale só para este dia.
            </Texto>
          ) : null
        ) : (
          <View className="gap-3">
            <Interruptor rotulo="Repetir toda semana" ajuda="Sem data final, repete por 3 meses." valor={repetir} aoMudar={setRepetir} />
            {repetir ? (
              <CampoData rotulo="Repetir até (opcional)" valor={repetirAte} aoMudar={(v) => (setRepetirAte(v), limpar('repetirAte'))} erro={erros.repetirAte} atalhos={false} />
            ) : null}
          </View>
        )}

        {salvar.isError ? (
          <Texto variante="legenda" tom="perigo">
            {mensagemDoErro(salvar.error)}
          </Texto>
        ) : null}
        <Botao
          titulo={original ? 'Salvar alterações' : 'Marcar evento'}
          icone="checkmark"
          tamanho="lg"
          carregando={salvar.isPending}
          onPress={enviar}
        />
        {diaIso ? (
          <Texto variante="legenda" tom="fraco" className="text-center">
            {rotuloDoDia(diaIso)}
            {outroDia && isoDaData(diaFim) && isoDaData(diaFim) !== diaIso ? ` até ${rotuloDoDia(isoDaData(diaFim))}` : ''}
          </Texto>
        ) : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

