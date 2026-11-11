/* Auditoria D-1 · esteira de verificação de vendas (portado do sistema Flask para o portal).
   Mudanças: URLs sob /auditoria-d1/api/, CSRF do Django, autor = usuário logado (sem a janela "Quem está
   verificando?") e botões/atalhos de gravação só para quem tem o papel (AUDITOR/GESTOR/SUPERADMIN). */
(() => {
  "use strict";

  const APP = document.getElementById("d1App");
  if (!APP) return;
  const BASE = APP.dataset.base || "/auditoria-d1/";
  const PODE = { auditar: APP.dataset.auditar === "1", gerir: APP.dataset.gerir === "1" };
  const URL_ = s => BASE + s.replace(/^\//, "");

  // ------------------------------------------------------------ estado
  const S = {
    meta: null,
    mes: null,          // "2026-10"
    dia: null,          // "2026-10-23"
    dias: [],
    itens: [],
    porId: new Map(),
    visiveis: [],       // ids na ordem da lista filtrada
    sel: null,          // id_servico selecionado
    filtro: "todas",
    busca: "",
    pdv: "",
    pilar: "",
    form: null,
    salvando: false,
    pedidoDia: 0,
    pedidoMes: 0,
    auditor: APP.dataset.usuario || "",
  };

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
  const SEM = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];

  function lerLocal(k) { try { return localStorage.getItem(k); } catch { return null; } }
  function gravarLocal(k, v) { try { localStorage.setItem(k, v); } catch { /* sem storage */ } }

  // ------------------------------------------------------------ formatação
  const brl = v => v == null || v === "" ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  const dig = s => String(s ?? "").replace(/\D/g, "");
  const dataBR = s => { if (!s) return "—"; const [a, m, d] = String(s).slice(0, 10).split("-"); return `${d}/${m}/${a}`; };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const titulo = s => String(s || "").toLowerCase().replace(/(^|\s)\S/g, c => c.toUpperCase());
  function doc(s) {
    const d = dig(s);
    if (d.length === 11) return d.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/, "$1.$2.$3-$4");
    if (d.length === 14) return d.replace(/(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})/, "$1.$2.$3/$4-$5");
    return s || "—";
  }
  function fone(s) {
    const d = dig(s);
    if (d.length === 11) return d.replace(/(\d{2})(\d{5})(\d{4})/, "($1) $2-$3");
    if (d.length === 10) return d.replace(/(\d{2})(\d{4})(\d{4})/, "($1) $2-$3");
    return s || "—";
  }
  const numBR = s => { if (s == null || String(s).trim() === "") return null; const t = String(s).replace(/[R$\s]/g, ""); const n = Number(t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t); return isNaN(n) ? NaN : n; };
  const statusDe = it => it.verificacao ? it.verificacao.status : "PENDENTE";
  const auto = v => !!v && !!v.origem && !["MANUAL", "CONTESTACAO"].includes(v.origem);
  const ORIGENS = { CRUZAMENTO_SVA: ["SVA auto", "Automação SVA"], CRUZAMENTO_FIXA: ["Fixa auto", "Automação Fixa"] };
  const rotuloAuto = (v, longo) => (ORIGENS[v?.origem] || ["Auto", "Automação"])[longo ? 1 : 0];
  const PILAR_COR = { "Móvel": "movel", "Fixa": "fixa", "SVA": "sva", "Seguros": "seguro" };

  // ------------------------------------------------------------ API
  function csrf() {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : (APP.querySelector("[name=csrfmiddlewaretoken]")?.value || "");
  }
  const existe = s => !!$(s);

  async function api(url, opt = {}) {
    const r = await fetch(URL_(url), {
      ...opt,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), ...(opt.headers || {}) },
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.erro || `Erro ${r.status}`);
    return j;
  }

  function toast(msg, tipo = "ok") {
    const t = document.createElement("div");
    t.className = `toast ${tipo}`;
    t.textContent = msg;
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), tipo === "ok" ? 2200 : 7000);
  }

  // ------------------------------------------------------------ dias do mês
  async function carregarMes(mes, diaPreferido) {
    S.mes = mes;
    const [a, m] = mes.split("-").map(Number);
    $("#mesTexto").textContent = `${MESES[m - 1]} ${a}`;
    $("#mesInput").value = mes;
    const pedido = ++S.pedidoMes;
    const r = await api(`/api/dias?mes=${mes}`);
    if (pedido !== S.pedidoMes) return;
    S.dias = r.dias;
    let dia = diaPreferido;
    if (!dia || !dia.startsWith(mes)) {
      const hoje = S.meta.hoje;
      dia = hoje.startsWith(mes) ? hoje : (S.dias.find(d => d.verificadas < d.total) || S.dias[0] || {}).dia || `${mes}-01`;
    }
    desenharDias();
    await carregarDia(dia);
  }

  function desenharDias() {
    const [a, m] = S.mes.split("-").map(Number);
    const ultimo = new Date(a, m, 0).getDate();
    const mapa = new Map(S.dias.map(d => [d.dia, d]));
    const html = [];
    for (let d = 1; d <= ultimo; d++) {
      const iso = `${S.mes}-${String(d).padStart(2, "0")}`;
      const info = mapa.get(iso);
      const futuro = iso > S.meta.hoje;
      if (futuro && !info) continue;
      const pct = info ? Math.round(info.verificadas / info.total * 100) : 0;
      const cls = ["dia", iso === S.dia && "ativo", iso === S.meta.hoje && "hoje", !info && "sem-venda",
        info && pct === 100 && "completo", info && pct > 0 && pct < 100 && "parcial"].filter(Boolean).join(" ");
      const sem = SEM[new Date(a, m - 1, d).getDay()];
      html.push(`<button class="${cls}" data-dia="${iso}" ${info ? "" : "disabled"} title="${info ? `${info.verificadas}/${info.total} verificadas · ${info.divergentes} divergentes` : "Sem vendas"}">
        <div class="sem">${sem}</div><div class="nro">${d}</div>
        <div class="qtd num">${info ? info.total : "–"}</div>
        <div class="barra"><i style="width:${pct}%"></i></div></button>`);
    }
    $("#dias").innerHTML = html.join("") || `<div class="fila-vazia">Nenhuma venda neste mês.</div>`;
    const ativo = $(".dia.ativo");
    if (ativo) ativo.scrollIntoView({ inline: "center", block: "nearest" });
  }

  function atualizarDiaAtual() {
    const info = S.dias.find(d => d.dia === S.dia);
    if (!info) return;
    info.verificadas = S.itens.filter(i => i.verificacao).length;
    info.divergentes = S.itens.filter(i => statusDe(i) === "DIVERGENTE").length;
    desenharDias();
  }

  // ------------------------------------------------------------ vendas do dia
  async function carregarDia(dia) {
    S.dia = dia;
    history.replaceState(null, "", `#${dia}`);
    desenharDias();
    $("#lista").innerHTML = '<div class="esq"></div>'.repeat(6);
    const dt = new Date(dia + "T12:00:00");
    const rot = dt.toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "long" });
    $("#resumoDia").textContent = rot.charAt(0).toUpperCase() + rot.slice(1);
    const pedido = ++S.pedidoDia;
    let itens = [];
    try {
      itens = (await api(`/api/vendas?data=${dia}`)).itens;
    } catch (e) {
      toast(e.message, "erro");
    }
    if (pedido !== S.pedidoDia) return;  // usuário já trocou de dia
    S.itens = itens;
    S.porId = new Map(S.itens.map(i => [i.id_servico, i]));
    preencherFiltros();
    const manter = S.sel && S.porId.has(S.sel);
    desenharLista();
    selecionar(manter ? S.sel : (S.visiveis.find(id => !S.porId.get(id).verificacao) || S.visiveis[0] || null), false);
  }

  function preencherFiltros() {
    const opcoes = (sel, valores, todos) => {
      const atual = sel.value;
      sel.innerHTML = `<option value="">${todos}</option>` + valores.map(v => `<option>${esc(v)}</option>`).join("");
      sel.value = valores.includes(atual) ? atual : "";
    };
    opcoes($("#fPdv"), [...new Set(S.itens.map(i => i.pdv).filter(Boolean))].sort(), "Todas as filiais");
    opcoes($("#fPilar"), [...new Set(S.itens.map(i => i.pilar).filter(Boolean))].sort(), "Todos os pilares");
    S.pdv = $("#fPdv").value;
    S.pilar = $("#fPilar").value;
  }

  function passaFiltro(i) {
    const st = statusDe(i);
    if (S.filtro === "pendentes" && st !== "PENDENTE") return false;
    if (S.filtro === "conformes" && st !== "CONFORME") return false;
    if (S.filtro === "divergentes" && st !== "DIVERGENTE") return false;
    if (S.filtro === "alertas" && !i.alertas.some(a => a.nivel !== "baixo")) return false;
    if (S.filtro === "auto" && !auto(i.verificacao)) return false;
    if (S.filtro === "contestadas" && !(st === "CONTESTADA" || i.verificacao?.contestacao)) return false;
    if (S.pdv && i.pdv !== S.pdv) return false;
    if (S.pilar && i.pilar !== S.pilar) return false;
    if (S.busca) {
      const b = S.busca.toLowerCase(), bd = dig(S.busca);
      const txt = [i.cliente, i.vendedor, i.plano, i.servico, i.id_venda, i.id_servico].join(" ").toLowerCase();
      const nums = [i.cpf, i.documento, i.acesso, i.portabilidade, i.id_venda].map(dig).join(" ");
      if (!txt.includes(b) && !(bd.length >= 3 && nums.includes(bd))) return false;
    }
    return true;
  }

  function desenharLista() {
    const tot = S.itens.length;
    const conf = S.itens.filter(i => statusDe(i) === "CONFORME").length;
    const div = S.itens.filter(i => statusDe(i) === "DIVERGENTE").length;
    const alert = S.itens.filter(i => i.alertas.some(a => a.nivel !== "baixo")).length;
    const cont = S.itens.filter(i => statusDe(i) === "CONTESTADA").length;
    const feitas = conf + div + cont;
    const pct = tot ? Math.round(feitas / tot * 100) : 0;
    $("#cPend").textContent = tot - feitas;
    $("#cConf").textContent = conf;
    $("#cDiv").textContent = div;
    $("#cAlert").textContent = alert;
    $("#anelPct").textContent = `${pct}%`;
    $("#anelValor").style.strokeDashoffset = 97.4 * (1 - pct / 100);
    $("#anelValor").style.stroke = pct === 100 ? "var(--ok)" : "var(--roxo)";
    $("#resumoTxt").textContent = tot ? `${feitas} de ${tot} serviços verificados` : "Nenhuma venda neste dia";
    $$(".cont, .chip-filtro").forEach(b => b.classList.toggle("ativo", b.dataset.filtro === S.filtro));

    const lista = S.itens.filter(passaFiltro);
    S.visiveis = lista.map(i => i.id_servico);
    if (!lista.length) {
      $("#lista").innerHTML = tot
        ? `<div class="fila-vazia"><b>${S.filtro === "pendentes" ? "Tudo verificado por aqui 🎉" : "Nada encontrado"}</b>Ajuste os filtros para ver outras vendas.</div>`
        : `<div class="fila-vazia"><b>Sem vendas</b>Não há vendas no escopo da auditoria neste dia.</div>`;
      return;
    }
    const grupos = new Map();
    lista.forEach(i => { if (!grupos.has(i.id_venda)) grupos.set(i.id_venda, []); grupos.get(i.id_venda).push(i); });
    const html = [];
    for (const [venda, its] of grupos) {
      const f = its[0];
      const pend = S.itens.filter(i => i.id_venda === venda && !i.verificacao).length;
      const pilares = [...new Set(its.map(i => i.pilar).filter(Boolean))];
      html.push(`<div class="grupo"><div class="grupo-cab">
        <div class="gc-info"><div class="gc-l1"><b class="gc-venda num" title="Nº da venda">#${esc(venda)}</b>
          ${pilares.map(p => `<span class="pilar-chip ${PILAR_COR[p] || ""}">${esc(p)}</span>`).join("")}<span class="gc-pdv">${esc(f.pdv)}</span></div>
        <div class="gc-l2" title="${esc(f.cliente)}">${esc(titulo(f.cliente))}</div></div>
        ${PODE.auditar && pend > 1 ? `<button class="btn-lote" data-lote="${esc(venda)}" title="Marcar os ${pend} serviços pendentes desta venda como conformes">✓ Venda conforme</button>` : ""}
        </div>`);
      for (const i of its) {
        const st = statusDe(i), v = i.verificacao;
        const alto = i.alertas.filter(a => a.nivel !== "baixo").length;
        let tag = "";
        if (st === "DIVERGENTE") tag = `<div class="i-tag div">${auto(v) ? `<span class="selo-auto">${rotuloAuto(v)}</span>` : ""}${v.contestacao ? '<span class="selo-cont" title="Contestação improcedente">contestada</span>' : ""}${esc(v.correcao)}</div>`;
        else if (st === "CONTESTADA") tag = `<div class="i-tag cont">↺ Contestada · volta</div>`;
        else if (st === "CONFORME") tag = `<div class="i-tag ok">Conforme</div>`;
        else if (alto) tag = `<div class="i-tag alerta">⚠ ${alto} alerta${alto > 1 ? "s" : ""}</div>`;
        const rec = st === "DIVERGENTE" && v.receita_validada !== i.receita
          ? `<s>${brl(i.receita)}</s>${brl(v.receita_validada)}` : brl(i.receita);
        html.push(`<div class="item st-${st} ${i.id_servico === S.sel ? "ativo" : ""}" data-id="${esc(i.id_servico)}">
          <span class="ponto"></span>
          <div style="min-width:0"><div class="i-serv">${esc(i.servico)}</div><div class="i-plano" title="${esc(i.plano)}">${esc(i.plano || "—")}</div></div>
          <div class="i-dir"><div class="i-rec">${rec}</div>${tag}</div></div>`);
      }
      html.push("</div>");
    }
    $("#lista").innerHTML = html.join("");
  }

  // ------------------------------------------------------------ seleção / detalhe
  function selecionar(id, rolar = true) {
    S.sel = id;
    $$(".item").forEach(el => el.classList.toggle("ativo", el.dataset.id === id));
    const it = id && S.porId.get(id);
    if (!it) {
      $("#detalhe").innerHTML = `<div class="vazio"><div class="vazio-ico">✓</div><div class="vazio-tit">Selecione uma venda</div><div class="vazio-txt">Use <kbd>J</kbd> / <kbd>K</kbd> para percorrer a esteira.</div></div>`;
      $("#verif").classList.add("desativado");
      $("#verifSub").textContent = "Nenhuma venda selecionada";
      return;
    }
    if (rolar) $(`.item[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" });
    desenharDetalhe(it);
    iniciarForm(it);
  }

  function destaque(rot, val, sub, { copiar, alerta, corrigido } = {}) {
    return `<div class="dest ${alerta ? "alerta" : ""} ${corrigido ? "corrigido" : ""}">
      <div class="d-rot"><span>${rot}</span>${copiar ? `<button class="copiar" data-copiar="${esc(copiar)}" title="Copiar">⧉</button>` : ""}</div>
      <div class="d-val">${val}</div>${sub ? `<div class="d-sub">${sub}</div>` : ""}</div>`;
  }

  function desenharDetalhe(it) {
    const v = it.verificacao, st = statusDe(it);
    const pos = S.visiveis.indexOf(it.id_servico);
    const temAlerta = txt => it.alertas.some(a => a.txt.includes(txt));
    const div = st === "DIVERGENTE" ? v : null;

    const vPlano = div?.plano_correto ? `<s>${esc(it.plano)}</s><em>${esc(div.plano_correto)}</em>` : esc(it.plano || "—");
    const vRec = div && div.receita_validada !== it.receita ? `<s>${brl(it.receita)}</s><em>${brl(div.receita_validada)}</em>` : brl(it.receita);
    const vAcesso = div?.linha_correta ? `<s>${fone(it.acesso)}</s><em>${fone(div.linha_correta)}</em>` : fone(it.acesso);
    const vDoc = div?.documento_correto ? `<s>${doc(it.cpf)}</s><em>${doc(div.documento_correto)}</em>` : doc(it.cpf);

    const irmaos = S.itens.filter(i => i.id_venda === it.id_venda && i.id_servico !== it.id_servico);
    const dados = [
      ["Nº da venda", it.id_venda], ["ID do serviço", it.id_servico], ["Data da venda", dataBR(it.data_venda)],
      ["Filial (PDV)", it.pdv], ["Vendedor", titulo(it.vendedor)], ["Cliente", titulo(it.cliente)],
      ["Documento cadastrado", doc(it.documento)], ["Cidade / UF", it.cidade_cliente ? `${titulo(it.cidade_cliente)} / ${it.uf_cliente || ""}` : "—"],
      ["Plano anterior", it.plano_antigo || "—"], ["Portabilidade", it.portabilidade ? fone(it.portabilidade) : "—"],
      ["Status do serviço", it.status_servico || "—"], ["Status BKO", it.status_bko || "—"],
      ["Ativação", it.data_ativacao ? dataBR(it.data_ativacao) : "—"], ["Última ativação", it.ultima_ativacao ? dataBR(it.ultima_ativacao) : "—"],
      ["Grupo do serviço", it.servico_grupo || "—"],
    ];
    if (it.obs_venda) dados.push(["Observação da venda", it.obs_venda]);

    $("#detalhe").innerHTML = `
      <div class="det-cab">
        <div class="det-topo">
          <div class="det-pos"><button class="nav-btn" data-nav="-1" title="Anterior (K)">↑</button><button class="nav-btn" data-nav="1" title="Próxima (J)">↓</button>
            <span class="num">${pos + 1} de ${S.visiveis.length}</span></div>
          <span class="det-badges">${auto(v) ? `<span class="badge auto" title="${esc(v.confianca || "")}">⚙ ${rotuloAuto(v, true)}</span>` : ""}<span class="badge ${st}">${{ PENDENTE: "Pendente", CONFORME: "✓ Conforme", DIVERGENTE: "! Divergente", CONTESTADA: "↺ Contestada" }[st] || st}</span></span>
        </div>
        <div class="det-serv"><h1>${esc(it.servico)}</h1>${it.pilar ? `<span class="badge pilar">${esc(it.pilar)}</span>` : ""}</div>
        <div class="det-plano"><span class="det-venda num" title="Nº da venda">#${esc(it.id_venda)}</span>${esc(titulo(it.cliente))} · ${esc(it.pdv)}</div>
      </div>
      <div class="det-corpo">
        <div class="destaques">
          ${destaque("Pilar", `<span class="pilar-val ${PILAR_COR[it.pilar] || ""}">${esc(it.pilar || "—")}</span>`, `${esc(it.servico_grupo || "—")} · ${esc(it.servico || "")}`)}
          ${destaque("Plano / serviço", vPlano, `Valor do plano ${brl(it.valor_plano)}`, { copiar: it.plano, corrigido: !!div?.plano_correto })}
          ${destaque("Receita", vRec, it.valor_plano != null && Number(it.valor_plano) !== Number(it.receita) ? "difere do valor do plano" : "igual ao valor do plano", { corrigido: div && div.receita_validada !== it.receita })}
          ${destaque("Nº de acesso", vAcesso, it.portabilidade ? `Portab. ${fone(it.portabilidade)}` : "", { copiar: dig(it.acesso), alerta: temAlerta("Número"),corrigido: !!div?.linha_correta })}
          ${destaque("CPF / CNPJ", vDoc, dig(it.cpf) !== dig(it.documento) && it.documento ? `Documento: ${doc(it.documento)}` : "", { copiar: dig(it.cpf), alerta: temAlerta("CPF") || temAlerta("Documento"),corrigido: !!div?.documento_correto })}
        </div>
        ${it.alertas.length ? `<div><div class="secao-tit">Pontos de atenção</div><div class="alertas">${it.alertas.map(a => `
          <div class="alerta-item ${a.nivel}"><span class="a-ico">${a.nivel === "baixo" ? "i" : "!"}</span>
            <div class="a-txt">${esc(a.txt)}${a.vendas ? `<div class="a-vendas">${a.vendas.map(x => `#${esc(x.id_venda)}${x.servico ? ` · serv. ${esc(x.servico)}${x.servico === it.id_servico ? " (esta)" : ""}` : ""} em ${dataBR(x.data)}`).join(" · ")}</div>` : ""}</div>
            ${PODE.auditar && a.sugere && S.meta.correcoes.includes(a.sugere) ? `<button class="a-sug" data-sugere="${esc(a.sugere)}" data-pen="${esc(a.penalidade || "")}">Marcar ${esc(titulo(a.penalidade || a.sugere))}</button>` : ""}
          </div>`).join("")}</div></div>` : ""}
        <div><div class="secao-tit">Dados da venda</div>
          <div class="dados">${dados.map(([k, val]) => `<div class="dado"><span>${k}</span><b>${esc(val ?? "—")}</b></div>`).join("")}</div>
        </div>
        ${irmaos.length ? `<div><div class="secao-tit">Outros serviços desta venda</div><div class="irmaos">${irmaos.map(i => `
          <div class="irmao st-${statusDe(i)}" data-id="${esc(i.id_servico)}"><span class="ponto"></span>
            <div><b>${esc(i.servico)}</b><small>${esc(i.plano || "")}</small></div><b class="num">${brl(i.receita)}</b></div>`).join("")}</div></div>` : ""}
      </div>`;
  }

  // ------------------------------------------------------------ formulário de verificação
  function iniciarForm(it) {
    const v = it.verificacao;
    S.form = {
      status: v?.status || null,
      correcao: v?.correcao || null,
      penalidade: v?.penalidade || null,
      receita_correta: v?.receita_correta ?? null,
      plano_correto: v?.plano_correto || "",
      linha_correta: v?.linha_correta || "",
      documento_correto: v?.documento_correto || "",
      observacao: v?.observacao || "",
    };
    $("#verif").classList.remove("desativado");
    $("#verifSub").textContent = `${it.servico} · #${it.id_venda}`;
    $("#recCorreta").value = S.form.receita_correta != null ? String(S.form.receita_correta).replace(".", ",") : "";
    $("#planoCorreto").value = S.form.plano_correto;
    $("#linhaCorreta").value = S.form.linha_correta ? fone(S.form.linha_correta) : "";
    $("#docCorreto").value = S.form.documento_correto ? doc(S.form.documento_correto) : "";
    $("#obs").value = S.form.observacao;
    $("#penBusca").value = "";
    $("#btnReabrir").hidden = !v || !PODE.auditar;
    $("#verifInfo").textContent = !v ? "" : auto(v)
      ? `Marcado pela ${rotuloAuto(v, true)} (base ${v.anomes || "—"} · ${v.confianca || "—"}). Uma nova base do mês pode refazer esta marcação.`
      : `Verificado por ${v.por || "—"} em ${new Date(v.em).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}`;
    desenharContestacao(it);
    desenharForm();
  }

  // ------------------------------------------------------------ contestação
  const quando = s => s ? new Date(s).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";

  function desenharContestacao(it) {
    const v = it.verificacao, box = $("#contestBox");
    const c = v?.contestacao;
    if (!v || !["DIVERGENTE", "CONTESTADA"].includes(v.status) || (!PODE.auditar && !c && v.status !== "CONTESTADA")) { box.hidden = true; return; }
    box.hidden = false;
    if (v.status === "CONTESTADA") {
      const d = v.divergencia_contestada || {};
      box.className = "contest procedente";
      box.innerHTML = `<div class="ct-tit">↺ Contestação procedente — a venda voltou</div>
        <div class="ct-txt"><b>${esc(c?.solicitante || "—")}</b>: ${esc(c?.motivo || "")}</div>
        <div class="ct-txt">Divergência desfeita: ${esc(titulo(d.correcao || v.correcao || "—"))}${d.penalidade ? ` · ${esc(d.penalidade)}` : ""}${d.origem && d.origem !== "MANUAL" ? ` (${esc(rotuloAuto(d, true))})` : ""}</div>
        <div class="ct-meta">Analisada por ${esc(c?.por || "—")} em ${quando(c?.em)} · sai no Exportar para voltar a venda no VivoGo</div>
        <button class="btn-sec" id="btnDesfazerContest">Desfazer contestação</button>`;
      return;
    }
    box.className = "contest";
    box.innerHTML = c
      ? `<div class="ct-tit">✕ Contestação improcedente</div>
         <div class="ct-txt"><b>${esc(c.solicitante || "—")}</b>: ${esc(c.motivo || "")}</div>
         <div class="ct-meta">Analisada por ${esc(c.por || "—")} em ${quando(c.em)}</div>
         <div class="ct-acoes"><button class="btn-sec" id="btnContestar">Nova contestação</button><button class="btn-sec" id="btnDesfazerContest">Apagar registro</button></div>`
      : `<div class="ct-tit">Contestação</div><div class="ct-meta">O vendedor/gerente contestou esta divergência?</div>
         <button class="btn-sec" id="btnContestar">Registrar contestação</button>`;
  }

  const CT = { decisao: null };
  function abrirContestacao() {
    if (!PODE.auditar || !existe("#modalContest")) return;
    const it = S.porId.get(S.sel);
    if (!it?.verificacao) return;
    CT.decisao = null;
    $("#contestSub").innerHTML = `Venda <b>#${esc(it.id_venda)}</b> · ${esc(it.servico)} · divergência <b>${esc(titulo(it.verificacao.correcao || ""))}</b>${it.verificacao.penalidade ? ` · ${esc(it.verificacao.penalidade)}` : ""}`;
    $("#contestSolic").value = "";
    $("#contestMotivo").value = "";
    $$("#formContest [data-decisao]").forEach(b => b.classList.remove("sel"));
    validarContestacao();
    $("#modalContest").hidden = false;
    setTimeout(() => $("#contestSolic").focus(), 0);
  }
  function validarContestacao() {
    $("#btnContestSalvar").disabled = !(CT.decisao && $("#contestSolic").value.trim() && $("#contestMotivo").value.trim());
    $("#btnContestSalvar").textContent = CT.decisao === "PROCEDENTE" ? "Registrar e voltar a venda" : "Registrar contestação";
  }
  async function salvarContestacao(e) {
    e.preventDefault();
    const it = S.porId.get(S.sel);
    if (!it || !CT.decisao) return;
    const btn = $("#btnContestSalvar");
    btn.disabled = true;
    try {
      const r = await api("/api/contestacoes", { method: "POST", body: JSON.stringify({
        id_servico: it.id_servico, decisao: CT.decisao, solicitante: $("#contestSolic").value.trim(), motivo: $("#contestMotivo").value.trim() }) });
      const v = it.verificacao;
      v.contestacao = { decisao: CT.decisao, solicitante: r.contestacao_solicitante, motivo: r.contestacao_motivo, por: r.contestacao_por, em: r.contestacao_em };
      if (CT.decisao === "PROCEDENTE") {
        v.divergencia_contestada = { correcao: v.correcao, penalidade: v.penalidade, receita_validada: v.receita_validada, origem: v.origem, por: v.por };
        Object.assign(v, { status: "CONTESTADA", origem: "CONTESTACAO", acao: "VOLTAR VENDA", receita_validada: v.receita_original ?? it.receita });
      }
      $("#modalContest").hidden = true;
      toast(CT.decisao === "PROCEDENTE" ? `Venda #${it.id_venda} voltou — vai no próximo Exportar` : "Contestação improcedente registrada");
      desenharLista(); atualizarDiaAtual(); selecionar(it.id_servico, false);
    } catch (err) {
      toast(err.message, "erro");
      validarContestacao();
    }
  }
  async function desfazerContestacao() {
    if (!PODE.auditar) return;
    const it = S.porId.get(S.sel);
    if (!it?.verificacao) return;
    const proc = it.verificacao.status === "CONTESTADA";
    if (proc && !confirm(`Desfazer a contestação da venda #${it.id_venda}? A divergência volta e a venda sai do Exportar como divergente.`)) return;
    try {
      await api(`/api/contestacoes/${encodeURIComponent(it.id_servico)}`, { method: "DELETE" });
      toast(proc ? "Contestação desfeita — a divergência voltou" : "Registro da contestação apagado");
      await carregarDia(S.dia);
    } catch (e) { toast(e.message, "erro"); }
  }

  function desenharCorrecoes() {
    const it = S.porId.get(S.sel);
    const sugeridas = new Set((it?.alertas || []).map(a => a.sugere).filter(Boolean));
    $("#correcoes").innerHTML = S.meta.correcoes.map((c, idx) => {
      const r = S.meta.regras[c] || {};
      return `<button class="corr ${S.form.correcao === c ? "sel" : ""} ${sugeridas.has(c) ? "sugerida" : ""}" data-corr="${esc(c)}" title="${esc(r.dica || "")}">
        <span class="k">${idx + 1}</span><span>${esc(titulo(c))}</span><span class="acao">${esc(r.acao || "")}</span></button>`;
    }).join("");
  }

  function desenharPenalidades() {
    const b = $("#penBusca").value.trim().toLowerCase();
    let lista = S.meta.penalidades.filter(p => !b || p.toLowerCase().includes(b));
    if (!b && S.form.penalidade) lista = [S.form.penalidade, ...lista.filter(p => p !== S.form.penalidade)];
    $("#penLista").innerHTML = lista.map((p, i) => `<button class="pen ${S.form.penalidade === p ? "sel" : ""} ${b && i === 0 ? "foco" : ""}" data-pen="${esc(p)}">${esc(p)}</button>`).join("")
      || `<div class="pen" style="color:var(--cinza-5)">Nenhuma penalidade encontrada</div>`;
  }

  function desenharForm() {
    const f = S.form, it = S.porId.get(S.sel);
    $("#decOk").classList.toggle("sel", f.status === "CONFORME");
    $("#decDiv").classList.toggle("sel", f.status === "DIVERGENTE");
    const div = f.status === "DIVERGENTE";
    $("#divForm").hidden = !div;
    if (div) {
      desenharCorrecoes();
      const regra = S.meta.regras[f.correcao] || { campos: [] };
      $("#dica").textContent = f.correcao ? regra.dica || "" : "Escolha a categoria do problema encontrado.";
      const campos = regra.campos || [];
      $$("#divForm .campo").forEach(el => {
        const c = el.dataset.campo;
        el.hidden = !(campos.includes(c) || campos.includes(c + "?"));
      });
      $("#recOpc").hidden = !campos.includes("receita_correta?");
      if (campos.includes("tipo_penalidade")) desenharPenalidades();
    }
    // impacto
    const { acao, receita } = impacto();
    $("#impAcao").textContent = acao || "—";
    const rb = $("#impReceita");
    rb.textContent = receita == null ? "—" : brl(receita);
    rb.className = receita === 0 && Number(it?.receita) !== 0 ? "zero" : (receita != null && Number(receita) !== Number(it?.receita) ? "alt" : "");
    const erro = validar();
    $("#btnSalvar").disabled = !!erro || S.salvando;
    $("#btnSalvar").title = erro || "";
  }

  function impacto() {
    const f = S.form, it = S.porId.get(S.sel);
    if (!f || !it || !f.status) return { acao: null, receita: null };
    const rec = Number(it.receita || 0);
    if (f.status === "CONFORME") return { acao: "Nenhuma", receita: rec };
    if (f.status === "CONTESTADA") return { acao: "Voltar venda (contestação)", receita: it.verificacao?.receita_original ?? rec };
    const regra = S.meta.regras[f.correcao];
    if (!regra) return { acao: null, receita: null };
    if (regra.zera) return { acao: regra.acao, receita: 0 };
    const rc = numBR($("#recCorreta").value);
    const usaRec = (regra.campos || []).some(c => c.startsWith("receita_correta"));
    return { acao: regra.acao || "Sem ação", receita: usaRec && rc != null && !isNaN(rc) ? rc : rec };
  }

  function validar() {
    const f = S.form;
    if (!f?.status) return "Escolha Conforme ou Divergente";
    if (f.status === "CONFORME") return null;
    if (!f.correcao) return "Escolha a correção VivoGo";
    const campos = S.meta.regras[f.correcao]?.campos || [];
    if (campos.includes("tipo_penalidade") && !f.penalidade) return "Escolha a penalidade";
    const rc = numBR($("#recCorreta").value);
    if (campos.includes("receita_correta") && (rc == null || isNaN(rc))) return "Informe a receita correta";
    if (campos.includes("receita_correta?") && rc != null && isNaN(rc)) return "Receita inválida";
    if (campos.includes("plano_correto") && !$("#planoCorreto").value.trim()) return "Informe o plano correto";
    if (campos.includes("linha_correta") && dig($("#linhaCorreta").value).length < 10) return "Informe a linha correta com DDD";
    if (campos.includes("documento_correto") && ![11, 14].includes(dig($("#docCorreto").value).length)) return "Informe o CPF/CNPJ correto";
    return null;
  }

  function definirStatus(st) {
    if (!S.sel || !PODE.auditar) return;
    S.form.status = st;
    desenharForm();
  }

  function definirCorrecao(c) {
    if (!S.sel || !PODE.auditar) return;
    S.form.status = "DIVERGENTE";
    S.form.correcao = c;
    if (c !== "MOVIMENTO INELEGÍVEL") S.form.penalidade = null;
    desenharForm();
    const campos = S.meta.regras[c]?.campos || [];
    const foco = { tipo_penalidade: "#penBusca", receita_correta: "#recCorreta", plano_correto: "#planoCorreto", linha_correta: "#linhaCorreta", documento_correto: "#docCorreto" }[campos[0]?.replace("?", "")];
    if (foco) setTimeout(() => $(foco).focus(), 0);
  }

  function payload(it, f) {
    return {
      id_servico: it.id_servico, data_venda: it.data_venda, status: f.status, correcao: f.correcao, penalidade: f.penalidade,
      receita_correta: $("#recCorreta").value.trim() || null, plano_correto: $("#planoCorreto").value.trim(),
      linha_correta: $("#linhaCorreta").value, documento_correto: $("#docCorreto").value, observacao: $("#obs").value.trim(),
    };
  }

  // Salvamento otimista: a esteira avança na hora; se o servidor recusar, a venda volta ao estado anterior.
  async function salvar() {
    if (!PODE.auditar) return;
    const it = S.porId.get(S.sel);
    if (!it) return;
    const erro = validar();
    if (erro) return toast(erro, "erro");
    const p = payload(it, S.form);
    const div = p.status === "DIVERGENTE";
    const campos = div ? S.meta.regras[p.correcao]?.campos || [] : [];
    const tem = c => campos.includes(c) || campos.includes(c + "?");
    const { acao, receita } = impacto();
    const anterior = it.verificacao;
    it.verificacao = {
      status: p.status, correcao: div ? p.correcao : null, penalidade: tem("tipo_penalidade") ? p.penalidade : null,
      acao: div ? acao : null, receita_correta: tem("receita_correta") ? numBR(p.receita_correta) : null,
      plano_correto: tem("plano_correto") ? p.plano_correto : null, linha_correta: tem("linha_correta") ? dig(p.linha_correta) : null,
      documento_correto: tem("documento_correto") ? dig(p.documento_correto) : null, receita_validada: receita,
      alterar_vivogo: div, observacao: p.observacao, por: S.auditor, em: new Date().toISOString(), salvando: true,
    };
    proximaPendente();
    try {
      const r = await api("/api/verificacoes", { method: "POST", body: JSON.stringify({ itens: [p] }) });
      const ret = r.itens[0];
      Object.assign(it.verificacao, { acao: ret.acao, receita_validada: ret.receita_validada, salvando: false });
      toast(div ? `Divergência registrada · ${titulo(p.correcao)}` : "Venda conforme");
    } catch (e) {
      it.verificacao = anterior;
      toast(`Não salvou a venda #${it.id_venda} (${it.servico}): ${e.message}`, "erro");
      desenharLista();
      atualizarDiaAtual();
      selecionar(it.id_servico);
    }
  }

  function proximaPendente() {
    const atual = S.sel;
    desenharLista();
    atualizarDiaAtual();
    const ordem = S.visiveis;
    const i = Math.max(0, ordem.indexOf(atual));
    const prox = ordem.slice(i + 1).find(id => !S.porId.get(id).verificacao)
      || ordem.slice(0, i).find(id => !S.porId.get(id).verificacao);
    if (prox) selecionar(prox);
    else {
      selecionar(ordem.includes(atual) ? atual : ordem[0] || null);
      if (S.itens.length && S.itens.every(x => x.verificacao)) toast("Dia concluído! Todas as vendas foram verificadas 🎉");
    }
  }

  async function loteConforme(venda) {
    if (!PODE.auditar) return;
    const its = S.itens.filter(i => i.id_venda === venda && !i.verificacao);
    if (!its.length) return;
    try {
      const r = await api("/api/verificacoes", { method: "POST", body: JSON.stringify({ itens: its.map(i => ({ id_servico: i.id_servico, data_venda: i.data_venda, status: "CONFORME" })) }) });
      const mapa = new Map(r.itens.map(x => [x.id_servico, x]));
      its.forEach(i => { i.verificacao = { status: "CONFORME", receita_validada: mapa.get(i.id_servico)?.receita_validada ?? i.receita, por: S.auditor, em: new Date().toISOString() }; });
      toast(`${its.length} serviços da venda #${venda} conformes`);
      const atual = S.porId.get(S.sel);
      if (atual && atual.id_venda === venda) proximaPendente();
      else { desenharLista(); atualizarDiaAtual(); selecionar(S.sel, false); }
    } catch (e) { toast(e.message, "erro"); }
  }

  async function reabrir() {
    if (!PODE.auditar) return;
    const it = S.porId.get(S.sel);
    if (!it?.verificacao) return;
    try {
      await api(`/api/verificacoes/${encodeURIComponent(it.id_servico)}`, { method: "DELETE" });
      it.verificacao = null;
      toast("Venda voltou para pendente");
      desenharLista();
      atualizarDiaAtual();
      selecionar(it.id_servico, false);
    } catch (e) { toast(e.message, "erro"); }
  }

  function navegar(passo) {
    const ordem = S.visiveis;
    if (!ordem.length) return;
    const i = ordem.indexOf(S.sel);
    const n = i < 0 ? 0 : Math.min(ordem.length - 1, Math.max(0, i + passo));
    selecionar(ordem[n]);
  }

  // ------------------------------------------------------------ eventos
  function ligarEventos() {
    $("#dias").addEventListener("click", e => { const b = e.target.closest(".dia"); if (b && !b.disabled) carregarDia(b.dataset.dia); });
    const trocaMes = delta => { const [a, m] = S.mes.split("-").map(Number); const d = new Date(a, m - 1 + delta, 1); carregarMes(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`); };
    $("#mesAnt").onclick = () => trocaMes(-1);
    $("#mesProx").onclick = () => trocaMes(1);
    $("#mesInput").onchange = e => e.target.value && carregarMes(e.target.value);
    ligarExportacao();
    if (existe("#modalSva")) ligarSva();
    if (existe("#modalFixa")) ligarFixa();

    $$(".cont, .chip-filtro").forEach(b => b.onclick = () => { S.filtro = S.filtro === b.dataset.filtro ? "todas" : b.dataset.filtro; desenharLista(); });
    let t;
    $("#busca").oninput = e => { clearTimeout(t); t = setTimeout(() => { S.busca = e.target.value.trim(); desenharLista(); }, 120); };
    $("#fPdv").onchange = e => { S.pdv = e.target.value; desenharLista(); };
    $("#fPilar").onchange = e => { S.pilar = e.target.value; desenharLista(); };

    $("#lista").addEventListener("click", e => {
      const lote = e.target.closest("[data-lote]");
      if (lote) return loteConforme(lote.dataset.lote);
      const it = e.target.closest(".item");
      if (it) selecionar(it.dataset.id, false);
    });
    $("#detalhe").addEventListener("click", e => {
      const nav = e.target.closest("[data-nav]");
      if (nav) return navegar(Number(nav.dataset.nav));
      const irm = e.target.closest(".irmao");
      if (irm) return selecionar(irm.dataset.id);
      const sug = e.target.closest("[data-sugere]");
      if (sug) { definirCorrecao(sug.dataset.sugere); if (sug.dataset.pen) { S.form.penalidade = sug.dataset.pen; desenharForm(); } return; }
      const cp = e.target.closest("[data-copiar]");
      if (cp) navigator.clipboard?.writeText(cp.dataset.copiar).then(() => toast("Copiado"));
    });

    $("#decOk").onclick = () => definirStatus("CONFORME");
    $("#decDiv").onclick = () => definirStatus("DIVERGENTE");
    $("#correcoes").addEventListener("click", e => { const b = e.target.closest("[data-corr]"); if (b) definirCorrecao(b.dataset.corr); });
    $("#penLista").addEventListener("click", e => { const b = e.target.closest("[data-pen]"); if (b) { S.form.penalidade = b.dataset.pen; desenharForm(); } });
    $("#penBusca").oninput = desenharPenalidades;
    $("#penBusca").onkeydown = e => {
      if (e.key === "Enter") {
        const p = $(".pen.foco") || $(".pen[data-pen]");
        if (p && $("#penBusca").value.trim()) { e.preventDefault(); e.stopPropagation(); S.form.penalidade = p.dataset.pen; $("#penBusca").value = ""; desenharForm(); }
      }
    };
    ["#recCorreta", "#planoCorreto", "#linhaCorreta", "#docCorreto", "#obs"].forEach(s => $(s).addEventListener("input", desenharForm));
    $("#linhaCorreta").addEventListener("blur", e => { if (dig(e.target.value).length >= 10) e.target.value = fone(e.target.value); });
    $("#docCorreto").addEventListener("blur", e => { e.target.value = dig(e.target.value) ? doc(e.target.value) : ""; });
    $("#btnSalvar").onclick = salvar;
    $("#btnReabrir").onclick = reabrir;
    $("#contestBox").addEventListener("click", e => {
      if (e.target.id === "btnContestar") abrirContestacao();
      if (e.target.id === "btnDesfazerContest") desfazerContestacao();
    });
    if (existe("#modalContest")) {
    $("#formContest").onsubmit = salvarContestacao;
    $$("#formContest [data-decisao]").forEach(b => b.onclick = () => {
      CT.decisao = b.dataset.decisao;
      $$("#formContest [data-decisao]").forEach(x => x.classList.toggle("sel", x === b));
      validarContestacao();
    });
    ["#contestSolic", "#contestMotivo"].forEach(x => $(x).addEventListener("input", validarContestacao));
    const fecharCt = () => { $("#modalContest").hidden = true; };
    $("#fecharContest").onclick = fecharCt;
    $("#modalContest").addEventListener("click", e => { if (e.target.id === "modalContest") fecharCt(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#modalContest").hidden) fecharCt(); });
    }

    document.addEventListener("keydown", e => {
      if (["#modalSva", "#modalFixa", "#modalContest"].some(m => existe(m) && !$(m).hidden)) return;
      const campo = e.target.closest?.("input, textarea, select");
      if (campo) {
        if (e.key === "Escape") campo.blur();
        else if (e.key === "Enter" && campo.tagName !== "TEXTAREA" && campo.closest("#verif")) { e.preventDefault(); salvar(); }
        else if (e.key === "Enter" && campo.tagName === "TEXTAREA" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); salvar(); }
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); navegar(1); }
      else if (k === "k" || e.key === "ArrowUp") { e.preventDefault(); navegar(-1); }
      else if (k === "c" && S.sel) { definirStatus("CONFORME"); salvar(); }
      else if (k === "d" && S.sel) definirStatus("DIVERGENTE");
      else if (/^[1-9]$/.test(e.key) && S.sel && S.meta.correcoes[Number(e.key) - 1]) definirCorrecao(S.meta.correcoes[Number(e.key) - 1]);
      else if (e.key === "Enter" && S.sel) { e.preventDefault(); salvar(); }
      else if (e.key === "/") { e.preventDefault(); $("#busca").focus(); }
    });

    window.addEventListener("hashchange", () => {
      const d = location.hash.slice(1);
      if (/^\d{4}-\d{2}-\d{2}$/.test(d) && d !== S.dia) d.startsWith(S.mes) ? carregarDia(d) : carregarMes(d.slice(0, 7), d);
    });
  }

  // ------------------------------------------------------------ exportação
  // Exportar = CSV no modelo de importação do VivoGo com as alterações do mês; o resumo vem no cabeçalho X-Resumo.
  function ligarExportacao() {
    $("#btnPlanilha").onclick = () => { window.location = URL_(`/api/exportar-planilha?mes=${S.mes}`); toast("Gerando a planilha do mês… pode levar alguns segundos"); };
    if (!existe("#btnExportar")) return;
    $("#btnExportar").onclick = async () => {
      const btn = $("#btnExportar");
      btn.disabled = true;
      try {
        const r = await fetch(URL_(`/api/exportar?mes=${S.mes}`), { credentials: "same-origin" });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).erro || `Erro ${r.status}`);
        const resumo = JSON.parse(decodeURIComponent(r.headers.get("X-Resumo") || "%7B%7D"));
        const nome = (r.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)/)?.[1] || `Importacao VivoGo ${S.mes}.csv`;
        const n = resumo.incluidas?.length || 0, man = resumo.manuais || [];
        const nc = (resumo.incluidas || []).filter(x => x.contestada).length;
        if (n) {
          const url = URL.createObjectURL(await r.blob());
          const a = Object.assign(document.createElement("a"), { href: url, download: nome });
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 5000);
        }
        toast(n ? `${n} venda${n > 1 ? "s" : ""} alterada${n > 1 ? "s" : ""} no arquivo de importação${nc ? ` (${nc} voltando por contestação)` : ""}` : "Nenhuma alteração importável neste mês", n ? "ok" : "aviso");
        if (man.length) toast(`${man.length} correção(ões) não importáveis — ajustar direto no VivoGo: ` +
          man.map(m => `#${m.id_venda} (${titulo(m.correcao)}${m.valor ? `: ${m.valor}` : ""})`).join(", "), "aviso");
      } catch (e) {
        toast(e.message, "erro");
      } finally {
        btn.disabled = false;
      }
    };
  }

  // ------------------------------------------------------------ cruzamento SVA
  const SV = { arquivo: null, previa: null, importacao: null, polling: null };

  function ligarSva() {
    const modal = $("#modalSva"), inp = $("#svaArquivo"), lbl = $("#svaArquivoLbl");
    const escolher = f => {
      SV.arquivo = f || null;
      $("#svaArquivoNome").textContent = SV.arquivo ? `✓ ${SV.arquivo.name}` : "Escolher ou arrastar a base (.xlsx)";
      lbl.classList.toggle("ok", !!SV.arquivo);
      $("#btnSvaPrevia").disabled = !SV.arquivo;
      $("#svaPrevia").hidden = true;
    };
    $("#btnSva").onclick = async () => {
      modal.hidden = false;
      await carregarUltimaSva();
      acompanharZap();
    };
    const fechar = () => { modal.hidden = true; clearInterval(SV.polling); };
    $("#fecharSva").onclick = fechar;
    modal.addEventListener("click", e => { if (e.target === modal) fechar(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !modal.hidden) fechar(); });
    inp.onchange = () => escolher(inp.files[0]);
    ["dragenter", "dragover"].forEach(ev => lbl.addEventListener(ev, e => { e.preventDefault(); lbl.classList.add("arrastando"); }));
    ["dragleave", "drop"].forEach(ev => lbl.addEventListener(ev, e => { e.preventDefault(); lbl.classList.remove("arrastando"); }));
    lbl.addEventListener("drop", e => escolher(e.dataTransfer.files[0]));

    $("#formSva").onsubmit = async e => {
      e.preventDefault();
      if (!SV.arquivo) return;
      const btn = $("#btnSvaPrevia");
      btn.disabled = true;
      btn.textContent = "Cruzando…";
      const fd = new FormData();
      fd.append("base", SV.arquivo);
      try {
        const r = await fetch(URL_("/api/sva/previa"), { method: "POST", body: fd, credentials: "same-origin", headers: { "X-CSRFToken": csrf() } });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.erro || `Erro ${r.status}`);
        SV.previa = j;
        mostrarPreviaSva(j);
      } catch (err) {
        toast(err.message, "erro");
      } finally {
        btn.disabled = false;
        btn.textContent = "Cruzar com as vendas";
      }
    };

    $("#svaPrevia").addEventListener("click", async e => {
      if (e.target.id === "btnSvaCancelar") { $("#svaPrevia").hidden = true; return; }
      if (e.target.id !== "btnSvaAplicar") return;
      e.target.disabled = true;
      e.target.textContent = "Aplicando…";
      try {
        const r = await api("/api/sva/aplicar", { method: "POST", body: JSON.stringify({ token: SV.previa.token }) });
        toast(`Base SVA ${r.anomes} aplicada: ${r.aplicadas} serviço(s) zerado(s), ${r.sugestoes} sugestão(ões)`);
        $("#svaPrevia").hidden = true;
        SV.previa = null;
        escolher(null);
        inp.value = "";
        await carregarUltimaSva();
        await carregarMes(S.mes, S.dia);
      } catch (err) {
        toast(err.message, "erro");
        e.target.disabled = false;
        e.target.textContent = "Aplicar no sistema";
      }
    });

    $("#btnZapTeste").onclick = () => enviarZap(true);
    $("#btnZapLojas").onclick = () => {
      const n = $("#svaZapLoja").options.length - 1;
      if (confirm(`Enviar a mensagem de SVA não ativado para o grupo de WhatsApp de ${n} loja(s)?\nO envio leva cerca de ${n} minuto(s) (1 min entre lojas).`)) enviarZap(false);
    };
    $("#btnZapCancelar").onclick = async () => { await api("/api/sva/whatsapp/cancelar", { method: "POST" }); toast("Cancelando o envio…", "aviso"); };
  }

  async function carregarUltimaSva() {
    let lista = [];
    try { lista = await api("/api/sva/importacoes"); } catch (e) { toast(e.message, "erro"); }
    const u = lista[0];
    SV.importacao = u || null;
    const el = $("#svaUltima");
    el.hidden = !u;
    $("#svaZap").hidden = !u;
    if (!u) return;
    const em = new Date(u.em).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    el.innerHTML = `<div>Última base: <b>${u.anomes.slice(4)}/${u.anomes.slice(0, 4)}</b> · vendas de ${dataBR(u.base_de)} a <b>${dataBR(u.base_ate)}</b><br>
      Importada em ${em} por ${esc(u.usuario || "—")} · ${u.aplicadas} zerado(s), ${u.sugestoes} sugestão(ões)${u.revertidas ? `, ${u.revertidas} desfeito(s)` : ""}</div>
      <a class="btn-sec" href="${URL_(`/api/sva/importacoes/${u.id}/planilha`)}">Planilha do resultado</a>`;
    $("#svaZapTxt").innerHTML = `Envia para cada loja os SVAs Flag 0 da base de <b>${dataBR(u.base_de)} a ${dataBR(u.base_ate)}</b>, agrupados por vendedor — a mesma mensagem da automação.`;
    try {
      const l = await api(`/api/sva/lojas?importacao_id=${u.id}`);
      $("#svaZapLoja").innerHTML = `<option value="">Teste: todas as lojas</option>` + l.lojas.map(x => `<option>${esc(x)}</option>`).join("");
      if (l.sem_grupo.length) $("#svaZapTxt").innerHTML += `<br><span class="txt-aviso">Sem grupo cadastrado (não recebem): ${l.sem_grupo.map(esc).join(", ")}</span>`;
      SV.ligado = !!l.ligado;
      if (!l.ligado) $("#svaZapTxt").innerHTML += `<br><span class="txt-aviso"><b>WhatsApp desligado:</b> o SUPERADMIN liga na Configuração da Auditoria D-1. Nada é enviado enquanto estiver desligado.</span>`;
      else if (!l.grupo_teste) $("#svaZapTxt").innerHTML += `<br><span class="txt-aviso">O teste precisa do ID do grupo de teste (Configuração).</span>`;
      ["#btnZapTeste", "#btnZapLojas"].forEach(b => { $(b).disabled = !l.ligado; });
    } catch (e) {
      $("#svaZapTxt").textContent = e.message;
    }
  }

  function mostrarPreviaSva(j) {
    const ultima = j.ultima && j.ultima.anomes === j.anomes
      ? `<div class="aviso">Substitui a base de ${dataBR(j.ultima.base_de)} a ${dataBR(j.ultima.base_ate)} importada antes para este mês.</div>` : "";
    const lista = (tit, itens) => itens.length ? `<details class="sva-lista"><summary>${tit} (${itens.length})</summary><ul>${itens.map(s =>
      `<li><b>#${esc(s.id_venda)}</b> · ${dataBR(s.data)} · ${esc(s.servico)}<small>${esc(s.motivo || "")}</small></li>`).join("")}</ul></details>` : "";
    const el = $("#svaPrevia");
    el.innerHTML = `
      <div class="sva-tit">Prévia — ${esc(j.arquivo)}</div>
      <div class="sva-periodo">📅 Base ${j.anomes.slice(4)}/${j.anomes.slice(0, 4)} com vendas de ${dataBR(j.base_de)} a ${dataBR(j.base_ate)} — vendas depois de ${dataBR(j.base_ate)} entram na próxima base.</div>
      <div class="sva-nums">
        <div class="zera"><b>${j.aplicar}</b><span>Zerar automático</span></div>
        <div class="sug"><b>${j.sugestoes}</b><span>Sugestões</span></div>
        <div class="ok"><b>${j.ok}</b><span>Serão pagas</span></div>
        <div><b>${j.sem_base}</b><span>SVA fora da base</span></div>
      </div>
      <div class="sva-txt">Cruzamento a partir dos <b>${j.servicos}</b> serviço(s) SVA lançados na VivoGo, procurando cada um na base pela mesma marca. Planos móveis/fixa não são alterados.</div>
      <div class="sva-impacto">
        ${ultima}
        ${j.revertidas ? `<div class="aviso">${j.revertidas} marcação(ões) da base anterior deste mês serão desfeitas (não estão mais como Flag 0).</div>` : ""}
        ${j.manuais_sobrescritas ? `<div class="aviso">${j.manuais_sobrescritas} verificação(ões) feitas na esteira serão substituídas pela automação (ficam guardadas e voltam se a automação sair).</div>` : ""}
        ${j.plano ? `<div>${j.plano} linha(s) da base são SVA embutido em plano (Controle, Pós, Fibra) — ficam só na planilha, a venda do plano não é zerada.</div>` : ""}
        ${j.nao_existe ? `<div>${j.nao_existe} SVA(s) avulso(s) da base sem serviço SVA da mesma marca na VivoGo (ver planilha).</div>` : ""}
        ${j.sem_base ? `<div>${j.sem_base} SVA(s) da VivoGo até ${dataBR(j.base_ate)} não estão na base (atraso ou não reconhecidos pela Vivo) — nada é alterado.</div>` : ""}
        <div>Sugestões aparecem como alerta na esteira para confirmar com um clique.</div>
      </div>
      ${lista("Sugestões para revisar", j.sugestoes_lista)}
      <div class="sva-acoes"><button class="btn-sec" id="btnSvaCancelar">Cancelar</button><button class="btn-pri" id="btnSvaAplicar">Aplicar no sistema</button></div>`;
    el.hidden = false;
  }

  async function enviarZap(teste) {
    if (!SV.importacao) return;
    try {
      await api("/api/sva/whatsapp", { method: "POST", body: JSON.stringify({ importacao_id: SV.importacao.id, teste, loja: teste ? $("#svaZapLoja").value : "" }) });
      toast(teste ? "Enviando teste para o grupo de teste…" : "Disparo iniciado");
      acompanharZap();
    } catch (e) { toast(e.message, "erro"); }
  }

  function acompanharZap() {
    clearInterval(SV.polling);
    const tick = async () => {
      let e;
      try { e = await api("/api/sva/whatsapp"); } catch { return; }
      const prog = $("#svaZapProg");
      prog.hidden = !e.inicio;
      ["#btnZapTeste", "#btnZapLojas"].forEach(b => { $(b).disabled = !!e.rodando || SV.ligado === false; });
      $("#btnZapCancelar").hidden = !e.rodando;
      if (!e.inicio) { clearInterval(SV.polling); return; }
      $("#svaZapBarra").style.width = `${e.total ? Math.round(e.enviadas / e.total * 100) : 0}%`;
      $("#svaZapStatus").textContent = e.rodando
        ? `${e.teste ? "Teste" : "Disparo"}: ${e.enviadas} de ${e.total} loja(s)${e.atual ? ` · ${e.atual}` : ""}`
        : e.erro ? `Erro: ${e.erro}` : `${e.teste ? "Teste" : "Disparo"} concluído: ${e.enviadas} de ${e.total} loja(s)`;
      $("#svaZapLog").innerHTML = (e.log || []).slice().reverse().map(l => `<li>${esc(l)}</li>`).join("");
      if (!e.rodando) clearInterval(SV.polling);
    };
    tick();
    SV.polling = setInterval(tick, 2000);
  }

  // ------------------------------------------------------------ cruzamento Fixa
  const FX = { arquivo: null, previa: null };

  function ligarFixa() {
    const modal = $("#modalFixa"), inp = $("#fixaArquivo"), lbl = $("#fixaArquivoLbl");
    const escolher = f => {
      FX.arquivo = f || null;
      $("#fixaArquivoNome").textContent = FX.arquivo ? `✓ ${FX.arquivo.name}` : "Escolher ou arrastar o Analítico de Vendas (.xlsx)";
      lbl.classList.toggle("ok", !!FX.arquivo);
      $("#btnFixaPrevia").disabled = !FX.arquivo;
      $("#fixaPrevia").hidden = true;
    };
    $("#btnFixa").onclick = async () => { modal.hidden = false; await carregarUltimaFixa(); };
    const fechar = () => { modal.hidden = true; };
    $("#fecharFixa").onclick = fechar;
    modal.addEventListener("click", e => { if (e.target === modal) fechar(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !modal.hidden) fechar(); });
    inp.onchange = () => escolher(inp.files[0]);
    ["dragenter", "dragover"].forEach(ev => lbl.addEventListener(ev, e => { e.preventDefault(); lbl.classList.add("arrastando"); }));
    ["dragleave", "drop"].forEach(ev => lbl.addEventListener(ev, e => { e.preventDefault(); lbl.classList.remove("arrastando"); }));
    lbl.addEventListener("drop", e => escolher(e.dataTransfer.files[0]));

    $("#formFixa").onsubmit = async e => {
      e.preventDefault();
      if (!FX.arquivo) return;
      const btn = $("#btnFixaPrevia");
      btn.disabled = true;
      btn.textContent = "Cruzando…";
      const fd = new FormData();
      fd.append("base", FX.arquivo);
      try {
        const r = await fetch(URL_("/api/fixa/previa"), { method: "POST", body: fd, credentials: "same-origin", headers: { "X-CSRFToken": csrf() } });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.erro || `Erro ${r.status}`);
        FX.previa = j;
        mostrarPreviaFixa(j);
      } catch (err) {
        toast(err.message, "erro");
      } finally {
        btn.disabled = false;
        btn.textContent = "Cruzar com as vendas de fixa";
      }
    };

    $("#fixaPrevia").addEventListener("click", async e => {
      if (e.target.id === "btnFixaCancelar") { $("#fixaPrevia").hidden = true; return; }
      if (e.target.id !== "btnFixaAplicar") return;
      e.target.disabled = true;
      e.target.textContent = "Aplicando…";
      try {
        const r = await api("/api/fixa/aplicar", { method: "POST", body: JSON.stringify({ token: FX.previa.token }) });
        toast(`Base Fixa aplicada: ${r.aplicadas} serviço(s) zerado(s), ${r.sugestoes} sugestão(ões)${r.revertidas ? `, ${r.revertidas} desfeito(s)` : ""}`);
        $("#fixaPrevia").hidden = true;
        FX.previa = null;
        escolher(null);
        inp.value = "";
        await carregarUltimaFixa();
        await carregarMes(S.mes, S.dia);
      } catch (err) {
        toast(err.message, "erro");
        e.target.disabled = false;
        e.target.textContent = "Aplicar no sistema";
      }
    });
  }

  const mesesTxt = m => (m || "").split(",").filter(Boolean).map(x => `${x.slice(4)}/${x.slice(0, 4)}`).join(" e ");

  async function carregarUltimaFixa() {
    let lista = [];
    try { lista = await api("/api/fixa/importacoes"); } catch (e) { toast(e.message, "erro"); }
    const u = lista[0], el = $("#fixaUltima");
    el.hidden = !u;
    if (!u) return;
    el.innerHTML = `<div>Última base: <b>${mesesTxt(u.meses || u.anomes)}</b> · vendas de ${dataBR(u.base_de)} a <b>${dataBR(u.base_ate)}</b><br>
      Importada em ${quando(u.em)} por ${esc(u.usuario || "—")} · ${u.aplicadas} zerado(s), ${u.sugestoes} sugestão(ões)${u.revertidas ? `, ${u.revertidas} desfeito(s)` : ""}</div>
      <a class="btn-sec" href="${URL_(`/api/fixa/importacoes/${u.id}/planilha`)}">Planilha do resultado</a>`;
  }

  function mostrarPreviaFixa(j) {
    const meses = j.meses.map(x => `${x.slice(4)}/${x.slice(0, 4)}`).join(" e ");
    const lista = (tit, itens) => itens.length ? `<details class="sva-lista"><summary>${tit} (${itens.length})</summary><ul>${itens.map(s =>
      `<li><b>#${esc(s.id_venda)}</b> · ${dataBR(s.data)} · ${esc(s.servico)}<small>${esc(s.motivo || "")}</small></li>`).join("")}</ul></details>` : "";
    const ultima = j.ultima && (j.ultima.meses || j.ultima.anomes).split(",").some(m => j.meses.includes(m))
      ? `<div class="aviso">Substitui a base de ${dataBR(j.ultima.base_de)} a ${dataBR(j.ultima.base_ate)} importada antes (${mesesTxt(j.ultima.meses || j.ultima.anomes)}).</div>` : "";
    const el = $("#fixaPrevia");
    el.innerHTML = `
      <div class="sva-tit">Prévia — ${esc(j.arquivo)}</div>
      <div class="sva-periodo">📅 Base ${meses} com ${j.linhas} ORDENS de ${dataBR(j.base_de)} a ${dataBR(j.base_ate)} — vendas depois de ${dataBR(j.base_ate)} entram na próxima base.</div>
      <div class="sva-nums">
        <div class="zera"><b>${j.aplicar_pedidos}</b><span>Fibra cancelada · zerar</span></div>
        <div class="sug"><b>${j.sugestoes}</b><span>Sugestões</span></div>
        <div class="ok"><b>${j.ok}</b><span>Instaladas</span></div>
        <div><b>${j.pendentes}</b><span>Instalação pendente</span></div>
      </div>
      <div class="sva-txt">${j.pedidos} pedido(s) de fixa da VivoGo: <b>${j.localizadas}</b> localizados na Vivo (Alta ${j.alta} · Média ${j.media} · Muito baixa ${j.muito_baixa}),
        <b>${j.nao_localizadas}</b> não localizados, ${j.b2b} B2B. ${j.nao_lancadas} ORDEM(ns) da Vivo sem venda na VivoGo.</div>
      <div class="sva-impacto">
        ${ultima}
        <div>Zera ${j.aplicar_servicos} serviço(s) com <b>Movimento inelegível · Fibra cancelada</b> — só o serviço que a ORDEM indica (FTTH → Alta Banda Larga; TV e fixo do combo não mudam), com vínculo Alta e CPF conferindo.</div>
        ${j.fora ? `<div>${j.fora} pedido(s) só de TV/fixo sem par ficam de fora (o Analítico não acompanha esse serviço).</div>` : ""}
        ${j.revertidas ? `<div class="aviso">${j.revertidas} marcação(ões) da base anterior serão desfeitas (não estão mais canceladas).</div>` : ""}
        ${j.manuais_sobrescritas ? `<div class="aviso">${j.manuais_sobrescritas} verificação(ões) feitas na esteira serão substituídas pela automação (ficam guardadas e voltam se a automação sair).</div>` : ""}
        ${j.contestadas_mantidas ? `<div>${j.contestadas_mantidas} venda(s) que voltaram por contestação ficam como estão.</div>` : ""}
        <div>Sugestões (vínculo Média/Muito baixa cancelado, venda não comissionada, pedido não localizado na Vivo) aparecem como alerta na esteira.</div>
        ${j.end_diverge ? `<div>Endereço: ${j.end_diverge} pedido(s) com endereço diferente da Vivo — só na planilha do resultado.</div>` : ""}
      </div>
      ${lista("Fibra cancelada (zerar)", j.aplicar_lista)}
      ${lista("Sugestões para revisar", j.sugestoes_lista)}
      <div class="sva-acoes"><button class="btn-sec" id="btnFixaCancelar">Cancelar</button><button class="btn-pri" id="btnFixaAplicar">Aplicar no sistema</button></div>`;
    el.hidden = false;
  }

  function mostrarAuditor() {
    $("#auditorNome").textContent = S.auditor || "—";
    $("#auditorIni").textContent = (S.auditor || "?").split(/\s+/).map(p => p[0]).slice(0, 2).join("").toUpperCase();
  }

  // ------------------------------------------------------------ início
  async function iniciar() {
    ligarEventos();
    mostrarAuditor();
    try {
      S.meta = await api("/api/meta");
    } catch (e) {
      toast("Não foi possível conectar ao servidor: " + e.message, "erro");
      return;
    }
    const h = location.hash.slice(1);
    const dia = /^\d{4}-\d{2}-\d{2}$/.test(h) ? h : S.meta.hoje;
    await carregarMes(dia.slice(0, 7), dia);
  }

  iniciar();
})();
