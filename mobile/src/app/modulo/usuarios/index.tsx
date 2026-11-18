/**
 * Usuários — a lista de /users/manage/users/ no celular: busca por nome, login,
 * e-mail ou setor e filtros de situação, setor e hierarquia (os mesmos da web).
 * Editar, ativar/desativar, senha e liberações ficam no portal.
 */
import { router, type Href } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { FlatList, RefreshControl, ScrollView, View } from 'react-native';

import { CartaoUsuario } from '@/components/modulos/usuarios/pecas';
import { BarraDetalhe, BotaoIcone, CabecalhoGrande, Campo, Chip, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Tela } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { marca } from '@/constants/theme';
import { useOpcoesUsuarios, useUsuarios } from '@/hooks/modulos/usuarios';
import { mensagemDoErro } from '@/services/api';
import type { Hierarquia, SituacaoUsuario, UsuarioResumo } from '@/services/modulos/usuarios';
import { abrirNoPortal } from '@/utils/links';
import { EsqueletoRodape } from '@/components/ui/esqueleto';

const SITUACOES: { valor: SituacaoUsuario | ''; rotulo: string }[] = [
  { valor: 'ATIVO', rotulo: 'Ativos' },
  { valor: '', rotulo: 'Todos' },
  { valor: 'FERIAS', rotulo: 'Férias' },
  { valor: 'AFASTADO', rotulo: 'Afastados' },
  { valor: 'INATIVO', rotulo: 'Inativos' },
];

export default function Usuarios() {
  const [texto, setTexto] = useState('');
  const [busca, setBusca] = useState('');
  const [status, setStatus] = useState<SituacaoUsuario | ''>('ATIVO');
  const [setor, setSetor] = useState<number>(0);
  const [hierarquia, setHierarquia] = useState<Hierarquia | ''>('');

  // Busca espera a pessoa parar de digitar (350 ms).
  useEffect(() => {
    const t = setTimeout(() => setBusca(texto.trim()), 350);
    return () => clearTimeout(t);
  }, [texto]);

  const opcoes = useOpcoesUsuarios();
  const filtro = useMemo(() => ({ busca: busca || undefined, status, setor: setor || null, hierarquia }), [busca, status, setor, hierarquia]);
  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useUsuarios(filtro);
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);
  const total = data?.pages[0]?.total ?? 0;

  const itensSetor = useMemo(() => [{ id: 0, nome: 'Todos os setores' }, ...(opcoes.data?.setores ?? [])], [opcoes.data]);
  const hierarquias = useMemo(() => opcoes.data?.hierarquias ?? [], [opcoes.data]);
  const itensHierarquia = useMemo(() => [{ id: 0, nome: 'Todas' }, ...hierarquias.map((h, i) => ({ id: i + 1, nome: h.rotulo }))], [hierarquias]);
  const idHierarquia = hierarquia ? hierarquias.findIndex((h) => h.valor === hierarquia) + 1 : 0;
  const filtrando = Boolean(busca || setor || hierarquia || status !== 'ATIVO');

  return (
    <Tela>
      <BarraDetalhe direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/users/manage/users/')} />} />
      <CabecalhoGrande
        titulo="Usuários"
        subtitulo={isPending ? ' ' : `${total} ${total === 1 ? 'pessoa' : 'pessoas'}${opcoes.data ? ` · ${opcoes.data.totais.ativos} ativas no portal` : ''}`}
      />
      <View className="gap-3 pb-3">
        <View className="px-5">
          <Campo icone="search" placeholder="Nome, login, e-mail ou setor" value={texto} onChangeText={setTexto} returnKeyType="search" autoCorrect={false} autoCapitalize="none" clearButtonMode="while-editing" />
        </View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
          {SITUACOES.map((s) => (
            <Chip key={s.rotulo} rotulo={s.rotulo} ativo={status === s.valor} onPress={() => setStatus(s.valor)} />
          ))}
        </ScrollView>
        <View className="flex-row gap-3 px-5">
          <View className="flex-1">
            <Seletor rotulo="Setor" placeholder="Todos os setores" itens={itensSetor} valor={setor} aoEscolher={setSetor} carregando={opcoes.isPending} />
          </View>
          <View className="flex-1">
            <Seletor
              rotulo="Hierarquia"
              placeholder="Todas"
              itens={itensHierarquia}
              valor={idHierarquia}
              aoEscolher={(id) => setHierarquia(id ? (hierarquias[id - 1]?.valor ?? '') : '')}
              carregando={opcoes.isPending}
            />
          </View>
        </View>
      </View>

      {isPending ? (
        <View className="px-5 pt-1">
          <EsqueletoLista quantos={5} />
        </View>
      ) : isError && !itens.length ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <FlatList<UsuarioResumo>
          data={itens}
          keyExtractor={(u) => String(u.id)}
          renderItem={({ item, index }) => (
            <Entrada indice={index < 10 ? index : 0}>
              <CartaoUsuario usuario={item} onPress={() => router.push({ pathname: '/modulo/usuarios/[id]', params: { id: String(item.id) } } as unknown as Href)} />
            </Entrada>
          )}
          contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 40, gap: 10 }}
          onEndReachedThreshold={0.4}
          onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
          refreshControl={<RefreshControl refreshing={isRefetching && !isFetchingNextPage} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
          ListFooterComponent={isFetchingNextPage ? <EsqueletoRodape /> : undefined}
          ListEmptyComponent={
            <EstadoVazio
              icone={filtrando ? 'search-outline' : 'people-outline'}
              titulo="Ninguém encontrado"
              texto={busca ? `Nenhum colaborador com "${busca}" neste filtro.` : 'Troque os filtros para ver mais gente.'}
            />
          }
          keyboardDismissMode="on-drag"
          keyboardShouldPersistTaps="handled"
        />
      )}
    </Tela>
  );
}
