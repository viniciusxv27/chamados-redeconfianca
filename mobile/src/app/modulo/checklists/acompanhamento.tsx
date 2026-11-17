import { router, type Href } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';
import Animated, { FadeIn, LinearTransition } from 'react-native-reanimated';

import { EsqueletoBloco, Indicador, TelaModulo } from '@/components/modulos/comum';
import { BarraProgresso, COR_CHECKLIST, diaPorExtenso, isoDoDia, somarDias } from '@/components/modulos/checklists/pecas';
import { Avatar, BotaoIcone, Campo, Cartao, Entrada, EstadoErro, EstadoVazio, Icone, Pressionavel, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useAcompanhamento } from '@/hooks/modulos/checklists';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { LinhaPessoa, Loja, Turno } from '@/services/modulos/checklists';
import { abrirNoPortal } from '@/utils/links';

function corDaTaxa(taxa: number, total: number) {
  if (!total) return '#6B7280';
  if (taxa >= 100) return estado.sucesso;
  if (taxa >= 50) return estado.alerta;
  return estado.perigo;
}

/**
 * Acompanhamento do dia por loja (o mesmo recorte da visão macro do portal, /checklists/admin/executions/macro/).
 * Só leitura: aprovar e cobrar continuam no portal.
 */
export default function Acompanhamento() {
  const hoje = isoDoDia(new Date());
  const [dia, setDia] = useState(hoje);
  const [busca, setBusca] = useState('');
  const [abertas, setAbertas] = useState<Record<string, boolean>>({});
  const { data, isPending, isError, error, refetch, isRefetching } = useAcompanhamento(dia);

  const termo = busca.trim().toLowerCase();
  const lojas: Loja[] = (data?.lojas ?? [])
    .map((l) => (!termo || l.loja.toLowerCase().includes(termo) ? l : { ...l, pessoas: l.pessoas.filter((p) => p.pessoa.nome.toLowerCase().includes(termo)) }))
    .filter((l) => l.pessoas.length);

  const t = data?.totais;
  return (
    <TelaModulo
      titulo="Acompanhamento"
      subtitulo="Quem fez e quem não fez, por loja"
      aoAtualizar={() => void refetch()}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => void abrirNoPortal(data?.url_web ?? '/checklists/admin/executions/macro/')} />}>
      <Cartao style={{ padding: 6, flexDirection: 'row', alignItems: 'center' }}>
        <BotaoIcone icone="chevron-back" rotulo="Dia anterior" onPress={() => setDia(somarDias(dia, -1))} />
        <Pressionavel onPress={() => setDia(hoje)} style={{ flex: 1, alignItems: 'center' }} accessibilityLabel="Voltar para hoje">
          <Texto variante="corpoForte" style={{ textTransform: 'capitalize' }}>
            {diaPorExtenso(dia, true)}
          </Texto>
          {dia !== hoje ? (
            <Texto variante="legenda" tom="marca">
              Tocar para voltar a hoje
            </Texto>
          ) : null}
        </Pressionavel>
        <BotaoIcone icone="chevron-forward" rotulo="Dia seguinte" onPress={() => setDia(somarDias(dia, 1))} />
      </Cartao>

      {/* Carregando/erro ficam abaixo do seletor de dia: trocar de dia não esconde a navegação. */}
      {isPending ? (
        <>
          <EsqueletoBloco linhas={3} />
          <EsqueletoBloco linhas={4} />
        </>
      ) : isError ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : data && t ? (
        <>
          <Entrada indice={0}>
            <View className="flex-row flex-wrap gap-3">
              <Indicador icone="checkmark-done-outline" rotulo="Feitos no dia" valor={`${t.feitos}/${t.total}`} detalhe={`${t.taxa}% · ${t.pessoas} pessoas`} cor={corDaTaxa(t.taxa, t.total)} />
              <Indicador
                icone="alert-circle-outline"
                rotulo="Atrasados"
                valor={String(t.atrasados)}
                detalhe={`Até ${diaPorExtenso(data.limite_atraso).split(', ')[1]}, ainda abertos`}
                cor={t.atrasados ? estado.perigo : estado.sucesso}
              />
            </View>
          </Entrada>

          <Campo icone="search" placeholder="Buscar loja ou pessoa" value={busca} onChangeText={setBusca} autoCorrect={false} returnKeyType="search" />

          {lojas.length ? (
            lojas.map((l, i) => (
              <Entrada key={l.loja} indice={i + 1}>
                <CartaoLoja loja={l} aberta={!!termo || !!abertas[l.loja]} alternar={() => setAbertas((a) => ({ ...a, [l.loja]: !a[l.loja] }))} dia={dia} />
              </Entrada>
            ))
          ) : (
            <EstadoVazio icone="storefront-outline" titulo={termo ? 'Nada encontrado' : 'Nenhum checklist neste dia'} texto={termo ? 'Tente outro nome.' : 'Ninguém do seu recorte tinha checklist para este dia.'} />
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}

function CartaoLoja({ loja, aberta, alternar, dia }: { loja: Loja; aberta: boolean; alternar: () => void; dia: string }) {
  const cores = useCores();
  const cor = corDaTaxa(loja.taxa, loja.total);
  return (
    <Cartao style={{ overflow: 'hidden' }}>
      <Pressionavel onPress={alternar} escala={0.99} accessibilityLabel={`${loja.loja}: ${loja.feitos} de ${loja.total} feitos`} accessibilityState={{ expanded: aberta }} style={{ padding: 16, gap: 10 }}>
        <View className="flex-row items-center gap-3">
          <View className="flex-1">
            <Texto variante="corpoForte" numberOfLines={1}>
              {loja.loja}
            </Texto>
            <Texto variante="legenda" tom="suave">
              {loja.pessoas.length} {loja.pessoas.length === 1 ? 'pessoa' : 'pessoas'}
              {loja.faltam ? ` · faltam ${loja.faltam}` : loja.total ? ' · tudo feito' : ''}
              {loja.atrasados ? ` · ${loja.atrasados} atrasado${loja.atrasados > 1 ? 's' : ''}` : ''}
            </Texto>
          </View>
          <Texto variante="titulo3" style={{ color: cor, fontVariant: ['tabular-nums'] }}>
            {loja.feitos}/{loja.total}
          </Texto>
          <Icone nome={aberta ? 'chevron-up' : 'chevron-down'} tamanho={18} cor={cores.texto3} />
        </View>
        <BarraProgresso valor={loja.total ? loja.taxa : 0} cor={cor} />
      </Pressionavel>
      {aberta ? (
        <Animated.View entering={FadeIn.duration(180)} layout={LinearTransition} style={{ borderTopWidth: 1, borderTopColor: cores.bordaSuave }}>
          {loja.pessoas.map((p) => (
            <LinhaDaPessoa key={p.pessoa.id} p={p} dia={dia} />
          ))}
        </Animated.View>
      ) : null}
    </Cartao>
  );
}

function turnoTexto(rotulo: string, t: Turno) {
  return t.total ? `${rotulo} ${t.feitos}/${t.total}` : null;
}

function LinhaDaPessoa({ p, dia }: { p: LinhaPessoa; dia: string }) {
  const cores = useCores();
  const tudo = p.total > 0 && p.feitos === p.total;
  const turnos = [turnoTexto('Manhã', p.manha), turnoTexto('Tarde', p.tarde)].filter(Boolean).join(' · ');
  return (
    <Pressionavel
      onPress={() => router.push({ pathname: '/modulo/checklists/pessoa/[id]', params: { id: String(p.pessoa.id), data: dia } } as unknown as Href)}
      accessibilityLabel={`${p.pessoa.nome}: ${p.feitos} de ${p.total} feitos`}
      style={{ paddingHorizontal: 16, paddingVertical: 12, flexDirection: 'row', alignItems: 'center', gap: 12 }}>
      <Avatar nome={p.pessoa.nome} foto={p.pessoa.foto} tamanho={34} />
      <View className="flex-1">
        <Texto variante="corpoForte" numberOfLines={1}>
          {p.pessoa.nome}
        </Texto>
        <Texto variante="legenda" tom="suave" numberOfLines={1}>
          {turnos || 'Nada no dia'}
          {p.atrasados ? ` · ${p.atrasados} atrasado${p.atrasados > 1 ? 's' : ''}` : ''}
        </Texto>
      </View>
      <Icone nome={tudo ? 'checkmark-circle' : p.total ? 'ellipse-outline' : 'alert-circle-outline'} tamanho={20} cor={tudo ? estado.sucesso : p.atrasados ? estado.perigo : cores.texto3} />
      <Texto variante="legenda" style={{ fontFamily: fontes.seminegrito, color: tudo ? estado.sucesso : COR_CHECKLIST, minWidth: 34, textAlign: 'right' }}>
        {p.total ? `${p.taxa}%` : '—'}
      </Texto>
    </Pressionavel>
  );
}
