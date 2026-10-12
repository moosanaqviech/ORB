#from ibapi.contract import Contract, ComboLeg
from ib_insync import *
import nest_asyncio
import math
import datetime
import schedule
import time
from pandas import DataFrame as df
"""ib = IB()
client_id = 1
ib.connect('127.0.0.1', 7496, clientId=client_id)
sym = Index("SPX", 'CBOE', 'USD')
ib.qualifyContracts(sym)

ib.reqMarketDataType(4)


[ticker] = ib.reqTickers(sym)
price = ticker.marketPrice()
print(ticker)
"""
nest_asyncio.apply()
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=2)
spx = Index("SPX", 'CBOE', 'USD')
ib.qualifyContracts(spx)
ib.reqMarketDataType(4)
price = 0
morningPrice = 0
data5sec = df


def createIronButterfly(bodyStrike, upperWingStrike, lowerWingStrike):
    expiration = retToday() 
    
    spread_contract = Contract()
    spread_contract.symbol = "SPX"
    spread_contract.secType = "BAG"
    spread_contract.currency = "USD"
    spread_contract.exchange = "SMART"
    option1 = Option('SPX', expiration, bodyStrike, 'C', 'CBOE')
    option2 = Option('SPX', expiration, bodyStrike, 'P', 'CBOE')
    option3 = Option('SPX', expiration, upperWingStrike, 'P', 'CBOE')
    option4 = Option('SPX', expiration, lowerWingStrike, 'C', 'CBOE')
    
    # ib.qualifyContracts(option2)
    contracts = [option1, option2, option3, option4]
    ib.qualifyContracts(*contracts)
    leg1 = ComboLeg()
    leg1.conId = option1.conId
    leg1.ratio = 1
    leg1.action = "SELL"
    leg1.exchange = "CBOE"
    
    leg2 = ComboLeg()
    leg2.conId = option2.conId
    leg2.ratio = 1
    leg2.action = "SELL"
    leg2.exchange = "CBOE"
    
    
    leg3 = ComboLeg()
    leg3.conId = option3.conId
    leg3.ratio = 1
    leg3.action = "BUY"
    leg3.exchange = "CBOE"
    
    leg4 = ComboLeg()
    leg4.conId = option4.conId
    leg4.ratio = 1
    leg4.action = "BUY"
    leg4.exchange = "CBOE"
    
    spread_contract.comboLegs = []
    spread_contract.comboLegs.append(leg1)
    spread_contract.comboLegs.append(leg2)
    spread_contract.comboLegs.append(leg3)
    spread_contract.comboLegs.append(leg4)
    ticker = ib.reqMktData(spread_contract,'221,233')
    ib.sleep(2)
    print('*******************ticker**************',ticker.ask)
    #mid = (ticker.ask + ticker.bid)/2
    #mid = math.floor(mid / 0.05) * 0.05
    #mid = round(mid, 2)
    #print('ticker mid price - > ', mid)
    print(spread_contract)
    profitPrice = ticker.ask + 1.5 
    profitPrice =  math.floor(profitPrice / 0.05) * 0.05
    profitPrice = round(profitPrice, 2)
    stoploss = ticker.ask - 1.5
    stoploss  =  round(stoploss, 2)
    print(ticker.ask, profitPrice, stoploss)
    order = ib.bracketOrder('BUY', 1, ticker.ask, profitPrice, stoploss, outsideRth=False, tif='GTC')
    order.parent.orderType = "LMT"
    for ord in order:
        ib.placeOrder(spread_contract, ord)
        ib.sleep(1)
def retToday():
    today = datetime.date.today()+ datetime.timedelta(days=1)
    today = today.strftime("%Y%m%d")
    return today 
def start():  
    global price  
    [ticker] = ib.reqTickers(spx)
    price = ticker.marketPrice()
    bodyStrike = price - price % 5
    upperWingStrike = bodyStrike - 35
    lowerWingStrike = bodyStrike + 35
    createIronButterfly(bodyStrike, upperWingStrike, lowerWingStrike)
    """ # Request 5sec Streaming bars
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
    ib.run()"""

def on_5sec_new_bar(bars: BarDataList, has_new_bar: bool):
    if has_new_bar:
        df = util.df(bars)
        closePrice = df.close.iloc[-1]
        trades = ib.trades()

    for trade in trades:
        print(f'You have {trade} shares of ')
        if closePrice - price > 25 or closePrice - price < -25:
            print("Add close order")
if __name__ == '__main__':
    start()
    schedule.every().day.at("09:31").do(start)
    while 1:
        schedule.run_pending()
        time.sleep(1)