(() => {
  const byId = id => document.getElementById(id);
  const form = byId('model-settings-form'), fieldset = byId('model-settings-fields');
  const control = name => form.querySelector(`[name="${name}"]`);
  const slider = byId('generation-duration-slider');
  const fallback = {resolutions:['720p'], max_images:9, max_video_seconds:15, max_duration:15};
  let items = [], applied = null, loaded = false, locked = true;
  let selected = null, ticks = [], sourceKey = null, restoringSelection = false;
  const find = id => items.find(item => item.id === id);
  const limits = () => find(control('model').value) || fallback;
  const sourceInfo = () => window.productionSource?.() || {};
  const rawSeconds = () => Number.isFinite(sourceInfo().duration) && sourceInfo().duration > 0 ? sourceInfo().duration : null;
  const seconds = () => selected ?? rawSeconds();
  const display = value => Number(value.toFixed(1)).toString();
  function sourceClip() {
    if (!limits().follow_source || selected === null || rawSeconds() === null) return null;
    const start = Number(byId('source-clip-start').value);
    const fixed = byId('source-clip-duration').value;
    const duration = fixed === '' ? Math.min(selected, rawSeconds() - start) : Number(fixed);
    if (start === 0 && Math.abs(duration - rawSeconds()) < 0.001) return null;
    return {start, duration};
  }
  function problem() {
    const value = seconds(), item = limits();
    if (rawSeconds() === null) return '请先选择参考视频，读取原片时长。';
    const minimum = item.follow_source ? 2 : 4;
    if (value < minimum || value > item.max_duration) return `当前模型支持 ${minimum}–${item.max_duration} 秒，请调整滑条。`;
    if (!item.follow_source && !Number.isInteger(value) && !item.auto_duration) return '当前模型仅支持整数秒，请选择整数刻度。';
    const clip = sourceClip();
    if (clip && (!Number.isFinite(clip.start) || clip.start < 0 || rawSeconds() - clip.start < 2)) return '片段起点之后需至少保留 2 秒原视频。';
    if (clip && (!Number.isFinite(clip.duration) || clip.duration < 2 || clip.duration > rawSeconds() - clip.start + .02)) return '基础片段需至少 2 秒，且不能超出原视频。';
    if (clip && clip.duration > value + .02) return '目标时长不能短于基础片段，请缩短基础片段。';
    return '';
  }
  function getForm() {
    const value = seconds();
    return {model:control('model').value, resolution:control('resolution').value,
      ratio:control('ratio').value || 'adaptive',
      duration:limits().follow_source || (value !== null && !Number.isInteger(value) && limits().auto_duration) ? -1 : value ?? 8,
      generate_audio:control('generate_audio').checked};
  }
  function showError(message) { byId('model-settings-error').textContent = message; byId('model-settings-error').hidden = !message; }
  function refreshSource() {
    const info = sourceInfo(), key = info.id || info.url || null;
    if (key && key !== sourceKey) {
      if (sourceKey && !restoringSelection) { selected = null; byId('source-clip-start').value = 0; byId('source-clip-duration').value = ''; }
      sourceKey = key; restoringSelection = false;
    }
    if (!key) { sourceKey = null; selected = null; }
    restoringSelection = false;
    const raw = rawSeconds(), value = seconds(), item = limits();
    ticks = Array.from({length:item.max_duration}, (_, index) => index + 1);
    if (raw !== null && raw >= 1 && raw <= item.max_duration && !ticks.includes(raw)) ticks.push(raw);
    ticks.sort((a,b) => a-b);
    slider.max = ticks.length - 1;
    slider.value = value === null ? 0 : ticks.reduce((best, tick, index) => Math.abs(tick-value) < Math.abs(ticks[best]-value) ? index : best, 0);
    slider.style.setProperty('--duration-fill', `${Number(slider.max) ? Number(slider.value) / Number(slider.max) * 100 : 0}%`);
    slider.disabled = locked || !loaded || raw === null;
    byId('generation-duration-reset').disabled = locked || !loaded || raw === null;
    slider.setAttribute('aria-valuetext', value === null ? '待选择视频' : `${display(value)} 秒`);
    byId('generation-duration-note').textContent = value === null ? '待选择视频' : `${display(value)} 秒`;
    byId('generation-source-duration').textContent = raw === null ? '读取参考视频后默认使用原片时长' : `原片 ${display(raw)} 秒`;
    const labels = byId('generation-duration-ticks');
    const signature = ticks.join(',');
    if (labels.dataset.signature !== signature) {
      labels.dataset.signature = signature;
      labels.replaceChildren(...ticks.map((tick,index) => {
        const option = document.createElement('option'); option.value = index; option.label = display(tick); return option;
      }));
    }
    const clip = sourceClip(), issue = problem();
    const canSelectSegment = item.follow_source && selected !== null;
    byId('source-clip-controls').hidden = !canSelectSegment;
    byId('source-clip-start').disabled = locked || !canSelectSegment;
    byId('source-clip-duration').disabled = locked || !canSelectSegment;
    byId('source-clip-preview').disabled = locked || !canSelectSegment || Boolean(issue) || !info.url;
    const baseSeconds = clip?.duration ?? raw;
    const extra = value !== null && baseSeconds !== null ? value - baseSeconds : 0;
    const explanation = item.follow_source && extra >= 1 - 1e-9 ? `保留原片正常速度，自动分析结尾并续写至 ${display(value)} 秒。`
      : item.follow_source && extra > 0 ? `多出不足 1 秒，保持 ${display(baseSeconds)} 秒原片时长。`
      : clip ? `从第 ${clip.start} 秒截取 ${display(clip.duration)} 秒，保留原视频。`
      : item.follow_source ? '默认跟随原片；缩短截取片段，多出至少 1 秒时自动续写剧情。'
      : value !== null && !Number.isInteger(value) ? '当前模型不支持小数秒输出，此刻度使用模型自动时长；选择整数可指定生成时长。'
      : '当前模型按所选整数秒生成；原片内容作为动作参考。';
    byId('generation-duration-help').textContent = issue || explanation;
    byId('generation-duration-help').dataset.error = String(Boolean(issue && raw !== null));
    byId('generation-duration-help').classList.toggle('sr-only', (!issue && extra <= 0) || raw === null);
    slider.title = issue || explanation;
    byId('generation-ratio-note').textContent = item.follow_source
      ? (control('ratio').value !== 'adaptive' ? '按所选比例补边，保留完整画面；多出至少 1 秒时自动续写。' : '时长默认跟随原片；缩短截取，多出至少 1 秒时自动续写。')
      : '按所选时长与比例生成；小数时长由模型自动决定。';
    byId('source-clip-note').textContent = issue || explanation;
    byId('source-clip-note').dataset.error = String(Boolean(issue));
    if (loaded && (!issue || raw === null) && find(control('model').value)) { applied = getForm(); window.productionDraftModel = {...applied}; }
  }
  function renderAudio() {
    const supported = Boolean(limits().audio_control);
    control('generate_audio').disabled = locked || !supported;
    byId('generation-audio-state').textContent = supported ? (control('generate_audio').checked ? '开启' : '关闭') : '不可设置';
    byId('generation-audio-note').textContent = !supported ? '当前模型不支持声音开关。' : control('generate_audio').checked ? '包含人声、音效和音乐，可能触发音频版权检查。' : '关闭后生成无声视频，可在后期添加配音或音乐。';
    control('generate_audio').closest('label').title = byId('generation-audio-note').textContent;
  }
  function render(values) {
    control('model').replaceChildren(...items.map(row => new Option(row.label, row.id)));
    if (!find(values.model)) { const option = new Option('原模型暂不可用，请重新选择', values.model); option.disabled = true; control('model').add(option); }
    control('model').value = values.model;
    const item = limits();
    control('resolution').replaceChildren(...item.resolutions.map(value => new Option(value === '4k' ? '4K' : value,value)));
    control('resolution').value = item.resolutions.includes(values.resolution) ? values.resolution : '720p';
    const ratios = item.ratios || ['adaptive'];
    control('ratio').replaceChildren(...ratios.map(value => new Option(value === 'adaptive' ? '跟随原片' : value, value)));
    control('ratio').value = ratios.includes(values.ratio) ? values.ratio : 'adaptive';
    control('generate_audio').checked = item.audio_control ? values.generate_audio !== false : true;
    renderAudio(); refreshSource();
    byId('generation-model-note').textContent = item.follow_source ? '视频编辑 · 保留动作与镜头，替换人物和服装' : '参考生成 · 根据素材与提示词生成视频';
    showError(find(values.model) ? '' : '原模型未启用，请选择可用模型。');
  }
  const valid = () => Boolean(find(control('model').value) && limits().resolutions.includes(control('resolution').value) && !problem());
  function setLocked(value) { locked = value; fieldset.disabled = locked || !loaded; renderAudio(); refreshSource(); }
  function apply() {
    if (locked || !loaded) return;
    refreshSource(); showError(valid() ? '' : problem());
    window.dispatchEvent(new Event('model-settings-saved'));
  }
  window.generationOptions = {
    label: id => find(id)?.label || id || '未记录', get: () => applied ? {...applied} : null,
    limits: () => limits(), available: () => loaded && valid(),
    sourceClip: () => { if (sourceClip() && problem()) throw new Error(problem()); return sourceClip(); },
    targetDuration: () => limits().follow_source ? selected : null,
    effectiveDuration: original => limits().follow_source ? sourceClip()?.duration ?? original : original,
    refreshSource, setLocked,
    restore(values,clip = null,target = null) {
      restoringSelection = true;
      selected = target ?? clip?.duration ?? (values.duration > 0 && !find(values.model)?.follow_source ? values.duration : null);
      byId('source-clip-start').value = clip?.start || 0;
      byId('source-clip-duration').value = clip && target > clip.duration && !clip.retime ? clip.duration : '';
      applied = {...values, generate_audio:values.generate_audio !== false}; render(applied);
    },
  };
  control('model').addEventListener('change', () => { selected = null; byId('source-clip-start').value = 0; byId('source-clip-duration').value = ''; render(getForm()); apply(); });
  slider.addEventListener('input', () => { selected = ticks[Number(slider.value)]; apply(); });
  byId('generation-duration-reset').addEventListener('click', () => {
    if (locked || !loaded || rawSeconds() === null) return;
    selected = null;
    byId('source-clip-start').value = 0;
    byId('source-clip-duration').value = '';
    apply();
  });
  byId('source-clip-start').addEventListener('input', apply);
  byId('source-clip-duration').addEventListener('input', apply);
  control('resolution').addEventListener('change', apply);
  control('ratio').addEventListener('change', apply);
  control('generate_audio').addEventListener('change', () => { renderAudio(); apply(); });
  const dialog = byId('source-clip-dialog'), player = byId('source-clip-video');
  let previewRange = null;
  byId('source-clip-preview').addEventListener('click', () => {
    if (locked || problem() || !sourceInfo().url) return;
    previewRange = sourceClip() || {start:0,duration:rawSeconds()}; player.src = sourceInfo().url; dialog.showModal();
  });
  player.addEventListener('loadedmetadata', () => {
    if (!previewRange) return;
    player.playbackRate = 1;
    player.currentTime = previewRange.start; void player.play().catch(() => {});
  });
  const end = () => previewRange.start + previewRange.duration;
  player.addEventListener('play', () => { if (previewRange && (player.currentTime < previewRange.start || player.currentTime >= end())) player.currentTime = previewRange.start; });
  player.addEventListener('timeupdate', () => { if (previewRange && player.currentTime >= end()) player.pause(); });
  player.addEventListener('seeked', () => {
    if (!previewRange) return;
    const bounded = Math.max(previewRange.start,Math.min(end(),player.currentTime));
    if (Math.abs(player.currentTime-bounded) > .01) player.currentTime = bounded;
  });
  byId('source-clip-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { player.pause(); player.removeAttribute('src'); player.load(); previewRange = null; });
  (async () => {
    try {
      const response = await fetch('/api/production/model-options', {cache:'no-store'}), data = await response.json();
      if (!response.ok) throw new Error(data.detail || '无法读取模型列表');
      items = data.items; loaded = true; applied = data.defaults;
      render(applied); setLocked(locked);
      if (!items.length) showError('后台尚未启用可用模型，请联系管理员。');
    } catch (reason) { showError(reason.message); }
    finally { window.dispatchEvent(new Event('model-settings-loaded')); }
  })();
})();
