import { fundStartDate, cumulativeWindow, cumulativeExcess, FLOW_ISSUE_LABELS } from './flow-math.js';
import { createRadar } from './radar.js';
(async function(){
'use strict';
const $=id=>document.getElementById(id), charts={}, colors={red:'#d93630',green:'#0aa06e',blue:'#4c7bf3',orange:'#e8862e'};
const radar=document.documentElement.dataset.radar==='on'?createRadar($('radar')):null;
if(radar){$('radar').hidden=false;document.querySelectorAll('[data-radar-link]').forEach(e=>e.hidden=false);document.querySelector('.sidebar-heading div>span').textContent='异动雷达 · 9 张图表';if(location.hash==='#radar')requestAnimationFrame(()=>$('radar').scrollIntoView({block:'start'}));}
const finite=v=>v!==null&&v!==undefined&&Number.isFinite(Number(v));
const fmt=(v,n=2)=>finite(v)?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:n,maximumFractionDigits:n}):'--';
const sign=(v,n=2)=>finite(v)?(v>0?'+':'')+fmt(v,n):'--';
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cst=()=>seed?.demo?seed.demo.as_of+'T15:00:00.000Z':new Date(Date.now()+8*3600000).toISOString();
const storageKey=key=>'ftshare-oss:'+document.documentElement.dataset.dataMode+':'+key;
let broadSelection=null,broadResetZoom=false,broadView='flow',broadExpanded=false;
let seed,live,loading=false,remaining=1,picked='',brushLoaded=false;
async function get(path){const r=await fetch(path,{cache:'default',signal:AbortSignal.timeout(30000)});const d=await r.json();if(!r.ok)throw new Error(d.error||'数据暂不可用');if(document.documentElement.dataset.dataMode==='demo'?d.provider!=='Demo':!['FTShare','FTShare+PublicSources','RQData'].includes(d.provider))throw new Error('数据来源校验失败');return d;}
function matchingLive(value){
 const provider=seed.provider==='FTShare+PublicSources'?'FTShare':seed.provider;
 return value?.provider===provider&&value.slots?.length===seed.minute_slots.length&&value.slots.every((slot,i)=>slot===seed.minute_slots[i]);
}
function renderSource(){
 const rq=seed.provider==='RQData';
 const text=seed.demo?'演示数据：程序生成的行情与资金样本。':rq
   ?'行情：RQData。'+(seed.source_audit?.fund_provider==='RQData'?'净申赎按 RQData 份额与单位净值估算。':seed.latest_flow_date?'净申赎数据沿用既有归档。':'日度份额与净值待接入。')
   :'历史归档：'+seed.provider+'。净申赎估算按交易日期对齐。';
 document.querySelectorAll('footer').forEach(e=>e.textContent=text);
 document.querySelectorAll('.chart-source').forEach(e=>e.hidden=!rq);
}
function tag(id,text,kind=''){const e=$(id);if(e){e.textContent=text;e.className='tag '+kind;}}
function saved(key){try{return JSON.parse(localStorage.getItem(storageKey(key))||'null');}catch{return null;}}
function save(key,value){try{localStorage.setItem(storageKey(key),JSON.stringify(value));}catch{}}
function safely(label,job){try{return job();}catch(error){console.error(label+'暂不可用');return null;}}
function combineLive(next,prior){
 if(!prior||next.date!==prior.date||next.provider!==prior.provider||Boolean(next.replay)!==Boolean(prior.replay))return next;
 next.bars=next.bars.map((v,i)=>{if(finite(v))return v;if(finite(prior.bars?.[i])&&prior.coverage?.[i]===20){next.coverage[i]=20;return prior.bars[i];}return null;});
 next.closes=next.closes.map((v,i)=>finite(v)?v:prior.closes?.[i]??null);
 const old=new Map((prior.table||[]).map(r=>[r.symbol,r]));
 next.table=next.table.map(r=>{const p=old.get(r.symbol);return p?{...r,price:finite(r.price)?r.price:p.price,amount:finite(r.amount)?r.amount:p.amount,pct:finite(r.pct)?r.pct:p.pct}:r;});
 next.amount=next.table.length===20&&next.table.every(r=>finite(r.amount))?next.table.reduce((s,r)=>s+r.amount,0):null;
 next.pre_close=next.pre_close??prior.pre_close;
 next.last_complete_minute=next.slots.filter((t,i)=>finite(next.bars[i])&&next.coverage[i]===20).at(-1)||null;
 next.cumulative=cumulativeWindow(next.slots,next.bars).values;
 return next;
}
function chartTheme(){
 const dark=document.documentElement?.dataset?.theme==='dark';
 return dark?{dark:true,text:'#dfedf0',muted:'#91a8b2',grid:'#28414b',axis:'#49616b',surface:'#10242e',soft:'#142c36',accent:'#59d8bd',ink:'#e1f8f0',red:'#ff8a97',green:'#4ad6b0',blue:'#71a4ff',orange:'#f1bd63',purple:'#c1a0ff',categories:['#71a4ff','#48cbb5','#efbd6a','#b69ae7','#ef93aa','#79bad1','#dfaa79','#a2b4c0','#87ce9c','#d6c276','#a6a8f2','#dea3ce']}:{dark:false,text:'#163039',muted:'#6e8189',grid:'#e3ebee',axis:'#b7c9ce',surface:'#ffffff',soft:'#f7fafa',accent:'#087f73',ink:'#163039',red:'#ce4657',green:'#138b70',blue:'#3374e6',orange:'#d69b29',purple:'#8863ce',categories:['#2b6de0','#0a9d8f','#dc9635','#8b65c7','#d7607a','#5a92ab','#bc6e32','#6b7e8b','#54aa68','#b39b36','#7477d1','#c373ac']};
}
function styleChart(option){
 const p=chartTheme(),swatches={'#d93630':p.red,'#0aa06e':p.green,'#4c7bf3':p.blue,'#e8862e':p.orange,'#f0b429':p.orange,'#7c3aed':p.purple,'#1f2d3d':p.ink,'rgba(255,255,255,.94)':p.surface,'#dfe4eb':p.grid};
 function tint(value){if(!value||typeof value!=='object')return;for(const key of Object.keys(value)){const v=value[key];if(typeof v==='string'&&swatches[v])value[key]=swatches[v];else if(v&&typeof v==='object')tint(v);}}
 tint(option);option.darkMode=p.dark;option.backgroundColor='transparent';option.textStyle={...option.textStyle,color:p.text,fontFamily:'Inter, -apple-system, PingFang SC, Microsoft YaHei, sans-serif'};
 if(option.title)option.title.textStyle={...option.title.textStyle,color:p.text,fontSize:14,fontWeight:600,lineHeight:20};
 if(option.legend)option.legend={...option.legend,left:'center',top:40,orient:'horizontal',padding:[0,16],itemWidth:14,itemHeight:10,itemGap:18,textStyle:{color:p.muted,fontSize:11,lineHeight:18,verticalAlign:'middle'},pageTextStyle:{color:p.muted,fontSize:11,lineHeight:18},pageIconSize:10,pageButtonItemGap:6,pageButtonGap:16,pageIconColor:p.accent,pageIconInactiveColor:p.grid};
 if(option.tooltip)option.tooltip={...option.tooltip,backgroundColor:p.surface,borderColor:p.grid,borderWidth:1,padding:[10,13],textStyle:{color:p.text,fontSize:12},extraCssText:'box-shadow:0 8px 30px rgba(0,0,0,.14);border-radius:10px;'};
 const axis=a=>{if(!a)return;a.axisLabel={...a.axisLabel,color:p.muted,fontSize:11};a.nameTextStyle={...a.nameTextStyle,color:p.muted,fontSize:11};a.axisLine={...a.axisLine,lineStyle:{color:p.axis}};a.axisTick={show:false};a.splitLine={...a.splitLine,lineStyle:{color:p.grid}};};
 for(const a of Array.isArray(option.xAxis)?option.xAxis:[option.xAxis])axis(a);
 for(const [i,a] of (Array.isArray(option.yAxis)?option.yAxis:[option.yAxis]).entries()){axis(a);if(a){a.nameGap??=16;a.nameTextStyle={align:a.position==='right'||i>0?'right':'left',verticalAlign:'bottom',lineHeight:16,...a.nameTextStyle};}}
 for(const z of option.dataZoom||[])if(z.type==='slider'){Object.assign(z,{borderColor:p.grid,backgroundColor:p.soft,fillerColor:p.dark?'rgba(89,216,189,.13)':'rgba(8,127,115,.11)',dataBackground:{lineStyle:{color:p.axis},areaStyle:{color:p.grid}},selectedDataBackground:{lineStyle:{color:p.accent},areaStyle:{color:p.accent,opacity:.14}},handleStyle:{color:p.surface,borderColor:p.accent},moveHandleStyle:{color:p.axis},textStyle:{color:p.muted}});}
 return option;
}
function renderOverview(){
 if(!seed)return;
 const put=(id,value,signed=false)=>{const e=$(id);if(!e)return;e.innerHTML=(finite(value)?(signed?sign(value,1):fmt(value,1)):'—')+'<small>亿元</small>';e.className=finite(value)&&signed?(value>0?'positive':value<0?'negative':''):'';};
 const b=seed.broad,h=seed.history,lastMarket=h.amount.findLastIndex(finite);
 const amount=finite(live?.amount)?live.amount:h.amount[lastMarket];
 const amountDate=finite(live?.amount)?live.date:h.dates[lastMarket];
 put('overviewAmount',amount);
 $('overviewAmountDate').textContent=amountDate?amountDate+' · 20只核心 ETF':'等待当日行情';
 const memberDate=live?.date||seed.latest_market_date||b.dates.at(-1);
 const expected=seed.universe.filter(f=>fundStartDate(f)<=memberDate).length;
 $('overviewCoverage').textContent=expected;$('overviewCoverageNote').textContent=b.cats.length+'类宽基指数 · 已上市成员';
 const idx=b.total.findLastIndex(finite);
 const date=b.dates[idx],year=date?.slice(0,4),ids=b.dates.map((d,i)=>i).filter(i=>year&&b.dates[i].startsWith(year)&&b.dates[i]<=date);
 const annual=ids.length?cumulativeWindow(ids.map(i=>b.dates[i]),ids.map(i=>b.total[i])).values.at(-1):null;
 put('overviewFlow',b.total[idx],true);put('overviewYear',annual,true);
 $('overviewFlowDate').textContent=date?date+' · 宽基 ETF':'等待日度数据';
 $('overviewYearLabel').textContent=year?year+'年宽基累计':'宽基年度累计';
 $('overviewYearDate').textContent=date?'年初至 '+date:'等待日度数据';
}
function base(title,dates,yName='亿元'){
 return {animation:false,title:{text:title,left:8,top:6,padding:0,textStyle:{fontSize:14}},grid:{top:100,left:16,right:16,bottom:54,containLabel:true},legend:{top:40,left:'center',type:'scroll'},tooltip:{trigger:'axis',confine:true,formatter:ps=>[ps[0]?.axisValue,...ps.map(p=>escape(p.seriesName)+'：'+sign(p.value,2))].join('<br>')},xAxis:{type:'category',data:dates,axisLabel:{hideOverlap:true}},yAxis:{type:'value',name:yName,scale:true},dataZoom:[{type:'inside',moveOnMouseMove:false},{type:'slider',height:16,bottom:12}],series:[]};
}
function draw(id,option){const e=$(id);if(!e)return;return safely(id,()=>{const previous=charts[id]?.getOption()?.dataZoom;const c=charts[id]||(charts[id]=echarts.init(e));if(previous&&option.dataZoom&&!(id==='chartBroad'&&broadResetZoom))option.dataZoom.forEach((z,i)=>{if(previous[i]){z.start=previous[i].start;z.end=previous[i].end;}});c.setOption(styleChart(option),{notMerge:true});return c;});}
function line(name,data,color,axis=0,dashed=false){return {name,type:'line',data,yAxisIndex:axis,connectNulls:false,showSymbol:false,lineStyle:{width:1.8,color,type:dashed?'dashed':'solid'},itemStyle:{color}};}
function bars(name,data,color=colors.blue){return {name,type:'bar',itemStyle:{color:color==='flow'?colors.red:color},data:data.map(v=>({value:v,itemStyle:{color:color==='flow'?(v<0?colors.green:colors.red):color}}))};}
function addIndex(o,idx){o.yAxis=[{type:'value',name:'亿元',scale:true},{type:'value',name:'上证指数（点）',scale:true,splitLine:{show:false}}];o.series.push(line('上证综指',idx,colors.orange,1));return o;}
function flowRows(){return seed.history.dates.map((d,i)=>({date:d,amount:seed.history.amount[i],flow:seed.history.netflow[i],idx:seed.history.idx[i]})).filter(r=>finite(r.flow));}
function supportRows(){return flowRows().filter(r=>r.flow>100&&!(r.date>='2024-09-24'&&r.date<='2024-10-18')&&!(r.date>='2026-01-14'&&r.date<='2026-01-30'));}
function renderDaily(){
 const h=seed.history, volumeDays=h.dates.map((d,i)=>({date:d,amount:h.amount[i],idx:h.idx[i]})).filter(r=>finite(r.amount));
 let o=base('20只ETF日度成交额与上证综指',volumeDays.map(r=>r.date));o.series=[bars('成交额合计',volumeDays.map(r=>r.amount))];draw('chartDAmt',addIndex(o,volumeDays.map(r=>r.idx)));
 const fs=flowRows();o=base('20只ETF日度净申购估算与上证综指',fs.map(r=>r.date));o.series=[bars('净申购估算',fs.map(r=>r.flow),'flow')];draw('chartDNf',addIndex(o,fs.map(r=>r.idx)));
 const last=fs.at(-1);tag('tagNf',last?'最新净申购估算 '+last.date+'：'+sign(last.flow,1)+'亿':'日度份额与净值待发布',last?'':'warn');
 $('dailyRange').textContent=last?'资金数据：'+fs[0].date+' 至 '+last.date+'；采用当前20只篮子回溯。':'资金数据待发布';
 safely('放量信号',renderSignals);safely('分钟对比',renderCases);safely('宽基资金',renderBroad);safely('市场概览',renderOverview);
}
function renderSignals(){
 const fs=flowRows(), recent=fs.slice(-3), support=supportRows(), selected=new Map(support.map(r=>[r.date,r]));recent.forEach(r=>selected.set(r.date,r));
 const rows=[...selected.values()].sort((a,b)=>a.date.localeCompare(b.date));
 if(live&&finite(live.amount)&&!selected.has(live.date))rows.push({date:live.date,amount:live.amount,flow:null});
 for(const [id,key,title,color] of [['chartAmt','amount','放量日与近期交易日 · 成交额',colors.blue],['chartNf','flow','放量日与近期交易日 · 净申购估算',colors.red]]){
  const o=base(title,rows.map(r=>r.date));o.series=[{name:key==='flow'?'净申购估算':'成交额',type:'bar',itemStyle:{color},data:rows.map(r=>({value:r[key],itemStyle:{color:recent.some(x=>x.date===r.date)||r.date===live?.date?'#f0b429':color}}))}];
  const pending=rows.filter(r=>!finite(r[key])).at(-1);if(pending)o.series[0].markArea={silent:true,itemStyle:{color:'rgba(138,147,163,.14)'},label:{show:true,formatter:'待发布'},data:[[{xAxis:pending.date},{xAxis:pending.date}]]};
  draw(id,o);
 }
 tag('tagAmt',live?'成交额 '+live.date+'：'+fmt(live.amount,1)+'亿':'正在获取实时成交额',live?'live':'');
}
function renderCases(){
 const today=cst().slice(0,10),current=live?.date===today;
 const minuteValues=new Map(current?live.slots.map((t,i)=>[t,live.coverage[i]===20&&t<=live.last_complete_minute&&finite(live.bars[i])?live.bars[i]:null]):[]);
 const todayBars=seed.minute_slots.map(t=>minuteValues.get(t)??null),hasToday=todayBars.some(finite);
 tag('tagCaseLive',seed.demo?'演示分钟样本 '+today+' ｜ 固定演示样本':hasToday?'今日 '+today+' ｜ 分钟成交额截至 '+live.last_complete_minute+' ｜ 20/20只 ｜ 盘中每60秒刷新':'今日 '+today+' 分钟行情待更新'+(live?' ｜ 最近行情 '+live.date:''),hasToday?'ok':'warn');
 const flows=Object.fromEntries(flowRows().map(r=>[r.date,r.flow]));
 const entries=Object.entries(seed.minutes).filter(([d,m])=>d<today&&finite(flows[d])&&!m.quality&&m.bars.every(finite));
 const eventDates=new Set(supportRows().map(r=>r.date)),sup=entries.filter(([d])=>eventDates.has(d)),normal=entries.filter(([d])=>!eventDates.has(d)&&flows[d]<=100);
 const avg=entries=>seed.minute_slots.map((_,i)=>entries.length?entries.reduce((s,[,m])=>s+m.bars[i],0)/entries.length:null);
 const s=avg(sup),n=avg(normal);
 for(const [id,title,a,bc] of [['chartTypCase','放量案例日 · 重点ETF分钟成交额',s,colors.red],['chartTypNorm','常态交易日 · 重点ETF分钟成交额',n,colors.blue]]){
  const o=base(title,seed.minute_slots,'亿元/分钟'),event=id==='chartTypCase';
  o.xAxis.axisLabel.interval=14;
  o.yAxis={type:'value',name:'亿元/分钟',min:0,max:v=>Math.max(15,Math.ceil(v.max/3)*3),nameTextStyle:{align:'left'}};
  o.series=[bars(event?'历史放量日均值':'历史常态日均值',a,bc)];
  if(event){
   const excess=cumulativeExcess(s,n);
   o.yAxis=[o.yAxis,{type:'value',name:'较常态日多成交（亿元）',position:'right',min:v=>Math.min(0,Math.floor(v.min/50)*50),max:v=>Math.max(250,Math.ceil(v.max/50)*50),splitLine:{show:false},nameTextStyle:{align:'right'}}];
   o.series.push(line('较常态日多成交',excess,colors.orange,1));
  }
  if(hasToday)o.series.push({...line('今日分钟成交额（'+today+'）',todayBars,'#7c3aed'),lineStyle:{width:2.4,color:'#7c3aed'},z:5});
  o.tooltip.formatter=ps=>[ps[0]?.axisValue,...ps.map(p=>escape(p.seriesName)+'：'+fmt(p.value,2)+(p.seriesName==='较常态日多成交'?' 亿元':' 亿/分钟'))].join('<br>');
  draw(id,o);
 }
 const sampleDates=[...sup,...normal].map(([d])=>d).sort();
 $('caseRange').textContent='历史样本'+(sampleDates.length?' '+sampleDates[0]+' 至 '+sampleDates.at(-1):'待补齐')+'：放量日 '+sup.length+' 日（净申购估算>100亿）；常态日 '+normal.length+' 日（≤100亿）。样本仅含完整分钟与资金数据，今日不纳入历史均值。紫线为今日分钟成交额，使用左轴；尚未发生或数据不完整的分钟留空。';
}
function broadPeriodStart(first,last,period){
 if(period==='ytd')return [first,last.slice(0,4)+'-01-01'].sort().at(-1);
 if(!Number(period))return first;
 const date=new Date(last+'T00:00:00Z'),day=date.getUTCDate();
 date.setUTCDate(1);
 date.setUTCMonth(date.getUTCMonth()-Number(period));
 const monthEnd=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate();
 date.setUTCDate(Math.min(day,monthEnd));
 return [first,date.toISOString().slice(0,10)].sort().at(-1);
}
function renderBroadMetric(b,metric,ids,dates,rows){
 const premium=broadView==='premium',title=premium?'收盘折溢价':'日末规模',unit=premium?'%':'亿元';
 const category=$('broadCategory').value,keys=category?[category]:b.cats;
 const expected=i=>keys.reduce((sum,c)=>sum+b.coverage[c].expected[i],0);
 const valid=i=>keys.reduce((sum,c)=>sum+metric.valid[c][i],0);
 const values=category?metric.series[category]:metric.total;
 const format=v=>!finite(v)?'--':premium?sign(v,3)+'%':fmt(v,2);
 const latest=ids.at(-1),shown=ids.filter(i=>finite(values[i])).length;
 $('broadCoverage').innerHTML=[['统计区间',dates.length?dates[0]+' — '+dates.at(-1):'无交易日'],['有数据交易日',shown+' / '+ids.length],['区间末日有效成员',latest==null?'--':valid(latest)+' / '+expected(latest)],['区间末日'+title,format(values[latest])]].map(([name,value])=>'<div><span>'+name+'</span><strong>'+value+'</strong></div>').join('');
 const incomplete=ids.some(i=>valid(i)<expected(i));
 $('broadCumulativeNote').className='coverage-note'+(incomplete?' has-gaps':'');
 $('broadCumulativeNote').textContent=premium?'当日有成交的 ETF 不复权收盘价 ÷ 同日原始单位净值 − 1。对有效成员等权平均，表中列出样本覆盖。':'同日基金份额 × 原始单位净值。所选范围任一已上市成员缺数时，当日规模留空。';
 $('broadIndexNote').textContent='';
 const fresh=b.dates.indexOf(metric.latest),covered=valid(fresh),target=expected(fresh);
 tag('tagBroad',metric.latest+' ｜ 有效成员 '+covered+'/'+target+'只',covered===target?'ok':'warn');
 const o=base((category||'全部跟踪类别')+' · '+title,dates,unit);
 o.yAxis.scale=true;
 o.series=[line(premium?'平均折溢价':title,ids.map(i=>values[i]),colors.blue)];
 o.tooltip.formatter=ps=>{const p=ps[0];if(!p)return '';const i=ids[p.dataIndex];return p.axisValue+'<br>'+title+'：'+format(p.value)+(premium?'':' '+unit)+'<br>有效成员 '+valid(i)+' / '+expected(i)+'只';};
 draw('chartBroad',o);
 $('broadTableTitle').textContent=title+'明细（'+unit+'）';
 $('broadTable').innerHTML='<thead><tr><th>日期</th><th class="num">'+title+'</th><th class="num">有效 / 应纳入</th></tr></thead><tbody>'+rows.map(i=>'<tr><td>'+b.dates[i]+'</td><td class="num">'+format(values[i])+'</td><td class="num">'+valid(i)+' / '+expected(i)+'</td></tr>').join('')+'</tbody>';
 if(premium&&dates.includes(metric.latest)){
  const names=new Map(seed.universe.map(f=>[f.symbol,f.name]));
  const members=metric.members.filter(r=>!category||r.category===category).sort((a,b)=>b.value-a.value);
  $('broadMembers').hidden=!members.length;
  $('broadMembersTitle').textContent=metric.latest+' · 单只 ETF 收盘折溢价（'+members.length+' 只）';
  $('broadMembersTable').innerHTML='<thead><tr><th>基金</th><th>类别</th><th class="num">折溢价率</th></tr></thead><tbody>'+members.map(r=>'<tr><td>'+escape(r.symbol+' '+(names.get(r.symbol)||''))+'</td><td>'+escape(r.category)+'</td><td class="num">'+format(r.value)+'</td></tr>').join('')+'</tbody>';
 }
}
function renderBroad(){
 const b=seed.broad,metric=seed.broad_metrics?.[broadView],isMetric=['size','premium'].includes(broadView);
 const latestDate=isMetric?metric?.latest:seed.latest_flow_date;
 const available=b.dates.map((d,i)=>i).filter(i=>latestDate&&b.dates[i]<=latestDate);
 $('broadCategoryField').hidden=!isMetric;
 $('broadRecent').hidden=isMetric;
 $('broadMembers').hidden=true;
 $('broadGapReport').hidden=true;
 $('broadExpand').hidden=true;
 if(!$('broadCategory').options[1]){
  for(const c of b.cats)$('broadCategory').add(new Option(c,c));
 }
 if(!available.length){
  const message=isMetric?'当前快照暂无此指标，请更新 RQData 历史快照。':'日度份额与净值待发布';
  tag('tagBroad',message,'warn');
  $('broadCumulativeNote').textContent=message;
  $('broadCoverage').innerHTML=$('broadIndexNote').textContent=$('broadTableTitle').textContent=$('broadTable').innerHTML='';
  $('broadYears').innerHTML='';
  for(const id of ['broadStart','broadEnd'])$(id).value='';
  charts.chartBroad?.clear();
  return;
 }
 const first=b.dates[available[0]],lastDate=b.dates[available.at(-1)];
 const years=[...new Set(available.map(i=>b.dates[i].slice(0,4)))];
 $('broadYears').innerHTML=years.map(y=>'<button type="button" data-year="'+y+'">'+y+'年</button>').join('');
 for(const button of $('broadYears').querySelectorAll('button'))button.addEventListener('click',()=>{const ds=available.map(i=>b.dates[i]).filter(d=>d.startsWith(button.dataset.year));setBroadRange(ds[0],ds.at(-1));});
 const from=broadSelection?.start||broadPeriodStart(first,lastDate,$('broadPeriod').value),to=broadSelection?.end||lastDate;
 for(const id of ['broadStart','broadEnd']){$(id).min=first;$(id).max=lastDate;}
 $('broadStart').value=from;$('broadEnd').value=to;
 const ids=available.filter(i=>b.dates[i]>=from&&b.dates[i]<=to),dates=ids.map(i=>b.dates[i]);
 const rows=(broadExpanded?ids:ids.slice(-22)).slice().reverse();
 $('broadExpand').hidden=ids.length<=22;
 $('broadExpand').textContent=broadExpanded?'仅显示最近 22 日':'显示区间全部 '+ids.length+' 日';
 $('broadExpand').setAttribute('aria-expanded',String(broadExpanded));
 if(isMetric){
  renderBroadMetric(b,metric,ids,dates,rows);
  broadResetZoom=false;
  if(from>to)$('broadCumulativeNote').textContent='起始日期不能晚于结束日期。';
  return;
 }
 const coverage=cumulativeWindow(dates,ids.map(i=>b.total[i]));
 $('broadCoverage').innerHTML=[['统计区间',dates.length?dates[0]+' — '+dates.at(-1):'无交易日'],['交易日',dates.length+'天'],['数据完整',coverage.completeDays+'天'],['数据不完整',coverage.missingDays+'天']].map(([name,value])=>'<div><span>'+name+'</span><strong>'+value+'</strong></div>').join('');
 const early=first>'2026-01-05'?'本图累计区间从'+first+'开始。':'';
 const cumulativeLast=coverage.cumulativeThrough?coverage.values[dates.indexOf(coverage.cumulativeThrough)]:null;
 const prefix=from>to?'起始日期不能晚于结束日期。':!dates.length?'所选区间无可用交易日。':coverage.missingDays?(coverage.cumulativeThrough?'累计仅计算至'+coverage.cumulativeThrough+'：'+sign(cumulativeLast,1)+'亿元；'+coverage.firstMissingDate+'起存在缺口，后续累计停止。':'区间首日数据不完整，无法计算本区间累计。'):'区间累计自首个交易日起计算，截至'+coverage.cumulativeThrough+'：'+sign(cumulativeLast,1)+'亿元。';
 $('broadCumulativeNote').textContent=prefix+(coverage.missingDays?' 缺失日不补零、不跳过续接。':'')+early;
 $('broadCumulativeNote').className='coverage-note'+(coverage.missingDays||early?' has-gaps':'');
 const o=base(from===first&&to===lastDate?first.slice(0,4)+'年以来 宽基ETF日度净流入与累计净流入':from.slice(0,4)===to.slice(0,4)?from.slice(0,4)+'年 · 宽基ETF日度净流入与累计净流入':'所选区间 · 宽基ETF日度净流入与累计净流入',dates);o.legend={...o.legend,itemWidth:12,itemHeight:10,data:['累计净流入','上证指数',...b.cats,...(coverage.missingDays?['数据不完整']:[])]};o.grid.right=90;o.yAxis=[{type:'value',name:'当日净流入（亿元）',scale:true,nameTextStyle:{align:'left'}},{type:'value',name:'累计净流入（亿元）',scale:true,position:'right',splitLine:{show:false},nameTextStyle:{align:'right'}},{type:'value',name:'上证指数（点）',scale:true,position:'right',offset:76,nameLocation:'end',nameRotate:0,nameGap:16,nameTextStyle:{align:'left',verticalAlign:'bottom'},splitLine:{show:false}}];
 const totalView=broadView==='total';
 if(totalView)o.title.text='宽基 ETF 日度净申赎合计';
 if(totalView)o.legend.data.splice(2,b.cats.length,'日度净申赎合计');
 o.series=totalView?[bars('日度净申赎合计',ids.map(i=>b.total[i]),'flow')]:b.cats.map((c,i)=>({name:c,type:'bar',stack:'broad',data:ids.map(j=>b.series[c][j]),itemStyle:{color:chartTheme().categories[i]}}));
 const cumulativeLine=line('累计净流入',coverage.values,'#1f2d3d',1);
 if(coverage.cumulativeThrough)cumulativeLine.markPoint={symbol:'circle',symbolSize:6,label:{show:true,position:coverage.cumulativeThrough===dates.at(-1)?'left':'top',distance:8,formatter:coverage.cumulativeThrough+'\n累计 '+sign(cumulativeLast,1)+'亿元',fontSize:11,color:'#1f2d3d',backgroundColor:'rgba(255,255,255,.94)',padding:[4,6],borderColor:'#dfe4eb',borderWidth:1},data:[{coord:[coverage.cumulativeThrough,cumulativeLast]}]};
 const indexByDate=new Map(seed.history.dates.map((d,i)=>[d,seed.history.idx[i]]));
 const indexValues=dates.map(d=>indexByDate.get(d)??null),indexDates=dates.filter((d,i)=>finite(indexValues[i]));
 $('broadIndexNote').textContent='橙色虚线：上证指数日收盘价（点，最右轴）'+(indexDates.length?'；'+indexDates[0]+' 至 '+indexDates.at(-1):'；所选区间暂无指数数据')+(indexDates.length<dates.length?'，缺失日期留空。':'。');
 o.series.push(cumulativeLine,{...line('上证指数',indexValues,colors.orange,2,true),z:4});
 if(coverage.missingDays)o.series.push({name:'数据不完整',type:'scatter',data:ids.map(i=>finite(b.total[i])?null:0),symbol:'diamond',symbolSize:7,itemStyle:{color:'#bd7c19'},z:5});
 o.tooltip.formatter=ps=>{const ix=ids[ps[0]?.dataIndex],out=[ps[0]?.axisValue];for(const p of ps){if(p.seriesType==='bar'){const c=b.coverage[p.seriesName];out.push(escape(p.seriesName)+'：'+sign(p.value,2)+'亿'+(c?'（'+c.valid[ix]+'/'+c.expected[ix]+'只）':''));}else if(p.seriesType==='scatter')out.push('当日分类数据不完整，详见日期说明');else if(p.seriesName==='上证指数')out.push('上证指数：'+fmt(p.value,2)+'点');else out.push('累计净流入：'+sign(p.value,1)+'亿');}return out.join('<br>');};draw('chartBroad',o);broadResetZoom=false;
 const latest=available.at(-1),valid=b.cats.reduce((s,c)=>s+b.coverage[c].valid[latest],0),expected=b.cats.reduce((s,c)=>s+b.coverage[c].expected[latest],0);
 tag('tagBroad',b.dates[latest]+' ｜ 已上市成员有效数据 '+valid+'/'+expected+'只',valid===expected?'ok':'warn');
 const cols=totalView?['合计']:[...b.cats,'合计'],val=(i,c)=>c==='合计'?b.total[i]:b.series[c][i],max=Object.fromEntries(cols.map(c=>[c,Math.max(1,...rows.map(i=>Math.abs(val(i,c)||0)))]));
 $('broadTableTitle').textContent=(totalView?'日度净申赎合计':'分类净申赎估算')+'（亿元）';
 $('broadTable').innerHTML='<thead><tr><th>日期</th>'+cols.map(c=>'<th class="num">'+escape(c)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(i=>'<tr><td>'+b.dates[i]+'</td>'+cols.map(c=>{const v=val(i,c),a=finite(v)?(.06+.5*Math.min(1,Math.abs(v)/max[c])):0,style=finite(v)&&v!==0?'background:rgba('+(v>0?'217,54,48,':'10,160,110,')+a+');':'';return '<td class="num" style="'+style+'">'+sign(v,c==='合计'?1:2)+'</td>';}).join('')+'</tr>').join('')+'</tbody>';
 $('broadGapReport').hidden=coverage.missingDays===0;
 const totals=cols.map(c=>{const values=ids.map(i=>val(i,c));return values.length&&values.every(finite)?values.reduce((s,v)=>s+v,0):null;});
 $('broadTable').insertAdjacentHTML('beforeend','<tfoot><tr><th scope="row">区间合计</th>'+totals.map(v=>'<td class="num">'+sign(v,2)+'</td>').join('')+'</tr></tfoot>');
 $('broadGapSummary').textContent='查看'+coverage.missingDays+'个不完整交易日与原因';
 const gapRows=coverage.missingDates.flatMap(d=>{const issues=b.issues?.[d];return issues?.length?issues.map(v=>({date:d,...v})):[{date:d,symbol:'--',name:'--',bucket:'--',reasons:['source_missing']}];});
 $('broadGapTable').innerHTML='<thead><tr><th>日期</th><th>基金</th><th>类别</th><th>原因</th></tr></thead><tbody>'+gapRows.map(r=>'<tr><td>'+r.date+'</td><td>'+escape(r.symbol+' '+r.name)+'</td><td>'+escape(r.bucket)+'</td><td>'+escape(r.reasons.map(v=>FLOW_ISSUE_LABELS[v]||'记录需核对').join('；'))+'</td></tr>').join('')+'</tbody>';
}
function setBroadRange(start,end){
 broadResetZoom=true;broadExpanded=false;broadSelection={start,end};
 $('broadPeriod').value='custom';
 renderBroad();
}
function renderMinute(){
 if(!live)return;safely('市场概览',renderOverview);
 let o=base('20只ETF分钟成交额与上证综指',live.slots,'亿元/分钟');o.series=[bars('分钟成交额合计',live.bars)];o=addIndex(o,live.closes);o.yAxis[0].name='亿元/分钟';if(live.pre_close)o.series[1].markLine={silent:true,symbol:['none','none'],data:[{yAxis:live.pre_close}],label:{formatter:'昨收 '+fmt(live.pre_close)},lineStyle:{type:'dashed',color:'#999'}};draw('chart',o);
 const total=live.table.every(r=>finite(r.amount))?live.table.reduce((s,r)=>s+r.amount,0):null,max=Math.max(1,...live.table.map(r=>r.amount||0)),absMax=Math.max(0,...live.table.filter(r=>finite(r.pct)).map(r=>Math.abs(r.pct)));
 $('etfTable').innerHTML=[...live.table].sort((a,b)=>(b.amount||0)-(a.amount||0)).map(r=>{
  const pctStyle=finite(r.pct)&&absMax?'background:rgba('+(r.pct>0?'217,54,48':r.pct<0?'10,160,110':'120,130,145')+','+(.10+.58*Math.min(1,Math.abs(r.pct)/absMax)).toFixed(3)+');':'';
  return '<tr><td>'+r.code+'</td><td>'+escape(r.name)+'</td><td class="num amt-cell"><span class="amt-bg" style="width:'+Math.max(0,(r.amount||0)/max*100).toFixed(1)+'%"></span><span class="amt-val">'+fmt(r.amount)+'</span></td><td class="num">'+(finite(r.amount)&&total?fmt(r.amount/total*100,1)+'%':'--')+'</td><td class="num pct-cell" style="'+pctStyle+'">'+(finite(r.pct)?sign(r.pct)+'%':'--')+'</td></tr>';
 }).join('');
 tag('tagDate',live.date+' 分时');const n=live.coverage.filter(v=>v===20).length;tag('tagUpdate','行情截至 '+(live.last_complete_minute||'--')+' ｜ '+(n?'20/20只':'数据补齐中')+' ｜ 更新 '+new Date(live.fetched_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'}),n?'ok':'warn');if(live.warnings?.length)tag('tagUpdate','部分行情暂缺；保留已成功取得的数据，其他图表正常展示','warn');
 renderCum();renderSignals();
}
function renderCum(){
 const desired=[...new Set([...seed.calendar,...(live?.trading_dates||[])])].filter(d=>d<(live?.date||cst().slice(0,10))).sort().slice(-20);
 const recent=desired.map(d=>[d,seed.minutes[d]]).filter(([,m])=>m&&!m.quality&&m.cumulative.every(finite));
 const average=seed.minute_slots.map((_,i)=>recent.length?recent.reduce((s,[,m])=>s+m.cumulative[i],0)/recent.length:null);
 const o=base('20只ETF分钟累计成交额',seed.minute_slots);o.yAxis.min=0;o.series=[];
 if(live)o.series.push(line(live.date===cst().slice(0,10)?'今日':live.date,live.cumulative,colors.red));
 if(picked&&seed.minutes[picked])o.series.push(line(picked,seed.minutes[picked].cumulative,colors.orange));
 o.series.push(line('近'+recent.length+'个完整交易日均值',average,colors.blue,0,true));draw('chartCum',o);
}
function historyIndex(date){
 const h=seed.history;let i=h.dates.indexOf(date);if(i>=0)return i;i=h.dates.findIndex(d=>d>date);if(i<0)i=h.dates.length;
 h.dates.splice(i,0,date);for(const k of ['amount','idx','netflow'])h[k].splice(i,0,null);h.flow_coverage.splice(i,0,0);h.flow_expected.splice(i,0,20);return i;
}
function mergeLiveHistory(){
 if(!live||(live.provider==='RQData'&&live.date<=seed.latest_market_date))return;const h=seed.history,i=historyIndex(live.date),index=live.closes.filter(finite).at(-1)??null;
 h.amount[i]=live.amount;h.idx[i]=index;
 if(live.bars.every(finite))seed.minutes[live.date]={bars:live.bars,cumulative:live.cumulative};
}
function renderCloudStatus(){
 const e=$('cloudUpdateStatus');e.hidden=false;const date=seed.cloud_update?.collected_at;
 e.textContent='日度数据随后台快照更新，盘中行情约每分钟读取一次缓存。'+(date?' 最近成功归档：'+new Date(date).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'');
}
async function refresh(){
 if(loading)return;loading=true;
 try{
  const health=await get('/api/health'),switched=(health.mode==='demo')!==Boolean(seed.demo);
  if(switched||health.history_built_at!==seed.built_at){seed=await get('/api/board');if(!matchingLive(live))live=null;save('ftshare-board-v1',seed);renderSource();renderCloudStatus();renderDaily();for(const d of Object.keys(seed.minutes).sort().reverse())if(!$('cumDate').querySelector('option[value="'+d+'"]')){const op=document.createElement('option');op.value=d;op.textContent=d;$('cumDate').insertBefore(op,$('cumDate').options[1]||null);}}
  const next=await get('/api/live');
  if(!matchingLive(next))throw new Error('行情与历史快照口径不一致');
  live=combineLive(next,live);if(!live.replay)save('ftshare-last-live-v1',live);safely('当日记录',mergeLiveHistory);safely('实时图表',renderMinute);safely('日度图表',renderDaily);if(radar)safely('异动雷达',()=>radar.render(seed,live));
  if(live.replay)tag('tagUpdate','历史回放 '+live.date+' '+live.last_complete_minute,'warn');
  else if(live.provider==='RQData'&&live.cache?.stale)tag('tagUpdate','行情已陈旧 · '+live.date+' '+live.last_complete_minute,'warn');
  else if(live.feed==='push')tag('tagUpdate','RQData 实时推送 · 分钟截至 '+live.last_complete_minute,'ok');
  if(live.cache?.last_error&&live.cache.last_error!=='partial_upstream')tag('tagUpdate','云端采集暂缓；行情保留至 '+live.date+' '+(live.last_complete_minute||'--'),'warn');
}catch(e){tag('tagUpdate',live?'缓存读取暂缓；保留 '+live.date+' '+(live.last_complete_minute||'')+' 的成功数据':'分时和成交雷达等待盘中采集；日度及历史图表可继续查看','warn');}
 finally{loading=false;}
}
function tick(){
 if(loading)return;
 if(live&&live.date&&!live.replay&&live.date!==cst().slice(0,10)){live=null;location.reload();return;}
 remaining--;const hm=cst().slice(11,16),day=new Date(Date.now()+8*3600000).getUTCDay(),trading=day>0&&day<6&&((hm>='09:15'&&hm<='11:35')||(hm>='12:55'&&hm<='15:10'));
 if(seed.demo)tag('tagCountdown','离线演示 · 固定样本 · '+Math.max(0,remaining)+'秒后检查');
 else tag('tagCountdown',(trading?'盘中':'非交易时段')+' · '+Math.max(0,remaining)+'秒后读取缓存');
 if(remaining<=0){remaining=trading?60:300;refresh();}
}
window.addEventListener('ftshare:theme-change',()=>{if(!seed)return;safely('主题切换',renderDaily);if(live)safely('实时图表',renderMinute);safely('累计图表',renderCum);});
try{
 try{seed=await get('/api/board');save('ftshare-board-v1',seed);}catch(error){seed=saved('ftshare-board-v1');if(!seed?.broad||!seed?.history)throw error;tag('tagUpdate','正在显示上次成功加载的历史数据','warn');}
 renderSource();
 const sourceNote=document.querySelector('#sourcePolicy');if(sourceNote)sourceNote.textContent=seed.demo?'离线生成的演示样本。':'网页读取后台发布的历史快照和盘中缓存，成员及分类使用项目配置。';
 renderCloudStatus();
 if(!$('etfTable').innerHTML)$('etfTable').innerHTML=seed.universe.filter(f=>f.is_core_basket).map(f=>'<tr><td>'+escape(f.symbol.slice(0,6))+'</td><td>'+escape(f.name)+'</td><td class="num">--</td><td class="num">--</td><td class="num">--</td></tr>').join('');
 $('universeText').textContent='宽基分类：'+seed.universe.length+'只ETF，按当前跟踪指数分组回溯（含增强ETF），从上市日起纳入。累计遇缺失日停止，日期与原因可展开查看。';
 for(const id of ['broadStart','broadEnd'])$(id).addEventListener('change',()=>setBroadRange($('broadStart').value,$('broadEnd').value));
 $('broadPeriod').addEventListener('change',()=>{
  if($('broadPeriod').value==='custom'){setBroadRange($('broadStart').value,$('broadEnd').value);return;}
  broadResetZoom=true;broadSelection=null;broadExpanded=false;renderBroad();
 });
 $('broadAll').addEventListener('click',()=>{$('broadPeriod').value='all';$('broadPeriod').dispatchEvent(new Event('change'));});
 $('broadRecent').addEventListener('click',()=>{const b=seed.broad,ids=b.dates.map((d,i)=>i).filter(i=>b.dates[i]<=seed.latest_flow_date&&b.dates[i]>=$('broadStart').value&&b.dates[i]<=$('broadEnd').value);const range=cumulativeWindow(ids.map(i=>b.dates[i]),ids.map(i=>b.total[i])).lastCompleteRange;if(range)setBroadRange(range.start,range.end);});
 $('broadCategory').addEventListener('change',()=>{broadExpanded=false;renderBroad();});
 $('broadExpand').addEventListener('click',()=>{broadExpanded=!broadExpanded;renderBroad();});
 const views=[...document.querySelectorAll('.broad-views button')];
 for(const button of views)button.addEventListener('click',()=>{
  if(broadView===button.dataset.view)return;
  broadView=button.dataset.view;broadResetZoom=true;broadExpanded=false;
  for(const view of views)view.setAttribute('aria-pressed',String(view===button));
  renderBroad();
 });
 safely('历史图表',renderDaily);safely('累计成交图表',renderCum);
 const lastLive=saved('ftshare-last-live-v1');if(matchingLive(lastLive)){live=lastLive;safely('已保存行情',renderMinute);tag('tagUpdate','已保存行情 '+live.date+' '+(live.last_complete_minute||'')+'；正在更新','warn');}
 const dates=Object.keys(seed.minutes).sort().reverse();for(const d of dates){const op=document.createElement('option');op.value=d;op.textContent=d;$('cumDate').appendChild(op);}picked=dates[0]||'';$('cumDate').value=picked;renderCum();
 $('cumDate').addEventListener('change',async()=>{picked=$('cumDate').value;if(picked&&!seed.minutes[picked]){try{seed.minutes[picked]=await get('/api/cumulative?date='+picked);}catch(e){tag('tagUpdate',e.message,'warn');}}renderCum();});
 window.addEventListener('resize',()=>Object.values(charts).forEach(c=>c.resize()));
 await refresh();window.__ETF_APP_READY=true;
 const s=document.createElement('script');s.src='/assets/brush.js';document.head.appendChild(s);brushLoaded=true;
 remaining=60;setInterval(tick,1000);
}catch(e){tag('tagUpdate','看板加载失败：'+e.message,'err');console.error('Dashboard initialization failed');}
})();
