import logging
from config.trading_mode import TradingMode
from .fee_calculator import FeeCalculator
from .order import Order, OrderSide, OrderStatus
from core.bot_management.event_bus import EventBus, Events
from ..validation.exceptions import InsufficientBalanceError, InsufficientCryptoBalanceError
from core.services.exchange_interface import ExchangeInterface

class BalanceTracker:
    def __init__(
        self, 
        event_bus: EventBus,
        fee_calculator: FeeCalculator, 
        trading_mode: TradingMode,
        base_currency: str,
        quote_currency: str,
    ):
        """
        初始化 BalanceTracker。

        参数:
            event_bus: 事件总线实例，用于订阅事件。
            fee_calculator: 费用计算器实例，用于计算交易费用。
            trading_mode: 交易模式，可以是 "BACKTEST"、"LIVE" 或 "PAPER_TRADING"。
            base_currency: 基础货币符号（通常为加密货币，如 BTC）。
            quote_currency: 报价货币符号（通常为法币，如 USD）。
        """
        self.logger = logging.getLogger(self.__class__.__name__)# 初始化日志记录器
        self.event_bus: EventBus = event_bus# 事件总线，用于接收订单完成事件
        self.fee_calculator: FeeCalculator = fee_calculator# 费用计算器，用于计算交易费用
        self.trading_mode: TradingMode = trading_mode# 交易模式，决定余额初始化方式
        self.base_currency: str = base_currency# 基础货币符号（加密货币）
        self.quote_currency: str = quote_currency# 报价货币符号（法币）

        self.balance: float = 0.0# 主法币余额
        self.crypto_balance: float = 0.0# 主加密货币余额
        self.total_fees: float = 0# 累计交易费用
        self.reserved_fiat: float = 0.0# 预留的法币（用于挂单）
        self.reserved_crypto: float = 0.0# 预留的加密货币（用于挂单）
        # 订阅 ORDER_FILLED 事件，订单完成时调用更新余额的方法
        self.event_bus.subscribe(Events.ORDER_FILLED, self._update_balance_on_order_completion)
    
    async def setup_balances(
        self, 
        initial_balance: float, 
        initial_crypto_balance: float, 
        exchange_service=ExchangeInterface
    ):
        """
        根据交易模式设置余额。

        对于 BACKTEST 模式，使用传入的初始余额。
        对于 LIVE 和 PAPER_TRADING 模式，从交易所动态获取余额。

        参数:
            initial_balance: 回测模式下的初始法币余额。
            initial_crypto_balance: 回测模式下的初始加密货币余额。
            exchange_service: 交易所接口实例（实盘和模拟交易模式需要）。
        """
        if self.trading_mode == TradingMode.BACKTEST:
            # 回测模式，直接使用传入的初始余额
            self.balance = initial_balance
            self.crypto_balance = initial_crypto_balance
        elif self.trading_mode == TradingMode.LIVE or self.trading_mode == TradingMode.PAPER_TRADING:
            # 实盘或模拟交易模式，从交易所异步获取实时余额
            self.balance, self.crypto_balance = await self._fetch_live_balances(exchange_service)

    async def _fetch_live_balances(
        self, 
        exchange_service: ExchangeInterface
    )-> tuple[float, float]:
        """
        异步从交易所获取实时余额。

        参数:
            exchange_service: 交易所接口实例。

        返回:
            tuple: 包含法币余额和加密货币余额的元组。
        """
        balances = await exchange_service.get_balance()

        if not balances or 'free' not in balances:
            raise ValueError(f"Unexpected balance structure: {balances}")
        # 提取报价货币（法币）的可用余额
        quote_balance = float(balances.get('free', {}).get(self.quote_currency, 0.0))
        # 提取基础货币（加密货币）的可用余额
        base_balance = float(balances.get('free', {}).get(self.base_currency, 0.0))
        self.logger.info(f"Fetched balances - Quote: {self.quote_currency}: {quote_balance}, Base: {self.base_currency}: {base_balance}")
        return quote_balance, base_balance # 返回法币和加密货币余额

    async def _update_balance_on_order_completion(self, order: Order) -> None:
        """
        当订单完成时更新账户的法币和加密货币余额。

        此方法在接收到 ORDER_FILLED 事件时被调用，根据订单类型（买入或卖出）更新余额。

        参数:
            order: 已完成的 Order 对象，包含订单方向（BUY/SELL）、成交数量和价格等信息。
        """
        if order.side == OrderSide.BUY:
            # 买入订单完成，更新余额
            self._update_after_buy_order_filled(order.filled, order.price)
        elif order.side == OrderSide.SELL:
            # 卖出订单完成，更新余额
            self._update_after_sell_order_filled(order.filled, order.price)

    def _update_after_buy_order_filled(
        self, 
        quantity: float, 
        price: float
    ) -> None:
        """
        在买入订单完成后更新余额，包括处理预留资金。

        从预留法币中扣除总成本（价格 * 数量 + 费用），释放多余预留资金到主余额，
        增加加密货币余额并记录费用。

        参数:
            quantity: 购买的加密货币数量。
            price: 购买价格（每单位）。
        """
        fee = self.fee_calculator.calculate_fee(quantity * price)# 计算交易费用
        total_cost = quantity * price + fee# 计算总成本

        self.reserved_fiat -= total_cost# 从预留法币中扣除总成本
        if self.reserved_fiat < 0:
            self.balance += self.reserved_fiat  # 如果预留不足，将多扣部分退回主余额
            self.reserved_fiat = 0
    
        self.crypto_balance += quantity # 增加加密货币余额
        self.total_fees += fee # 累加交易费用
        self.logger.info(f"Buy order completed: {quantity} crypto purchased at {price}.")

    def _update_after_sell_order_filled(
        self, 
        quantity: float, 
        price: float
    ) -> None:
        """
        在卖出订单完成后更新余额，包括处理预留资金。

        从预留加密货币中扣除卖出数量，释放多余预留加密货币到主余额，
        增加法币余额（销售收益 - 费用）并记录费用。

        参数:
            quantity: 卖出的加密货币数量。
            price: 卖出价格（每单位）。
        """
        fee = self.fee_calculator.calculate_fee(quantity * price)# 计算交易费用
        sale_proceeds = quantity * price - fee# 计算销售收益
        self.reserved_crypto -= quantity# 从预留加密货币中扣除卖出数量

        if self.reserved_crypto < 0:
            self.crypto_balance += abs(self.reserved_crypto)  # 如果预留不足，将多扣部分退回主余额
            self.reserved_crypto = 0

        self.balance += sale_proceeds# 增加法币余额
        self.total_fees += fee# 累加交易费用
        self.logger.info(f"Sell order completed: {quantity} crypto sold at {price}.")
    
    def update_after_initial_purchase(self, initial_order: Order):
        """
        在初始加密货币购买完成后更新余额。

        参数:
            initial_order: 包含已完成购买详情的 Order 对象。
        """
        if initial_order.status != OrderStatus.CLOSED:
            raise ValueError(f"Order {initial_order.id} is not CLOSED. Cannot update balances.")
    
        total_cost = initial_order.filled * initial_order.average # 计算总成本
        fee = self.fee_calculator.calculate_fee(initial_order.amount * initial_order.average)# 增加加密货币余额
        
        self.crypto_balance += initial_order.filled# 减少法币余额
        self.balance -= total_cost + fee# 累加交易费用
        self.total_fees += fee
        self.logger.info(f"Updated balances. Crypto balance: {self.crypto_balance}, Fiat balance: {self.balance}, Total fees: {self.total_fees}")

    def reserve_funds_for_buy(
        self, 
        amount: float
    ) -> None:
        """
        为挂起的卖出订单预留加密货币。

        参数:
            quantity: 要预留的加密货币数量。
        """
        if self.balance < amount:
            raise InsufficientBalanceError(f"Insufficient fiat balance to reserve {amount}.")

        self.reserved_fiat += amount # 增加预留加密货币
        self.balance -= amount# 减少主加密货币余额
        self.logger.info(f"Reserved {amount} fiat for a buy order. Remaining fiat balance: {self.balance}.")

    def reserve_funds_for_sell(
        self, 
        quantity: float
    ) -> None:
        """
        为挂起的卖出订单预留加密货币。

        参数:
            quantity: 要预留的加密货币数量。
        """
        if self.crypto_balance < quantity:
            raise InsufficientCryptoBalanceError(f"Insufficient crypto balance to reserve {quantity}.")

        self.reserved_crypto += quantity
        self.crypto_balance -= quantity
        self.logger.info(f"Reserved {quantity} crypto for a sell order. Remaining crypto balance: {self.crypto_balance}.")

    def get_adjusted_fiat_balance(self) -> float:
        """
        返回包括预留资金在内的总法币余额。

        返回:
            float: 总法币余额。
        """
        return self.balance + self.reserved_fiat

    def get_adjusted_crypto_balance(self) -> float:
        """
        返回包括预留资金在内的总加密货币余额。

        返回:
            float: 总加密货币余额。
        """
        return self.crypto_balance + self.reserved_crypto

    def get_total_balance_value(self, price: float) -> float:
        """
        计算以法币计的账户总价值，包括预留资金。

        参数:
            price: 加密货币的当前市场价格。

        返回:
            float: 以法币计的账户总价值。
        """
        return self.get_adjusted_fiat_balance() + self.get_adjusted_crypto_balance() * price