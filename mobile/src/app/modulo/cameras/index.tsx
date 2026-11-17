import { type Href, router } from 'expo-router';
import { useMemo, useState } from 'react';
import { View } from 'react-native';

import { CartaoAlerta, COR_SITUACAO, Faixa, FotoCamera, irParaLoja, Secao, SeloSituacao } from '@/components/modulos/cameras/pecas';
import { Indicador, TelaModulo } from '@/components/modulos/comum';
import { BotaoIcone, Campo, Cartao, Chip, Entrada, EstadoVazio, Icone, type NomeIcone, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useFocado, useInicioCameras } from '@/hooks/modulos/cameras';
import { useCores } from '@/providers/tema';
import type { LojaResumo } from '@/services/modulos/cameras';
import { comAlfa, tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

type Filtro = 'todas' | 'online' | 'sem_contato' | 'alerta' | 'atencao';

const FILTROS: { valor: Filtro; rotulo: string }[] = [
  { valor: 'todas', rotulo: 'Todas' },
  { valor: 'online', rotulo: 'Online' },
  { valor: 'sem_contato', rotulo: 'Sem contato' },
  { valor: 'alerta', rotulo: 'Com alerta' },
  { valor: 'atencao', rotulo: 'Precisam de atenção' },
];

function semAcento(t: string) {
  return t
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

/** O mosaico da web em lista: situação de cada loja, a última imagem e os alertas para conferir. */
export default function Cameras() {
  const focado = useFocado();
  const { data, isPending, error, refetch, isRefetching } = useInicioCameras(focado);
  const [filtro, setFiltro] = useState<Filtro>('todas');
  const [busca, setBusca] = useState('');

  const lojas = useMemo(() => {
    const termo = semAcento(busca.trim());
    return (data?.lojas ?? []).filter((l) => {
      if (termo && !semAcento(`${l.loja} ${l.coordenador ?? ''}`).includes(termo)) return false;
      if (filtro === 'online') return l.online;
      if (filtro === 'sem_contato') return l.tem_equipamento && !l.online;
      if (filtro === 'alerta') return l.alertas_novos > 0;
      if (filtro === 'atencao') return l.problemas.length > 0;
      return true;
    });
  }, [data, filtro, busca]);

  return (
    <TelaModulo
      titulo="Câmeras"
      subtitulo="Lojas, alertas e indicadores"
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data?.caminho_web ?? '/cameras/')} />}>
      {data ? (
        <>
          {!data.ativo ? (
            <Faixa icone="power-outline" cor={estado.alerta} texto="Módulo desligado: as lojas não enviam imagem nem alerta até ligar na Configuração do portal." />
          ) : null}
          {data.sem_agente > 0 ? (
            <Faixa icone="cloud-offline-outline" cor={estado.info} texto={`${data.sem_agente} de ${data.total_lojas} lojas sem imagem porque o agente ainda não foi instalado.`} />
          ) : null}

          <View className="flex-row flex-wrap gap-3">
            <Indicador rotulo="Lojas online" valor={`${data.online}/${data.total_lojas}`} icone="wifi-outline" cor={estado.sucesso} />
            <Indicador rotulo="Câmeras" valor={String(data.total_cameras)} icone="videocam-outline" cor={estado.info} />
            <Indicador rotulo="Alertas novos" valor={String(data.novos)} icone="alert-circle-outline" cor={data.novos ? estado.perigo : '#6B7280'} />
            <Indicador rotulo="Precisam de atenção" valor={String(data.com_problema)} icone="medkit-outline" cor={data.com_problema ? estado.alerta : '#6B7280'} />
          </View>

          <View className="flex-row gap-3">
            <Atalho icone="notifications-outline" titulo="Alertas" detalhe={data.novos ? `${data.novos} novos` : 'Nenhum novo'} cor={estado.perigo} aoTocar={() => router.push('/modulo/cameras/alertas' as Href)} />
            <Atalho icone="stats-chart-outline" titulo="Indicadores" detalhe="Porta, matinal e mesa" cor="#6366F1" aoTocar={() => router.push('/modulo/cameras/indicadores' as Href)} />
          </View>

          <Secao icone="notifications-outline" titulo="Para conferir" cor={data.recentes.length ? estado.perigo : undefined}>
            {data.recentes.length ? (
              <View className="gap-3">
                {data.recentes.map((a) => (
                  <CartaoAlerta key={a.id} alerta={a} />
                ))}
              </View>
            ) : (
              <Texto variante="legenda" tom="suave">
                Nenhum alerta aberto.
              </Texto>
            )}
          </Secao>

          <Texto variante="rotulo" tom="fraco" className="mt-1">
            Lojas ({lojas.length})
          </Texto>
          <Campo icone="search" placeholder="Buscar loja ou coordenação" value={busca} onChangeText={setBusca} autoCorrect={false} clearButtonMode="while-editing" />
          <View className="flex-row flex-wrap gap-2">
            {FILTROS.map((f) => (
              <Chip key={f.valor} rotulo={f.rotulo} ativo={filtro === f.valor} onPress={() => setFiltro(f.valor)} />
            ))}
          </View>
          {lojas.length === 0 ? (
            <EstadoVazio icone="storefront-outline" titulo={data.lojas.length ? 'Nenhuma loja neste filtro' : 'Nenhuma loja cadastrada'} texto={data.lojas.length ? 'Troque o filtro ou a busca.' : 'Importe a planilha das lojas pela Configuração do portal.'} />
          ) : (
            lojas.map((l, i) => (
              <Entrada key={l.id} indice={i}>
                <CartaoLoja loja={l} />
              </Entrada>
            ))
          )}
          <Texto variante="legenda" tom="fraco" style={{ textAlign: 'center' }}>
            A imagem chega enquanto esta tela está aberta e atualiza sozinha.
          </Texto>
        </>
      ) : null}
    </TelaModulo>
  );
}

function Atalho({ icone, titulo, detalhe, cor, aoTocar }: { icone: NomeIcone; titulo: string; detalhe: string; cor: string; aoTocar: () => void }) {
  return (
    <Cartao acessibilidade={titulo} onPress={aoTocar} style={{ flex: 1, padding: 14, gap: 8 }}>
      <View style={{ width: 34, height: 34, borderRadius: 10, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome={icone} tamanho={18} cor={cor} />
      </View>
      <Texto variante="corpoForte">{titulo}</Texto>
      <Texto variante="legenda" tom="suave" numberOfLines={1}>
        {detalhe}
      </Texto>
    </Cartao>
  );
}

function legendaSemImagem(l: LojaResumo) {
  if (!l.tem_equipamento) return 'Sem gravador';
  if (l.erro_agente) return l.erro_agente;
  if (!l.ultimo_contato) return 'Agente não instalado';
  if (!l.online) return 'Agente sem contato';
  return 'Aguardando imagem';
}

function CartaoLoja({ loja: l }: { loja: LojaResumo }) {
  const cores = useCores();
  return (
    <Cartao acessibilidade={`Loja ${l.loja}`} onPress={l.tem_equipamento ? () => irParaLoja(l.id) : undefined} style={{ padding: 12, gap: 10 }}>
      <View className="flex-row" style={{ gap: 12 }}>
        <View style={{ width: 124 }}>
          <FotoCamera url={l.foto} em={l.foto_em} rotulo={`Câmera principal de ${l.loja}`} vazio={legendaSemImagem(l)} arredondar={10} />
        </View>
        <View className="flex-1 gap-1">
          <View className="flex-row items-center gap-2">
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: COR_SITUACAO[l.situacao] }} />
            <Texto variante="corpoForte" className="flex-1" numberOfLines={2}>
              {l.loja}
            </Texto>
          </View>
          {l.coordenador ? (
            <Texto variante="legenda" tom="fraco" numberOfLines={1}>
              {l.coordenador}
            </Texto>
          ) : null}
          <SeloSituacao situacao={l.situacao} rotulo={l.situacao_rotulo} />
          <Texto variante="legenda" tom="suave" numberOfLines={1}>
            {l.cameras} {l.cameras === 1 ? 'câmera' : 'câmeras'}
            {l.ultimo_contato && !l.online ? ` · visto ${tempoRelativo(l.ultimo_contato)}` : ''}
          </Texto>
        </View>
        {l.tem_equipamento ? (
          <View style={{ justifyContent: 'center' }}>
            <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
          </View>
        ) : null}
      </View>
      {l.alertas_novos > 0 || l.problemas.length > 0 ? (
        <View className="gap-1">
          {l.alertas_novos > 0 ? (
            <Texto variante="legenda" style={{ color: estado.perigo }}>
              {l.alertas_novos === 1 ? '1 alerta novo' : `${l.alertas_novos} alertas novos`}
            </Texto>
          ) : null}
          {l.problemas.map((p, i) => (
            <Texto key={i} variante="legenda" style={{ color: p.nivel === 'erro' ? estado.perigo : estado.alerta }} numberOfLines={2}>
              {p.texto}
            </Texto>
          ))}
        </View>
      ) : null}
    </Cartao>
  );
}
