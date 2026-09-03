const $ = (id) => document.getElementById(id);

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

async function api(path, body) {
  const opt = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || res.statusText);
  return data;
}

function fillConfig(cfg) {
  $("roomId").value = cfg.douyin_room_id || "";
  $("chatModel").innerHTML = (cfg.chat_models || []).map((m) => `<option>${m}</option>`).join("");
  $("chatModel").value = cfg.chat_model;
  $("apiKey").placeholder = cfg.siliconflow_api_key_masked || "尚未保存密钥";
  $("perSub").value = cfg.points_per_sublevel || 180;
  $("rankNames").value = (cfg.rank_names || []).join("\n");
  $("gamePicker").value = cfg.active_game || "semantic";
  const s = cfg.semantic || {};
  $("semCountdown").value = s.countdown ?? 180;
  $("semThreshold").value = s.hit_threshold ?? 80;
  $("semHintInterval").value = s.hint_interval ?? 30;
  $("semHints").value = s.hints_per_round ?? 3;
  $("quizCountdown").value = (cfg.quiz || {}).countdown ?? 60;
  $("bombMax").value = (cfg.bomb || {}).max_value ?? 100;
  $("lotKeyword").value = (cfg.lottery || {}).keyword || "抽奖";
  renderIngest(cfg.ingest || {});
  renderScoring(cfg);
}

function renderIngest(st) {
  const el = $("ingestStatus");
  const on = !!st.connected;
  el.innerHTML = `<span class="dot${on ? " on" : ""}"></span>${st.message || "未连接"}`;
}

function renderWords(words) {
  $("wordTitle").textContent = `谜底词库 · 共 ${words.length} 词`;
  $("wordTags").innerHTML = words.map((w) => (
    `<span class="tag">${w}<button data-w="${w}" title="删除">×</button></span>`
  )).join("");
}

function renderScoring(info) {
  const el = $("scoringMode");
  if (!el) return;
  const label = info.scoring_mode_label || "本地拼音+字面";
  const missing = info.has_api_key ? "" : "（未配置 API Key）";
  const detail = info.scoring_mode_detail ? ` · ${info.scoring_mode_detail}` : "";
  el.textContent = `计分方式：${label}${missing}${detail}`;
}

function renderState(state) {
  const host = (state && state.host) || {};
  $("hostStatus").textContent = host.status_text || "等待开启回合…";
  if (state && state.game) $("gamePicker").value = state.game;
  if (state) renderScoring(state);
}

async function refreshAll() {
  const [cfg, words, state] = await Promise.all([
    api("/api/config"),
    api("/api/words"),
    api("/api/state?role=control"),
  ]);
  fillConfig(cfg);
  renderWords(words.words || []);
  renderState(state);
}

$("openOverlay").onclick = () => window.open("/overlay", "overlay", "width=420,height=748");
$("gamePicker").onchange = async () => {
  await api("/api/game/switch", { game: $("gamePicker").value });
  toast("已切换玩法");
};
$("startRound").onclick = async () => {
  await api("/api/round/start", { specified: $("specified").value });
  toast("新回合已开始");
};
$("skipRound").onclick = async () => {
  await api("/api/round/skip");
  toast("已跳过");
};
$("clearBoard").onclick = async () => {
  if (!confirm("确定清空积分榜？")) return;
  await api("/api/leaderboard/clear");
  toast("积分榜已清空");
};
$("sendChat").onclick = async () => {
  try {
    await api("/api/mock/chat", { nickname: $("mockName").value, content: $("mockText").value });
    $("mockText").value = "";
    toast("弹幕已发送");
  } catch (e) { toast(e.message); }
};
$("mockText").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") $("sendChat").click();
});
$("sendGift").onclick = async () => {
  await api("/api/mock/gift", {
    nickname: $("mockName").value,
    gift_name: $("giftName").value,
    count: Number($("giftCount").value || 1),
  });
  toast("已模拟送礼");
};
$("connectRoom").onclick = async () => {
  try {
    const data = await api("/api/douyin/connect", { room_id: $("roomId").value });
    renderIngest(data.ingest);
    toast("正在连接");
  } catch (e) { toast(e.message); }
};
$("disconnectRoom").onclick = async () => {
  const data = await api("/api/douyin/disconnect");
  renderIngest(data.ingest);
};
$("saveKey").onclick = async () => {
  await api("/api/key", { siliconflow_api_key: $("apiKey").value, chat_model: $("chatModel").value });
  $("apiKey").value = "";
  toast("已保存密钥");
};
$("testKey").onclick = async () => {
  try {
    const data = await api("/api/key/test");
    toast("连接成功：" + (data.reply || data.model));
  } catch (e) { toast(e.message); }
};
$("genWords").onclick = async () => {
  try {
    const data = await api("/api/generate", {
      count: Number($("genCount").value || 50),
      theme: $("genTheme").value,
      overwrite: $("overwrite").checked,
      kind: "words",
    });
    toast(`已入库 ${data.added} 词，当前 ${data.count}`);
    renderWords((await api("/api/words")).words);
  } catch (e) { toast(e.message); }
};
$("genQuiz").onclick = async () => {
  try {
    const data = await api("/api/generate", {
      count: 10,
      theme: $("genTheme").value,
      overwrite: false,
      kind: "questions",
    });
    toast(`题库现有 ${data.count} 题`);
  } catch (e) { toast(e.message); }
};
$("saveRanks").onclick = async () => {
  await api("/api/config", {
    payload: {
      points_per_sublevel: Number($("perSub").value || 180),
      rank_names: $("rankNames").value.split(/\n+/).map((s) => s.trim()).filter(Boolean),
    },
  });
  toast("段位已保存");
};
$("saveParams").onclick = async () => {
  await api("/api/config", {
    payload: {
      semantic: {
        countdown: Number($("semCountdown").value),
        hit_threshold: Number($("semThreshold").value),
        hint_interval: Number($("semHintInterval").value),
        hints_per_round: Number($("semHints").value),
      },
      quiz: { countdown: Number($("quizCountdown").value) },
      bomb: { max_value: Number($("bombMax").value) },
      lottery: { keyword: $("lotKeyword").value || "抽奖" },
      chat_model: $("chatModel").value,
    },
  });
  toast("参数已保存");
};
$("addWord").onclick = async () => {
  const word = $("newWord").value.trim();
  if (!word) return;
  const data = await api("/api/words", { words: [word], overwrite: false });
  $("newWord").value = "";
  renderWords(data.words);
};
$("wordTags").onclick = async (ev) => {
  const btn = ev.target.closest("button[data-w]");
  if (!btn) return;
  const data = await api("/api/words/delete", { word: btn.dataset.w });
  renderWords(data.words);
};

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    renderState(data);
  };
  ws.onclose = () => setTimeout(connectWs, 1200);
}
refreshAll().catch((e) => toast(e.message));
connectWs();
setInterval(async () => {
  try { renderIngest(await api("/api/douyin/status")); } catch (_) {}
}, 4000);
