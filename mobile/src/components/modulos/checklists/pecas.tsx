/** Peças do módulo Checklists: status, datas sem fuso, barra de progresso, cartão de checklist, atalho e faixa. */
import { router, type Href } from 'expo-router';
import { View } from 'react-native';

import { Cartao, Icone, type NomeIcone, Pressionavel, Selo, Texto } from '@/components/ui';
import { estado, fontes, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { ExecucaoResumo, StatusExecucao } from '@/services/modulos/checklists';
import { comAlfa } from '@/utils/formatar';

export const COR_CHECKLIST = '#14B8A6';

export const COR_STATUS: Record<StatusExecucao, string> = {
  pending: '#6B7280',
  in_progress: estado.info,
  overdue: estado.perigo,
  awaiting_approval: estado.alerta,
  completed: estado.sucesso,
};

export function SeloExecucao({ e }: { e: Pick<ExecucaoResumo, 'status' | 'status_nome' | 'atrasado'> }) {
  if (e.atrasado && e.status !== 'overdue') return <Selo texto="Atrasado" cor={estado.perigo} />;
  return <Selo texto={e.status_nome} cor={COR_STATUS[e.status] ?? '#6B7280'} />;
}

/** 'AAAA-MM-DD' → Date local (new Date('AAAA-MM-DD') seria UTC e escorregaria um dia). */
export function diaLocal(iso: string) {
  const [a, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(a, (m || 1) - 1, d || 1);
}

export function isoDoDia(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function somarDias(iso: string, dias: number) {
  const d = diaLocal(iso);
  d.setDate(d.getDate() + dias);
  return isoDoDia(d);
}

/** '17/11' (ou '17/11/2025' em outro ano). */
export function dataCurta(iso: string | null | undefined) {
  if (!iso) return '';
  const d = diaLocal(iso);
  const mesmoAno = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString('pt-BR', mesmoAno ? { day: '2-digit', month: '2-digit' } : { day: '2-digit', month: '2-digit', year: 'numeric' });
}

/** 'segunda, 17/11' — 'hoje'/'ontem' quando for o caso. */
export function diaPorExtenso(iso: string, relativo = false) {
  if (relativo) {
    const hoje = isoDoDia(new Date());
    if (iso === hoje) return `hoje, ${dataCurta(iso)}`;
    if (iso === somarDias(hoje, -1)) return `ontem, ${dataCurta(iso)}`;
  }
  const semana = diaLocal(iso).toLocaleDateString('pt-BR', { weekday: 'long' }).replace('-feira', '');
  return `${semana}, ${dataCurta(iso)}`;
}

export function BarraProgresso({ valor, cor = COR_CHECKLIST, altura = 6 }: { valor: number; cor?: string; altura?: number }) {
  const cores = useCores();
  const pct = Math.max(0, Math.min(100, valor));
  return (
    <View style={{ height: altura, borderRadius: altura, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
      <View style={{ width: `${pct}%`, height: '100%', borderRadius: altura, backgroundColor: cor }} />
    </View>
  );
}

export function abrirExecucao(id: number) {
  router.push({ pathname: '/modulo/checklists/[id]', params: { id: String(id) } } as unknown as Href);
}

/** Um checklist (execução) na lista. */
export function CartaoExecucao({ e, mostrarData = true }: { e: ExecucaoResumo; mostrarData?: boolean }) {
  const cores = useCores();
  const feito = e.status === 'completed' || e.status === 'awaiting_approval';
  const cor = feito ? COR_STATUS[e.status] : e.atrasado ? estado.perigo : COR_CHECKLIST;
  return (
    <Cartao onPress={() => abrirExecucao(e.id)} acessibilidade={`${e.checklist}, ${e.periodo_nome}, ${e.status_nome}`} style={{ padding: 16, gap: 12 }}>
      <View className="flex-row items-start gap-3">
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={feito ? 'checkmark-done' : e.periodo === 'morning' ? 'sunny-outline' : 'partly-sunny-outline'} tamanho={20} cor={cor} />
        </View>
        <View className="flex-1 gap-0.5">
          <Texto variante="rotulo" tom="fraco">
            {[mostrarData ? diaPorExtenso(e.data, true) : null, e.periodo_nome].filter(Boolean).join(' · ')}
          </Texto>
          <Texto variante="corpoForte" numberOfLines={2}>
            {e.checklist}
          </Texto>
          {e.setor ? (
            <Texto variante="legenda" tom="suave" numberOfLines={1}>
              {e.setor}
            </Texto>
          ) : null}
        </View>
        <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
      </View>
      <View className="gap-2">
        <BarraProgresso valor={e.progresso} cor={cor} />
        <View className="flex-row items-center gap-2">
          <SeloExecucao e={e} />
          <Texto variante="legenda" tom="fraco" className="ml-auto" style={{ fontVariant: ['tabular-nums'] }}>
            {e.feitas}/{e.total} tarefas
          </Texto>
        </View>
      </View>
    </Cartao>
  );
}

/** Atalho em lista (ícone + título + detalhe + contador). */
export function Atalho({
  icone,
  titulo,
  detalhe,
  cor = COR_CHECKLIST,
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

/** Pílulas de escolha única (Sim / Não / Não se aplica). Tocar de novo na escolhida limpa, se `limpavel`. */
export function Escolha<T extends string>({
  opcoes,
  valor,
  aoEscolher,
  limpavel = false,
  desativado = false,
}: {
  opcoes: { valor: T; rotulo: string; cor: string; icone: NomeIcone }[];
  valor: T | null | undefined;
  aoEscolher: (v: T | null) => void;
  limpavel?: boolean;
  desativado?: boolean;
}) {
  const cores = useCores();
  return (
    <View className="flex-row gap-2" accessibilityRole="radiogroup">
      {opcoes.map((o) => {
        const ativo = o.valor === valor;
        return (
          <Pressionavel
            key={o.valor}
            vibrar
            disabled={desativado}
            onPress={() => aoEscolher(ativo && limpavel ? null : o.valor)}
            accessibilityRole="radio"
            accessibilityState={{ selected: ativo, disabled: desativado }}
            accessibilityLabel={o.rotulo}
            style={{
              flex: 1,
              minHeight: 46,
              borderRadius: raios.md,
              borderWidth: 1.5,
              borderColor: ativo ? o.cor : cores.borda,
              backgroundColor: ativo ? comAlfa(o.cor, 0.12) : cores.superficie,
              alignItems: 'center',
              justifyContent: 'center',
              flexDirection: 'row',
              gap: 6,
              paddingHorizontal: 6,
            }}>
            <Icone nome={o.icone} tamanho={17} cor={ativo ? o.cor : cores.texto3} />
            <Texto variante="legenda" numberOfLines={1} style={{ color: ativo ? o.cor : cores.texto2, fontFamily: ativo ? fontes.negrito : fontes.seminegrito }}>
              {o.rotulo}
            </Texto>
          </Pressionavel>
        );
      })}
    </View>
  );
}

export const OPCOES_FEITO = [
  { valor: 'nao' as const, rotulo: 'Não feito', cor: '#6B7280', icone: 'ellipse-outline' as NomeIcone },
  { valor: 'sim' as const, rotulo: 'Feito', cor: estado.sucesso, icone: 'checkmark-circle' as NomeIcone },
];

export const OPCOES_SIM_NAO = [
  { valor: 'yes' as const, rotulo: 'Sim', cor: estado.sucesso, icone: 'checkmark-circle-outline' as NomeIcone },
  { valor: 'no' as const, rotulo: 'Não', cor: estado.perigo, icone: 'close-circle-outline' as NomeIcone },
];

export const OPCOES_SITUACAO = [
  ...OPCOES_SIM_NAO,
  { valor: 'not_applicable' as const, rotulo: 'Não se aplica', cor: estado.alerta, icone: 'remove-circle-outline' as NomeIcone },
];

export const NOME_SITUACAO: Record<string, string> = { yes: 'Sim', no: 'Não', not_applicable: 'Não se aplica' };

