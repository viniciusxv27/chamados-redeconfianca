import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { CartaoDocumento } from '@/components/modulos/documentos/cartao-documento';
import { TelaModulo } from '@/components/modulos/comum';
import { BotaoIcone, Chip, Entrada, EstadoVazio, Segmentado } from '@/components/ui';
import { useDocumentos } from '@/hooks/modulos/documentos';
import type { SituacaoDocumentos } from '@/services/modulos/documentos';
import { abrirNoPortal } from '@/utils/links';

export default function Documentos() {
  const [situacao, setSituacao] = useState<SituacaoDocumentos>('todos');
  const [categoria, setCategoria] = useState<number | null>(null);
  const { data, isPending, error, refetch, isRefetching } = useDocumentos(situacao);

  const lista = (data?.documentos ?? []).filter((d) => categoria == null || d.categoria?.id === categoria);
  const textoVazio = situacao === 'pendentes' ? 'Nada pendente por aqui.' : situacao === 'assinados' ? 'Você ainda não assinou nenhum documento.' : undefined;

  return (
    <TelaModulo
      titulo="Documentos"
      subtitulo="Para assinar"
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={data?.administra ? <BotaoIcone icone="settings-outline" rotulo="Administrar no portal" onPress={() => abrirNoPortal('/documentos/admin/')} /> : undefined}>
      {data ? (
        <>
          <Segmentado
            opcoes={[
              { valor: 'todos', rotulo: 'Todos' },
              { valor: 'pendentes', rotulo: `Pendentes (${data.pendentes})` },
              { valor: 'assinados', rotulo: `Assinados (${data.assinados})` },
            ]}
            valor={situacao}
            aoMudar={(v) => {
              setSituacao(v);
              setCategoria(null);
            }}
          />
          {data.categorias.length > 1 ? (
            <View className="flex-row flex-wrap gap-2">
              <Chip rotulo="Todas" ativo={categoria == null} onPress={() => setCategoria(null)} />
              {data.categorias.map((c) => (
                <Chip key={c.id} rotulo={c.nome} ativo={categoria === c.id} onPress={() => setCategoria(c.id)} />
              ))}
            </View>
          ) : null}
          {lista.length === 0 ? (
            <EstadoVazio icone="folder-open-outline" titulo="Nenhum documento" texto={textoVazio ?? 'Quando um documento for enviado para você assinar, ele aparece aqui.'} />
          ) : (
            lista.map((d, i) => (
              <Entrada key={d.id} indice={i}>
                <CartaoDocumento
                  documento={d}
                  aoAbrir={() => router.push({ pathname: '/modulo/documentos/[id]', params: { id: String(d.id) } } as unknown as Href)}
                />
              </Entrada>
            ))
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}
