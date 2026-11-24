// AI-yordamchi boshqaruv paneli. Build bosqichi yo'q: oddiy ES modul, tashqi kutubxonalarsiz.

const $ = (sel, el = document) => el.querySelector(sel);
const view = $('#view');
const show = (...kids) => view.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));

// ------------------------------------------------------------------ helpers
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch('/api' + path, init);
  if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`);
  return data;
}

function toast(text, ms = 2600) {
  const t = $('#toast');
  t.textContent = text; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
}

function fail(e) { toast('Xato: ' + (e.message || e), 5000); }

function toggle(checked, onchange) {
  const inp = h('input', { type: 'checkbox', checked: checked || null });
  inp.addEventListener('change', () => onchange(inp.checked, inp));
  return h('label', { class: 'toggle' }, inp, h('span'));
}

function modal(title, body, actions) {
  const bg = h('div', { class: 'modal-bg', onclick: (e) => { if (e.target === bg) bg.remove(); } });
  const close = () => bg.remove();
  bg.append(h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' },
    h('h2', {}, title), body,
    h('div', { class: 'row', style: 'margin-top:16px' }, h('span', { class: 'spacer' }),
      h('button', { class: 'btn ghost', onclick: close }, 'Bekor'), ...actions(close))));
  document.body.append(bg);
  const first = bg.querySelector('input, textarea, select');
  if (first) first.focus();
  return close;
}

function linesToList(text) { return text.split('\n').map((s) => s.trim()).filter(Boolean); }
function wordsToList(text) { return text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean); }
function fmtTime(ts) { return (ts || '').replace('T', ' ').slice(0, 19); }

// ------------------------------------------------------------------ status & confirmations
let STATUS = null;
async function refreshStatus() {
  try {
    STATUS = await api('/status');
    $('#brandName').textContent = 'AI-yordamchi' + (STATUS.assistant_name ? ` · ${STATUS.assistant_name}` : '');
    const pill = $('#statusPill');
    if (!STATUS.running) { pill.className = 'pill warn'; pill.textContent = 'faqat panel'; }
    else if (STATUS.teaching) { pill.className = 'pill warn'; pill.textContent = "o'rgatilmoqda: " + STATUS.teaching; }
    else { pill.className = 'pill ok'; pill.textContent = STATUS.voice ? `ovoz: ${STATUS.voice_state}` : 'ishlayapti'; }
    renderConfirm(STATUS.pending_confirmations || []);
  } catch { /* tarmoq uzildi — keyingi safar */ }
}

function renderConfirm(items) {
  const box = $('#confirmBox');
  if (!items.length) { box.hidden = true; box.replaceChildren(); return; }
  const c = items[0];
  box.hidden = false;
  box.replaceChildren(
    h('div', { class: 'section-title' }, h('strong', {}, 'Tasdiq kerak')),
    h('p', { style: 'white-space:pre-wrap' }, c.text),
    h('div', { class: 'row' },
      h('button', { class: 'btn primary', onclick: () => answer(c.id, true) }, 'Ha, bajar'),
      h('button', { class: 'btn', onclick: () => answer(c.id, false) }, "Yo'q")));
}
async function answer(id, approve) {
  try { await api('/confirmations/' + id, { method: 'POST', body: { approve } }); } catch (e) { fail(e); }
  refreshStatus();
}

// ------------------------------------------------------------------ pages
const pages = {};

pages.home = async () => {
  const [st, tools, procs, skills, facts] = await Promise.all([
    api('/status'), api('/tools'), api('/procedures'), api('/skills'), api('/facts')]);
  const items = procs.items;
  const stat = (num, lbl, href) => h('a', { class: 'card stat', href, style: 'text-decoration:none;color:inherit' },
    h('div', { class: 'num' }, num), h('div', { class: 'lbl' }, lbl));
  const warn = st.missing_settings.length ? h('div', { class: 'card', style: 'border-color:var(--warn)' },
    h('strong', {}, 'Sozlash kerak: '), st.missing_settings.join(', '), ' — ',
    h('a', { href: '#/settings' }, 'Sozlamalarga o\'tish')) : null;
  const notRunning = !st.running ? h('div', { class: 'card' },
    'Yordamchi hozir faqat panel rejimida. Sozlamalarni kiritgach ', h('strong', {}, 'qayta ishga tushiring'), '.',
    h('div', { style: 'margin-top:10px' }, h('button', { class: 'btn primary', onclick: restart }, 'Qayta ishga tushirish'))) : null;
  show(
    h('h1', {}, 'Bosh sahifa'),
    h('p', { class: 'muted' }, `Versiya ${st.version} · ${st.platform} · kalitlar saqlagichi: ${st.secret_backend}`),
    warn, notRunning,
    h('div', { class: 'grid' },
      stat(tools.tools.length, 'Vositalar', '#/tools'),
      stat(skills.skills.length, "O'rgatilgan skill'lar", '#/skills'),
      stat(items.filter((p) => p.kind === 'task').length, 'Retseptlar', '#/recipes'),
      stat(items.filter((p) => p.kind === 'quirk').length, "O'ziga xosliklar", '#/quirks'),
      stat(facts.facts.length, 'Faktlar', '#/facts'),
      stat(Object.keys(st.plugins).length, "Plugin'lar", '#/plugins')),
    h('h2', {}, 'Tezkor amallar'),
    h('div', { class: 'row' },
      h('a', { class: 'btn', href: '#/chat' }, 'Buyruq berish'),
      h('a', { class: 'btn', href: '#/skills' }, "Yangi skill o'rgatish"),
      h('a', { class: 'btn', href: '#/logs' }, 'Jonli log'),
      h('button', { class: 'btn', onclick: restart }, 'Qayta ishga tushirish')));
};

async function restart() {
  if (!confirm('Yordamchi qayta ishga tushirilsinmi? Joriy suhbat to\'xtaydi.')) return;
  try { await api('/restart', { method: 'POST' }); toast('Qayta ishga tushirilmoqda…', 6000); }
  catch (e) { fail(e); }
  setTimeout(() => location.reload(), 7000);
}

// ---------------------------------------------------------------- chat
let chatSocket = null;
// Loglar mahalliy vaqtda yoziladi — panel ham mahalliy vaqtni ishlatsin (toISOString UTC beradi)
function localIso(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
const SOURCE_LABEL = { voice: 'ovoz', panel: 'panel', text: 'konsol' };

function logToChat(r) {
  const map = { user: ['user', r.text], reply: ['bot', r.text], error: ['sys', 'xato: ' + (r.error || '')], cancelled: ['sys', "to'xtatildi"] };
  const m = map[r.kind];
  if (!m || !m[1]) return null;
  return { ts: r.ts, who: m[0], text: String(m[1]), source: r.source || 'voice' };
}

pages.chat = async () => {
  const data = await api('/chat/history?limit=300');
  const items = data.items;
  let busy = false;
  const box = h('div', { class: 'chat' });
  const input = h('textarea', { rows: 2, placeholder: 'Masalan: word dasturini och va yangi hujjat yarat' });
  const send = h('button', { class: 'btn primary' }, 'Yuborish');
  const stop = h('button', { class: 'btn', onclick: () => api('/chat/cancel', { method: 'POST' }).catch(fail) }, "To'xtat");
  const draw = () => {
    const out = [];
    let day = '';
    for (const m of items) {
      const d = (m.ts || '').slice(0, 10);
      if (d && d !== day) { day = d; out.push(h('div', { class: 'msg sys' }, d)); }
      out.push(h('div', { class: 'msg ' + m.who }, m.text,
        m.who !== 'sys' ? h('div', { class: 'msg-meta' }, `${SOURCE_LABEL[m.source] || m.source} · ${(m.ts || '').slice(11, 16)}`) : null));
    }
    if (busy) out.push(h('div', { class: 'msg sys' }, 'bajarilmoqda…'));
    box.replaceChildren(...(out.length ? out
      : [h('div', { class: 'empty' }, "Yozma buyruq bering. Ovozda aytilganlar ham shu yerda ko'rinadi.")]));
    window.scrollTo(0, document.body.scrollHeight);
  };
  // Jonli: ovozda yoki boshqa qurilmadagi paneldan aytilganlar ham darhol chiqadi
  if (chatSocket) chatSocket.close();
  let live = false;
  try {
    chatSocket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/logs/stream');
    chatSocket.onopen = () => { live = true; };
    chatSocket.onclose = () => { live = false; };
    chatSocket.onmessage = (ev) => {
      let r; try { r = JSON.parse(ev.data); } catch { return; }
      const m = logToChat(r);
      if (!m) return;
      items.push(m);
      if (m.who !== 'user' && busy && m.source === 'panel') busy = false;
      draw();
    };
  } catch { chatSocket = null; }
  const go = async () => {
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = ''; busy = true; draw();
    send.disabled = true;
    try {
      const r = await api('/chat', { method: 'POST', body: { text } });
      if (!live) {  // jonli oqim bo'lmasa — natijani o'zimiz qo'shamiz
        const now = localIso();
        items.push({ ts: now, who: 'user', text, source: 'panel' });
        items.push(r.ok ? { ts: now, who: 'bot', text: r.reply, source: 'panel' }
          : { ts: now, who: 'sys', text: r.cancelled ? "to'xtatildi" : ('xato: ' + r.error), source: 'panel' });
      }
    } catch (e) { items.push({ ts: localIso(), who: 'sys', text: 'xato: ' + e.message, source: 'panel' }); }
    busy = false; send.disabled = false; draw(); input.focus();
  };
  send.addEventListener('click', go);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); } });
  show(h('h1', {}, 'Suhbat'),
    h('p', { class: 'muted' }, "Panel, ovoz va konsoldagi barcha suhbat (oxirgi 7 kun). Hammasi bitta umumiy suhbat: " +
      "yordamchi ovozda aytilganni ham, shu yerda yozilganni ham eslab turadi."),
    box, h('div', { class: 'chat-input' }, input, send, stop));
  draw(); input.focus();
};

// ---------------------------------------------------------------- taught skills
pages.skills = async () => {
  const data = await api('/skills');
  const teaching = data.teaching;
  const head = h('div', { class: 'card' },
    h('div', { class: 'section-title' }, h('strong', {}, "Ko'rsatib o'rgatish")),
    teaching
      ? h('div', {}, h('p', {}, `Hozir yozilmoqda: "${teaching}". Kerakli amallarni kompyuterda bajaring.`),
        h('div', { class: 'row' },
          h('button', { class: 'btn primary', onclick: () => teachStop(true) }, 'Tugatish va saqlash'),
          h('button', { class: 'btn', onclick: () => teachStop(false) }, 'Bekor qilish')))
      : h('div', {}, h('p', { class: 'muted' }, 'Nom bering, "Boshlash"ni bosing va amallarni bajaring: dasturni oching, ' +
          'tugmalarni bosing, matn yozing. Parol maydonlari yozilmaydi. Ovoz bilan ham bo\'ladi: "Jarvis, o\'rgatish rejimi…".'),
        (() => {
          const name = h('input', { placeholder: "masalan: burp suite ishga tushirish" });
          return h('div', { class: 'row' }, name,
            h('button', { class: 'btn primary', onclick: async () => {
              if (!name.value.trim()) return toast('Nom kiriting');
              try { await api('/teach/start', { method: 'POST', body: { name: name.value } }); toast('Yozish boshlandi'); render(); }
              catch (e) { fail(e); }
            } }, 'Boshlash'));
        })()));
  const list = h('div', { class: 'card' }, data.skills.length ? data.skills.map(skillItem)
    : h('div', { class: 'empty' }, "Hali o'rgatilgan skill yo'q."));
  show(h('h1', {}, "O'rgatilgan skill'lar"),
    h('p', { class: 'muted' }, "Siz ko'rsatgan qadamlar. Yordamchi so'rov mos kelganda ularni aynan takrorlaydi."), head, list);
};

async function teachStop(save) {
  try {
    const r = await api('/teach/stop', { method: 'POST', body: { save } });
    toast(save ? (r.ok === false ? r.error : `${r.steps} ta qadam saqlandi`) : 'Bekor qilindi', 4000);
  } catch (e) { fail(e); }
  render();
}

function skillItem(s) {
  return h('div', { class: 'list-item' },
    h('div', { class: 'main' },
      h('strong', {}, s.name), ' ', h('span', { class: 'pill' }, s.app || '?'), ' ',
      h('span', { class: 'muted' }, `${s.steps.length} qadam · ishlatilgan: ${s.uses}`),
      s.params && s.params.length ? h('div', { class: 'help' }, 'Parametrlar: ' + s.params.map((n) => `{${n}}`).join(', ')) : null,
      h('ol', { class: 'steps' }, s.steps.map((st) => h('li', {}, st.text_)))),
    h('div', { class: 'row' },
      h('button', { class: 'btn small', onclick: () => runSkill(s) }, 'Ishga tushirish'),
      h('button', { class: 'btn small', onclick: () => editSkill(s) }, 'Tahrirlash'),
      h('button', { class: 'btn small', onclick: () => skillToTool(s) }, 'Vositaga aylantirish'),
      h('button', { class: 'btn small danger', onclick: async () => {
        if (!confirm(`"${s.name}" o'chirilsinmi?`)) return;
        try { await api('/skills/' + s.slug, { method: 'DELETE' }); render(); } catch (e) { fail(e); }
      } }, "O'chirish")));
}

async function runSkill(s) {
  const params = {};
  for (const n of s.params || []) {
    const v = prompt(`{${n}} qiymati`);
    if (v === null) return;
    params[n] = v;
  }
  toast('Bajarilmoqda…', 60000);
  try {
    const r = await api(`/skills/${s.slug}/run`, { method: 'POST', body: { params } });
    toast(r.ok ? 'Bajarildi' : (r.paused_for_secret ? 'Parolni o\'zingiz kiriting' : `Xato (${(r.failed_step ?? 0) + 1}-qadam): ${r.error}`), 7000);
  } catch (e) { fail(e); }
}

function editSkill(s) {
  const name = h('input', { value: s.name });
  const steps = s.steps.map((st) => ({ ...st }));
  const list = h('ol', { class: 'steps' });
  const draw = () => list.replaceChildren(...steps.map((st, i) => h('li', {},
    h('div', { class: 'row' }, h('span', { class: 'spacer' }, st.text_ || st.type),
      st.type === 'text' && !st.secret ? h('button', { class: 'btn small', onclick: () => {
        const v = prompt('Yoziladigan matn', st.text); if (v !== null) { st.text = v; st.text_ = `yozish: "${v.slice(0, 40)}"`; draw(); }
      } }, 'Matn') : null,
      i > 0 ? h('button', { class: 'btn small', onclick: () => { [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]]; draw(); } }, '↑') : null,
      h('button', { class: 'btn small danger', onclick: () => { steps.splice(i, 1); draw(); } }, '✕')))));
  draw();
  modal('Skillni tahrirlash', h('div', {}, h('label', {}, 'Nomi'), name, h('label', {}, 'Qadamlar'), list,
    h('div', { class: 'help' }, "Yozilgan matnni {kontakt} kabi o'zgartirsangiz, skill parametrli bo'ladi: " +
      "yordamchi har safar o'rniga kerakli qiymatni yozadi (masalan, \"Telegramda Aliga yoz\").")), (close) => [
    h('button', { class: 'btn primary', onclick: async () => {
      try { await api('/skills/' + s.slug, { method: 'PUT', body: { name: name.value, steps } }); close(); render(); }
      catch (e) { fail(e); }
    } }, 'Saqlash')]);
}

async function skillToTool(s) {
  const base = s.name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  const name = /^[a-z]/.test(base) ? base.slice(0, 40) : ('skill_' + base).slice(0, 40);
  try {
    const data = await api('/usertools');
    await editTool(null, data, {
      kind: 'skill', skill: s.name, name: name.length >= 3 ? name : 'skill_' + name,
      description: `Runs the taught skill "${s.name}"` + (s.params.length ? ` (values for: ${s.params.join(', ')})` : ''),
      params: (s.params || []).map((n) => ({ name: n, type: 'string', description: '', required: true })),
    });
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- recipes & quirks
function procPage(kind) {
  return async () => {
    const data = await api('/procedures?kind=' + kind);
    const isQuirk = kind === 'quirk';
    const items = data.items.sort((a, b) => (a.app || '').localeCompare(b.app || ''));
    show(
      h('div', { class: 'row' }, h('h1', {}, isQuirk ? "Ilova o'ziga xosliklari" : 'Retseptlar'), h('span', { class: 'spacer' }),
        h('button', { class: 'btn primary', onclick: () => procEdit(kind, null) }, "+ Qo'shish")),
      h('p', { class: 'muted' }, isQuirk
        ? "Ilova har safar qiladigan \"injiqlik\" va uni qanday yengish. So'rovda shu ilova tilga olinsa, doim hisobga olinadi."
        : "Vazifani qanday bajarish bo'yicha bilim. Yordamchi muvaffaqiyatli vazifalardan o'zi yozadi, siz tahrirlashingiz mumkin."),
      h('div', { class: 'card' }, items.length ? items.map((p) => h('div', { class: 'list-item' },
        h('div', { class: 'main' },
          h('strong', {}, p.task), ' ', h('span', { class: 'pill' }, p.app || '?'),
          p.kind === 'task' ? h('span', { class: 'muted' }, ` · ishlatilgan: ${p.uses}`) : null,
          p.steps.length ? h('ol', { class: 'steps' }, p.steps.map((s) => h('li', {}, s))) : null,
          p.keywords.length ? h('div', { class: 'help' }, 'Kalit so\'zlar: ' + p.keywords.join(', ')) : null),
        h('div', { class: 'row' },
          h('button', { class: 'btn small', onclick: () => procEdit(kind, p) }, 'Tahrirlash'),
          h('button', { class: 'btn small danger', onclick: async () => {
            if (!confirm("O'chirilsinmi?")) return;
            try { await api('/procedures/' + p.id, { method: 'DELETE' }); render(); } catch (e) { fail(e); }
          } }, "O'chirish")))) : h('div', { class: 'empty' }, "Hali yo'q.")));
  };
}

function procEdit(kind, p) {
  const isQuirk = kind === 'quirk';
  const task = h('textarea', { rows: 2 }); task.value = p ? p.task : '';
  const app = h('input', { value: p ? p.app : '', placeholder: 'Word, Telegram, Chrome…' });
  const steps = h('textarea', { rows: 5, placeholder: "Har qatorda bitta qadam" }); steps.value = p ? p.steps.join('\n') : '';
  const kw = h('input', { value: p ? p.keywords.join(', ') : '', placeholder: 'word, hujjat, yangi' });
  modal(p ? 'Tahrirlash' : "Qo'shish", h('div', {},
    h('label', {}, isQuirk ? "Nima bo'ladi va nima qilish kerak" : 'Vazifa'), task,
    h('label', {}, 'Ilova'), app,
    isQuirk ? null : h('div', {}, h('label', {}, 'Qadamlar'), steps),
    h('label', {}, "Kalit so'zlar (vergul bilan)"), kw,
    h('div', { class: 'help' }, "So'rovda shu so'zlar bo'lsa, bu bilim yordamchiga ko'rsatiladi.")), (close) => [
    h('button', { class: 'btn primary', onclick: async () => {
      const body = { kind, task: task.value, app: app.value, steps: linesToList(steps.value), keywords: wordsToList(kw.value) };
      try {
        if (p) await api('/procedures/' + p.id, { method: 'PUT', body });
        else await api('/procedures', { method: 'POST', body });
        close(); render();
      } catch (e) { fail(e); }
    } }, 'Saqlash')]);
}

pages.recipes = procPage('task');
pages.quirks = procPage('quirk');

// ---------------------------------------------------------------- facts
pages.facts = async () => {
  const data = await api('/facts');
  const inp = h('input', { placeholder: "Masalan: Menga 'Aziz aka' deb murojaat qil" });
  show(
    h('h1', {}, 'Faktlar va xotira'),
    h('p', { class: 'muted' }, `Yordamchi siz haqingizda doim yodda tutadigan narsalar (ko'pi bilan ${data.max} ta). ` +
      'Har bir buyruqda unga ko\'rsatiladi.'),
    h('div', { class: 'card' }, h('div', { class: 'row' }, inp, h('button', { class: 'btn primary', onclick: async () => {
      if (!inp.value.trim()) return;
      try { await api('/facts', { method: 'POST', body: { fact: inp.value } }); render(); } catch (e) { fail(e); }
    } }, "Qo'shish"))),
    h('div', { class: 'card' }, data.facts.length ? data.facts.map((f, i) => h('div', { class: 'list-item' },
      h('div', { class: 'main' }, f),
      h('div', { class: 'row' },
        h('button', { class: 'btn small', onclick: async () => {
          const v = prompt('Tahrirlash', f); if (v === null) return;
          try { await api('/facts/' + i, { method: 'PUT', body: { fact: v } }); render(); } catch (e) { fail(e); }
        } }, 'Tahrirlash'),
        h('button', { class: 'btn small danger', onclick: async () => {
          if (!confirm("O'chirilsinmi?")) return;
          try { await api('/facts/' + i, { method: 'DELETE' }); render(); } catch (e) { fail(e); }
        } }, "O'chirish")))) : h('div', { class: 'empty' }, "Hali fakt yo'q.")));
};

// ---------------------------------------------------------------- tools
pages.tools = async () => {
  const data = await api('/tools');
  const q = h('input', { placeholder: 'Qidirish…' });
  const body = h('tbody');
  const draw = () => {
    const s = q.value.toLowerCase();
    body.replaceChildren(...data.tools.filter((t) => !s || (t.name + t.description + t.source).toLowerCase().includes(s))
      .map((t) => h('tr', {},
        h('td', {}, h('div', { class: 'mono' }, t.name), h('div', { class: 'help' }, t.description.split('\n')[0])),
        h('td', {}, h('span', { class: 'pill' }, t.source)),
        h('td', {}, h('span', { class: 'pill ' + (t.risk === 'confirm' ? 'warn' : '') }, t.risk === 'confirm' ? 'tasdiq' : 'xavfsiz')),
        h('td', {}, toggle(!t.disabled, (on, el) => patchTool(t, { disabled: !on }, el))),
        h('td', {}, t.risk === 'confirm' && !t.always_confirm ? h('span', { class: 'muted' }, '—')
          : toggle(t.always_confirm, (on, el) => patchTool(t, { always_confirm: on }, el))))));
  };
  q.addEventListener('input', draw);
  show(
    h('h1', {}, 'Vositalar'),
    h('p', { class: 'muted' }, "Yordamchining kompyuterdagi \"qo'l-oyog'i\". O'chirilgan vosita modelga ko'rsatilmaydi. " +
      '"Doim tasdiq" yoqilsa, har safar ruxsat so\'raydi. Xavf darajasini pasaytirib bo\'lmaydi.'),
    q,
    h('div', { class: 'card table-wrap', style: 'margin-top:12px' }, h('table', { class: 'table' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Vosita'), h('th', {}, 'Manba'), h('th', {}, 'Xavf'), h('th', {}, 'Yoqilgan'),
        h('th', {}, 'Doim tasdiq'))), body)));
  draw();
};

async function patchTool(t, body, el) {
  try { const r = await api('/tools/' + encodeURIComponent(t.name), { method: 'PATCH', body }); Object.assign(t, r.tool); }
  catch (e) { fail(e); if (el) el.checked = !el.checked; }
}

// ---------------------------------------------------------------- user-created tools
const KIND_LABEL = { http: 'API (HTTP)', script: 'Skript', skill: "O'rgatilgan skill" };
const KIND_HELP = {
  http: "Istalgan xizmat API'sini chaqiradi. URL, sarlavha va tanada {parametr} va {secret:KALIT} ishlatiladi. " +
    'Kalitlar tizim saqlagichida turadi, natijada doim yashiriladi.',
  script: "PowerShell, sh yoki Python skripti. Parametrlar muhit o'zgaruvchisi bo'lib keladi: $env:P_SHAHAR " +
    "(PowerShell), $P_SHAHAR (sh), os.environ['P_SHAHAR'] (Python). Har safar tasdiq so'raladi.",
  skill: "Ko'rsatib o'rgatilgan skill'ni parametr bilan chaqiradi. Skill matnida {kontakt} kabi joylar bo'lsa, " +
    'yordamchi ularga har safar kerakli qiymatni qo\'yadi.',
};

pages.mytools = async () => {
  const data = await api('/usertools');
  const list = h('div', { class: 'card' }, data.tools.length ? data.tools.map((t) => h('div', { class: 'list-item' },
    h('div', { class: 'main' },
      h('strong', { class: 'mono' }, t.name), ' ', h('span', { class: 'pill' }, KIND_LABEL[t.kind] || t.kind), ' ',
      h('span', { class: 'pill ' + (t.risk === 'confirm' ? 'warn' : '') }, t.risk === 'confirm' ? 'tasdiq' : 'xavfsiz'), ' ',
      t.error ? h('span', { class: 'pill warn' }, 'xato') : null,
      h('div', { class: 'help' }, t.description),
      t.params && t.params.length ? h('div', { class: 'help' }, 'Parametrlar: ' + t.params.map((p) => p.name).join(', ')) : null,
      t.error ? h('div', { class: 'help', style: 'color:var(--warn)' }, t.error) : null),
    h('div', { class: 'row' },
      toggle(t.enabled !== false, async (on, el) => {
        try { await api('/usertools/' + t.name, { method: 'PUT', body: { ...t, enabled: on } }); render(); }
        catch (e) { fail(e); el.checked = !on; }
      }),
      h('button', { class: 'btn small', onclick: () => testTool(t) }, 'Sinash'),
      h('button', { class: 'btn small', onclick: () => editTool(t, data) }, 'Tahrirlash'),
      h('button', { class: 'btn small danger', onclick: async () => {
        if (!confirm(`"${t.name}" o'chirilsinmi?`)) return;
        try { await api('/usertools/' + t.name, { method: 'DELETE' }); render(); } catch (e) { fail(e); }
      } }, "O'chirish"))))
    : h('div', { class: 'empty' }, "Hali o'zingiz yaratgan vosita yo'q."));
  const secrets = data.secrets.length ? h('div', { class: 'card' }, h('h2', { style: 'margin-top:0' }, 'Vosita kalitlari'),
    data.secrets.map((s) => h('div', { class: 'list-item' },
      h('div', { class: 'main' }, h('span', { class: 'mono' }, s.name), ' ',
        h('span', { class: 'pill ' + (s.is_set ? 'ok' : 'warn') }, s.is_set ? "o'rnatilgan" : "o'rnatilmagan")),
      h('button', { class: 'btn small', onclick: () => setSecret(s) }, s.is_set ? 'Almashtirish' : 'Kiritish')))) : null;
  show(
    h('div', { class: 'row' }, h('h1', {}, 'Mening vositalarim'), h('span', { class: 'spacer' }),
      h('button', { class: 'btn', onclick: importTool }, 'JSON import'),
      h('button', { class: 'btn primary', onclick: () => editTool(null, data) }, '+ Yangi vosita')),
    h('p', { class: 'muted' }, "Kod yozmasdan yangi vosita qo'shing: API chaqiruvi, skript yoki o'rgatilgan skill. " +
      'Saqlangan zahoti yordamchi uni ishlata boshlaydi, qayta ishga tushirish shart emas.'),
    list, secrets);
};

function setSecret(s) {
  const inp = h('input', { type: 'password', autocomplete: 'off', placeholder: 'qiymat' });
  modal(`Kalit: ${s.name}`, h('div', {}, inp, h('div', { class: 'help' },
    "Tizim saqlagichiga yoziladi va panelda boshqa ko'rinmaydi. Bo'sh qoldirsangiz o'chiriladi.")),
  (close) => [h('button', { class: 'btn primary', onclick: async () => {
    try { await api('/usertools/secrets/' + s.name, { method: 'PUT', body: { value: inp.value } }); close(); render(); }
    catch (e) { fail(e); }
  } }, 'Saqlash')]);
}

function importTool() {
  const ta = h('textarea', { rows: 12, placeholder: '{"name": "...", "kind": "http", ...}' });
  modal('JSON import', h('div', {}, ta, h('div', { class: 'help' },
    "Boshqa foydalanuvchi ulashgan vosita. Kalitlar JSON ichida bo'lmaydi: ularni keyin o'zingiz kiritasiz.")),
  (close) => [h('button', { class: 'btn primary', onclick: async () => {
    let spec;
    try { spec = JSON.parse(ta.value); } catch (e) { toast('JSON xato: ' + e.message, 5000); return; }
    try { await api('/usertools', { method: 'POST', body: spec }); close(); render(); toast("Qo'shildi"); } catch (e) { fail(e); }
  } }, "Qo'shish")]);
}

function paramEditor(initial) {
  const rows = initial.map((p) => ({ ...p }));
  const box = h('div', {});
  const draw = () => box.replaceChildren(
    ...rows.map((p, i) => {
      const name = h('input', { value: p.name || '', placeholder: 'nom (shahar)' });
      name.addEventListener('input', () => { p.name = name.value.trim(); });
      const type = h('select', {}, ['string', 'integer', 'number', 'boolean'].map((t) => h('option',
        { value: t, selected: t === (p.type || 'string') || null },
        { string: 'matn', integer: 'butun son', number: 'son', boolean: "ha/yo'q" }[t])));
      type.addEventListener('change', () => { p.type = type.value; });
      const desc = h('input', { value: p.description || '', placeholder: "nima ekanligi (modelga ko'rsatiladi)" });
      desc.addEventListener('input', () => { p.description = desc.value; });
      return h('div', { class: 'row', style: 'margin-bottom:6px;flex-wrap:wrap' },
        h('div', { style: 'flex:1 1 90px' }, name), h('div', { style: 'flex:0 0 110px' }, type),
        h('div', { style: 'flex:2 1 140px' }, desc), h('span', { class: 'help' }, 'majburiy'),
        toggle(p.required !== false, (on) => { p.required = on; }),
        h('button', { class: 'btn small danger', onclick: () => { rows.splice(i, 1); draw(); } }, '✕'));
    }),
    h('button', { class: 'btn small', onclick: () => { rows.push({ name: '', type: 'string', description: '', required: true }); draw(); } },
      '+ Parametr'));
  draw();
  return { el: box, get: () => rows.filter((p) => p.name), set: (list) => { rows.splice(0, rows.length, ...list); draw(); } };
}

function headersToText(hd) { return Object.entries(hd || {}).map(([k, v]) => `${k}: ${v}`).join('\n'); }
function textToHeaders(t) {
  const out = {};
  for (const line of linesToList(t)) { const i = line.indexOf(':'); if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
  return out;
}

async function editTool(orig, data, preset = null) {
  const isNew = !orig;
  const t = orig ? { ...orig } : { kind: 'http', method: 'GET', body_type: 'json', shell: data.shells[0], params: [], timeout: 30, enabled: true, ...(preset || {}) };
  const skills = (await api('/skills').catch(() => ({ skills: [] }))).skills;
  const name = h('input', { value: t.name || '', placeholder: 'masalan: ob_havo' });
  const desc = h('textarea', { rows: 2, placeholder: 'Qachon ishlatiladi: masalan "Shahardagi hozirgi ob-havoni aytadi"' });
  desc.value = t.description || '';
  const kind = h('select', {}, data.kinds.map((k) => h('option', { value: k, selected: k === t.kind || null }, KIND_LABEL[k])));
  const kindHelp = h('div', { class: 'help' });
  const params = paramEditor(t.params || []);
  const risk = h('select', {}, h('option', { value: 'safe' }, "xavfsiz: o'zi bajaradi"), h('option', { value: 'confirm' }, "tasdiq so'rasin"));
  const timeout = h('input', { type: 'number', value: t.timeout || 30, min: 3, max: 300 });
  // http
  const method = h('select', {}, data.methods.map((m) => h('option', { value: m, selected: m === t.method || null }, m)));
  const url = h('input', { value: t.url || '', placeholder: 'https://api.example.com/weather?city={shahar}' });
  const headers = h('textarea', { rows: 2, placeholder: 'Authorization: Bearer {secret:OBHAVO_KALIT}' });
  headers.value = headersToText(t.headers);
  const bodyType = h('select', {}, data.body_types.map((b) => h('option', { value: b, selected: b === t.body_type || null }, b)));
  const body = h('textarea', { rows: 4, placeholder: '{"text": "{matn}"}' }); body.value = t.body || '';
  const resultPath = h('input', { value: t.result_path || '', placeholder: "masalan: current.temp (bo'sh: butun javob)" });
  // script
  const shell = h('select', {}, data.shells.map((s) => h('option', { value: s, selected: s === t.shell || null }, s)));
  const script = h('textarea', { rows: 8, class: 'mono', placeholder: 'Get-Date -Format "HH:mm"' }); script.value = t.script || '';
  const secretsInp = h('input', { value: (t.secrets || []).join(', '), placeholder: 'KALIT_NOMI (skriptda $env:SECRET_KALIT_NOMI)' });
  // skill
  const skill = h('select', {}, h('option', { value: '' }, '— tanlang —'), skills.map((s) => h('option',
    { value: s.name, selected: s.name === t.skill || null }, s.name + (s.params.length ? ` (${s.params.join(', ')})` : ''))));
  skill.addEventListener('change', () => {
    const s = skills.find((x) => x.name === skill.value);
    if (s && !params.get().length && s.params.length) params.set(s.params.map((n) => ({ name: n, type: 'string', description: '', required: true })));
  });
  const sections = {
    http: h('div', {},
      h('div', { class: 'row' }, h('div', { style: 'flex:0 0 110px' }, h('label', {}, 'Metod'), method),
        h('div', { class: 'spacer' }, h('label', {}, 'URL'), url)),
      h('label', {}, 'Sarlavhalar (har qatorda Nom: qiymat)'), headers,
      h('div', { class: 'row' }, h('div', { style: 'flex:0 0 110px' }, h('label', {}, 'Tana turi'), bodyType),
        h('div', { class: 'spacer' }, h('label', {}, 'Javobdan olinadigan qism'), resultPath)),
      h('label', {}, 'Tana (GET uchun ishlatilmaydi)'), body),
    script: h('div', {}, h('label', {}, 'Til'), shell, h('label', {}, 'Skript'), script,
      h('label', {}, 'Ishlatadigan kalitlar (vergul bilan)'), secretsInp),
    skill: h('div', {}, h('label', {}, "O'rgatilgan skill"), skill,
      h('div', { class: 'help' }, "Skill matnidagi {nom} joylari uchun shu nomdagi parametr qo'shing.")),
  };
  const kindBox = h('div', {});
  const syncKind = () => {
    kindBox.replaceChildren(sections[kind.value]);
    kindHelp.textContent = KIND_HELP[kind.value];
    risk.disabled = kind.value === 'script';
    risk.value = kind.value === 'script' ? 'confirm'
      : (t.risk && t.kind === kind.value ? t.risk : (kind.value === 'http' && method.value !== 'GET' ? 'confirm' : 'safe'));
  };
  kind.addEventListener('change', syncKind);
  method.addEventListener('change', () => { if (isNew) syncKind(); });
  syncKind();
  const collect = () => {
    const spec = { name: name.value.trim(), kind: kind.value, description: desc.value, params: params.get(), risk: risk.value,
      timeout: Number(timeout.value) || 30, enabled: t.enabled !== false };
    if (kind.value === 'http') Object.assign(spec, { method: method.value, url: url.value.trim(), headers: textToHeaders(headers.value),
      body_type: bodyType.value, body: body.value, result_path: resultPath.value.trim() });
    if (kind.value === 'script') Object.assign(spec, { shell: shell.value, script: script.value, secrets: wordsToList(secretsInp.value) });
    if (kind.value === 'skill') spec.skill = skill.value;
    return spec;
  };
  modal(isNew ? 'Yangi vosita' : `Tahrirlash: ${t.name}`, h('div', {},
    h('div', { class: 'row' }, h('div', { class: 'spacer' }, h('label', {}, 'Nomi'), name),
      h('div', { style: 'flex:0 0 190px' }, h('label', {}, 'Turi'), kind)),
    kindHelp,
    h('label', {}, 'Tavsif (yordamchi shu orqali qachon ishlatishni biladi)'), desc,
    h('label', {}, 'Parametrlar'), params.el,
    kindBox,
    h('div', { class: 'row' }, h('div', { class: 'spacer' }, h('label', {}, 'Xavf'), risk),
      h('div', { style: 'flex:0 0 120px' }, h('label', {}, 'Vaqt (s)'), timeout))), (close) => [
    h('button', { class: 'btn', onclick: () => testTool(collect()) }, 'Sinash'),
    h('button', { class: 'btn', onclick: () => {
      const blob = new Blob([JSON.stringify(collect(), null, 1)], { type: 'application/json' });
      h('a', { href: URL.createObjectURL(blob), download: (name.value || 'vosita') + '.json' }).click();
    } }, 'JSON'),
    h('button', { class: 'btn primary', onclick: async () => {
      try {
        const r = isNew ? await api('/usertools', { method: 'POST', body: collect() })
          : await api('/usertools/' + t.name, { method: 'PUT', body: collect() });
        close(); render();
        toast(r.error ? 'Saqlandi, lekin: ' + r.error : 'Saqlandi: yordamchi endi ishlata oladi', 5000);
      } catch (e) { fail(e); }
    } }, 'Saqlash')]);
}

function testTool(spec) {
  const inputs = {};
  const fields = (spec.params || []).map((p) => {
    const inp = p.type === 'boolean'
      ? h('select', {}, h('option', { value: 'true' }, 'ha'), h('option', { value: 'false' }, "yo'q"))
      : h('input', { type: p.type === 'string' ? 'text' : 'number', value: p.default ?? '', placeholder: p.description || '' });
    inputs[p.name] = () => {
      if (inp.value === '') return undefined;
      if (p.type === 'boolean') return inp.value === 'true';
      return p.type === 'string' ? inp.value : Number(inp.value);
    };
    return h('div', {}, h('label', {}, p.name + (p.required !== false ? ' *' : '')), inp);
  });
  const out = h('pre', { class: 'mono', style: 'white-space:pre-wrap;max-height:300px;overflow:auto' });
  modal(`Sinash: ${spec.name || 'vosita'}`, h('div', {}, fields.length ? fields : h('p', { class: 'muted' }, "Parametr yo'q."), out), () => [
    h('button', { class: 'btn primary', onclick: async () => {
      if (spec.kind === 'script' && !confirm('Skript shu kompyuterda bajariladi. Davom etilsinmi?')) return;
      const args = {};
      for (const [k, get] of Object.entries(inputs)) { const v = get(); if (v !== undefined) args[k] = v; }
      out.textContent = 'Bajarilmoqda…';
      try {
        const r = await api('/usertools/test', { method: 'POST', body: { spec, args, confirm: spec.kind === 'script' } });
        out.textContent = JSON.stringify(r, null, 2);
      } catch (e) { out.textContent = 'Xato: ' + e.message; }
    } }, 'Bajarish')]);
}

// ---------------------------------------------------------------- plugins
pages.plugins = async () => {
  const data = await api('/plugins');
  show(
    h('h1', {}, "Plugin'lar"),
    h('p', { class: 'muted' }, "Yangi funksiyalar alohida modul bo'lib qo'shiladi va asosiy kodga tegmaydi. " +
      'O\'z plugin\'ingizni ', h('code', {}, data.user_dir), ' papkasiga qo\'ying. Batafsil: docs/PLUGINS.md.'),
    h('div', { class: 'card' }, data.plugins.length ? data.plugins.map((p) => h('div', { class: 'list-item' },
      h('div', { class: 'main' },
        h('strong', {}, p.name), ' ', h('span', { class: 'muted' }, `v${p.version} · ${p.id}`), ' ',
        h('span', { class: 'pill ' + ({ loaded: 'ok', error: 'bad', disabled: '' }[p.status] || '') },
          { loaded: 'ishlayapti', error: 'xato', disabled: "o'chiq", not_loaded: 'yuklanmagan' }[p.status] || p.status),
        p.builtin ? h('span', { class: 'pill' }, 'ichki') : null,
        h('div', { class: 'help' }, p.description),
        p.tools.length ? h('div', { class: 'help' }, 'Vositalar: ' + p.tools.join(', ')) : null,
        p.error ? h('pre', { class: 'error mono', style: 'white-space:pre-wrap' }, p.error) : null),
      toggle(p.enabled, async (on, el) => {
        try { await api('/plugins/' + p.id, { method: 'PATCH', body: { enabled: on } }); toast("Kuchga kirishi uchun qayta ishga tushiring", 4000); }
        catch (e) { fail(e); el.checked = !on; }
      }))) : h('div', { class: 'empty' }, "Plugin yo'q.")));
};

// ---------------------------------------------------------------- settings
let CHECK = { options: {}, results: [], ts: null };

pages.settings = async () => {
  const [data, last] = await Promise.all([api('/settings'), api('/settings/options').catch(() => null)]);
  if (last && last.ts) CHECK = last;
  const groups = {};
  for (const s of data.settings) (groups[s.section || s.group] ||= []).push(s);
  const inputs = {};
  const dirty = { v: false };
  const form = h('div', {});
  form.addEventListener('input', () => { dirty.v = true; });
  form.addEventListener('change', () => { dirty.v = true; });
  for (const [section, items] of Object.entries(groups)) {
    const card = h('div', { class: 'card' }, h('h2', { style: 'margin-top:0' }, section));
    for (const s of items) card.append(settingField(s, inputs));
    form.append(card);
  }
  const save = h('button', { class: 'btn primary' }, 'Saqlash');
  const saveValues = async () => {
    const values = {};
    for (const [key, get] of Object.entries(inputs)) { const v = get(); if (v !== undefined) values[key] = v; }
    const r = await api('/settings', { method: 'PUT', body: { values } });
    dirty.v = false;
    return r;
  };
  save.addEventListener('click', async () => {
    try {
      const r = await saveValues();
      if (Object.keys(r.errors).length) toast('Xatolar: ' + Object.entries(r.errors).map(([k, v]) => `${k}: ${v}`).join('; '), 8000);
      else toast(r.restart_needed ? 'Saqlandi. Kuchga kirishi uchun qayta ishga tushiring.' : 'Saqlandi', 4000);
      render();
    } catch (e) { fail(e); }
  });
  const checkBtn = h('button', { class: 'btn' }, 'Ulanishni tekshirish');
  const checkBox = h('div', {});
  checkBox.append(checkResults(CHECK));
  checkBtn.addEventListener('click', async () => {
    checkBtn.disabled = true; checkBtn.textContent = 'Tekshirilmoqda…';
    try {
      if (dirty.v) {  // kiritilgan, lekin saqlanmagan kalitlar bilan tekshiramiz
        const r = await saveValues();
        if (Object.keys(r.errors).length) toast('Saqlanmadi: ' + Object.values(r.errors).join('; '), 8000);
      }
      CHECK = await api('/settings/check', { method: 'POST' });
      await render();
      const box = document.getElementById('checkCard');
      if (box) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) { fail(e); checkBtn.disabled = false; checkBtn.textContent = 'Ulanishni tekshirish'; }
  });
  show(
    h('div', { class: 'row', style: 'flex-wrap:wrap' }, h('h1', {}, 'Sozlamalar va kalitlar'), h('span', { class: 'spacer' }),
      checkBtn, save, h('button', { class: 'btn', onclick: restart }, 'Qayta ishga tushirish')),
    h('p', { class: 'muted' }, `Maxfiy kalitlar tizim saqlagichida turadi (${data.secret_backend}) va bu yerda hech qachon ` +
      "ko'rsatilmaydi. .env yoki muhit o'zgaruvchisi bilan berilgan qiymatlarni shu joyda o'zgartiring."),
    checkBox, form);
};

function checkResults(c) {
  if (!c || !c.results || !c.results.length) return null;
  const when = c.ts ? new Date(c.ts * 1000).toLocaleTimeString() : '';
  return h('div', { class: 'card', id: 'checkCard' },
    h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, 'Ulanish natijasi'), h('span', { class: 'spacer' }),
      h('span', { class: 'muted' }, when)),
    c.results.map((r) => {
      const cls = r.ok === true ? 'ok' : r.ok === false ? 'bad' : '';
      const word = r.ok === true ? 'ishlayapti' : r.ok === false ? 'xato' : "o'tkazildi";
      const list = (r.details || []).length ? h('details', {},
        h('summary', {}, `Ro'yxat (${r.details.length})`),
        h('table', { class: 'table' }, h('tbody', {}, r.details.map((d) => h('tr', {},
          h('td', { class: 'mono' }, d.name), h('td', { class: 'muted' }, d.info || '')))))) : null;
      return h('div', { class: 'list-item' }, h('div', { class: 'main' },
        h('strong', {}, r.label), ' ', h('span', { class: 'pill ' + cls }, word), ' ',
        h('span', { class: 'muted' }, `${r.ms} ms`),
        h('div', {}, r.summary || r.error || ''), list));
    }),
    Object.keys(c.options || {}).length ? h('div', { class: 'help' },
      "Topilgan modellar va ovozlar quyidagi maydonlarda tanlash ro'yxati bo'lib chiqdi.") : null);
}

function settingField(s, inputs) {
  const fromEnv = s.source === 'env';
  const srcPill = h('span', { class: 'pill src ' + (fromEnv ? 'warn' : '') },
    fromEnv ? `env: ${s.env_name}` : ({ config: 'saqlangan', keyring: 'saqlagichda', default: 'standart' }[s.source] || s.source));
  let input, get;
  if (s.type === 'bool') {
    input = h('input', { type: 'checkbox', checked: s.value || null, disabled: fromEnv || null });
    get = () => (fromEnv ? undefined : input.checked);
    input = h('label', { class: 'toggle' }, input, h('span'));
    const inner = input.querySelector('input');
    get = () => (fromEnv ? undefined : inner.checked);
  } else if ((CHECK.options || {})[s.key] && CHECK.options[s.key].length && !fromEnv) {
    const opts = [...CHECK.options[s.key]];
    const cur = s.value ?? '';
    if (!opts.includes(cur)) opts.unshift(cur);
    input = h('select', {}, opts.map((o) => h('option', { value: o, selected: o === cur || null },
      o === '' ? '(standart)' : (o === cur && !CHECK.options[s.key].includes(cur) ? `${o} (ro'yxatda yo'q)` : o))));
    get = () => input.value;
  } else if (s.type === 'choice') {
    input = h('select', { disabled: fromEnv || null }, s.choices.map((c) => h('option', { value: c, selected: c === s.value || null }, c)));
    get = () => (fromEnv ? undefined : input.value);
  } else if (s.type === 'secret') {
    input = h('input', { type: 'password', autocomplete: 'off', disabled: fromEnv || null,
      placeholder: s.is_set ? "o'rnatilgan — o'zgartirish uchun yangisini yozing" : "o'rnatilmagan" });
    get = () => (fromEnv || !input.value ? undefined : input.value);
  } else if (s.type === 'text') {
    input = h('textarea', { disabled: fromEnv || null }); input.value = s.value ?? '';
    get = () => (fromEnv ? undefined : input.value);
  } else {
    input = h('input', { type: s.type === 'int' || s.type === 'float' ? 'number' : 'text', step: s.type === 'float' ? 'any' : null,
      value: s.value ?? '', disabled: fromEnv || null });
    get = () => (fromEnv ? undefined : input.value);
  }
  inputs[s.key] = get;
  const clear = s.type === 'secret' && s.is_set && !fromEnv ? h('button', { class: 'btn small danger', onclick: async () => {
    if (!confirm("Kalit o'chirilsinmi?")) return;
    try { await api('/settings', { method: 'PUT', body: { clear: [s.key] } }); render(); } catch (e) { fail(e); }
  } }, "O'chirish") : null;
  return h('div', { class: 'field' },
    h('label', {}, s.label, s.required ? ' *' : '', ' ', srcPill, s.restart ? null : h('span', { class: 'pill' }, 'darhol')),
    h('div', { class: 'row' }, h('div', { class: 'spacer' }, input), clear),
    h('div', { class: 'help' }, h('code', {}, s.key), s.help ? ' — ' + s.help : ''));
}

// ---------------------------------------------------------------- logs
let logSocket = null;
pages.logs = async () => {
  const data = await api('/logs?limit=300');
  const box = h('div', { class: 'log' });
  const day = h('select', {}, (data.days.length ? data.days : [data.day]).map((d) => h('option', { value: d }, d)));
  const q = h('input', { placeholder: 'Matn bo\'yicha filtr' });
  const live = h('input', { type: 'checkbox', checked: true });
  const line = (r) => {
    const kind = r.kind === 'voice' ? `voice:${r.ev}` : r.kind;
    const rest = { ...r }; delete rest.ts; delete rest.kind; delete rest.state;
    if (r.kind === 'tool' && rest.result) rest.result = JSON.stringify(rest.result).slice(0, 300);
    return h('div', { class: 'k-' + r.kind }, `${fmtTime(r.ts).slice(11)} ${kind}  ${JSON.stringify(rest)}`);
  };
  const load = async () => {
    const d = await api(`/logs?day=${day.value}&limit=500${q.value ? '&q=' + encodeURIComponent(q.value) : ''}`);
    box.replaceChildren(...d.items.map(line)); box.scrollTop = box.scrollHeight;
  };
  box.replaceChildren(...data.items.map(line));
  day.addEventListener('change', load); q.addEventListener('change', load);
  show(h('h1', {}, 'Loglar'),
    h('p', { class: 'muted' }, 'Yordamchi nimani eshitgani, qaysi vositani chaqirgani va nima javob bergani.'),
    h('div', { class: 'row', style: 'margin-bottom:10px' }, day, h('div', { class: 'spacer' }, q),
      h('label', { class: 'row', style: 'margin:0;font-weight:400' }, live, 'Jonli')), box);
  box.scrollTop = box.scrollHeight;
  if (logSocket) logSocket.close();
  logSocket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/logs/stream');
  logSocket.onmessage = (ev) => {
    if (!live.checked || day.value !== data.days[0]) return;
    try {
      const r = JSON.parse(ev.data);
      if (r.kind === 'voice' && ['state', 'vad', 'gate_ms'].includes(r.ev)) return;
      if (q.value && !ev.data.toLowerCase().includes(q.value.toLowerCase())) return;
      box.append(line(r)); box.scrollTop = box.scrollHeight;
    } catch { /* */ }
  };
};

// ------------------------------------------------------------------ router
const routes = { '': 'home', chat: 'chat', skills: 'skills', recipes: 'recipes', quirks: 'quirks', facts: 'facts',
  tools: 'tools', mytools: 'mytools', plugins: 'plugins', settings: 'settings', logs: 'logs' };

async function render() {
  const key = location.hash.replace(/^#\/?/, '').split('?')[0];
  const page = routes[key] || 'home';
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.page === page));
  $('#nav').classList.remove('open');
  document.querySelectorAll('.modal-bg').forEach((m) => m.remove()); // boshqa sahifaga o'tganda ochiq oyna qolmasin
  if (page !== 'logs' && logSocket) { logSocket.close(); logSocket = null; }
  if (page !== 'chat' && chatSocket) { chatSocket.close(); chatSocket = null; }
  try { await pages[page](); } catch (e) { show(h('div', { class: 'card error' }, 'Yuklab bo\'lmadi: ' + e.message)); }
  view.focus({ preventScroll: true });
}

$('#menuBtn').addEventListener('click', () => $('#nav').classList.toggle('open'));
$('#logoutBtn').addEventListener('click', async () => { await api('/logout', { method: 'POST' }).catch(() => {}); location.href = '/login'; });
window.addEventListener('hashchange', render);
render();
refreshStatus();
setInterval(refreshStatus, 2500);
