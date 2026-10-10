(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const form = byId('standby-form'), source = byId('standby-source');
  let data = null, sourceId = '', baseline = '', initialized = false;
  const values = () => JSON.stringify(['standby-name', 'standby-content', 'standby-note'].map(id => byId(id).value));
  function status(message, error = false) {
    const node = byId('standby-status');
    node.textContent = message; node.hidden = !message; node.dataset.error = String(error);
  }
  async function request(method = 'GET', body) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/admin/prompt-standby', {method, cache:'no-store', signal:controller.signal,
        headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '保存失败，请检查名称和正文。');
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时；保存结果可能未返回，请刷新核对版本记录后再重试。');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function ruleText(item) {
    return item ? `${data.rule_labels[item.rule_version] || item.rule_version}（${item.rule_version}）` : '无来源模板';
  }
  function fillSource(id) {
    const item = data.templates.find(x => x.id === id);
    sourceId = item?.id || ''; source.value = sourceId;
    byId('standby-name').value = '';
    byId('standby-content').value = item?.content || '';
    byId('standby-note').value = '';
    byId('standby-rule').textContent = '继承来源的规则版本：' + ruleText(item) + '。仅保存候选正文，不修改规则。';
    baseline = values();
  }
  function readOnly(label, value, rows = 6) {
    const node = document.createElement('label'), input = document.createElement('textarea');
    node.textContent = label; input.value = value; input.readOnly = true; input.rows = rows; node.append(input); return node;
  }
  function renderRecords() {
    const list = byId('standby-records'); list.replaceChildren();
    if (!data.items.length) { list.textContent = '暂无待用版本。保存后会出现在这里，当前默认内容保持不变。'; return; }
    for (const item of data.items) {
      const details = document.createElement('details'), summary = document.createElement('summary'), meta = document.createElement('p');
      details.className = 'settings-details'; summary.textContent = `${item.name} · 待用（未生效）`;
      meta.className = 'hint'; meta.textContent = `${new Date(item.created_at).toLocaleString()} · 来源：${item.source_name} · 规则：${item.rule_label}（${item.rule_version}）`;
      details.append(summary, meta, readOnly('待用正文', item.content));
      if (item.note) { const note = document.createElement('p'); note.textContent = '改动备注：' + item.note; details.append(note); }
      const snapshot = document.createElement('details'), heading = document.createElement('summary');
      snapshot.className = 'settings-details'; heading.textContent = '对照保存时的来源正文与规则示例';
      snapshot.append(heading, readOnly('来源正文快照', item.source_content), readOnly('保存时示例（人物、衣服、独立围巾、独立手饰）', item.example_prompt));
      details.append(snapshot); list.append(details);
    }
  }
  async function load() {
    data = await request();
    byId('prompt-current-name').textContent = data.current?.name || '未设置默认模板（新建草稿正文为空）';
    byId('prompt-current-rule').textContent = data.current ? ruleText(data.current) : '新建草稿使用早期素材联动（legacy-v1）';
    byId('prompt-current-body').value = data.current?.content || '';
    byId('prompt-current-example').value = data.current?.example_prompt || '';
    source.replaceChildren();
    for (const item of data.templates) {
      const option = document.createElement('option'); option.value = item.id;
      option.textContent = item.name + (item.is_default ? '（当前默认）' : ''); source.append(option);
    }
    // Refresh the live overview without discarding an administrator's candidate edits.
    if (!initialized) { fillSource(data.current?.id || data.templates[0]?.id || ''); initialized = true; }
    else if (data.templates.some(x => x.id === sourceId)) {
      source.value = sourceId;
      byId('standby-rule').textContent = '继承来源的规则版本：' + ruleText(data.templates.find(x => x.id === sourceId)) + '。仅保存候选正文，不修改规则。';
    } else {
      sourceId = ''; source.selectedIndex = -1;
      byId('standby-rule').textContent = '原来源模板已删除，请重新选择来源；填写内容仍保留。';
    }
    form.querySelector('fieldset').disabled = !data.templates.length;
    renderRecords();
  }
  source.addEventListener('change', () => {
    if (values() !== baseline && !confirm('切换来源将重新填入正文，当前未保存内容会丢失。确认切换？')) { source.value = sourceId; return; }
    fillSource(source.value); status('');
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.querySelector('fieldset').disabled) return;
    const payload = {name:byId('standby-name').value, content:byId('standby-content').value,
      note:byId('standby-note').value, source_template_id:source.value};
    form.querySelector('fieldset').disabled = true; status('正在保存待用版本…');
    try {
      const saved = await request('POST', payload);
      data.items = [saved, ...data.items].slice(0, 100); renderRecords(); baseline = values();
      status('已保存为待用版本，当前默认提示词未改变。');
    } catch (error) { status(error.message || '无法保存，填写内容已保留。', true); }
    finally { form.querySelector('fieldset').disabled = !data.templates.length; }
  });
  window.PromptMechanism = {load};
})();
