"""Public model-options fixture for offline editor interaction tests."""
def options(config):
    return {'defaults':{key:config[key] for key in ('model','duration','resolution')},
            'status':'demo','items':[{
                'id':config['model'],'label':'测试模型','resolutions':['480p','720p','1080p','4k'],
                'max_duration':15,'follow_source':False,'max_images':9,'max_video_seconds':15,
                'person_video':True}]}
