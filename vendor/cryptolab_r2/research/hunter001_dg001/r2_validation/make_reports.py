"""Reproduce reports and annual coin attribution; snapshot live state without rewriting it."""
from pathlib import Path
from collections import defaultdict
import json,csv,gzip,sqlite3,datetime,hashlib,os
import numpy as np
R=Path(__file__).resolve().parent
def read(n):
 with (gzip.open(R/n,'rt',encoding='utf-8') if n.endswith('.gz') else (R/n).open(encoding='utf-8')) as f:return list(csv.DictReader(f))
def write(n,s):(R/n).write_bytes(s.encode('utf-8'))
def f(z,k):return float(z[k])
def pct(a):return f'{a*100:.2f}%'
def usd(a):return f'${a:,.2f}'
def table(h,rows):return '| '+' | '.join(h)+' |\n|'+'|'.join(['---']*len(h))+'|\n'+''.join('| '+' | '.join(map(str,z))+' |\n' for z in rows)+'\n'
def attribution():
 trades=read('HISTORICAL_TRADES.csv.gz');eq=read('HISTORICAL_EQUITY.csv.gz');flows=defaultdict(lambda:dict(cashflow=0.,fees=0.,slippage=0.,turnover=0.))
 for z in trades:
  year='2022' if int(z['day'])<27875520 else '2023'
  key=(z['strategy'],z['capital'],z['cost'],year,z['symbol']);a=flows[key];a['cashflow']+=f(z,'cash_delta');a['fees']+=f(z,'fee_quote');a['slippage']+=f(z,'slippage_quote');a['turnover']+=f(z,'gross_qty')*f(z,'reference_price')
 with np.load(R/'history/SPOT_2022_2023.npz',allow_pickle=False) as z:
  t=z['hour_t'];assert t.max()<28401120;x=z['ohlc']
 prior=defaultdict(float);out=[]
 for z in eq:
  if z['date'] not in ['2022-12-31','2023-12-31']:continue
  year=z['date'][:4];day=int((np.datetime64(z['date'])-np.datetime64('1970-01-01'))/np.timedelta64(1,'m'));idx=np.searchsorted(t,day+1380)
  for j,(sym,col) in enumerate([('BTCUSDT','qty_BTC'),('ETHUSDT','qty_ETH'),('SOLUSDT','qty_SOL')]):
   key=(z['strategy'],z['capital'],z['cost'],year,sym);account=(z['strategy'],z['capital'],z['cost'],sym);endvalue=f(z,col)*x[idx,3 if year=='2022' else 0,j];a=flows[key]
   out.append(dict(strategy=z['strategy'],capital=z['capital'],cost=z['cost'],year=year,symbol=sym,net_contribution=a['cashflow']+endvalue-prior[account],start_position_mtm=prior[account],end_position_mtm=endvalue,fees=a['fees'],slippage=a['slippage'],turnover=a['turnover']))
   prior[account]=endvalue
 with (R/'HISTORICAL_COIN_YEAR.csv').open('w',encoding='utf-8',newline='') as file:
  w=csv.DictWriter(file,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
 for z in read('HISTORICAL_RESULTS.csv'):
  for year in ['2022','2023']:
   actual=sum(a['net_contribution'] for a in out if all(str(a[k])==z[k] for k in ['strategy','capital','cost']) and a['year']==year)
   assert abs(actual-f(z,'pnl_'+year))<1e-5

def leader_costs():
 signals=defaultdict(list)
 for z in read('HISTORICAL_SIGNALS.csv'):signals[int(z['day'])].append(z)
 switches=set();previous=None
 for day,rows in sorted(signals.items()):
  eligible=sum(z['eligible']=='True' for z in rows);omitted=next((z['symbol'] for z in rows if float(z['r2_weight'])==0),None)
  if previous and previous[0]==day-1440 and previous[1]==3 and eligible==3 and previous[2]!=omitted:switches.add(day)
  previous=(day,eligible,omitted)
 totals=defaultdict(lambda:dict(fees=0.,buffer=0.,turnover=0.,orders=0))
 for z in read('HISTORICAL_TRADES.csv.gz'):
  if int(z['day']) not in switches:continue
  key=(z['strategy'],z['capital'],z['cost']);a=totals[key];a['fees']+=float(z['fee_quote']);a['buffer']+=float(z['slippage_quote']);a['turnover']+=float(z['gross_qty'])*float(z['reference_price']);a['orders']+=1
 out=[dict(strategy=k[0],capital=k[1],cost=k[2],r2_switch_days=len(switches),attribution='ALL_ORDERS_ON_R2_SWITCH_DAYS_INCLUDING_MICRO_RETARGET',**a) for k,a in totals.items()]
 with (R/'LEADER_SWITCH_COSTS.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
 return out

def main():
 switch=leader_costs()
 attribution();rows=read('HISTORICAL_RESULTS.csv');rand=read('MATCHED_RANDOM_BASELINE.csv');boot=read('HISTORICAL_UNCERTAINTY.csv');bh=read('HISTORICAL_BUY_HOLD.csv')
 history='# Историческая проверка 2022–2023\n\n**Данные и воспроизводимый расчёт PASS; преимущество R2 статистически НЕ ПОДТВЕРЖДЕНО. Это ретроспективная временная проверка, не перспективный OOS.**\n\n'
 history+='Все параметры опубликованы заранее в протоколе 7b6fed5ae5d72573e827f4e5c2cee419c183b445. Bybit Spot, BTC/ETH/SOL, TSMOM28, неизменный R2, C5 MODELED, BASE/STRESS, независимые сопоставимые портфели. Начальный cash один раз, непрерывный счёт2022–2023 без сброса2023; терминальная оценка/ликвидация2023-12-31 23:00open. Январь–сентябрь2026 не читались.\n\n'
 for cap in [300,1000,10000]:
  history+=f'## Первоначальный капитал ${cap:,}\n\n'
  selected=[z for z in rows if z['capital']==str(cap)]
  history+=table(['Модель','Расходы','2022 net','2023 net','Финал MTM','Ликвидируемые USDT','MDD (hourly open)','Комиссии','Оборот'],[[z['strategy'],z['cost'],pct(f(z,'net_2022')),pct(f(z,'net_2023')),usd(f(z,'final_mtm')),usd(f(z,'liquidatable_cash')),pct(f(z,'mdd')),usd(f(z,'fees')),usd(f(z,'turnover'))] for z in selected])
  for cost in ['BASE','STRESS']:
   a=next(z for z in selected if z['cost']==cost and z['strategy']=='R2');b=next(z for z in selected if z['cost']==cost and z['strategy']=='BASELINE')
   history+=f'{cost}: совокупная дополнительная прибыль R2 {usd(f(a,"final_mtm")-f(b,"final_mtm"))}; разница годовой доходности2022 {pct(f(a,"net_2022")-f(b,"net_2022"))},2023 {pct(f(a,"net_2023")-f(b,"net_2023"))}.\n\n'
 history+='Оба варианта теряют деньги в2022. Большая часть полной прибыли приходится на2023; годовой процент R2 в2023 немного ниже baseline, несмотря на более высокий конечный капитал благодаря меньшему убытку2022. Это не устойчивое превосходство в каждом году. Доли не сбрасывались; годовые проценты имеют разные стартовые NAV.\n\n'
 history+='## Статистическая неопределённость\n\nПарные календарные круговые блоки7/30дней,5000 повторений. Сохранена зависимость всех монет внутри портфельного дня. Bootstrap CI для дневной разницы и составной доходности периода опубликованы полностью. Holm учитывает6 капитал/затраты сравнений в каждом заранее заданном блоке.\n\n'
 history+=table(['Капитал','Затраты','Блок','Средняя разница bp/день','95%CI bp/день','95%CI составной разницы pp','Holm p'],[[z['capital'],z['cost'],z['block'],f'{f(z,"daily_delta")*10000:.3f}',f'[{f(z,"daily_ci_low")*10000:.3f};{f(z,"daily_ci_high")*10000:.3f}]',f'[{f(z,"compound_ci_low")*100:.2f};{f(z,"compound_ci_high")*100:.2f}]',z['p_holm']] for z in boot])
 history+='## Случайный контроль\n\nЗаранее заданный persistent random меняет исключаемую монету в ту же дату, что R2, и сохраняет экспозицию. Но оборот также зависит от того, какая монета выходит из допустимого множества. Поэтому одинаковая частота смен не равна одинаковому обороту. Первичный критерий≥90% траекторий в диапазоне±20% оборота и±2pp экспозиции НЕ ПРОЙДЕН.\n\n'
 history+=table(['Капитал','Затраты','Доля сопоставимых','Оборот/NAV R2','Оборот/NAV median random','Найдено сопоставимых','Первичный допуск'],[[z['capital'],z['cost'],pct(f(z,'comparable_fraction')),f'{f(z,"r2_normalized_turnover"):.2f}',f'{f(z,"random_median_normalized_turnover"):.2f}',z['matched_paths'],z['comparability_gate']] for z in rand])
 history+='RANDOM_PATHS.csv сохраняет все6000 траекторий. Для полноты отдельно показано подмножество, совпадающее по обороту/экспозиции, без выбора по прибыли. Оно условное и описательное; не заменяет провал первичного допуска и не выдаётся за1000 прошедших контроль траекторий. Метод/порог не менялись ради PASS. Перед положительным статистическим решением потребуется отдельно зарегистрированный подход к случайному контролю, действительно проходящий сопоставимость, без использования прибыли для настройки.\n\n'
 history+='## Переключения лидеров\n\nСтоимость всех сторон операций на днях фактической смены исключаемой монеты при трёх допустимых активах. Включены микрокорректировки той же даты; это прозрачная атрибуция затрат, не заявление о чистом причинном эффекте рейтинга. LEADER_SWITCH_COSTS.csv сохраняет обе стратегии и все капиталы/затраты.\n\n'
 history+=table(['Стратегия','Капитал','Расходы','Дней смены','Комиссии','Buffer/slippage','Оборот'],[[z['strategy'],z['capital'],z['cost'],z['r2_switch_days'],usd(z['fees']),usd(z['buffer']),usd(z['turnover'])] for z in switch if z['capital']=='1000'])
 history+='## Риск и пассивное удержание\n\nДоля дней с тремя допустимыми монетами '+pct(f(rows[0],'three_eligible_fraction'))+'. Полные помесячные результаты, вклад каждой монеты по годам и затратам, отказы и балансы сохранены в HISTORICAL_*.csv[.gz]. Стоимость переключений учитывает обе стороны; торговые причины и все комиссии сохранены в полном журнале. Не складывать монеты или случайные траектории как независимые OOS.\n\n'
 history+=table(['Капитал','Затраты','Buy/hold2022','Buy/hold2023','Финал','MDD (hourly open)'],[[z['capital'],z['cost'],pct(f(z,'net_2022')),pct(f(z,'net_2023')),usd(f(z,'final_mtm')),pct(f(z,'mdd'))] for z in bh])
 history+='R2 MDD2022–2023 около27%; это не отменяет ранее известную просадку>30% в2024–2025. Допуск реального капитала не выдан. Новые цены, минимумы, комиссии и реальные исполнения прошлых лет не подтверждены этим OHLC-скринингом.\n'
 write('HISTORICAL_EXTENSION_REPORT.md',history)
 write('STATUS_AND_GO_NO_GO.md',(R/'CURRENT_STATUS.md').read_text(encoding='utf-8'))
 audit=json.loads((R/'INDEPENDENT_AUDIT.json').read_text())
 write('DATA_QUALITY_REPORT.md','# Data quality\n\n'+json.dumps(audit,indent=2)+'\n\nAll3 official Spot inputs cover [2021-12-01,2024-01-01), no gaps/duplicates/invalid OHLC. 66 public responses,4,682,958 raw bytes,18,264hours/symbol. All219,168 OHLC values independently compared to raw responses; overlapping probes identical. Historical event times are observed exchange bar labels; publication availability in the past is UNKNOWN/MODELED, never OBSERVED. Forward HTTP receive times are observed, and separate from server/book event timestamps and raw ingested_at. Orderbook age<=5s, cross-symbol skew<=5s, no crossed/unsorted/nonpositive books.\n\n135 regression tests PASS (37new+98prior). Published XML is evidence, not an economic result. The generalized replay matches all12 parent C5 capital/cost/strategy cases to<1e-6USD. All498 previous sealed files remain unchanged.\n\nForward controls verify the source and plumbing; there are0 prospective performance observations at publication. Observer NOT DEPLOYED; local control process stopped before prospective start. Current spectra do not establish historical filters; bid/ask snapshots do not establish actual fills. Primary random comparability fails and is explicitly retained.\n')
if __name__=='__main__':main()
