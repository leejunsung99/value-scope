// ECharts components. Palette = validated reference categorical order (fixed, never cycled);
// color follows the entity (company order in the confirmed peer group), never its rank.
import ReactECharts from 'echarts-for-react'
import { useEffect, useMemo, useState } from 'react'
import type { Charts, QuarterRow, ScenarioMap } from './apiAnalysis'
import { krw } from './apiAnalysis'
import { IS_PRINT } from './print'

const LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
const DARK = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767']
const OTHER = { light: '#8a8984', dark: '#77766f' }

export function useDark() {
  const [dark, setDark] = useState(document.documentElement.classList.contains('dark'))
  useEffect(() => {
    const ob = new MutationObserver(() => setDark(document.documentElement.classList.contains('dark')))
    ob.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => ob.disconnect()
  }, [])
  return dark
}

function theme(dark: boolean) {
  return {
    palette: dark ? DARK : LIGHT,
    other: dark ? OTHER.dark : OTHER.light,
    text: dark ? '#c3c2b7' : '#52514e',
    textStrong: dark ? '#f0efec' : '#0b0b0b',
    grid: dark ? '#2c2c2a' : '#ebeae6',
    axis: dark ? '#4a4945' : '#c9c8c2',
    surface: dark ? '#0f172a' : '#ffffff',
    tooltipBg: dark ? '#1e293b' : '#ffffff',
  }
}

/** company id -> fixed color (index in companies list; 9th+ fold to gray). */
export function colorMap(companies: Charts['companies'], dark: boolean): Record<number, string> {
  const t = theme(dark)
  const out: Record<number, string> = {}
  ;(companies ?? []).forEach((c, i) => {
    out[c.id] = i < t.palette.length ? t.palette[i] : t.other
  })
  return out
}

function base(dark: boolean) {
  const t = theme(dark)
  return {
    backgroundColor: 'transparent',
    animation: !IS_PRINT,
    textStyle: { color: t.text, fontFamily: 'Pretendard, Malgun Gothic, sans-serif', fontSize: 11 },
    grid: { left: 56, right: 24, top: 36, bottom: 40, containLabel: true },
    tooltip: {
      backgroundColor: t.tooltipBg, borderColor: t.axis, textStyle: { color: t.textStrong, fontSize: 12 },
      confine: true,
    },
    legend: { top: 0, textStyle: { color: t.text }, itemWidth: 14, itemHeight: 8, type: 'scroll' as const },
    xAxis: { axisLine: { lineStyle: { color: t.axis } }, axisLabel: { color: t.text }, splitLine: { show: false } },
    yAxis: { axisLine: { show: false }, axisLabel: { color: t.text }, splitLine: { lineStyle: { color: t.grid } } },
  }
}

function Chart({ option, height = 300 }: { option: object; height?: number }) {
  // 인쇄본은 지면이 좁으므로 높이를 줄이고 SVG로 그린다
  const h = IS_PRINT ? Math.round(Math.min(height, 420) * 0.72) : height
  return <ReactECharts option={option} style={{ height: h }} notMerge lazyUpdate opts={{ renderer: 'svg' }} />
}

const eok = (v: number) => `${Math.round(v / 1e8).toLocaleString('ko-KR')}억`

// ------------------------------------------------------------------ 1. price comparison (indexed = 100)
const RANGES: Record<string, number> = { '1M': 31, '3M': 92, '6M': 183, '1Y': 366, '3Y': 1100 }

export function PriceCompareChart({ charts, onlyIds }: { charts: Charts; onlyIds?: number[] }) {
  const dark = useDark()
  const [range, setRange] = useState('1Y')
  const colors = colorMap(charts.companies, dark)
  const option = useMemo(() => {
    const b = base(dark)
    const comps = (charts.companies ?? []).filter((c) => charts.price_index?.[String(c.id)]?.length && (!onlyIds || onlyIds.includes(c.id)))
    const end = Math.max(...comps.flatMap((c) => (charts.price_index![String(c.id)].slice(-1)).map((p) => Date.parse(p[0]))), 0)
    const since = end - RANGES[range] * 86400000
    const rebase = (pts: [string, number][]) => {
      const s = pts.filter((p) => Date.parse(p[0]) >= since)
      const b0 = s[0]?.[1]
      return s.map((p) => [p[0], b0 ? +(p[1] / b0 * 100).toFixed(2) : null])
    }
    const series: object[] = comps.map((c) => ({
      name: c.name, type: 'line', showSymbol: false, data: rebase(charts.price_index![String(c.id)]),
      lineStyle: { width: c.is_target ? 2.5 : 1.5, color: colors[c.id] }, itemStyle: { color: colors[c.id] },
      z: c.is_target ? 10 : 2, emphasis: { focus: 'series' },
    }))
    if (charts.market_index?.points?.length) {
      series.push({ name: charts.market_index.label, type: 'line', showSymbol: false, data: rebase(charts.market_index.points),
        lineStyle: { width: 1.5, type: 'dashed', color: theme(dark).text }, itemStyle: { color: theme(dark).text } })
    }
    return {
      ...b,
      tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => (v == null ? '—' : v.toFixed(1)) },
      xAxis: { ...b.xAxis, type: 'time' },
      yAxis: { ...b.yAxis, type: 'value', scale: true, name: '기준=100', nameTextStyle: { color: theme(dark).text } },
      series,
    }
  }, [charts, dark, range, colors, onlyIds])
  return (
    <div>
      <RangeTabs value={range} onChange={setRange} options={Object.keys(RANGES)} />
      <Chart option={option} height={340} />
      <div className="chart-note text-[11px] text-slate-500">범례를 클릭해 기업을 표시·숨김할 수 있습니다. 1년 초과 구간은 월말 표본입니다. 분할·병합은 상장주식수 변동으로 보정했습니다.</div>
    </div>
  )
}

export function RangeTabs({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: string[] }) {
  if (IS_PRINT) return null
  return (
    <div className="mb-1 flex gap-1 text-xs">
      {options.map((o) => (
        <button key={o} onClick={() => onChange(o)} className={`rounded px-2 py-0.5 ${value === o ? 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900' : 'text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800'}`}>{o}</button>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ 2. excess return vs market
export function ExcessReturnChart({ charts }: { charts: Charts }) {
  const dark = useDark()
  const [range, setRange] = useState('1Y')
  const option = useMemo(() => {
    const b = base(dark)
    const pts = charts.excess_return ?? []
    const end = pts.length ? Date.parse(pts[pts.length - 1][0]) : 0
    const s = pts.filter((p) => Date.parse(p[0]) >= end - RANGES[range] * 86400000)
    const t0 = s[0]?.[1] ?? 0
    const data = s.map((p) => [p[0], +(((100 + p[1]) / (100 + t0) - 1) * 100).toFixed(2)])
    return {
      ...b, legend: { show: false },
      tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => `${v.toFixed(1)}%p` },
      xAxis: { ...b.xAxis, type: 'time' },
      yAxis: { ...b.yAxis, type: 'value', axisLabel: { color: theme(dark).text, formatter: '{value}%' } },
      series: [{ name: '시장 대비 초과수익률', type: 'line', showSymbol: false, data, lineStyle: { width: 2, color: theme(dark).palette[0] },
        areaStyle: { opacity: 0.08, color: theme(dark).palette[0] },
        markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0 }], lineStyle: { color: theme(dark).axis } } }],
    }
  }, [charts, dark, range])
  return <div><RangeTabs value={range} onChange={setRange} options={Object.keys(RANGES)} /><Chart option={option} height={260} /></div>
}

// ------------------------------------------------------------------ 3. market cap bars
export function MarketCapChart({ charts }: { charts: Charts }) {
  const dark = useDark()
  const colors = colorMap(charts.companies, dark)
  const rows = (charts.market_caps ?? []).filter((r) => r.value).sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
  const b = base(dark)
  const option = {
    ...b, legend: { show: false }, grid: { ...b.grid, left: 90 },
    tooltip: { ...b.tooltip, trigger: 'item', formatter: (p: { name: string; value: number }) => `${p.name}<br/>${krw(p.value)}` },
    xAxis: { ...b.xAxis, type: 'log', axisLabel: { color: theme(dark).text, formatter: (v: number) => eok(v) }, splitLine: { lineStyle: { color: theme(dark).grid } } },
    yAxis: { ...b.yAxis, type: 'category', inverse: true, data: rows.map((r) => r.name), splitLine: { show: false } },
    series: [{ type: 'bar', barMaxWidth: 18, data: rows.map((r) => ({ value: r.value, itemStyle: { color: colors[r.id], borderRadius: [0, 4, 4, 0] } })),
      label: { show: true, position: 'right', color: theme(dark).text, formatter: (p: { value: number }) => krw(p.value) } }],
  }
  return <div><Chart option={option} height={Math.max(200, rows.length * 34 + 40)} /><div className="text-[11px] text-slate-500">로그 축 — 기업 간 규모 차이가 커서 사용했습니다.</div></div>
}

// ------------------------------------------------------------------ 4. multiples (grouped bars, one chart per multiple)
export function MultiplesChart({ charts, metric, label }: { charts: Charts; metric: 'per' | 'ev_ebit' | 'ev_ebitda' | 'pbr' | 'per_adj' | 'ev_ebit_norm' | 'pe_norm'; label: string }) {
  const dark = useDark()
  const colors = colorMap(charts.companies, dark)
  const rows = charts.multiples ?? []
  const valid = rows.filter((r) => r[metric] != null)
  const missing = rows.filter((r) => r[metric] == null)
  const val = (r: (typeof rows)[number]) => r[metric] as number
  const b = base(dark)
  const option = {
    ...b, legend: { show: false }, grid: { ...b.grid, top: 16 },
    tooltip: { ...b.tooltip, trigger: 'item', formatter: (p: { name: string; value: number }) => `${p.name}<br/>${label} ${p.value.toFixed(1)}배` },
    xAxis: { ...b.xAxis, type: 'category', data: valid.map((r) => r.name), axisLabel: { color: theme(dark).text, interval: 0, rotate: valid.length > 6 ? 30 : 0 } },
    yAxis: { ...b.yAxis, type: 'value', axisLabel: { color: theme(dark).text, formatter: '{value}x' } },
    series: [{ type: 'bar', barMaxWidth: 28, data: valid.map((r) => ({ value: val(r), itemStyle: { color: colors[r.id], borderRadius: [4, 4, 0, 0] } })),
      label: { show: true, position: 'top', color: theme(dark).text, formatter: (p: { value: number }) => p.value.toFixed(1) }}],
  }
  return (
    <div>
      <div className="text-xs font-semibold">{label}</div>
      {valid.length ? <Chart option={option} height={220} /> : <div className="py-8 text-center text-xs text-slate-500">산출 가능한 기업 없음</div>}
      {missing.length > 0 && (
        <div className="text-[11px] text-slate-500">N/A: {missing.map((r) => `${r.name}(${r.na?.[metric] ?? '데이터 없음'})`).join(', ')}</div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ 5/6. quarterly revenue & OP, margin
export function QuarterlyChart({ rows }: { rows: QuarterRow[] }) {
  const dark = useDark()
  const t = theme(dark)
  const b = base(dark)
  const option = {
    ...b,
    tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => (v == null ? '데이터 없음' : krw(v)) },
    xAxis: { ...b.xAxis, type: 'category', data: rows.map((r) => r.period) },
    yAxis: { ...b.yAxis, type: 'value', axisLabel: { color: t.text, formatter: (v: number) => eok(v) } },
    series: [
      { name: '매출액', type: 'bar', barGap: '10%', barMaxWidth: 18, data: rows.map((r) => r.revenue), itemStyle: { color: t.palette[0], borderRadius: [4, 4, 0, 0] } },
      { name: '영업이익', type: 'bar', barMaxWidth: 18, data: rows.map((r) => r.operating_income), itemStyle: { color: t.palette[1], borderRadius: [4, 4, 0, 0] } },
    ],
  }
  return <Chart option={option} height={280} />
}

export function MarginChart({ series }: { series: { name: string; color: string; rows: QuarterRow[]; target?: boolean }[] }) {
  const dark = useDark()
  const t = theme(dark)
  const b = base(dark)
  const periods = [...new Set(series.flatMap((s) => s.rows.map((r) => r.period)))].sort()
  const option = {
    ...b,
    tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => (v == null ? 'N/A' : `${(v * 100).toFixed(1)}%`) },
    xAxis: { ...b.xAxis, type: 'category', data: periods },
    yAxis: { ...b.yAxis, type: 'value', axisLabel: { color: t.text, formatter: (v: number) => `${Math.round(v * 100)}%` } },
    series: series.map((s) => ({
      name: s.name, type: 'line', connectNulls: false, symbolSize: 8,
      data: periods.map((p) => s.rows.find((r) => r.period === p)?.op_margin ?? null),
      lineStyle: { width: s.target ? 2.5 : 1.5, color: s.color }, itemStyle: { color: s.color, borderColor: t.surface, borderWidth: 2 },
    })),
  }
  return <Chart option={option} height={280} />
}

// ------------------------------------------------------------------ 7. contracts
export function ContractsChart({ monthly, backlog }: { monthly: [string, number][]; backlog?: number | null }) {
  const dark = useDark()
  const t = theme(dark)
  const b = base(dark)
  const option = {
    ...b, legend: { show: false },
    tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => krw(v) },
    xAxis: { ...b.xAxis, type: 'category', data: monthly.map((m) => m[0]) },
    yAxis: { ...b.yAxis, type: 'value', axisLabel: { color: t.text, formatter: (v: number) => eok(v) } },
    series: [{ name: '신규 수주(공시 금액)', type: 'bar', barMaxWidth: 18, data: monthly.map((m) => m[1]), itemStyle: { color: t.palette[2], borderRadius: [4, 4, 0, 0] },
      markLine: backlog ? { silent: true, symbol: 'none', label: { formatter: `수주잔고 ${krw(backlog)}`, color: t.text }, data: [{ yAxis: backlog }], lineStyle: { color: t.palette[0], type: 'dashed' } } : undefined }],
  }
  return <Chart option={option} height={260} />
}

// ------------------------------------------------------------------ 8. forward scenarios
export function ForwardChart({ forward }: { forward: NonNullable<Charts['forward']> }) {
  const dark = useDark()
  const t = theme(dark)
  const b = base(dark)
  const keys = ['conservative', 'base', 'optimistic'] as const
  const names = ['보수적', '기준', '낙관적']
  const option = {
    ...b,
    tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => krw(v) },
    xAxis: { ...b.xAxis, type: 'category', data: names },
    yAxis: { ...b.yAxis, type: 'value', axisLabel: { color: t.text, formatter: (v: number) => eok(v) } },
    series: [
      { name: '향후 12개월 매출(추정)', type: 'bar', barMaxWidth: 26, data: keys.map((k) => forward[k].forward_revenue), itemStyle: { color: t.palette[0], borderRadius: [4, 4, 0, 0] } },
      { name: '영업이익(추정)', type: 'bar', barMaxWidth: 26, data: keys.map((k) => forward[k].forward_ebit), itemStyle: { color: t.palette[1], borderRadius: [4, 4, 0, 0] } },
    ],
  }
  return <Chart option={option} height={240} />
}

// ------------------------------------------------------------------ 9. fair value range vs price
export function FairValueChart({ scenarios, price }: { scenarios: Record<string, ScenarioMap | null>; price: number | null | undefined }) {
  const dark = useDark()
  const t = theme(dark)
  const b = base(dark)
  const label: Record<string, string> = { forward: 'A. 미래이익', peer: 'B. 상대평가', history: 'C. 자사 과거', combined: '가중 결합' }
  const keys = Object.keys(label).filter((k) => scenarios[k]?.base)
  const option = {
    ...b, legend: { show: false }, grid: { ...b.grid, left: 90 },
    tooltip: {
      ...b.tooltip, trigger: 'item',
      formatter: (p: { name: string; data: { range: number[] } }) => p.data?.range
        ? `${p.name}<br/>보수 ${Math.round(p.data.range[0]).toLocaleString()}원 · 기준 ${Math.round(p.data.range[1]).toLocaleString()}원 · 낙관 ${Math.round(p.data.range[2]).toLocaleString()}원` : p.name,
    },
    xAxis: { ...b.xAxis, type: 'value', axisLabel: { color: t.text, formatter: (v: number) => `${(v / 1000).toFixed(0)}천` }, splitLine: { lineStyle: { color: t.grid } } },
    yAxis: { ...b.yAxis, type: 'category', inverse: true, data: keys.map((k) => label[k]), splitLine: { show: false } },
    series: [
      {
        type: 'custom',
        renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[]; size: (v: number[]) => number[] }) => {
          const y = api.value(0), lo = api.value(1), mid = api.value(2), hi = api.value(3)
          const a = api.coord([lo, y]), m = api.coord([mid, y]), z = api.coord([hi, y])
          const h = api.size([0, 1])[1] * 0.35
          const isComb = keys[y] === 'combined'
          const col = isComb ? t.palette[0] : t.palette[6]
          return {
            type: 'group', children: [
              { type: 'rect', shape: { x: a[0], y: a[1] - h / 2, width: Math.max(z[0] - a[0], 2), height: h, r: 4 }, style: { fill: col, opacity: isComb ? 0.85 : 0.45 } },
              { type: 'rect', shape: { x: m[0] - 1.5, y: m[1] - h / 2 - 3, width: 3, height: h + 6 }, style: { fill: t.textStrong } },
            ],
          }
        },
        encode: { x: [1, 2, 3], y: 0 },
        data: keys.map((k, i) => ({ value: [i, scenarios[k]!.conservative, scenarios[k]!.base, scenarios[k]!.optimistic], range: [scenarios[k]!.conservative, scenarios[k]!.base, scenarios[k]!.optimistic], name: label[k] })),
        markLine: price ? { silent: true, symbol: 'none', data: [{ xAxis: price }], lineStyle: { color: t.palette[7], width: 2 },
          label: { formatter: `현재가 ${Math.round(price).toLocaleString()}원`, color: t.textStrong, position: 'start', rotate: 0, distance: 6 } } : undefined,
      },
    ],
  }
  return <Chart option={option} height={Math.max(180, keys.length * 52 + 60)} />
}

// ------------------------------------------------------------------ 10. history of fair values across runs
export function HistoryChart({ history }: { history: NonNullable<Charts['history']> }) {
  const dark = useDark()
  const t = theme(dark)
  const b = base(dark)
  const x = history.map((h) => (h.date ?? '').slice(0, 16).replace('T', ' '))
  const option = {
    ...b,
    tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => (v == null ? '—' : `${Math.round(v).toLocaleString()}원`) },
    xAxis: { ...b.xAxis, type: 'category', data: x },
    yAxis: { ...b.yAxis, type: 'value', scale: true },
    series: [
      { name: '적정주가(기준)', type: 'line', symbolSize: 8, data: history.map((h) => h.base ?? null), lineStyle: { width: 2, color: t.palette[0] }, itemStyle: { color: t.palette[0] } },
      { name: '보수적', type: 'line', symbolSize: 8, data: history.map((h) => h.conservative ?? null), lineStyle: { width: 1.5, type: 'dashed', color: t.palette[6] }, itemStyle: { color: t.palette[6] } },
      { name: '낙관적', type: 'line', symbolSize: 8, data: history.map((h) => h.optimistic ?? null), lineStyle: { width: 1.5, type: 'dashed', color: t.palette[2] }, itemStyle: { color: t.palette[2] } },
      { name: '분석 시점 주가', type: 'line', symbolSize: 8, data: history.map((h) => h.price ?? null), lineStyle: { width: 2, color: t.palette[7] }, itemStyle: { color: t.palette[7] } },
    ],
  }
  return <Chart option={option} height={260} />
}

// ------------------------------------------------------------------ 주간 분석: 지수화 주가 경로 (직전 거래일 = 100)
export function WeeklyPathChart({ chart, names, targetName }: {
  chart: { target: [string, number][]; market: [string, number][]; peer_avg: [string, number][] | null; peers: Record<string, [string, number][]> }
  names: Record<string, string>; targetName: string
}) {
  const dark = useDark()
  const option = useMemo(() => {
    const t = theme(dark)
    const b = base(dark)
    const vals = (pts: [string, number][]) => pts.map((p) => p[1])
    const series: object[] = Object.keys(chart.peers).map((id) => ({
      name: names[id] ?? id, type: 'line', data: vals(chart.peers[id]), showSymbol: false,
      lineStyle: { width: 1, color: t.other, opacity: 0.5 }, itemStyle: { color: t.other }, z: 1,
    }))
    if (chart.peer_avg) {
      series.push({ name: '비교군 가중평균', type: 'line', data: vals(chart.peer_avg), symbolSize: 6,
        lineStyle: { width: 2.5, color: t.palette[1] }, itemStyle: { color: t.palette[1] }, z: 5 })
    }
    series.push({ name: '시장 대용지수', type: 'line', data: vals(chart.market), symbolSize: 5,
      lineStyle: { width: 2, type: 'dashed', color: t.text }, itemStyle: { color: t.text }, z: 4 })
    series.push({ name: targetName, type: 'line', data: vals(chart.target), symbolSize: 8,
      lineStyle: { width: 3, color: t.palette[0] }, itemStyle: { color: t.palette[0] }, z: 10,
      label: { show: true, position: 'top', color: t.textStrong, fontSize: 10,
        formatter: (p: { value: number }) => `${p.value - 100 >= 0 ? '+' : ''}${(p.value - 100).toFixed(1)}%` } })
    return {
      ...b,
      legend: { ...b.legend, data: [targetName, '비교군 가중평균', '시장 대용지수'] },
      tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => (v == null ? '—' : `${(v - 100).toFixed(2)}%`) },
      xAxis: { ...b.xAxis, type: 'category', boundaryGap: false, data: chart.target.map((p) => p[0].slice(5)) },
      grid: { ...b.grid, top: 44 },
      yAxis: { ...b.yAxis, type: 'value', scale: true },
      series,
    }
  }, [chart, names, targetName, dark])
  return <Chart option={option} height={300} />
}

// ------------------------------------------------------------------ 주간 분석: 일별 실제 vs 기대(시장+비교군) vs 개별 요인
export function WeeklyDailyChart({ days }: { days: { date: string; weekday: string; ret: number; expected: number; residual: number; abnormal: boolean }[] }) {
  const dark = useDark()
  const option = useMemo(() => {
    const t = theme(dark)
    const b = base(dark)
    const pc = (v: number) => +(v * 100).toFixed(2)
    return {
      ...b,
      tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => `${v.toFixed(2)}%` },
      xAxis: { ...b.xAxis, type: 'category', data: days.map((d) => `${d.date.slice(5)}(${d.weekday})`) },
      yAxis: { ...b.yAxis, type: 'value', axisLabel: { ...b.yAxis.axisLabel, formatter: '{value}%' } },
      series: [
        { name: '실제 수익률', type: 'bar', data: days.map((d) => pc(d.ret)), itemStyle: { color: t.palette[0], borderRadius: 3 } },
        { name: '기대(시장+비교군)', type: 'bar', data: days.map((d) => pc(d.expected)), itemStyle: { color: t.other, borderRadius: 3 } },
        { name: '개별 요인', type: 'bar', itemStyle: { borderRadius: 3 },
          data: days.map((d) => ({ value: pc(d.residual), itemStyle: { color: d.abnormal ? t.palette[7] : t.palette[3] } })) },
      ],
    }
  }, [days, dark])
  return <Chart option={option} height={260} />
}
