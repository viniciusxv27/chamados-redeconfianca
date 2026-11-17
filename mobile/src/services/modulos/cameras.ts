/**
 * Módulo Câmeras (/cameras/ do portal, só SUPERADMIN): lojas e câmeras, alertas,
 * indicadores e a última imagem de cada câmera. Vídeo ao vivo só no portal (WebRTC).
 * Nada daqui manda comando para o agente da loja.
 */
import { api } from '../api';

export type Situacao = 'online' | 'offline' | 'sem_agente' | 'sem_equipamento';
export type Opcao = { valor: string; rotulo: string };
export type Problema = { nivel: 'erro' | 'atencao'; texto: string };

export type AlertaResumo = {
  id: number;
  loja: { id: number; nome: string };
  canal: string | null;
  tipo: 'ITEM_SUMIU' | 'FORA_HORARIO' | 'PERMANENCIA';
  tipo_rotulo: string;
  status: 'NOVO' | 'ANALISE' | 'CONFIRMADO' | 'FALSO';
  status_rotulo: string;
  confianca: number;
  prioridade: number;
  chance_real: number | null;
  repeticoes: number;
  motivo: string | null;
  criado_em: string;
  ultimo_em: string;
  video_status: 'SEM_VIDEO' | 'AGUARDANDO' | 'RECEBIDO' | 'APAGADO';
  video_rotulo: string;
  ia_status: 'PENDENTE' | 'FEITA' | 'ERRO' | null;
  ia_risco: number | null;
  ia_classificacao: string | null;
  /** Link assinado (5 min). */
  foto: string | null;
};

export type LojaResumo = {
  id: number;
  loja: string;
  coordenador: string | null;
  situacao: Situacao;
  situacao_rotulo: string;
  online: boolean;
  tem_equipamento: boolean;
  ultimo_contato: string | null;
  versao_agente: string | null;
  erro_agente: string | null;
  cameras: number;
  alertas_novos: number;
  problemas: Problema[];
  /** Última imagem da câmera principal (cache de 5 min do portal), por link assinado. */
  foto: string | null;
  foto_em: string | null;
};

export type InicioCameras = {
  ativo: boolean;
  total_lojas: number;
  online: number;
  sem_agente: number;
  total_cameras: number;
  novos: number;
  com_problema: number;
  lojas: LojaResumo[];
  recentes: AlertaResumo[];
  caminho_web: string;
};

export type CameraLoja = {
  id: number;
  numero: number;
  titulo: string;
  analisando: boolean;
  areas: number;
  imagem_ok: boolean | null;
  quadros_por_segundo: number | null;
  erro: string | null;
  foto: string | null;
  foto_em: string | null;
};

export type LojaDetalhe = LojaResumo & {
  abre_as: string;
  fecha_as: string;
  saude_em: string | null;
  saude: { rotulo: string; valor: string }[];
  cameras_lista: CameraLoja[];
  alertas: AlertaResumo[];
  ao_vivo: { disponivel: boolean; motivo: string | null };
  caminho_web: string;
};

export type FiltroAlertas = { status: string; loja?: string; tipo?: string };

export type PaginaAlertas = {
  filtros: { status: string; loja: string; tipo: string; de: string; ate: string };
  pagina: number;
  tem_mais: boolean;
  total: number;
  total_abertos: number;
  por_tipo: { valor: string; rotulo: string; abertos: number }[];
  opcoes: { status: Opcao[]; tipos: Opcao[]; lojas: Opcao[] };
  alertas: AlertaResumo[];
};

export type Momento = { segundo: number; o_que: string };

export type AlertaDetalhe = AlertaResumo & {
  observacao: string | null;
  motivo_falso: string | null;
  motivo_falso_rotulo: string | null;
  tratado_por: { id: number; nome: string } | null;
  tratado_em: string | null;
  /** Link assinado próprio (15 min, responde por pedaços para o player). */
  video: string | null;
  video_segundos: number;
  video_evento_segundo: number;
  video_tamanho: number;
  quadros: { indice: number; segundo: number; no_video: number; imagem: string }[];
  medidas: { rotulo: string; valor: string }[];
  historico: { chance: number | null; decisoes_canal: number; decisoes_loja: number };
  motivos_camera: { rotulo: string; quantos: number }[];
  rigor: string;
  ia: {
    status: string | null;
    status_rotulo: string | null;
    risco: number | null;
    classificacao: string | null;
    resumo: string | null;
    linha_do_tempo: Momento[];
    sinais_de_risco: string[];
    sinais_normais: string[];
    conferir_no_video: Momento[];
    recomendacao: string | null;
    modelo: string | null;
    em: string | null;
    erro: string | null;
  };
  equipe: { nome: string; horarios: string[]; como: string }[];
  na_tolerancia: boolean;
  caminho_web: string;
  opcoes: { status: Opcao[]; motivos_falso: Opcao[] };
  mensagem?: string;
};

export type Tratamento = { status: string; motivo_falso?: string; observacao?: string };

export type LinhaIndicador = {
  loja: { id: number; nome: string };
  cobertura: number;
  cobertura_porta: number;
  cobertura_mesa: number;
  tem_ponto: boolean;
  entradas: number | null;
  entradas_aviso: string | null;
  entradas_equipe: number | null;
  clientes: number | null;
  parcial_entradas: boolean;
  pico: string | null;
  atendimentos: number | null;
  mesas_aviso: string | null;
  parcial_mesas: boolean;
  longos: number;
  media: string;
  mediana: string;
  maior: string;
  por_mesa: { nome: string; quantos: number; media: string; mediana: string; maior: string }[];
  matinal_aviso: string | null;
  matinal_dias: number;
  matinal_medidos: number;
  matinal_sem_equipe: number;
  matinais: { data: string; inicio: string | null; duracao: string; pessoas: number }[];
  por_dia: {
    data: string;
    cobertura: number;
    entradas: number | null;
    clientes: number | null;
    atendimentos: number | null;
    mediana: string;
    matinal: 'sim' | 'nao' | 'sem_medicao' | 'sem_equipe' | null;
    equipe: number | null;
    primeira: string | null;
  }[];
};

export type Indicadores = {
  de: string;
  ate: string;
  hoje: string;
  um_dia: boolean;
  loja: number | null;
  lojas: Opcao[];
  total_entradas: number | null;
  total_clientes: number | null;
  lojas_com_medicao: number;
  total_lojas: number;
  cobertura_media: number;
  matinal_dias: number;
  matinal_medidos: number;
  matinal_com: number;
  matinal_medidas: number;
  total_atendimentos: number | null;
  total_longos: number;
  media_geral: string;
  mediana_geral: string;
  atendimento_maximo_min: number;
  por_hora: { hora: number; entradas: number; atendimentos: number; pe: number; pa: number }[];
  anterior: { de: string; ate: string; entradas: number | null; atendimentos: number | null } | null;
  linhas: LinhaIndicador[];
  caminho_web: string;
};

export type FiltroIndicadores = { de: string; ate: string; loja?: number | null };

export const cameras = {
  async inicio() {
    const { data } = await api.get<InicioCameras>('/m/cameras/');
    return data;
  },
  async loja(id: number) {
    const { data } = await api.get<LojaDetalhe>(`/m/cameras/lojas/${id}/`);
    return data;
  },
  async alertas(f: FiltroAlertas, pagina: number) {
    const { data } = await api.get<PaginaAlertas>('/m/cameras/alertas/', { params: { ...f, pagina } });
    return data;
  },
  async alerta(id: number) {
    const { data } = await api.get<AlertaDetalhe>(`/m/cameras/alertas/${id}/`);
    return data;
  },
  async tratar(id: number, corpo: Tratamento) {
    const { data } = await api.post<AlertaDetalhe>(`/m/cameras/alertas/${id}/tratar/`, corpo);
    return data;
  },
  async indicadores(f: FiltroIndicadores) {
    const { data } = await api.get<Indicadores>('/m/cameras/indicadores/', { params: { de: f.de, ate: f.ate, loja: f.loja ?? '' } });
    return data;
  },
};
