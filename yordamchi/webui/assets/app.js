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
const chatLog = [];
pages.chat = async () => {
  const box = h('div', { class: 'chat' });
  const input = h('textarea', { rows: 2, placeholder: "Masalan: word dasturini och va yangi hujjat yarat" });
  const send = h('button', { class: 'btn primary' }, 'Yuborish');
  const stop = h('button', { class: 'btn', onclick: () => api('/chat/cancel', { method: 'POST' }).catch(fail) }, "To'xtat");
  const draw = () => {
    box.replaceChildren(...(chatLog.length ? chatLog.map((m) => h('div', { class: 'msg ' + m.who }, m.text))
      : [h('div', { class: 'empty' }, 'Yozma buyruq bering. Xavfli amallar uchun shu yerda tasdiq so\'raladi.')]));
    window.scrollTo(0, document.body.scrollHeight);
  };
  const go = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = ''; chatLog.push({ who: 'user', text }); chatLog.push({ who: 'sys', text: 'bajarilmoqda…' }); draw();
    send.disabled = true;
    try {
      const r = await api('/chat', { method: 'POST', body: { text } });
      chatLog.pop();
      chatLog.push(r.ok ? { who: 'bot', text: r.reply } : { who: 'sys', text: r.cancelled ? "to'xtatildi" : ('xato: ' + r.error) });
    } catch (e) { chatLog.pop(); chatLog.push({ who: 'sys', text: 'xato: ' + e.message }); }
    send.disabled = false; draw(); input.focus();
  };
  send.addEventListener('click', go);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); } });
  show(h('h1', {}, 'Suhbat'), box, h('div', { class: 'chat-input' }, input, send, stop));
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
      h('ol', { class: 'steps' }, s.steps.map((st) => h('li', {}, st.text_)))),
    h('div', { class: 'row' },
      h('button', { class: 'btn small', onclick: () => runSkill(s) }, 'Ishga tushirish'),
      h('button', { class: 'btn small', onclick: () => editSkill(s) }, 'Tahrirlash'),
      h('button', { class: 'btn small danger', onclick: async () => {
        if (!confirm(`"${s.name}" o'chirilsinmi?`)) return;
        try { await api('/skills/' + s.slug, { method: 'DELETE' }); render(); } catch (e) { fail(e); }
      } }, "O'chirish")));
}

async function runSkill(s) {
  toast('Bajarilmoqda…', 60000);
  try {
    const r = await api(`/skills/${s.slug}/run`, { method: 'POST', body: {} });
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
  modal('Skillni tahrirlash', h('div', {}, h('label', {}, 'Nomi'), name, h('label', {}, 'Qadamlar'), list), (close) => [
    h('button', { class: 'btn primary', onclick: async () => {
      try { await api('/skills/' + s.slug, { method: 'PUT', body: { name: name.value, steps } }); close(); render(); }
      catch (e) { fail(e); }
    } }, 'Saqlash')]);
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
pages.settings = async () => {
  const data = await api('/settings');
  const groups = {};
  for (const s of data.settings) (groups[s.section || s.group] ||= []).push(s);
  const inputs = {};
  const form = h('div', {});
  for (const [section, items] of Object.entries(groups)) {
    const card = h('div', { class: 'card' }, h('h2', { style: 'margin-top:0' }, section));
    for (const s of items) card.append(settingField(s, inputs));
    form.append(card);
  }
  const save = h('button', { class: 'btn primary' }, 'Saqlash');
  save.addEventListener('click', async () => {
    const values = {};
    for (const [key, get] of Object.entries(inputs)) { const v = get(); if (v !== undefined) values[key] = v; }
    try {
      const r = await api('/settings', { method: 'PUT', body: { values } });
      if (Object.keys(r.errors).length) toast('Xatolar: ' + Object.entries(r.errors).map(([k, v]) => `${k}: ${v}`).join('; '), 8000);
      else toast(r.restart_needed ? 'Saqlandi. Kuchga kirishi uchun qayta ishga tushiring.' : 'Saqlandi', 4000);
      render();
    } catch (e) { fail(e); }
  });
  show(
    h('div', { class: 'row' }, h('h1', {}, 'Sozlamalar va kalitlar'), h('span', { class: 'spacer' }), save,
      h('button', { class: 'btn', onclick: restart }, 'Qayta ishga tushirish')),
    h('p', { class: 'muted' }, `Maxfiy kalitlar tizim saqlagichida turadi (${data.secret_backend}) va bu yerda hech qachon ` +
      "ko'rsatilmaydi. .env yoki muhit o'zgaruvchisi bilan berilgan qiymatlarni shu joyda o'zgartiring."),
    form);
};

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
  tools: 'tools', plugins: 'plugins', settings: 'settings', logs: 'logs' };

async function render() {
  const key = location.hash.replace(/^#\/?/, '').split('?')[0];
  const page = routes[key] || 'home';
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.page === page));
  $('#nav').classList.remove('open');
  if (page !== 'logs' && logSocket) { logSocket.close(); logSocket = null; }
  try { await pages[page](); } catch (e) { show(h('div', { class: 'card error' }, 'Yuklab bo\'lmadi: ' + e.message)); }
  view.focus({ preventScroll: true });
}

$('#menuBtn').addEventListener('click', () => $('#nav').classList.toggle('open'));
$('#logoutBtn').addEventListener('click', async () => { await api('/logout', { method: 'POST' }).catch(() => {}); location.href = '/login'; });
window.addEventListener('hashchange', render);
render();
refreshStatus();
setInterval(refreshStatus, 2500);
