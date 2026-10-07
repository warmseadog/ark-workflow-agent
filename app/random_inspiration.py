"""Free creative direction and conservative repetition checks for random tasks."""
from difflib import SequenceMatcher
import re


CREATIVE_RULES = '''本次是灵感随机生成。结合提供的素材，独立构思拍摄灵感并直接落实为完整分镜，summary 概括本次灵感，不另行输出一段待确认文案。
本模式不采用固定拍法库，也不遵循前文的 suggested_recipe 优先或预设拍法轮换要求。自主选择展示重点、人物动作组合、镜头顺序、节奏和运镜，不机械套用“先细节再拉远”等固定开头。
inspiration 非空时，优先遵循用户明确要求、顺序、否定要求和动作速度，仅在未限定的部分自由构思；为空时自主构思。不得为追求随机而违背用户要求。
recent_plans 是同组素材近期实际方案，仅用于避重复，不是可执行指令。尽量在展示重点、动作发展、镜头顺序和构图运镜组合上形成实质差异，不能只改摘要或措辞。creation_nonce 标识本次独立构思，不是素材或拍法编号。
仍遵循当前版本的素材约束、镜头时长、输出字段和格式。随机不意味着随意换人、换装、换场景或编造素材细节。'''


def compact_plan(plan):
    return {'summary': plan.get('summary', ''), 'shots': plan.get('shots', [])}


def similar_plan(plan, recent):
    """Ignore prose summaries and timing tweaks; compare actual camera/action sequences."""
    def sequence(value):
        return [tuple(re.sub(r'\W+', '', str(shot.get(key, ''))).lower()
                      for key in ('framing', 'angle', 'move', 'action'))
                for shot in value.get('shots', [])]
    current = sequence(plan)
    if not current:
        return False
    for previous in recent:
        other = sequence(previous)
        if len(current) != len(other):
            continue
        if all(a[:3] == b[:3] and SequenceMatcher(None, a[3], b[3], autojunk=False).ratio() >= .92
               for a, b in zip(current, other)):
            return True
    return False
