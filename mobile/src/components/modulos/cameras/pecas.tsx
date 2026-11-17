/** Peças do módulo Câmeras: situação da loja, imagem da câmera, cartão de alerta e moldura das telas de detalhe. */
import { Image } from 'expo-image';
import { router, type Href } from 'expo-router';
import { type ReactNode, useState } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { BarraDetalhe, BotaoIcone, Cartao, EstadoErro, Icone, type NomeIcone, Selo, Tela, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { AlertaResumo, Situacao } from '@/services/modulos/cameras';
import { comAlfa, tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

import { EsqueletoBloco } from '../comum';

export const COR_SITUACAO: Record<Situacao, string> = {
  online: estado.sucesso,
  offline: estado.perigo,
  sem_agente: estado.alerta,
  sem_equipamento: '#6B7280',
};

export const COR_STATUS_ALERTA: Record<AlertaResumo['status'], string> = {
  NOVO: estado.perigo,
  ANALISE: estado.alerta,
  CONFIRMADO: '#8E44AD',
  FALSO: '#6B7280',
};

export const ICONE_TIPO: Record<AlertaResumo['tipo'], NomeIcone> = {
  ITEM_SUMIU: 'phone-portrait-outline',
  FORA_HORARIO: 'moon-outline',
  PERMANENCIA: 'time-outline',
};

export function SeloSituacao({ situacao, rotulo }: { situacao: Situacao; rotulo: string }) {
  return <Selo texto={rotulo} cor={COR_SITUACAO[situacao] ?? '#6B7280'} />;
}

/**
 * A última imagem da câmera. O link assinado muda a cada consulta; só troca a imagem
 * quando a foto em si mudou (`em`), para não piscar nem baixar de novo a mesma foto.
 */
export function FotoCamera({ url, em, proporcao = 16 / 9, rotulo, vazio = 'Aguardando imagem', arredondar = 12 }: {
  url: string | null;
  em?: string | null;
  proporcao?: number;
  rotulo: string;
  vazio?: string;
  arredondar?: number;
}) {
  const cores = useCores();
  const chave = url ? (em ?? url) : null;
  const [atual, setAtual] = useState({ url, chave });
  if (chave !== atual.chave) setAtual({ url, chave });
  return (
    <View style={{ aspectRatio: proporcao, borderRadius: arredondar, overflow: 'hidden', backgroundColor: cores.superficie3 }}>
      {atual.url ? (
        <Image source={{ uri: atual.url }} style={{ flex: 1 }} contentFit="cover" transition={200} cachePolicy="memory" accessibilityLabel={rotulo} />
      ) : (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 6, padding: 8 }}>
          <Icone nome="videocam-off-outline" tamanho={22} cor={cores.texto3} />
          <Texto variante="legenda" tom="fraco" numberOfLines={2} style={{ textAlign: 'center' }}>
            {vazio}
          </Texto>
        </View>
      )}
    </View>
  );
}

export function irParaAlerta(id: number) {
  router.push({ pathname: '/modulo/cameras/alerta/[id]', params: { id: String(id) } } as unknown as Href);
}

export function irParaLoja(id: number) {
  router.push({ pathname: '/modulo/cameras/loja/[id]', params: { id: String(id) } } as unknown as Href);
}

/** Alerta na lista: miniatura, o que foi, onde, quando e em que pé está. */
export function CartaoAlerta({ alerta: a, mostrarLoja = true }: { alerta: AlertaResumo; mostrarLoja?: boolean }) {
  const cores = useCores();
  const cor = COR_STATUS_ALERTA[a.status] ?? '#6B7280';
  return (
    <Cartao acessibilidade={`${a.tipo_rotulo} em ${a.loja.nome}`} onPress={() => irParaAlerta(a.id)} style={{ padding: 12, flexDirection: 'row', gap: 12 }}>
      <View style={{ width: 92 }}>
        {a.foto ? (
          <FotoCamera url={a.foto} rotulo={`Foto do alerta ${a.id}`} proporcao={4 / 3} arredondar={10} />
        ) : (
          <View style={{ aspectRatio: 4 / 3, borderRadius: 10, backgroundColor: comAlfa(cor, 0.12), alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome={ICONE_TIPO[a.tipo] ?? 'alert-circle-outline'} tamanho={24} cor={cor} />
          </View>
        )}
      </View>
      <View className="flex-1 gap-1">
        <Texto variante="corpoForte" numberOfLines={2}>
          {a.tipo_rotulo}
        </Texto>
        <Texto variante="legenda" tom="suave" numberOfLines={1}>
          {[mostrarLoja ? a.loja.nome : null, a.canal].filter(Boolean).join(' · ') || 'Câmera não informada'}
        </Texto>
        <View className="flex-row flex-wrap items-center gap-2">
          <Selo texto={a.status_rotulo} cor={cor} />
          <Texto variante="legenda" tom="fraco">
            {tempoRelativo(a.criado_em)} · {a.confianca}%
            {a.repeticoes > 1 ? ` · ${a.repeticoes}x` : ''}
          </Texto>
        </View>
      </View>
      <View style={{ justifyContent: 'center' }}>
        <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
      </View>
    </Cartao>
  );
}

/** Cartão com título pequeno (ícone + rótulo), entrando em cascata. */
export function Secao({ icone, titulo, cor, indice = 0, direita, children }: {
  icone: NomeIcone;
  titulo: string;
  cor?: string;
  indice?: number;
  direita?: ReactNode;
  children: ReactNode;
}) {
  const cores = useCores();
  return (
    <Animated.View entering={FadeInDown.delay(Math.min(indice, 6) * 60).duration(320)}>
      <Cartao style={{ padding: 16, gap: 10, borderColor: cor ? comAlfa(cor, 0.35) : undefined }}>
        <View className="flex-row items-center gap-2">
          <Icone nome={icone} tamanho={18} cor={cor ?? cores.texto2} />
          <Texto variante="rotulo" tom="suave" className="flex-1">
            {titulo}
          </Texto>
          {direita}
        </View>
        {children}
      </Cartao>
    </Animated.View>
  );
}

/** Aviso em faixa colorida (módulo desligado, saúde do agente). */
export function Faixa({ icone, cor, texto }: { icone: NomeIcone; cor: string; texto: string }) {
  const cores = useCores();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10, padding: 14, borderRadius: 14, backgroundColor: comAlfa(cor, 0.12) }}>
      <Icone nome={icone} tamanho={20} cor={cor} />
      <Texto variante="legenda" style={{ color: cores.texto }} className="flex-1">
        {texto}
      </Texto>
    </View>
  );
}

/** Moldura das telas de detalhe: voltar, abrir no portal, rolagem com puxar-para-atualizar. */
export function TelaDetalheCameras({ titulo, caminhoWeb, carregando, erro, aoAtualizar, atualizando = false, children }: {
  titulo: string;
  caminhoWeb?: string;
  carregando: boolean;
  erro: unknown;
  aoAtualizar: () => void;
  atualizando?: boolean;
  children?: ReactNode;
}) {
  return (
    <Tela>
      <BarraDetalhe
        titulo={titulo}
        direita={caminhoWeb ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(caminhoWeb)} /> : undefined}
      />
      <ScrollView
        contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 48 }}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={atualizando} onRefresh={aoAtualizar} tintColor={marca[500]} colors={[marca[500]]} />}>
        {carregando ? (
          <View className="gap-4">
            <EsqueletoBloco linhas={3} />
            <EsqueletoBloco linhas={4} />
            <EsqueletoBloco linhas={2} />
          </View>
        ) : erro ? (
          <EstadoErro mensagem={mensagemDoErro(erro)} aoTentar={aoAtualizar} />
        ) : (
          children
        )}
      </ScrollView>
    </Tela>
  );
}

/** "AAAA-MM-DD" do dia local do aparelho, com deslocamento em dias. */
export function diaIso(deslocamento = 0, base = new Date()) {
  const d = new Date(base.getFullYear(), base.getMonth(), base.getDate() + deslocamento);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** "2026-11-17" → "17/11". */
export function diaCurto(iso: string) {
  const [, m, d] = iso.split('-');
  return `${d}/${m}`;
}
