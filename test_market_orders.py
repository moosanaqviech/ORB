"""
test_market_orders.py - end-to-end order test on the IBKR PAPER account.

Opens 1 SPY 0DTE put credit spread and 1 SPY 0DTE short iron butterfly with
MARKET orders (BAG combos, same leg convention as spy0dtecreditspread.py),
waits for the fills, prints positions, then closes both with MARKET orders.

Run:
  python test_market_orders.py                 # both, open then close
  python test_market_orders.py --only cs       # credit spread only
  python test_market_orders.py --only ifly     # iron butterfly only
  python test_market_orders.py --keep          # open but do NOT close
  python test_market_orders.py --hold 30       # seconds to hold before closing (default 10)

Refuses to run on a live port. Market must be open (09:30-16:00 ET).
"""

import argparse
import sys

import nest_asyncio
from ib_insync import IB, ComboLeg, Contract, LimitOrder, MarketOrder, Option, Stock

nest_asyncio.apply()

HOST = '127.0.0.1'
PORT = 7497                  # TWS paper 7497 / Gateway paper 4002
CLIENT_ID = 31               # different from the strategy scripts
MARKET_DATA_TYPE = 3         # 1 live, 3 delayed
SYMBOL = 'SPY'
QTY = 1
CS_OTM = 3.0                 # credit spread: short put this many $ below spot
CS_WIDTH = 2.0               # credit spread wing width
IFLY_WIDTH = 5.0             # iron butterfly wing width
FILL_TIMEOUT = 30            # seconds to wait for a market fill
TAG = 'TEST-MKT'

if PORT in (7496, 4001):
    sys.exit('Live port - this test script only runs on paper.')


def connect():
    ib = IB()
    ib.errorEvent += lambda reqId, code, msg, c: (
        None if code in (2104, 2106, 2107, 2108, 2158, 2119, 10167)
        else print(f'  IB error {code}: {msg}'))
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=20)
    ib.reqMarketDataType(MARKET_DATA_TYPE)
    acct = ib.managedAccounts()
    print(f'Connected {HOST}:{PORT} accounts={acct}')
    if acct and not all(a.startswith('D') for a in acct):
        print('  WARNING: account id does not start with "D" (paper accounts usually do)')
    return ib


def get_spot(ib, und):
    t = ib.reqMktData(und, '', False, False)
    ib.sleep(3)
    p = t.marketPrice()
    ib.cancelMktData(und)
    if p == p and p > 0:
        return p
    bars = ib.reqHistoricalData(und, '', '1800 S', '1 min', 'TRADES', True)
    if not bars:
        sys.exit(f'No {SYMBOL} price available.')
    print('  no quote - using last 1-min bar')
    return bars[-1].close


def nearest(strikes, x):
    return min(strikes, key=lambda k: abs(k - x))


def bag(legs):
    """legs = [(contract, 'BUY'|'SELL'), ...]"""
    c = Contract(symbol=SYMBOL, secType='BAG', currency='USD', exchange='SMART')
    c.comboLegs = [ComboLeg(conId=k.conId, ratio=1, action=a, exchange='SMART') for k, a in legs]
    return c


def send(ib, combo, action, label, width=None):
    """2-leg combos: true MARKET order. IBKR rejects MKT on combos with more than
    2 legs (error 321), so for those send a marketable LIMIT instead: SELL at 0.01,
    BUY at the wing width (the combo can never be worth more) - fills like market."""
    if len(combo.comboLegs) <= 2:
        order, kind = MarketOrder(action, QTY, tif='DAY', orderRef=TAG), 'MKT'
    else:
        px = 0.01 if action == 'SELL' else width
        order, kind = LimitOrder(action, QTY, px, tif='DAY', orderRef=TAG), f'LMT {px:.2f} (marketable)'
    trade = ib.placeOrder(combo, order)
    print(f'  {label}: {action} {QTY} {kind} sent (orderId {trade.order.orderId})')
    for _ in range(FILL_TIMEOUT):
        ib.sleep(1)
        if trade.isDone():
            break
    s = trade.orderStatus
    print(f'  {label}: status={s.status} filled={s.filled} avgPrice={s.avgFillPrice}')
    if s.status != 'Filled':
        for e in trade.log:
            if e.message:
                print(f'    log: {e.status} {e.message}')
        if not trade.isDone():
            ib.cancelOrder(trade.order)
            print(f'  {label}: not filled in {FILL_TIMEOUT}s - cancelled')
    return s.status == 'Filled'


def show_positions(ib, expiry):
    ib.sleep(2)
    pos = [p for p in ib.positions() if p.contract.symbol == SYMBOL
           and p.contract.secType == 'OPT'
           and p.contract.lastTradeDateOrContractMonth == expiry]
    print(f'  {SYMBOL} {expiry} option positions: {len(pos)}')
    for p in sorted(pos, key=lambda p: (p.contract.right, p.contract.strike)):
        print(f'    {p.contract.right} {p.contract.strike:g}  qty {p.position:+g}  avgCost {p.avgCost:.2f}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', choices=['cs', 'ifly'])
    ap.add_argument('--keep', action='store_true', help='do not close after opening')
    ap.add_argument('--hold', type=int, default=10, help='seconds before closing')
    a = ap.parse_args()

    ib = connect()
    und = Stock(SYMBOL, 'SMART', 'USD')
    ib.qualifyContracts(und)
    chains = ib.reqSecDefOptParams(und.symbol, '', 'STK', und.conId)
    chain = next((c for c in chains if c.tradingClass == SYMBOL and c.exchange == 'SMART'), None)
    expiry = sorted(chain.expirations)[0]        # nearest expiry (today on a trading day)
    strikes = sorted(chain.strikes)
    spot = get_spot(ib, und)
    print(f'{SYMBOL} spot {spot:.2f}  expiry {expiry}')

    def opt(k, r):
        return Option(SYMBOL, expiry, k, r, 'SMART', tradingClass=SYMBOL, currency='USD')

    tests = []
    if a.only in (None, 'cs'):
        ks = nearest(strikes, spot - CS_OTM)
        kl = ks - CS_WIDTH
        s, l = ib.qualifyContracts(opt(ks, 'P'), opt(kl, 'P'))
        # BUY short strike / SELL wing -> SELL the bag = open credit spread
        tests.append((f'PUT CREDIT SPREAD {ks:g}/{kl:g}', bag([(s, 'BUY'), (l, 'SELL')]), CS_WIDTH))
    if a.only in (None, 'ifly'):
        kc = nearest(strikes, spot)
        pw, cw, bp, bc = ib.qualifyContracts(opt(kc - IFLY_WIDTH, 'P'), opt(kc + IFLY_WIDTH, 'C'),
                                             opt(kc, 'P'), opt(kc, 'C'))
        # BUY body / SELL wings -> SELL the bag = open short iron butterfly
        tests.append((f'IRON BUTTERFLY {kc - IFLY_WIDTH:g}/{kc:g}/{kc + IFLY_WIDTH:g}',
                      bag([(bp, 'BUY'), (bc, 'BUY'), (pw, 'SELL'), (cw, 'SELL')]), IFLY_WIDTH))

    opened = []
    print('\n--- OPEN ---')
    for label, combo, width in tests:
        if send(ib, combo, 'SELL', label, width):
            opened.append((label, combo, width))
    show_positions(ib, expiry)

    if a.keep or not opened:
        print('\nLeaving positions open.' if opened else '\nNothing filled.')
    else:
        print(f'\nHolding {a.hold}s ...')
        ib.sleep(a.hold)
        print('--- CLOSE ---')
        for label, combo, width in opened:
            send(ib, combo, 'BUY', label, width)
        show_positions(ib, expiry)

    ib.disconnect()
    print('Done.')


if __name__ == '__main__':
    main()
