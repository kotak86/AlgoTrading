"""2 trading strategies for Apple (stock + options) using IB API"""

import ta
import time
import random
import threading
import datetime
import pandas as pd

from ibapi.client import *
from ibapi.wrapper import *

DATA_SUBSCRIPTION = "DELAYED"
SYMBOL = "AAPL"

class IBApi(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextorderId = None
        self.window = 100

        self.mkt_price = 0
        self.option_market_price = 0.0
        self.expiration_date = None
        
        self.option_contract = None
        self.open_stock = False
        self.open_option = False
        
        self.hist_data_rdy = False
        self.positions_rdy = False
        self.mkt_price_rdy = False
        self.option_chain_rdy = False
        self.option_price_id = None
        
        self.option_chain_dict: dict[str, list] = {}
        self.historical_data_dfs: dict[str, pd.DataFrame] = {}
        self.historical_data_lists: dict[str, list] = {"AAPL": []}

    def create_contract(self, symbol="AAPL", contract_type="STK"):
        contract = Contract()
        contract.symbol = symbol
        contract.secType = contract_type
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        return contract
        
    def calc_ema(self, time, symbol="AAPL", dummy=True):
        if dummy:
            return [100/time]
        
        ema_series = ta.trend.ema_indicator(self.historical_data_dfs[symbol]["close"], window=self.window)
        ema_list = ema_series.to_list()
        
        if f"ema{time}" not in self.historical_data_dfs[symbol]:
            self.historical_data_dfs[symbol][f"ema{time}"] = ema_list 
        
        return ema_list
    
    def calc_sma(self, time, symbol="AAPL", dummy=True):
        if dummy:
            return [100/time]
        
        sma_series = ta.trend.sma_indicator(self.historical_data_dfs[symbol]["close"], window=self.window)
        sma_list = sma_series.to_list()
        
        if f"sma{time}" not in self.historical_data_dfs[symbol]:
            self.historical_data_dfs[symbol][f"sma{time}"] = sma_list
        
        return sma_list

    def add_mkt_price_row(self, symbol):
        mkt_price_row  = pd.DataFrame({"date": [datetime.datetime.now()], "close": [self.mkt_price]})
        mkt_price_row.set_index('date', inplace=True)
        self.historical_data_dfs[symbol] = pd.concat([self.historical_data_dfs[symbol], mkt_price_row])

    def get_option_contract(self):
        atm_strike = self.get_atm_strike(self.mkt_price)
        print(self.expiration_date)
        print("atm strike:", atm_strike)
        option_contract = self.create_contract(contract_type="OPT")
        option_contract.right = "C"
        option_contract.strike = atm_strike
        option_contract.lastTradeDateOrContractMonth = self.expiration_date
        
        self.option_contract = option_contract
        return option_contract

    def execute_strategy1(self):
        """
        1) Buy Apple ATM call option for next available Strike Price for expiry 90+ days from current date if SMA 50 GreaterThan SMA 100 for 1 Hr timeframe
        Target Price: 25 %
        Stoploss: 10 %
        """
        sma50 = self.calc_sma(50)[-1]
        sma100 = self.calc_sma(100)[-1]
        
        if sma50 > sma100:
            print("price:", self.option_market_price)
            print(self.option_market_price*0.9)
            print(self.option_market_price*1.25)
            
            mult = 0.05
            self.create_buy_order(self.nextorderId, self.option_contract, bracket_prices=(round(math.floor(self.option_market_price*0.9 / mult) * mult, 2),
                                                                                          round(math.floor(self.option_market_price*1.25 / mult) * mult, 2)))
            
        return True
        
    def execute_strategy2(self):
        """
        2) Buy 1 Apple Stock For following condition
        if
        SMA 50 GreaterThan SMA 100 and
        ema5 GreaterThan ema8
        and ema8 GreaterThan ema21
        and ema21 GreaterThan ema34
        for 1 Hr timeframe 
        With Trailing Stop: 5 %
        """
        if self.historical_data_dfs["AAPL"] is not None:
            ema5 = self.calc_ema(5)[-1]
            ema8 = self.calc_ema(8)[-1]
            ema21 =self.calc_ema(21)[-1]
            ema34 = self.calc_ema(34)[-1]
            sma50 = self.calc_sma(50)[-1]
            sma100 = self.calc_sma(100)[-1]
            
            if (sma50 > sma100) and (ema5 > ema8) and (ema8 > ema21) and (ema21 > ema34):
                aapl_contract = self.create_contract()
                self.create_buy_order(self.nextorderId, aapl_contract, trailing_percentage=5)
                
        return True
                
    def create_buy_order(self, reqId, contract: ContractDetails,
                          bracket_prices: tuple[Decimal, Decimal] = None,
                          trailing_percentage=None):

        order = Order()
        order.orderId = reqId
        order.action = "Buy"
        order.orderType = "MKT"
        order.totalQuantity = 1
        if bracket_prices or trailing_percentage:
            order.transmit = False
        self.nextorderId += 1
        
        self.placeOrder(order.orderId, contract, order)
        
        if bracket_prices:
            bracket_orders = self.create_bracket_orders(order.orderId, contract, bracket_prices[0], bracket_prices[1])
            #stop-loss
            print(bracket_orders[0].orderId)
            self.placeOrder(bracket_orders[0].orderId, contract, bracket_orders[0])
            #take profit
            self.placeOrder(bracket_orders[1].orderId, contract, bracket_orders[1])
        elif trailing_percentage:
            trailing_order = self.create_trailing_order(order.orderId, trailing_percentage)
            print(trailing_order.orderId)
            self.placeOrder(trailing_order.orderId, contract, trailing_order)
                
            
    def create_bracket_orders(self, parent_id, contract, stop_price, take_profit_price):
        #Create stop loss order
        stop_order = Order()
        stop_order.action = 'SELL'
        stop_order.totalQuantity = 1
        stop_order.orderType = 'STP'
        stop_order.auxPrice = stop_price
        stop_order.orderId = self.nextorderId
        stop_order.parentId = parent_id
        stop_order.tif = "GTC"
        stop_order.transmit = False
        self.nextorderId += 1

        take_profit_order = Order()
        take_profit_order.action = 'SELL'
        take_profit_order.totalQuantity = 1
        take_profit_order.orderType = 'LMT'
        take_profit_order.lmtPrice = take_profit_price
        take_profit_order.orderId = self.nextorderId
        take_profit_order.parentId = parent_id
        take_profit_order.tif = "GTC"
        take_profit_order.transmit = False
        self.nextorderId += 1

        return (stop_order, take_profit_order)
    
    def create_trailing_order(self, parent_id, trailingPercent: int):
        trailing_order = Order()
        trailing_order.action = 'SELL'
        trailing_order.totalQuantity = 1
        trailing_order.orderType = 'TRAIL'
        trailing_order.trailingPercent = trailingPercent
        trailing_order.orderId = self.nextorderId
        trailing_order.parentId = parent_id
        trailing_order.transmit = False
        self.nextorderId += 1
        
        #trailing_order.action = 'SELL'
        return trailing_order
        
    def get_atm_strike(self, mkt_price, symbol="AAPL"):
        atm_strike = math.inf
        for strike in self.option_chain_dict[symbol]:
            if abs(strike - mkt_price) < abs(atm_strike - mkt_price):
                atm_strike = strike
    
        return atm_strike
    
    ############ Overwrites ############
    def execDetails(self, reqId, contract, execution):
        print('Order Executed: ', reqId, contract.symbol, contract.secType, contract.currency, execution.execId, execution.orderId, execution.shares, execution.lastLiquidity)
        
    def openOrder(self, orderId, contract, order, orderState):
        print('openOrder id:', orderId, contract.symbol, contract.secType, '@', contract.exchange, ':', order.action, order.orderType, order.totalQuantity, orderState.status)

    def contractDetails(self, reqId, contractDetails):
        if contractDetails.contract.secType == "OPT":
            symbol = contractDetails.contract.symbol
            expiration_date = contractDetails.contract.lastTradeDateOrContractMonth
            
            self.option_chain_dict[symbol].append(contractDetails.contract.strike)
            self.expiration_date = expiration_date
            self.option_chain_rdy = True
            # print(f"contract details: {contractDetails.contract.strike}")
            # print(f"contract details: {contractDetails.contract.lastTradeDateOrContractMonth}")
            
        
    def contractDetailsEnd(self, reqId:int):
        print("end contract details")
        self.option_chain_processed = True
            
        
    def tickPrice(self, reqId, tickType, price, attrib):
        if reqId == self.option_price_id:
            self.option_market_price = price
            return
        if TickTypeEnum.to_str(tickType) == "BID" or TickTypeEnum.to_str(tickType) == "DELAYED_BID":
            self.mkt_price = price
            self.mkt_price_rdy = True

    def tickSize(self, reqId, tickType, size):
        pass
        # print(reqId, TickTypeEnum.to_str(tickType), size)
        
    def historicalData(self, reqId, bar):
        bar.date = " ".join(bar.date.split(" ")[:-1])
        self.historical_data_lists["AAPL"].append(vars(bar))

    def historicalDataEnd(self, reqId, start, end):
        print(f"Start: {start}, End: {end}")
        self.historical_data_dfs["AAPL"] = pd.DataFrame(self.historical_data_lists["AAPL"])
        self.historical_data_dfs["AAPL"]['date'] = pd.to_datetime(self.historical_data_dfs["AAPL"]['date'])
        self.historical_data_dfs["AAPL"].set_index('date', inplace=True)
        
        self.hist_data_rdy = True
        
    def position(self, account: str, contract: Contract, position: Decimal,
                 avgCost: float):
        super().position(account, contract, position, avgCost)
        print("Position.", "Account:", account, "Symbol:", contract.symbol, "SecType:",
              contract.secType, "Currency:", contract.currency,
              "Position:", decimalMaxString(position), "Avg cost:", floatMaxString(avgCost))
        
        
        if contract.symbol == "AAPL" and contract.secType == "STK":
            if position > 0:
                self.open_stock = True
        if contract.symbol == "AAPL" and contract.secType == "OPT":    
            if position > 0:
                self.open_option = True
    
    def positionEnd(self):
        super().positionEnd()
        self.positions_rdy = True
        print("PositionEnd")
        
    def nextValidId(self, orderId: id):
        super().nextValidId(orderId)
        self.nextorderId = orderId
        print('The next valid order id is: ', self.nextorderId)
        
def run_loop():
    app.run()
    
def request_historical_data(app, symbol):
    aapl_contract = app.create_contract()
    today = str(datetime.date.today()).replace("-", "")
    hour = datetime.datetime.now().hour
    app.reqHistoricalData(app.nextorderId, aapl_contract, f"{today} {hour}:00:00 UTC", "30 D", "1 hour", "TRADES", 0, 1, 0, [])
    
if __name__ == "__main__":
    app = IBApi()
    app.connect("127.0.0.1", 7497, clientId=1)
    
    app.nextorderId = None

    #Start the socket in a thread
    api_thread = threading.Thread(target=run_loop, daemon=True)
    api_thread.start()
    
    #Check if the API is connected via orderid
    while True:
        if isinstance(app.nextorderId, int):
            print('connected')
            break
        else:
            print('waiting for connection')
            time.sleep(1)
            
    if DATA_SUBSCRIPTION == "DELAYED":
        app.reqMarketDataType(3)
                
    #set option chain
    if SYMBOL not in app.option_chain_dict:
        app.option_chain_dict[SYMBOL] = []
        option_contracts = app.create_contract(symbol=SYMBOL, contract_type="OPT")
        max_expiration_date=datetime.datetime.now() + datetime.timedelta(days=90)
        max_expiration_string = str(max_expiration_date.year) + str(max_expiration_date.month)
        option_contracts.lastTradeDateOrContractMonth = max_expiration_string
        option_contracts.right = "C"
    
        app.reqContractDetails(app.nextorderId, option_contracts)

    while True:    
        app.hist_data_rdy = app.positions_rdy = app.option_chain_rdy = app.mkt_price_rdy = False
    
        request_historical_data(app, SYMBOL)
        app.reqMktData(app.nextorderId, app.create_contract(), "", 0, 0, [])
        app.reqPositions()
    
        while True:
            #print(app.hist_data_rdy, app.positions_rdy, app.option_chain_rdy, app.mkt_price_rdy)
            if app.hist_data_rdy and app.positions_rdy and app.option_chain_rdy and app.mkt_price_rdy:
                break
            print("waiting while")
            time.sleep(2)
            
        app.cancelMktData(app.nextorderId)
        #add the mkt price to historical data as last row
        app.add_mkt_price_row(SYMBOL)
                
        app.nextorderId += 1
        if app.open_option == False:
            app.option_price_id = app.nextorderId
            app.reqMktData(app.nextorderId, app.get_option_contract(), "", 0, 0, [])
            while True:
                time.sleep(2)
                if app.option_market_price > 0:
                    break
                print("wait for options price")
            s1_success = app.execute_strategy1()
    
        if app.open_stock == False:
            print("executing strategy 2")
            s2_success = app.execute_strategy2()
            
        time.sleep(20)
    
    
    time.sleep(5)
    app.disconnect()
    