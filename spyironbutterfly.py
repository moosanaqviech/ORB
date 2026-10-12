#from ibapi.contract import Contract, ComboLeg
from ib_insync import *
import nest_asyncio
import math
import datetime
import schedule
import time
from pandas import DataFrame as df

nest_asyncio.apply()
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=2)
spx = Index("SPY", 'SMART', 'USD')
ib.qualifyContracts(spx)
ib.reqMarketDataType(4)
price = 0
data5sec = df


def createIronButterfly(bodyStrike, upperWingStrike, lowerWingStrike):
    expiration = retToday() 
    
    spread_contract = Contract()
    spread_contract.symbol = "SPY"
    spread_contract.secType = "BAG"
    spread_contract.currency = "USD"
    spread_contract.exchange = "SMART"
    option1 = Option('SPY', expiration, bodyStrike, 'C', 'SMART')
    option2 = Option('SPY', expiration, bodyStrike, 'P', 'SMART')
    option3 = Option('SPY', expiration, upperWingStrike, 'P', 'SMART')
    option4 = Option('SPY', expiration, lowerWingStrike, 'C', 'SMART')
    
    # ib.qualifyContracts(option2)
    contracts = [option1, option2, option3, option4]
    ib.qualifyContracts(*contracts)
    leg1 = ComboLeg()
    leg1.conId = option1.conId
    leg1.ratio = 1
    leg1.action = "SELL"
    leg1.exchange = "SMART"
    
    leg2 = ComboLeg()
    leg2.conId = option2.conId
    leg2.ratio = 1
    leg2.action = "SELL"
    leg2.exchange = "SMART"
    
    
    leg3 = ComboLeg()
    leg3.conId = option3.conId
    leg3.ratio = 1
    leg3.action = "BUY"
    leg3.exchange = "SMART"
    
    leg4 = ComboLeg()
    leg4.conId = option4.conId
    leg4.ratio = 1
    leg4.action = "BUY"
    leg4.exchange = "SMART"
    
    spread_contract.comboLegs = []
    spread_contract.comboLegs.append(leg1)
    spread_contract.comboLegs.append(leg2)
    spread_contract.comboLegs.append(leg3)
    spread_contract.comboLegs.append(leg4)
    ticker = ib.reqMktData(spread_contract,'221,233')
    ib.sleep(2)
    print('ticker',ticker)
    #mid = (ticker.ask + ticker.bid)/2
    #mid = math.floor(mid / 0.05) * 0.05
    #mid = round(mid, 2)
    #print('ticker mid price - > ', mid)
    print(spread_contract)
    profitPrice = ticker.ask + 1 
    profitPrice =  math.floor(profitPrice / 0.05) * 0.05
    profitPrice = round(profitPrice, 2)
    stoploss = ticker.ask - 1.5
    stoploss  =  round(stoploss, 2)
    print(profitPrice, stoploss)
    order = ib.bracketOrder('BUY', 1, ticker.ask, profitPrice, stoploss, outsideRth=False, tif='GTC')
    order.parent.orderType = "MKT"
    for ord in order:
        ib.placeOrder(spread_contract, ord)
        ib.sleep(1)
def retToday():
    today = datetime.date.today()
    today = today.strftime("%Y%m%d")
    return today 
def start():  
    global price  
    [ticker] = ib.reqTickers(spx)
    price = ticker.marketPrice()
    bodyStrike = price - price % 1
    upperWingStrike = bodyStrike - 2
    lowerWingStrike = bodyStrike + 2
    createIronButterfly(bodyStrike, upperWingStrike, lowerWingStrike)
    

if __name__ == '__main__':
    start()
    schedule.every().day.at("09:31").do(start)
    schedule.every().day.at("11:00").do(start)
    while 1:
        schedule.run_pending()
        time.sleep(1)