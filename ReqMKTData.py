from ibapi.client import *
from ibapi.wrapper import *

class TestApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)

    def nextValidId(self, orderId: int):
        
        mycontract = Contract()
        mycontract.symbol = "SPY"
        mycontract.secType = "STK"
        mycontract.exchange = "SMART"
        mycontract.currency = "USD"

        self.reqMarketDataType(4)
        self.reqMktData(orderId, mycontract, "", 0, 0, [])

    def tickPrice(self, reqId, tickType, price, attrib):
        print(f"tickPrice. reqId: {reqId}, tickType: {TickTypeEnum.to_str(tickType)}, price: {price}, attribs: {attrib}")

    def tickSize(self, reqId, tickType, size):
        print(f"tickSize. reqId: {reqId}, tickType: {TickTypeEnum.to_str(tickType)}, size: {size}")

        

app = TestApp()
app.connect("127.0.0.1", 7496, 1000)
app.run()