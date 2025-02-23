from .exceptions import InsufficientBalanceError, InsufficientCryptoBalanceError, InvalidOrderQuantityError
"""
OrderValidator 类是网格交易策略中用于验证和调整订单数量的关键组件，确保订单在执行前不会超出可用余额或资产的限制。
"""
class OrderValidator:
    def __init__(self, tolerance: float = 1e-6, threshold_ratio: float = 0.5):
        """
        使用指定的容忍度和阈值初始化 OrderValidator。

        参数:
            tolerance (float): 验证的最小精度容忍度。
            threshold_ratio (float): 阈值比例，当余额或资产低于该比例时，提前触发不足错误。
        """
        self.tolerance = tolerance
        self.threshold_ratio = threshold_ratio

    def adjust_and_validate_buy_quantity(self, balance: float, order_quantity: float, price: float) -> float:
        """
        根据可用余额调整和验证买入订单数量。

        参数:
            balance (float): 可用法币余额。
            order_quantity (float): 请求的买入数量。
            price (float): 资产的价格。

        返回:
            float: 调整和验证后的买入订单数量。

        抛出:
            InsufficientBalanceError: 如果余额不足以放置任何有效订单。
            InvalidOrderQuantityError: 如果调整后的数量无效。
        """
        total_cost = order_quantity * price# 计算订单总成本
        # 如果余额远低于总成本，提前抛出错误
        if balance < total_cost * self.threshold_ratio:
            raise InsufficientBalanceError(f"Balance {balance:.2f} is far below the required cost {total_cost:.2f} (threshold ratio: {self.threshold_ratio}).")
        # 如果总成本超过余额，调整数量
        if total_cost > balance:
            adjusted_quantity = max((balance - self.tolerance) / price, 0)
            # 如果调整后的数量为 0 或订单成本小于容忍度，抛出错误
            if adjusted_quantity <= 0 or (adjusted_quantity * price) < self.tolerance:
                raise InsufficientBalanceError(f"Insufficient balance: {balance:.2f} to place any buy order at price {price:.2f}.")
        else:
            adjusted_quantity = order_quantity# 如果余额足够，保持原始数量

        self._validate_quantity(adjusted_quantity, is_buy=True)# 验证调整后的数量
        return adjusted_quantity# 返回调整后的数量

    def adjust_and_validate_sell_quantity(self, crypto_balance: float, order_quantity: float) -> float:
        """
        根据可用加密货币余额调整和验证卖出订单数量。

        参数:
            crypto_balance (float): 可用加密货币余额。
            order_quantity (float): 请求的卖出数量。

        返回:
            float: 调整和验证后的卖出订单数量。

        抛出:
            InsufficientCryptoBalanceError: 如果加密货币余额不足以放置任何有效订单。
            InvalidOrderQuantityError: 如果调整后的数量无效。
        """
        # 如果加密货币余额远低于请求的数量，提前抛出错误
        if crypto_balance < order_quantity * self.threshold_ratio:
            raise InsufficientCryptoBalanceError(
                f"Crypto balance {crypto_balance:.6f} is far below the required quantity {order_quantity:.6f} "
                f"(threshold ratio: {self.threshold_ratio})."
            )
        # 调整数量为请求数量和可用余额（减去容忍度）中的较小值
        adjusted_quantity = min(order_quantity, crypto_balance - self.tolerance)
        self._validate_quantity(adjusted_quantity, is_buy=False)# 验证调整后的数量
        return adjusted_quantity# 返回调整后的数量

    def _validate_quantity(self, quantity: float, is_buy: bool) -> None:
        """
        验证调整后的订单数量。

        参数:
            quantity (float): 调整后的数量。
            is_buy (bool): 订单是否为买入订单。

        抛出:
            InvalidOrderQuantityError: 如果数量无效。
        """
        if quantity <= 0:
            order_type = "buy" if is_buy else "sell"# 根据 is_buy 判断订单类型
            raise InvalidOrderQuantityError(f"Invalid {order_type} quantity: {quantity:.6f}")