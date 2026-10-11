// Ole-K2 Free Video: GUI logic (vanilla JS, talks to app/server.py)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const S = { info: null, slug: '', proj: null, jobTimer: null, llmTask: null, saveTimer: null, optTimer: null };

async function api(path, body, raw) {
  const opt = body === undefined ? {} : raw
    ? { method: 'POST', body, headers: { 'Content-Type': 'application/octet-stream' } }
    : { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } };
  const r = await fetch(path, opt);
  let j = {};
  try { j = await r.json(); } catch (e) { j = { error: 'Bad answer from the server' }; }
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, err) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast' + (err ? ' err' : '');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('hidden'), err ? 7000 : 3000);
}
const fail = e => toast(e.message || String(e), true);
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove();
  }
}
const fileUrl = p => p ? '/files/' + p : '';
const fmtLen = s => s >= 90 ? `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, '0')} s` : `${Math.round(s)} s`;
const fmtGB = g => (g || 0).toFixed(g < 10 ? 1 : 0) + ' GB';
const fitText = { fits: 'fits', tight: 'works, slower', too_big: 'too big', unknown: '?' };
const badge = f => `<span class="badge ${f}">${fitText[f] || f}</span>`;

// ------------------------------------------------------------------ tabs
function showTab(name) {
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  $$('.tab').forEach(t => t.classList.toggle('on', t.id === 'tab-' + name));
  try { localStorage.setItem('k2.tab', name); } catch (e) {}
  if (name === 'scenes') drawScenes();
  if (name === 'render') drawRender();
  if (name === 'library') loadLibrary();
  if (name === 'models') loadModels();
  if (name === 'settings') fillSettings();
}
$$('#tabs button').forEach(b => b.onclick = () => showTab(b.dataset.tab));
$$('#srcSeg button').forEach(b => b.onclick = () => {
  $$('#srcSeg button').forEach(x => x.classList.toggle('on', x === b));
  $$('.src').forEach(s => s.classList.toggle('hidden', s.id !== 'src-' + b.dataset.src));
});

// ------------------------------------------------------------------ status + info
async function pollStatus() {
  try {
    const s = await api('/api/status');
    $('#pComfy').classList.toggle('up', s.comfy);
    $('#pOllama').classList.toggle('up', s.ollama);
    $('#pComfy').title = s.comfy ? 'ComfyUI is running' : 'ComfyUI is not reachable';
    $('#pOllama').title = s.ollama ? 'Ollama is running' : 'Ollama is not reachable';
    S.tasks = s.tasks;
    if ($('#tab-models').classList.contains('on')) drawTasks(s.tasks);
  } catch (e) { $('#pComfy').classList.remove('up'); $('#pOllama').classList.remove('up'); }
}

function opts(sel, items, value, first) {
  const el = typeof sel === 'string' ? $(sel) : sel;
  el.innerHTML = (first ? `<option value="">${esc(first)}</option>` : '') +
    items.map(i => Array.isArray(i) ? `<option value="${esc(i[0])}">${esc(i[1])}</option>` : `<option>${esc(i)}</option>`).join('');
  if (value !== undefined) el.value = value;
}

async function loadInfo() {
  S.info = await api('/api/info');
  const I = S.info, hw = I.hardware;
  $('#pHw').textContent = `${hw.gpu ? hw.gpu.replace('NVIDIA GeForce ', '') + ' · ' : ''}${fmtGB(hw.vram_gb)} VRAM · ${fmtGB(hw.ram_gb)} RAM`;
  const llms = I.llms.map(m => [m.name, `${m.name} (${m.size_gb} GB, ${fitText[m.fit]})`]);
  opts('#llmSel', llms, I.settings.llm || I.llm_default, `auto (${I.llm_default || 'none installed'})`);
  const voices = [...I.voices, ...I.own_voices];
  $('#voiceList').innerHTML = voices.map(v => `<option value="${esc(v)}">`).join('');
  if (!I.ffmpeg[0]) toast('ffmpeg problem: ' + I.ffmpeg[1], true);
}

// ------------------------------------------------------------------ projects
async function loadProjects(select) {
  const r = await api('/api/projects');
  S.projects = r.projects;
  const sel = $('#projSel');
  sel.innerHTML = '<option value="">(new script, not saved)</option>' +
    r.projects.map(p => `<option value="${esc(p.slug)}">${esc(p.name)}${p.final ? '' : ' (not rendered)'}</option>`).join('');
  let want = select !== undefined ? select : S.slug;
  if (!want && r.projects.length && !S.newScript) want = r.projects[0].slug;   // open the newest
  sel.value = r.projects.some(p => p.slug === want) ? want : '';
  if (sel.value !== S.slug || select !== undefined) await openProject(sel.value);
}
$('#projSel').onchange = e => openProject(e.target.value);
$('#btnNewProj').onclick = () => {
  S.newScript = true;
  $('#projSel').value = ''; openProject('');
  $('#script').value = ''; $('#projName').value = ''; $('#outName').value = '';
  showTab('story');
};

async function openProject(slug) {
  S.slug = slug || '';
  try { localStorage.setItem('k2.slug', S.slug); } catch (e) {}
  S.proj = slug ? await api('/api/projects/' + slug) : null;
  $('#btnSaveCur').classList.toggle('hidden', !slug);
  if (S.proj) {
    $('#script').value = S.proj.script;
    $('#projName').value = S.proj.name;
    $('#outName').value = S.proj.output_name || '';
    showCheck(S.proj.check);
    if (S.proj.job.state === 'running') watchJob();
  }
  const active = $('.tab.on').id.replace('tab-', '');
  if (active === 'scenes') drawScenes();
  if (active === 'render') drawRender();
}
async function refreshProject() {
  if (S.slug) S.proj = await api('/api/projects/' + S.slug);
}

// ------------------------------------------------------------------ story tab
function showCheck(c) {
  $('#scriptStat').textContent = c ? `${c.scenes} scenes${c.chapters ? ' · ' + c.chapters + ' chapters' : ''} · about ${fmtLen(c.seconds)}`
    + (c.long_lines ? ` · ${c.long_lines} long lines get extra shots` : '') : '';
  $('#warnings').innerHTML = c ? (c.warnings.length ? c.warnings.map(w => `<li>${esc(w)}</li>`).join('')
    : (c.scenes ? '<li class="good">Looks good. Save it as a project, then render.</li>' : '')) : '';
}
async function checkScript(tidy) {
  const r = await api('/api/script/parse', { text: $('#script').value });
  if (tidy) $('#script').value = r.script;
  if (!$('#projName').value && r.parsed.title) $('#projName').placeholder = r.parsed.title;
  showCheck(r.check);
  return r;
}
$('#btnCheck').onclick = () => checkScript(false).catch(fail);
$('#btnTidy').onclick = () => checkScript(true).catch(fail);

function watchTask(id, { box, stage, text, onDone, onFail }) {
  box && box.classList.remove('hidden');
  const tick = async () => {
    let t;
    try { t = await api('/api/tasks/' + id); } catch (e) { onFail && onFail(e); return; }
    if (stage) stage.textContent = t.stage || t.label || 'Working';
    if (text) { text.textContent = t.text || ''; text.scrollTop = text.scrollHeight; }
    if (t.state === 'running') return setTimeout(tick, 700);
    if (t.state === 'done') onDone && onDone(t.result);
    else { onFail ? onFail(new Error(t.error || t.state)) : fail(new Error(t.error || t.state)); }
  };
  tick();
}
function llmRun(path, body, label) {
  $$('#btnWrite,#btnConvert,#btnImprove').forEach(b => b.disabled = true);
  $('#llmStage').textContent = label;
  $('#llmText').textContent = '';
  // the live box sits in the "local AI" card; show that card so the user sees progress
  $$('#srcSeg button')[0].click();
  return api(path, body).then(r => {
    S.llmTask = r.task;
    watchTask(r.task, {
      box: $('#llmBox'), stage: $('#llmStage'), text: $('#llmText'),
      onDone: res => {
        $$('#btnWrite,#btnConvert,#btnImprove').forEach(b => b.disabled = false);
        $('#script').value = res.script; showCheck(res.check);
        $('#llmStage').textContent = `Done with ${res.model}. The script is in the editor.`;
        toast('Script ready. Check it, then save it as a project.');
      },
      onFail: e => { $$('#btnWrite,#btnConvert,#btnImprove').forEach(b => b.disabled = false); $('#llmStage').textContent = 'Stopped'; fail(e); }
    });
  }).catch(e => { $$('#btnWrite,#btnConvert,#btnImprove').forEach(b => b.disabled = false); fail(e); });
}
$('#btnWrite').onclick = () => llmRun('/api/llm/write', {
  idea: $('#idea').value, genre: $('#genre').value, seconds: +$('#length').value,
  narration: $('#narration').value, extra: $('#extra').value, model: $('#llmSel').value
}, 'Starting the local AI');
$('#btnConvert').onclick = () => {
  if (!$('#script').value.trim()) return toast('Paste something into the Script box first', true);
  llmRun('/api/llm/convert', { text: $('#script').value, model: $('#llmSel').value }, 'Converting');
};
$('#btnImprove').onclick = () => {
  if (!$('#script').value.trim()) return toast('There is no script to improve yet', true);
  llmRun('/api/llm/improve', { text: $('#script').value, instruction: $('#improveText').value, model: $('#llmSel').value }, 'Improving');
};
$('#btnLlmStop').onclick = () => S.llmTask && api(`/api/tasks/${S.llmTask}/cancel`, {}).catch(fail);

$('#btnCopyPrompt').onclick = async () => {
  try {
    const r = await api('/api/script/template', { idea: $('#ideaWeb').value, genre: $('#genreWeb').value, seconds: +$('#lengthWeb').value });
    await copyText(r.text);
    $('#copyHint').textContent = 'Copied. Paste it into ChatGPT / DeepSeek.';
  } catch (e) { fail(e); }
};
async function templates() { S.tpl = S.tpl || await api('/api/templates'); return S.tpl; }
$('#btnTplBlank').onclick = async () => { $('#script').value = (await templates()).blank; showCheck(null); };
$('#btnTplExample').onclick = async () => { $('#script').value = (await templates()).example; checkScript(false).catch(fail); };
$('#btnTplCopy').onclick = async () => { await copyText((await templates()).blank); toast('Template copied'); };

$('#btnSaveNew').onclick = async () => {
  try {
    const r0 = await checkScript(false);
    if (!r0.check.scenes) return toast('No scenes found yet. See the Template tab for the format.', true);
    const r = await api('/api/projects', { text: $('#script').value, name: $('#projName').value || r0.parsed.title, output_name: $('#outName').value });
    S.newScript = false;
    await loadProjects(r.slug);
    toast('Project saved. Check the scenes, then render.');
    showTab('scenes');
  } catch (e) { fail(e); }
};
$('#btnSaveRender').onclick = async () => {
  try {
    const r0 = await checkScript(false);
    if (!r0.check.scenes) return toast('No scenes found yet. See the Template tab for the format.', true);
    let slug = S.slug;
    if (slug) {
      await api(`/api/projects/${slug}/script`, { text: $('#script').value });
      slug = (await api(`/api/projects/${slug}/meta`, { name: $('#projName').value, output_name: $('#outName').value })).slug;
    } else {
      slug = (await api('/api/projects', { text: $('#script').value, name: $('#projName').value || r0.parsed.title, output_name: $('#outName').value })).slug;
    }
    S.newScript = false;
    await loadProjects(slug);
    await api(`/api/projects/${slug}/render`, {});
    showTab('render');
    watchJob();
    toast('Rendering started. You can follow it here.');
  } catch (e) { fail(e); }
};
$('#btnSaveCur').onclick = async () => {
  try {
    await api(`/api/projects/${S.slug}/script`, { text: $('#script').value });
    const r = await api(`/api/projects/${S.slug}/meta`, { name: $('#projName').value, output_name: $('#outName').value });
    await loadProjects(r.slug);
    $('#saveHint').textContent = 'Saved';
  } catch (e) { fail(e); }
};

// ------------------------------------------------------------------ scenes tab
function overridesArr() {
  const n = S.proj.parsed.scenes.length, o = S.proj.meta.scenes || {};
  return Array.from({ length: n }, (_, i) => ({ ...(o[String(i + 1)] || {}) }));
}
function drawPickers() {
  const list = S.projects || [];
  $$('.projpick').forEach(el => {
    el.innerHTML = list.length ? list.map(p => `<button data-open="${esc(p.slug)}">${esc(p.name)}${p.final ? '' : ' (not rendered)'}</button>`).join(' ')
      : '<span class="muted">No projects yet.</span>';
    $$('[data-open]', el).forEach(b => b.onclick = async () => {
      $('#projSel').value = b.dataset.open; await openProject(b.dataset.open);
    });
  });
}
function drawScenes() {
  const has = !!S.proj;
  if (!has) drawPickers();
  $('#scenesEmpty').classList.toggle('hidden', has);
  $('#scenesWrap').classList.toggle('hidden', !has);
  if (!has) return;
  const P = S.proj, sc = P.parsed;
  $('#sbTitle').textContent = P.name;
  const est = P.estimate || {};
  $('#sbStat').textContent = `${sc.scenes.length} scenes${sc.chapters.length ? ' in ' + sc.chapters.length + ' chapters' : ''}`
    + ` · about ${fmtLen(P.check.seconds)}` + (est.shots && est.shots !== est.scenes ? ` · ${est.shots} shots after automatic splitting` : '');
  $$('[data-h]').forEach(el => {
    const k = el.dataset.h;
    el.value = k === 'characters' ? sc.characters.map(c => `${c.name} = ${c.look}${c.voice ? ' | voice: ' + c.voice : ''}`).join('\n')
      : k === 'hashtags' ? sc.hashtags.join(' ') : sc[k] || '';
    el.oninput = queueScriptSave;
  });
  const ov = overridesArr();
  const engines = Object.entries(S.info.engines);
  $('#sceneCards').innerHTML = sc.scenes.map((s, i) => {
    const f = P.files[i] || {}, o = ov[i];
    const vid = f.preview || f.clip;
    const thumb = f.image ? `<img src="${fileUrl(f.image)}" loading="lazy">${vid ? `<video src="${fileUrl(vid)}" muted loop playsinline preload="none" class="hov"></video>` : ''}`
      : vid ? `<video src="${fileUrl(vid)}" muted loop playsinline preload="none"></video>` : 'not made yet';
    const ch = s.chapter >= 0 && (i === 0 || sc.scenes[i - 1].chapter !== s.chapter)
      ? `<div class="chap">Chapter ${s.chapter + 1}${sc.chapters[s.chapter].title ? ': ' + esc(sc.chapters[s.chapter].title) : ''}</div>` : '';
    const voice = s.speaker ? `${s.speaker}: ${s.voice}` : s.voice;
    return `${ch}<div class="scene" data-i="${i}" data-ch="${s.chapter}">
      <div class="side">
        <div class="thumb">${thumb}</div>
        <select data-ov="engine" title="Picture engine for this scene"><option value="">engine: default</option>
          ${engines.map(([k, v]) => `<option value="${k}" ${o.engine === k ? 'selected' : ''}>${esc(v.split(' (')[0])}</option>`).join('')}</select>
        <button data-act="preview" title="Make just this scene">Preview</button>
        <button data-act="seed" title="Another picture: new random seed, then preview">New picture</button>
        <label class="filebtn" title="Use your own clip or picture">Own clip<input type="file" data-act="upload" accept="video/*,image/*"></label>
      </div>
      <div>
        <div class="head"><span class="num">Scene ${i + 1}</span>
          <span class="muted small">${P.estimates[i]} s${o.media ? ' · own clip' : ''}${o.seed ? ' · seed ' + o.seed : ''}</span>
          <span><button class="ghost small" data-act="up" title="Move up">Up</button><button class="ghost small danger" data-act="del">Delete</button></span></div>
        <label>Visual<textarea data-f="visual" rows="3">${esc(s.visual)}</textarea></label>
        <div class="grid2"><label>Motion<input data-f="motion" value="${esc(s.motion)}"></label>
          <label>Seconds (0 = fit voice)<input data-f="seconds" type="number" step="0.5" min="0" value="${s.seconds || 0}"></label></div>
        <label>Voice (Name: line for a character)<textarea data-f="voice" rows="2">${esc(voice)}</textarea></label>
        <div class="grid2"><label>On-screen text<input data-f="text" value="${esc(s.text)}"></label>
          <label>Sound<input data-f="sound" value="${esc(s.sound)}"></label></div>
      </div></div>`;
  }).join('');
  $$('#sceneCards .scene').forEach(card => {
    const i = +card.dataset.i;
    $$('[data-f]', card).forEach(el => el.oninput = queueScriptSave);
    $('[data-ov="engine"]', card).onchange = e => setOverride(i, { engine: e.target.value });
    $('[data-act="preview"]', card).onclick = () => previewScene(i + 1);
    $('[data-act="seed"]', card).onclick = async () => { await setOverride(i, { seed: 1 + Math.floor(Math.random() * 1e6) }); previewScene(i + 1); };
    $('[data-act="upload"]', card).onchange = e => uploadMedia(i + 1, e.target.files[0]);
    $('[data-act="up"]', card).onclick = () => moveScene(i, -1);
    $('[data-act="del"]', card).onclick = () => { if (confirm(`Delete scene ${i + 1}?`)) moveScene(i, 0); };
    const v = $('video', card);
    if (v) {
      card.onmouseenter = () => { v.classList.add('on'); v.play().catch(() => {}); };
      card.onmouseleave = () => { v.pause(); v.classList.remove('on'); };
    }
  });
}
function collectParsed() {
  const sc = { scenes: [] };
  $$('[data-h]').forEach(el => {
    const k = el.dataset.h;
    if (k === 'characters') sc.characters = el.value.split('\n').filter(l => l.trim()).map(l => {
      const m = l.match(/^(.*?)\s*=\s*(.*?)(?:\|\s*voice\s*[:=]\s*(\S+))?\s*$/i) || [null, l, '', ''];
      return { name: (m[1] || '').trim(), look: (m[2] || '').trim(), voice: (m[3] || '').trim() };
    });
    else if (k === 'hashtags') sc.hashtags = el.value.split(/\s+/).filter(Boolean);
    else sc[k] = el.value;
  });
  sc.chapters = (S.proj && S.proj.parsed.chapters) || [];
  $$('#sceneCards .scene').forEach(card => {
    const s = { speaker: '', chapter: +card.dataset.ch };
    $$('[data-f]', card).forEach(el => s[el.dataset.f] = el.dataset.f === 'seconds' ? +el.value || 0 : el.value);
    sc.scenes.push(s);
  });
  return sc;
}
function queueScriptSave() {
  $('#sbSaved').textContent = 'editing...';
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(saveFromCards, 900);
}
async function saveFromCards(redraw) {
  const r = await api('/api/script/format', { parsed: collectParsed() });
  S.proj = await api(`/api/projects/${S.slug}/script`, { text: r.script });
  $('#script').value = S.proj.script;
  $('#sbSaved').textContent = 'saved';
  $('#sbStat').textContent = `${S.proj.parsed.scenes.length} scenes · about ${fmtLen(S.proj.check.seconds)}`;
  if (redraw) drawScenes();
}
async function setOverride(i, patch) {
  const ov = overridesArr();
  Object.assign(ov[i], patch);
  Object.keys(ov[i]).forEach(k => (ov[i][k] === '' || ov[i][k] == null) && delete ov[i][k]);
  const scenes = {}; ov.forEach((o, k) => { if (Object.keys(o).length) scenes[String(k + 1)] = o; });
  await api(`/api/projects/${S.slug}/meta`, { scenes });
  S.proj.meta.scenes = scenes;
}
async function moveScene(i, dir) {
  const sc = collectParsed(), ov = overridesArr();
  if (dir === 0) { sc.scenes.splice(i, 1); ov.splice(i, 1); }
  else if (i + dir >= 0) { [sc.scenes[i], sc.scenes[i + dir]] = [sc.scenes[i + dir], sc.scenes[i]]; [ov[i], ov[i + dir]] = [ov[i + dir], ov[i]]; }
  const scenes = {}; ov.forEach((o, k) => { if (Object.keys(o).length) scenes[String(k + 1)] = o; });
  await api(`/api/projects/${S.slug}/meta`, { scenes });
  const r = await api('/api/script/format', { parsed: sc });
  S.proj = await api(`/api/projects/${S.slug}/script`, { text: r.script });
  $('#script').value = S.proj.script; drawScenes();
}
$('#btnAddScene').onclick = async () => {
  const sc = collectParsed();
  const lastCh = sc.scenes.length ? sc.scenes[sc.scenes.length - 1].chapter : -1;
  sc.scenes.push({ visual: 'describe the picture', motion: 'slow push in', voice: '', text: '', sound: '', seconds: 4, speaker: '', chapter: lastCh });
  const r = await api('/api/script/format', { parsed: sc });
  S.proj = await api(`/api/projects/${S.slug}/script`, { text: r.script });
  $('#script').value = S.proj.script; drawScenes();
};
async function uploadMedia(scene, file) {
  if (!file) return;
  try {
    toast('Uploading...');
    await api(`/api/projects/${S.slug}/media?scene=${scene}&name=${encodeURIComponent(file.name)}`, file, true);
    await refreshProject(); drawScenes(); toast(`Scene ${scene} now uses your file`);
  } catch (e) { fail(e); }
}
async function previewScene(n) {
  try {
    clearTimeout(S.saveTimer);
    await saveFromCards();
    await api(`/api/projects/${S.slug}/render`, { scene: n });
    toast(`Making scene ${n}. Progress is on the Render tab.`);
    watchJob(() => { refreshProject().then(drawScenes); });
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ render tab
function outPreview() {
  const v = $('#rOut').value.trim();
  const d = new Date(), p = n => String(n).padStart(2, '0');
  $('#rOutPreview').textContent = (v ? v.replace(/\.mp4$/i, '') : `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}_${S.slug}`) + '.mp4';
}
function drawRender() {
  const has = !!S.proj;
  if (!has) drawPickers();
  $('#renderEmpty').classList.toggle('hidden', has);
  $('#renderWrap').classList.toggle('hidden', !has);
  if (!has) return;
  const I = S.info, P = S.proj, o = P.meta.options || {};
  $('#rName').value = P.name; $('#rOut').value = P.output_name || ''; outPreview();
  const sets = {
    engine: [Object.entries(I.engines), 'default (' + (I.engines[I.settings.engine] || '').split(' (')[0] + ')'],
    quality: [I.quality_levels, `default (${I.settings.quality || I.recommended.quality})`],
    voice_override: [[...I.voices, ...I.own_voices], 'from the script / Settings'],
    voice_language: [I.languages, 'default (' + I.settings.voice_language + ')'],
    music_file: [I.music_files, I.music_files.length ? 'random' : '(music folder is empty)'],
  };
  for (const [k, [items, first]] of Object.entries(sets)) opts(`[data-o="${k}"]`, items, o[k] ?? '', first);
  $$('[data-o]').forEach(el => {
    if (!(el.dataset.o in sets)) el.value = o[el.dataset.o] === undefined ? '' : String(o[el.dataset.o]);
    el.onchange = saveOptions;
  });
  showJob(P.job);
  showResult();
  showEstimate();
}
function showEstimate() {
  const e = S.proj && S.proj.estimate;
  $('#estimate').innerHTML = e && e.shots ? `About <b>${fmtLen(e.seconds)}</b> of video: ${e.scenes} scenes become <b>${e.shots} shots</b>.
    Rendering takes roughly <b>${e.minutes < 90 ? Math.round(e.minutes) + ' min' : (e.minutes / 60).toFixed(1) + ' hours'}</b> on this PC
    (a rough guess). You can close the browser; the render keeps going while the program window is open. Cancel and Render again continues where it stopped.` : '';
}
async function saveOptions() {
  const options = {};
  $$('[data-o]').forEach(el => {
    let v = el.value; if (v === '') return;
    if (v === 'true' || v === 'false') v = v === 'true';
    else if (['voice_speed', 'voice_emotion'].includes(el.dataset.o)) v = +v;
    options[el.dataset.o] = v;
  });
  try { await api(`/api/projects/${S.slug}/meta`, { options }); S.proj.meta.options = options; } catch (e) { fail(e); }
}
$('#rOut').oninput = outPreview;
async function saveNames() {
  try {
    const r = await api(`/api/projects/${S.slug}/meta`, { name: $('#rName').value, output_name: $('#rOut').value });
    if (r.slug !== S.slug) { await loadProjects(r.slug); } else { await refreshProject(); }
  } catch (e) { fail(e); }
}
$('#rName').onchange = saveNames; $('#rOut').onchange = saveNames;

$('#btnRender').onclick = async () => {
  try {
    await saveNames(); await saveOptions();
    await api(`/api/projects/${S.slug}/render`, {});
    watchJob();
  } catch (e) { fail(e); }
};
$('#btnCancel').onclick = () => S.slug && api(`/api/projects/${S.slug}/cancel`, {}).then(() => refreshProject().then(() => showJob(S.proj.job))).catch(fail);

function showJob(j) {
  j = j || { state: 'idle' };
  $('#pBar').style.width = (j.pct || 0) + '%';
  const names = { idle: 'Ready', running: 'Rendering', done: 'Finished', error: 'Error', cancelled: 'Cancelled', stopped: 'Stopped' };
  $('#jobStage').textContent = names[j.state] || j.state;
  $('#jobMsg').textContent = j.state === 'running' ? `${j.message} · ${j.pct || 0}% · ${Math.round((Date.now() / 1000 - j.started) || 0)} s`
    : j.state === 'done' && j.preview ? 'Scene preview is ready (see the Scenes tab)'
    : j.message || '';
  $('#jobWarn').innerHTML = (j.warnings || []).map(w => `<li>${esc(w)}</li>`).join('') + (j.error ? `<li>${esc(j.error)}</li>` : '');
  $('#jobLog').textContent = j.log || '';
  $('#btnRender').disabled = j.state === 'running';
  $('#renderHint').textContent = j.state === 'running' ? 'You can switch tabs; it keeps going.' : '';
}
function showResult() {
  const P = S.proj;
  $('#resultCard').classList.toggle('hidden', !P.final);
  if (!P.final) return;
  const v = $('#finalVid'), src = fileUrl(P.final) + '?t=' + encodeURIComponent(P.rendered || '');
  if (v.getAttribute('src') !== src) v.src = src;
  $('#coverImg').src = fileUrl(P.cover) + '?t=' + encodeURIComponent(P.rendered || '');
  $('#capText').value = P.caption;
  const last = (P.exports || []).slice(-1)[0];
  $('#exportInfo').innerHTML = last ? `File for TikTok: <code>exports/${esc(last)}</code> · ${P.duration} s` : '';
}
function watchJob(onDone) {
  clearTimeout(S.jobTimer);
  const slug = S.slug;
  const tick = async () => {
    if (S.slug !== slug) return;
    try { S.proj = await api('/api/projects/' + slug); } catch (e) { return; }
    showJob(S.proj.job);
    if (S.proj.job.state === 'running') { S.jobTimer = setTimeout(tick, 1500); return; }
    if (S.proj.job.state === 'done') {
      if (S.proj.job.final) { toast('Video finished'); showResult(); loadProjects(); }
      onDone && onDone();
    } else if (S.proj.job.state === 'error') fail(new Error(S.proj.job.error || 'Render failed'));
  };
  tick();
}
$('#btnCopyCap').onclick = () => copyText($('#capText').value).then(() => toast('Caption copied'));
const openExports = () => api('/api/open', { what: 'exports' }).catch(fail);
$('#btnOpenExports').onclick = openExports; $('#btnOpenExports2').onclick = openExports;

// ------------------------------------------------------------------ library
async function loadLibrary() {
  const r = await api('/api/projects');
  const done = r.projects.filter(p => p.final);
  $('#libGrid').innerHTML = done.length ? done.map(p => `
    <div class="libcard ${p.posted ? 'posted' : ''}" data-slug="${esc(p.slug)}">
      <video src="${fileUrl(p.final)}" poster="${fileUrl(p.cover)}" preload="none" controls playsinline></video>
      <div class="body">
        <b>${esc(p.name)}</b>
        <span class="muted small">${p.duration} s · ${esc((p.rendered || '').replace('T', ' ').slice(0, 16))}</span>
        <div class="file">exports/${esc((p.exports || []).slice(-1)[0] || '')}</div>
        <div class="cap">${esc(p.caption)}</div>
        <div class="row">
          <button class="small" data-act="cap">Copy caption</button>
          <button class="small ghost" data-act="open">Open</button>
          <label class="check"><input type="checkbox" data-act="posted" ${p.posted ? 'checked' : ''}> posted ${esc(p.posted || '')}</label>
        </div>
      </div></div>`).join('') : '<div class="empty">No finished videos yet.</div>';
  $$('.libcard').forEach(c => {
    const p = done.find(x => x.slug === c.dataset.slug);
    $('[data-act="cap"]', c).onclick = () => copyText(p.caption).then(() => toast('Caption copied'));
    $('[data-act="open"]', c).onclick = async () => { $('#projSel').value = p.slug; await openProject(p.slug); showTab('render'); };
    $('[data-act="posted"]', c).onchange = e => api(`/api/projects/${p.slug}/posted`, { posted: e.target.checked }).then(loadLibrary).catch(fail);
  });
}

// ------------------------------------------------------------------ models
async function loadModels() {
  let M;
  try { M = await api('/api/models'); } catch (e) { return fail(e); }
  const hw = M.hardware;
  $('#mHw').innerHTML = `<b>${esc(hw.gpu || 'GPU')}</b> · ${fmtGB(hw.vram_gb)} graphics memory · ${fmtGB(hw.ram_gb)} RAM
    <span class="muted small"> (change in Settings). "fits" = runs fully on the graphics card, "works, slower" = part runs on the CPU.</span>`;
  $('#llmTbl').innerHTML = '<tr><th>Installed LLM</th><th>Size</th><th>Fit</th><th></th></tr>' + (M.llms.length ? M.llms.map(m => `
    <tr><td>${esc(m.name)} <span class="muted small">${esc(m.params)} ${esc(m.quant)}</span></td><td>${m.size_gb} GB</td><td>${badge(m.fit)}</td>
    <td><button class="small" data-use="${esc(m.name)}">Use for stories</button> <button class="small ghost danger" data-del="${esc(m.name)}">Delete</button></td></tr>`).join('')
    : `<tr><td colspan="4" class="muted">${M.ollama ? 'No LLM installed yet. Pull one below.' : 'Ollama is not running.'}</td></tr>`);
  $('#llmSugg').innerHTML = '<tr><th>Suggested</th><th>Size</th><th>Fit</th><th>Note</th><th></th></tr>' + M.llm_suggestions.map(m => `
    <tr><td>${esc(m.name)}</td><td>~${m.size_gb} GB</td><td>${badge(m.fit)}</td><td class="note">${esc(m.note)}</td>
    <td>${m.installed ? '<span class="badge have">installed</span>' : `<button class="small" data-pull="${esc(m.name)}">Pull</button>`}</td></tr>`).join('');
  $('#mDir').textContent = M.comfy_models_dir ? `Saved into ${M.comfy_models_dir}` : 'No local ComfyUI folder found (set it in Settings).';
  $('#catTbl').innerHTML = '<tr><th>Model</th><th>For</th><th>Size</th><th>Needs</th><th>Fit</th><th></th></tr>' + M.catalog.map(e => `
    <tr data-id="${esc(e.id)}"><td>${esc(e.label)}<div class="muted small">${esc(e.note)}</div></td><td>${esc(e.use)}</td>
    <td>${e.size_gb} GB</td><td class="note">${e.min_vram ? e.min_vram + ' GB VRAM' : ''}${e.min_ram ? ', ' + e.min_ram + ' GB RAM' : ''}</td><td>${badge(e.fit)}</td>
    <td class="act">${e.installed ? '<span class="badge have">installed</span>' : `<button class="small" data-dl="${esc(e.id)}">Download</button>`}</td></tr>`).join('');
  $$('[data-use]').forEach(b => b.onclick = () => api('/api/settings', { llm: b.dataset.use }).then(() => { toast('Story LLM set'); loadInfo(); }).catch(fail));
  $$('[data-del]').forEach(b => b.onclick = () => confirm(`Delete ${b.dataset.del} from Ollama?`) &&
    api('/api/ollama/delete', { name: b.dataset.del }).then(loadModels).catch(fail));
  $$('[data-pull]').forEach(b => b.onclick = () => pull(b.dataset.pull));
  $$('[data-dl]').forEach(b => b.onclick = () => {
    const e = M.catalog.find(x => x.id === b.dataset.dl);
    if (e.fit === 'too_big' && !confirm(`${e.label} is probably too big for this PC. Download anyway?`)) return;
    if (!confirm(`Download ${e.label} (${e.size_gb} GB) from Hugging Face?`)) return;
    api('/api/models/download', { id: e.id }).then(() => { toast('Download started'); pollStatus(); }).catch(fail);
  });
  drawTasks(S.tasks || []);
}
function pull(name) {
  if (!name) return;
  if (!confirm(`Download ${name} with Ollama?`)) return;
  api('/api/ollama/pull', { name }).then(() => { toast('Pull started'); pollStatus(); }).catch(fail);
}
$('#btnPull').onclick = () => pull($('#pullName').value.trim());
function drawTasks(tasks) {
  const dl = (tasks || []).filter(t => t.kind === 'download' || t.kind === 'pull');
  $('#dlTasks').innerHTML = dl.length ? '<div class="card"><h3>Downloads</h3><table class="tbl">' + dl.map(t => {
    const pct = t.total ? Math.round(100 * t.done / t.total) : 0;
    return `<tr><td>${esc(t.label.replaceAll('__', '/'))}</td><td style="width:40%"><div class="bar"><div style="width:${pct}%"></div></div></td>
      <td class="note">${esc(t.stage)} ${t.total ? pct + '% of ' + (t.total / 1e9).toFixed(1) + ' GB' : ''} ${t.speed ? '· ' + (t.speed / 1e6).toFixed(1) + ' MB/s' : ''}</td>
      <td><button class="small ghost" data-cancel="${t.id}">Stop</button></td></tr>`;
  }).join('') + '</table></div>' : '';
  $$('[data-cancel]').forEach(b => b.onclick = () => api(`/api/tasks/${b.dataset.cancel}/cancel`, {}).catch(fail));
  if (S._hadDl && !dl.length) loadModels();
  S._hadDl = dl.length > 0;
}
$('#btnHfSearch').onclick = async () => {
  try {
    const r = await api('/api/hf/search?q=' + encodeURIComponent($('#hfQ').value));
    $('#hfRes').innerHTML = '<table class="tbl">' + r.results.map(x => `<tr><td>${esc(x.repo)}</td><td class="note">${x.downloads} downloads</td>
      <td><button class="small" data-files="${esc(x.repo)}">Show files</button></td></tr><tr class="hidden" data-for="${esc(x.repo)}"><td colspan="3"></td></tr>`).join('') + '</table>';
    $$('[data-files]').forEach(b => b.onclick = async () => {
      const row = $(`[data-for="${CSS.escape(b.dataset.files)}"]`);
      const f = await api('/api/hf/files?repo=' + encodeURIComponent(b.dataset.files));
      row.classList.remove('hidden');
      row.firstElementChild.innerHTML = '<table class="tbl">' + f.files.map(x => `<tr><td>${esc(x.quant)}</td><td>${x.size_gb} GB</td><td>${badge(x.fit)}</td>
        <td><button class="small" data-pull="${esc(x.ollama)}">Pull with Ollama</button></td></tr>`).join('') + '</table>';
      $$('[data-pull]', row).forEach(p => p.onclick = () => pull(p.dataset.pull));
    });
  } catch (e) { fail(e); }
};
$('#btnCustomDl').onclick = () => {
  if (!confirm('Download this file from Hugging Face?')) return;
  api('/api/models/download', { url: $('#cUrl').value, folder: $('#cFolder').value, filename: $('#cName').value })
    .then(() => { toast('Download started'); pollStatus(); }).catch(fail);
};

// ------------------------------------------------------------------ settings
function fillSettings() {
  const I = S.info, s = I.settings, hw = I.hardware, rec = I.recommended;
  opts('[data-s="llm"]', I.llms.map(m => [m.name, `${m.name} (${m.size_gb} GB, ${fitText[m.fit]})`]), s.llm, `auto (${I.llm_default || 'none'})`);
  opts('[data-s="engine"]', Object.entries(I.engines), s.engine);
  opts('[data-s="image_model"]', I.image_models, s.image_model, 'auto');
  opts('[data-s="video_model"]', I.video_models, s.video_model, 'auto');
  opts('[data-s="quality"]', I.quality_levels, s.quality, `auto (${rec.quality})`);
  opts('[data-s="voice"]', [...I.voices, ...I.own_voices], s.voice);
  opts('[data-s="voice_language"]', I.languages, s.voice_language);
  opts('[data-s="music_file"]', I.music_files, s.music_file, 'random');
  $$('[data-s]').forEach(el => {
    const v = s[el.dataset.s];
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.tagName !== 'SELECT' || ['transition', 'music', 'sfx_engine', 'look'].includes(el.dataset.s)) el.value = v ?? '';
  });
  $('#detected').textContent = `Detected: ${hw.gpu || 'no GPU found'} · ${fmtGB(hw.detected_vram_gb)} VRAM · ${fmtGB(hw.detected_ram_gb)} RAM (${hw.source || 'local'})`;
  $('#recText').innerHTML = `With <b>${fmtGB(hw.vram_gb)}</b> graphics memory and <b>${fmtGB(hw.ram_gb)}</b> RAM: quality <b>${rec.quality}</b>,
    engine <b>${esc((I.engines[rec.engine] || '').split(' (')[0])}</b>, story LLMs up to about <b>${rec.llm_max_gb} GB</b> run fully on the card
    (up to ~${rec.llm_cpu_max_gb} GB with help from RAM, slower). Video clips up to ${(rec.frames_cap / 24).toFixed(1)} s per scene before slowing/holding.`;
  $('#pvText').placeholder = 'Hello! This is how I sound in your next video.';
}
$('#btnDetect').onclick = async () => {
  try {
    const d = await api('/api/hardware/detect');
    $('[data-s="vram_gb"]').value = d.vram_gb; $('[data-s="ram_gb"]').value = d.ram_gb;
    $('#detected').textContent = `Detected: ${d.gpu || 'no GPU'} · ${fmtGB(d.vram_gb)} VRAM · ${fmtGB(d.ram_gb)} RAM. Click Save settings.`;
  } catch (e) { fail(e); }
};
$('#btnSaveSettings').onclick = async () => {
  const body = {};
  $$('[data-s]').forEach(el => {
    const k = el.dataset.s;
    body[k] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? +el.value : el.value;
  });
  try { await api('/api/settings', body); await loadInfo(); fillSettings(); $('#setHint').textContent = 'Saved'; toast('Settings saved'); }
  catch (e) { fail(e); }
};
$('#btnPreview').onclick = async () => {
  try {
    const r = await api('/api/voice/preview', {
      voice: $('[data-s="voice"]').value, text: $('#pvText').value, language: $('[data-s="voice_language"]').value,
      speed: +$('[data-s="voice_speed"]').value || 1, emotion: +$('[data-s="voice_emotion"]').value || 0.5
    });
    $('#btnPreview').disabled = true; $('#btnPreview').textContent = 'Speaking...';
    watchTask(r.task, {
      onDone: res => { const a = $('#pvAudio'); a.classList.remove('hidden'); a.src = res.url; a.play(); reset(); },
      onFail: e => { reset(); fail(e); }
    });
    function reset() { $('#btnPreview').disabled = false; $('#btnPreview').textContent = 'Hear voice'; }
  } catch (e) { fail(e); }
};
async function uploadVoice(blob, filename) {
  const name = ($('#ownName').value || 'my-voice').trim();
  try {
    const r = await api(`/api/voices/upload?name=${encodeURIComponent(name)}&file=${encodeURIComponent(filename)}`, blob, true);
    await loadInfo(); fillSettings(); $('[data-s="voice"]').value = r.voice;
    $('#recHint').textContent = `Saved as voice "${r.voice}". Click Save settings to make it the default.`;
  } catch (e) { fail(e); }
}
$('#btnRec').onclick = async () => {
  if (S.rec) { S.rec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [], rec = new MediaRecorder(stream);
    rec.ondataavailable = e => chunks.push(e.data);
    rec.onstop = () => {
      stream.getTracks().forEach(t => t.stop()); S.rec = null; clearTimeout(S.recT);
      $('#btnRec').textContent = 'Record';
      uploadVoice(new Blob(chunks, { type: rec.mimeType }), 'rec.webm');
    };
    rec.start(); S.rec = rec; $('#btnRec').textContent = 'Stop recording';
    $('#recHint').textContent = 'Recording... read something aloud for 10-20 s';
    S.recT = setTimeout(() => rec.state === 'recording' && rec.stop(), 30000);
  } catch (e) { fail(new Error('No microphone access: ' + e.message)); }
};
$('#ownFile').onchange = e => e.target.files[0] && uploadVoice(e.target.files[0], e.target.files[0].name);
$('#musicFile').onchange = e => {
  const f = e.target.files[0]; if (!f) return;
  api('/api/music/upload?name=' + encodeURIComponent(f.name), f, true)
    .then(() => { $('#addHint').textContent = 'Music added'; return loadInfo().then(fillSettings); }).catch(fail);
};
$('#sfxFile').onchange = e => {
  const f = e.target.files[0]; if (!f) return;
  api('/api/sfx/upload?name=' + encodeURIComponent(f.name), f, true)
    .then(() => { $('#addHint').textContent = 'Sound added (its file name words are matched to SOUND lines)'; }).catch(fail);
};

// ------------------------------------------------------------------ start
(async () => {
  try { await loadInfo(); } catch (e) { fail(e); }
  let slug = '', tab = 'story';
  try { slug = localStorage.getItem('k2.slug') || ''; tab = localStorage.getItem('k2.tab') || 'story'; } catch (e) {}
  try { await loadProjects(slug); } catch (e) { fail(e); }
  showTab(tab);
  pollStatus(); setInterval(pollStatus, 4000);
})();
