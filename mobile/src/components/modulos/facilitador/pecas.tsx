/** Peças do Facilitador: ícone por tipo de arquivo, cartão do item e o "abrir" (link, página ou arquivo). */
import { type Href, router } from 'expo-router';
import { Alert, Linking, View } from 'react-native';

import { abrirArquivo } from '@/components/modulos/comum';
import { Cartao, Icone, type NomeIcone, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useAbrirRecurso } from '@/hooks/modulos/facilitador';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { RecursoResumo } from '@/services/modulos/facilitador';
import { comAlfa, tamanhoArquivo } from '@/utils/formatar';

export const COR_FACILITADOR = '#14B8A6';

/** Extensão → ícone e cor (as mesmas famílias de cor do portal, facilitador/models.py ICONES). */
const POR_EXTENSAO: Record<string, [NomeIcone, string]> = {
  pdf: ['document-text-outline', '#EF4444'],
  doc: ['document-outline', '#3B82F6'],
  docx: ['document-outline', '#3B82F6'],
  odt: ['document-outline', '#3B82F6'],
  xls: ['grid-outline', '#10B981'],
  xlsx: ['grid-outline', '#10B981'],
  csv: ['grid-outline', '#10B981'],
  ods: ['grid-outline', '#10B981'],
  ppt: ['easel-outline', '#F97316'],
  pptx: ['easel-outline', '#F97316'],
  png: ['image-outline', '#EC4899'],
  jpg: ['image-outline', '#EC4899'],
  jpeg: ['image-outline', '#EC4899'],
  gif: ['image-outline', '#EC4899'],
  webp: ['image-outline', '#EC4899'],
  svg: ['image-outline', '#EC4899'],
  mp4: ['videocam-outline', '#8B5CF6'],
  mov: ['videocam-outline', '#8B5CF6'],
  webm: ['videocam-outline', '#8B5CF6'],
  mp3: ['musical-notes-outline', '#06B6D4'],
  wav: ['musical-notes-outline', '#06B6D4'],
  ogg: ['musical-notes-outline', '#06B6D4'],
  zip: ['archive-outline', '#F59E0B'],
  rar: ['archive-outline', '#F59E0B'],
  '7z': ['archive-outline', '#F59E0B'],
  apk: ['phone-portrait-outline', '#84CC16'],
  txt: ['reader-outline', '#6B7280'],
  html: ['globe-outline', '#14B8A6'],
  htm: ['globe-outline', '#14B8A6'],
};

export function iconeDoRecurso(r: Pick<RecursoResumo, 'tipo' | 'extensao'>): [NomeIcone, string] {
  if (r.tipo === 'link') return ['link-outline', '#6366F1'];
  return POR_EXTENSAO[r.extensao ?? ''] ?? ['document-attach-outline', '#64748B'];
}

export function IconeRecurso({ recurso, tamanho = 46 }: { recurso: Pick<RecursoResumo, 'tipo' | 'extensao'>; tamanho?: number }) {
  const [icone, cor] = iconeDoRecurso(recurso);
  return (
    <View style={{ width: tamanho, height: tamanho, borderRadius: tamanho / 3, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
      <Icone nome={icone} tamanho={tamanho * 0.48} cor={cor} />
    </View>
  );
}

/** "PDF · 1,2 MB", "site.com.br", "Página". */
export function legendaDoRecurso(r: RecursoResumo) {
  if (r.tipo === 'link') return r.dominio ?? 'Link';
  const partes = [r.pagina ? 'Página' : (r.extensao ?? 'arquivo').toUpperCase()];
  if (r.tamanho) partes.push(tamanhoArquivo(r.tamanho));
  return partes.join(' · ');
}

export function irParaRecurso(id: number) {
  router.push({ pathname: '/modulo/facilitador/[id]', params: { id: String(id) } } as unknown as Href);
}

export function CartaoRecurso({ recurso }: { recurso: RecursoResumo }) {
  const cores = useCores();
  return (
    <Cartao onPress={() => irParaRecurso(recurso.id)} acessibilidade={recurso.titulo} style={{ padding: 18, flexDirection: 'row', alignItems: 'center', gap: 14 }}>
      <IconeRecurso recurso={recurso} />
      <View className="flex-1 gap-1">
        <Texto variante="corpoForte" numberOfLines={2}>
          {recurso.titulo}
        </Texto>
        <Texto variante="legenda" tom="fraco" numberOfLines={1}>
          {legendaDoRecurso(recurso)}
        </Texto>
        {recurso.oculto ? <Selo texto="Oculto" cor={estado.alerta} /> : null}
      </View>
      <Icone nome={recurso.tipo === 'link' ? 'open-outline' : 'chevron-forward'} tamanho={18} cor={cores.texto3} />
    </Cartao>
  );
}

/** Abre o item: link no navegador do sistema; página e arquivo no navegador do app. */
export function useAbrir() {
  const abrir = useAbrirRecurso();
  const executar = (id: number, como?: 'arquivo') =>
    abrir.mutate(
      { id, como },
      {
        onSuccess: (d) => {
          if (d.tipo === 'link') {
            Linking.openURL(d.url).catch(() => Alert.alert('Não deu para abrir', 'O celular não conseguiu abrir este link.'));
          } else {
            void abrirArquivo(d.url);
          }
        },
        onError: (erro) => Alert.alert('Não deu para abrir', mensagemDoErro(erro)),
      },
    );
  return { executar, abrindo: abrir.isPending, qual: abrir.variables };
}
