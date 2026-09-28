"""Remove the editor-generated block before reusing a template or composing a request."""
import re


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
