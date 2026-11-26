"""Build an offline review artifact from the current Web views and controllers."""
import base64
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_backend.burst import BurstDay, make_baseline
from etf_backend.model import BASKET
from etf_backend.web import demo_live

def read(path):
    return (ROOT / path).read_text(encoding='utf-8')

def js(value):
    return json.dumps(value, ensure_ascii=False).replace('</', '<\\/')

seed = json.loads(read('data/demo-seed.json'))
seed['provider'] = 'RQData'
day = seed['demo']['as_of']
seed['core_fund_daily'] = {'date':day,'rows':[{'symbol':s,'flow':seed['demo']['fund_flows'][day][s]} for s in BASKET]}
live = demo_live(seed)
live['provider'] = 'RQData'
live['replay'] = True
live['table'] = [{**row,'bid':row['price']-.001,'ask':row['price']+.001,'quote_at':day+'T15:00:00+08:00'} for row in live['table']]
previous = seed['calendar'][-61:-1]
minutes = {d:{s:[1e6+(i%20)*10000 for i in range(240)] for s in BASKET} for d in previous}
daily = {d:{s:8e8 for s in BASKET} for d in previous}
baseline = make_baseline(previous,minutes,daily,day,.75,BASKET)
detector = BurstDay(day,baseline,.75,symbols=BASKET,baseline_as_of=previous[-1])
from etf_backend.model import SLOTS
for s in BASKET:
    for i,slot in enumerate(SLOTS):
        detector.add(s,slot,32e6 if s in BASKET[:4] and 40<=i<49 else 1e6)
burst = detector.payload()
burst.update(provider='RQData',replay=False,baseline_ready=True,
             futures_monitor={'state':'receiving','products':['IH','IF','IC','IM']},
             tick_factor_status={'state':'ready','covered':20,'expected':20},
             tick_monitor={'minute_driver':'tick','compared':240,'mismatched':0,'event_mismatches':0,'unrecovered':0,'recovered':0},
             journal={'enabled':True,'format':'sqlite-append-v1'},candidates=[],tick_candidates=[])
for event in burst['events']:
    signal = event['peak']
    sides = {side:{'turnover_cny':amount,'oi_increase_cny':amount*.25,'oi_decrease_cny':-amount*.05,'oi_net_cny':amount*.2} for side,amount in [('sell',2e8),('buy',1e8),('unclassified',.2e8)]}
    values = {'estimated_sell_cny':2e8,'estimated_sell_oi_cny':.5e8,'net_oi_cny':.6e8,'turnover_cny':3.2e8,'oi_increase_cny':.8e8,'oi_decrease_cny':-.2e8,'directions':sides,'delta_oi':40,'relative_return_bp':-3.5}
    event['evidence'] = {'premium':{'bp':8.3},'pressure':{'ask_up_with_volume':4},'peers':BASKET[:2],
        'etf_direction':{'side':'buy','buy_cny':signal['amount']*.7,'sell_cny':signal['amount']*.2,'neutral_cny':signal['amount']*.1},
        'futures':{**values,'product':'IH','status':'sell_side_evidence','window_start':signal['start'],'window_end':signal['end'],
            'etf_turnover_cny':signal['amount'],'etf_volume_shares':1e6,'covered':4,'expected':4,'classified_share':.94,
            'contracts':[{'code':'IH260'+str(i+3),'tenor':t,'values':values} for i,t in enumerate(['当月','次月','当季','次季'])]}}
comparison = {'schema_version':1,'unit':'CNY_1e8','method':'rqdata-l1-midpoint-band-v1','rows':[
    {'day':d,'symbol':s,'gross_yi':3+i*.2,'active_buy_yi':2+i*.1,'active_sell_yi':.6,'net_flow_yi':None if i==4 else 1.8+i*.1,'flow_issues':'nav_missing' if i==4 else ''}
    for d in seed['calendar'][-20:] for i,s in enumerate(BASKET)]}
market_rows=[]
for i,d in enumerate(seed['calendar'][-250:]):
    market_rows.append({'date':d,'concentration':{'expected':5300,'covered':5300,'suspended':12,'traded':5288,'total':18000,
        'tiers':{str(k):{'share':18+k*3+math.sin(i/12)*2,'amount':(18+k*3+math.sin(i/12)*2)*180,
            'count':math.ceil(5288*k/100),'percentile':65+math.sin(i/12)*20,'sample':250} for k in (1,3,5)}},
        'style':{'capital':4.2+math.sin(i/22)*.5,'turnover':1.1+math.sin(i/14)*.3},
        'indexes':{'000001.XSHG':3400+i*2,'000300.XSHG':4100+i*3,'000905.XSHG':6000+i*4,'000852.XSHG':6500+i*4,'399006.XSHE':2400+i*2}})
index_names={'000001.XSHG':'上证综指','000300.XSHG':'沪深300','000905.XSHG':'中证500','000852.XSHG':'中证1000','399006.XSHE':'创业板指'}
market={'provider':'RQData','built_at':seed['built_at'],'indexes':index_names,'rows':market_rows,
        'first_date':market_rows[0]['date'],'last_date':day,'archived_days':len(market_rows),
        'table_rows':market_rows[-20:],'capital_stats':{'mean':4.2,'std':.35}}
industry={'rows':[{'date':r['date'],'indexes':{code:{'capital':{'电子':18+math.sin(i/12)*3,'医药生物':13+math.cos(i/16)*3},
    'turnover':{'电子':24+math.cos(i/13)*3,'医药生物':19+math.sin(i/12)*3},'classified':n,'expected':n}
    for code,n in [('000300.XSHG',260),('000905.XSHG',440)]}} for i,r in enumerate(market_rows)],'label_frequency':'weekly'}
funds={'rows':[{'date':f'{2020+i//4}-{(i%4+1)*3:02d}-28','published':f'{2020+i//4}-{(i%4+1)*3:02d}-30',
    'funds':2100,'expected':2200,'weights':{'电子':15+i*.3+math.sin(i)*2,'食品饮料':24-i*.3,'医药生物':17+math.sin(i/3)*3}} for i in range(24)]}
financing={'indexes':index_names,'rows':[{'date':r['date'],'balance':16000+i*11,'change':math.sin(i/15)*24,'weekly_yoy':math.sin(i/40)*20,
    'change_share':math.sin(i/15)*.2,'capital_share':2.0+i*.001,'floating_share':2.6+i*.001,'m2_share':.7+i*.0002,'gdp_share':70+i*.05,'indexes':r['indexes']}
    for i,r in enumerate(market_rows)]}
sources={'default':'rqdata','realtime_default':'rqdata','sources':[{'id':s,'label':label,'ready':True,'latest_flow_date':day,'latest_market_date':day} for s,label in [('rqdata','RQData 日频归档'),('ddb','DDB 日频归档')]],
    'realtime_sources':[{'id':'rqdata','label':'Gateway · MATSFILE（SQLite）','ready':True}]}
payload={'seed':seed,'live':live,'burst':burst,'comparison':comparison,'market':market,'industry':industry,'funds':funds,'financing':financing,'sources':sources}
payload['licenses']={url:read(path) for url,path in [('/licenses/MIT.txt','LICENSE'),
    ('/licenses/echarts-NOTICE.txt','third_party/echarts/NOTICE'),
    ('/licenses/echarts-LICENSE.txt','third_party/echarts/LICENSE'),
    ('/licenses/d3-LICENSE.txt','third_party/echarts/licenses/LICENSE-d3')]}
mock = '''
const P=PAYLOAD;
window.__PROTOTYPE=true;
const burstListeners=new Set();
history.replaceState=()=>{};
window.fetch=async input=>{
 const u=new URL(input,'http://prototype.local'),p=u.pathname;
 let v;
 if(p==='/api/sources')v=P.sources;
 else if(p==='/api/board'){v=structuredClone(P.seed);v.provider=u.searchParams.get('source')==='ddb'?'DDB':'RQData';v.source={realtime_source:{label:'RQData'}};}
 else if(p==='/api/health')v={provider:'RQData',mode:'demo',history_built_at:P.seed.built_at};
 else if(p==='/api/live')v=P.live;
 else if(p==='/api/etf-flow-comparison')v=P.comparison;
 else if(p==='/api/etf-burst/dates')v={dates:[P.burst.date]};
 else if(p==='/api/etf-burst'){v=structuredClone(P.burst);v.replay=true;}
 else if(p==='/api/etf-burst/journal')v={rows:P.burst.events,notice:'演示日志'};
 else if(p==='/api/crowding'){v=structuredClone(P.market);v.rows=v.rows.slice(-Number(u.searchParams.get('days')||250));v.table_rows=P.market.rows.slice(-Number(u.searchParams.get('table_days')||5));}
 else if(p.startsWith('/api/crowding/'))v=P[p.split('/').pop()];
 else if(p==='/api/cumulative')v={provider:'RQData',date:u.searchParams.get('date'),...P.seed.minutes[u.searchParams.get('date')]};
 else if(p==='/api/daily')v={provider:'RQData',dates:P.seed.calendar.slice(-3),funds:P.seed.universe.slice(Number(u.searchParams.get('batch')||0)*8,Number(u.searchParams.get('batch')||0)*8+8).map(f=>({symbol:f.symbol,days:Object.fromEntries(P.seed.calendar.slice(-3).map(d=>[d,{flow:P.seed.demo.fund_flows[d]?.[f.symbol]??null}]))}))};
 else v={error:'原型中没有该演示接口'};
 return new Response(JSON.stringify(v),{status:v.error?503:200,headers:{'content-type':'application/json'}});
};
window.EventSource=class{
 constructor(url){this.url=url;setTimeout(()=>this.onopen?.(),50);}
 addEventListener(kind,handler){if(kind==='burst'){this.burstHandler=handler;burstListeners.add(handler);setTimeout(()=>{if(burstListeners.has(handler))handler({data:JSON.stringify(P.burst)});},180);}}
 close(){burstListeners.delete(this.burstHandler);}
};
document.addEventListener('DOMContentLoaded',()=>{
 const selector=document.getElementById('driverMode');
 if(!selector)return;
 const apply=document.getElementById('driverApply'),status=document.getElementById('driverStatus');
 let current='tick';
 selector.addEventListener('change',()=>{apply.disabled=selector.value===current;});
 apply.addEventListener('click',()=>{
   const requested=selector.value;
   if(requested===current)return;
   apply.disabled=true;selector.disabled=true;
   status.textContent='正在检查输入和基准…';
   setTimeout(()=>{
     if(requested==='bar'&&!document.getElementById('barFeedReady').checked){
       status.textContent='切换失败 · Bar 输入不可用，当前仍为 '+(current==='tick'?'Tick':'Bar')+'（演示）';
       selector.disabled=false;apply.disabled=false;return;
     }
     current=requested;
     P.burst.tick_monitor.minute_driver=current==='tick'?'tick':'provider';
     P.burst.updated_at=new Date().toISOString();
     for(const handler of burstListeners)handler({data:JSON.stringify(P.burst)});
     status.textContent='当前生效 '+(current==='tick'?'Tick 本地聚合':'Bar 标准分钟')+' · 独立会话（演示）';
     selector.disabled=false;apply.disabled=true;
   },250);
 });
});
document.addEventListener('click',e=>{
 const a=e.target.closest('a');
 const license=P.licenses[a?.getAttribute('href')];
 if(license){e.preventDefault();const w=window.open('','prototype_license');if(w){w.document.body.textContent=license;w.document.body.style.whiteSpace='pre-wrap';}return;}
 if(a?.getAttribute('href')?.startsWith('/api/etf-burst/journal')){
 e.preventDefault();const w=window.open('','prototype_journal');if(w){w.document.body.textContent=JSON.stringify({演示日志:P.burst.events},null,2);w.document.body.style.whiteSpace='pre-wrap';}}
});
'''.replace('PAYLOAD',js(payload))

module_cache={}
def module_url(name):
    if name in module_cache:return module_cache[name]
    source=read('src/assets/'+name)
    if name in ('radar.js','dashboard.js'):
        source=source.replace('RQData 实时推送','Gateway · MATSFILE（SQLite）')
    if name=='dashboard.js':
        source=source.replace('else box.hidden=true;','else box.hidden=false;')
    source=re.sub(r"from '(\./[^']+)'",lambda m:"from '"+module_url(m[1][2:])+"'",source)
    source=source.replace("s.src='/assets/brush.js'",'s.src='+js('data:text/javascript;base64,'+base64.b64encode(read('src/assets/brush.js').encode()).decode()))
    url='data:text/javascript;base64,'+base64.b64encode(source.encode()).decode()
    module_cache[name]=url
    return url

desktop_css='''
html{scroll-behavior:auto}body{font:13px 'Microsoft YaHei UI',system-ui,sans-serif;background:var(--bg);padding:0}
.wrap{max-width:none;width:100%;padding:8px 12px 24px;margin:0}.chart-sidebar,.section-nav,.nav-backdrop,.eyebrow{display:none!important}
.top{padding:0;margin:0}.top-line{padding:4px 0 8px}.top .brand{font-size:16px}.hero-heading{padding:0;margin:0 0 6px;min-height:0}.hero-heading h1{display:none}.hero-status{width:100%;display:flex;flex-direction:row;flex-wrap:wrap;gap:8px}.freshness{padding:3px 6px}.market-label{font-size:11px}.overview{gap:6px;margin:6px 0}.metric-card{padding:8px 12px;border-radius:2px;min-height:72px;box-shadow:none;background:var(--surface)}.metric-card strong{font-size:23px;margin:4px 0;line-height:1.15}.metric-label,.metric-foot{font-size:11px}.data-note{font-size:11px;margin:6px 0;line-height:1.5}
.chart-wrap,.table-wrap,.radar-board,.chart-source,.gap-report{border-radius:2px;box-shadow:none}.chart-wrap{padding:8px;margin-bottom:10px}.table-wrap{padding:10px;margin-top:10px}.chart{height:310px;min-height:230px;resize:vertical;overflow:hidden}#chart,#chartCum,#chartTypCase,#chartTypNorm,#chartAmt,#chartNf,#chartDAmt,#chartDNf,#chartBroad{height:310px}.comparison-grid{gap:8px}.part{margin-top:10px}.pt{font-size:15px;margin:2px 0 6px}.section-toggle{padding:4px 0;gap:6px}.section-number{border-radius:2px;padding:3px 5px}.signal-section{margin-top:10px}table{font-size:12px}th,td{padding:5px 9px}th{font-weight:600}.tscroll{max-height:360px;overflow:auto}th{position:sticky;top:0;z-index:1}.radar-heading{padding:8px 0}.radar-heading h2{font-size:18px}.radar-heading .eyebrow{display:none}.radar-summary{padding:10px 12px}.radar-summary h3{font-size:20px}.radar-summary p{margin:5px 0}.radar-metrics{padding:10px 12px;gap:8px}.radar-metrics strong{font-size:22px}.radar-controls,.radar-rules,.radar-funds-heading,.radar-voice{padding:8px 12px}.radar-board{margin-bottom:10px}.radar-funds{gap:7px}.radar-fund{border-radius:2px;padding:10px}.radar-index-summary{padding:8px 12px}.radar-columns{margin:0 12px 8px;border-radius:2px}.radar-candidate{padding:5px 12px}.radar-state-badge,.source-badge,.tag{border-radius:2px}.source-support{font-size:11px}.demo-notice{display:block!important;background:#fff4d1;color:#604a18;padding:4px 12px;font-size:11px;text-align:left;border-bottom:1px solid #dfcc9a}
button,input,select{font:12px 'Microsoft YaHei UI',sans-serif;border-radius:2px;min-height:25px;padding:3px 8px;box-shadow:none}input[type=checkbox],input[type=range]{min-height:0}.official-link{padding:5px 9px;font-size:12px}.theme-switch{padding:2px;border-radius:2px}.theme-switch button{border-radius:2px;width:auto;min-width:28px;height:26px}.broad-views button{border-radius:2px}.broad-controls{padding:5px 0;gap:7px}.coverage-grid{gap:6px}.source-content{padding:9px}.source-trigger,.source-menu{border-radius:2px}
.qt-tabs{position:sticky;top:0;z-index:30;display:flex;align-items:end;flex-wrap:wrap;gap:1px;border-bottom:1px solid #8597ac;padding-top:7px;margin-top:5px;background:var(--bg)}.qt-tabs button{border:1px solid var(--line);border-bottom:0;padding:7px 16px;min-height:31px;background:var(--surface-2);color:var(--muted);border-radius:3px 3px 0 0}.qt-tabs button[aria-selected=true]{background:var(--surface);color:var(--text);font-weight:600;box-shadow:inset 0 2px var(--accent)}.qt-tabs label{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:11px;padding:3px 0 5px}.qt-tabs select{max-width:210px}.qt-settings{margin-top:4px!important}.qt-settings>summary{font-size:12px;padding:5px 9px}.qt-settings>div{padding:8px 10px}.qt-driver{padding:5px 9px!important;margin:5px 0!important}.qt-driver>p{display:none}.qt-driver details{font-size:11px}.qt-driver .broad-controls{padding:0}.qt-native-status{padding:5px 8px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}
@media(max-width:800px){.header-actions{flex-wrap:wrap}.qt-tabs button{padding:6px 10px}.qt-tabs label{width:100%;margin-left:0}.overview{grid-template-columns:repeat(2,minmax(0,1fr))}.hero-status{align-items:start}.chart{height:270px}.comparison-grid{grid-template-columns:1fr}}
'''

native_ui='''
document.addEventListener('DOMContentLoaded',()=>{
 const isEtf=!!document.getElementById('radar');
 const panels=isEtf?[['radar','概览与异动'],['part1','盘中交易'],['part2','放量信号'],['part3','日度资金']]:[['marketConcentration','成交额集中度'],['marketStyle','大小盘风格'],['marketIndustry','指数行业'],['marketFunds','公募重仓'],['marketFinancing','融资与估值']];
 const navigation=document.createElement('nav');navigation.className='qt-tabs';navigation.setAttribute('aria-label','监控窗口页签');
 const tabs=document.createElement('div');tabs.setAttribute('role','tablist');navigation.append(tabs);
 const jump=document.createElement('select');jump.setAttribute('aria-label','定位图表');jump.add(new Option('定位图表…',''));
 const links=[...document.querySelectorAll('.chart-directory a')];
 const destinations=new Set();for(const link of links){const id=link.hash.slice(1);if(id&&!destinations.has(id)){destinations.add(id);jump.add(new Option(link.textContent.trim(),id));}}
 const label=document.createElement('label');label.append('定位',jump);navigation.append(label);
 const globalRange=document.getElementById('marketDays')?.closest('.broad-controls');
 document.querySelector('.section-nav').before(navigation);
 let selected=panels[0][0];
 function selectPanel(id){
   selected=id;
   for(const [key] of panels){const panel=document.getElementById(key);panel.style.display=key===id?'':'none';}
   for(const button of tabs.children){const active=button.dataset.panel===id;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
   if(globalRange)globalRange.style.display=id==='marketConcentration'||id==='marketStyle'?'':'none';
   requestAnimationFrame(()=>window.dispatchEvent(new Event('resize')));
 }
 for(const [id,name]of panels){const button=document.createElement('button');button.textContent=name;button.dataset.panel=id;button.setAttribute('role','tab');button.setAttribute('aria-controls',id);button.onclick=()=>{selectPanel(id);navigation.scrollIntoView({block:'start'});};button.onkeydown=e=>{if(e.key!=='ArrowRight'&&e.key!=='ArrowLeft')return;e.preventDefault();const next=(panels.findIndex(p=>p[0]===selected)+(e.key==='ArrowRight'?1:-1)+panels.length)%panels.length;tabs.children[next].click();tabs.children[next].focus();};tabs.append(button);}
 jump.onchange=()=>{const target=document.getElementById(jump.value);if(!target)return;const owner=panels.find(([id])=>document.getElementById(id).contains(target));if(owner)selectPanel(owner[0]);const toggle=target.closest('.part')?.querySelector('.section-toggle[aria-expanded=false]');toggle?.click();requestAnimationFrame(()=>target.scrollIntoView({block:'start'}));};
 window.__qtSelectPanel=selectPanel;
 document.querySelectorAll('.metric-card[href^="#"]').forEach(link=>link.addEventListener('click',()=>{const target=document.querySelector(link.getAttribute('href'));const owner=panels.find(([id])=>document.getElementById(id).contains(target));if(owner)selectPanel(owner[0]);}));
 document.querySelectorAll('.source-support a').forEach(link=>link.style.fontSize='11px');
 document.querySelectorAll('.qt-settings').forEach(section=>section.open=false);
 selectPanel(panels[0][0]);
});
'''

def document(body,controller):
    body=re.sub(r'<script[^>]*src=["\'][^"\']+["\'][^>]*></script>','',body)
    body=re.sub(r'<link[^>]*stylesheet[^>]*>','',body)
    body=body.replace('data-radar="off"','data-radar="on"').replace('data-data-mode="demo"','data-data-mode="live"')
    if controller=='dashboard.js':
        body=body.replace('>日频数据源<','>日频归档<').replace('>实时行情源<','>实时 Gateway<')
        body=body.replace('>数据说明 <','>观测数据配置与说明 <')
        config_view='<details class="qt-settings"><summary>观测配置与状态</summary><div class="source-content"><p>实时 Gateway：MATSFILE · SQLite，连接和数据库路径由 Aces 网关配置管理。IOPV 使用 IndexArb 当日 PCF 估值。</p><p>观测数据目录：由参数管理配置。ETF 日频、各市场模块、主动成交对照、分钟及秒级基准、回放日志分别读取对应归档。</p><p>归档来源与字段可在各图下查看。日频显示数据归属日期和覆盖，Gateway 状态独立展示。</p></div></details>'
        driver_view='''<section class="qt-driver" aria-label="监控驱动设置">
<div class="broad-controls"><label>正式监控驱动 <select id="driverMode"><option value="tick">Tick · 本地聚合</option><option value="bar">Bar · 标准分钟</option></select></label><button id="driverApply" type="button" disabled>应用切换</button><span id="driverStatus" role="status" aria-live="polite">当前生效 Tick 本地聚合（演示）</span></div>
<p class="data-note">切换针对正式告警驱动；候选、IOPV 和期货证据继续按各自输入更新。演示使用同一组固定样本。</p>
<details><summary>切换行为与演示条件</summary><p class="data-note">生产切换会检查来源、基准和交易日，准备目标模式独立会话并回放当日已完成数据。成功后切换显示，切换前数据按原模式归档。</p><label><input id="barFeedReady" type="checkbox" checked> 演示 Bar 输入可用</label></details></section>'''
        body=body.replace('</header>','</header>'+driver_view+config_view,1)
    body=body.replace('<head>','<head><style>'+read('src/style.css')+desktop_css+'</style>')
    body=re.sub(r'<div class="demo-notice"[^>]*>.*?</div>','',body)
    body=body.replace('<body>','<body><div class="demo-notice">完整 Web 内容映射预览 · 所有数值为演示样本 · Qt 将复用相同区域与交互</div>')
    return body.replace('</body>','<script>'+mock+'</script><script>'+read('src/echarts.min.js').replace('</script','<\\/script')+'</script>'+
        '<script src="'+module_url('presentation.js')+'"></script><script type="module" src="'+module_url(controller)+'"></script><script>'+native_ui+'</script></body>')

def select(id,options):
    return '<select id="'+id+'">'+''.join(f'<option value="{value}">{label}</option>' for value,label in options)+'</select>'

def details(text):return '<details class="gap-report"><summary>数据与计算口径</summary><p>'+text+'</p></details>'
def chart(id):return f'<section class="chart-wrap"><div class="chart" id="{id}"></div></section>'
def section(id,title,content,content_id=None):
    cid=content_id or id+'Content'
    return f'<section class="part" id="{id}"><h2 class="pt"><button class="section-toggle" aria-expanded="true" aria-controls="{cid}"><span>{title}</span><span class="collapse-label">收起</span></button></h2><div id="{cid}" class="section-content">{content}</div></section>'

sections=[('marketConcentration','成交额集中度'),('marketStyle','大小盘风格'),('marketIndustry','指数行业拥挤度'),('marketFunds','公募重仓'),('marketFinancing','融资与估值')]
nav=''.join(f'<a href="#{id}">{title}</a>' for id,title in sections)
chart_nav=''.join(f'<div class="directory-group"><a class="directory-section" href="#{id}">{title}</a>'+''.join(f'<a href="#{chart}">{label}</a>' for chart,label in links)+'</div>' for id,title,links in [
    ('marketConcentration','成交额集中度',[('marketConcentrationChart','集中度与指数')]),
    ('marketStyle','大小盘风格',[('marketCapitalChart','总市值比值'),('marketTurnoverChart','成交额比值')]),
    ('marketIndustry','指数行业拥挤度',[('marketIndustryChart','行业占比走势')]),
    ('marketFunds','公募重仓集中度',[('marketFundChart','重仓行业走势')]),
    ('marketFinancing','融资与市场估值',[('marketBalanceChart','融资余额与净增'),('marketLeverageChart','融资增速与占比'),('marketValuationChart','市值与宏观比值')])])
crowding='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#f2f6f6"><title>市场集中度监控</title></head><body>
<button class="nav-backdrop" id="navBackdrop" hidden></button><aside class="chart-sidebar" id="chartNavigation"><div class="sidebar-heading"><div><strong>图表导航</strong><span>市场拥挤度 · 全部区域</span></div><button id="chartNavClose">×</button></div><nav class="chart-directory">'''+chart_nav+'''</nav><a href="#top">返回概览</a></aside><main class="wrap"><header class="top" id="top"><div class="top-line"><span class="brand">市场集中度监控</span><div class="theme-switch"><button data-theme-choice="light">浅色</button><button data-theme-choice="dark">深色</button><button data-theme-choice="system">系统</button></div></div><div class="hero-heading"><h1>市场拥挤度观测台</h1><span class="market-label">沪深 A 股</span></div><div class="overview">'''
for id,title,foot,target in [('marketTop1','前 1% 成交额占比','marketTopDate','marketConcentration'),('marketPercentile','前 1% 历史分位','marketSample','marketConcentration'),('marketCapRatio','沪深300 / 中证1000 · 总市值','marketCapDate','marketCapitalChart'),('marketCashRatio','沪深300 / 中证1000 · 成交额','marketCashDate','marketTurnoverChart')]:
    crowding+=f'<a class="metric-card" href="#{target}"><span class="metric-label">{title}</span><strong id="{id}">—</strong><span class="metric-foot" id="{foot}"></span></a>'
crowding+='</div><p id="marketStatus" class="data-note"></p></header><nav class="section-nav"><div class="nav-sections">'+nav+'</div><button id="chartNavToggle">图表导航</button></nav>'
crowding+='<div class="broad-controls"><label>图表范围 '+select('marketDays',[(20,'近20交易日'),(60,'近60交易日'),(125,'近半年'),(250,'近一年'),(750,'近三年'),(1250,'近五年'),(2500,'近十年')]).replace('value="250"','value="250" selected')+'</label></div>'
crowding+=section('marketConcentration','A 股成交额集中度','<div class="broad-controls"><label>档位 '+select('marketTier',[(1,'前1%'),(3,'前3%'),(5,'前5%')])+'</label><label>叠加指数 '+select('marketIndex',[('','无')])+'</label></div>'+chart('marketConcentrationChart')+details('按每日沪深 A 股股票池计算，正成交额参与排名，档位股数向上取整。分母使用同股票池成交额。历史分位使用截至当日近五年有效样本，缺数日期留空。')+'<div class="table-wrap"><h3 id="marketTableTitle">日度明细</h3><label>表格范围 '+select('marketTableDays',[(5,'近5交易日'),(20,'近20交易日'),(60,'近60交易日'),(125,'近半年'),(250,'近一年')])+'</label><div class="tscroll"><table><thead><tr><th>交易日</th><th>入选股数</th><th>档位成交额（亿元）</th><th>集中度</th><th>历史分位</th><th>有成交 / 停牌 / 缺数</th></tr></thead><tbody id="marketTable"></tbody></table></div></div>')
crowding+=section('marketStyle','大小盘风格',chart('marketCapitalChart')+chart('marketTurnoverChart')+details('按各交易日有效指数成分汇总市值和成交额。均值与 ±1σ／±2σ 按当前区间有效数据计算。'))
crowding+=section('marketIndustry','指数行业拥挤度','<div class="broad-controls"><label>指数 '+select('industryIndex',[('000300.XSHG','沪深300'),('000905.XSHG','中证500')])+'</label><label>指标 '+select('industryMetric',[('capital','市值占比'),('turnover','成交额占比')])+'</label><label>行业 '+select('industryChoice',[('','每日第一行业')])+'</label></div><p id="industryStatus" class="data-note"></p>'+chart('marketIndustryChart')+details('按当日有效成分和申万一级分类汇总，剔除银行、非银金融、石油石化。沿用 Web 服务端分类与峰值口径。'))
crowding+=section('marketFunds','公募基金重仓集中度','<div class="broad-controls"><label>行业 '+select('fundIndustry',[('','每季第一行业')])+'</label></div><p id="fundStatus" class="data-note"></p>'+chart('marketFundChart')+details('合并同基金份额类别，按首次披露的季度前十大 A 股持仓市值计算行业占比，展示报告期、披露日期和基金覆盖。'),'marketFundContent')
crowding+=section('marketFinancing','融资与市场估值','<div class="broad-controls"><label>叠加指数 '+select('financeIndex',[('','无')])+'</label></div><p id="financingStatus" class="data-note"></p>'+chart('marketBalanceChart')+chart('marketLeverageChart')+chart('marketValuationChart')+'<p class="coverage-note">沪深 A 股市值 / 住户存款 · 待 Wind API</p>'+details('融资余额、近20日日均净增、周均同比及市值／流通市值／M2占比沿用 Web 口径。GDP取最近四个已公布季度，M2按公布日对齐。'),'marketFinanceContent')
crowding+='</main></body></html>'
for filename,content in [('src/index.html',read('src/index.html')),('src/crowding.html',crowding)]:
    original=set(re.findall(r'id="([^"]+)"',read(filename)))
    output=set(re.findall(r'id="([^"]+)"',content))
    assert original<=output,(filename,original-output)
pages={'etf':document(read('src/index.html'),'dashboard.js'),'market':document(crowding,'crowding.js')}
encoded={key:base64.b64encode(value.encode()).decode() for key,value in pages.items()}
wrapper='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aces · FtShare 全量窗口原型</title><style>
*{box-sizing:border-box}body{margin:0;font:13px 'Microsoft YaHei UI',sans-serif;background:#101a27;color:#e4edf7}header{height:58px;display:flex;align-items:center;gap:9px;padding:8px 18px;border-bottom:1px solid #344154}button,a{font:inherit;color:inherit;background:#1e3045;border:1px solid #40536a;border-radius:4px;padding:8px 12px;text-decoration:none;cursor:pointer}button[aria-pressed=true]{background:#21534b;border-color:#61b6a5}.brand{font-weight:700;padding-right:10px}.note{margin-left:auto;color:#dccb9d;font-size:12px}iframe{display:block;border:0;width:100%;height:calc(100vh - 58px);background:#f2f6f6}.standalone header{display:none}.standalone iframe{height:100vh}@media(max-width:800px){header{height:auto;flex-wrap:wrap}.note{width:100%;margin:0}iframe{height:calc(100vh - 115px)}}
</style></head><body><header><span class="brand">Aces · IndexArb</span><span>基差期限结构</span><button id="etf" aria-pressed="true">ETF异动监控</button><button id="market" aria-pressed="false">市场集中度监控</button><a id="independent" href="?window=etf" target="aces_etf_monitor">独立窗口 ↗</a><span class="note">完整 Web 功能预览 · 演示数据</span></header><iframe id="view" title="FtShare 完整监控窗口"></iframe><script>
const pages=PAGES;let mode=new URLSearchParams(location.search).get('window')||'etf';if(!pages[mode])mode='etf';if(new URLSearchParams(location.search).has('window'))document.body.classList.add('standalone');
const frame=document.getElementById('view');
function show(key){mode=key;for(const k of ['etf','market'])document.getElementById(k).setAttribute('aria-pressed',String(k===key));const a=document.getElementById('independent');a.href='?window='+key;a.target='aces_'+key+'_monitor';frame.srcdoc=new TextDecoder().decode(Uint8Array.from(atob(pages[key]),c=>c.charCodeAt(0)));document.title='Aces · '+(key==='etf'?'ETF异动监控':'市场集中度监控');}
document.getElementById('etf').onclick=()=>show('etf');document.getElementById('market').onclick=()=>show('market');show(mode);
</script></body></html>'''.replace('PAGES',js(encoded))
(ROOT/'docs/assets/mockups/aces-monitors.html').write_text(wrapper,encoding='utf-8')
print('Built complete prototype',len(wrapper),'characters')
