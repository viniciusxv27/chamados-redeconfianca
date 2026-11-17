/**
 * Quadro de HC — a rede no celular: números do topo, previsto × real da rede e a
 * árvore carteira → loja (toque na loja para ver as pessoas). Filtros iguais aos da
 * web (busca, carteira, cargo, rápidos, sem gerente) e mais "com falta/sobra".
 * Movimentações, vagas, carteiras e acessos ficam no portal.
 */
import { useEffect, useMemo, useState } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';

import { Indicador } from '@/components/modulos/comum';
import { abrirLoja, abrirPessoa } from '@/components/modulos/quadro_hc/navegar';
import { CartaoLoja, NumerosHC } from '@/components/modulos/quadro_hc/pecas';
import { BarraDetalhe, BotaoIcone, CabecalhoGrande, Campo, Cartao, Chip, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Icone, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { estado, marca } from '@/constants/theme';
import { useOpcoesQuadroHC, useQuadroHC } from '@/hooks/modulos/quadro_hc';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { CarteiraHC, FiltroQuadro, Quadro, Rapido } from '@/services/modulos/quadro_hc';
import { abrirNoPortal } from '@/utils/links';

const RAPIDOS: { valor: Rapido; rotulo: string }[] = [
  { valor: 'alerta', rotulo: 'Com alerta' },
  { valor: 'vaga', rotulo: 'Vagas' },
  { valor: 'novo', rotulo: 'Novos' },
  { valor: 'experiencia', rotulo: 'Experiência em 7 dias' },
  { valor: 'demissao', rotulo: 'Demissão no cadastro' },
  { valor: 'ajustado', rotulo: 'Ajustados' },
];

const ROTULO_PAPEL = { SUPERADMIN: 'Superadmin', GERENCIAR: 'Pode gerenciar', VER: 'Só visualização', '': '' } as const;

export default function QuadroHC() {
  const [texto, setTexto] = useState('');
  const [busca, setBusca] = useState('');
  const [carteira, setCarteira] = useState<number | null>(null);
  const [cargo, setCargo] = useState('');
  const [rapido, setRapido] = useState<Rapido | ''>('');
  const [hc, setHc] = useState<'falta' | 'sobra' | ''>('');
  const [semGerente, setSemGerente] = useState(false);

  // Busca espera a pessoa parar de digitar (350 ms).
  useEffect(() => {
    const t = setTimeout(() => setBusca(texto.trim()), 350);
    return () => clearTimeout(t);
  }, [texto]);

  const filtro = useMemo<FiltroQuadro>(
    () => ({ busca: busca || undefined, carteira, cargo: cargo || undefined, rapido, hc, sem_gerente: semGerente }),
    [busca, carteira, cargo, rapido, hc, semGerente],
  );
  const { data, isPending, isError, error, refetch, isRefetching, isPlaceholderData } = useQuadroHC(filtro);
  const opcoes = useOpcoesQuadroHC();
  const cargos = useMemo(() => [{ id: 0, nome: 'Todos os cargos' }, ...(opcoes.data?.cargos ?? []).map((c, i) => ({ id: i + 1, nome: c }))], [opcoes.data]);
  const idCargo = cargo ? (opcoes.data?.cargos.indexOf(cargo) ?? -1) + 1 : 0;
  const filtrando = Boolean(busca || carteira !== null || cargo || rapido || hc || semGerente);

  function limpar() {
    setTexto('');
    setBusca('');
    setCarteira(null);
    setCargo('');
    setRapido('');
    setHc('');
    setSemGerente(false);
  }

  return (
    <Tela>
      <BarraDetalhe direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/quadro-hc/')} />} />
      <ScrollView
        keyboardDismissMode="on-drag"
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={{ paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={isRefetching && !isPlaceholderData} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}>
        <CabecalhoGrande titulo="Quadro de HC" subtitulo={data ? ROTULO_PAPEL[data.papel] || 'Loja a loja' : 'Loja a loja'} />

        {isPending ? (
          <View className="px-5">
            <EsqueletoLista quantos={5} />
          </View>
        ) : isError || !data ? (
          <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
        ) : (
          <View className="gap-4">
            <Topo quadro={data} />

            {/* Filtros */}
            <View className="gap-3">
              <View className="px-5">
                <Campo icone="search" placeholder="Nome, login, cargo ou cidade" value={texto} onChangeText={setTexto} returnKeyType="search" autoCorrect={false} autoCapitalize="none" clearButtonMode="while-editing" />
              </View>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
                <Chip rotulo="Todas as carteiras" ativo={carteira === null} onPress={() => setCarteira(null)} />
                {data.todas_carteiras.map((c) => (
                  <Chip key={c.id} rotulo={`${c.nome} · ${c.total}`} ativo={carteira === c.id} onPress={() => setCarteira(carteira === c.id ? null : c.id)} />
                ))}
              </ScrollView>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
                <Chip rotulo="Com falta" ativo={hc === 'falta'} onPress={() => setHc(hc === 'falta' ? '' : 'falta')} />
                <Chip rotulo="Com sobra" ativo={hc === 'sobra'} onPress={() => setHc(hc === 'sobra' ? '' : 'sobra')} />
                <Chip rotulo={`Sem gerente${data.kpis.sem_gerente ? ` · ${data.kpis.sem_gerente}` : ''}`} ativo={semGerente} onPress={() => setSemGerente(!semGerente)} />
                {RAPIDOS.filter((r) => r.valor !== 'demissao' || data.kpis.demissao_antiga).map((r) => (
                  <Chip
                    key={r.valor}
                    rotulo={r.valor === 'demissao' ? `${r.rotulo} · ${data.kpis.demissao_antiga}` : r.rotulo}
                    ativo={rapido === r.valor}
                    onPress={() => setRapido(rapido === r.valor ? '' : r.valor)}
                  />
                ))}
              </ScrollView>
              <View className="px-5">
                <Seletor rotulo="Cargo" placeholder="Todos os cargos" itens={cargos} valor={idCargo} aoEscolher={(id) => setCargo(id ? (opcoes.data?.cargos[id - 1] ?? '') : '')} carregando={opcoes.isPending} />
              </View>
              {filtrando ? (
                <View className="flex-row items-center justify-between px-5">
                  <Texto variante="legenda" tom="suave">
                    {data.resultados != null ? `${data.resultados} ${data.resultados === 1 ? 'resultado' : 'resultados'}` : ''}
                  </Texto>
                  <Pressionavel onPress={limpar} accessibilityRole="button" hitSlop={8}>
                    <Texto variante="legenda" tom="marca">
                      Limpar filtros
                    </Texto>
                  </Pressionavel>
                </View>
              ) : null}
            </View>

            {/* Árvore */}
            <View className="gap-5 px-5" style={{ opacity: isPlaceholderData ? 0.6 : 1 }}>
              {data.carteiras.length ? (
                data.carteiras.map((c, i) => <SecaoCarteira key={c.id} carteira={c} filtro={filtro} indice={i} abertaDeInicio={filtrando || data.carteiras.length === 1} />)
              ) : (
                <EstadoVazio icone="search-outline" titulo="Nada encontrado" texto="Nenhuma loja ou pessoa com esses filtros." />
              )}
              {!filtrando ? <Avulsos quadro={data} /> : null}
            </View>
          </View>
        )}
      </ScrollView>
    </Tela>
  );
}

function Topo({ quadro }: { quadro: Quadro }) {
  const k = quadro.kpis;
  const h = quadro.hc;
  return (
    <View className="gap-3 px-5">
      <View className="flex-row flex-wrap gap-3">
        <Indicador rotulo="Colaboradores" valor={String(k.total)} icone="people-outline" />
        <Indicador rotulo="Lojas" valor={String(k.lojas)} icone="storefront-outline" cor={estado.info} />
        <Indicador rotulo="Vagas em aberto" valor={String(k.vagas)} icone="person-add-outline" cor={estado.alerta} detalhe={k.vagas_previstas ? `${k.vagas_previstas} pelo HC previsto` : undefined} />
        <Indicador rotulo="Lojas sem gerente" valor={String(k.sem_gerente)} icone="warning-outline" cor={k.sem_gerente ? estado.perigo : estado.sucesso} />
      </View>
      <Cartao style={{ padding: 16, gap: 12 }}>
        <View className="flex-row items-center justify-between gap-2">
          <Texto variante="titulo3">Previsto × real</Texto>
          {h.lojas_com_previsto ? (
            <Texto variante="legenda" tom="suave">
              {h.lojas_com_previsto} {h.lojas_com_previsto === 1 ? 'loja' : 'lojas'} com previsto
            </Texto>
          ) : null}
        </View>
        {h.lojas_com_previsto ? (
          <>
            <NumerosHC real={h.real} previsto={h.previsto} diferenca={h.real - h.previsto} />
            <View className="flex-row flex-wrap gap-1.5">
              <Selo texto={`Faltam ${h.falta} em ${h.lojas_com_falta} ${h.lojas_com_falta === 1 ? 'loja' : 'lojas'}`} cor={h.falta ? estado.perigo : estado.sucesso} />
              {h.sobra ? <Selo texto={`Sobram ${h.sobra} em ${h.lojas_com_sobra} ${h.lojas_com_sobra === 1 ? 'loja' : 'lojas'}`} cor={estado.alerta} /> : null}
            </View>
          </>
        ) : (
          <Texto variante="legenda" tom="suave">
            Nenhuma loja tem HC previsto ainda. {quadro.pode_gerenciar ? 'Abra uma loja para cadastrar o quadro ideal.' : ''}
          </Texto>
        )}
      </Cartao>
      {k.demissao_antiga || k.para_hoje ? (
        <View className="gap-2">
          {k.demissao_antiga ? <Aviso cor={estado.perigo} icone="calendar-outline" texto={`${k.demissao_antiga} trabalham, mas o cadastro tem demissão no passado. Confira o cadastro.`} /> : null}
          {k.para_hoje ? <Aviso cor={estado.alerta} icone="notifications-outline" texto={`${k.para_hoje} movimentação(ões) para hoje ou atrasada(s): aplique no portal.`} /> : null}
        </View>
      ) : null}
    </View>
  );
}

function Aviso({ cor, icone, texto }: { cor: string; icone: 'calendar-outline' | 'notifications-outline'; texto: string }) {
  return (
    <Cartao style={{ padding: 12, flexDirection: 'row', alignItems: 'center', gap: 10, borderLeftWidth: 4, borderLeftColor: cor }}>
      <Icone nome={icone} tamanho={20} cor={cor} />
      <Texto variante="legenda" className="flex-1">
        {texto}
      </Texto>
    </Cartao>
  );
}

function SecaoCarteira({ carteira: c, filtro, indice, abertaDeInicio }: { carteira: CarteiraHC; filtro: FiltroQuadro; indice: number; abertaDeInicio: boolean }) {
  const cores = useCores();
  // Fechada por padrão (a árvore cabe na tela); com filtro abre sozinha. O toque da pessoa manda.
  const [escolha, setEscolha] = useState<boolean | null>(null);
  const aberto = escolha ?? abertaDeInicio;
  const comFalta = c.lojas.filter((l) => l.situacao_hc === 'falta').length;
  const semGerente = c.lojas.filter((l) => l.sem_gerente).length;
  return (
    <Entrada indice={indice} style={{ gap: 10 }}>
      <Pressionavel onPress={() => setEscolha(!aberto)} accessibilityRole="button" accessibilityState={{ expanded: aberto }} escala={0.98}>
        <View className="flex-row items-center gap-3">
          <View className="flex-1 gap-0.5">
            <Texto variante="titulo3" numberOfLines={1}>
              {c.nome}
            </Texto>
            <Texto variante="legenda" tom="suave" numberOfLines={2}>
              {[c.coordenacao ? `Coordenação ${c.coordenacao}` : '', `${c.lojas.length} ${c.lojas.length === 1 ? 'loja' : 'lojas'}`, `${c.total} pessoas`, c.vagas ? `${c.vagas} vaga(s)` : '']
                .filter(Boolean)
                .join(' · ')}
            </Texto>
            {comFalta || semGerente ? (
              <Texto variante="legenda" style={{ color: estado.perigo }}>
                {[comFalta ? `${comFalta} com falta` : '', semGerente ? `${semGerente} sem gerente` : ''].filter(Boolean).join(' · ')}
              </Texto>
            ) : null}
          </View>
          <Icone nome={aberto ? 'chevron-up' : 'chevron-down'} tamanho={20} cor={cores.texto3} />
        </View>
      </Pressionavel>
      {aberto ? (
        <View className="gap-2.5">
          {c.lojas.map((l) => (
            <CartaoLoja key={l.id} loja={l} onPress={() => abrirLoja(l.id, filtro)} />
          ))}
        </View>
      ) : null}
    </Entrada>
  );
}

/** Sem loja no cadastro e fora do quadro por ajuste manual (os dois blocos do fim da tela web). */
function Avulsos({ quadro }: { quadro: Quadro }) {
  if (!quadro.sem_loja.length && !quadro.fora.length) return null;
  return (
    <View className="gap-4">
      {quadro.sem_loja.length ? (
        <Cartao style={{ padding: 16, gap: 10, borderLeftWidth: 4, borderLeftColor: estado.perigo }}>
          <Texto variante="corpoForte">Sem loja no cadastro ({quadro.sem_loja.length})</Texto>
          <View className="flex-row flex-wrap gap-2">
            {quadro.sem_loja.map((p) => (
              <Chip key={p.id} rotulo={`${p.nome} · ${p.cargo || 'sem cargo'}`} ativo={false} onPress={() => abrirPessoa(p.id)} />
            ))}
          </View>
        </Cartao>
      ) : null}
      {quadro.fora.length ? (
        <Cartao style={{ padding: 16, gap: 10 }}>
          <Texto variante="corpoForte">Fora do quadro por ajuste manual ({quadro.fora.length})</Texto>
          <View className="flex-row flex-wrap gap-2">
            {quadro.fora.map((p) => (
              <Chip key={p.id} rotulo={`${p.nome} · ${p.loja}`} ativo={false} onPress={() => abrirPessoa(p.id)} />
            ))}
          </View>
        </Cartao>
      ) : null}
    </View>
  );
}
