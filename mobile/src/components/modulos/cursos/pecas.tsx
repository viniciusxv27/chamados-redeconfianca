/** Peças do módulo Cursos: cor/rótulo da situação, prazo em palavras e o cartão da lista. */
import { View } from 'react-native';

import { Cartao, Icone, type NomeIcone, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { CursoResumo, SituacaoCurso } from '@/services/modulos/cursos';

/** As mesmas cores da web: âmbar a enviar, vermelho recusado/vencido, azul em conferência, verde aprovado. */
export const COR_SITUACAO: Record<SituacaoCurso, string> = {
  NAO_FEZ: estado.alerta,
  RECUSADO: estado.perigo,
  CONFERIR: estado.info,
  APROVADO: estado.sucesso,
};

export const ICONE_SITUACAO: Record<SituacaoCurso, NomeIcone> = {
  NAO_FEZ: 'hourglass-outline',
  RECUSADO: 'arrow-undo-outline',
  CONFERIR: 'time-outline',
  APROVADO: 'checkmark-circle',
};

/** "DD/MM/AAAA" de uma data ISO (só a data, sem fuso). */
export function dataCurta(iso: string | null | undefined) {
  if (!iso) return '';
  const [a, m, d] = iso.slice(0, 10).split('-');
  return `${d}/${m}/${a}`;
}

const MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];

export function mesReferencia(iso: string | null | undefined) {
  if (!iso) return '';
  const [a, m] = iso.split('-');
  return `${MESES[Number(m) - 1] ?? ''}/${a}`;
}

/** O selo do cartão: o que a pessoa precisa saber primeiro (como meus_cursos.html). */
export function seloDoCurso(c: Pick<CursoResumo, 'situacao' | 'atrasado' | 'dias'>) {
  if (c.situacao === 'APROVADO') return { texto: 'Aprovado', cor: estado.sucesso };
  if (c.situacao === 'CONFERIR') return { texto: 'Em conferência', cor: estado.info };
  if (c.situacao === 'RECUSADO') return { texto: 'Reenviar', cor: estado.perigo };
  if (c.atrasado) return { texto: 'Prazo vencido', cor: estado.perigo };
  return { texto: c.dias === 0 ? 'Vence hoje' : `Faltam ${c.dias} dia${c.dias === 1 ? '' : 's'}`, cor: estado.alerta };
}

export function CartaoCurso({ curso: c, aoAbrir }: { curso: CursoResumo; aoAbrir: () => void }) {
  const cores = useCores();
  const selo = seloDoCurso(c);
  return (
    <Cartao acessibilidade={`${c.titulo}, ${selo.texto}`} onPress={aoAbrir} style={{ padding: 18, gap: 10, borderLeftWidth: 4, borderLeftColor: selo.cor }}>
      <View className="flex-row items-center justify-between gap-3">
        <Texto variante="rotulo" tom="fraco" numberOfLines={1} className="flex-1">
          {c.tipo === 'FOCO' ? 'Curso Foco' : 'Capacitação inicial'}
        </Texto>
        <Selo texto={selo.texto} cor={selo.cor} />
      </View>
      <Texto variante="corpoForte">{c.titulo}</Texto>
      {c.competencia ? (
        <Texto variante="legenda" tom="fraco">
          Referente a {mesReferencia(c.competencia)}
        </Texto>
      ) : null}
      {c.situacao === 'RECUSADO' && c.ultimo_envio?.observacao ? (
        <Texto variante="legenda" tom="perigo" numberOfLines={2}>
          {c.ultimo_envio.observacao}
        </Texto>
      ) : null}
      <View className="flex-row items-center justify-between gap-3">
        <View className="flex-row items-center gap-1.5">
          <Icone nome="calendar-outline" tamanho={14} cor={cores.texto3} />
          <Texto variante="legenda" tom="fraco">
            Prazo {dataCurta(c.prazo)}
          </Texto>
        </View>
        <View className="flex-row items-center gap-1">
          <Texto variante="legenda" tom="marca">
            {c.entregue ? 'Ver' : 'Enviar comprovante'}
          </Texto>
          <Icone nome="chevron-forward" tamanho={16} cor={cores.texto3} />
        </View>
      </View>
    </Cartao>
  );
}
