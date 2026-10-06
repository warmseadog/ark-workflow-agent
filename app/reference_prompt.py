"""Remove the editor-generated block before reusing a template or composing a request."""
import re


def compose_exclusive_prompt(prompt, roles, *, person_video=False, scene_description='', follow_source=False, rule_version='exclusive-v2', variation_plan=None):
    """Versioned deterministic composer shared by preview and provider submission."""
    from .prompt_templates import EXCLUSIVE_RULE_VERSION, YOYO_RULE_VERSION
    if rule_version not in (EXCLUSIVE_RULE_VERSION, YOYO_RULE_VERSION):
        raise ValueError('提示词规则版本不正确。')
    yoyo = rule_version == YOYO_RULE_VERSION
    from .reference_roles import ACCESSORY_LABELS, ACCESSORY_RULES
    allowed = {'人物', '衣服', '人物补充', '衣服补充', '发型', '场景', *ACCESSORY_LABELS.values()}
    if not isinstance(prompt, str) or len(prompt) > 14000 or len(strip_reference_rules(prompt)) > 10000:
        raise ValueError('提示词正文最多 10000 字。')
    if not isinstance(roles, list) or len(roles) > 69 or any(not isinstance(role,str) or role not in allowed for role in roles):
        raise ValueError('参考素材类别不正确。')
    if type(person_video) is not bool or type(follow_source) is not bool or not isinstance(scene_description,str) or len(scene_description)>2000:
        raise ValueError('参考模式或场景描述不正确。')
    primary = allowed - {'人物补充', '衣服补充'}
    if any(roles.count(role)>1 for role in primary) or (person_video and any(role.startswith('人物') for role in roles)):
        raise ValueError('主参考重复或人物模式冲突。')
    refs = {role:f'@Image{i}' for i,role in enumerate(roles,1) if role in primary}
    identity = '@Video2' if person_video else refs.get('人物')
    rules = [f'本次规则：{"独立参考优先，未指定配饰继承穿搭参考" if yoyo else "独立参考优先"}（{rule_version}）。以下来源分工优先于正文中的冲突描述。']
    if variation_plan is not None:
        rules.append('换个拍法任务：直接使用本次原始素材生成独立视频。@Video1 只提供核心展示意图及未被独立参考覆盖的场景、光照和视觉风格；不复制其运镜、构图和镜头节奏，不继承其中的人物、发型和服装。镜头按本次摄影方案执行。保持同一场景布局、光源方向、色温、调色和质感；仅允许视角变化引起的自然透视、反光和遮挡变化。不得默认新增人物、对白、字幕或花哨转场。')
    elif follow_source:
        rules.append('视频编辑任务：唯一编辑目标为 @Video1；保持原视频时长、画面比例、动作与镜头节奏，不延长或新增镜头。其他视频仅作人物身份参考。')
    if variation_plan is None:
        rules.append('动作来源：@Video1。严格遵循动作顺序、关键姿态、步态、移动方向、运镜、构图和节奏；不自行增加动作或镜头，不采用其中的人脸、发型和服装，不生成打码痕迹。')
    else:
        rules.append('保留原视频的核心展示内容，允许为新拍法服务的轻微姿态调整；不新增剧情，不生成打码痕迹。')
    if identity:
        rules.append(f'人物来源：{identity}。锁定脸型、五官比例、肤色及人物身份，全片一致；忽略所有其他素材中的人物身份，不自行美化或重塑五官。')
    if person_video:
        rules.append('人物参考视频 @Video2 不提供动作、服装、背景、声音或台词；保留原视频核心展示意图。' if variation_plan is not None else '人物参考视频 @Video2 不提供动作、服装、背景、声音或台词；动作仅以 @Video1 为准。')
    if '衣服' in refs:
        rules.append(f'服装来源：{refs["衣服"]}。严格还原款式、剪裁、版型、颜色、材质、纹理、图案和可见细节；忽略 @Video1、人物参考和其他素材中的服装，不改款、不换色、不增加装饰。已启用的独立配饰由各自参考决定。')
    for i,role in enumerate(roles,1):
        if role in ('人物补充', '衣服补充'):
            if yoyo and role == '衣服补充':
                rules.append(f'@Image{i} 为穿搭补充参考，补充服装正面、背面、侧面、结构和主图未展示的配饰；同类冲突时以主穿搭参考为准，独立参考优先；不提供人物身份、发型或场景。')
            else:
                rules.append(f'@Image{i} 仅补充{role[:2]}角度和可见细节，冲突时以对应主参考为准；不提供其他类别元素。')
    if yoyo:
        rules.append('穿搭图及补充图中的拼图、全身穿搭、正面、背面与单品特写共同描述同一套穿搭；服装结构与配饰需要综合考虑。背面细节仅在原动作自然露出时使用，不新增转身或镜头。主图同类冲突优先，补充图补全缺失信息；独立参考覆盖对应类别。忽略拼图排版、边框、标题、展示用手、水印、播放按钮和商品拍摄背景，不把手持展示照搬为成片中的展示动作。')
    hair = refs.get('发型')
    if hair:
        rules.append(f'发型来源：{hair}。该图是发型的唯一外观来源，采用发长、轮廓、刘海、卷曲程度和发色；忽略 @Video1、人物参考、服装参考和其他素材中的发型，不采用发型图中的人脸、身份、穿着或背景。')
    elif identity:
        rules.append(f'发型来源：{identity}。本次未启用独立发型图，发型仅以主人物参考为准，保留发长、轮廓、刘海、卷曲程度和发色；忽略 @Video1、服装参考和其他素材中的发型。')
    else:
        rules.append('发型来源：待添加主人物参考。本次未启用独立发型图，不得回退使用 @Video1 的发型。')
    if '场景' in refs:
        rules.append(f'场景来源：{refs["场景"]}。该图是场景的唯一外观来源，以其空间布局、布景、光线和环境替换原视频背景；忽略 @Video1 和所有其他素材中的背景，不引入场景图的人物、动作、服装或配饰。')
        if scene_description.strip():
            rules.append('场景补充（只补充场景图未指定的细节，不覆盖可见布局和环境）：'+scene_description.strip())
    elif scene_description.strip():
        rules.append('场景来源：文字描述。按以下描述替换原视频背景，忽略其他素材中的背景，保留 @Video1 的动作与镜头：'+scene_description.strip())
    else:
        rules.append('场景来源：@Video1。本次未启用更换场景，保留原视频场景，不采用其他参考素材中的背景。')
    for kind,label in ACCESSORY_LABELS.items():
        if label in refs:
            extra = '不保留或叠加旧包，不混合不同包款。' if kind=='bag' else '不继承旧元素，不混合款式，不额外叠加同类物品。'
            rules.append(f'{label}来源：{refs[label]}。该图是{label}的唯一外观来源；{ACCESSORY_RULES[kind]}严格保留形状、款式、颜色、材质及可见细节；忽略 @Video1、人物参考、服装参考及其他参考素材中的{label}。{extra}只提取{label}，不带入图中人物、发型、服装、其他配饰或背景。')
        elif yoyo and '衣服' in refs:
            clothing_refs = '、'.join(f'@Image{i}' for i,role in enumerate(roles,1) if role in ('衣服','衣服补充'))
            rules.append(f'{label}来源：穿搭参考 {clothing_refs}。本次未启用独立{label}参考；采用穿搭图或单品特写中清楚展示的{label}，主图未展示时从补充图补全，同类冲突以主图为准；{ACCESSORY_RULES[kind]}忽略 @Video1、人物参考及其他类别参考中的{label}，不混合或叠加。穿搭资料未清楚展示该类时，不凭空添加。')
    ending = ('其他清楚展示的穿搭配饰也从穿搭参考提取，不继承其他素材中的同类物品；' if yoyo else '未启用的独立配饰不产生替换指令，沿用既有素材分工；')
    rules.append(ending+'不得凭空增加物品。保持全片身份和各元素连续一致，运动、佩戴、接触、遮挡和透视自然，不漂移、不闪烁、不穿模。禁止新增文字、水印或特效，未展示部分仅作最小且一致的补全。')
    if variation_plan is not None:
        from .variation_llm import render_plan
        rules = [line.replace('背面细节仅在原动作自然露出时使用，不新增转身或镜头。','背面细节仅在参考充分时使用，不默认新增转身。').replace('保留 @Video1 的动作与镜头：','摄影按本次方案执行：') for line in rules]
        base = render_plan(variation_plan)
    else:
        base = normalize_reference_mentions(strip_reference_rules(prompt), person_video)
    return base + '\n\n【素材联动】\n' + '\n'.join(rules) + '\n【联动结束】'


def strip_reference_rules(prompt: str) -> str:
    return re.sub(r"\n*【素材联动】[\s\S]*?【联动结束】", "", prompt).strip()


def normalize_reference_mentions(prompt: str, person_video: bool) -> str:
    """Rebind role-labelled template mentions; leave arbitrary numbered prose alone."""
    identity = '@Video2' if person_video else '@Image1'
    clothing = '@Image1' if person_video else '@Image2'
    prompt = re.sub(r'@(?:Image1|Video2)人物参考(?:图|视频)', identity + ('人物参考视频' if person_video else '人物参考图'), prompt)
    prompt = re.sub(r'@(?:Image1|Video2)(?=的人物身份|的脸|为主人物身份)', identity, prompt)
    return re.sub(r'@Image[12](?=衣服参考图|服装参考图|的服装|服装|为主服装)', clothing, prompt)


def strict_reference_rules(references, person_video: bool = False) -> str:
    """Bind constraints to actual submission order, never to editor-supplied numbering."""
    rules = [
        '【严格参考】',
        '参考素材在各自负责范围内优先于文字描述；文字只补充素材未指定的细节，不能改变已指定的身份、穿着、动作或镜头。',
        '动作主参考：@Video1。严格遵循主体动作顺序、关键姿态、步态、移动方向、运镜、构图和节奏；禁止自行增加动作、镜头或改变动作顺序。不得继承动作视频中主体的人脸和服装，不生成马赛克或打码痕迹。',
    ]
    identity = '@Video2' if person_video else next((f'@Image{i}' for i, (_, role) in enumerate(references, 1) if role == '人物'), None)
    clothing = next((f'@Image{i}' for i, (_, role) in enumerate(references, 1) if role == '衣服'), None)
    if identity:
        rules.append(f'人物主参考：{identity}。锁定同一人物的脸型、五官比例、肤色及可见外貌特征，全片保持一致；不得混入其他素材中的人物身份，不自行美化或重塑五官。')
    if clothing:
        rules.append(f'服装主参考：{clothing}。严格还原款式、剪裁、版型、颜色、材质、纹理、图案和可见细节；不得改款、换色、添加装饰或混入其他参考中的衣服。允许随动作产生合理褶皱，不改变服装设计。')
    for i, (_, role) in enumerate(references, 1):
        if role in ('人物补充', '衣服补充'):
            kind = '人物' if role == '人物补充' else '服装'
            rules.append(f'@Image{i} 仅补充{kind}的角度和可见细节；冲突时以对应主参考为准，不混合不同身份或款式。')
        elif role == '场景':
            rules.append(f'@Image{i} 仅控制场景，严格保持空间布局、布景、光线、环境色彩及可见细节；不带入图中人物、动作或穿着。')
        elif role not in ('人物', '衣服'):
            rules.append(f'@Image{i} 仅控制{role}，严格保持该部分的形状、颜色、材质及可见细节；不提取图中其他人物、穿着或背景。')
    rules.extend([
        '独立发型、场景、配饰启用时，在各自范围内优先于人物、服装和动作素材中的同类内容；未启用时按前述素材分工保留。',
        '禁止凭空增加人物、商品、配饰、文字、水印或特效。保持全片身份、服装细节和空间关系连续，不漂移、不闪变；被遮挡或未展示部分只作最小且一致的补全。',
        '以上素材分工与严格参考约束适用于整段视频；用户描述与之冲突时，遵循对应参考素材。',
        '【严格参考结束】',
    ])
    return '\n'.join(rules)
