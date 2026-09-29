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
  $("llmBase").value = cfg.llm_base_url || "";
  $("llmModel").value = cfg.llm_model || "glm-5.2";
  $("llmKey").value = "";
  $("llmKey").placeholder = cfg.llm_api_key_masked || "尚未保存密钥";
  $("llmCanWin").checked = !!cfg.llm_related_can_win;
  $("mmBase").value = cfg.minimax_base_url || "https://api.minimaxi.com";
  $("mmChat").value = cfg.minimax_chat_model || "MiniMax-M3";
  $("mmEmbed").value = cfg.minimax_embed_model || "embo-01";
  $("mmTts").value = cfg.minimax_tts_model || "speech-02-turbo";
  $("mmVoice").value = cfg.minimax_tts_voice || "male-qn-qingse";
  $("mmKey").value = "";
  $("mmKey").placeholder = cfg.minimax_api_key_masked || "尚未保存密钥";
  $("mmCanWin").checked = !!cfg.llm_related_can_win;
  $("mmVoices").innerHTML = (cfg.minimax_voices || []).map(
    (item) => `<option value="${item.id}">${item.label}</option>`
  ).join("");
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
  const idiom = cfg.idiom || {};
  $("idiomSeconds").value = idiom.link_seconds ?? 30;
  $("idiomPinyin").checked = !!idiom.allow_pinyin;
  const emoji = cfg.emoji || {};
  $("emojiCategory").value = emoji.category || "rotate";
  $("emojiCountdown").value = emoji.countdown ?? 70;
  $("emojiPinyin").checked = emoji.allow_pinyin !== false;
  $("autoContinue").checked = cfg.auto_continue !== false;
  $("intermission").value = cfg.intermission_seconds ?? 8;
  $("countGapChat").checked = cfg.count_intermission_chat !== false;
  const tiers = cfg.gift_tiers || [];
  const small = tiers.find((t) => t.id === "small") || tiers[0] || {};
  const big = tiers.find((t) => t.id === "big") || tiers[1] || {};
  $("smallNames").value = (small.names || []).join("\n");
  $("smallMax").value = small.max_value || 9;
  $("smallAction").value = small.action || "hint";
  $("bigNames").value = (big.names || []).join("\n");
  $("bigMin").value = big.min_value || 10;
  $("bigAction").value = big.action || "add_time";
  $("bigSeconds").value = big.seconds || 30;
  const likes = cfg.likes || {};
  $("likeTarget").value = likes.target ?? 100;
  $("likeReward").value = likes.reward || "hint";
  $("bonusSeconds").value = likes.bonus_seconds ?? 45;
  $("bonusMult").value = likes.multiplier ?? 2;
  const streak = cfg.streak || {};
  $("streakPer").value = streak.bonus_per ?? 15;
  $("streakMax").value = streak.max_bonus ?? 60;
  const giftNames = [...(small.names || []), ...(big.names || [])];
  $("giftName").innerHTML = (giftNames.length ? giftNames : ["小心心", "鲜花"]).map(
    (name) => `<option>${name}</option>`
  ).join("");
  renderIngest(cfg.ingest || {});
  renderScoring(cfg);
}

function splitNames(text) {
  return String(text || "").split(/[,，\n]+/).map((s) => s.trim()).filter(Boolean);
}

function tierPayload() {
  return [
    {
      id: "small",
      label: "小礼物",
      names: splitNames($("smallNames").value),
      min_value: 1,
      max_value: Number($("smallMax").value || 9),
      action: $("smallAction").value,
      seconds: Number($("bigSeconds").value || 30),
    },
    {
      id: "big",
      label: "大礼物",
      names: splitNames($("bigNames").value),
      min_value: Number($("bigMin").value || 10),
      max_value: 0,
      action: $("bigAction").value,
      seconds: Number($("bigSeconds").value || 30),
    },
  ];
}

function renderIngest(st) {
  const el = $("ingestStatus");
  const on = !!st.connected;
  el.innerHTML = `<span class="dot${on ? " on" : ""}"></span>${st.message || "未连接"}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function renderEmojiBank(puzzles) {
  const rows = puzzles || [];
  $("emojiTitle").textContent = `看图猜题库 · 共 ${rows.length} 题`;
  $("emojiTags").innerHTML = rows.map((item) => {
    const kind = item.category === "song" ? "歌名" : "成语";
    return `<span class="tag">${escapeHtml(item.emojis)} ${escapeHtml(kind)} ${escapeHtml(item.answer)}<button data-puzzle="${escapeHtml(item.id)}" title="删除">×</button></span>`;
  }).join("");
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
  const missing = (info.has_api_key || info.has_llm || info.has_minimax) ? "" : "（未配置 API Key）";
  const detail = info.scoring_mode_detail ? ` · ${info.scoring_mode_detail}` : "";
  const voice = info.voice_mode_label ? ` · 语音：${info.voice_mode_label}` : "";
  el.textContent = `计分方式：${label}${missing}${detail}${voice}`;
}

function renderState(state) {
  const host = (state && state.host) || {};
  const bar = (state && state.like_bar) || {};
  const bonus = (state && state.bonus) || {};
  const extra = [];
  if (bar.target) extra.push(`点赞 ${bar.count || 0}/${bar.target}`);
  if (bonus.active) extra.push(`${bonus.label} 剩余 ${bonus.remaining} 秒`);
  $("hostStatus").textContent = [host.status_text || "等待开启回合…", extra.join(" · ")].filter(Boolean).join("\n");
  if ($("likeProgress") && bar.target) {
    $("likeProgress").textContent = `点赞进度 ${bar.count || 0} / ${bar.target} · ${bar.reward_label || ""}`;
  }
  if ($("bonusStatus")) {
    $("bonusStatus").textContent = bonus.active
      ? `当前${bonus.label}，剩余 ${bonus.remaining} 秒`
      : "满赞后解锁提示，或开启限时多倍积分。";
  }
  if (state && state.game) $("gamePicker").value = state.game;
  if (state) renderScoring(state);
}

async function refreshAll() {
  const [cfg, words, state, puzzles] = await Promise.all([
    api("/api/config"),
    api("/api/words"),
    api("/api/state?role=control"),
    api("/api/emoji/puzzles"),
  ]);
  fillConfig(cfg);
  renderWords(words.words || []);
  renderEmojiBank(puzzles.puzzles || []);
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
  if (!confirm("确定清空全部玩法的积分、连击和日榜周榜？此操作不可恢复。")) return;
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
async function sendGift(name) {
  await api("/api/mock/gift", {
    nickname: $("mockName").value,
    gift_name: name || $("giftName").value,
    count: Number($("giftCount").value || 1),
  });
  toast("已模拟送礼");
}
$("sendGift").onclick = () => sendGift($("giftName").value);
$("sendSmall").onclick = () => sendGift(splitNames($("smallNames").value)[0] || "小心心");
$("sendBig").onclick = () => sendGift(splitNames($("bigNames").value)[0] || "鲜花");
$("sendMember").onclick = async () => {
  await api("/api/mock/member", { nickname: $("mockName").value || "新观众" });
  toast("已模拟进场");
};
$("likeBurst").onclick = async () => {
  await api("/api/mock/like", { nickname: $("mockName").value, count: 20 });
  toast("已模拟点赞");
};
$("likeFill").onclick = async () => {
  const target = Number($("likeTarget").value || 100);
  await api("/api/mock/like", { nickname: $("mockName").value, count: target });
  toast("已模拟灌满点赞");
};
$("saveGifts").onclick = async () => {
  await api("/api/config", { payload: { gift_tiers: tierPayload() } });
  toast("礼物档位已保存");
  fillConfig(await api("/api/config"));
};
$("saveLikes").onclick = async () => {
  await api("/api/config", {
    payload: {
      likes: {
        target: Number($("likeTarget").value || 100),
        reward: $("likeReward").value,
        bonus_seconds: Number($("bonusSeconds").value || 45),
        multiplier: Number($("bonusMult").value || 2),
      },
    },
  });
  toast("点赞设置已保存");
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
$("saveMinimax").onclick = async () => {
  try {
    const data = await api("/api/minimax", {
      minimax_base_url: $("mmBase").value,
      minimax_api_key: $("mmKey").value,
      minimax_chat_model: $("mmChat").value,
      minimax_embed_model: $("mmEmbed").value,
      minimax_tts_model: $("mmTts").value,
      minimax_tts_voice: $("mmVoice").value,
      llm_related_can_win: $("mmCanWin").checked,
    });
    $("mmKey").value = "";
    fillConfig(data.config);
    toast("MiniMax 已保存");
  } catch (e) { toast(e.message); }
};
$("testMinimax").onclick = async () => {
  try {
    const data = await api("/api/minimax/test");
    toast("MiniMax 对话成功：" + (data.reply || data.model));
  } catch (e) { toast(e.message); }
};
$("saveLlm").onclick = async () => {
  try {
    const data = await api("/api/llm", {
      llm_base_url: $("llmBase").value,
      llm_api_key: $("llmKey").value,
      llm_model: $("llmModel").value,
      llm_related_can_win: $("llmCanWin").checked,
    });
    $("llmKey").value = "";
    fillConfig(data.config);
    toast("对话接口已保存");
  } catch (e) { toast(e.message); }
};
$("testLlm").onclick = async () => {
  try {
    const data = await api("/api/llm/test");
    toast("对话成功：" + (data.reply || data.model));
  } catch (e) { toast(e.message); }
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
      streak: {
        bonus_per: Number($("streakPer").value || 0),
        max_bonus: Number($("streakMax").value || 0),
      },
    },
  });
  toast("段位与连击已保存");
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
      idiom: {
        link_seconds: Number($("idiomSeconds").value || 30),
        allow_pinyin: $("idiomPinyin").checked,
      },
      emoji: {
        category: $("emojiCategory").value || "rotate",
        countdown: Number($("emojiCountdown").value || 70),
        allow_pinyin: $("emojiPinyin").checked,
      },
      auto_continue: $("autoContinue").checked,
      intermission_seconds: Math.max(3, Math.min(60, Number($("intermission").value || 8))),
      count_intermission_chat: $("countGapChat").checked,
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
$("genEmoji").onclick = async () => {
  try {
    const data = await api("/api/emoji/generate", {
      count: Number($("emojiGenCount").value || 4),
      category: $("emojiGenCategory").value,
    });
    toast(`已入库 ${data.added} 题`);
    renderEmojiBank((await api("/api/emoji/puzzles")).puzzles || []);
  } catch (e) { toast(e.message); }
};
$("emojiTags").onclick = async (ev) => {
  const btn = ev.target.closest("button[data-puzzle]");
  if (!btn) return;
  const data = await api("/api/emoji/delete", { puzzle_id: btn.dataset.puzzle });
  renderEmojiBank(data.puzzles || []);
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
