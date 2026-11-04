/* Vídeo ao vivo das câmeras: WebRTC direto do computador da loja para este navegador.
   O portal só apresenta os dois lados. Se a rede não deixar conectar, a tela segue com as imagens. */
(function () {
  'use strict';

  var STUN = 'stun:stun.l.google.com:19302';
  var ESPERA_ICE = 2000, RESPOSTA_A_CADA = 800, RESPOSTA_MAXIMA = 25000;
  var TENTATIVAS = 2, CHECA_IMAGEM_A_CADA = 5000, CHECAGENS_SEM_IMAGEM = 3;

  function CamerasAoVivo(opcoes) {
    this.urls = opcoes;
    this.sessoes = {};
    this.fila = [];
    this.esperando = {};
    this.timerFila = null;
    this.timerRespostas = null;
    var eu = this;
    window.addEventListener('pagehide', function () { eu._avisarFechamento(); });
  }

  CamerasAoVivo.prototype._csrf = function () { return this.urls.csrf; };

  CamerasAoVivo.prototype.ligar = function (canal, video, qualidade, aoMudar) {
    this.desligar(canal);
    var s = {canal: canal, video: video, qualidade: qualidade || 'BAIXA', aoMudar: aoMudar || function () {}, tentativas: 0, viva: true};
    this.sessoes[canal] = s;
    this._conectar(s);
  };

  CamerasAoVivo.prototype.desligar = function (canal) {
    var s = this.sessoes[canal];
    if (!s) return;
    delete this.sessoes[canal];
    this._encerrar(s);
    if (s.id) this._estado([s.id], 'FECHADA');
  };

  CamerasAoVivo.prototype.desligarTudo = function () {
    var ids = [], eu = this;
    Object.keys(this.sessoes).forEach(function (canal) {
      var s = eu.sessoes[canal];
      delete eu.sessoes[canal];
      eu._encerrar(s);
      if (s.id) ids.push(s.id);
    });
    if (ids.length) this._estado(ids, 'FECHADA');
  };

  CamerasAoVivo.prototype._encerrar = function (s) {
    s.viva = false;
    clearInterval(s.vigia);
    clearTimeout(s.retentativa);
    if (s.id) delete this.esperando[s.id];
    if (s.pc) { s.pc.ontrack = s.pc.onconnectionstatechange = null; s.pc.close(); s.pc = null; }
    s.video.onplaying = null;
    s.video.pause();
    s.video.srcObject = null;
    s.video.hidden = true;
  };

  CamerasAoVivo.prototype._conectar = function (s) {
    var eu = this;
    s.aoMudar('conectando');
    var pc = s.pc = new RTCPeerConnection({iceServers: [{urls: STUN}]});
    pc.addTransceiver('video', {direction: 'recvonly'});
    pc.ontrack = function (e) {
      s.video.srcObject = (e.streams && e.streams[0]) || new MediaStream([e.track]);
      s.video.muted = true;
      var tocar = s.video.play();
      if (tocar && tocar.catch) tocar.catch(function () {});
    };
    s.video.onplaying = function () {
      s.video.hidden = false;
      s.aoMudar('vivo', {codec: s.codec, largura: s.video.videoWidth, altura: s.video.videoHeight});
      eu._vigiar(s);
    };
    pc.onconnectionstatechange = function () {
      if (pc.connectionState === 'connected' && s.id) eu._estado([s.id], 'CONECTADA');
      if (pc.connectionState === 'failed') eu._falhar(s, 'A rede não deixou o vídeo chegar até aqui.');
    };
    pc.createOffer().then(function (oferta) {
      return pc.setLocalDescription(oferta);
    }).then(function () {
      return eu._esperarIce(pc);
    }).then(function () {
      if (!s.viva || s.pc !== pc) return;
      eu.fila.push(s);
      if (!eu.timerFila) eu.timerFila = setTimeout(function () { eu._enviarFila(); }, 120);
    }).catch(function () { eu._falhar(s, 'Este navegador não abriu o vídeo.'); });
  };

  CamerasAoVivo.prototype._esperarIce = function (pc) {
    return new Promise(function (pronto) {
      if (pc.iceGatheringState === 'complete') return pronto();
      var fim = setTimeout(pronto, ESPERA_ICE);
      pc.addEventListener('icegatheringstatechange', function () {
        if (pc.iceGatheringState === 'complete') { clearTimeout(fim); pronto(); }
      });
    });
  };

  // Vários vídeos de uma vez viram um pedido só: cada pedido ao portal custa consultas.
  CamerasAoVivo.prototype._enviarFila = function () {
    var eu = this, lote = this.fila.splice(0, 32);
    this.timerFila = this.fila.length ? setTimeout(function () { eu._enviarFila(); }, 120) : null;
    lote = lote.filter(function (s) { return s.viva && s.pc; });
    if (!lote.length) return;
    fetch(this.urls.abrir, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': this._csrf()},
      body: JSON.stringify({sessoes: lote.map(function (s) {
        return {canal: s.canal, qualidade: s.qualidade, sdp: s.pc.localDescription.sdp};
      })})
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.sessoes) throw new Error(d.erro || 'erro');
      lote.forEach(function (s, i) {
        var item = d.sessoes[i] || {};
        if (item.erro) return eu._falhar(s, item.erro, item.atualizar);
        s.id = item.id;
        s.pedidoEm = Date.now();
        eu.esperando[s.id] = s;
      });
      eu._buscarRespostas();
    }).catch(function () {
      lote.forEach(function (s) { eu._falhar(s, 'Sem resposta do portal.'); });
    });
  };

  CamerasAoVivo.prototype._buscarRespostas = function () {
    var eu = this;
    if (this.timerRespostas) return;
    this.timerRespostas = setTimeout(function () {
      eu.timerRespostas = null;
      var ids = Object.keys(eu.esperando);
      if (!ids.length) return;
      fetch(eu.urls.respostas + '?ids=' + ids.join(','), {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (d) {
        var respostas = d.respostas || {};
        ids.forEach(function (id) {
          var s = eu.esperando[id], r = respostas[id];
          if (!s) return;
          if (r) {
            delete eu.esperando[id];
            if (r.erro) return eu._falhar(s, r.erro);
            s.codec = r.codec;
            s.pc.setRemoteDescription({type: 'answer', sdp: r.sdp}).catch(function () { eu._falhar(s, 'Resposta de vídeo inválida.'); });
          } else if (Date.now() - s.pedidoEm > RESPOSTA_MAXIMA) {
            delete eu.esperando[id];
            eu._falhar(s, 'O computador da loja não respondeu.');
          }
        });
      }).catch(function () {}).then(function () { eu._buscarRespostas(); });
    }, RESPOSTA_A_CADA);
  };

  // Conectado não é o mesmo que imagem andando: a câmera pode ter caído do outro lado.
  CamerasAoVivo.prototype._vigiar = function (s) {
    var eu = this, ultimo = -1, paradas = 0;
    clearInterval(s.vigia);
    s.vigia = setInterval(function () {
      if (document.hidden) return;
      if (s.video.currentTime === ultimo) {
        paradas += 1;
        if (paradas >= CHECAGENS_SEM_IMAGEM) eu._falhar(s, 'A imagem parou.');
      } else {
        paradas = 0;
        s.tentativas = 0;
      }
      ultimo = s.video.currentTime;
    }, CHECA_IMAGEM_A_CADA);
  };

  CamerasAoVivo.prototype._falhar = function (s, motivo, semRetentar) {
    if (!s.viva) return;
    var eu = this, id = s.id;
    this._encerrar(s);
    s.id = null;
    if (id) this._estado([id], 'FALHOU');
    if (!semRetentar && s.tentativas < TENTATIVAS) {
      s.tentativas += 1;
      s.viva = true;
      s.aoMudar('conectando');
      s.retentativa = setTimeout(function () {
        if (eu.sessoes[s.canal] === s) eu._conectar(s);
      }, 3000 * s.tentativas);
      return;
    }
    delete this.sessoes[s.canal];
    s.aoMudar('falhou', {motivo: motivo, atualizar: !!semRetentar});
  };

  CamerasAoVivo.prototype._estado = function (ids, estado) {
    var corpo = new FormData();
    corpo.append('ids', ids.join(','));
    corpo.append('estado', estado);
    corpo.append('csrfmiddlewaretoken', this._csrf());
    fetch(this.urls.estado, {method: 'POST', credentials: 'same-origin', body: corpo, keepalive: true}).catch(function () {});
  };

  CamerasAoVivo.prototype._avisarFechamento = function () {
    var ids = [], eu = this;
    Object.keys(this.sessoes).forEach(function (c) { if (eu.sessoes[c].id) ids.push(eu.sessoes[c].id); });
    if (!ids.length || !navigator.sendBeacon) return;
    var corpo = new FormData();
    corpo.append('ids', ids.join(','));
    corpo.append('estado', 'FECHADA');
    corpo.append('csrfmiddlewaretoken', this._csrf());
    navigator.sendBeacon(this.urls.estado, corpo);
  };

  CamerasAoVivo.disponivel = function () { return typeof RTCPeerConnection !== 'undefined'; };
  window.CamerasAoVivo = CamerasAoVivo;
})();
