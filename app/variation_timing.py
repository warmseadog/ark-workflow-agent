"""Versioned timing tolerance. Repairs never rewrite actions or split shots."""
import math
import re

TIMING_POLICY = 'motion-timing-tolerant-v1'
TIMING_RULE = '''本次分镜时长规则优先于资料和旧Skill中的时长限制：每个镜头通常2～3秒，优先保证动作完整与自然衔接；1～4秒（含边界）均可接受，不必为了凑2～3秒机械切镜。按内容和总时长安排镜头数量，不固定为3或4镜头；连续完整覆盖0到duration。总时长不足1秒时使用单镜头覆盖全程。start/end必须输出JSON数字，不要带引号。
original_prompt仅提供展示意图与外观背景资料，其中自动生成的“不增加动作或镜头”“严格遵循原节奏”等旧约束不得阻止本次分镜安排；用户本次inspiration明确要求保留的动作、顺序、速度、运镜和禁止项仍应遵循。'''
FINAL_TIMING_RULE = '镜头以2～3秒为目标，允许1～4秒的合理偏差；按已规划时序执行，不为凑时长机械切镜。'


class PlanTimingError(ValueError):
    """A recoverable planning error containing only trusted timing details."""


def normalize_timing(shots, duration, repairs):
    if duration < 1 and len(shots) != 1:
        raise PlanTimingError('总时长不足1秒时需用单镜头覆盖全程；尚未提交视频生成。')
    cursor = 0
    adjusted = 0.0
    for index, shot in enumerate(shots, 1):
        for key in ('start', 'end'):
            value = shot[key]
            if isinstance(value, str) and len(value) <= 64 and re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value.strip()):
                value = float(value.strip())
                repairs.append({'shot':index, 'field':key, 'kind':'numeric_string'})
            if type(value) not in (int, float) or not math.isfinite(value):
                raise PlanTimingError(f'第{index}镜头的{key}必须是有效数字；尚未提交视频生成。')
            shot[key] = value
        start, end = shot['start'], shot['end']
        if end <= start:
            raise PlanTimingError(f'第{index}镜头结束时间必须晚于开始时间；尚未提交视频生成。')
        changes = [('start', cursor)]
        if index == len(shots): changes.append(('end', duration))
        for key, target in changes:
            delta = abs(shot[key] - target)
            if delta > .050000001:
                raise PlanTimingError(f'第{index}镜头时间边界偏差为{delta:.3f}秒，超过可自动修正范围；请连续覆盖总时长；尚未提交视频生成。')
            adjusted += delta
            if adjusted > .100000001:
                raise PlanTimingError('分镜时间边界累计偏差超过0.1秒，请重新安排连续时间线；尚未提交视频生成。')
            if delta:
                repairs.append({'shot':index, 'field':key, 'kind':'boundary', 'from':shot[key], 'to':target})
            shot[key] = target
        length = shot['end'] - shot['start']
        if length <= 0 or shot['end'] > duration:
            raise PlanTimingError(f'第{index}镜头时间无效或超出视频总时长；尚未提交视频生成。')
        if duration >= 1 and not 1-1e-9 <= length <= 4+1e-9:
            raise PlanTimingError(f'第{index}镜头时长为{length:.3f}秒，可接受范围为1～4秒（目标2～3秒），请重新规划；尚未提交视频生成。')
        cursor = shot['end']
