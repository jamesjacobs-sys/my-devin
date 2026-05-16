import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  Bot,
  CircleDollarSign,
  Cpu,
  GaugeCircle,
  Loader2,
  PlayCircle,
  Power,
  RotateCcw,
  Settings as SettingsIcon,
  Signal,
  TrendingDown,
  TrendingUp,
  Wallet,
  Wifi,
  WifiOff,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const WS_URL = API.replace(/^http/, "ws") + "/api/ws";

type Status = {
  mode: string;
  auto_trade: boolean;
  paper_balance: number;
  equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  open_positions: number;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  btc_price: number;
  btc_source: string;
  connected_to_polymarket: boolean;
  connected_to_binance: boolean;
  active_markets: number;
};

type Market = {
  condition_id: string;
  slug: string;
  question: string;
  end_date_iso: string | null;
  end_unix: number | null;
  token_up_id: string;
  token_down_id: string;
  token_up_price: number;
  token_down_price: number;
  token_up_best_bid: number | null;
  token_up_best_ask: number | null;
  token_down_best_bid: number | null;
  token_down_best_ask: number | null;
  volume: number;
  liquidity: number;
  active: boolean;
};

type Trade = {
  id: number;
  created_at: string;
  resolved_at: string | null;
  mode: string;
  market_slug: string;
  market_condition_id: string;
  market_end_ts: number | null;
  outcome: string;
  size: number;
  entry_price: number;
  exit_price: number | null;
  fee: number;
  pnl: number | null;
  status: string;
  signal_type: string;
  signal_strength: number;
  btc_price_at_entry: number | null;
};

type SignalRow = {
  ts: string;
  market_condition_id: string;
  market_slug: string;
  market_end_unix: number;
  signal_type: string;
  direction: string;
  strength: number;
  edge: number;
  suggested_size: number;
  btc_price: number;
  btc_window_open_price?: number | null;
  btc_pct_move: number;
  token_up_price: number;
  token_down_price: number;
  note: string;
};

type BalancePoint = {
  ts: string;
  balance: number;
  equity: number;
  open_positions: number;
  realized_pnl: number;
  unrealized_pnl: number;
};

type BotSettings = {
  mode: string;
  auto_trade: boolean;
  max_position_usd: number;
  max_concurrent: number;
  min_edge: number;
  dislocation_threshold: number;
  starting_balance: number;
  live_credentials_present: boolean;
};

type BacktestResult = {
  starting_balance: number;
  ending_balance: number;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  roi: number;
  sharpe: number;
  max_drawdown: number;
  trades: {
    ts: number;
    direction: string;
    entry_price: number;
    exit_price: number;
    size: number;
    pnl: number;
    balance: number;
  }[];
  equity_curve: { ts: number; balance: number }[];
};

function fmt(num: number | null | undefined, digits = 2): string {
  if (num === null || num === undefined || Number.isNaN(num)) return "—";
  return num.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtTime(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined) return "—";
  const d = typeof ts === "number" ? new Date(ts) : new Date(ts);
  return d.toLocaleTimeString();
}

function secondsToWindow(end_unix: number | null): string {
  if (!end_unix) return "—";
  const s = Math.max(0, end_unix - Math.floor(Date.now() / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function classNames(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

function Card(props: { title?: string; icon?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={classNames("rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm", props.className)}>
      {(props.title || props.right) && (
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-200">
            {props.icon}
            {props.title}
          </div>
          {props.right}
        </div>
      )}
      {props.children}
    </div>
  );
}

function Stat(props: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "pos" | "neg" | "neutral" }) {
  const toneClass =
    props.tone === "pos"
      ? "text-emerald-400"
      : props.tone === "neg"
      ? "text-rose-400"
      : "text-slate-100";
  return (
    <div className="flex flex-col gap-1">
      <div className="text-xs uppercase tracking-wide text-slate-500">{props.label}</div>
      <div className={classNames("text-2xl font-semibold tabular-nums", toneClass)}>{props.value}</div>
      {props.sub && <div className="text-xs text-slate-400">{props.sub}</div>}
    </div>
  );
}

function Pill(props: { children: React.ReactNode; tone?: "ok" | "bad" | "info" | "warn" }) {
  const map = {
    ok: "bg-emerald-500/15 text-emerald-300 border-emerald-700/40",
    bad: "bg-rose-500/15 text-rose-300 border-rose-700/40",
    info: "bg-sky-500/15 text-sky-300 border-sky-700/40",
    warn: "bg-amber-500/15 text-amber-300 border-amber-700/40",
  } as const;
  const cls = map[props.tone || "info"];
  return (
    <span className={classNames("inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium", cls)}>
      {props.children}
    </span>
  );
}

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [signals, setSignals] = useState<SignalRow[]>([]);
  const [balanceHistory, setBalanceHistory] = useState<BalancePoint[]>([]);
  const [btcHistory, setBtcHistory] = useState<{ ts_ms: number; mid: number }[]>([]);
  const [settings, setSettings] = useState<BotSettings | null>(null);
  const [wsLive, setWsLive] = useState(false);
  const [tick, setTick] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const id = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const loadAll = useCallback(async () => {
    try {
      const [s, m, t, sig, hist, btc, st] = await Promise.all([
        fetchJSON<Status>("/api/status"),
        fetchJSON<Market[]>("/api/markets"),
        fetchJSON<Trade[]>("/api/trades?limit=100"),
        fetchJSON<SignalRow[]>("/api/signals?limit=50"),
        fetchJSON<BalancePoint[]>("/api/balance/history?limit=500"),
        fetchJSON<{ history: { ts_ms: number; mid: number }[]; latest: { mid: number; ts_ms: number }; source: string }>(
          "/api/btc/price"
        ),
        fetchJSON<BotSettings>("/api/settings"),
      ]);
      setStatus(s);
      setMarkets(m);
      setTrades(t);
      setSignals(sig);
      setBalanceHistory(hist);
      setBtcHistory(btc.history);
      setSettings(st);
    } catch (e) {
      console.warn("fetch failed", e);
    }
  }, []);

  useEffect(() => {
    loadAll();
    const id = setInterval(loadAll, 5000);
    return () => clearInterval(id);
  }, [loadAll]);

  useEffect(() => {
    let stopped = false;
    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => setWsLive(true);
      ws.onclose = () => {
        setWsLive(false);
        if (!stopped) setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === "snapshot" || data.type === "markets") {
            if (Array.isArray(data.markets)) setMarkets(data.markets as Market[]);
          } else if (data.type === "book" || data.type === "trade") {
            if (data.market) {
              const m = data.market as Market;
              setMarkets((prev) => {
                const idx = prev.findIndex((x) => x.condition_id === m.condition_id);
                if (idx < 0) return prev;
                const next = prev.slice();
                next[idx] = { ...next[idx], ...m };
                return next;
              });
            }
          } else if (data.type === "signal") {
            setSignals((prev) => [data.signal as SignalRow, ...prev].slice(0, 100));
          }
        } catch {
          // ignore
        }
      };
    }
    connect();
    return () => {
      stopped = true;
      wsRef.current?.close();
    };
  }, []);

  const openTrades = useMemo(() => trades.filter((t) => t.status === "OPEN"), [trades]);
  const closedTrades = useMemo(() => trades.filter((t) => t.status !== "OPEN"), [trades]);

  const btcChartData = useMemo(
    () =>
      btcHistory.slice(-300).map((p) => ({
        t: p.ts_ms,
        mid: p.mid,
        label: new Date(p.ts_ms).toLocaleTimeString(),
      })),
    [btcHistory]
  );

  const balanceChartData = useMemo(
    () =>
      balanceHistory.map((p) => ({
        t: new Date(p.ts).getTime(),
        balance: p.balance,
        equity: p.equity,
        label: new Date(p.ts).toLocaleTimeString(),
      })),
    [balanceHistory]
  );

  const updateSettings = async (patch: Partial<BotSettings>) => {
    await fetchJSON("/api/settings", { method: "POST", body: JSON.stringify(patch) });
    await loadAll();
  };

  const reset = async () => {
    if (!confirm("Reset paper trading state? Trades & balance history will be cleared.")) return;
    await fetchJSON("/api/reset", { method: "POST" });
    await loadAll();
  };

  const totalPnl = (status?.realized_pnl || 0) + (status?.unrealized_pnl || 0);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-30 border-b border-slate-800 bg-slate-950/85 backdrop-blur">
        <div className="mx-auto flex max-w-screen-2xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-emerald-500/20 text-emerald-300">
              <Bot size={20} />
            </div>
            <div>
              <div className="text-sm font-semibold">Polymarket BTC 5m Bot</div>
              <div className="text-xs text-slate-400">DISLOCATION + MOMENTUM signal engine</div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone={status?.connected_to_polymarket ? "ok" : "bad"}>
              {status?.connected_to_polymarket ? <Wifi size={12} /> : <WifiOff size={12} />}
              Polymarket
            </Pill>
            <Pill tone={status?.connected_to_binance ? "ok" : "bad"}>
              {status?.connected_to_binance ? <Wifi size={12} /> : <WifiOff size={12} />}
              BTC: {status?.btc_source || "—"}
            </Pill>
            <Pill tone={wsLive ? "ok" : "warn"}>
              <Activity size={12} /> WS {wsLive ? "live" : "off"}
            </Pill>
            <Pill tone={status?.mode === "live" ? "warn" : "info"}>
              <Cpu size={12} /> mode: {status?.mode || "—"}
            </Pill>
            <Pill tone={status?.auto_trade ? "ok" : "info"}>
              <Power size={12} /> auto-trade: {status?.auto_trade ? "on" : "off"}
            </Pill>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-screen-2xl space-y-4 px-6 py-4">
        <Card>
          <div className="grid grid-cols-2 gap-6 md:grid-cols-6">
            <Stat
              label="BTC mid"
              value={`$${fmt(status?.btc_price, 2)}`}
              sub={<span className="inline-flex items-center gap-1 text-slate-400">via {status?.btc_source || "—"}</span>}
            />
            <Stat label="Balance" value={`$${fmt(status?.paper_balance, 2)}`} sub={status?.mode === "live" ? "live wallet" : "paper"} />
            <Stat label="Equity" value={`$${fmt(status?.equity, 2)}`} sub={`${status?.open_positions || 0} open`} />
            <Stat label="Realized PnL" value={`$${fmt(status?.realized_pnl, 2)}`} tone={(status?.realized_pnl || 0) >= 0 ? "pos" : "neg"} />
            <Stat label="Unrealized PnL" value={`$${fmt(status?.unrealized_pnl, 2)}`} tone={(status?.unrealized_pnl || 0) >= 0 ? "pos" : "neg"} />
            <Stat
              label="Win rate"
              value={`${fmt((status?.win_rate || 0) * 100, 1)}%`}
              sub={`${status?.wins || 0}W / ${status?.losses || 0}L`}
              tone={(status?.win_rate || 0) >= 0.53 ? "pos" : (status?.win_rate || 0) > 0 ? "neg" : "neutral"}
            />
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
            <span>
              Total PnL:{" "}
              <span className={classNames(totalPnl >= 0 ? "text-emerald-400" : "text-rose-400", "font-semibold")}>${fmt(totalPnl, 2)}</span>
              {" · "}Markets tracked: <span className="text-slate-200">{status?.active_markets || 0}</span>
            </span>
            <span className="text-slate-500">tick {tick}</span>
          </div>
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="BTC live price (last 10 min)" icon={<TrendingUp size={16} className="text-emerald-400" />}>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={btcChartData}>
                  <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                  <XAxis dataKey="label" stroke="#475569" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                  <YAxis
                    stroke="#475569"
                    tick={{ fontSize: 10 }}
                    domain={["dataMin - 5", "dataMax + 5"]}
                    width={70}
                    tickFormatter={(v) => `$${(v as number).toFixed(0)}`}
                  />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 12 }} formatter={(v) => `$${fmt(v as number, 2)}`} />
                  <Line type="monotone" dataKey="mid" stroke="#34d399" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card title="Balance / equity history" icon={<Wallet size={16} className="text-sky-400" />}>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={balanceChartData}>
                  <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                  <XAxis dataKey="label" stroke="#475569" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                  <YAxis stroke="#475569" tick={{ fontSize: 10 }} width={70} tickFormatter={(v) => `$${(v as number).toFixed(0)}`} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 12 }} formatter={(v, n) => [`$${fmt(v as number, 2)}`, n as string]} />
                  <Area type="monotone" dataKey="equity" stroke="#38bdf8" fill="#38bdf8" fillOpacity={0.2} dot={false} isAnimationActive={false} />
                  <Area type="monotone" dataKey="balance" stroke="#94a3b8" fill="#94a3b8" fillOpacity={0.05} dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Active Polymarket BTC 5m markets" icon={<GaugeCircle size={16} className="text-amber-300" />}>
            <div className="max-h-80 overflow-auto scrollbar-thin">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-slate-900/80 text-xs uppercase text-slate-500">
                  <tr>
                    <th className="px-2 py-2 text-left font-medium">Market</th>
                    <th className="px-2 py-2 text-right font-medium">UP</th>
                    <th className="px-2 py-2 text-right font-medium">DOWN</th>
                    <th className="px-2 py-2 text-right font-medium">Closes in</th>
                    <th className="px-2 py-2 text-right font-medium">Vol</th>
                  </tr>
                </thead>
                <tbody>
                  {markets.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-2 py-6 text-center text-slate-500">
                        No active markets
                      </td>
                    </tr>
                  )}
                  {markets.map((m) => (
                    <tr key={m.condition_id} className="border-t border-slate-800 hover:bg-slate-800/30">
                      <td className="px-2 py-2 text-slate-200">{m.slug.replace("btc-updown-5m-", "")}</td>
                      <td className="px-2 py-2 text-right tabular-nums text-emerald-300">{fmt(m.token_up_price * 100, 1)}¢</td>
                      <td className="px-2 py-2 text-right tabular-nums text-rose-300">{fmt(m.token_down_price * 100, 1)}¢</td>
                      <td className="px-2 py-2 text-right tabular-nums text-slate-300">{secondsToWindow(m.end_unix)}</td>
                      <td className="px-2 py-2 text-right tabular-nums text-slate-400">${fmt(m.volume, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title={`Open positions (${openTrades.length})`} icon={<CircleDollarSign size={16} className="text-emerald-400" />}>
            <div className="max-h-80 overflow-auto scrollbar-thin">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-slate-900/80 text-xs uppercase text-slate-500">
                  <tr>
                    <th className="px-2 py-2 text-left font-medium">Time</th>
                    <th className="px-2 py-2 text-left font-medium">Side</th>
                    <th className="px-2 py-2 text-right font-medium">Size</th>
                    <th className="px-2 py-2 text-right font-medium">Entry</th>
                    <th className="px-2 py-2 text-right font-medium">Fee</th>
                    <th className="px-2 py-2 text-left font-medium">Signal</th>
                  </tr>
                </thead>
                <tbody>
                  {openTrades.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-2 py-6 text-center text-slate-500">
                        No open positions
                      </td>
                    </tr>
                  )}
                  {openTrades.map((t) => (
                    <tr key={t.id} className="border-t border-slate-800">
                      <td className="px-2 py-2 text-slate-300">{fmtTime(t.created_at)}</td>
                      <td className={classNames("px-2 py-2 font-medium", t.outcome === "UP" ? "text-emerald-300" : "text-rose-300")}>{t.outcome}</td>
                      <td className="px-2 py-2 text-right tabular-nums">${fmt(t.size, 2)}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{fmt(t.entry_price * 100, 1)}¢</td>
                      <td className="px-2 py-2 text-right tabular-nums text-slate-400">${fmt(t.fee, 3)}</td>
                      <td className="px-2 py-2 text-slate-300">{t.signal_type}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card title={`Closed trades (${closedTrades.length})`} icon={<Wallet size={16} className="text-slate-300" />}>
            <div className="max-h-80 overflow-auto scrollbar-thin">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-slate-900/80 text-xs uppercase text-slate-500">
                  <tr>
                    <th className="px-2 py-2 text-left font-medium">Time</th>
                    <th className="px-2 py-2 text-left font-medium">Side</th>
                    <th className="px-2 py-2 text-right font-medium">Entry</th>
                    <th className="px-2 py-2 text-right font-medium">Exit</th>
                    <th className="px-2 py-2 text-right font-medium">PnL</th>
                    <th className="px-2 py-2 text-left font-medium">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {closedTrades.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-2 py-6 text-center text-slate-500">
                        No closed trades yet
                      </td>
                    </tr>
                  )}
                  {closedTrades.slice(0, 60).map((t) => (
                    <tr key={t.id} className="border-t border-slate-800">
                      <td className="px-2 py-2 text-slate-300">{fmtTime(t.resolved_at || t.created_at)}</td>
                      <td className={classNames("px-2 py-2 font-medium", t.outcome === "UP" ? "text-emerald-300" : "text-rose-300")}>{t.outcome}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{fmt(t.entry_price * 100, 1)}¢</td>
                      <td className="px-2 py-2 text-right tabular-nums">{t.exit_price !== null ? fmt(t.exit_price * 100, 1) + "¢" : "—"}</td>
                      <td className={classNames("px-2 py-2 text-right tabular-nums", (t.pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400")}>
                        ${fmt(t.pnl, 2)}
                      </td>
                      <td className="px-2 py-2">
                        {t.status === "WIN" ? (
                          <Pill tone="ok">WIN</Pill>
                        ) : t.status === "LOSS" ? (
                          <Pill tone="bad">LOSS</Pill>
                        ) : (
                          <Pill tone="info">{t.status}</Pill>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Signal stream" icon={<Signal size={16} className="text-sky-400" />}>
            <div className="max-h-80 space-y-2 overflow-auto pr-1 scrollbar-thin">
              {signals.length === 0 && <div className="py-6 text-center text-sm text-slate-500">No signals fired yet — watching BTC.</div>}
              {signals.map((s, i) => (
                <div key={i} className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {s.direction === "UP" ? <TrendingUp size={14} className="text-emerald-300" /> : <TrendingDown size={14} className="text-rose-300" />}
                      <span className="font-medium">{s.signal_type}</span>
                      <Pill tone={s.direction === "UP" ? "ok" : "bad"}>{s.direction}</Pill>
                      <Pill tone="info">edge {fmt(s.edge * 100, 1)}%</Pill>
                    </div>
                    <span className="text-xs text-slate-500">{fmtTime(s.ts)}</span>
                  </div>
                  <div className="mt-1 text-xs text-slate-400">{s.note}</div>
                  <div className="mt-1 grid grid-cols-3 gap-2 text-xs text-slate-500">
                    <span>BTC: ${fmt(s.btc_price, 0)}</span>
                    <span>UP {fmt(s.token_up_price * 100, 1)}¢</span>
                    <span>DOWN {fmt(s.token_down_price * 100, 1)}¢</span>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <SettingsPanel settings={settings} onUpdate={updateSettings} onReset={reset} />
          <BacktestPanel />
        </div>

        <footer className="pb-8 pt-4 text-center text-xs text-slate-500">
          paper trading is default · set <code className="text-slate-300">POLYMARKET_PRIVATE_KEY</code> to enable live execution
        </footer>
      </main>
    </div>
  );
}

function SettingsPanel(props: { settings: BotSettings | null; onUpdate: (p: Partial<BotSettings>) => Promise<void>; onReset: () => Promise<void> }) {
  const s = props.settings;
  const [local, setLocal] = useState<Partial<BotSettings>>({});

  useEffect(() => {
    setLocal({});
  }, [s?.mode]);

  if (!s) {
    return (
      <Card title="Settings" icon={<SettingsIcon size={16} />}>
        <div className="flex items-center gap-2 text-slate-500">
          <Loader2 className="animate-spin" size={14} /> Loading...
        </div>
      </Card>
    );
  }

  const merged: BotSettings = { ...s, ...local };

  const save = async () => {
    if (Object.keys(local).length === 0) return;
    await props.onUpdate(local);
    setLocal({});
  };

  return (
    <Card title="Settings & controls" icon={<SettingsIcon size={16} />}>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Mode">
          <select
            value={merged.mode}
            onChange={(e) => setLocal((p) => ({ ...p, mode: e.target.value }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm"
          >
            <option value="paper">paper</option>
            <option value="live" disabled={!s.live_credentials_present}>
              live {s.live_credentials_present ? "" : "(no wallet)"}
            </option>
          </select>
        </Field>
        <Field label="Auto-trade">
          <label className="flex items-center gap-2 text-sm text-slate-200">
            <input
              type="checkbox"
              checked={!!merged.auto_trade}
              onChange={(e) => setLocal((p) => ({ ...p, auto_trade: e.target.checked }))}
              className="h-4 w-4 rounded border-slate-600 bg-slate-900"
            />
            place trades automatically when signal fires
          </label>
        </Field>
        <Field label="Max position USD">
          <input
            type="number"
            min={1}
            value={merged.max_position_usd}
            onChange={(e) => setLocal((p) => ({ ...p, max_position_usd: parseFloat(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
        <Field label="Max concurrent positions">
          <input
            type="number"
            min={1}
            value={merged.max_concurrent}
            onChange={(e) => setLocal((p) => ({ ...p, max_concurrent: parseInt(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
        <Field label="Min edge (3% covers fees)">
          <input
            type="number"
            step={0.005}
            min={0}
            value={merged.min_edge}
            onChange={(e) => setLocal((p) => ({ ...p, min_edge: parseFloat(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
        <Field label="Dislocation threshold (BTC % move)">
          <input
            type="number"
            step={0.0005}
            min={0}
            value={merged.dislocation_threshold}
            onChange={(e) => setLocal((p) => ({ ...p, dislocation_threshold: parseFloat(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="text-xs text-slate-500">
          {s.live_credentials_present ? (
            <Pill tone="ok">live wallet ready</Pill>
          ) : (
            <Pill tone="warn">
              <AlertCircle size={12} /> no POLYMARKET_PRIVATE_KEY — paper only
            </Pill>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={props.onReset}
            className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            <RotateCcw size={12} /> reset paper
          </button>
          <button
            onClick={save}
            disabled={Object.keys(local).length === 0}
            className="inline-flex items-center gap-1 rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-medium text-emerald-950 hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
          >
            save settings
          </button>
        </div>
      </div>
    </Card>
  );
}

function Field(props: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs uppercase tracking-wide text-slate-500">{props.label}</span>
      {props.children}
    </label>
  );
}

function BacktestPanel() {
  const [params, setParams] = useState({
    starting_balance: 1000,
    minutes: 240,
    min_edge: 0.03,
    dislocation_threshold: 0.002,
    max_position_usd: 25,
    seed: 42,
  });
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const r = await fetchJSON<BacktestResult>("/api/backtest", { method: "POST", body: JSON.stringify(params) });
      setResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const equityChartData = useMemo(() => {
    if (!result) return [];
    return result.equity_curve.map((p) => ({ t: p.ts, balance: p.balance, label: new Date(p.ts * 1000).toLocaleTimeString() }));
  }, [result]);

  return (
    <Card title="Backtest (synthetic BTC, real strategy)" icon={<PlayCircle size={16} className="text-violet-300" />}>
      <div className="grid gap-2 md:grid-cols-3">
        <Field label="Starting $">
          <input
            type="number"
            value={params.starting_balance}
            onChange={(e) => setParams((p) => ({ ...p, starting_balance: parseFloat(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
        <Field label="Minutes">
          <input
            type="number"
            value={params.minutes}
            onChange={(e) => setParams((p) => ({ ...p, minutes: parseInt(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
        <Field label="Min edge">
          <input
            type="number"
            step={0.005}
            value={params.min_edge}
            onChange={(e) => setParams((p) => ({ ...p, min_edge: parseFloat(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
        <Field label="Dislocation thr">
          <input
            type="number"
            step={0.0005}
            value={params.dislocation_threshold}
            onChange={(e) => setParams((p) => ({ ...p, dislocation_threshold: parseFloat(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
        <Field label="Max pos $">
          <input
            type="number"
            value={params.max_position_usd}
            onChange={(e) => setParams((p) => ({ ...p, max_position_usd: parseFloat(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
        <Field label="Seed">
          <input
            type="number"
            value={params.seed}
            onChange={(e) => setParams((p) => ({ ...p, seed: parseInt(e.target.value) }))}
            className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm tabular-nums"
          />
        </Field>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={run}
          disabled={running}
          className="inline-flex items-center gap-1 rounded-md bg-violet-500 px-3 py-1.5 text-xs font-medium text-violet-950 hover:bg-violet-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
        >
          {running ? <Loader2 className="animate-spin" size={12} /> : <PlayCircle size={12} />} run backtest
        </button>
        {error && <span className="text-xs text-rose-400">{error}</span>}
      </div>
      {result && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat
              label="Ending balance"
              value={`$${fmt(result.ending_balance, 2)}`}
              tone={result.ending_balance >= result.starting_balance ? "pos" : "neg"}
              sub={`from $${fmt(result.starting_balance, 0)}`}
            />
            <Stat label="ROI" value={`${fmt(result.roi * 100, 2)}%`} tone={result.roi >= 0 ? "pos" : "neg"} />
            <Stat label="Win rate" value={`${fmt(result.win_rate * 100, 1)}%`} sub={`${result.wins}W / ${result.losses}L`} />
            <Stat label="Max drawdown" value={`${fmt(result.max_drawdown * 100, 2)}%`} tone="neg" sub={`sharpe ${fmt(result.sharpe, 2)}`} />
          </div>
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={equityChartData}>
                <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="#475569" tick={{ fontSize: 10 }} interval="preserveStartEnd" hide />
                <YAxis stroke="#475569" tick={{ fontSize: 10 }} width={60} tickFormatter={(v) => `$${(v as number).toFixed(0)}`} />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 12 }} />
                <Line type="monotone" dataKey="balance" stroke="#a78bfa" strokeWidth={1.5} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </Card>
  );
}
