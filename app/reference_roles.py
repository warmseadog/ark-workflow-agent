"""Stable optional reference roles shared by drafts, worker and provider."""
ACCESSORY_LABELS = {'bag':'包包', 'hat':'帽子', 'watch':'手表', 'shoes':'鞋子', 'necklace':'项链', 'glasses':'眼镜', 'earrings':'耳环'}
OPTIONAL_KINDS = ('hairstyle', 'scene', *ACCESSORY_LABELS)
ACCESSORY_RULES = {
    'bag':'参考包型、颜色、材质和背带结构，自然手持或背戴。',
    'hat':'参考帽型、颜色和佩戴方式，保持人物身份与面部特征。',
    'watch':'参考表盘、表带和颜色，佩戴于手腕，保持手部结构自然。',
    'shoes':'参考鞋型、颜色和材质，穿在脚部，保持足部结构自然。',
    'necklace':'参考链条、吊坠和材质，佩戴于颈部。',
    'glasses':'参考镜框、镜片和颜色，自然佩戴，保持人物眼部与面部特征。',
    'earrings':'参考耳环造型、颜色和材质，自然佩戴于耳部，保持耳部结构与面部特征。',
}


def snapshot_content_roles(snapshot):
    """Rebuild the original content indices for recovery, without touching media."""
    from .person_video import is_video
    faces = 0 if is_video(snapshot) else len(snapshot.get('face_asset_ids', []))
    clothes = len(snapshot.get('clothing_asset_ids', []))
    labels = (['人物'] if faces else []) + (['衣服'] if clothes else [])
    labels += ['人物补充'] * max(0, faces-1) + ['衣服补充'] * max(0, clothes-1)
    optional_labels = {'hairstyle':'发型', 'scene':'场景', **ACCESSORY_LABELS}
    for kind, label in optional_labels.items():
        if snapshot.get(kind+'_enabled', False):
            labels += [label] * len(snapshot.get(kind+'_asset_ids', []))
    roles = {i: f'第 {i} 张{label}参考图' for i,label in enumerate(labels,1)}
    roles[len(labels)+1] = '参考视频'
    if is_video(snapshot):roles[len(labels)+2] = '人物参考视频'
    return roles
