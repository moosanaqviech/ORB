import ConnectAPI
from ib_insync import *
#util.logToConsole('DEBUG')
import _datetime
import datetime
import math
import nest_asyncio
from ib_insync.util import schedule
import schedule
import time

from pandas import DataFrame as df
nest_asyncio.apply()
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)
spx = Index("SPX", 'CBOE', 'USD')
ib.qualifyContracts(spx)
now = _datetime.datetime.now()
today945am = now.replace(hour=9, minute=45, second=0, microsecond=0)
symbol15minlow=0
symbol15minhigh=0
data5sec= df
isNotTradedUp = True
isNotTradedDown = True

def setOpeningRange():
    bars = ib.reqHistoricalData(
                    spx, endDateTime='', durationStr='1 D',
                    barSizeSetting='15 mins', whatToShow='TRADES', useRTH=False,
                    formatDate=1, keepUpToDate=True)
    for index, bar in enumerate(bars):
        if bar.date == today945am:
            global symbol15minlow
            symbol15minlow = bars[index-1].low
            global symbol15minhigh
            symbol15minhigh = bars[index-1].high
            print(symbol15minlow , " <-low,->high->" , symbol15minhigh)
    global data5sec
    # Request 5sec Streaming bars
    data5sec = ib.reqHistoricalData(
        spx,
        endDateTime='',
        durationStr='1 D',
        barSizeSetting='5 secs',
        whatToShow='TRADES',
        useRTH=False,
        keepUpToDate=True,
    )
    
    # Set callback function for streaming bars
    data5sec.updateEvent += on_5sec_new_bar
    
   
    
    # Run infinitely 
    ib.run()
            
def on_5sec_new_bar(bars: BarDataList, has_new_bar: bool):
    global isNotTradedUp
    global isNotTradedDown
    if has_new_bar:
        #print("5 secs")
        df = util.df(data5sec)
        #print(df.tail(2))
        #sma = df.close.tail(50).mean()
        #std_dev = df.close.tail(50).std() * 3

        # Check if we are in a trade
        # NOT REQUIRED if spx not in [i.contract for i in ib.positions()]:
            # We are not in a trade - Look for a signal
        closePrice = df.close.iloc[-1]
        #CHECKING > 0 CONDITION MIGHT BE OBSOLETE BECAUSE OF SCHDULER LOGIC
        if closePrice > symbol15minhigh and isNotTradedUp and symbol15minhigh > 0:
                #if isBullish:
                    print('inside call' , spx.symbol)
                    print(df.tail(2))
                    isNotTradedUp = False
                    createSpreadStrike( spx.symbol, 'BULLISH', 'premarketbreakout')
                    
        if closePrice < symbol15minlow and isNotTradedDown and symbol15minlow > 0:
                #if not isBullish:
                    print('inside put' , spx.symbol)
                    print(df.tail(2))
                    isNotTradedDown = False
                    createSpreadStrike(spx.symbol, 'BEARISH', 'premarketbreakout')
                    
            


def createSpreadStrike(symbol, direction, strategi):
    print('increateOptncontract')
    acc = [v for v in ib.accountValues() if v.tag == 'NetLiquidationByCurrency' and v.currency == 'BASE']
    print(acc[0].value)
    accBalance = acc[0].value
    #sym = Index('spx', 'CBOE', 'USD')
    #ib.qualifyContracts(spx)
    
    ib.reqMarketDataType(4)
    #chains = ib.reqSecDefOptParams(sym.symbol, '', sym.secType, sym.conId)
    #util.df(chains)
    #print(chains)
    if (direction == "BULLISH"):
        strike1 = (symbol15minlow - symbol15minlow % 10) - 10
        strike2 = strike1 - 10
        
        print(symbol15minlow, strike1)
        #strike1 = barHigh - barHigh % 10 + 10
        createSpreadOrder(strike1, strike2, direction)
    if (direction == "BEARISH"):
        strike1 = (symbol15minhigh - symbol15minhigh % 10) + 10
        strike2 = strike1 + 10
        
        print(symbol15minlow, strike1)
        #strike1 = barHigh - barHigh % 10 + 10
        createSpreadOrder(strike1, strike2, direction)
    
                                  
def createSpreadOrder(strike1, strike2, direction):
    expiration = retToday() 
    optionType = 'C' if direction == 'BULLISH' else 'P' 
    spread_contract = Contract()
    spread_contract.symbol = "SPX"
    spread_contract.secType = "BAG"
    spread_contract.currency = "USD"
    spread_contract.exchange = "CBOE"
    option1 = Option('SPX', expiration, strike1, optionType, 'CBOE')
    option2 = Option('SPX', expiration, strike2, optionType, 'CBOE')
    
    
    # ib.qualifyContracts(option2)
    contracts = [option1, option2]
    ib.qualifyContracts(*contracts)
    leg1 = ComboLeg()
    leg1.conId = option1.conId
    leg1.ratio = 1
    leg1.action = "SELL"
    leg1.exchange = "CBOE"
    
    leg2 = ComboLeg()
    leg2.conId = option2.conId
    leg2.ratio = 1
    leg2.action = "BUY"
    leg2.exchange = "CBOE"
    
    spread_contract.comboLegs = []
    spread_contract.comboLegs.append(leg1)
    spread_contract.comboLegs.append(leg2)
    ticker = ib.reqMktData(spread_contract,'221,233')
    ib.sleep(2)
    print('ticker',ticker)
    mid = (ticker.ask + ticker.bid)/2
    mid = math.floor(mid / 0.05) * 0.05
    mid = round(mid, 2)
    print('ticker mid price - > ', mid)
    print(spread_contract)
    profitPrice = mid - (mid/2) 
    profitPrice =  math.floor(profitPrice / 0.05) * 0.05
    profitPrice = round(profitPrice, 2)
    stoploss = math.floor((mid*2) / 0.05) * 0.05
    stoploss  =  round(stoploss, 2)
    print(profitPrice, stoploss)
    order = ib.bracketOrder('BUY', 1, mid, profitPrice, stoploss, outsideRth=False, tif='GTC')
    order.parent.orderType = "LMT"
    for ord in order:
        ib.placeOrder(spread_contract, ord)
        ib.sleep(1)
def retToday():
    today = datetime.date.today()
    today = today.strftime("%Y%m%d")
    return today
#ib.reqMarketDataType(4)
if __name__ == '__main__':
    
    setOpeningRange()
    schedule.every().day.at("09:46").do(setOpeningRange)
    while 1:
        schedule.run_pending()
        time.sleep(1)
   