"""Presentation and eligibility for provider output-audio copyright failures."""
AUDIO_COPYRIGHT_CODE = 'OutputAudioSensitiveContentDetected.PolicyViolation'
AUDIO_COPYRIGHT_MESSAGE = '生成声音未通过版权检查。服务商认为生成的音频可能涉及版权限制。'


def is_audio_copyright(error='', kind=None):
    return kind == 'audio_copyright' or AUDIO_COPYRIGHT_CODE in (error or '')


def decorate_audio_failure(value, *, enabled=True, supported=True):
    detected = is_audio_copyright(value.get('error'), value.get('error_kind'))
    value['can_retry_without_audio'] = bool(value.get('status') == 'failed' and detected and enabled and supported)
    if detected:
        value['error_kind'] = 'audio_copyright'
        if value.get('status') == 'failed':
            value['message'] = AUDIO_COPYRIGHT_MESSAGE
    return value
