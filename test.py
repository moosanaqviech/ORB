import datetime
from ib_insync import *
#util.logToConsole('DEBUG')
import nest_asyncio
import math
import datetime
import schedule
import time
from pandas import DataFrame as df

nest_asyncio.apply()
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=2, timeout=10)
#spx = Index("NDX", 'NASDAQ', 'USD')
spx = Index("SPX", 'CBOE', 'USD')
ib.qualifyContracts(spx)
ib.reqMarketDataType(4)

[ticker] = ib.reqTickers(spx)
price = ticker.marketPrice()
print(ticker)
data5sec = ib.reqHistoricalData(
        spx,
        #endDateTime='20230101-15:59:00',
        endDateTime='', 
        durationStr='2 Y',
        #barSizeSetting='15 mins',
        barSizeSetting='1 day',
        whatToShow='TRADES',
        useRTH=True,
        keepUpToDate=False,
    )
df = util.df(data5sec)
print(df.iloc[0])
df.to_csv('daily2YearNDX.csv')
mid = -0.9
mid = round(mid, 2)
print('ticker mid price - > ', mid)
profitPrice = mid - (mid/2) 
#profitPrice = profitPrice + (profitPrice%5)
profitPrice = round(profitPrice, 2)
stoploss = mid*2# + ((mid + mid)%5)
stoploss  =  round(stoploss, 2)

print(profitPrice, stoploss)

def retToday():
    today = datetime.date.today() + datetime.timedelta(days=1)
    today = today.strftime("%Y%m%d")
    return today 

if __name__ == '__main__':
   print( retToday())