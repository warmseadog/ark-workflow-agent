/* Small local people directory; no official queries on every page render. */
(() => {
  const picker = document.getElementById('person-picker');
  if (!picker) return;
  const el = id => document.getElementById('person-' + id);
  let people = [], selected = null, locked = false, personType = 'LivenessFace';
  const tabs = document.createElement('div'); tabs.className = 'person-library-tabs'; tabs.setAttribute('role','group'); tabs.setAttribute('aria-label','人物库类型');
  for (const [type,label] of [['LivenessFace','真人库'],['AIGC','虚拟人物库']]) {
    const button = document.createElement('button'); button.type = 'button'; button.dataset.personType = type; button.textContent = label;
    button.addEventListener('click',() => { personType = type; render(); }); tabs.append(button);
  }
  el('search').before(tabs);
  const menu = picker.querySelector('.person-menu');
  function positionMenu() {
    if (!picker.open) return;
    const rect = picker.querySelector('summary').getBoundingClientRect();
    const width = Math.min(420,window.innerWidth-24), height = Math.min(520,window.innerHeight-24);
    const below = window.innerHeight-rect.bottom-20, above = rect.top-20;
    const available = below >= Math.min(340,height) ? Math.min(height,below) : above >= Math.min(340,height) ? Math.min(height,above) : height;
    const top = below >= Math.min(340,height) ? rect.bottom+8 : above >= Math.min(340,height) ? rect.top-available-8 : Math.max(12,(window.innerHeight-available)/2);
    menu.style.width = width+'px'; menu.style.maxHeight = available+'px';
    menu.style.left = Math.max(12,Math.min(rect.left,window.innerWidth-width-12))+'px'; menu.style.top = top+'px';
  }
  picker.addEventListener('toggle',positionMenu);
  window.addEventListener('resize',positionMenu);
  window.addEventListener('scroll',positionMenu,true);
  el('menu-close').addEventListener('click',() => { picker.open = false; picker.querySelector('summary').focus(); });
  async function request(path, method = 'GET', body) {
    const response = await fetch('/api/portrait/' + path, {method,
      headers: body === undefined ? {} : {'Content-Type':'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '人物读取失败，请稍后重试');
    return data;
  }
  function render() {
    const person = people.find(x => x.id === selected);
    el('current').textContent = person ? person.name + (person.person_type === 'AIGC' ? ' · 虚拟人物' : ' · 真人') : selected ? '原人物不可用，请重新选择' : '选择人物';
    el('avatar').hidden = !person?.thumbnail_url;
    if (person?.thumbnail_url) el('avatar').src = person.thumbnail_url;
    el('add-first').hidden = people.length > 0;
    el('search').hidden = false;
    el('current').title = el('current').textContent;
    el('photos-open').disabled = locked;
    el('photos-open').hidden = !person;
    const videoMode = window.productionPortraits?.referenceMode === 'video';
    el('photos-open').textContent = videoMode ? '选视频' : '选照片';
    el('photos-hint').textContent = videoMode ? (person ? '可选择已入库视频，也可上传这个人的新视频。' : '先选人物，再选视频；新视频会自动入库检查。') : person ? '可换选已入库照片，也可上传这个人的新照片。' : '先选人物，再选照片；新照片会自动入库检查。';
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
    const ordinary = document.createElement('button'); ordinary.type = 'button'; ordinary.className = 'person-option';
    ordinary.textContent = '不选人物，仅用参考图'; ordinary.classList.add('person-ordinary'); ordinary.disabled = locked; ordinary.hidden = videoMode;
    ordinary.addEventListener('click', () => choose(null)); el('options').append(ordinary);
    if (!matches.length) { const note = document.createElement('p'); note.className = 'person-empty'; note.textContent = query ? /^asset-/i.test(query) ? '这里按名称搜索；照片编号请到后台人物库导入。' : '没有找到这个人物，请换个名称搜索。' : '暂无人物，请到后台人物库添加。'; el('options').prepend(note); }
    positionMenu();
  }
  function selectPhoto(person) {
    if (locked) return;
    picker.open = false;
    window.portraitPhotos.open(person);
  }
  el('photos-open').addEventListener('click',() => {
    const person = people.find(item => item.id === selected);
    if (person) selectPhoto(person);
    else { picker.open = true; el('search').focus(); }
  });
  function choose(id, notify = true) {
    if (locked && notify) return;
    selected = id || null;
    const person = people.find(item => item.id === selected); if (person) personType = person.person_type || 'LivenessFace';
    render(); picker.open = false;
    if (notify) window.dispatchEvent(new CustomEvent('portrait-person-changed', {detail:{id:selected}}));
  }
  async function refresh(sync = false) {
    const data = await request(sync ? 'people/sync' : 'people', sync ? 'POST' : 'GET', sync ? {person_type:personType} : undefined);
    people = (data.items || []).sort((a,b) => Number(b.photo_count > 0 || b.video_count > 0)-Number(a.photo_count > 0 || a.video_count > 0)); render(); return people;
  }
  el('search').addEventListener('input',render);
  picker.addEventListener('keydown',event => { if (event.key === 'Escape') { picker.open = false; picker.querySelector('summary').focus(); } });
  document.addEventListener('click',event => { if (!picker.contains(event.target)) picker.open = false; });
  window.addEventListener('portrait-verified', async event => {
    try { await refresh(); choose(event.detail?.person_id || selected || (people.length === 1 ? people[0].id : null)); }
    catch (error) { window.toast?.(error.message); }
  });
  const ready = refresh().catch(error => { el('current').textContent = '人物暂不可用'; return []; });
  window.portraitPeople = {
    ready, request, refresh, choose, get items() { return [...people]; }, get selected() { return selected; },
    restore(id) { choose(id === undefined && people.length === 1 ? people[0].id : id, false); },
    lock(value) { locked = value; picker.inert = value; if (value) picker.open = false; render(); },
    async selectGroup(groupId, personType = 'LivenessFace') {
      const data = await request('people/resolve','POST',{group_id:groupId,person_type:personType});
      await refresh(); choose(data.id,false);
    },
  };
})();
