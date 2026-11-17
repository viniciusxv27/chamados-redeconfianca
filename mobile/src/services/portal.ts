/** Chamadas à API, agrupadas por assunto. As telas usam via hooks (src/hooks/consultas.ts). */
import * as Application from 'expo-application';
import * as Device from 'expo-device';
import { Platform } from 'react-native';

import { api } from './api';
import type {
  Bloqueio,
  Categoria,
  Chamado,
  ChamadoResumo,
  Comentario,
  Comunicado,
  ComunicadoResumo,
  Inicio,
  Modulo,
  Notificacao,
  Opcao,
  Pagina,
  Prioridade,
  SituacaoComunicado,
  Usuario,
} from './tipos';

export const sessao = {
  async entrar(email: string, senha: string) {
    const { data } = await api.post<{ token: string; usuario: Usuario; bloqueio: Bloqueio | null }>('/auth/login/', {
      email,
      senha,
      plataforma: Platform.OS,
      dispositivo: [Device.manufacturer, Device.modelName].filter(Boolean).join(' ') || Platform.OS,
      versao_app: Application.nativeApplicationVersion ?? '',
    });
    return data;
  },
  async sair() {
    await api.post('/auth/logout/');
  },
  async eu(atualizar = false) {
    const { data } = await api.get<{ usuario: Usuario; bloqueio: Bloqueio | null; notificacoes_nao_lidas: number }>(
      '/eu/',
      { params: atualizar ? { atualizar: 1 } : undefined },
    );
    return data;
  },
};

export async function buscarInicio() {
  const { data } = await api.get<Inicio>('/inicio/');
  return data;
}

export type FiltroChamados = {
  situacao: 'abertos' | 'concluidos' | 'todos';
  escopo: 'todos' | 'meus' | 'comigo';
  busca?: string;
};

export const chamados = {
  async listar(filtro: FiltroChamados, pagina: number) {
    const { data } = await api.get<Pagina<ChamadoResumo>>('/chamados/', { params: { ...filtro, pagina } });
    return data;
  },
  async detalhe(id: number) {
    const { data } = await api.get<Chamado>(`/chamados/${id}/`);
    return data;
  },
  async comentar(id: number, texto: string) {
    const { data } = await api.post<Comentario>(`/chamados/${id}/comentarios/`, { texto });
    return data;
  },
  async setores() {
    const { data } = await api.get<Opcao[]>('/chamados/setores/');
    return data;
  },
  async categorias(setor: number) {
    const { data } = await api.get<Categoria[]>('/chamados/categorias/', { params: { setor } });
    return data;
  },
  async abrir(campos: {
    titulo: string;
    descricao: string;
    setor: number;
    categoria: number;
    prioridade: Prioridade;
    local?: string;
    anexos: { uri: string; nome: string; tipo: string }[];
  }) {
    const corpo = new FormData();
    corpo.append('titulo', campos.titulo);
    corpo.append('descricao', campos.descricao);
    corpo.append('setor', String(campos.setor));
    corpo.append('categoria', String(campos.categoria));
    corpo.append('prioridade', campos.prioridade);
    if (campos.local) corpo.append('local', campos.local);
    for (const a of campos.anexos) {
      // React Native aceita {uri, name, type} no FormData.
      corpo.append('anexos', { uri: a.uri, name: a.nome, type: a.tipo } as unknown as Blob);
    }
    const { data } = await api.post<ChamadoResumo>('/chamados/', corpo, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    });
    return data;
  },
};

export const comunicados = {
  async listar(pagina: number) {
    const { data } = await api.get<Pagina<ComunicadoResumo>>('/comunicados/', { params: { pagina } });
    return data;
  },
  async detalhe(id: number) {
    const { data } = await api.get<Comunicado>(`/comunicados/${id}/`);
    return data;
  },
  async situacao(id: number, situacao: Exclude<SituacaoComunicado, 'NAO_VISUALIZADO'>) {
    const { data } = await api.post<{ situacao: SituacaoComunicado }>(`/comunicados/${id}/situacao/`, { situacao });
    return data;
  },
  async curtir(id: number) {
    const { data } = await api.post<{ curti: boolean; curtidas: number }>(`/comunicados/${id}/curtir/`);
    return data;
  },
};

export const notificacoes = {
  async listar() {
    const { data } = await api.get<{ resultados: Notificacao[]; nao_lidas: number }>('/notificacoes/');
    return data;
  },
  async marcarLida(id: string) {
    const { data } = await api.post<{ nao_lidas: number }>(`/notificacoes/${id}/lida/`);
    return data;
  },
  async marcarTodas() {
    await api.post('/notificacoes/lidas/');
  },
};

export async function listarModulos() {
  const { data } = await api.get<Modulo[]>('/modulos/');
  return data;
}
