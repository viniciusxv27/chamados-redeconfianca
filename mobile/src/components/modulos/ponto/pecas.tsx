/** Peças do módulo Ponto: selos de status, cartão de solicitação, batidas do dia, datas sem fuso. */
import { router, type Href } from 'expo-router';
import { View } from 'react-native';

import { Cartao, Icone, type NomeIcone, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { Batida, SolicitacaoResumo, StatusSolicitacao, TipoSolicitacao } from '@/services/modulos/ponto';
import { comAlfa, tempoRelativo } from '@/utils/formatar';

export const AZUL_PONTO = '#3B82F6';

export const COR_SOLICITACAO: Record<StatusSolicitacao, string> = {
  ENVIADA: estado.info,
  EM_ANALISE: marca[500],
  DEVOLVIDA: estado.alerta,
  APROVADA: estado.sucesso,
  CANCELADA: '#6B7280',
};

export const ICONE_TIPO: Record<TipoSolicitacao, NomeIcone> = {
  AJUSTE: 'time-outline',
  ATESTADO: 'medkit-outline',
  DECLARACAO: 'document-text-outline',
};

export function SeloSolicitacao({ status, nome }: { status: StatusSolicitacao; nome: string }) {
  return <Selo texto={nome} cor={COR_SOLICITACAO[status] ?? '#6B7280'} />;
}

/** 'AAAA-MM-DD' → Date local (sem o deslize de fuso do new Date('AAAA-MM-DD'), que é UTC). */
export function diaLocal(iso: string) {
  const [a, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(a, (m || 1) - 1, d || 1);
}

/** 'AAAA-MM-DD' → '17/11' ou '17/11/2025' (outro ano). */
export function dataCurta(iso: string | null | undefined, comAno = false) {
  if (!iso) return '';
  const d = diaLocal(iso);
  const mesmoAno = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString('pt-BR', comAno || !mesmoAno ? { day: '2-digit', month: '2-digit', year: 'numeric' } : { day: '2-digit', month: '2-digit' });
}

/** 'AAAA-MM-DD' → 'segunda, 17/11'. */
export function diaPorExtenso(iso: string) {
  const d = diaLocal(iso);
  const semana = d.toLocaleDateString('pt-BR', { weekday: 'long' }).replace('-feira', '');
  return `${semana}, ${dataCurta(iso)}`;
}

export function isoDoDia(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function somarDias(iso: string, dias: number) {
  const d = diaLocal(iso);
  d.setDate(d.getDate() + dias);
  return isoDoDia(d);
}

/** Saldo em segundos → '+01:20' / '-00:35'. */
export function saldo(segundos: number | null | undefined) {
  if (segundos == null) return '—';
  const s = Math.abs(segundos);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${segundos < 0 ? '-' : '+'}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** As batidas do dia em pílulas: verde entrada, laranja saída; lápis quando foi editada na Sólides. */
export function Batidas({ eventos, vazio = 'Sem batidas' }: { eventos: Batida[]; vazio?: string }) {
  const cores = useCores();
  if (!eventos.length) {
    return (
      <Texto variante="legenda" tom="fraco">
        {vazio}
      </Texto>
    );
  }
  return (
    <View className="flex-row flex-wrap gap-2">
      {eventos.map((e, i) => {
        const cor = e.tipo === 'ENTRADA' ? estado.sucesso : marca[500];
        return (
          <View
            key={`${e.iso}-${i}`}
            accessibilityLabel={`${e.tipo === 'ENTRADA' ? 'Entrada' : 'Saída'} às ${e.hora}${e.editado ? ', editada' : ''}`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999, backgroundColor: comAlfa(cor, 0.12) }}>
            <Icone nome={e.tipo === 'ENTRADA' ? 'log-in-outline' : 'log-out-outline'} tamanho={14} cor={cor} />
            <Texto variante="legenda" style={{ color: cores.texto, fontFamily: fontes.seminegrito }}>
              {e.hora}
            </Texto>
            {e.editado ? <Icone nome="pencil" tamanho={11} cor={cores.texto3} /> : null}
          </View>
        );
      })}
    </View>
  );
}

/** Uma solicitação na lista (minhas ou caixa do DP). */
export function CartaoSolicitacao({ sol, mostrarPessoa = false }: { sol: SolicitacaoResumo; mostrarPessoa?: boolean }) {
  const cores = useCores();
  const cor = COR_SOLICITACAO[sol.status] ?? AZUL_PONTO;
  return (
    <Cartao
      onPress={() => router.push({ pathname: '/modulo/ponto/[id]', params: { id: String(sol.id) } } as unknown as Href)}
      acessibilidade={`${sol.titulo}, ${sol.status_nome}`}
      style={{ padding: 16, gap: 10 }}>
      <View className="flex-row items-start gap-3">
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={ICONE_TIPO[sol.tipo]} tamanho={20} cor={cor} />
        </View>
        <View className="flex-1 gap-1">
          <Texto variante="rotulo" tom="fraco">
            {sol.tipo_nome} · #{sol.id}
          </Texto>
          <Texto variante="corpoForte" numberOfLines={2}>
            {mostrarPessoa && sol.colaborador ? sol.colaborador.nome : sol.titulo}
          </Texto>
          {mostrarPessoa ? (
            <Texto variante="legenda" tom="suave" numberOfLines={1}>
              {[sol.colaborador?.setor, sol.periodo].filter(Boolean).join(' · ')}
            </Texto>
          ) : null}
        </View>
        <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
      </View>
      <View className="flex-row flex-wrap items-center gap-2">
        <SeloSolicitacao status={sol.status} nome={sol.status_nome} />
        {sol.anexos ? <Selo texto={`${sol.anexos} anexo${sol.anexos > 1 ? 's' : ''}`} cor={cores.texto3} ponto={false} /> : null}
        {sol.tipo === 'AJUSTE' && sol.status === 'APROVADA' && !sol.ajustado_no_ponto ? <Selo texto="Falta lançar no ponto" cor={estado.alerta} ponto={false} /> : null}
        <Texto variante="legenda" tom="fraco" className="ml-auto">
          {mostrarPessoa && sol.aberta && sol.dias_esperando > 0 ? `esperando há ${sol.dias_esperando} d` : tempoRelativo(sol.enviada_em)}
        </Texto>
      </View>
    </Cartao>
  );
}

/** Atalho em lista (ícone + título + detalhe + contador). */
export function Atalho({
  icone,
  titulo,
  detalhe,
  cor = AZUL_PONTO,
  contador,
  onPress,
}: {
  icone: NomeIcone;
  titulo: string;
  detalhe?: string;
  cor?: string;
  contador?: number;
  onPress: () => void;
}) {
  const cores = useCores();
  return (
    <Cartao onPress={onPress} acessibilidade={titulo} style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }}>
      <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome={icone} tamanho={20} cor={cor} />
      </View>
      <View className="flex-1">
        <Texto variante="corpoForte">{titulo}</Texto>
        {detalhe ? (
          <Texto variante="legenda" tom="suave" numberOfLines={2}>
            {detalhe}
          </Texto>
        ) : null}
      </View>
      {contador ? (
        <View style={{ minWidth: 24, height: 24, borderRadius: 12, paddingHorizontal: 7, backgroundColor: estado.perigo, alignItems: 'center', justifyContent: 'center' }}>
          <Texto variante="legenda" tom="branco" style={{ fontFamily: fontes.negrito, fontSize: 12 }}>
            {contador}
          </Texto>
        </View>
      ) : null}
      <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
    </Cartao>
  );
}

/** Aviso em faixa (erro, informação). */
export function Faixa({ icone, texto, cor }: { icone: NomeIcone; texto: string; cor: string }) {
  return (
    <View className="flex-row items-start gap-2 rounded-md px-3 py-2.5" style={{ backgroundColor: comAlfa(cor, 0.12) }}>
      <Icone nome={icone} tamanho={18} cor={cor} />
      <Texto variante="legenda" className="flex-1" style={{ color: cor, fontFamily: fontes.seminegrito }}>
        {texto}
      </Texto>
    </View>
  );
}
