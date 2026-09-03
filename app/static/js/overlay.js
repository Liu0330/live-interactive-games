const $ = (id) => document.getElementById(id);
let lastAnnounce = 0;
let lastAudio = "";

function colorOf(name) {
  let h = 0;
  for (const ch of name || "") h = (h * 33 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 80% 62%)`;
}

function avatar(name) {
  const ch = (name || "观").slice(0, 1);
  return `<div class="avatar" style="background:${colorOf(name)}">${ch}</div>`;
}

function fmtTime(sec) {
  sec = Math.max(0, Number(sec) || 0);
  const m = String(Math.floor(sec / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function medal(place) {
  return ["", "🥇", "🥈", "🥉"][place] || String(place);
}

function speak(tts) {
  if (!tts || !tts.text) return;
  if (tts.audio_url && tts.audio_url !== lastAudio) {
    lastAudio = tts.audio_url;
    const audio = new Audio(tts.audio_url);
    audio.play().catch(() => browserSpeak(tts.text));
    return;
  }
  if (tts.use_browser) browserSpeak(tts.text);
}

function browserSpeak(text) {
  if (!window.speechSynthesis) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "zh-CN";
  u.rate = 1.05;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

function renderGifts(rules) {
  $("gifts").innerHTML = (rules || []).map((g) => (
    `<div>${g.name} → ${g.label || g.action}</div>`
  )).join("");
}

function renderGuesses(list) {
  if (!list || !list.length) {
    return `<div class="empty">观众发弹幕开始猜词</div>`;
  }
  return (list || []).map((g) => `
    <div class="guess">
      <div class="barfill" style="width:${Math.min(100, g.score)}%"></div>
      ${avatar(g.nickname)}
      <div class="name">
        <div class="nick">${g.nickname}</div>
        <div class="word">${g.word}</div>
      </div>
      <div class="pct">${Number(g.score).toFixed(1)}%</div>
    </div>`).join("");
}

function renderBoard(list) {
  if (!list || !list.length) {
    return `<div class="empty">暂无积分</div>`;
  }
  return (list || []).map((s) => `
    <div class="score">
      <div class="medal">${medal(s.place)}</div>
      ${avatar(s.nickname)}
      <div class="name">
        <div class="nick">${s.nickname}</div>
        <div class="badge">${s.rank_name}</div>
      </div>
      <div class="pts">${s.points}</div>
    </div>`).join("");
}

function renderSemantic(state) {
  $("title").textContent = state.title || "挑战最强大脑";
  $("rightStat").textContent = `最高 ${(state.max_score || 0).toFixed(1)}%`;
  $("meta").textContent = [state.category, state.answer_len ? `答案 ${state.answer_len}` : ""].filter(Boolean).join(" · ");
  $("hints").innerHTML = (state.hints && state.hints.length)
    ? `与 ${state.hints.map((h) => `<b>${h}</b>`).join("、")} 相关`
    : "等待提示…";
  $("body").innerHTML = `
    <div class="panel">
      <h3>相似度排名</h3>
      <div class="list">${renderGuesses(state.guesses)}</div>
    </div>
    <div class="panel">
      <h3>积分榜</h3>
      <div class="list">${renderBoard(state.leaderboard)}</div>
    </div>`;
  if (state.reveal) $("hints").insertAdjacentHTML("afterend", "");
}

function renderQuiz(state) {
  $("title").textContent = "弹幕答题";
  $("rightStat").textContent = state.winner ? `抢答 ${state.winner}` : "抢答中";
  $("meta").textContent = "发送 A/B/C/D 或完整答案";
  $("hints").textContent = state.reveal ? `正确答案：${state.reveal}` : "";
  const opts = (state.options || []).map((o, i) => `<div class="opt">${"ABCD"[i]}. ${o}</div>`).join("");
  const attempts = (state.attempts || []).slice(-8).map((a) => (
    `<div class="chip">${a.nickname}：${a.text}${a.correct ? " ✓" : ""}</div>`
  )).join("");
  $("body").innerHTML = `
    <div class="panel" style="grid-column:1/-1">
      <div class="center-card">
        <div class="q">${state.question || "等待出题"}</div>
        <div class="opts">${opts}</div>
        <div class="chips">${attempts}</div>
      </div>
    </div>`;
}

function renderBomb(state) {
  $("title").textContent = "数字炸弹";
  $("rightStat").textContent = state.winner ? `${state.winner}` : "别踩雷";
  $("meta").textContent = "弹幕发送整数";
  $("hints").textContent = state.reveal ? `炸弹是 ${state.reveal}` : "";
  const rows = (state.guesses || []).slice(-10).map((g) => {
    const tip = { low: "太小", high: "太大", hit: "炸了", out: "超范围" }[g.hint] || "";
    return `<div class="chip">${g.nickname} ${g.guess} ${tip}</div>`;
  }).join("");
  $("body").innerHTML = `
    <div class="panel" style="grid-column:1/-1">
      <div class="center-card">
        <div>当前范围</div>
        <div class="range">${state.low} — ${state.high}</div>
        <div class="chips">${rows}</div>
      </div>
    </div>`;
}

function renderLottery(state) {
  $("title").textContent = "弹幕抽奖";
  $("rightStat").textContent = `${state.count || 0} 人`;
  $("meta").textContent = `发送「${state.keyword || "抽奖"}」参与`;
  $("hints").textContent = "";
  const chips = (state.participants || []).map((p) => `<div class="chip">${p.nickname}</div>`).join("");
  const win = state.winner ? `<div class="winner-pop">🎉 ${state.winner.nickname}</div>` : "";
  $("body").innerHTML = `
    <div class="panel" style="grid-column:1/-1">
      <div class="center-card">
        ${win}
        <div class="chips">${chips || "等待参与…"}</div>
      </div>
    </div>`;
}

function render(state) {
  $("roundText").textContent = `第${state.round || 0}局`;
  $("timer").textContent = fmtTime(state.countdown);
  $("announce").textContent = state.announcement || "";
  renderGifts(state.gift_rules);
  const game = state.game;
  if (game === "quiz") renderQuiz(state);
  else if (game === "bomb") renderBomb(state);
  else if (game === "lottery") renderLottery(state);
  else renderSemantic(state);
  if (state.reveal && (game === "semantic")) {
    $("meta").textContent += state.status === "reveal" ? ` · 揭晓 ${state.reveal}` : "";
  }
  if (state.announce_seq && state.announce_seq !== lastAnnounce) {
    lastAnnounce = state.announce_seq;
    if (state.tts) speak(state.tts);
    else if (state.announcement) speak({ text: state.announcement, use_browser: true });
  }
}

function fit() {
  const stage = $("stage");
  const sx = window.innerWidth / 1080;
  const sy = window.innerHeight / 1920;
  const s = Math.min(sx, sy);
  stage.style.transform = `scale(${s})`;
  document.body.style.height = `${1920 * s}px`;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    render(data);
  };
  ws.onclose = () => setTimeout(connect, 1000);
}

window.addEventListener("resize", fit);
fit();
connect();
fetch("/api/state").then((r) => r.json()).then(render);
