"""
portfolio_ledger.py — 本地持仓账本（SQLite）
==============================================
记录买卖交易 → 自动计算持仓成本、未实现盈亏、已实现盈亏。
存储路径：~/.arthera/portfolio.db

公开 API：
  PortfolioLedger.add_trade(symbol, side, qty, price, date, reason, fee)
  PortfolioLedger.get_positions() → List[Dict]
  PortfolioLedger.get_trades(symbol, limit) → List[Dict]
  PortfolioLedger.get_pnl_with_prices(prices_dict) → List[Dict]
  PortfolioLedger.get_realized_pnl() → List[Dict]
  PortfolioLedger.export_csv(path) → Path
  PortfolioLedger.delete_trade(id) → bool
  PortfolioLedger.trade_count() → int
  PortfolioLedger.position_count() → int
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".arthera" / "portfolio.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL CHECK(side IN ('BUY','SELL')),
    qty         REAL    NOT NULL CHECK(qty > 0),
    price       REAL    NOT NULL CHECK(price > 0),
    amount      REAL    NOT NULL,
    fee         REAL    NOT NULL DEFAULT 0,
    date        TEXT    NOT NULL,
    reason      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_date   ON trades(date);
"""


class PortfolioLedger:

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or _DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── Write ─────────────────────────────────────────────────────────────────

    def add_trade(
        self,
        symbol: str,
        side:   str,
        qty:    float,
        price:  float,
        date:   Optional[str] = None,
        reason: str = "",
        fee:    float = 0.0,
    ) -> int:
        """
        Record a trade. Returns the new row id.
        side: "BUY" or "SELL"
        date: "YYYY-MM-DD"  (defaults to today)
        """
        symbol = symbol.upper().strip()
        side   = side.upper().strip()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必须是 BUY 或 SELL，收到: {side!r}")
        qty   = float(qty)
        price = float(price)
        if qty <= 0:
            raise ValueError("qty 必须大于 0")
        if price <= 0:
            raise ValueError("price 必须大于 0")

        date   = (date or datetime.now().strftime("%Y-%m-%d")).strip()
        amount = round(qty * price, 6)

        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO trades (symbol, side, qty, price, amount, fee, date, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, side, qty, price, amount, fee, date, reason),
            )
            row_id = cur.lastrowid
        logger.debug("Ledger: added trade #%s %s %s %.4f @ %.4f", row_id, side, symbol, qty, price)
        return row_id

    def delete_trade(self, trade_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        return cur.rowcount > 0

    # ── Read: Trades ──────────────────────────────────────────────────────────

    def get_trades(
        self,
        symbol: Optional[str] = None,
        limit:  int = 50,
    ) -> List[Dict]:
        sql    = "SELECT * FROM trades"
        params: list = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY date DESC, id DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Read: Positions ───────────────────────────────────────────────────────

    def get_positions(self) -> List[Dict]:
        """
        Current open positions. Average cost = total BUY amount / total BUY qty.
        Returns only positions with net_qty > 0.
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    symbol,
                    SUM(CASE WHEN side='BUY'  THEN qty ELSE -qty END)    AS net_qty,
                    SUM(CASE WHEN side='BUY'  THEN amount ELSE 0 END)    AS total_buy_amt,
                    SUM(CASE WHEN side='BUY'  THEN qty    ELSE 0 END)    AS total_buy_qty,
                    SUM(CASE WHEN side='SELL' THEN amount ELSE 0 END)    AS total_sell_amt,
                    MIN(date) AS first_trade_date,
                    MAX(date) AS last_trade_date
                FROM trades
                GROUP BY symbol
                HAVING net_qty > 0.0001
                ORDER BY symbol
            """).fetchall()

        positions = []
        for row in rows:
            r        = dict(row)
            buy_qty  = r["total_buy_qty"] or 0
            buy_amt  = r["total_buy_amt"] or 0
            net_qty  = r["net_qty"]
            avg_cost = buy_amt / buy_qty if buy_qty > 0 else 0
            positions.append({
                "symbol":     r["symbol"],
                "net_qty":    round(net_qty, 4),
                "avg_cost":   round(avg_cost, 4),
                "cost_basis": round(net_qty * avg_cost, 2),
                "first_trade": r["first_trade_date"],
                "last_trade":  r["last_trade_date"],
            })
        return positions

    def get_pnl_with_prices(self, current_prices: Dict[str, float]) -> List[Dict]:
        """Attach live prices to positions and compute unrealized P&L."""
        positions = self.get_positions()
        result    = []
        for pos in positions:
            sym   = pos["symbol"]
            price = current_prices.get(sym) or current_prices.get(sym.lower())
            if price:
                diff    = price - pos["avg_cost"]
                unreal  = round(diff * pos["net_qty"], 2)
                pct     = round(diff / pos["avg_cost"] * 100, 2) if pos["avg_cost"] else 0
                pos.update({
                    "current_price":  price,
                    "market_value":   round(price * pos["net_qty"], 2),
                    "unrealized_pnl": unreal,
                    "unrealized_pct": pct,
                })
            result.append(pos)
        return result

    def get_realized_pnl(self) -> List[Dict]:
        """FIFO realized P&L per symbol (all closed lots)."""
        with self._conn() as conn:
            symbols = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM trades ORDER BY symbol"
            ).fetchall()]

        realized = []
        for sym in symbols:
            with self._conn() as conn:
                trades = [dict(r) for r in conn.execute(
                    "SELECT * FROM trades WHERE symbol=? ORDER BY date, id", (sym,)
                ).fetchall()]

            buy_queue: List[Tuple[float, float]] = []  # (qty, price)
            total_pnl = 0.0
            total_sold = 0.0

            for t in trades:
                if t["side"] == "BUY":
                    buy_queue.append((t["qty"], t["price"]))
                else:
                    remaining = t["qty"]
                    while remaining > 0.0001 and buy_queue:
                        bq, bp    = buy_queue[0]
                        matched   = min(bq, remaining)
                        total_pnl += matched * (t["price"] - bp)
                        total_sold += matched * t["price"]
                        remaining -= matched
                        if matched >= bq - 0.0001:
                            buy_queue.pop(0)
                        else:
                            buy_queue[0] = (bq - matched, bp)

            open_lots = sum(q for q, _ in buy_queue)
            realized.append({
                "symbol":       sym,
                "realized_pnl": round(total_pnl, 2),
                "open_lots":    round(open_lots, 4),
                "has_open":     open_lots > 0.0001,
            })
        return realized

    # ── Export ────────────────────────────────────────────────────────────────

    def export_csv(self, path: Optional[Path] = None) -> Path:
        out    = path or (Path.home() / "Desktop" / f"trades_{datetime.now():%Y%m%d_%H%M}.csv")
        trades = self.get_trades(limit=100_000)
        if not trades:
            out.write_text("no trades\n", encoding="utf-8")
            return out
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=trades[0].keys())
            writer.writeheader()
            writer.writerows(trades)
        return out

    # ── Stats ─────────────────────────────────────────────────────────────────

    def trade_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    def position_count(self) -> int:
        return len(self.get_positions())

    def summary(self) -> Dict:
        return {
            "trade_count":    self.trade_count(),
            "position_count": self.position_count(),
            "db_path":        str(self.db_path),
        }
