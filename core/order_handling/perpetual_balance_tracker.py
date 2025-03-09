import logging
from decimal import Decimal
from typing import Optional
from config.trading_mode import TradingMode
from .fee_calculator import FeeCalculator
from .order import Order, OrderSide, OrderStatus
from core.bot_management.event_bus import EventBus, Events
from ..validation.exceptions import InsufficientBalanceError, InsufficientMarginError
from core.services.exchange_interface import ExchangeInterface

class PerpetualBalanceTracker:
    def __init__(
        self, 
        event_bus: EventBus,
        fee_calculator: FeeCalculator, 
        trading_mode: TradingMode,
        base_currency: str,
        quote_currency: str,
        leverage: int = 1,
        initial_margin_ratio: float = 0.1,  # 初始保证金率
        maintenance_margin_ratio: float = 0.05,  # 维持保证金率
    ):
        """
        初始化永续合约余额追踪器。

        参数:
            event_bus: 事件总线实例，用于订阅事件。
            fee_calculator: 费用计算器实例，用于计算交易费用。
            trading_mode: 交易模式，可以是 "BACKTEST"、"LIVE" 或 "PAPER_TRADING"。
            base_currency: 基础货币符号（如 BTC）。
            quote_currency: 报价货币符号（如 USDT）。
            leverage: 杠杆倍数。
            initial_margin_ratio: 初始保证金率。
            maintenance_margin_ratio: 维持保证金率。
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.event_bus = event_bus
        self.fee_calculator = fee_calculator
        self.trading_mode = trading_mode
        self.base_currency = base_currency
        self.quote_currency = quote_currency
        self.leverage = leverage
        self.initial_margin_ratio = initial_margin_ratio
        self.maintenance_margin_ratio = maintenance_margin_ratio

        # 余额相关
        self.margin_balance: float = 0.0  # 保证金余额（USDT）
        self.reserved_margin: float = 0.0  # 已冻结的保证金
        self.total_fees: float = 0.0  # 累计手续费

        # 持仓相关
        self.long_position: float = 0.0  # 多头持仓数量
        self.short_position: float = 0.0  # 空头持仓数量
        self.long_avg_price: float = 0.0  # 多头平均持仓价格
        self.short_avg_price: float = 0.0  # 空头平均持仓价格
        self.unrealized_pnl: float = 0.0  # 未实现盈亏
        self.realized_pnl: float = 0.0  # 已实现盈亏
        self.funding_fees: float = 0.0  # 累计资金费用

        # 订阅事件
        self.event_bus.subscribe(Events.ORDER_FILLED, self._update_balance_on_order_completion)
        self.event_bus.subscribe(Events.FUNDING_FEE_CHARGED, self._handle_funding_fee)

    async def setup_balances(
        self, 
        initial_margin: float,
        exchange_service: ExchangeInterface
    ):
        """
        根据交易模式设置初始保证金余额。

        参数:
            initial_margin: 初始保证金金额（USDT）。
            exchange_service: 交易所接口实例。
        """
        if self.trading_mode == TradingMode.BACKTEST:
            self.margin_balance = initial_margin
        else:
            balances = await self._fetch_live_balances(exchange_service)
            self.margin_balance = balances['margin_balance']
            self.long_position = balances['long_position']
            self.short_position = balances['short_position']
            self.long_avg_price = balances['long_avg_price']
            self.short_avg_price = balances['short_avg_price']
            self.unrealized_pnl = balances['unrealized_pnl']

    async def _fetch_live_balances(self, exchange_service: ExchangeInterface) -> dict:
        """
        从交易所获取永续合约账户余额和持仓信息。

        参数:
            exchange_service: 交易所接口实例。

        返回:
            dict: 包含保证金余额、持仓信息等的字典。
        """
        result = {
            'margin_balance': 0.0,
            'long_position': 0.0,
            'short_position': 0.0,
            'long_avg_price': 0.0,
            'short_avg_price': 0.0,
            'unrealized_pnl': 0.0
        }
        
        # 获取余额信息
        balances = await exchange_service.get_balance()
        if not balances:
            raise ValueError("Failed to fetch perpetual balance information")
            
        # 获取保证金余额
        usdt_balance = balances['free'].get('USDT', 0.0)  # 如果没有USDT键，默认值为0.0
        result['margin_balance'] = float(usdt_balance)
        
        # 获取持仓信息
        symbol = self.base_currency + '/' + self.quote_currency + ':' + self.quote_currency
        position = await exchange_service.get_position(symbol)
        if not position:
            return result
        position_size = 0.0
        # 查找当前交易对的持仓
        if position['symbol'] == symbol:
            position_size = abs(float(position.get('contracts', 0)))
            if not position.get('unrealizedPnl'):
                result['unrealized_pnl'] = 0.0

            # 判断多空方向并设置相应的持仓信息
            if position.get('side') == 'long' or float(position.get('contracts', 0)) > 0:
                result['long_position'] = position_size
                result['long_avg_price'] = float(position.get('entryPrice', 0))
            elif position.get('side') == 'short' or float(position.get('contracts', 0)) < 0:
                result['short_position'] = position_size
                result['short_avg_price'] = float(position.get('entryPrice', 0))

        self.logger.info(f"合约账户余额 - 可用保证金: {result['margin_balance']}, 持仓量: {position_size}")
        return result

    def _calculate_required_margin(self, quantity: float, price: float) -> float:
        """
        计算开仓所需的保证金。

        参数:
            quantity: 合约数量。
            price: 合约价格。

        返回:
            float: 所需保证金金额。
        """
        contract_value = quantity * price
        return contract_value * self.initial_margin_ratio

    def _calculate_maintenance_margin(self, quantity: float, price: float) -> float:
        """
        计算维持保证金。

        参数:
            quantity: 合约数量。
            price: 合约价格。

        返回:
            float: 维持保证金金额。
        """
        contract_value = quantity * price
        return contract_value * self.maintenance_margin_ratio

    def _update_unrealized_pnl(self, current_price: float) -> None:
        """
        更新未实现盈亏。

        参数:
            current_price: 当前市场价格。
        """
        long_pnl = self.long_position * (current_price - self.long_avg_price) if self.long_position > 0 else 0
        short_pnl = self.short_position * (self.short_avg_price - current_price) if self.short_position > 0 else 0
        self.unrealized_pnl = long_pnl + short_pnl

    def get_available_margin(self) -> float:
        """
        获取可用保证金余额。

        返回:
            float: 可用保证金金额。
        """
        return self.margin_balance - self.reserved_margin

    def get_total_margin_balance(self) -> float:
        """
        获取总保证金余额（包括未实现盈亏）。

        返回:
            float: 总保证金余额。
        """
        return self.margin_balance + self.unrealized_pnl

    def fetch_margin_ratio(self, exchange: ExchangeInterface) -> float:
        pass

    def get_margin_ratio(self, current_price: float) -> float:
        """
        计算当前保证金率。

        参数:
            current_price: 当前市场价格。

        返回:
            float: 当前保证金率。
        """
        total_position_value = (self.long_position + self.short_position) * current_price
        if total_position_value == 0:
            return float('inf')
        return self.get_total_margin_balance() / total_position_value

    def check_margin_requirement(self, current_price: float) -> bool:
        """
        检查是否满足保证金要求。

        参数:
            current_price: 当前市场价格。

        返回:
            bool: 是否满足保证金要求。
        """
        margin_ratio = self.get_margin_ratio(current_price)
        return margin_ratio >= self.maintenance_margin_ratio

    def reserve_margin_for_order(self, quantity: float, price: float) -> None:
        """
        为订单预留保证金。

        参数:
            quantity: 合约数量。
            price: 合约价格。
        """
        required_margin = self._calculate_required_margin(quantity, price)
        if self.get_available_margin() < required_margin:
            raise InsufficientMarginError(f"Insufficient margin balance. Required: {required_margin}, Available: {self.get_available_margin()}")

        self.reserved_margin += required_margin
        self.logger.info(f"Reserved margin: {required_margin} USDT for order. Available margin: {self.get_available_margin()} USDT")

    async def _update_balance_on_order_completion(self, order: Order) -> None:
        """
        订单完成时更新余额和持仓。

        参数:
            order: 已完成的订单对象。
        """
        fee = self.fee_calculator.calculate_fee(order.filled * order.price)
        self.total_fees += fee

        # 计算订单所需的保证金
        required_margin = self._calculate_required_margin(order.filled, order.price)
        
        if order.side == OrderSide.BUY:  # 开多或平空
            if self.short_position > 0:  # 平空
                self._handle_close_position(order, OrderSide.SELL)
            else:  # 开多
                self._handle_open_position(order, OrderSide.BUY)
        else:  # 开空或平多
            if self.long_position > 0:  # 平多
                self._handle_close_position(order, OrderSide.BUY)
            else:  # 开空
                self._handle_open_position(order, OrderSide.SELL)

        # 释放预留的保证金
        self.reserved_margin -= required_margin
        if self.reserved_margin < 0:
            self.reserved_margin = 0

        # 扣除手续费
        self.margin_balance -= fee

    def _handle_open_position(self, order: Order, side: OrderSide) -> None:
        """
        处理开仓订单。

        参数:
            order: 订单对象。
            side: 订单方向。
        """
        if side == OrderSide.BUY:  # 开多
            new_position = self.long_position + order.filled
            new_cost = self.long_position * self.long_avg_price + order.filled * order.price
            self.long_position = new_position
            self.long_avg_price = new_cost / new_position if new_position > 0 else 0
        else:  # 开空
            new_position = self.short_position + order.filled
            new_cost = self.short_position * self.short_avg_price + order.filled * order.price
            self.short_position = new_position
            self.short_avg_price = new_cost / new_position if new_position > 0 else 0

    def _handle_close_position(self, order: Order, position_side: OrderSide) -> None:
        """
        处理平仓订单。

        参数:
            order: 订单对象。
            position_side: 持仓方向。
        """
        if position_side == OrderSide.BUY:  # 平多
            close_quantity = min(self.long_position, order.filled)
            pnl = close_quantity * (order.price - self.long_avg_price)
            self.long_position -= close_quantity
            if self.long_position == 0:
                self.long_avg_price = 0
        else:  # 平空
            close_quantity = min(self.short_position, order.filled)
            pnl = close_quantity * (self.short_avg_price - order.price)
            self.short_position -= close_quantity
            if self.short_position == 0:
                self.short_avg_price = 0

        self.realized_pnl += pnl
        self.margin_balance += pnl

    def _handle_funding_fee(self, fee_data: dict) -> None:
        """
        处理资金费用。

        参数:
            fee_data: 资金费用数据。
        """
        fee = fee_data.get('amount', 0)
        self.funding_fees += fee
        self.margin_balance -= fee
        self.logger.info(f"Funding fee applied: {fee} USDT. New margin balance: {self.margin_balance} USDT")


    def get_total_balance_value(self, price: float) -> float:
        """
        计算以法币计的账户总价值，包括预留资金。

        参数:
            price: 加密货币的当前市场价格。

        返回:
            float: 以法币计的账户总价值。
        """
        return 0.0

    def update_after_initial_purchase(self, initial_order):
        pass