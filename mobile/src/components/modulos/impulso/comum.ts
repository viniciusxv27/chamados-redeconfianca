/** Cores e textos do Impulso (status da meta, prazo, cores dos pilares). */
import { estado, marca } from '@/constants/theme';
import type { PrioridadeMeta, StatusMeta } from '@/services/modulos/impulso';

export const COR_STATUS_META: Record<StatusMeta, string> = {
  A_FAZER: estado.info,
  EM_ANDAMENTO: marca[500],
  ENTREGUE: estado.alerta,
  CONCLUIDA: estado.sucesso,
};

export const COR_PRIORIDADE_META: Record<PrioridadeMeta, string> = {
  BAIXA: '#6B7280',
  MEDIA: estado.info,
  ALTA: estado.alerta,
  CRITICA: estado.perigo,
};

export const ROTULO_STATUS: Record<StatusMeta, string> = {
  A_FAZER: 'A fazer',
  EM_ANDAMENTO: 'Em andamento',
  ENTREGUE: 'Entregue',
  CONCLUIDA: 'Concluída',
};

/** "2026-11-17" → "17/11" (ou "17/11/2025" em outro ano), sem passar por fuso. */
export function dataCurta(iso: string | null | undefined) {
  if (!iso) return '';
  const [ano, mes, dia] = iso.slice(0, 10).split('-');
  return ano === String(new Date().getFullYear()) ? `${dia}/${mes}` : `${dia}/${mes}/${ano}`;
}

/** Prazo da meta em palavras: "venceu há 2 dias", "vence hoje", "vence amanhã", "vence em 5 dias". */
export function textoDoPrazo(dias: number | null, concluida = false) {
  if (dias == null) return 'Sem prazo';
  if (concluida) return null;
  if (dias < -1) return `venceu há ${-dias} dias`;
  if (dias === -1) return 'venceu ontem';
  if (dias === 0) return 'vence hoje';
  if (dias === 1) return 'vence amanhã';
  return `vence em ${dias} dias`;
}

/** Pontos com uma casa quando precisa: 12 → "12", 12.5 → "12,5". */
export function pontos(valor: number | null | undefined) {
  if (valor == null) return '—';
  return valor.toLocaleString('pt-BR', { maximumFractionDigits: 1 });
}
