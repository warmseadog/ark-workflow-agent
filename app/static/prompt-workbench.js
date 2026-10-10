(() => {
  'use strict';
  const $ = id => document.getElementById(id), edits = new Map(), resets = new Set(), opened = new Set();
  let data, loading = false, saving = false, timer, sequence = 0, manualBody = false;
  const node = (tag, text, cls) => { const el = document.createElement(tag); if (text != null) el.textContent = text; if (cls) el.className = cls; return el; };
  function note(id, message, error = false) { $(id).textContent = message; $(id).dataset.error = String(error); }
  async function request(path = '', method = 'GET', body) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/admin/prompt-workbench' + path, {method, cache:'no-store', signal:controller.signal,
        headers: body ? {'Content-Type':'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
      const value = await response.json();
      if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '提示词配置请求失败。');
      return value;
    } catch (error) { if (error.name === 'AbortError') throw new Error('请求超时，编辑内容已保留；请重新读取配置核对保存结果。'); throw error; }
    finally { clearTimeout(timeout); }
  }
  function content(item) { return edits.has(item.id) ? edits.get(item.id) : item.content; }
  function textArea(label, value, readonly = false) {
    const wrap = node('label', label), input = node('textarea'); input.value = value; input.readOnly = readonly; input.rows = 7; input.maxLength = 30000; wrap.append(input); return [wrap, input];
  }
  function renderItems() {
    const group = $('pw-group').value, search = $('pw-search').value.trim().toLowerCase(), list = $('pw-items'); list.replaceChildren();
    const visible = data.items.filter(item => (!group || item.group === group) && (!search || (item.label + item.content + item.default).toLowerCase().includes(search)));
    $('pw-count').textContent = `${visible.length} / ${data.items.length} 段`;
    for (const item of visible) {
      const details = node('details', null, 'pw-item'); details.dataset.promptId = item.id; details.open = opened.has(item.id);
      const summary = node('summary', item.label), badge = node('span', edits.has(item.id) ? '未保存' : '', 'pw-unsaved'); summary.append(badge);
      details.addEventListener('toggle', () => { if (details.open) opened.add(item.id); else opened.delete(item.id); });
      details.append(summary, node('p', item.group + ' · ' + (item.customized ? '已自定义' : '使用默认内容'), 'pw-meta'), node('p', item.condition, 'hint'));
      const [label, editor] = textArea('当前内容（可编辑）', content(item)); details.append(label);
      if (Object.keys(item.variables).length) details.append(node('p', '保留自动填入项：' + Object.entries(item.variables).map(([key,value]) => `{${key}} ${value}`).join('；'), 'pw-meta'));
      editor.addEventListener('input', () => { edits.set(item.id, editor.value); resets.delete(item.id); badge.textContent = '未保存'; syncBody(item.id); schedule(); });
      const original = node('details', null, 'settings-details pw-default'); original.append(node('summary', '查看完整内置默认内容'), textArea('内置默认（只读）', item.default, true)[0]); details.append(original);
      const actions = node('div', null, 'actions'), save = node('button', '保存本段', 'primary'), restore = node('button', '填入内置默认', 'secondary');
      save.type = restore.type = 'button'; save.disabled = saving;
      save.addEventListener('click', () => void saveItem(item, editor));
      restore.addEventListener('click', () => { editor.value = item.default; edits.set(item.id, item.default); resets.add(item.id); badge.textContent = '待保存恢复'; syncBody(item.id); schedule(); });
      actions.append(save, restore); details.append(actions); list.append(details);
    }
    if (!visible.length) list.append(node('p', '没有匹配的提示词，请调整分类或搜索词。', 'pw-empty'));
  }
  function syncBody(key) {
    if (!manualBody && (!key || key === 'template.' + $('pw-template').value)) {
      const item = data.items.find(x => x.id === 'template.' + $('pw-template').value);
      $('pw-body').value = item ? content(item) : '';
    }
  }
  async function saveItem(item, editor) {
    if (saving) return;
    const value = editor.value; saving = true;
    document.querySelectorAll('#pw-items button.primary').forEach(b => b.disabled = true);
    note('pw-status', '正在保存「' + item.label + '」…');
    try {
      data = await request('', 'PUT', {revision:data.revision, changes:{[item.id]: resets.has(item.id) ? null : value}});
      // Keep edits made during the request, and all other unsaved paragraphs.
      if (edits.get(item.id) === value) { edits.delete(item.id); resets.delete(item.id); }
      note('pw-status', '已保存「' + item.label + '」。' + data.scope);
      syncBody(); schedule();
    } catch (error) { note('pw-status', error.message, true); }
    finally { saving = false; renderItems(); }
  }
  function payload() {
    const person = $('pw-person').value === 'video', selected = data.templates.find(x => x.id === $('pw-template').value);
    const supplements = Number($('pw-supplements').value);
    if (!Number.isInteger(supplements) || supplements < 0 || supplements > 29) throw new Error('服装补充图数量需为 0–29。');
    const roles = [...(person ? [] : ['人物']), '衣服', ...Array(supplements).fill('衣服补充')];
    document.querySelectorAll('#pw-roles input:checked').forEach(input => roles.push(input.value));
    return {roles, person_video:person, follow_source:$('pw-model').value === 'true', prompt:$('pw-body').value,
      rule_version:selected?.rule_version || 'yoyo-v3', scene_description:$('pw-scene').value, inspiration:$('pw-idea').value,
      mode:$('pw-mode').value, overrides:Object.fromEntries(edits)};
  }
  function schedule() { clearTimeout(timer); const id = ++sequence; note('pw-preview-status', '正在更新预览…'); timer = setTimeout(() => void preview(id), 250); }
  async function preview(id) {
    try {
      const value = await request('/preview', 'POST', payload()); if (id !== sequence) return;
      $('pw-final').value = value.final_prompt; $('pw-system').value = value.system_prompt;
      $('pw-input').value = Object.keys(value.planner_input || {}).length ? JSON.stringify(value.planner_input, null, 2) : '普通生成不经过拍摄规划模型。';
      $('pw-bindings').replaceChildren(...value.bindings.map(x => node('span', `${x.token} · ${x.role}`)));
      $('pw-diff').replaceChildren(...value.changes.map(x => node('p', (x.kind === 'added' ? '＋ 新增 / 替换后\n' : '－ 原规则\n') + x.text, 'pw-change pw-' + x.kind)));
      if (!value.changes.length) $('pw-diff').append(node('p', '当前配置与基础搭配没有额外差异。', 'pw-empty'));
      $('pw-applied').replaceChildren(...value.applied.map(x => { const b = node('button', x.label, 'pw-applied-link'); b.type = 'button'; b.addEventListener('click', () => { $('pw-group').value = ''; $('pw-search').value = ''; opened.add(x.id); renderItems(); const target = [...document.querySelectorAll('.pw-item')].find(n => n.dataset.promptId === x.id); target?.scrollIntoView({behavior:'smooth',block:'center'}); }); return b; }));
      note('pw-preview-status', value.message + (edits.size ? ' 当前预览包含未保存编辑。' : ''));
    } catch (error) { if (id === sequence) { $('pw-final').value = ''; note('pw-preview-status', error.message + ' 请修正后重试。', true); } }
  }
  async function load() {
    if (loading) return; loading = true;
    try {
      const first = !data, previous = $('pw-template').value;
      data = await request(); $('pw-scope').textContent = data.scope;
      const group = $('pw-group').value; $('pw-group').replaceChildren(new Option('全部提示词', ''), ...[...new Set(data.items.map(x => x.group))].map(x => new Option(x, x)));
      $('pw-group').value = first ? '默认正文与共享模板' : group;
      $('pw-template').replaceChildren(...data.templates.map(x => new Option(x.name + (x.is_default ? '（当前默认）' : ''), x.id)));
      $('pw-template').value = data.templates.some(x => x.id === previous) ? previous : (data.templates.find(x => x.is_default)?.id || data.templates[0]?.id || '');
      if (first) {
        for (const [key,value] of Object.entries({hairstyle:'发型',scene:'场景',...data.accessories})) {
          const label = node('label', null, 'check'), input = node('input'); input.type = 'checkbox'; input.id = 'pw-role-' + key; input.value = value; label.append(input, document.createTextNode(value)); $('pw-roles').append(label);
        }
        if (data.templates.length) opened.add('template.' + $('pw-template').value);
      }
      syncBody(); renderItems(); $('pw-options').disabled = false; schedule();
      note('pw-status', `已读取 ${data.items.length} 段提示词。` + (edits.size ? '未保存的编辑仍保留。' : '保存影响此服务器后续新任务；已提交任务保留原配置。'));
    } catch (error) { note('pw-status', error.message + ' 可点击“重新读取配置”重试。', true); throw error; }
    finally { loading = false; }
  }
  $('pw-reload').addEventListener('click', () => void load().catch(() => {}));
  $('pw-group').addEventListener('change', renderItems); $('pw-search').addEventListener('input', () => data && renderItems());
  $('pw-template').addEventListener('change', () => { manualBody = false; syncBody(); });
  $('pw-body').addEventListener('input', () => { manualBody = true; });
  $('pw-options').addEventListener('input', schedule); $('pw-options').addEventListener('change', schedule);
  $('pw-copy').addEventListener('click', async () => { try { await navigator.clipboard.writeText($('pw-final').value); note('pw-preview-status', '已复制最终文本。'); } catch (_) { $('pw-final').focus(); $('pw-final').select(); note('pw-preview-status', '请复制已选中的文本。'); } });
  window.addEventListener('beforeunload', event => { if (edits.size) { event.preventDefault(); event.returnValue = ''; } });
  window.PromptWorkbench = {load};
})();
