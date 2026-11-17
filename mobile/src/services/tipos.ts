/** Formatos que a API /api/mobile/v1/ devolve (api_mobile/dados.py no Django). */

export type Pessoa = {
  id: number;
  nome: string;
  primeiro_nome: string;
  foto: string | null;
  setor: string | null;
};

export type Usuario = Pessoa & {
  email: string;
  cargo: string | null;
  telefone: string | null;
  hierarquia: string;
  hierarquia_nome: string;
  superadmin: boolean;
  tema: 'light' | 'dark';
  setores: string[];
};

export type Bloqueio = {
  tipo: 'ferias' | 'jornada' | 'curso' | 'trilha' | 'cadastro';
  titulo: string;
  mensagem: string;
  caminho_web: string;
};

export type StatusChamado =
  | 'ABERTO'
  | 'EM_ANDAMENTO'
  | 'RESOLVIDO'
  | 'AGUARDANDO_APROVACAO'
  | 'FECHADO'
  | 'ENCERRADO_SEM_SOLUCAO'
  | 'REABERTO'
  | 'REJEITADO';

export type Prioridade = 'BAIXA' | 'MEDIA' | 'ALTA' | 'CRITICA';

export type ChamadoResumo = {
  id: number;
  titulo: string;
  status: StatusChamado;
  status_nome: string;
  prioridade: Prioridade;
  prioridade_nome: string;
  setor: string | null;
  categoria: string | null;
  solicitante: Pessoa | null;
  responsavel: Pessoa | null;
  criado_em: string;
  atualizado_em: string;
  prazo: string | null;
  atrasado: boolean;
  em_aberto: boolean;
};

export type Comentario = {
  id: number;
  texto: string;
  tipo: string;
  tipo_nome: string;
  autor: Pessoa | null;
  criado_em: string;
  sou_eu: boolean;
};

export type Anexo = {
  id: number;
  nome: string;
  tamanho: number;
  tipo: string;
  url: string | null;
  enviado_em: string;
};

export type Chamado = ChamadoResumo & {
  descricao: string;
  solucao: string | null;
  local: string | null;
  auxiliares: Pessoa[];
  tags: { nome: string; cor: string | null }[];
  comentarios: Comentario[];
  anexos: Anexo[];
  pode_comentar: boolean;
  url_web: string;
};

export type SituacaoComunicado = 'NAO_VISUALIZADO' | 'ESTOU_CIENTE' | 'ESTOU_COM_DUVIDA';

export type ComunicadoResumo = {
  id: number;
  titulo: string;
  cor_titulo: string | null;
  resumo: string;
  imagem: string | null;
  fixado: boolean;
  obrigatorio: boolean;
  autor: Pessoa | null;
  criado_em: string;
  curtidas: number;
  curti: boolean;
  situacao: SituacaoComunicado;
};

export type Comunicado = ComunicadoResumo & { texto: string; imagens: string[] };

export type Notificacao = {
  id: string;
  titulo: string;
  mensagem: string;
  tipo: string;
  lida: boolean;
  criada_em: string;
  link: string | null;
};

export type Pagina<T> = { resultados: T[]; pagina: number; total: number; tem_mais: boolean };

export type Inicio = {
  usuario: Usuario;
  chamados: {
    abertos_por_mim: number;
    comigo: number;
    aguardando_minha_aprovacao: number;
    atrasados: number;
  };
  chamados_recentes: ChamadoResumo[];
  comunicados_fixados: ComunicadoResumo[];
  comunicados: ComunicadoResumo[];
  comunicados_obrigatorios: { id: number; titulo: string }[];
  notificacoes_nao_lidas: number;
};

export type Opcao = { id: number; nome: string };
export type Categoria = Opcao & { descricao_padrao: string; horas_solucao: number | null };

/** Um módulo do portal que a pessoa pode abrir (a API já filtrou pela regra de acesso). */
export type Modulo = { chave: string; rotulo: string; descricao: string; icone: string; cor: string; rota: string };
