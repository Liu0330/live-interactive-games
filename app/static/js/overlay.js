const $ = (id) => document.getElementById(id);
let lastAnnounce = 0;
let lastAudio = "";
let lastState = null;
let boardCursor = 0;
let seenEffect = 0;
let shownWelcome = 0;
let fxTimer = 0;
let welcomeTimer = 0;
const flySeen = new Set();
const flyOrder = [];
const flyFree = [];
let flyQueue = [];
let flyWindow = 0;
let flySent = 0;
let flyPump = 0;
const BOARD_ORDER = ["day", "week", "all"];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

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

function currentBoard(state) {
  const boards = (state && state.boards) || {};
  const key = BOARD_ORDER[boardCursor % BOARD_ORDER.length];
  return boards[key] || { label: "总榜", rows: (state && state.leaderboard) || [] };
}

function renderBoard(list) {
  if (!list || !list.length) {
    return `<div class="empty">暂无积分</div>`;
  }
  return (list || []).map((s) => {
    const streak = Number(s.streak) >= 2 ? `${s.streak}连击 · ` : "";
    return `
    <div class="score">
      <div class="medal">${medal(s.place)}</div>
      ${avatar(s.nickname)}
      <div class="name">
        <div class="nick">${escapeHtml(s.nickname)}</div>
        <div class="badge">${streak}${escapeHtml(s.rank_name || "")}</div>
      </div>
      <div class="pts">${s.points}</div>
    </div>`;
  }).join("");
}

function renderMiniBoard(state) {
  const board = currentBoard(state);
  const rows = (board.rows || []).slice(0, 4);
  if (!rows.length) {
    return `<div class="mini-board"><h3>${escapeHtml(board.label || "积分榜")}</h3><div class="empty">暂无积分</div></div>`;
  }
  const body = rows.map((s) => {
    const streak = Number(s.streak) >= 2 ? ` ${s.streak}连` : "";
    return `<div class="mini-row"><span>${medal(s.place)}</span><span class="nick">${escapeHtml(s.nickname)}</span><span>${escapeHtml(s.rank_name || "")}${streak}</span><span class="pts">${s.points}</span></div>`;
  }).join("");
  return `<div class="mini-board"><h3>${escapeHtml(board.label || "积分榜")}</h3>${body}</div>`;
}

function renderSemantic(state) {
  const board = currentBoard(state);
  $("title").textContent = state.title || "挑战最强大脑";
  $("rightStat").textContent = `最高 ${(state.max_score || 0).toFixed(1)}%`;
  $("meta").textContent = [state.category, state.answer_len ? `答案 ${state.answer_len}` : ""].filter(Boolean).join(" · ");
  $("hints").innerHTML = (state.hints && state.hints.length)
    ? `与 ${state.hints.map((h) => `<b>${escapeHtml(h)}</b>`).join("、")} 相关`
    : "等待提示…";
  $("body").innerHTML = `
    <div class="panel">
      <h3>相似度排名</h3>
      <div class="list">${renderGuesses(state.guesses)}</div>
    </div>
    <div class="panel">
      <h3>${escapeHtml(board.label || "积分榜")}</h3>
      <div class="list">${renderBoard(board.rows)}</div>
    </div>`;
}

function renderQuiz(state) {
  $("title").textContent = "弹幕答题";
  $("rightStat").textContent = state.winner ? `抢答 ${state.winner}` : "抢答中";
  $("meta").textContent = "发送 A/B/C/D 或完整答案";
  $("hints").textContent = state.reveal ? `正确答案：${state.reveal}` : "";
  const eliminated = new Set(state.eliminated || []);
  const opts = (state.options || []).map((o, i) => (
    `<div class="opt${eliminated.has(i) ? " gone" : ""}">${"ABCD"[i]}. ${escapeHtml(o)}</div>`
  )).join("");
  const attempts = (state.attempts || []).slice(-8).map((a) => (
    `<div class="chip">${escapeHtml(a.nickname)}：${escapeHtml(a.text)}${a.correct ? " ✓" : ""}</div>`
  )).join("");
  const clue = state.clue ? `<div class="clue">${escapeHtml(state.clue)}</div>` : "";
  $("body").innerHTML = `
    <div class="panel" style="grid-column:1/-1">
      <div class="center-card">
        <div class="q">${escapeHtml(state.question || "等待出题")}</div>
        <div class="opts">${opts}</div>
        ${clue}
        <div class="chips">${attempts}</div>
        ${renderMiniBoard(state)}
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
        ${renderMiniBoard(state)}
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
        ${renderMiniBoard(state)}
      </div>
    </div>`;
}

function renderIdiom(state) {
  $("title").textContent = "成语接龙";
  $("rightStat").textContent = state.allow_pinyin ? "同音可接" : "同字相接";
  const need = state.need ? `接「${state.need}」` : "等待开头";
  $("meta").textContent = state.allow_pinyin ? `${need}，同音也可以` : need;
  const masks = (state.hints || []).map((item) => escapeHtml(item)).join("  ");
  $("hints").textContent = masks ? `可接 ${masks}` : "发四字成语接龙";
  const chain = (state.chain || []).map((item) => escapeHtml(item)).join(" → ");
  const links = (state.links || []).slice(-6).map((item) => (
    `<div class="chip">${escapeHtml(item.nickname)} ${escapeHtml(item.idiom)}</div>`
  )).join("");
  $("body").innerHTML = `
    <div class="panel" style="grid-column:1/-1">
      <div class="center-card">
        <div class="chain-head">${escapeHtml(state.head || "—")}</div>
        <div class="chain-need">${escapeHtml(need)}</div>
        <div class="chain-list">${chain}</div>
        <div class="chips">${links}</div>
        ${renderMiniBoard(state)}
      </div>
    </div>`;
}

function renderEmoji(state) {
  const label = state.category_label || "成语";
  $("title").textContent = `看图猜${label}`;
  $("rightStat").textContent = state.winner ? `${state.winner} 猜中` : "看表情猜";
  $("meta").textContent = `猜一个${label}`;
  const hints = (state.hints || []).map((item) => escapeHtml(item)).join(" · ");
  $("hints").textContent = state.reveal ? `答案：${state.reveal}` : (hints || "发弹幕猜答案");
  const attempts = (state.attempts || []).slice(-8).map((item) => (
    `<div class="chip">${escapeHtml(item.nickname)}：${escapeHtml(item.text)}${item.correct ? " ✓" : ""}</div>`
  )).join("");
  $("body").innerHTML = `
    <div class="panel" style="grid-column:1/-1">
      <div class="center-card">
        <div class="emoji-row">${escapeHtml(state.emojis || "🎁")}</div>
        <div class="chips">${attempts || "观众发弹幕作答"}</div>
        ${renderMiniBoard(state)}
      </div>
    </div>`;
}

function rememberFly(id) {
  if (flySeen.has(id)) return false;
  flySeen.add(id);
  flyOrder.push(id);
  return true;
}

function trimFly(feed) {
  if (flyOrder.length <= 300) return;
  const live = new Set((feed || []).map((item) => String(item.id)));
  let guard = flyOrder.length;
  while (flyOrder.length > 200 && guard > 0) {
    guard -= 1;
    const old = flyOrder.shift();
    if (live.has(old)) flyOrder.push(old);
    else flySeen.delete(old);
  }
}

function findLane(count, nowMs) {
  for (let i = 0; i < count; i += 1) {
    if (!flyFree[i] || flyFree[i] <= nowMs) return i;
  }
  return -1;
}

function spawnFly(layer, item, lane, lanePx, speedSec, fontPx) {
  const row = document.createElement("div");
  const kind = item.style === "win" ? "win" : (item.kind || "chat");
  row.className = `fly-item ${kind}`;
  row.dataset.id = String(item.id);
  const nick = document.createElement("span");
  nick.className = "nick";
  nick.textContent = item.nickname || "观众";
  row.appendChild(nick);
  row.appendChild(document.createTextNode(item.text || ""));
  const size = Math.max(16, Math.min(fontPx, lanePx * 0.86));
  row.style.fontSize = `${size}px`;
  row.style.top = `${lane * lanePx}px`;
  row.style.animationDuration = `${speedSec}s`;
  layer.appendChild(row);
  const width = row.offsetWidth || Math.ceil(String(`${item.nickname || ""}${item.text || ""}`).length * size);
  const travel = 1080 + width + 20;
  flyFree[lane] = performance.now() + speedSec * 1000 * (width / travel);
  row.addEventListener("animationend", () => row.remove());
}

function scheduleFly() {
  if (flyPump || !flyQueue.length) return;
  flyPump = setTimeout(() => {
    flyPump = 0;
    if (lastState) renderFly(lastState);
  }, 200);
}

function renderFly(state) {
  const layer = $("fly");
  if (!layer) return;
  const cfg = state.danmaku || {};
  const enabled = cfg.enabled !== false && cfg.enabled !== 0 && cfg.enabled !== "0" && cfg.enabled !== "false" && cfg.enabled !== "False";
  const bandTop = Number(cfg.band_top ?? 18);
  const bandHeight = Number(cfg.band_height ?? 18);
  layer.style.top = `${bandTop}%`;
  layer.style.height = `${bandHeight}%`;
  layer.style.opacity = String(cfg.opacity ?? 0.82);
  if (!enabled) {
    layer.innerHTML = "";
    flyQueue = [];
    return;
  }
  const lanes = Math.max(2, Math.min(12, Number(cfg.lanes) || 4));
  const speed = Math.max(4, Math.min(20, Number(cfg.speed) || 8));
  const font = Math.max(20, Math.min(72, Number(cfg.font_size) || 32));
  const perSecond = Math.max(1, Math.min(20, Number(cfg.per_second) || 6));
  const bandPx = 1920 * (bandHeight / 100);
  const lanePx = bandPx / lanes;
  const nowMs = performance.now();
  if (nowMs - flyWindow >= 1000) {
    flyWindow = nowMs;
    flySent = 0;
  }
  trimFly(state.feed);
  for (const item of state.feed || []) {
    if (!item || item.id == null) continue;
    if (!rememberFly(String(item.id))) continue;
    if (flyQueue.length >= perSecond) continue;
    flyQueue.push(item);
  }
  while (flyQueue.length && flySent < perSecond) {
    const lane = findLane(lanes, performance.now());
    if (lane < 0) break;
    const item = flyQueue.shift();
    spawnFly(layer, item, lane, lanePx, speed, font);
    flySent += 1;
  }
  scheduleFly();
}

function renderFeed(state) {
  const box = $("danmaku");
  if (!box) return;
  const now = Number(state.now) || Date.now() / 1000;
  const items = (state.feed || []).filter((item) => now - Number(item.ts || 0) < 9).slice(-4);
  const ids = new Set(items.map((item) => item.id));
  for (const node of [...box.children]) {
    if (!ids.has(node.dataset.id)) node.remove();
  }
  for (const item of items) {
    if (box.querySelector(`[data-id="${item.id}"]`)) continue;
    const row = document.createElement("div");
    row.className = `line ${item.kind || "chat"}`;
    row.dataset.id = item.id;
    row.innerHTML = `<span class="nick">${escapeHtml(item.nickname || "观众")}</span>${escapeHtml(item.text || "")}`;
    box.appendChild(row);
  }
}

function renderLike(state) {
  const bar = state.like_bar || {};
  const target = bar.target || 100;
  $("likeCount").textContent = `${bar.count || 0} / ${target}`;
  $("likeTitle").textContent = bar.reward_label || "点赞进度";
  $("likeFill").style.width = `${Math.max(0, Math.min(100, Number(bar.percent) || 0))}%`;
  const bonus = state.bonus || {};
  $("bonus").textContent = bonus.active ? `${bonus.label} · 剩余 ${bonus.remaining} 秒` : "";
}

function renderWelcome(state) {
  const welcome = state.welcome || {};
  if (!welcome.seq || welcome.seq === shownWelcome) return;
  shownWelcome = welcome.seq;
  $("welcome").textContent = welcome.text || "";
  clearTimeout(welcomeTimer);
  welcomeTimer = setTimeout(() => {
    if ($("welcome")) $("welcome").textContent = "";
  }, 4000);
}

function playEffects(effects) {
  const fresh = (effects || []).filter((item) => item && item.seq > seenEffect);
  if (!fresh.length) return;
  fresh.forEach((item) => {
    seenEffect = Math.max(seenEffect, item.seq);
  });
  const fx = fresh[fresh.length - 1];
  const el = $("fx");
  if (!el) return;
  const big = fx.tier === "big" || fx.kind === "level" ? " big" : "";
  const kicker = fx.kind === "like" ? "点赞达成" : fx.kind === "level" ? "段位提升" : "感谢送礼";
  el.hidden = false;
  el.innerHTML = `<div class="fx-card${big} ${fx.kind || ""}">
      <div class="fx-kicker">${kicker}</div>
      <div class="fx-name">${escapeHtml(fx.headline || fx.nickname || "")}</div>
      <div class="fx-detail">${escapeHtml(fx.detail || "")}</div>
    </div>`;
  clearTimeout(fxTimer);
  fxTimer = setTimeout(() => {
    el.hidden = true;
    el.innerHTML = "";
  }, 3400);
}

function render(state) {
  lastState = state;
  const flag = $("pauseFlag");
  if (flag) flag.hidden = !state.paused;
  $("roundText").textContent = `第${state.round || 0}局`;
  $("timer").textContent = fmtTime(state.countdown);
  $("announce").textContent = state.announcement || "";
  renderLike(state);
  renderWelcome(state);
  playEffects(state.effects);
  renderGifts(state.gift_rules);
  const game = state.game;
  if (game === "quiz") renderQuiz(state);
  else if (game === "bomb") renderBomb(state);
  else if (game === "lottery") renderLottery(state);
  else if (game === "idiom") renderIdiom(state);
  else if (game === "emoji") renderEmoji(state);
  else renderSemantic(state);
  if (state.reveal && (game === "semantic")) {
    $("meta").textContent += state.status === "reveal" ? ` · 揭晓 ${state.reveal}` : "";
  }
  if (state.status === "reveal" && state.auto_continue) {
    const wait = `${state.intermission || 0} 秒后自动下一局`;
    $("meta").textContent = [$("meta").textContent, wait].filter(Boolean).join(" · ");
  }
  renderFeed(state);
  renderFly(state);
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
setInterval(() => {
  boardCursor = (boardCursor + 1) % BOARD_ORDER.length;
  if (lastState) render(lastState);
}, 8000);
fetch("/api/state").then((r) => r.json()).then(render);
