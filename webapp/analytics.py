"""Conservative consumption analysis. Predictions never count as confirmed usage."""
from datetime import datetime,timezone
from decimal import Decimal

def analyze(records,now=None,daily_limit=30.0):
    now=now or datetime.now(timezone.utc)
    selected=sorted([r for r in records if r['status']=='confirmed' and r.get('confirmed_reading') is not None],key=lambda r:r['captured_at'])
    alerts=[];points=[];total=Decimal('0');seconds=0.;previous=None
    for row in selected:
        timestamp=datetime.fromisoformat(row['captured_at']);value=Decimal(row['confirmed_reading']);delta=None
        if previous:
            hours=(timestamp-previous[0]).total_seconds()/3600
            change=value-previous[1]
            if change<0:
                alerts.append({'level':'warning','title':'读数回退','message':'已确认读数出现回退，可能是审核错误、换表或归零；该区间不计入用水统计。'})
            elif hours>0:
                delta=float(change);total+=change;seconds+=hours*3600
                rate=float(change)/hours*24
                if hours>=1 and rate>daily_limit:
                    alerts.append({'level':'warning','title':'区间用水偏高','message':f'一个{hours:.1f}小时区间折算日用水{rate:.2f} m³，超过配置阈值{daily_limit:.2f} m³/日；请先核查读数与用水场景，不能据此确定漏水。'})
        points.append({'time':row['captured_at'],'reading':float(value),'usage_m3':delta})
        previous=(timestamp,value)
    average=float(total)/(seconds/86400) if seconds>=86400 else None
    if selected and (now-datetime.fromisoformat(selected[-1]['captured_at'])).total_seconds()>86400:
        alerts.append({'level':'info','title':'记录超过24小时未更新','message':'请检查采集设备与网络；当前未配置设备预期采样间隔，不能断言设备离线。'})
    has_high=any(a.get('title')=='区间用水偏高' for a in alerts)
    advice=['建议定期查看读数变化，并与家中用水场景对照。','固定相机位置与光照，避免反光遮挡，并为每块实体水表使用独立编号。']
    if has_high:
        advice.append('近期区间用水偏高：请检查马桶水箱是否漏水、水龙头是否滴漏、热水器与洗衣机是否有异常进水，必要时关闭总阀分段排查。')
    if len(selected)<2:advice.insert(0,'至少需要两条同一水表的记录，才能计算区间用水；当前不生成用水结论。')
    else:advice.append('可在确认无人用水的时段对比前后读数；若仍增长，请排查水龙头、马桶及管路，必要时联系专业人员。')
    if average is None:advice.append('有效记录区间不足24小时，暂不提供平均日用水量。')
    return {'confirmed_count':len(selected),'latest_reading':selected[-1]['confirmed_reading'] if selected else None,'net_usage_m3':float(total) if seconds>0 else None,'average_daily_m3':average,'interval_hours':seconds/3600,'points':points,'alerts':alerts,'advice':advice,'advice_mode':'rules','note':'仅统计设备自动计入的同一水表读数；规则建议不是大模型输出。读数变化只能提示异常，不能诊断漏水。'}
