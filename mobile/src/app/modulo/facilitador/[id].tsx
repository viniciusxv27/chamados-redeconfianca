import { useLocalSearchParams } from 'expo-router';
import { Share, View } from 'react-native';

import { LinhaValor, TelaModulo } from '@/components/modulos/comum';
import { COR_FACILITADOR, IconeRecurso, legendaDoRecurso, useAbrir } from '@/components/modulos/facilitador/pecas';
import { BotaoIcone, Botao, Cartao, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useRecurso } from '@/hooks/modulos/facilitador';
import { tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

/** Um item do Facilitador: a explicação e o botão de abrir (link, página ou arquivo). */
export default function DetalheRecurso() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const recursoId = Number(id) || 0;
  const { data: r, isPending, error, refetch, isRefetching } = useRecurso(recursoId);
  const { executar, abrindo, qual } = useAbrir();
  const abrindoArquivo = abrindo && qual?.como === 'arquivo';
  const abrindoPrincipal = abrindo && !qual?.como;

  return (
    <TelaModulo
      titulo={r?.titulo ?? 'Facilitador'}
      subtitulo={r?.categoria ?? 'Facilitador'}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      esqueleto="detalhe"
      direita={
        r?.tipo === 'link' && r.url ? (
          <BotaoIcone icone="share-outline" rotulo="Compartilhar link" onPress={() => void Share.share({ message: r.url ?? '' })} />
        ) : undefined
      }>
      {r ? (
        <>
          <Cartao style={{ padding: 18, gap: 16 }}>
            <View className="flex-row items-center gap-4">
              <IconeRecurso recurso={r} tamanho={56} />
              <View className="flex-1 gap-1">
                <Texto variante="corpoForte" numberOfLines={2}>
                  {r.tipo === 'link' ? 'Link' : r.pagina ? 'Página' : (r.nome_arquivo ?? 'Arquivo')}
                </Texto>
                <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                  {legendaDoRecurso(r)}
                </Texto>
              </View>
            </View>
            {r.oculto ? <Selo texto="Oculto: só quem gerencia vê" cor={estado.alerta} /> : null}
            <Botao
              titulo={r.tipo === 'link' ? 'Abrir link' : r.pagina ? 'Abrir página' : 'Abrir arquivo'}
              icone={r.tipo === 'link' ? 'open-outline' : r.pagina ? 'globe-outline' : 'document-outline'}
              tamanho="lg"
              carregando={abrindoPrincipal}
              onPress={() => executar(r.id)}
              style={{ backgroundColor: COR_FACILITADOR, boxShadow: '0px 10px 24px rgba(20,184,166,0.3)' }}
            />
            {r.pagina ? <Botao titulo="Baixar o arquivo" icone="download-outline" variante="secundario" carregando={abrindoArquivo} onPress={() => executar(r.id, 'arquivo')} /> : null}
          </Cartao>

          {r.descricao ? (
            <Cartao style={{ padding: 18, gap: 8 }}>
              <Texto variante="rotulo" tom="fraco">
                Como usar
              </Texto>
              <Texto selectable>{r.descricao}</Texto>
            </Cartao>
          ) : null}

          <Cartao style={{ paddingHorizontal: 18, paddingVertical: 8 }}>
            {r.tipo === 'link' && r.dominio ? <LinhaValor rotulo="Site" valor={r.dominio} /> : null}
            {r.categoria ? <LinhaValor rotulo="Categoria" valor={r.categoria} /> : null}
            {r.atualizado_em ? <LinhaValor rotulo="Atualizado" valor={tempoRelativo(r.atualizado_em)} /> : null}
            {r.acessos != null ? <LinhaValor rotulo={r.tipo === 'link' ? 'Cliques' : 'Downloads'} valor={String(r.acessos)} /> : null}
          </Cartao>

          <Botao titulo="Ver no portal" icone="desktop-outline" variante="fantasma" onPress={() => void abrirNoPortal(r.caminho_web)} />
        </>
      ) : null}
    </TelaModulo>
  );
}
