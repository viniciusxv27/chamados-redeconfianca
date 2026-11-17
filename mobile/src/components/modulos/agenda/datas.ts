/** Datas da agenda: tudo no fuso do aparelho (o portal manda ISO com o fuso de Brasília). */

const DIAS_CURTOS = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];
const DIAS = ['domingo', 'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado'];
const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

function dois(n: number) {
  return String(n).padStart(2, '0');
}

/** Date → 'AAAA-MM-DD' (dia local). */
export function isoDoDia(d: Date) {
  return `${d.getFullYear()}-${dois(d.getMonth() + 1)}-${dois(d.getDate())}`;
}

/** 'AAAA-MM-DD' → Date à meia-noite local. */
export function dataDoDia(iso: string) {
  const [a, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(a, m - 1, d);
}

export function somarDias(iso: string, n: number) {
  const d = dataDoDia(iso);
  d.setDate(d.getDate() + n);
  return isoDoDia(d);
}

/** Segunda-feira da semana do dia (a semana do portal vai de segunda a domingo). */
export function segundaDa(iso: string) {
  const d = dataDoDia(iso);
  const dia = (d.getDay() + 6) % 7;
  return somarDias(iso, -dia);
}

export function diasDaSemana(segunda: string) {
  return Array.from({ length: 7 }, (_, i) => somarDias(segunda, i));
}

export function diaCurto(iso: string) {
  return DIAS_CURTOS[dataDoDia(iso).getDay()];
}

/** "segunda, 17 de nov." */
export function rotuloDoDia(iso: string) {
  const d = dataDoDia(iso);
  return `${DIAS[d.getDay()]}, ${d.getDate()} de ${MESES[d.getMonth()]}.`;
}

/** "17 a 23 de nov." / "29 de set. a 5 de out." */
export function rotuloDaSemana(segunda: string) {
  const a = dataDoDia(segunda);
  const b = dataDoDia(somarDias(segunda, 6));
  if (a.getMonth() === b.getMonth()) return `${a.getDate()} a ${b.getDate()} de ${MESES[b.getMonth()]}.`;
  return `${a.getDate()} de ${MESES[a.getMonth()]}. a ${b.getDate()} de ${MESES[b.getMonth()]}.`;
}

export function horaCurta(iso: string) {
  const d = new Date(iso);
  return `${dois(d.getHours())}:${dois(d.getMinutes())}`;
}

/** "09:00 – 10:00", "Dia inteiro", "seg. 17/11 09:00 – qua. 19/11 10:00". */
export function faixaDeHorario(inicio: string, fim: string, diaInteiro: boolean) {
  if (diaInteiro) return 'Dia inteiro';
  const a = new Date(inicio);
  const b = new Date(fim);
  if (isoDoDia(a) === isoDoDia(b)) return `${horaCurta(inicio)} – ${horaCurta(fim)}`;
  const curto = (d: Date) => `${DIAS_CURTOS[d.getDay()]}. ${dois(d.getDate())}/${dois(d.getMonth() + 1)}`;
  return `${curto(a)} ${horaCurta(inicio)} – ${curto(b)} ${horaCurta(fim)}`;
}

/** O evento passa por este dia? (vale para evento de vários dias). */
export function estaNoDia(ev: { inicio: string; fim: string }, dia: string) {
  const comeco = dataDoDia(dia).getTime();
  const fimDoDia = dataDoDia(somarDias(dia, 1)).getTime();
  const a = new Date(ev.inicio).getTime();
  const b = new Date(ev.fim).getTime();
  return a < fimDoDia && (b > comeco || a === b);
}

/** "1 h 05", "45 min". */
export function duracao(segundos: number) {
  if (!segundos) return '';
  const min = Math.round(segundos / 60);
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h ${dois(min % 60)}`;
}

/** Máscaras e leitura do que a pessoa digita (o app não tem seletor de data nativo instalado). */
function soDigitos(v: string, max: number) {
  return v.replace(/\D/g, '').slice(0, max);
}

export function mascaraData(v: string) {
  const d = soDigitos(v, 8);
  if (d.length <= 2) return d;
  if (d.length <= 4) return `${d.slice(0, 2)}/${d.slice(2)}`;
  return `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}`;
}

export function mascaraHora(v: string) {
  const d = soDigitos(v, 4);
  return d.length <= 2 ? d : `${d.slice(0, 2)}:${d.slice(2)}`;
}

/** 'DD/MM/AAAA' → 'AAAA-MM-DD' ('' se incompleta ou inválida). */
export function isoDaData(texto: string) {
  const m = texto.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (!m) return '';
  const [, d, mes, a] = m;
  const data = new Date(Number(a), Number(mes) - 1, Number(d));
  if (data.getDate() !== Number(d) || data.getMonth() !== Number(mes) - 1) return '';
  return `${a}-${mes}-${d}`;
}

export function dataDoIso(iso: string | null | undefined) {
  if (!iso) return '';
  const [a, m, d] = iso.slice(0, 10).split('-');
  return `${d}/${m}/${a}`;
}

export function horaValida(texto: string) {
  const m = texto.match(/^(\d{2}):(\d{2})$/);
  return !!m && Number(m[1]) < 24 && Number(m[2]) < 60;
}

/** Dia ('AAAA-MM-DD') + hora ('HH:MM') do aparelho → ISO com fuso (o mesmo que o portal manda: toISOString). */
export function momento(dia: string, hora: string) {
  const [h, m] = hora.split(':').map(Number);
  const d = dataDoDia(dia);
  d.setHours(h, m, 0, 0);
  return d.toISOString();
}
