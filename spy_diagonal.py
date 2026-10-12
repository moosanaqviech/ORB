"""
spy_diagonal.py - SPY call diagonal ("buy wholesale, sell retail") on IBKR.

  Wholesale: buy one SPY call ~35 days out, at the money.
  Retail:    each week, sell a call that expires that Friday, ~30 delta,
             at or above the long strike (so the long always covers it).

Rules
  - 10:00 ET each day:
      * no long held  -> buy the long call (expiry closest to LONG_DTE days,
        strike nearest the money)
      * long has <= ROLL_LONG_DTE days left, or no weekly short can finish
        before that -> close the short (if any), sell the long, and buy a
        fresh one (a new "cycle")
      * no short held -> sell this week's call (expiry 3-9 days out, Friday
        preferred, finishing before the long's roll date), strike closest to SHORT_DELTA, never below the long
        strike, credit >= MIN_SHORT_CREDIT. Then a GTC buy-back order at
        (1 - SHORT_PROFIT_TAKE) of the credit is left working.
      * VIX above VIX3M (stressed market) -> don't sell a new short today
  - On the short's expiry day at 15:45 ET: cancel the buy-back order and buy
    the short back, so nothing can be assigned. Next morning a new one is sold.
  - No stop-loss. The long call is the protection on rallies; a sharp drop
    is the main risk (the long loses value faster than the shorts pay).

Run it every trading day (start before 10:00, or any time - it acts on start
if it's after 10:00). It exits by itself at 15:50, or right after the morning
step if nothing expires today.
  python spy_diagonal.py            (paper port 7497)
  python spy_diagonal.py --dry-run  (picks strikes and logs, places no orders)
  python spy_diagonal.py --status   (prints the position and P&L, then exits)

Positions it owns are kept in diagonal_state.json (it only manages those, so
it can run next to the 0DTE scripts). Every event goes to
logs/diagonal_trades.csv, with commissions estimated at COMMISSION_PER_LEG.

Notes
  - Single-leg orders: long first, short only after the long is held. In a
    margin account IBKR treats the pair as a spread. Not for a cash account.
  - SPY pays dividends (ex-dates around the 3rd Friday of Mar/Jun/Sep/Dec).
    An in-the-money short call can be assigned early the day before; the
    script warns on those weeks - consider skipping the short (--no-short).
"""

import argparse
import csv
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import nest_asyncio
from ib_insync import IB, Index, LimitOrder, Option, Stock

nest_asyncio.apply()

# ================================ SETTINGS ===================================
HOST = '127.0.0.1'
PORT = 7497                  # TWS paper 7497 / live 7496; Gateway paper 4002 / live 4001
CLIENT_ID = 23
ALLOW_LIVE = False           # must be True to run on 7496 / 4001
MARKET_DATA_TYPE = 1         # 1 live, 3 delayed

SYMBOL = 'SPY'
QTY = 1                      # diagonals (1 long + 1 short each)

LONG_DTE = 35                # aim for an expiry this many days out
LONG_DTE_RANGE = (25, 60)
ROLL_LONG_DTE = 10           # roll the long when this few days are left
SHORT_DTE_RANGE = (3, 9)     # this week's expiry (Friday preferred)
SHORT_DELTA = 0.30
MIN_SHORT_CREDIT = 0.50      # per share ($50 per contract)
SHORT_PROFIT_TAKE = 0.80     # buy back once 80% of the credit is earned
SKIP_SHORT_IF_VIX_BACKWARDATION = True

ACTION_TIME = '10:00'
EXPIRY_CLOSE = '15:45'       # buy back a short expiring today
EXIT_TIME = '15:50'
REPRICE_SEC = 10             # wait between 1-cent steps
MAX_STEPS = 10               # steps from mid toward the natural price
COMMISSION_PER_LEG = 0.65

DIVIDEND_MONTHS = (3, 6, 9, 12)   # SPY ex-div ~3rd Friday of these months
TAG = 'SPY-DIAG'
STATE_FILE = 'diagonal_state.json'
LOG_DIR = 'logs'
LOG_FILE = 'diagonal_trades.csv'
# ============================================================================

LOG_COLS = ['date', 'time', 'event', 'leg', 'expiry', 'strike', 'qty', 'price',
            'cash', 'cycle_pnl', 'spot', 'delta', 'note']


# ------------------------------ time (US Eastern) ----------------------------
def to_eastern(utc_dt):
    """UTC -> US Eastern without a tz database (works on any Windows Python)."""
    def nth_sunday(y, mo, n):
        d = datetime(y, mo, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    naive = utc_dt.replace(tzinfo=None)
    dst = nth_sunday(naive.year, 3, 2).replace(hour=7) <= naive < \
        nth_sunday(naive.year, 11, 1).replace(hour=6)
    return naive - timedelta(hours=4 if dst else 5)


def now_et():
    return to_eastern(datetime.now(timezone.utc))


def hm(s):
    return int(s[:2]) * 60 + int(s[3:])


def minute_now():
    t = now_et()
    return t.hour * 60 + t.minute + t.second / 60


def ymd(s):
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ------------------------------ helpers ---------------------------------------
def ok(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x)) and x >= 0


def mid(t):
    return (t.bid + t.ask) / 2 if ok(t.bid) and ok(t.ask) and t.ask > 0 else float('nan')


def cents(x):
    return round(round(x * 100) / 100, 2)


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call_delta(spot, k, iv, days):
    t = max(days, 0.2) / 365
    v = iv * math.sqrt(t)
    return ncdf((math.log(spot / k) + 0.5 * v * v) / v)


def log(event, **kw):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, LOG_FILE)
    new = not os.path.exists(path)
    t = now_et()
    row = {'date': t.date().isoformat(), 'time': t.strftime('%H:%M:%S'), 'event': event, **kw}
    with open(path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS, extrasaction='ignore')
        if new:
            w.writeheader()
        w.writerow(row)
    print(f"{row['time']} {event:12} " + ' '.join(f'{k}={v}' for k, v in kw.items()))


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {'long': None, 'short': None, 'cycle_cash': 0.0, 'cycles': []}


def save_state(s):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, STATE_FILE)


# ================================ STRATEGY ===================================
class Diagonal:
    def __init__(self, ib, dry_run=False, no_short=False):
        self.ib = ib
        self.dry = dry_run
        self.no_short = no_short
        self.today = now_et().date()
        self.st = load_state()
        self.und = Stock(SYMBOL, 'SMART', 'USD')
        ib.qualifyContracts(self.und)
        chains = ib.reqSecDefOptParams(self.und.symbol, '', 'STK', self.und.conId)
        chain = next((c for c in chains if c.tradingClass == SYMBOL and c.exchange == 'SMART'),
                     next((c for c in chains if c.tradingClass == SYMBOL), None))
        if not chain:
            sys.exit(f'No {SYMBOL} option chain.')
        self.expirations = sorted(e for e in chain.expirations if ymd(e) >= self.today)
        self.strikes = sorted(chain.strikes)
        self.und_t = ib.reqMktData(self.und, '', False, False)
        self.vix, self.vix3m = self.prior_close('VIX'), self.prior_close('VIX3M')
        self.reconcile()

    # -- data -------------------------------------------------------------------
    def prior_close(self, name):
        try:
            c = Index(name, 'CBOE', 'USD')
            self.ib.qualifyContracts(c)
            bars = self.ib.reqHistoricalData(c, '', '10 D', '1 day', 'TRADES', True)
            prev = [b for b in bars if b.date < self.today]
            return prev[-1].close if prev else None
        except Exception as e:
            print(f'  could not get {name} history: {e}')
            return None

    def spot(self):
        self.ib.sleep(1)
        p = self.und_t.marketPrice()
        if ok(p) and p > 0:
            return p
        try:
            bars = self.ib.reqHistoricalData(self.und, '', '1800 S', '1 min', 'TRADES', True)
            if bars:
                return bars[-1].close
        except Exception as e:
            print(f'  1-min bar fallback failed: {e}')
        return float('nan')

    def call(self, expiry, strike):
        return Option(SYMBOL, expiry, strike, 'C', 'SMART', tradingClass=SYMBOL, currency='USD')

    def quote(self, contracts, wait=3):
        ts = [self.ib.reqMktData(c, '', False, False) for c in contracts]
        self.ib.sleep(wait)
        out = [(t.bid, t.ask, mid(t), t.modelGreeks) for t in ts]
        for c in contracts:
            self.ib.cancelMktData(c)
        return out

    def dte(self, expiry):
        return (ymd(expiry) - self.today).days

    # -- bookkeeping ----------------------------------------------------------
    def reconcile(self):
        """Drop legs from the state file that are no longer in the account
        (expired, assigned, or bought back by the GTC order while offline)."""
        held = {p.contract.conId: p.position for p in self.ib.positions()}
        for leg in ('long', 'short'):
            L = self.st.get(leg)
            if not L or self.dry:
                continue
            want = L['qty'] if leg == 'long' else -L['qty']
            if held.get(L['conId'], 0) == want:
                continue
            if leg == 'short' and held.get(L['conId'], 0) == 0:
                # most likely the GTC take-profit filled while the script was off
                px = L.get('tp_price', 0.0)
                self.cash(-px * 100 * L['qty'] - COMMISSION_PER_LEG * L['qty'])
                log('short_gone', leg='short', expiry=L['expiry'], strike=L['strike'],
                    qty=L['qty'], price=px, cash=round(self.st['cycle_cash'], 2),
                    note='not in account any more - assumed take-profit fill; check TWS')
            else:
                log('mismatch', leg=leg, expiry=L['expiry'], strike=L['strike'],
                    note=f'state says {want}, account has {held.get(L["conId"], 0)} - '
                         f'leg dropped from state, check TWS by hand')
            self.st[leg] = None
        save_state(self.st)

    def cash(self, amount):
        self.st['cycle_cash'] = round(self.st.get('cycle_cash', 0.0) + amount, 2)

    # -- orders ------------------------------------------------------------------
    def work(self, contract, action, qty, note=''):
        """Limit order from mid, stepping 1 cent toward the natural price.
        Returns avg fill price or None."""
        [(bid, ask, m, _)] = self.quote([contract])
        if not ok(m):
            print(f'  no quote for {contract.localSymbol or contract.strike} - skipping')
            return None
        nat = ask if action == 'BUY' else bid
        start, step = cents(m), (0.01 if action == 'BUY' else -0.01)
        if self.dry:
            print(f'  [dry run] would {action} {qty} {contract.lastTradeDateOrContractMonth} '
                  f'{contract.strike}C @ {start:.2f} ({note})')
            return None
        order = LimitOrder(action, qty, start, tif='DAY', orderRef=TAG)
        trade = self.ib.placeOrder(contract, order)
        for i in range(MAX_STEPS + 1):
            self.ib.sleep(REPRICE_SEC)
            if trade.orderStatus.status == 'Filled' or trade.isDone():
                break
            nxt = cents(order.lmtPrice + step)
            if i < MAX_STEPS and ok(nat) and ((step > 0 and nxt <= nat) or (step < 0 and nxt >= nat)):
                order.lmtPrice = nxt
                self.ib.placeOrder(contract, order)
        if not trade.isDone():
            self.ib.cancelOrder(order)
            for _ in range(10):
                self.ib.sleep(1)
                if trade.isDone():
                    break
        if trade.orderStatus.status == 'Filled' or trade.orderStatus.filled > 0:
            return trade.orderStatus.avgFillPrice
        print(f'  {action} not filled ({trade.orderStatus.status})')
        return None

    def cancel_tp(self):
        S = self.st.get('short')
        if not S or not S.get('tp_order_id'):
            return
        for t in self.ib.openTrades():
            if t.order.orderId == S['tp_order_id'] or (
                    t.order.orderRef == TAG and t.contract.conId == S['conId'] and t.order.action == 'BUY'):
                self.ib.cancelOrder(t.order)
        self.ib.sleep(2)

    # -- the steps -------------------------------------------------------------
    def morning(self):
        spot = self.spot()
        if not ok(spot):
            log('no_price', note=f'no {SYMBOL} price')
            return
        L = self.st.get('long')
        if L and self.dte(L['expiry']) <= ROLL_LONG_DTE:
            self.close_cycle(spot, f'long has {self.dte(L["expiry"])} days left - rolling')
        elif L and not self.st.get('short') and not self.short_expiries(L):
            self.close_cycle(spot, f'no weekly fits before the roll date '
                                   f'({self.dte(L["expiry"])} days left) - rolling early')
        if not self.st.get('long'):
            self.open_long(spot)
        if self.st.get('long') and not self.st.get('short'):
            self.open_short(spot)
        self.status(spot)

    def open_long(self, spot):
        exps = [e for e in self.expirations if LONG_DTE_RANGE[0] <= self.dte(e) <= LONG_DTE_RANGE[1]]
        if not exps:
            log('no_expiry', leg='long', note=f'nothing {LONG_DTE_RANGE} days out')
            return
        e = min(exps, key=lambda e: abs(self.dte(e) - LONG_DTE))
        k = min(self.strikes, key=lambda k: abs(k - spot))
        [c] = self.ib.qualifyContracts(self.call(e, k))
        px = self.work(c, 'BUY', QTY, 'long')
        if px is None:
            log('dry_run' if self.dry else 'no_fill', leg='long', expiry=e, strike=k,
                spot=round(spot, 2))
            return
        self.st['long'] = {'conId': c.conId, 'expiry': e, 'strike': k, 'qty': QTY,
                           'cost': px, 'opened': self.today.isoformat()}
        self.st['cycle_cash'] = 0.0
        self.cash(-px * 100 * QTY - COMMISSION_PER_LEG * QTY)
        save_state(self.st)
        log('buy_long', leg='long', expiry=e, strike=k, qty=QTY, price=px,
            cash=self.st['cycle_cash'], spot=round(spot, 2),
            note=f'{self.dte(e)} days out, cost ${px * 100 * QTY:,.0f}')

    def short_expiries(self, L):
        """This week's expiries that finish before the long has to be rolled."""
        last = self.dte(L['expiry']) - ROLL_LONG_DTE
        return [e for e in self.expirations
                if SHORT_DTE_RANGE[0] <= self.dte(e) <= min(SHORT_DTE_RANGE[1], last)]

    def open_short(self, spot):
        if self.no_short:
            log('skip_short', note='--no-short')
            return
        if SKIP_SHORT_IF_VIX_BACKWARDATION and self.vix and self.vix3m and self.vix > self.vix3m:
            log('skip_short', note=f'VIX {self.vix} > VIX3M {self.vix3m}')
            return
        L = self.st['long']
        exps = self.short_expiries(L)
        if not exps:
            log('skip_short', note=f'no expiry {SHORT_DTE_RANGE} days out')
            return
        fridays = [e for e in exps if ymd(e).weekday() == 4]
        e = (fridays or exps)[0]
        if ymd(e).month in DIVIDEND_MONTHS and 15 <= ymd(e).day <= 21:
            print('  WARNING: SPY dividend week - an in-the-money short call can be '
                  'assigned early. Consider --no-short this week.')
        lo = max(L['strike'], spot * 0.995)          # out of the money, never below the long
        cands = [k for k in self.strikes if lo <= k <= spot * 1.05][:30]
        cs = [c for c in self.ib.qualifyContracts(*[self.call(e, k) for k in cands]) if c and c.conId]
        if not cs:
            log('skip_short', note='no strikes')
            return
        qs = self.quote(cs, wait=4)
        iv = (self.vix or 18) / 100
        rows = []
        for c, (b, a, m, g) in zip(cs, qs):
            d = g.delta if g and g.delta is not None and not math.isnan(g.delta) \
                else bs_call_delta(spot, c.strike, iv, self.dte(e))
            rows.append((c, m, d))
        # closest to SHORT_DELTA that still pays enough; else move toward the money
        rows.sort(key=lambda r: abs(r[2] - SHORT_DELTA))
        pick = next((r for r in rows if ok(r[1]) and r[1] >= MIN_SHORT_CREDIT
                     and r[2] <= SHORT_DELTA + 0.15), None)
        if not pick:
            best = max(rows, key=lambda r: r[1] if ok(r[1]) else -1)
            log('skip_short', expiry=e, strike=best[0].strike,
                price=round(best[1], 2) if ok(best[1]) else '',
                note=f'no strike pays >= {MIN_SHORT_CREDIT} near {SHORT_DELTA} delta')
            return
        c, m, d = pick
        px = self.work(c, 'SELL', L['qty'], 'weekly short')
        if px is None:
            log('dry_run' if self.dry else 'no_fill', leg='short', expiry=e, strike=c.strike,
                price=round(m, 2), delta=round(d, 2))
            return
        tp_px = max(cents(px * (1 - SHORT_PROFIT_TAKE)), 0.01)
        tp = self.ib.placeOrder(c, LimitOrder('BUY', L['qty'], tp_px, tif='GTC', orderRef=TAG))
        self.st['short'] = {'conId': c.conId, 'expiry': e, 'strike': c.strike, 'qty': L['qty'],
                            'credit': px, 'tp_price': tp_px, 'tp_order_id': tp.order.orderId}
        self.cash(px * 100 * L['qty'] - COMMISSION_PER_LEG * L['qty'])
        save_state(self.st)
        log('sell_short', leg='short', expiry=e, strike=c.strike, qty=L['qty'], price=px,
            cash=self.st['cycle_cash'], spot=round(spot, 2), delta=round(d, 2),
            note=f'+${px * 100 * L["qty"]:.0f}; GTC buy-back at {tp_px}')

    def check_tp(self):
        """Has the GTC buy-back filled (while we're running)?"""
        S = self.st.get('short')
        if not S or self.dry:
            return
        for t in self.ib.trades():
            if t.order.orderId == S.get('tp_order_id') and t.orderStatus.status == 'Filled':
                px = t.orderStatus.avgFillPrice
                self.cash(-px * 100 * S['qty'] - COMMISSION_PER_LEG * S['qty'])
                log('short_tp', leg='short', expiry=S['expiry'], strike=S['strike'], qty=S['qty'],
                    price=px, cash=self.st['cycle_cash'],
                    note=f'kept ${(S["credit"] - px) * 100 * S["qty"]:.0f} of ${S["credit"] * 100 * S["qty"]:.0f}')
                self.st['short'] = None
                save_state(self.st)

    def close_short(self, why):
        S = self.st.get('short')
        if not S:
            return True
        self.check_tp()
        if not self.st.get('short'):
            return True
        self.cancel_tp()
        self.check_tp()                 # it may have filled just before the cancel
        if not self.st.get('short'):
            return True
        [c] = self.ib.qualifyContracts(self.call(S['expiry'], S['strike']))
        px = self.work(c, 'BUY', S['qty'], why)
        if px is None:
            if not self.dry:
                log('close_fail', leg='short', expiry=S['expiry'], strike=S['strike'],
                    note='buy-back did not fill - CLOSE IT BY HAND in TWS')
            return False
        self.cash(-px * 100 * S['qty'] - COMMISSION_PER_LEG * S['qty'])
        log('buy_short', leg='short', expiry=S['expiry'], strike=S['strike'], qty=S['qty'],
            price=px, cash=self.st['cycle_cash'],
            note=f'{why}; short P&L ${(S["credit"] - px) * 100 * S["qty"]:+.0f}')
        self.st['short'] = None
        save_state(self.st)
        return True

    def close_cycle(self, spot, why):
        if not self.close_short('closing before rolling the long'):
            return
        L = self.st['long']
        [c] = self.ib.qualifyContracts(self.call(L['expiry'], L['strike']))
        px = self.work(c, 'SELL', L['qty'], 'close long')
        if px is None:
            if not self.dry:
                log('close_fail', leg='long', expiry=L['expiry'], strike=L['strike'],
                    note='long did not sell - will retry tomorrow')
            return
        self.cash(px * 100 * L['qty'] - COMMISSION_PER_LEG * L['qty'])
        pnl = self.st['cycle_cash']
        log('sell_long', leg='long', expiry=L['expiry'], strike=L['strike'], qty=L['qty'],
            price=px, cash=pnl, cycle_pnl=pnl, spot=round(spot, 2),
            note=f'{why}; cycle P&L after commissions ${pnl:+,.2f}')
        self.st['cycles'].append({'opened': L['opened'], 'closed': self.today.isoformat(),
                                  'pnl': pnl})
        self.st['long'] = None
        self.st['cycle_cash'] = 0.0
        save_state(self.st)

    def expiry_day_close(self):
        S = self.st.get('short')
        if S and S['expiry'] == self.today.strftime('%Y%m%d'):
            self.close_short('expires today - avoiding assignment')

    def status(self, spot=None):
        L, S = self.st.get('long'), self.st.get('short')
        spot = spot if spot is not None else self.spot()
        print('\n--- diagonal status ---')
        if not L:
            print('  no position')
        else:
            legs = [self.call(L['expiry'], L['strike'])] + \
                   ([self.call(S['expiry'], S['strike'])] if S else [])
            legs = self.ib.qualifyContracts(*legs)
            q = self.quote(legs)
            val = q[0][2] * 100 * L['qty'] if ok(q[0][2]) else float('nan')
            sval = q[1][2] * 100 * S['qty'] if S and ok(q[1][2]) else 0.0
            print(f'  SPY {spot:.2f} | long {L["strike"]}C {L["expiry"]} '
                  f'({self.dte(L["expiry"])}d) bought {L["cost"]:.2f}, now {q[0][2]:.2f}')
            if S:
                print(f'  short {S["strike"]}C {S["expiry"]} ({self.dte(S["expiry"])}d) '
                      f'sold {S["credit"]:.2f}, now {q[1][2]:.2f}')
            if ok(val):
                open_pnl = self.st['cycle_cash'] + val - sval - COMMISSION_PER_LEG * L['qty'] * (2 if S else 1)
                print(f'  this cycle so far (if closed now, after commissions): ${open_pnl:+,.2f}')
        done = self.st.get('cycles', [])
        if done:
            tot = sum(c['pnl'] for c in done)
            wins = sum(c['pnl'] > 0 for c in done)
            print(f'  finished cycles: {len(done)}, {wins} winners, total ${tot:+,.2f}')
        print('-----------------------\n')


# ================================== RUN ======================================
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='pick strikes and log, no orders')
    ap.add_argument('--no-short', action='store_true', help="don't sell a new weekly short today")
    ap.add_argument('--status', action='store_true', help='print position and P&L, then exit')
    a = ap.parse_args()
    if PORT in (7496, 4001) and not ALLOW_LIVE:
        sys.exit('PORT is a LIVE port. Set ALLOW_LIVE = True if you really mean it.')
    if now_et().weekday() >= 5 and not a.status:
        sys.exit('Weekend - market closed.')

    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=20)
    ib.reqMarketDataType(MARKET_DATA_TYPE)
    bot = Diagonal(ib, dry_run=a.dry_run, no_short=a.no_short)
    if a.status:
        bot.status()
        ib.disconnect()
        return

    did_morning = False
    while True:
        m = minute_now()
        if not ib.isConnected():
            print('Disconnected - reconnecting ...')
            try:
                ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=20)
                ib.reqMarketDataType(MARKET_DATA_TYPE)
            except Exception as e:
                print(f'  reconnect failed: {e}')
                ib.sleep(30)
                continue
        if not did_morning and m >= hm(ACTION_TIME):
            did_morning = True
            if m >= hm(EXIT_TIME):
                print('Started after the close window - nothing to do today.')
                break
            bot.morning()
            S = bot.st.get('short')
            if (not S or S['expiry'] != bot.today.strftime('%Y%m%d')) and not bot.dry:
                print('Nothing expires today - done.')
                break
        bot.check_tp()
        if did_morning and m >= hm(EXPIRY_CLOSE):
            bot.expiry_day_close()
            break
        if did_morning and bot.dry:
            break
        ib.sleep(30)
    ib.disconnect()
    print('Done for the day.')


if __name__ == '__main__':
    main()
