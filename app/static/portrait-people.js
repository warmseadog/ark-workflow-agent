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
    el('current').textContent = person ? person.name + (person.person_type === 'AIGC' ? ' · 虚拟人物' : ' · 已认证') : selected ? '原人物不可用，请重新选择' : '选择人物';
    el('avatar').hidden = !person?.thumbnail_url;
    if (person?.thumbnail_url) el('avatar').src = person.thumbnail_url;
    el('add-first').hidden = people.length > 0;
    el('search').hidden = people.length < 8;
    el('rename-toggle').hidden = !person;
    tabs.querySelectorAll('button').forEach(button => { button.setAttribute('aria-pressed',String(button.dataset.personType === personType)); button.disabled = locked; });
    const query = el('search').value.trim().toLowerCase();
    el('options').replaceChildren();
    for (const item of people.filter(x => (x.person_type || 'LivenessFace') === personType && x.name.toLowerCase().includes(query))) {
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'person-option'; button.dataset.personId = item.id;
      button.disabled = locked; button.setAttribute('aria-pressed', String(item.id === selected));
      if (item.thumbnail_url) { const img = document.createElement('img'); img.src = item.thumbnail_url; img.alt = ''; img.loading = 'lazy'; button.append(img); }
      const info = document.createElement('span'); info.textContent = item.name;
      const note = document.createElement('small'); note.textContent = (item.person_type === 'AIGC' ? '虚拟人物' : '已认证真人') + (item.photo_count ? ' · 有可用照片' : ' · 可上传第一张照片') + (item.generated_count ? ' · 已成功生成 ' + item.generated_count + ' 次' : ' · 尚无生成成功记录');
      info.append(note); button.append(info);
      button.addEventListener('click', () => choose(item.id)); el('options').append(button);
    }
    const ordinary = document.createElement('button'); ordinary.type = 'button'; ordinary.className = 'person-option';
    ordinary.textContent = '普通参考图（不使用真人授权）'; ordinary.disabled = locked;
    ordinary.addEventListener('click', () => choose(null)); el('options').append(ordinary);
    if (!people.length) { const note = document.createElement('p'); note.textContent = '添加人物后，上传照片即可自动校验。'; el('options').append(note); }
  }
  function choose(id, notify = true) {
    if (locked && notify) return;
    selected = id || null;
    const person = people.find(item => item.id === selected); if (person) personType = person.person_type || 'LivenessFace';
    render(); picker.open = false;
    el('rename').hidden = true;
    if (notify) window.dispatchEvent(new CustomEvent('portrait-person-changed', {detail:{id:selected}}));
  }
  async function refresh(sync = false) {
    const data = await request(sync ? 'people/sync' : 'people', sync ? 'POST' : 'GET', sync ? {person_type:personType} : undefined);
    people = data.items || []; render(); return people;
  }
  el('search').addEventListener('input',render);
  el('sync').addEventListener('click',async () => {
    el('sync').disabled = true;
    try { await refresh(true); if (!selected && people.length === 1) choose(people[0].id); }
    catch (error) { window.toast?.(error.message); }
    finally { el('sync').disabled = false; }
  });
  el('rename-toggle').addEventListener('click',() => {
    const person = people.find(x => x.id === selected); if (!person) return;
    el('name').value = person.name; el('rename').hidden = false; el('name').focus();
  });
  el('name-save').addEventListener('click',async () => {
    const id = selected; el('name-save').disabled = true;
    try {
      const person = await request('people/'+id,'PUT',{name:el('name').value});
      people = people.map(x => x.id === id ? person : x); render(); el('rename').hidden = true;
    } catch (error) { window.toast?.(error.message); }
    finally { el('name-save').disabled = false; }
  });
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
