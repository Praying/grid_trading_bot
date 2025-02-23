import asyncio, logging
from typing import Optional
from core.bot_management.event_bus import EventBus, Events
from core.order_handling.order_book import OrderBook
from core.order_handling.order import Order, OrderStatus

class PerpetualOrderStatus:
    """永续合约特有的订单状态"""
    LIQUIDATED = "liquidated"  # 强平状态
    ADL = "adl"  # 自动减仓状态
    PARTIAL_CLOSE = "partial_close"  # 部分平仓状态

class PerpetualEvents:
    """永续合约特有的事件类型"""
    LIQUIDATION_WARNING = "liquidation_warning"  # 强平警告
    POSITION_UPDATE = "position_update"  # 仓位更新
    FUNDING_FEE = "funding_fee"  # 资金费用结算
    ADL_TRIGGERED = "adl_triggered"  # 自动减仓触发

class PerpetualOrderStatusTracker:
    """永续合约订单状态追踪器，专门处理U本位永续合约的订单状态变化"""

    def __init__(
        self,
        order_book: OrderBook,
        order_execution_strategy,
        event_bus: EventBus,
        polling_interval: float = 5.0,  # 合约默认使用更短的轮询间隔
        funding_check_interval: float = 60.0,  # 资金费率检查间隔
    ):
        """初始化永续合约订单状态追踪器

        Args:
            order_book: 订单簿实例
            order_execution_strategy: 合约订单执行策略
            event_bus: 事件总线
            polling_interval: 订单状态轮询间隔（秒）
            funding_check_interval: 资金费率检查间隔（秒）
        """
        self.order_book = order_book
        self.order_execution_strategy = order_execution_strategy
        self.event_bus = event_bus
        self.polling_interval = polling_interval
        self.funding_check_interval = funding_check_interval
        self._monitoring_task = None
        self._funding_check_task = None
        self._active_tasks = set()
        self.logger = logging.getLogger(self.__class__.__name__)

    def start_tracking(self) -> None:
        """启动订单追踪和资金费率检查"""
        if self._monitoring_task and not self._monitoring_task.done():
            self.logger.warning("PerpetualOrderStatusTracker is already running.")
            return

        self._monitoring_task = asyncio.create_task(self._track_open_order_statuses())
        self._funding_check_task = asyncio.create_task(self._check_funding_rate())
        self.logger.info("Started perpetual order tracking and funding rate monitoring.")

    async def stop_tracking(self) -> None:
        """停止订单追踪和资金费率检查"""
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
            self._monitoring_task = None

        if self._funding_check_task:
            self._funding_check_task.cancel()
            try:
                await self._funding_check_task
            except asyncio.CancelledError:
                pass
            self._funding_check_task = None

        await self._cancel_active_tasks()
        self.logger.info("Stopped perpetual order tracking and funding rate monitoring.")

    async def _track_open_order_statuses(self) -> None:
        """持续追踪所有未成交订单状态"""
        try:
            while True:
                await self._process_open_orders()
                await asyncio.sleep(self.polling_interval)
        except asyncio.CancelledError:
            self.logger.info("Perpetual order monitoring task was cancelled.")
            await self._cancel_active_tasks()
        except Exception as error:
            self.logger.error(f"Unexpected error in PerpetualOrderStatusTracker: {error}")

    async def _process_open_orders(self) -> None:
        """批量处理所有未完成订单"""
        open_orders = self.order_book.get_open_orders()
        tasks = [self._create_task(self._query_and_handle_order(order)) for order in open_orders]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"Error during order processing: {result}", exc_info=True)

    async def _query_and_handle_order(self, local_order: Order):
        """查询并处理单个订单状态"""
        try:
            remote_order = await self.order_execution_strategy.get_order(local_order.identifier, local_order.symbol)
            self._handle_order_status_change(remote_order)
        except Exception as error:
            self.logger.error(f"Failed to query remote order with identifier {local_order.identifier}: {error}", exc_info=True)

    def _handle_order_status_change(self, remote_order: Order) -> None:
        """处理合约订单状态变更

        Args:
            remote_order: 从交易所获取的最新订单信息
        """
        try:
            if remote_order.status == OrderStatus.UNKNOWN:
                self.logger.error(f"Missing status in remote order: {remote_order}", exc_info=True)
                raise ValueError("Order data missing status field")

            if remote_order.status == PerpetualOrderStatus.LIQUIDATED:
                self._handle_liquidation(remote_order)
            elif remote_order.status == PerpetualOrderStatus.ADL:
                self._handle_adl(remote_order)
            elif remote_order.status == PerpetualOrderStatus.PARTIAL_CLOSE:
                self._handle_partial_close(remote_order)
            elif remote_order.status == OrderStatus.CLOSED:
                self.order_book.update_order_status(remote_order.identifier, OrderStatus.CLOSED)
                self.event_bus.publish_sync(Events.ORDER_FILLED, remote_order)
                self.logger.info(f"Order {remote_order.identifier} filled.")
            elif remote_order.status == OrderStatus.CANCELED:
                self.order_book.update_order_status(remote_order.identifier, OrderStatus.CANCELED)
                self.event_bus.publish_sync(Events.ORDER_CANCELLED, remote_order)
                self.logger.warning(f"Order {remote_order.identifier} was canceled.")
            elif remote_order.status == OrderStatus.OPEN:
                if remote_order.filled > 0:
                    self.logger.info(f"Order {remote_order} partially filled. Filled: {remote_order.filled}, Remaining: {remote_order.remaining}.")
                else:
                    self.logger.info(f"Order {remote_order} is still open. No fills yet.")
            else:
                self.logger.warning(f"Unhandled order status '{remote_order.status}' for order {remote_order.identifier}.")

            self._check_liquidation_risk(remote_order)

        except Exception as e:
            self.logger.error(f"Error handling perpetual order status change: {e}", exc_info=True)

    def _handle_liquidation(self, order: Order) -> None:
        """处理强平订单"""
        self.order_book.update_order_status(order.identifier, PerpetualOrderStatus.LIQUIDATED)
        self.event_bus.publish_sync(PerpetualEvents.POSITION_UPDATE, order)
        self.logger.warning(f"Order {order.identifier} was liquidated.")

    def _handle_adl(self, order: Order) -> None:
        """处理自动减仓订单"""
        self.order_book.update_order_status(order.identifier, PerpetualOrderStatus.ADL)
        self.event_bus.publish_sync(PerpetualEvents.ADL_TRIGGERED, order)
        self.logger.warning(f"Order {order.identifier} was automatically deleveraged.")

    def _handle_partial_close(self, order: Order) -> None:
        """处理部分平仓订单"""
        self.order_book.update_order_status(order.identifier, PerpetualOrderStatus.PARTIAL_CLOSE)
        self.event_bus.publish_sync(PerpetualEvents.POSITION_UPDATE, order)
        self.logger.info(
            f"Order {order.identifier} partially closed. "
            f"Filled: {order.filled}, Remaining: {order.remaining}"
        )

    async def _check_funding_rate(self) -> None:
        """定期检查资金费率并处理资金费用结算"""
        try:
            while True:
                try:
                    funding_rates = await self.order_execution_strategy.get_funding_rates()
                    for symbol, rate in funding_rates.items():
                        self.event_bus.publish_sync(
                            PerpetualEvents.FUNDING_FEE,
                            {"symbol": symbol, "rate": rate}
                        )
                except Exception as e:
                    self.logger.error(f"Error checking funding rates: {e}", exc_info=True)
                await asyncio.sleep(self.funding_check_interval)
        except asyncio.CancelledError:
            self.logger.info("Funding rate check task cancelled.")

    def _check_liquidation_risk(self, order: Order) -> None:
        """检查订单的强平风险

        Args:
            order: 需要检查的订单对象
        """
        try:
            margin_ratio = self.order_execution_strategy.get_position_margin_ratio(
                order.symbol, order.position_side
            )
            
            if margin_ratio and margin_ratio < 0.1:  # 10%作为警戒线
                self.event_bus.publish_sync(
                    PerpetualEvents.LIQUIDATION_WARNING,
                    {
                        "order": order,
                        "margin_ratio": margin_ratio,
                        "liquidation_price": self.order_execution_strategy.get_liquidation_price(
                            order.symbol, order.position_side
                        )
                    }
                )
                self.logger.warning(
                    f"Liquidation warning for {order.identifier}: "
                    f"margin ratio {margin_ratio:.2%}"
                )
        except Exception as e:
            self.logger.error(f"Error checking liquidation risk: {e}", exc_info=True)

    def _create_task(self, coro):
        """创建并管理异步任务

        Args:
            coro: 要执行的协程
        """
        task = asyncio.create_task(coro)
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        return task

    async def _cancel_active_tasks(self):
        """取消所有活跃任务"""
        for task in self._active_tasks:
            task.cancel()
        await asyncio.gather(*self._active_tasks, return_exceptions=True)
        self._active_tasks.clear()