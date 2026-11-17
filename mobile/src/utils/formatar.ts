/** Datas, nomes e status para a tela. */
import { estado, marca } from '@/constants/theme';
import type { Prioridade, StatusChamado } from '@/services/tipos';

const MINUTO = 60_000;
const HORA = 60 * MINUTO;
const DIA = 24 * HORA;

/** "agora", "há 5 min", "há 3 h", "ontem", "seg.", "12/03", "12/03/2025". */
export function tempoRelativo(iso: string | null | undefined, agora = new Date()) {
  if (!iso) return '';
  const data = new Date(iso);
  const diff = agora.getTime() - data.getTime();
  if (diff < MINUTO) return 'agora';
  if (diff < HORA) return `há ${Math.floor(diff / MINUTO)} min`;
  if (diff < DIA && data.getDate() === agora.getDate()) return `há ${Math.floor(diff / HORA)} h`;
  const ontem = new Date(agora);
  ontem.setDate(agora.getDate() - 1);
  if (data.toDateString() === ontem.toDateString()) return 'ontem';
  if (diff < 6 * DIA) return data.toLocaleDateString('pt-BR', { weekday: 'short' });
  const mesmoAno = data.getFullYear() === agora.getFullYear();
  return data.toLocaleDateString('pt-BR', mesmoAno ? { day: '2-digit', month: '2-digit' } : undefined);
}

export function dataHora(iso: string | null | undefined) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.toLocaleDateString('pt-BR')} às ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
}

/** Prazo do chamado: "vence em 3 h", "venceu há 2 dias". */
export function prazo(iso: string | null | undefined, agora = new Date()) {
  if (!iso) return null;
  const diff = new Date(iso).getTime() - agora.getTime();
  const abs = Math.abs(diff);
  const quanto =
    abs < HORA ? `${Math.max(1, Math.round(abs / MINUTO))} min` : abs < DIA ? `${Math.round(abs / HORA)} h` : `${Math.round(abs / DIA)} dias`;
  return diff >= 0 ? `vence em ${quanto}` : `venceu há ${quanto}`;
}

export function saudacao(agora = new Date()) {
  const h = agora.getHours();
  if (h < 12) return 'Bom dia';
  if (h < 18) return 'Boa tarde';
  return 'Boa noite';
}

export function iniciais(nome: string | null | undefined) {
  const partes = (nome || '?').trim().split(/\s+/);
  return ((partes[0]?.[0] ?? '') + (partes.length > 1 ? partes[partes.length - 1][0] : '')).toUpperCase();
}

export function tamanhoArquivo(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Cor de cada status (a mesma lógica de cor do portal: azul andamento, verde resolvido…). */
export const COR_STATUS: Record<StatusChamado, string> = {
  ABERTO: estado.info,
  REABERTO: estado.info,
  EM_ANDAMENTO: marca[500],
  RESOLVIDO: estado.alerta,
  AGUARDANDO_APROVACAO: estado.alerta,
  FECHADO: estado.sucesso,
  ENCERRADO_SEM_SOLUCAO: '#6B7280',
  REJEITADO: estado.perigo,
};

export const COR_PRIORIDADE: Record<Prioridade, string> = {
  BAIXA: '#6B7280',
  MEDIA: estado.info,
  ALTA: estado.alerta,
  CRITICA: estado.perigo,
};

/** Cor estável a partir do nome (avatar sem foto). */
export function corDoNome(nome: string) {
  const cores = ['#FF6B35', '#3B82F6', '#27AE60', '#8B5CF6', '#EC4899', '#F39C12', '#14B8A6', '#2C3E50'];
  let h = 0;
  for (const c of nome) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return cores[h % cores.length];
}

/** Cor com transparência (#RRGGBB + alfa 0–1). */
export function comAlfa(hex: string, alfa: number) {
  const a = Math.round(Math.min(1, Math.max(0, alfa)) * 255)
    .toString(16)
    .padStart(2, '0');
  return `${hex}${a}`;
}
