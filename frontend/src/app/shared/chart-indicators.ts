export type StudyKind =
  | 'SMA'
  | 'EMA'
  | 'WMA'
  | 'Bollinger'
  | 'Donchian'
  | 'VWAP'
  | 'RSI'
  | 'MACD'
  | 'Stochastic'
  | 'ATR'
  | 'CCI'
  | 'ROC'
  | 'OBV'
  | 'Volume';
export interface Study {
  id: number;
  kind: StudyKind;
  period: number;
  color: string;
}
export const STUDIES: {
  kind: StudyKind;
  name: string;
  period: number;
  pane?: boolean;
  volume?: boolean;
}[] = [
  { kind: 'SMA', name: 'Simple moving average', period: 20 },
  { kind: 'EMA', name: 'Exponential moving average', period: 20 },
  { kind: 'WMA', name: 'Weighted moving average', period: 20 },
  { kind: 'Bollinger', name: 'Bollinger bands · 2 standard deviations', period: 20 },
  { kind: 'Donchian', name: 'Donchian channel', period: 20 },
  { kind: 'VWAP', name: 'Session VWAP · IST', period: 1, volume: true },
  { kind: 'RSI', name: 'Relative strength index', period: 14, pane: true },
  { kind: 'MACD', name: 'MACD · 12 / 26 / 9', period: 26, pane: true },
  { kind: 'Stochastic', name: 'Stochastic · %K / 3-period %D', period: 14, pane: true },
  { kind: 'ATR', name: 'Average true range', period: 14, pane: true },
  { kind: 'CCI', name: 'Commodity channel index', period: 20, pane: true },
  { kind: 'ROC', name: 'Rate of change · %', period: 12, pane: true },
  { kind: 'OBV', name: 'On-balance volume', period: 1, pane: true, volume: true },
  { kind: 'Volume', name: 'Traded volume', period: 1, pane: true, volume: true },
];
const average = (values: number[], n: number, exponential = false, wilder = false): number[] => {
  const result = Array(values.length).fill(NaN);
  let sum = 0,
    previous = NaN,
    run = 0;
  for (let i = 0; i < values.length; i++) {
    if (!Number.isFinite(values[i])) {
      sum = 0;
      run = 0;
      previous = NaN;
      continue;
    }
    sum += values[i];
    run++;
    if (run < n) continue;
    if (exponential) {
      previous = Number.isFinite(previous)
        ? previous + (values[i] - previous) * (wilder ? 1 / n : 2 / (n + 1))
        : sum / n;
      result[i] = previous;
    } else {
      if (run > n) sum -= values[i - n];
      result[i] = sum / n;
    }
  }
  return result;
};
/** Missing warm-up/volume values are gaps, never synthetic zeroes. */
export function calculateStudy(
  rows: any[],
  study: Study,
): { values: number[][]; histogram?: number; levels?: number[] } {
  const n = Math.max(2, Math.min(500, Math.round(study.period)));
  const close = rows.map((r) => Number(r.close ?? r.value));
  const high = rows.map((r) => Number(r.high ?? r.value));
  const low = rows.map((r) => Number(r.low ?? r.value));
  const volume = rows.map((r) =>
    r.volumeComplete !== false && Number.isFinite(r.volume) && r.volume >= 0 ? r.volume : NaN,
  );
  const sma = average(close, n);
  const windowMap = (fn: (i: number) => number) => rows.map((_, i) => (i < n - 1 ? NaN : fn(i)));
  switch (study.kind) {
    case 'SMA':
      return { values: [sma] };
    case 'EMA':
      return { values: [average(close, n, true)] };
    case 'WMA':
      return {
        values: [
          windowMap(
            (i) =>
              close.slice(i - n + 1, i + 1).reduce((sum, v, j) => sum + v * (j + 1), 0) /
              ((n * (n + 1)) / 2),
          ),
        ],
      };
    case 'Bollinger': {
      const deviation = windowMap(
        (i) =>
          Math.sqrt(
            close.slice(i - n + 1, i + 1).reduce((sum, v) => sum + (v - sma[i]) ** 2, 0) / n,
          ) * 2,
      );
      return {
        values: [sma, sma.map((v, i) => v + deviation[i]), sma.map((v, i) => v - deviation[i])],
      };
    }
    case 'Donchian':
      return {
        values: [
          windowMap((i) => Math.max(...high.slice(i - n + 1, i + 1))),
          windowMap((i) => Math.min(...low.slice(i - n + 1, i + 1))),
        ],
      };
    case 'RSI': {
      const change = close.map((v, i) => (i ? v - close[i - 1] : NaN));
      const gain = average(
        change.map((v) => Math.max(0, v)),
        n,
        true,
        true,
      );
      const loss = average(
        change.map((v) => Math.max(0, -v)),
        n,
        true,
        true,
      );
      return {
        values: [
          gain.map((v, i) =>
            !Number.isFinite(v)
              ? NaN
              : loss[i] === 0
                ? v === 0
                  ? 50
                  : 100
                : 100 - 100 / (1 + v / loss[i]),
          ),
        ],
        levels: [30, 70],
      };
    }
    case 'MACD': {
      const fast = average(close, 12, true),
        slow = average(close, 26, true);
      const macd = fast.map((v, i) => v - slow[i]),
        signal = average(macd, 9, true);
      return {
        values: [macd, signal, macd.map((v, i) => v - signal[i])],
        histogram: 2,
        levels: [0],
      };
    }
    case 'Stochastic': {
      const k = windowMap((i) => {
        const h = Math.max(...high.slice(i - n + 1, i + 1)),
          l = Math.min(...low.slice(i - n + 1, i + 1));
        return h === l ? 50 : (100 * (close[i] - l)) / (h - l);
      });
      return { values: [k, average(k, 3)], levels: [20, 80] };
    }
    case 'ATR':
      return {
        values: [
          average(
            high.map((h, i) =>
              i
                ? Math.max(h - low[i], Math.abs(h - close[i - 1]), Math.abs(low[i] - close[i - 1]))
                : h - low[i],
            ),
            n,
            true,
            true,
          ),
        ],
      };
    case 'CCI': {
      const tp = close.map((v, i) => (v + high[i] + low[i]) / 3),
        mean = average(tp, n);
      return {
        values: [
          windowMap((i) => {
            const d =
              tp.slice(i - n + 1, i + 1).reduce((sum, v) => sum + Math.abs(v - mean[i]), 0) / n;
            return d ? (tp[i] - mean[i]) / (0.015 * d) : 0;
          }),
        ],
        levels: [-100, 100],
      };
    }
    case 'ROC':
      return {
        values: [
          close.map((v, i) => (i < n || !close[i - n] ? NaN : 100 * (v / close[i - n] - 1))),
        ],
        levels: [0],
      };
    case 'VWAP': {
      let session = '',
        total = 0,
        weighted = 0;
      return {
        values: [
          rows.map((r, i) => {
            const day =
              typeof r.time === 'number'
                ? new Date((r.time + 19800) * 1000).toISOString().slice(0, 10)
                : String(r.time);
            if (day !== session) {
              session = day;
              total = 0;
              weighted = 0;
            }
            if (!Number.isFinite(volume[i])) {
              weighted = NaN;
              return NaN;
            }
            total += volume[i];
            weighted += (volume[i] * (high[i] + low[i] + close[i])) / 3;
            return total ? weighted / total : NaN;
          }),
        ],
      };
    }
    case 'OBV': {
      let total = 0;
      return {
        values: [
          volume.map((v, i) => {
            if (!Number.isFinite(v)) {
              total = NaN;
              return NaN;
            }
            total += i ? Math.sign(close[i] - close[i - 1]) * v : 0;
            return total;
          }),
        ],
      };
    }
    case 'Volume':
      return { values: [volume], histogram: 0 };
  }
}
