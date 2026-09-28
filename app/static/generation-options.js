(() => {
  const form = document.getElementById('model-settings-form');
  const fieldset = document.getElementById('model-settings-fields');
  const error = document.getElementById('model-settings-error');
  const status = document.getElementById('model-settings-status');
  const control = name => form.querySelector(`[name="${name}"]`);
  let items = [], applied = null, loaded = false, locked = true;
  const fallback = {resolutions:['720p'], max_images:9, max_video_seconds:15, max_duration:15};
  const find = id => items.find(item => item.id === id);
  function showError(message) { error.textContent = message; error.hidden = !message; }
  function refreshSource() {
    const video = document.querySelector('#video-reference-preview video');
    const duration = Number.isFinite(video?.duration) ? ` · ${video.duration.toFixed(1)} 秒` : '';
    document.getElementById('generation-duration-note').textContent = '跟随动作视频' + duration;
    const ratio = video?.videoWidth && video?.videoHeight ? ` · ${video.videoWidth} × ${video.videoHeight}` : '';
    document.getElementById('generation-ratio-note').textContent = '跟随动作视频' + ratio;
  }
  function render(values, adjust = false) {
    const item = find(values.model);
    control('model').replaceChildren(...items.map(row => new Option(row.label, row.id)));
    if (!item) {
      const option = new Option('原模型暂不可用，请重新选择', values.model);
      option.disabled = true; control('model').add(option);
    }
    control('model').value = values.model;
    const limits = item || fallback;
    control('resolution').replaceChildren(...limits.resolutions.map(value => new Option(value === '4k' ? '4K' : value, value)));
    let resolution = values.resolution;
    if (!limits.resolutions.includes(resolution)) resolution = '720p';
    control('resolution').value = resolution;
    control('duration').value = limits.follow_source ? -1 : (Number(values.duration) >= 4 && Number(values.duration) <= limits.max_duration ? values.duration : 8);
    control('duration').disabled = Boolean(limits.follow_source);
    control('duration').max = limits.max_duration;
    document.getElementById('generation-duration-field').hidden = Boolean(limits.follow_source);
    document.getElementById('generation-follow-source').hidden = !limits.follow_source;
    document.getElementById('generation-model-note').textContent = limits.follow_source ? '视频编辑 · 保留动作与镜头，替换人物和服装' : '参考生成 · 根据素材与提示词生成视频';
    showError(item ? '' : '原模型未启用，请选择可用模型，或联系管理员在后台启用。');
    status.textContent = adjust && resolution !== values.resolution ? `当前模型清晰度已调整为 ${resolution}。` : '';
    status.hidden = !status.textContent;
    refreshSource();
  }
  function getForm() { return {model:control('model').value, resolution:control('resolution').value, duration:Number(control('duration').value)}; }
  function valid() {
    const item = find(control('model').value);
    const seconds = Number(control('duration').value);
    return Boolean(item && item.resolutions.includes(control('resolution').value) &&
      (item.follow_source || (Number.isInteger(seconds) && seconds >= 4 && seconds <= item.max_duration)));
  }
  function setLocked(value) {
    locked = value;
    fieldset.disabled = locked || !loaded;
    control('duration').disabled = Boolean(find(control('model').value)?.follow_source);
  }
  function apply() {
    if (locked || !loaded) return;
    if (valid()) {
      applied = getForm();
      window.productionDraftModel = {...applied};
      showError('');
    } else showError('请选择可用模型，并填写范围内的整数秒数。');
    window.dispatchEvent(new Event('model-settings-saved'));
  }
  window.generationOptions = {
    get: () => applied ? {...applied} : null,
    limits: () => find(applied?.model) || fallback,
    available: () => loaded && valid(),
    refreshSource,
    setLocked,
    restore(values) {
      applied = {model:values.model, duration:values.duration, resolution:values.resolution};
      if (find(applied.model)?.follow_source) applied.duration = -1;
      render(applied);
    },
  };
  control('model').addEventListener('change', () => { render(getForm(), true); apply(); });
  control('resolution').addEventListener('change', apply);
  control('duration').addEventListener('input', apply);
  control('duration').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); apply(); } });
  (async () => {
    try {
      const response = await fetch('/api/production/model-options', {cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '无法读取模型列表');
      items = data.items; loaded = true; applied = data.defaults;
      render(applied); setLocked(locked);
      if (!items.length) showError('后台尚未启用可用模型，请联系管理员。');
    } catch (reason) { showError(reason.message); }
    finally { window.dispatchEvent(new Event('model-settings-loaded')); }
  })();
})();
