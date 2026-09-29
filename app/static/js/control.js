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
  $("mmVoice").value = cfg.minimax_tts_voice || "presenter_female";
  $("mmTtsSpeed").value = cfg.minimax_tts_speed ?? 0.92;
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
  const dm = cfg.danmaku || {};
  $("dmEnabled").checked = dm.enabled !== false && dm.enabled !== 0 && dm.enabled !== "0" && dm.enabled !== "false";
  $("dmSpeed").value = dm.speed ?? 8;
  $("dmFont").value = dm.font_size ?? 32;
  $("dmOpacity").value = dm.opacity ?? 0.82;
  $("dmLanes").value = dm.lanes ?? 4;
  $("dmPerSecond").value = dm.per_second ?? 6;
  $("dmBandTop").value = dm.band_top ?? 18;
  $("dmBandHeight").value = dm.band_height ?? 18;
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

const BANK_HINTS = {
  word: { one: "一个词语", bulk: "食物|面条\n动物|熊猫" },
  question: { one: "题干|选项A|选项B|选项C|选项D|答案", bulk: "常识|一年有几季？|三|四|五|六|四" },
  idiom: { one: "四个汉字的成语", bulk: "动物|画蛇添足" },
  puzzle: { one: "表情|答案|提示", bulk: "成语|🦊🐯|狐假虎威|狐狸\n歌名|🎤🎈|告白气球|情歌" },
};

function bankKind() {
  return $("bankKind").value || "word";
}

function renderBank(data) {
  const items = (data && data.items) || [];
  const total = data && data.total != null ? data.total : items.length;
  $("bankTitle").textContent = `题库 · 当前显示 ${items.length} / ${total}`;
  $("bankHint").textContent = total > items.length ? "结果较多，用搜索缩小范围。内置词表仍在原文件里，这里的新增写进本机数据库。" : "内置词表仍在原文件里。这里新增、修改和删除会写进本机数据库，下一局生效。";
  $("bankList").innerHTML = items.map((item) => (
    `<span class="tag" data-id="${escapeHtml(item.id)}" data-category="${escapeHtml(item.category)}" data-label="${escapeHtml(item.label)}" data-line="${escapeHtml(item.line)}">${escapeHtml(item.category)} ${escapeHtml(item.label)}<button data-edit="${escapeHtml(item.id)}" title="修改">改</button><button data-del="${escapeHtml(item.id)}" title="删除">×</button></span>`
  )).join("");
  const pending = (data && data.suggestions) || [];
  $("bankPending").innerHTML = pending.length ? pending.map((item) => (
    `<span class="tag">${escapeHtml(item.nickname)}：${escapeHtml(item.text)}<button data-approve="${item.id}">通过</button><button data-reject="${item.id}">拒绝</button></span>`
  )).join("") : `<span class="hint">暂无。观众可发「出题 内容」。</span>`;
}

function renderPreview(drafts) {
  const rows = drafts || [];
  if (!rows.length) {
    $("bankPreview").innerHTML = "";
    return;
  }
  $("bankPreview").innerHTML = `<div class="tags">${rows.map((item, index) => {
    const label = item.word || item.idiom || item.question || `${item.emojis || ""} ${item.answer || ""}`;
    const mark = item.duplicate ? "（已有）" : "";
    return `<span class="tag"><label><input type="checkbox" data-draft="${index}" ${item.duplicate ? "" : "checked"} style="width:auto"> ${escapeHtml(item.category || "")} ${escapeHtml(label)}${mark}</label></span>`;
  }).join("")}</div><button class="btn sm" id="bankSaveDraft" type="button" style="margin-top:8px">入库所选</button>`;
  $("bankPreview").dataset.drafts = JSON.stringify(rows);
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
  const paused = !!(state && state.paused);
  const btn = $("pauseToggle");
  if (btn) {
    btn.dataset.paused = paused ? "1" : "0";
    btn.textContent = paused ? "继续" : "暂停";
    btn.classList.toggle("green", paused);
    btn.classList.toggle("red", !paused);
  }
  $("hostStatus").textContent = [paused ? "暂停中" : "", host.status_text || "等待开启回合…", extra.join(" · ")].filter(Boolean).join("\n");
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

async function refreshBank() {
  const kind = bankKind();
  const hint = BANK_HINTS[kind] || BANK_HINTS.word;
  $("bankOne").placeholder = hint.one;
  $("bankBulk").placeholder = hint.bulk;
  const q = $("bankSearch").value || "";
  const category = $("bankCategory").value || "";
  const data = await api(`/api/bank?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}`);
  renderBank(data);
}

async function refreshAll() {
  const [cfg, state] = await Promise.all([
    api("/api/config"),
    api("/api/state?role=control"),
  ]);
  fillConfig(cfg);
  renderState(state);
  await refreshBank();
}

$("openOverlay").onclick = () => window.open("/overlay", "overlay", "width=420,height=748");
$("pauseToggle").onclick = async () => {
  const wantPause = $("pauseToggle").dataset.paused !== "1";
  try {
    const data = await api("/api/pause", { paused: wantPause });
    if (data.state) renderState(data.state);
    toast(wantPause ? "已暂停" : "已继续");
  } catch (e) { toast(e.message); }
};
function danmakuPayload() {
  return {
    enabled: $("dmEnabled").checked,
    speed: Number($("dmSpeed").value || 8),
    font_size: Number($("dmFont").value || 32),
    opacity: Number($("dmOpacity").value || 0.82),
    lanes: Number($("dmLanes").value || 4),
    per_second: Number($("dmPerSecond").value || 6),
    band_top: Number($("dmBandTop").value || 18),
    band_height: Number($("dmBandHeight").value || 18),
  };
}
$("saveDanmaku").onclick = async () => {
  try {
    await api("/api/config", { payload: { danmaku: danmakuPayload() } });
    toast("弹幕样式已保存");
  } catch (e) { toast(e.message); }
};
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
      minimax_tts_speed: Number($("mmTtsSpeed").value || 0.92),
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
      danmaku: danmakuPayload(),
      chat_model: $("chatModel").value,
    },
  });
  toast("参数已保存");
};
$("bankKind").onchange = () => refreshBank().catch((e) => toast(e.message));
$("bankSearch").addEventListener("input", () => refreshBank().catch(() => {}));
$("bankAddOne").onclick = async () => {
  const text = $("bankOne").value.trim();
  if (!text) return;
  try {
    const data = await api("/api/bank/items", { kind: bankKind(), text, category: $("bankCategory").value });
    $("bankOne").value = "";
    toast(data.added.length ? `已添加 ${data.added.length} 条` : (data.rejected[0] || data.skipped[0] || "没有新内容"));
    await refreshBank();
  } catch (e) { toast(e.message); }
};
$("bankAddBulk").onclick = async () => {
  try {
    const data = await api("/api/bank/items", { kind: bankKind(), text: $("bankBulk").value, category: $("bankCategory").value });
    toast(`新增 ${data.added.length}，跳过 ${data.skipped.length}，拒绝 ${data.rejected.length}`);
    if (data.added.length) $("bankBulk").value = "";
    await refreshBank();
  } catch (e) { toast(e.message); }
};
$("bankGenerate").onclick = async () => {
  try {
    const data = await api("/api/bank/generate", {
      kind: bankKind(),
      category: $("bankCategory").value,
      theme: $("bankTheme").value,
      count: Number($("bankCount").value || 8),
      auto_add: $("bankAuto").checked,
    });
    if ($("bankAuto").checked) {
      toast(`已入库 ${data.added.length} 条`);
      renderPreview([]);
      await refreshBank();
    } else {
      renderPreview(data.drafts || []);
      toast(`生成 ${((data.drafts) || []).length} 条，确认后入库`);
    }
  } catch (e) { toast(e.message); }
};
$("bankPreview").onclick = async (ev) => {
  const btn = ev.target.closest("#bankSaveDraft");
  if (!btn) return;
  const drafts = JSON.parse($("bankPreview").dataset.drafts || "[]");
  const picked = [...$("bankPreview").querySelectorAll("input[data-draft]:checked")].map((box) => drafts[Number(box.dataset.draft)]).filter(Boolean);
  try {
    const data = await api("/api/bank/save", { kind: bankKind(), items: picked });
    toast(`已入库 ${data.added.length} 条`);
    renderPreview([]);
    await refreshBank();
  } catch (e) { toast(e.message); }
};
function downloadBank(fmt) {
  window.open(`/api/bank/export?kind=${encodeURIComponent(bankKind())}&fmt=${fmt}`, "_blank");
}
$("bankExportTxt").onclick = () => downloadBank("txt");
$("bankExportCsv").onclick = () => downloadBank("csv");
$("bankImport").onchange = async () => {
  const file = $("bankImport").files && $("bankImport").files[0];
  if (!file) return;
  const text = await file.text();
  try {
    const data = await api("/api/bank/import", { kind: bankKind(), text, category: $("bankCategory").value });
    toast(`导入新增 ${data.added.length} 条`);
    await refreshBank();
  } catch (e) { toast(e.message); }
  $("bankImport").value = "";
};
$("bankList").onclick = async (ev) => {
  const del = ev.target.closest("button[data-del]");
  const edit = ev.target.closest("button[data-edit]");
  if (del) {
    await api("/api/bank/delete", { item_id: del.dataset.del });
    await refreshBank();
    return;
  }
  if (!edit) return;
  const tag = edit.closest(".tag");
  const text = prompt("修改这一条（分类|内容）", tag.dataset.line || "");
  if (!text) return;
  try {
    await api("/api/bank/update", { item_id: edit.dataset.edit, text });
    await refreshBank();
  } catch (e) { toast(e.message); }
};
$("bankPending").onclick = async (ev) => {
  const approve = ev.target.closest("button[data-approve]");
  const reject = ev.target.closest("button[data-reject]");
  const button = approve || reject;
  if (!button) return;
  try {
    await api("/api/bank/suggestions/review", {
      suggestion_id: Number((approve || reject).dataset.approve || reject.dataset.reject),
      action: approve ? "approve" : "reject",
    });
    await refreshBank();
  } catch (e) { toast(e.message); }
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
