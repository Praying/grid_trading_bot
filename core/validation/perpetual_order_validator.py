from .perpetual_exceptions import InsufficientMarginError, InsufficientPositionError, InvalidContractQuantityError, MarginRatioError

"""
PerpetualOrderValidator 类是永续合约交易中用于验证和调整订单数量的关键组件，确保订单在执行前满足保证金要求、持仓限制和合约规格。
"""
class PerpetualOrderValidator:
    def __init__(self, tolerance: float = 1e-6, threshold_ratio: float = 0.5,
                 maintenance_margin_rate: float = 0.005, min_contract_size: float = 0.001):
        """
        使用指定的参数初始化 PerpetualOrderValidator。

        参数:
            tolerance (float): 验证的最小精度容忍度。
            threshold_ratio (float): 阈值比例，当保证金低于该比例时，提前触发不足错误。
            maintenance_margin_rate (float): 维持保证金率，用于检查是否有强平风险。
            min_contract_size (float): 最小合约张数，用于验证订单数量。
        """
        self.tolerance = tolerance
        self.threshold_ratio = threshold_ratio
        self.maintenance_margin_rate = maintenance_margin_rate
        self.min_contract_size = min_contract_size

    def adjust_and_validate_open_long(self, margin_balance: float, order_quantity: float,
                                     price: float, leverage: float) -> float:
        """
        调整和验证开多仓订单数量，确保有足够的保证金。

        参数:
            margin_balance (float): 可用保证金余额。
            order_quantity (float): 请求的开仓数量（以合约张数为单位）。
            price (float): 合约当前价格。
            leverage (float): 杠杆倍数。

        返回:
            float: 调整和验证后的开仓数量。

        抛出:
            InsufficientMarginError: 如果保证金不足以开仓。
            InvalidContractQuantityError: 如果调整后的数量无效。
            MarginRatioError: 如果开仓后保证金率低于维持保证金率。
        """
        required_margin = (order_quantity * price) / leverage
        # 如果保证金余额远低于所需保证金，提前抛出错误
        if margin_balance < required_margin * self.threshold_ratio:
            raise InsufficientMarginError(
                f"Margin balance {margin_balance:.2f} is far below the required margin {required_margin:.2f} "
                f"(threshold ratio: {self.threshold_ratio})."
            )

        # 如果保证金不足，调整数量
        if required_margin > margin_balance:
            adjusted_quantity = max((margin_balance - self.tolerance) * leverage / price, 0)
            # 如果调整后的数量为 0 或保证金小于容忍度，抛出错误
            if adjusted_quantity <= 0 or (adjusted_quantity * price / leverage) < self.tolerance:
                raise InsufficientMarginError(
                    f"Insufficient margin: {margin_balance:.2f} to open long position at price {price:.2f}."
                )
        else:
            adjusted_quantity = order_quantity

        self._validate_contract_quantity(adjusted_quantity)
        self._check_margin_ratio(margin_balance, adjusted_quantity, price, leverage)
        return adjusted_quantity

    def adjust_and_validate_open_short(self, margin_balance: float, order_quantity: float,
                                      price: float, leverage: float) -> float:
        """
        调整和验证开空仓订单数量，确保有足够的保证金。

        参数:
            margin_balance (float): 可用保证金余额。
            order_quantity (float): 请求的开仓数量（以合约张数为单位）。
            price (float): 合约当前价格。
            leverage (float): 杠杆倍数。

        返回:
            float: 调整和验证后的开仓数量。

        抛出:
            InsufficientMarginError: 如果保证金不足以开仓。
            InvalidContractQuantityError: 如果调整后的数量无效。
            MarginRatioError: 如果开仓后保证金率低于维持保证金率。
        """
        # 开空仓的逻辑与开多仓类似
        return self.adjust_and_validate_open_long(margin_balance, order_quantity, price, leverage)

    def adjust_and_validate_close_long(self, long_position: float, order_quantity: float) -> float:
        """
        调整和验证平多仓订单数量，确保有足够的持仓。

        参数:
            long_position (float): 当前多仓持仓量（以合约张数为单位）。
            order_quantity (float): 请求的平仓数量（以合约张数为单位）。

        返回:
            float: 调整和验证后的平仓数量。

        抛出:
            InsufficientPositionError: 如果持仓不足以平仓。
            InvalidContractQuantityError: 如果调整后的数量无效。
        """
        # 如果持仓量远低于请求的平仓数量，提前抛出错误
        if long_position < order_quantity * self.threshold_ratio:
            raise InsufficientPositionError(
                f"Long position {long_position:.6f} is far below the required quantity {order_quantity:.6f} "
                f"(threshold ratio: {self.threshold_ratio})."
            )

        # 调整数量为请求数量和可用持仓量中的较小值
        adjusted_quantity = min(order_quantity, long_position - self.tolerance)
        self._validate_contract_quantity(adjusted_quantity)
        return adjusted_quantity

    def adjust_and_validate_close_short(self, short_position: float, order_quantity: float) -> float:
        """
        调整和验证平空仓订单数量，确保有足够的持仓。

        参数:
            short_position (float): 当前空仓持仓量（以合约张数为单位）。
            order_quantity (float): 请求的平仓数量（以合约张数为单位）。

        返回:
            float: 调整和验证后的平仓数量。

        抛出:
            InsufficientPositionError: 如果持仓不足以平仓。
            InvalidContractQuantityError: 如果调整后的数量无效。
        """
        # 平空仓的逻辑与平多仓类似
        return self.adjust_and_validate_close_long(short_position, order_quantity)

    def _validate_contract_quantity(self, quantity: float) -> None:
        """
        验证合约数量是否有效。

        参数:
            quantity (float): 需要验证的合约数量。

        抛出:
            InvalidContractQuantityError: 如果数量小于等于0或小于最小合约张数。
        """
        if quantity <= 0 or quantity < self.min_contract_size:
            raise InvalidContractQuantityError(
                f"Invalid contract quantity: {quantity:.6f}, minimum contract size: {self.min_contract_size}"
            )

    def _check_margin_ratio(self, margin_balance: float, order_quantity: float,
                           price: float, leverage: float) -> None:
        """
        检查开仓后的保证金率是否满足要求。

        参数:
            margin_balance (float): 可用保证金余额。
            order_quantity (float): 开仓数量。
            price (float): 合约当前价格。
            leverage (float): 杠杆倍数。

        抛出:
            MarginRatioError: 如果开仓后保证金率低于维持保证金率。
        """
        position_value = order_quantity * price
        margin_ratio = margin_balance / position_value

        if margin_ratio < self.maintenance_margin_rate:
            raise MarginRatioError(
                f"Opening position would result in margin ratio {margin_ratio:.4f} below "
                f"maintenance margin rate {self.maintenance_margin_rate}"
            )