from ib_insync import *
util.startLoop()

ib = IB()
ib.connect('127.0.0.1', 7496, clientId=13)
positions = ib.positions()
for position in positions:
    print(position)
print('_________________________________________')
trades = ib.trades()
for trade in trades:
    print(trade)
