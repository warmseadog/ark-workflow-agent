/* Small local people directory; no official queries on every page render. */
(() => {
  const picker = document.getElementById('person-picker');
  if (!picker) return;
  const el = id => document.getElementById('person-' + id);
  let people = [], selected = null, locked = false, personType = 'AIGC';
  const tabs = document.createElement('div'); tabs.className = 'person-library-tabs'; tabs.setAttribute('role','group'); tabs.setAttribute('aria-label','人物库类型');
  for (const [type,label] of [['AIGC','虚拟人物'],['LivenessFace','已授权真人']]) {
    const button = document.createElement('button'); button.type = 'button'; button.dataset.personType = type; button.textContent = label;
    button.addEventListener('click',() => { personType = type; render(); }); tabs.append(button);
  }
  el('search').before(tabs);
  const menu = picker.querySelector('.person-menu');
  document.querySelector('[data-photo-library]').append(menu);
  picker.querySelector('summary').addEventListener('click',event => {
    event.preventDefault();
    if (!locked) window.portraitPhotos.openLibrary();
  });
  const directoryReads = new AbortController();
  window.addEventListener('pagehide', () => directoryReads.abort());
  async function request(path, method = 'GET', body, options = {}) {
    const timeout = method === 'GET' ? AbortSignal.timeout(8000) : null;
    try {
    const response = await fetch('/api/portrait/' + path, {method,
      headers: body === undefined ? {} : {'Content-Type':'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: method === 'GET' ? AbortSignal.any([directoryReads.signal,timeout,...(options.signal ? [options.signal] : [])]) : undefined});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '人物读取失败，请稍后重试');
    return data;
    } catch (error) {
      if (error.name === 'TimeoutError') throw new Error('人物读取超时，请重试。');
      throw error;
    }
  }
  function render() {
    const person = people.find(x => x.id === selected);
    el('current').textContent = person ? '来自人物库：' + person.name : selected ? '原人物不可用，请重新选择' : '';
    el('bar').hidden = !selected;
    el('search').hidden = false;
    el('current').title = el('current').textContent;
    el('photos-open').disabled = locked;
    el('photos-open').textContent = '更换';
    el('photos-hint').textContent = selected ? '上传替换图会作为新参考，不会追加到原人物。' : '虚拟人物可直接上传；真人请从人物库选择已授权素材。';
    tabs.querySelectorAll('button').forEach(button => { button.setAttribute('aria-pressed',String(button.dataset.personType === personType)); button.disabled = locked; });
    const query = el('search').value.trim().toLowerCase();
    el('options').replaceChildren();
    const matches = people.filter(x => (x.person_type || 'LivenessFace') === personType && x.name.toLowerCase().includes(query));
    for (const item of matches) {
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'person-option'; button.dataset.personId = item.id;
      button.disabled = locked; button.setAttribute('aria-pressed', String(item.id === selected));
      if (item.thumbnail_url) { const img = document.createElement('img'); img.src = item.thumbnail_url; img.alt = ''; img.loading = 'lazy'; button.append(img); }
      const info = document.createElement('span'); info.className = 'person-option-info';
      const name = document.createElement('strong'); name.textContent = item.name; info.append(name);
      const note = document.createElement('small'); note.textContent = [item.photo_count ? `${item.photo_count} 张照片` : '', item.video_count ? `${item.video_count} 段视频` : ''].filter(Boolean).join(' · ') || '暂无可用素材';
      info.append(note); button.append(info);
      if (item.id === selected) { const badge = document.createElement('span'); badge.className = 'person-selected-label'; badge.textContent = '已选'; button.append(badge); }
      button.addEventListener('click', () => selectPhoto(item)); el('options').append(button);
    }
    if (!matches.length) { const note = document.createElement('p'); note.className = 'person-empty'; note.textContent = query ? /^asset-/i.test(query) ? '这里按名称搜索；照片编号请到后台人物库导入。' : '没有找到这个人物，请换个名称搜索。' : '暂无人物，请到后台人物库添加。'; el('options').prepend(note); }
  }
  function selectPhoto(person) {
    if (locked) return;
    window.portraitPhotos.open(person);
  }
  el('photos-open').addEventListener('click',() => {
    if (!locked) window.portraitPhotos.openLibrary();
  });
  function choose(id, notify = true) {
    if (locked && notify) return;
    selected = id || null;
    const person = people.find(item => item.id === selected); if (person) personType = person.person_type || 'LivenessFace';
    render(); picker.open = false;
    if (notify) window.dispatchEvent(new CustomEvent('portrait-person-changed', {detail:{id:selected}}));
  }
  async function refresh(sync = false, options = {}) {
    const data = await request(sync ? 'people/sync' : 'people', sync ? 'POST' : 'GET', sync ? {person_type:personType} : undefined, options);
    if (directoryReads.signal.aborted || options.signal?.aborted) throw new DOMException('Page closed', 'AbortError');
    people = (data.items || []).sort((a,b) => Number(b.photo_count > 0 || b.video_count > 0)-Number(a.photo_count > 0 || a.video_count > 0)); render(); return people;
  }
  el('search').addEventListener('input',render);
  window.addEventListener('portrait-verified', async event => {
    try { await refresh(); choose(event.detail?.person_id || selected || (people.length === 1 ? people[0].id : null)); }
    catch (error) { window.toast?.(error.message); }
  });
  const ready = refresh().catch(error => { el('current').textContent = '人物暂不可用'; return []; });
  window.portraitPeople = {
    ready, request, refresh, choose, get items() { return [...people]; }, get selected() { return selected; },
    restore(id) { choose(id, false); },
    lock(value) { locked = value; picker.inert = value; if (value) picker.open = false; render(); },
    async selectPerson(id, options = {}) {
      await refresh(false, options);
      if (!people.some(person => person.id === id)) throw new Error('所选人物不在当前人物库中，请刷新后重试。');
      choose(id, false);
    },
    async selectGroup(groupId, personType = 'LivenessFace') {
      const data = await request('people/resolve','POST',{group_id:groupId,person_type:personType});
      await refresh(); choose(data.id,false);
    },
  };
})();
