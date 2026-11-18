import { useMemo, useState } from 'react';
import { ScrollView, View } from 'react-native';

import { TelaModulo } from '@/components/modulos/comum';
import { CartaoRecurso } from '@/components/modulos/facilitador/pecas';
import { useBuscaAdiada } from '@/components/modulos/vendas/tela-lista';
import { BotaoIcone, Campo, Chip, Entrada, EstadoVazio, Segmentado, Texto } from '@/components/ui';
import { useFacilitador } from '@/hooks/modulos/facilitador';
import type { RecursoResumo, TipoRecurso } from '@/services/modulos/facilitador';
import { abrirNoPortal } from '@/utils/links';

type FiltroTipo = 'todos' | TipoRecurso;

/** Agrupa por categoria, na ordem em que vieram (a ordem do portal); sem categoria vai para "Outros". */
function agrupar(itens: RecursoResumo[]) {
  const grupos = new Map<string, { r: RecursoResumo; i: number }[]>();
  itens.forEach((r, i) => {
    const chave = r.categoria ?? 'Outros';
    grupos.set(chave, [...(grupos.get(chave) ?? []), { r, i }]);
  });
  return [...grupos.entries()];
}

/** Facilitador: arquivos e links úteis, com busca, categorias e tipo. */
export default function Facilitador() {
  const { texto, setTexto, busca } = useBuscaAdiada();
  const [categoria, setCategoria] = useState<string | undefined>(undefined);
  const [tipo, setTipo] = useState<FiltroTipo>('todos');
  const filtro = useMemo(() => ({ busca: busca || undefined, categoria, tipo: tipo === 'todos' ? undefined : tipo }), [busca, categoria, tipo]);
  const { data, isPending, error, refetch, isRefetching } = useFacilitador(filtro);

  const grupos = useMemo(() => agrupar(data?.recursos ?? []), [data]);
  const filtrando = !!busca || !!categoria || tipo !== 'todos';

  return (
    <TelaModulo
      titulo="Facilitador"
      subtitulo="Arquivos e links úteis"
      carregando={isPending}
      erro={data ? undefined : error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={data?.gerencia ? <BotaoIcone icone="add" rotulo="Cadastrar no portal" onPress={() => void abrirNoPortal('/facilitador/novo/')} /> : undefined}>
      <Campo
        icone="search"
        placeholder="Buscar arquivo ou link"
        value={texto}
        onChangeText={setTexto}
        returnKeyType="search"
        autoCorrect={false}
        clearButtonMode="while-editing"
      />
      <Segmentado
        opcoes={[
          { valor: 'todos', rotulo: 'Todos' },
          { valor: 'arquivo', rotulo: 'Arquivos' },
          { valor: 'link', rotulo: 'Links' },
        ]}
        valor={tipo}
        aoMudar={setTipo}
      />
      {data && data.categorias.length > 1 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 20 }} style={{ marginHorizontal: -20 }}>
          <Chip rotulo="Todas" ativo={!categoria} onPress={() => setCategoria(undefined)} />
          {data.categorias.map((c) => (
            <Chip key={c} rotulo={c} ativo={categoria?.toLowerCase() === c.toLowerCase()} onPress={() => setCategoria(c)} />
          ))}
        </ScrollView>
      ) : null}
      {data && data.recursos.length === 0 ? (
        <EstadoVazio
          icone="link-outline"
          titulo={filtrando ? 'Nada encontrado' : 'Nada por aqui ainda'}
          texto={filtrando ? 'Tente outra palavra, categoria ou tipo.' : 'Quando a empresa publicar arquivos e links úteis, eles aparecem aqui.'}
        />
      ) : (
        grupos.map(([nome, itens]) => (
          <View key={nome} style={{ gap: 16 }}>
            {grupos.length > 1 ? (
              <Texto variante="rotulo" tom="fraco" style={{ paddingHorizontal: 4 }}>
                {`${nome} · ${itens.length}`}
              </Texto>
            ) : null}
            {itens.map(({ r, i }) => (
              <Entrada key={r.id} indice={i}>
                <CartaoRecurso recurso={r} />
              </Entrada>
            ))}
          </View>
        ))
      )}
    </TelaModulo>
  );
}
