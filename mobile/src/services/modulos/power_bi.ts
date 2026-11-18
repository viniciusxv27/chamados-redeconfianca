/** Módulo Power BI: os dashboards liberados para a pessoa (/power-bi/ do portal). */
import { api } from '../api';

export type Dashboard = {
  id: number;
  nome: string;
  descricao: string;
  /** Classe Font Awesome cadastrada no portal (ex.: "fas fa-chart-line"). */
  icone: string;
  cor: string;
  imagem: string | null;
  /** "Nova guia permitida" no cadastro: abre o painel direto, fora da página do portal. */
  abre_direto: boolean;
  origem: 'power_bi' | 'outros';
};

export type AberturaDashboard = { url: string; onde: 'direto' | 'portal'; registrado: boolean };

export const powerBi = {
  listar: () => api.get<{ resultados: Dashboard[] }>('/m/power_bi/').then((r) => r.data.resultados),
  /** O servidor confere o acesso, registra a visualização (como a web) e devolve o endereço. */
  abrir: (id: number) => api.post<AberturaDashboard>(`/m/power_bi/${id}/abrir/`).then((r) => r.data),
};
