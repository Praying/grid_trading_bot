import logging, bisect
from tabulate import tabulate
from .order import Order, OrderType
from .grid_level import GridLevel, GridCycleState

class OrderManager:
    def __init__(self, config_manager):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_manager = config_manager
        self.trading_fee = self.config_manager.get_trading_fee()
        self.trade_percentage = 0.1 ## TODO: trade_percentage based on num_grids instead ? Default to 10%
        self.grid_levels = {}
        self.sorted_buy_grids = []
        self.sorted_sell_grids = []
        self.total_trading_fees = 0

    def initialize_grid_levels(self, grids, central_price):
        self.grid_levels = {price: GridLevel(price, GridCycleState.READY_TO_BUY if price <= central_price else GridCycleState.READY_TO_SELL) for price in grids}
        self.sorted_buy_grids = [price for price in sorted(grids) if price <= central_price]
        self.sorted_sell_grids = [price for price in sorted(grids) if price > central_price]

    ## TODO: is exceptions handled here ?
    def execute_order(self, order_type: OrderType, current_price, previous_price, timestamp, balance, crypto_balance):
        if order_type == OrderType.BUY:
            if self.can_execute_buy_order(current_price, timestamp):
                return self.execute_buy(current_price, previous_price, timestamp, balance, crypto_balance)
        elif order_type == OrderType.SELL:
            if self.can_execute_sell_order(current_price, timestamp):
                return self.execute_sell(current_price, previous_price, timestamp, balance, crypto_balance)
        raise ValueError(f"Cannot place {order_type} order at this time.")
    
    def can_execute_buy_order(self, current_price, timestamp):
        idx = bisect.bisect_right(self.sorted_buy_grids, current_price)
        return idx > 0 and idx < len(self.sorted_buy_grids)

    def can_execute_sell_order(self, current_price, timestamp):
        idx = bisect.bisect_left(self.sorted_sell_grids, current_price)
        return idx > 0 and idx < len(self.sorted_sell_grids)
    
    def detect_grid_level_crossing(self, current_price, previous_price, sell=False):
        grid_list = self.sorted_sell_grids if sell else self.sorted_buy_grids
        for grid_price in grid_list:
            if (sell and previous_price < grid_price <= current_price) or (not sell and previous_price > grid_price >= current_price):
                return self.grid_levels[grid_price]
        return None

    def execute_buy(self, current_price, previous_price, timestamp, balance, crypto_balance):
        try:
            grid_level_crossed = self.detect_grid_level_crossing(current_price, previous_price)
            if self.is_valid_buy_level(grid_level_crossed):
                return self.place_buy_order(grid_level_crossed, current_price, timestamp, balance, crypto_balance)
            else:
                raise ValueError(f"Cannot place buy order. No valid grid level crossed or grid level is not ready to buy.")
        except ValueError as e:
            self.logger.error(f"Error executing buy order: {e}")
            return balance, crypto_balance

    def is_valid_buy_level(self, grid_level_crossed):
        return grid_level_crossed is not None and grid_level_crossed.can_place_buy_order()

    def place_buy_order(self, grid_level, current_price, timestamp, balance, crypto_balance):
        quantity = self.trade_percentage * balance / current_price
        try:
            grid_level.place_buy_order(Order(current_price, quantity, OrderType.BUY, timestamp))
        except ValueError as e:
            self.logger.error(f"Failed to place buy order at grid level {grid_level.price}: {e}")
            raise

        trade_value = quantity * current_price
        buy_fee = trade_value * self.trading_fee
        balance -= trade_value + buy_fee
        crypto_balance += quantity
        self.total_trading_fees += buy_fee
        return balance, crypto_balance

    def execute_sell(self, current_price, previous_price, timestamp, balance, crypto_balance):
        try:
            grid_level_crossed = self.detect_grid_level_crossing(current_price, previous_price, sell=True)
            
            if grid_level_crossed is None:
                self.logger.info("No grid level crossed for selling.")
                return balance, crypto_balance
            
            buy_grid_level = self.find_lowest_completed_buy_order_grid_level()
            if not buy_grid_level.buy_orders:
                self.logger.warning(f"No completed buy orders found for grid level {grid_level_crossed.price}")
                return balance, crypto_balance
            
            buy_order = buy_grid_level.buy_orders[-1] # Get the last buy order
            quantity = buy_order.quantity
            self.check_sufficient_crypto(crypto_balance, quantity, grid_level_crossed.price)
            return self.process_sell_order(grid_level_crossed, current_price, quantity, timestamp, balance, crypto_balance, buy_grid_level)

        except ValueError as e:
            self.logger.warning(f"Failed to place sell order: {e}")
            return balance, crypto_balance
    
    def process_sell_order(self, grid_level_crossed, current_price, quantity, timestamp, balance, crypto_balance, buy_grid_level):
        balance, crypto_balance = self.place_sell_order(grid_level_crossed, current_price, quantity, timestamp, balance, crypto_balance)
        self.reset_grid_cycle(buy_grid_level)
        return balance, crypto_balance
    
    def find_lowest_completed_buy_order_grid_level(self):
        for grid_level_price in self.sorted_buy_grids:
            grid_level = self.grid_levels.get(grid_level_price)
            if grid_level.can_place_sell_order():
                return grid_level
        raise ValueError("No grid level found with a completed buy order ready for a sell.")

    def check_sufficient_crypto(self, crypto_balance, quantity, grid_price):
        if crypto_balance < quantity:
            raise ValueError(f"Insufficient crypto balance to place sell order at {grid_price}")

    def place_sell_order(self, grid_level, current_price, quantity, timestamp, balance, crypto_balance):
        try:
            grid_level.place_sell_order(Order(current_price, quantity, OrderType.SELL, timestamp))
            trade_value = quantity * current_price
            sell_fee = trade_value * self.trading_fee
            balance += trade_value - sell_fee
            self.total_trading_fees += sell_fee
            crypto_balance -= quantity
            self.logger.info(f"Sell order placed at {current_price} on {timestamp}. Updated balance: {balance}, crypto balance: {crypto_balance}")
        except ValueError as e:
            self.logger.error(f"Failed to place sell order at {current_price} on {timestamp}. Error: {e}")
            return balance, crypto_balance
        return balance, crypto_balance

    def reset_grid_cycle(self, buy_grid_level):
        buy_grid_level.reset_buy_level_cycle()
        self.logger.info(f"Buy Grid level at price {buy_grid_level.price} is reset and ready for the next buy/sell cycle.")

    def display_orders(self):
        orders = []
        for grid_level in self.grid_levels.values():
            for buy_order in grid_level.buy_orders:
                orders.append(self.format_order(buy_order, grid_level))
            for sell_order in grid_level.sell_orders:
                orders.append(self.format_order(sell_order, grid_level))
        
        orders.sort(key=lambda x: x[3])  # x[3] is the timestamp
        print(tabulate(orders, headers=["Order Type", "Price", "Quantity", "Timestamp", "Grid Level"]))
    
    def format_order(self, order: Order, grid_level: GridLevel):
        order_type = "BUY" if order.order_type == OrderType.BUY else "SELL"
        return [order_type, order.price, order.quantity, order.timestamp, grid_level.price]