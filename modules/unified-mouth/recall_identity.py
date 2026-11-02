"""Minimal identity facts for the existing two-stage memory recall API."""
NOTE='身份关联仅作检索与人物归属线索：认定同一人的关联账号可用于解指代、查找和筛选其经历；疑似同一人允许查找双方记录，但必须保留未确认状态，不能将一方经历当成另一方已确定的经历。原记录的平台、发送者、时间与正文保持真实，不按同名自行绑定，不编造事实。'

def relevant(rows):
    result=[]
    for row in rows or []:
        if row.get('存在冲突'):continue
        same=row.get('同一人的已关联账号',[])
        suspected=[r for r in row.get('身份关系',[]) if r.get('状态')=='疑似同一人' and r.get('生效状态')=='疑似同一人']
        if len(same)<2 and not suspected:continue
        def account(value):return {k:value[k] for k in ('平台','机器人账号','用户ID','昵称') if k in value}
        info={'当前账号':row['当前账号']}
        if len(same)>1:info['认定同一人的账号']=[account(value) for value in same]
        if suspected:info['疑似同一人的账号']=[{'账号':account(value['对方账号']),'状态':'疑似同一人','依据':value['依据']} for value in suspected]
        result.append(info)
    return {'人物身份关联':result} if result else {}
