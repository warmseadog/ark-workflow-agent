window.syncLegacyReferencePrompt = function ({input, personMode, imageFiles, accessoryLabels, stripReferenceRules}) {
  const accessoryRules = {bag:'参考包型、颜色、材质及背带，自然手持或背戴',hat:'参考帽型、颜色与佩戴方式',watch:'参考表盘、表带与颜色，佩戴于手腕',shoes:'参考鞋型、颜色与材质，保持足部结构自然',necklace:'参考链条、吊坠及材质，佩戴于颈部',glasses:'参考镜框、镜片与颜色，保持眼部和面部特征',earrings:'参考耳环造型、颜色与材质，自然佩戴于耳部，保持耳部结构与面部特征',scarf:'参考围巾款式、颜色、材质、围法、长度与垂坠关系，随动作自然摆动，不穿模',hand_jewelry:'手饰包括手链、手镯、戒指，不含手表；保持款式、颜色、材质、佩戴位置和数量，贴合手腕或手指，保持手部结构自然'};
    const rules = [
      '严格参考：参考素材在各自负责范围内优先于文字描述，文字仅补充未指定细节。',
      '@Video1 动作主参考：严格遵循动作顺序、关键姿态、移动方向、运镜、构图和节奏，不自行增加动作或镜头；不采用其中的人脸和服装，不生成打码痕迹。'
    ];
    const identity = personMode === 'video' ? '@Video2' : '@Image1';
    const clothing = personMode === 'video' ? '@Image1' : '@Image2';
    if (personMode === 'video' || imageFiles.face.length) rules.push(`${identity} 人物主参考：锁定脸型、五官比例、肤色与人物身份，全片一致；不混入其他素材人物，不自行美化或重塑五官。`);
    if (imageFiles.clothing.length) rules.push(`${clothing} 服装主参考：严格还原款式、剪裁、版型、颜色、材质、纹理、图案及可见细节，不改款、不换色、不增加装饰。`);
    let supplement = (personMode === 'video' ? 0 : Math.min(1,imageFiles.face.length)) + Math.min(1,imageFiles.clothing.length);
    if (personMode !== 'video') for (const _ of imageFiles.face.slice(1)) rules.push(`@Image${++supplement} 仅补充人物角度和细节，冲突时以人物主参考为准。`);
    for (const _ of imageFiles.clothing.slice(1)) rules.push(`@Image${++supplement} 仅补充服装角度和细节，冲突时以服装主参考为准。`);
    let index = (personMode === 'video' ? 0 : imageFiles.face.length) + imageFiles.clothing.length;
    if (personMode === 'video') rules.push('人物身份以 @Video2 为准；动作和镜头以 @Video1 为准；衣服主参考为 @Image1。不采用视频2的动作、服装、背景、声音或台词。');
    const hair = imageFiles.hairstyle.length > 0 && document.getElementById('hairstyle-enabled').checked;
    const description = document.getElementById('scene-description').value.trim();
    const sceneEnabled = document.getElementById('scene-enabled').checked;
    const scene = imageFiles.scene.length > 0 && sceneEnabled;
    if (hair) rules.push(`@Image${++index} 发型参考：采用图中的发长、轮廓、刘海、卷曲程度和发色；仅参考头发，不采用该图的人脸、身份、服装或背景。人物身份以 ${personMode === 'video' ? '@Video2' : '@Image1'} 为准，发型以本图为准。`);
    else rules.push(personMode === 'video' ? '发型沿用人物参考视频 @Video2，不使用独立发型图。' : '发型沿用主人物参考图，不使用独立发型图。');
    if (scene) {
      rules.push(`@Image${++index} 场景参考：使用图中的空间、布景、光线和环境替换原视频背景；不引入图中的人物，保留 @Video1 的主体动作、运镜与节奏。`);
      if (description) rules.push('场景补充：'+description);
    } else if (sceneEnabled && description) rules.push('按文字描述替换场景：'+description+'；保留原视频的动作、镜头与节奏。');
    else rules.push('保留原视频场景，不替换背景。');
    for (const [kind,label] of Object.entries(accessoryLabels)) {
      if (imageFiles[kind].length && document.getElementById(kind+'-enabled').checked) rules.push(`@Image${++index} ${label}参考：${accessoryRules[kind]}；仅采用对应配饰，不引入图中人物、服装或背景。`);
      else if (['scarf','hand_jewelry'].includes(kind) && imageFiles.clothing.length) rules.push(`${label}来源：衣服参考图及补充图。采用清楚展示的${label}，主图未展示时由补充图补全，同类冲突以主图为准；${accessoryRules[kind]}；忽略动作视频、人物参考及其他类别参考中的同类配饰，未展示时不凭空添加。`);
    }
    rules.push('独立发型、场景或配饰在各自范围内优先，严格保持对应参考的可见细节，不混用其他内容。禁止凭空增加人物、配饰、文字、水印或特效；全片保持身份、穿着和细节连续一致。');
    const base = stripReferenceRules(input.value)
      .replace(/@(?:Image1|Video2)人物参考(?:图|视频)/g, identity + (personMode === 'video' ? '人物参考视频' : '人物参考图'))
      .replace(/@(?:Image1|Video2)(?=的人物身份|的脸|为主人物身份)/g, identity)
      .replace(/@Image[12](?=衣服参考图|服装参考图|的服装|服装|为主服装)/g, clothing);
    input.value = base + '\n\n【素材联动】\n' + rules.join('\n') + '\n【联动结束】';
    input.rows = 9;
    const hint = document.getElementById('prompt-reference-status');
    if (hint) hint.textContent = '已启用严格参考';
};
