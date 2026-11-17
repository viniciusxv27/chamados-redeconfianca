import { useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { CartaoAlerta, COR_SITUACAO, FotoCamera, Secao, SeloSituacao, TelaDetalheCameras } from '@/components/modulos/cameras/pecas';
import { LinhaValor } from '@/components/modulos/comum';
import { Botao, Cartao, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useFocado, useLojaCameras } from '@/hooks/modulos/cameras';
import { useCores } from '@/providers/tema';
import type { CameraLoja } from '@/services/modulos/cameras';
import { dataHora, tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

/** A loja: câmeras com a última imagem (atualiza a cada 10 s com a tela aberta), saúde do agente e últimos alertas. */
export default function LojaCameras() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const focado = useFocado();
  const { data, isPending, error, refetch, isRefetching } = useLojaCameras(Number(id), focado);

  return (
    <TelaDetalheCameras
      titulo={data?.loja ?? 'Loja'}
      caminhoWeb={data?.caminho_web}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}>
      {data ? (
        <>
          <Animated.View entering={FadeInDown.duration(320)}>
            <Cartao style={{ padding: 18, gap: 12 }}>
              <View className="gap-1">
                <Texto variante="titulo2">{data.loja}</Texto>
                {data.coordenador ? (
                  <Texto variante="legenda" tom="suave">
                    {data.coordenador}
                  </Texto>
                ) : null}
              </View>
              <View className="flex-row flex-wrap items-center gap-2">
                <SeloSituacao situacao={data.situacao} rotulo={data.situacao_rotulo} />
                {data.ultimo_contato && !data.online ? (
                  <Texto variante="legenda" tom="fraco">
                    visto pela última vez {tempoRelativo(data.ultimo_contato)} ({dataHora(data.ultimo_contato)})
                  </Texto>
                ) : null}
              </View>
              {data.erro_agente ? (
                <Texto variante="legenda" tom="perigo">
                  {data.erro_agente}
                </Texto>
              ) : null}
              <View>
                <LinhaValor rotulo="Câmeras" valor={String(data.cameras)} />
                <LinhaValor rotulo="Horário da loja" valor={`${data.abre_as} às ${data.fecha_as}`} />
                {data.versao_agente ? <LinhaValor rotulo="Versão do agente" valor={data.versao_agente} /> : null}
                <LinhaValor rotulo="Alertas novos" valor={String(data.alertas_novos)} cor={data.alertas_novos ? estado.perigo : undefined} />
              </View>
              <Botao
                titulo="Ver ao vivo no portal"
                icone="play-circle-outline"
                variante={data.ao_vivo.disponivel ? 'primario' : 'secundario'}
                onPress={() => abrirNoPortal(data.caminho_web)}
              />
              <Texto variante="legenda" tom="fraco">
                {data.ao_vivo.disponivel
                  ? 'O vídeo ao vivo abre no navegador (vai direto da loja até você). Entre no portal se ele pedir.'
                  : `Ao vivo indisponível agora: ${data.ao_vivo.motivo ?? 'tente pelo portal.'}`}
              </Texto>
            </Cartao>
          </Animated.View>

          {data.problemas.length || data.saude.length ? (
            <Secao icone="medkit-outline" titulo="Saúde do agente" cor={data.problemas.length ? estado.alerta : undefined} indice={1}
              direita={data.saude_em ? <Texto variante="legenda" tom="fraco">{tempoRelativo(data.saude_em)}</Texto> : undefined}>
              {data.problemas.map((p, i) => (
                <Texto key={i} variante="legenda" style={{ color: p.nivel === 'erro' ? estado.perigo : estado.alerta }}>
                  {p.texto}
                </Texto>
              ))}
              {data.saude.map((m) => (
                <LinhaValor key={m.rotulo} rotulo={m.rotulo} valor={m.valor} />
              ))}
            </Secao>
          ) : null}

          <Secao icone="videocam-outline" titulo={`Câmeras (${data.cameras_lista.length})`} indice={2}>
            {data.cameras_lista.length ? (
              <View className="flex-row flex-wrap" style={{ gap: 10 }}>
                {data.cameras_lista.map((c) => (
                  <Camera key={c.id} camera={c} />
                ))}
              </View>
            ) : (
              <Texto variante="legenda" tom="suave">
                Nenhuma câmera nesta loja.
              </Texto>
            )}
            <Texto variante="legenda" tom="fraco">
              {data.online ? 'A imagem chega em alguns segundos e atualiza enquanto a tela está aberta.' : 'Sem contato com o agente: as imagens voltam quando ele se conectar.'}
            </Texto>
          </Secao>

          <Secao icone="notifications-outline" titulo="Últimos alertas" indice={3}>
            {data.alertas.length ? (
              <View className="gap-3">
                {data.alertas.map((a) => (
                  <CartaoAlerta key={a.id} alerta={a} mostrarLoja={false} />
                ))}
              </View>
            ) : (
              <Texto variante="legenda" tom="suave">
                Nenhum alerta nesta loja.
              </Texto>
            )}
          </Secao>
        </>
      ) : null}
    </TelaDetalheCameras>
  );
}

function Camera({ camera: c }: { camera: CameraLoja }) {
  const cores = useCores();
  const cor = c.imagem_ok == null ? cores.texto3 : c.imagem_ok ? COR_SITUACAO.online : COR_SITUACAO.offline;
  return (
    <View style={{ flexBasis: '47%', flexGrow: 1, gap: 6 }}>
      <FotoCamera url={c.foto} em={c.foto_em} rotulo={c.titulo} vazio={c.imagem_ok === false ? 'Sem imagem' : 'Aguardando imagem'} />
      <View className="flex-row items-center gap-2">
        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: cor }} />
        <Texto variante="legenda" style={{ color: cores.texto }} className="flex-1" numberOfLines={1}>
          {c.titulo}
        </Texto>
      </View>
      <Texto variante="legenda" tom="fraco" numberOfLines={2}>
        {c.erro
          ? c.erro
          : [c.areas ? `${c.areas} ${c.areas === 1 ? 'área' : 'áreas'}` : 'sem área marcada', c.analisando ? null : 'sem análise', c.foto_em ? tempoRelativo(c.foto_em) : null]
              .filter(Boolean)
              .join(' · ')}
      </Texto>
    </View>
  );
}
