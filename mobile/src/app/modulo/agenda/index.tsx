import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { diaCurto, diasDaSemana, estaNoDia, isoDoDia, rotuloDaSemana, rotuloDoDia, segundaDa, somarDias, dataDoDia } from '@/components/modulos/agenda/datas';
import { CartaoEvento } from '@/components/modulos/agenda/pecas';
import { EsqueletoBloco, TelaModulo } from '@/components/modulos/comum';
import { BotaoIcone, Cartao, Chip, Entrada, EstadoErro, EstadoVazio, Icone, type NomeIcone, Pressionavel, Segmentado, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useSemana } from '@/hooks/modulos/agenda';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { Evento } from '@/services/modulos/agenda';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

type Visao = 'dia' | 'semana';

function abrirEvento(id: number) {
  router.push({ pathname: '/modulo/agenda/[id]', params: { id: String(id) } } as unknown as Href);
}

/** Minha agenda: a semana (segunda a domingo) com o dia escolhido em destaque. */
export default function Agenda() {
  const [hoje] = useState(() => isoDoDia(new Date()));
  const [agora] = useState(() => Date.now());
  const [dia, setDia] = useState(hoje);
  const [visao, setVisao] = useState<Visao>('dia');
  const segunda = segundaDa(dia);
  const domingo = somarDias(segunda, 6);
  const { data, isPending, error, refetch, isRefetching } = useSemana(segunda, domingo);
  const daSemana = data && data.inicio === segunda;
  const eventos = daSemana ? data.eventos : [];
  const pendentes = (data?.convites_pendentes ?? 0) + (data?.solicitacoes_pendentes ?? 0);

  return (
    <TelaModulo
      titulo="Agenda"
      subtitulo={rotuloDaSemana(segunda)}
      carregando={isPending && !data}
      erro={data ? undefined : error}
      aoAtualizar={() => void refetch()}
      atualizando={isRefetching}
      direita={
        <View className="flex-row gap-2">
          <BotaoIcone icone="open-outline" rotulo="Abrir a agenda no portal" onPress={() => abrirNoPortal('/agenda/')} />
          <BotaoIcone icone="add" rotulo="Novo evento" cor="#FFFFFF" fundo={marca[500]} onPress={() => router.push({ pathname: '/modulo/agenda/evento', params: { dia } } as unknown as Href)} />
        </View>
      }>
      {pendentes > 0 ? (
        <Entrada indice={0}>
          <Atalho
            icone="mail-unread-outline"
            cor={estado.alerta}
            titulo={pendentes === 1 ? '1 convite para responder' : `${pendentes} convites para responder`}
            texto="Aceite ou recuse os eventos e pedidos de reunião."
            onPress={() => router.push('/modulo/agenda/convites' as Href)}
          />
        </Entrada>
      ) : null}

      <Entrada indice={1}>
        <View className="gap-3">
          <View className="flex-row items-center gap-2">
            <BotaoIcone icone="chevron-back" rotulo="Semana anterior" onPress={() => setDia(somarDias(dia, -7))} />
            <View className="flex-1 items-center">
              <Texto variante="corpoForte">{rotuloDaSemana(segunda)}</Texto>
            </View>
            <BotaoIcone icone="chevron-forward" rotulo="Próxima semana" onPress={() => setDia(somarDias(dia, 7))} />
          </View>
          <FaixaDaSemana segunda={segunda} hoje={hoje} escolhido={dia} eventos={eventos} aoEscolher={(d) => (setDia(d), setVisao('dia'))} />
          <View className="flex-row items-center gap-3">
            <View className="flex-1">
              <Segmentado
                opcoes={[
                  { valor: 'dia', rotulo: 'Dia' },
                  { valor: 'semana', rotulo: 'Semana' },
                ]}
                valor={visao}
                aoMudar={setVisao}
              />
            </View>
            {dia !== hoje ? <Chip rotulo="Hoje" ativo={false} onPress={() => setDia(hoje)} /> : null}
          </View>
        </View>
      </Entrada>

      {!daSemana && error ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : !daSemana ? (
        <View className="gap-3">
          <EsqueletoBloco linhas={3} />
          <EsqueletoBloco linhas={3} />
        </View>
      ) : visao === 'dia' ? (
        <ListaDoDia dia={dia} hoje={hoje} agora={agora} eventos={eventos.filter((e) => estaNoDia(e, dia))} />
      ) : (
        diasDaSemana(segunda).map((d, i) => (
          <Entrada key={d} indice={i + 2}>
            <BlocoDoDia dia={d} hoje={hoje} agora={agora} eventos={eventos.filter((e) => estaNoDia(e, d))} aoAbrirDia={() => (setDia(d), setVisao('dia'))} />
          </Entrada>
        ))
      )}

      <Entrada indice={9}>
        <Atalho
          icone="people-outline"
          cor={estado.info}
          titulo="Agenda de alguém"
          texto="Veja os horários livres de um colega e peça uma reunião."
          onPress={() => router.push('/modulo/agenda/pessoas' as Href)}
        />
      </Entrada>
      <Entrada indice={9}>
        <Atalho
          icone="swap-horizontal-outline"
          cor={estado.alerta}
          titulo="Solicitações de reunião"
          texto="As que você recebeu e as que enviou (cancele as pendentes)."
          onPress={() => router.push('/modulo/agenda/solicitacoes' as Href)}
        />
      </Entrada>
      <Entrada indice={9}>
        <Atalho
          icone="mic-outline"
          cor="#8B5CF6"
          titulo="Atas de reunião"
          texto="Transcrições com resumo, decisões e as tarefas criadas."
          onPress={() => router.push('/modulo/agenda/transcricoes' as Href)}
        />
      </Entrada>
    </TelaModulo>
  );
}

/** Os 7 dias da semana; ponto embaixo = tem compromisso. */
function FaixaDaSemana({ segunda, hoje, escolhido, eventos, aoEscolher }: { segunda: string; hoje: string; escolhido: string; eventos: Evento[]; aoEscolher: (d: string) => void }) {
  const cores = useCores();
  return (
    <View className="flex-row gap-1.5">
      {diasDaSemana(segunda).map((d) => {
        const ativo = d === escolhido;
        const eHoje = d === hoje;
        const qtd = eventos.filter((e) => estaNoDia(e, d)).length;
        return (
          <Pressionavel
            key={d}
            onPress={() => aoEscolher(d)}
            vibrar
            escala={0.92}
            accessibilityRole="button"
            accessibilityState={{ selected: ativo }}
            accessibilityLabel={`${rotuloDoDia(d)}${qtd ? `, ${qtd} compromisso${qtd > 1 ? 's' : ''}` : ''}`}
            style={{
              flex: 1,
              alignItems: 'center',
              paddingVertical: 10,
              gap: 4,
              borderRadius: raios.md,
              backgroundColor: ativo ? marca[500] : cores.superficie,
              borderWidth: 1.5,
              borderColor: ativo ? marca[500] : eHoje ? comAlfa(marca[500], 0.6) : cores.bordaSuave,
            }}>
            <Texto variante="rotulo" style={{ color: ativo ? '#FFFFFF' : eHoje ? marca[600] : cores.texto3 }}>
              {diaCurto(d)}
            </Texto>
            <Texto variante="corpoForte" style={{ color: ativo ? '#FFFFFF' : cores.texto, fontFamily: fontes.negrito }}>
              {String(dataDoDia(d).getDate())}
            </Texto>
            <View style={{ height: 6, flexDirection: 'row', gap: 2 }}>
              {Array.from({ length: Math.min(qtd, 3) }, (_, i) => (
                <View key={i} style={{ width: 5, height: 5, borderRadius: 3, backgroundColor: ativo ? '#FFFFFF' : marca[500] }} />
              ))}
            </View>
          </Pressionavel>
        );
      })}
    </View>
  );
}

function ListaDoDia({ dia, hoje, agora, eventos }: { dia: string; hoje: string; agora: number; eventos: Evento[] }) {
  return (
    <View className="gap-3">
      <Texto variante="titulo3">{dia === hoje ? `Hoje · ${rotuloDoDia(dia)}` : rotuloDoDia(dia)}</Texto>
      {eventos.length ? (
        eventos.map((ev, i) => (
          <Entrada key={ev.id} indice={i + 2}>
            <CartaoEvento ev={ev} agora={agora} onPress={() => abrirEvento(ev.id)} />
          </Entrada>
        ))
      ) : (
        <EstadoVazio icone="sunny-outline" titulo="Nada marcado" texto={dia === hoje ? 'Seu dia está livre.' : 'Nenhum compromisso neste dia.'} />
      )}
    </View>
  );
}

function BlocoDoDia({ dia, hoje, agora, eventos, aoAbrirDia }: { dia: string; hoje: string; agora: number; eventos: Evento[]; aoAbrirDia: () => void }) {
  const eHoje = dia === hoje;
  return (
    <View className="gap-2">
      <Pressionavel onPress={aoAbrirDia} accessibilityLabel={`Ver ${rotuloDoDia(dia)}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        {eHoje ? (
          <View style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: raios.pilula, backgroundColor: marca[500] }}>
            <Texto variante="rotulo" tom="branco">
              Hoje
            </Texto>
          </View>
        ) : null}
        <Texto variante="corpoForte" tom={eHoje ? 'marca' : 'padrao'} className="flex-1">
          {rotuloDoDia(dia)}
        </Texto>
        <Texto variante="legenda" tom="fraco">
          {eventos.length ? `${eventos.length}` : 'livre'}
        </Texto>
      </Pressionavel>
      {eventos.map((ev) => (
        <CartaoEvento key={ev.id} ev={ev} agora={agora} onPress={() => abrirEvento(ev.id)} />
      ))}
    </View>
  );
}

function Atalho({ icone, cor, titulo, texto, onPress }: { icone: NomeIcone; cor: string; titulo: string; texto: string; onPress: () => void }) {
  const cores = useCores();
  return (
    <Cartao onPress={onPress} acessibilidade={titulo} style={{ padding: 16, flexDirection: 'row', alignItems: 'center', gap: 14 }}>
      <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome={icone} tamanho={22} cor={cor} />
      </View>
      <View className="flex-1 gap-0.5">
        <Texto variante="corpoForte">{titulo}</Texto>
        <Texto variante="legenda" tom="suave">
          {texto}
        </Texto>
      </View>
      <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
    </Cartao>
  );
}
