(() => {
  'use strict';
  const host = document.getElementById('section-overview');
  if (!host) return;
  const panel = document.createElement('section');
  panel.className = 'card';
  panel.id = 'scheduling-settings';
  panel.innerHTML = `<h3>全局生成并发</h3>
    <p class="hint">所有账号共享的同时执行名额。调低后，已开始的任务会继续完成；各账号仍遵守自己的并发与排队额度。</p>
    <p data-scheduling-usage role="status">正在读取…</p>
    <form><fieldset disabled><label for="scheduling-limit">全局并发数</label>
    <input id="scheduling-limit" name="global_concurrency" type="number" min="1" step="1" required inputmode="numeric">
    <p data-scheduling-capacity class="hint"></p>
    <button class="primary" type="submit">保存并发设置</button></fieldset></form>
    <p data-scheduling-note role="status" class="notice" hidden></p>
    <button class="secondary" type="button" data-scheduling-refresh>刷新使用情况</button>`;
  host.appendChild(panel);
  const form = panel.querySelector('form');
  const fields = panel.querySelector('fieldset');
  const input = form.elements.global_concurrency;
  const note = panel.querySelector('[data-scheduling-note]');
  let canEdit = false;
  let pending = false;
  function message(text, error = false) {
    note.textContent = text;
    note.hidden = !text;
    note.classList.toggle('error', error);
  }
  function render(data) {
    canEdit = data.can_edit === true;
    input.value = data.config.global_concurrency;
    input.max = data.config.worker_capacity;
    fields.disabled = !canEdit;
    panel.querySelector('[data-scheduling-capacity]').textContent = `当前服务可设置 1–${data.config.worker_capacity} 个名额。${canEdit ? '' : '仅超级管理员可修改。'}`;
    panel.querySelector('[data-scheduling-usage]').textContent = `执行中 ${data.usage.running} · 排队中 ${data.usage.queued} · 当前可用名额 ${data.usage.available_slots}`;
  }
  async function request(values) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/admin/scheduling', {
        method: values ? 'PUT' : 'GET', cache: 'no-store', signal: controller.signal,
        headers: values ? {'Content-Type': 'application/json'} : {},
        body: values ? JSON.stringify(values) : undefined,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '并发数无效，请检查填写内容。');
      return data;
    } finally { clearTimeout(timeout); }
  }
  async function load() {
    if (pending) return;
    pending = true;
    try { render(await request()); message(''); }
    catch (error) { message(error.name === 'AbortError' ? '读取并发设置超时，请重试。' : error.message, true); }
    finally { pending = false; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (pending || !canEdit || !form.reportValidity()) return;
    const value = Number(input.value);
    if (!Number.isInteger(value)) return;
    pending = true;
    fields.disabled = true;
    try {
      render(await request({global_concurrency: value}));
      message('并发设置已保存并立即生效；执行中的任务会继续完成。');
    } catch (error) {
      message(error.name === 'AbortError' ? '保存请求超时，请刷新核对当前值。' : error.message, true);
    } finally { pending = false; fields.disabled = !canEdit; }
  });
  panel.querySelector('[data-scheduling-refresh]').addEventListener('click', load);
  document.getElementById('refresh-overview')?.addEventListener('click', load);
  window.SchedulingSettings = {load};
  load();
})();
