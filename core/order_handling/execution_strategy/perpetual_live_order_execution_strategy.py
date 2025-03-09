import logging, asyncio
from typing import Optional
from enum import Enum
from ..order import Order, OrderType, OrderSide, OrderStatus
from core.services.exchange_interface import ExchangeInterface
from core.services.exceptions import DataFetchError
from .order_execution_strategy_interface import OrderExecutionStrategyInterface
from ..exceptions import OrderExecutionFailedError
from ..perpetual_order import PerpetualOrderSide, PerpetualOrder, PerpetualOrderType


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"

class MarginMode(Enum):
    ISOLATED = "isolated"
    CROSS = "cross"

class PerpetualLiveOrderExecutionStrategy(OrderExecutionStrategyInterface):
    def __init__(
        self, 
        exchange_service: ExchangeInterface, 
        max_retries: int = 3, 
        retry_delay: int = 1, 
        max_slippage: float = 0.01,
        leverage: int = 1,
        margin_mode: MarginMode = MarginMode.ISOLATED
    ) -> None:
        self.exchange_service = exchange_service
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_slippage = max_slippage
        self.leverage = leverage
        self.margin_mode = margin_mode
        self.logger = logging.getLogger(self.__class__.__name__)

    async def execute_market_order(
        self, 
        order_side: OrderSide,
        pair: str, 
        quantity: float, 
        price: float,
        position_side: Optional[PositionSide] = None
    ) -> Optional[PerpetualOrder]:
        for attempt in range(self.max_retries):
            try:
                raw_order = await self.exchange_service.place_order(
                    pair,
                    OrderType.MARKET.value.lower(),
                    order_side.value.lower(),
                    quantity, 
                    price,
                )
                
                order_result = await self._parse_order_result(raw_order)
                
                if order_result.status == OrderStatus.CLOSED:
                    return order_result

                elif order_result.status == OrderStatus.OPEN:
                    await self._handle_partial_fill(order_result, pair)

                await asyncio.sleep(self.retry_delay)
                self.logger.info(f"Retrying order. Attempt {attempt + 1}/{self.max_retries}.")
                price = await self._adjust_price(order_side, price, attempt)

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1} failed with error: {str(e)}")
                await asyncio.sleep(self.retry_delay)

        raise OrderExecutionFailedError("Failed to execute Perpetual Market order after maximum retries.", 
                                      order_side, OrderType.MARKET, pair, quantity, price)
    
    async def execute_limit_order(
        self, 
        order_side: OrderSide, 
        pair: str, 
        quantity: float, 
        price: float,
        position_side: Optional[PositionSide] = None
    ) -> Optional[Order]:
        try:
            raw_order = await self.exchange_service.place_order(
                pair, 
                OrderType.LIMIT.value.lower(), 
                order_side.name.lower(), 
                quantity, 
                price,
            )
            
            order_result = await self._parse_order_result(raw_order)
            return order_result
        
        except DataFetchError as e:
            self.logger.error(f"DataFetchError during perpetual order execution for {pair} - {e}")
            raise OrderExecutionFailedError(f"Failed to execute Perpetual Limit order on {pair}: {e}", 
                                          order_side, OrderType.LIMIT, pair, quantity, price)

        except Exception as e:
            self.logger.error(f"Unexpected error in execute_limit_order: {e}")
            raise OrderExecutionFailedError(f"Unexpected error during perpetual order execution: {e}", 
                                          order_side, OrderType.LIMIT, pair, quantity, price)

    async def get_order(
        self, 
        order_id: str,
        pair: str
    ) -> Optional[Order]:
        try:
            raw_order = await self.exchange_service.fetch_order(order_id, pair)
            order_result = await self._parse_order_result(raw_order)
            return order_result

        except DataFetchError as e:
            raise e

        except Exception as e:
            raise DataFetchError(f"Unexpected error during perpetual order status retrieval: {str(e)}")

    async def _parse_order_result(
        self, 
        raw_order_result: dict
    ) -> Order:
        """解析永续合约订单响应，包含合约特有字段。"""
        status = raw_order_result.get("status")
        if status is None:
            status = "unknown"
        return Order(
            identifier=raw_order_result.get("id", ""),
            status=OrderStatus(status),
            order_type=OrderType(raw_order_result.get("type", "unknown").lower()),
            side=OrderSide(raw_order_result.get("side", "unknown").lower()),
            price= 0.0 if not raw_order_result.get("price", 0.0) else float(raw_order_result.get("price", 0.0)),
            average=raw_order_result.get("average", None),
            amount=0.0 if not raw_order_result.get("amount", 0.0) else float(raw_order_result.get("amount", 0.0)),
            filled= 0.0 if not raw_order_result.get("filled", 0.0) else float(raw_order_result.get("filled", 0.0)),
            remaining= 0.0 if not raw_order_result.get("remaining", 0.0) else float(raw_order_result.get("remaining", 0.0)),
            timestamp=0 if not raw_order_result.get("timestamp", 0) else int(raw_order_result.get("timestamp", 0)),
            datetime=raw_order_result.get("datetime", None),
            last_trade_timestamp=raw_order_result.get("lastTradeTimestamp", None),
            symbol=raw_order_result.get("symbol", ""),
            time_in_force=raw_order_result.get("timeInForce", None),
            trades=raw_order_result.get("trades", []),
            fee=raw_order_result.get("fee", None),
            cost=raw_order_result.get("cost", None),
            info={
                **raw_order_result.get("info", {}),
                "positionSide": raw_order_result.get("info", {}).get("positionSide"),
                "leverage": raw_order_result.get("info", {}).get("leverage"),
                "marginMode": raw_order_result.get("info", {}).get("marginMode"),
                "liquidationPrice": raw_order_result.get("info", {}).get("liquidationPrice"),
                "marginRatio": raw_order_result.get("info", {}).get("marginRatio"),
                "unrealizedPnl": raw_order_result.get("info", {}).get("unrealizedPnl")
            }
        )

    async def _adjust_price(
        self, 
        order_side: OrderSide, 
        price: float, 
        attempt: int
    ) -> float:
        """调整永续合约订单价格，考虑标记价格。"""
        try:
            # 获取标记价格，如果可用的话
            mark_price = await self.exchange_service.fetch_mark_price()
            if mark_price:
                price = mark_price
        except:
            pass  # 如果无法获取标记价格，使用原始价格
            
        adjustment = self.max_slippage / self.max_retries * attempt
        return price * (1 + adjustment) if order_side == OrderSide.BUY else price * (1 - adjustment)
    
    async def _handle_partial_fill(
        self, 
        order: Order, 
        pair: str,
    ) -> Optional[dict]:
        """处理永续合约部分成交订单。"""
        self.logger.info(f"Perpetual order partially filled with {order.filled}. Attempting to cancel and retry full quantity.")

        if not await self._retry_cancel_order(order.identifier, pair):
            self.logger.error(f"Unable to cancel partially filled perpetual order {order.identifier} after retries.")

    async def _retry_cancel_order(
        self, 
        order_id: str, 
        pair: str
    ) -> bool:
        """重试取消永续合约订单。"""
        for cancel_attempt in range(self.max_retries):
            try:
                cancel_result = await self.exchange_service.cancel_order(order_id, pair)

                if cancel_result['status'] == 'canceled':
                    self.logger.info(f"Successfully canceled perpetual order {order_id}.")
                    return True

                self.logger.warning(f"Cancel attempt {cancel_attempt + 1} for perpetual order {order_id} failed.")

            except Exception as e:
                self.logger.warning(f"Error during cancel attempt {cancel_attempt + 1} for perpetual order {order_id}: {str(e)}")

            await asyncio.sleep(self.retry_delay)
        return False

    async def _setup_leverage_and_margin(
        self,
        pair: str
    ) -> None:
        """设置永续合约的杠杆和保证金模式。"""
        try:
            await self.exchange_service.set_leverage(self.leverage, pair)
            await self.exchange_service.set_margin_mode(self.margin_mode.value, pair)
        except Exception as e:
            self.logger.error(f"Failed to setup leverage and margin mode: {str(e)}")
            raise

    def _determine_position_side(
        self,
        order_side: PerpetualOrderSide,
    ) -> PositionSide:
        """根据订单方向确定仓位方向。"""
        return PositionSide.LONG if order_side == PerpetualOrderSide.BUY_OPEN else PositionSide.SHORT

    async def get_funding_rate(self, pair: str)->float:
        return await self.exchange_service.get_funding_rate(pair)