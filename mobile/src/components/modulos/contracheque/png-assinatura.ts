/**
 * Traços da assinatura → PNG (data URL), sem biblioteca nativa.
 *
 * O portal guarda a assinatura como `data:image/png;base64,…` (o canvas da web).
 * No app não há canvas, então rasterizamos os traços aqui: PNG de paleta com
 * 1 bit por pixel (tinta #1A1A2E e fundo transparente, como o da web), compressão
 * "stored" do zlib. Fica em ~30 KB para um quadro de 340×180 em 2x.
 */

export type Ponto = { x: number; y: number };
export type Traco = Ponto[];

const TINTA: [number, number, number] = [0x1a, 0x1a, 0x2e];

/** Desenha os traços (em pontos da tela) num bitmap 1-bit e devolve o data URL PNG. */
export function assinaturaEmPng(tracos: Traco[], largura: number, altura: number, espessura = 2.5, escala = 2): string {
  const w = Math.max(1, Math.round(largura * escala));
  const h = Math.max(1, Math.round(altura * escala));
  const bytesLinha = Math.ceil(w / 8);
  // 1 = fundo (transparente), 0 = tinta. Começa tudo fundo.
  const bits = new Uint8Array(bytesLinha * h).fill(0xff);
  const raio = Math.max(1, (espessura * escala) / 2);

  const carimbar = (cx: number, cy: number) => {
    const x0 = Math.max(0, Math.floor(cx - raio));
    const x1 = Math.min(w - 1, Math.ceil(cx + raio));
    const y0 = Math.max(0, Math.floor(cy - raio));
    const y1 = Math.min(h - 1, Math.ceil(cy + raio));
    const r2 = raio * raio;
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const dx = x + 0.5 - cx;
        const dy = y + 0.5 - cy;
        if (dx * dx + dy * dy <= r2) bits[y * bytesLinha + (x >> 3)] &= ~(0x80 >> (x & 7));
      }
    }
  };

  for (const traco of tracos) {
    if (!traco.length) continue;
    carimbar(traco[0].x * escala, traco[0].y * escala);
    for (let i = 1; i < traco.length; i++) {
      const a = traco[i - 1];
      const b = traco[i];
      const ax = a.x * escala;
      const ay = a.y * escala;
      const dx = b.x * escala - ax;
      const dy = b.y * escala - ay;
      const passos = Math.max(1, Math.ceil(Math.hypot(dx, dy) / 0.75));
      for (let p = 1; p <= passos; p++) carimbar(ax + (dx * p) / passos, ay + (dy * p) / passos);
    }
  }

  // Linhas cruas do PNG: filtro 0 + bytes da linha.
  const cru = new Uint8Array((bytesLinha + 1) * h);
  for (let y = 0; y < h; y++) {
    cru[y * (bytesLinha + 1)] = 0;
    cru.set(bits.subarray(y * bytesLinha, (y + 1) * bytesLinha), y * (bytesLinha + 1) + 1);
  }

  const ihdr = new Uint8Array(13);
  escrever32(ihdr, 0, w);
  escrever32(ihdr, 4, h);
  ihdr[8] = 1; // 1 bit por pixel
  ihdr[9] = 3; // paleta
  const plte = new Uint8Array([...TINTA, 0xff, 0xff, 0xff]);
  const trns = new Uint8Array([0xff, 0x00]); // índice 1 (fundo) transparente

  const partes = [
    new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pedaco('IHDR', ihdr),
    pedaco('PLTE', plte),
    pedaco('tRNS', trns),
    pedaco('IDAT', zlibSemCompressao(cru)),
    pedaco('IEND', new Uint8Array(0)),
  ];
  const total = partes.reduce((s, p) => s + p.length, 0);
  const png = new Uint8Array(total);
  let pos = 0;
  for (const p of partes) {
    png.set(p, pos);
    pos += p.length;
  }
  return `data:image/png;base64,${base64(png)}`;
}

function escrever32(alvo: Uint8Array, pos: number, valor: number) {
  alvo[pos] = (valor >>> 24) & 0xff;
  alvo[pos + 1] = (valor >>> 16) & 0xff;
  alvo[pos + 2] = (valor >>> 8) & 0xff;
  alvo[pos + 3] = valor & 0xff;
}

let tabelaCrc: Uint32Array | null = null;

function crc32(dados: Uint8Array, inicio = 0xffffffff) {
  if (!tabelaCrc) {
    tabelaCrc = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      tabelaCrc[n] = c >>> 0;
    }
  }
  let c = inicio;
  for (let i = 0; i < dados.length; i++) c = tabelaCrc[(c ^ dados[i]) & 0xff] ^ (c >>> 8);
  return c >>> 0;
}

function pedaco(tipo: string, dados: Uint8Array) {
  const saida = new Uint8Array(12 + dados.length);
  escrever32(saida, 0, dados.length);
  for (let i = 0; i < 4; i++) saida[4 + i] = tipo.charCodeAt(i);
  saida.set(dados, 8);
  escrever32(saida, 8 + dados.length, (crc32(saida.subarray(4, 8 + dados.length)) ^ 0xffffffff) >>> 0);
  return saida;
}

/** zlib com blocos "stored" (sem compressão): simples e válido para qualquer leitor de PNG. */
function zlibSemCompressao(dados: Uint8Array) {
  const MAX = 65535;
  const blocos = Math.max(1, Math.ceil(dados.length / MAX));
  const saida = new Uint8Array(2 + blocos * 5 + dados.length + 4);
  saida[0] = 0x78;
  saida[1] = 0x01;
  let pos = 2;
  for (let b = 0; b < blocos; b++) {
    const parte = dados.subarray(b * MAX, Math.min(dados.length, (b + 1) * MAX));
    saida[pos++] = b === blocos - 1 ? 1 : 0;
    saida[pos++] = parte.length & 0xff;
    saida[pos++] = (parte.length >>> 8) & 0xff;
    saida[pos++] = ~parte.length & 0xff;
    saida[pos++] = (~parte.length >>> 8) & 0xff;
    saida.set(parte, pos);
    pos += parte.length;
  }
  let a = 1;
  let s = 0;
  for (let i = 0; i < dados.length; i++) {
    a = (a + dados[i]) % 65521;
    s = (s + a) % 65521;
  }
  escrever32(saida, pos, ((s << 16) | a) >>> 0);
  return saida;
}

const ALFABETO = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

function base64(bytes: Uint8Array) {
  let saida = '';
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    saida += ALFABETO[(n >> 18) & 63] + ALFABETO[(n >> 12) & 63] + ALFABETO[(n >> 6) & 63] + ALFABETO[n & 63];
  }
  const resto = bytes.length - i;
  if (resto === 1) {
    const n = bytes[i] << 16;
    saida += `${ALFABETO[(n >> 18) & 63]}${ALFABETO[(n >> 12) & 63]}==`;
  } else if (resto === 2) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
    saida += `${ALFABETO[(n >> 18) & 63]}${ALFABETO[(n >> 12) & 63]}${ALFABETO[(n >> 6) & 63]}=`;
  }
  return saida;
}
