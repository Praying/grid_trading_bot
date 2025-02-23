import asyncio, logging
from core.bot_management.event_bus import EventBus, Events
from core.order_handling.order_book import OrderBook
from core.order_handling.order import Order, OrderStatus

class OrderStatusTracker:
    """
    Tracks the status of pending orders and publishes events
    when their states change (e.g., open, filled, canceled).
    订单状态追踪器，监控未完成订单状态变化并发布对应事件
    """

    def __init__(
        self,
        order_book: OrderBook,
        order_execution_strategy,
        event_bus: EventBus,
        polling_interval: float = 15.0,
    ):
        """
        Initializes the OrderStatusTracker.

        Args:
            order_book: OrderBook instance to manage and query orders.
            order_execution_strategy: Strategy for querying order statuses from the exchange.
            event_bus: EventBus instance for publishing state change events.
            polling_interval: Time interval (in seconds) between status checks.
        初始化订单状态追踪器

        参数:
            order_book: 订单簿实例，用于管理/查询订单
            order_execution_strategy: 交易所订单状态查询策略接口
            event_bus: 事件总线实例，用于发布状态变更事件
            polling_interval: 交易所状态轮询间隔（单位：秒）
        """
        self.order_book = order_book # 订单簿引用
        self.order_execution_strategy = order_execution_strategy # 交易所接口适配器
        self.event_bus = event_bus# 事件发布总线
        self.polling_interval = polling_interval# 轮询频率控制
        self._monitoring_task = None# 主监控任务句柄
        self._active_tasks = set()# 活跃子任务集合
        self.logger = logging.getLogger(self.__class__.__name__)# 日志记录器

    async def _track_open_order_statuses(self) -> None:
        """
        Periodically checks the statuses of open orders and updates their states.
        异步循环任务：持续追踪所有未成交订单状态
        """
        try:
            while True: # 循环执行模式
                await self._process_open_orders() # 核心处理逻辑
                await asyncio.sleep(self.polling_interval)# 频率控制

        except asyncio.CancelledError:# 任务取消信号处理
            self.logger.info("OrderStatusTracker monitoring task was cancelled.")
            await self._cancel_active_tasks()

        except Exception as error:# 全局异常捕获
            self.logger.error(f"Unexpected error in OrderStatusTracker: {error}")

    async def _process_open_orders(self) -> None:
        """
        Processes open orders by querying their statuses and handling state changes.
        批量处理所有未完成订单
        """
        open_orders = self.order_book.get_open_orders()
        tasks = [self._create_task(self._query_and_handle_order(order)) for order in open_orders]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"Error during order processing: {result}", exc_info=True)

    async def _query_and_handle_order(self, local_order: Order):
        """
        Query order and handling state changes if needed.
        # 获取所有未成交订单
        """
        try:
            # 从交易所获取最新订单状态
            remote_order = await self.order_execution_strategy.get_order(local_order.identifier, local_order.symbol)
            self._handle_order_status_change(remote_order)

        except Exception as error:
            self.logger.error(f"Failed to query remote order with identifier {local_order.identifier}: {error}", exc_info=True)

    def _handle_order_status_change(
        self,
        remote_order: Order,
    ) -> None:
        """
        Handles changes in the status of the order data fetched from the exchange.

        Args:
            remote_order: The latest `Order` object fetched from the exchange.
        
        Raises:
            ValueError: If critical fields (e.g., status) are missing from the remote order.

        处理交易所返回的订单状态变更

        参数:
            remote_order: 从交易所获取的最新订单对象

        异常:
            当交易所返回数据缺失关键字段时抛出ValueError
        """
        try:
            # 状态校验
            if remote_order.status == OrderStatus.UNKNOWN:
                self.logger.error(f"Missing 'status' in remote order object: {remote_order}", exc_info=True)
                raise ValueError("Order data from the exchange is missing the 'status' field.")
            # 状态处理分支
            elif remote_order.status == OrderStatus.CLOSED: # 完全成交
                self.order_book.update_order_status(remote_order.identifier, OrderStatus.CLOSED)
                self.event_bus.publish_sync(Events.ORDER_FILLED, remote_order)# 发布成交事件
                self.logger.info(f"Order {remote_order.identifier} filled.")
            elif remote_order.status == OrderStatus.CANCELED:# 已取消
                self.order_book.update_order_status(remote_order.identifier, OrderStatus.CANCELED)
                self.event_bus.publish_sync(Events.ORDER_CANCELLED, remote_order)# 发布取消事件
                self.logger.warning(f"Order {remote_order.identifier} was canceled.")
            elif remote_order.status == OrderStatus.OPEN: # 未成交/部分成交
                if remote_order.filled > 0:
                    self.logger.info(f"Order {remote_order} partially filled. Filled: {remote_order.filled}, Remaining: {remote_order.remaining}.")
                else:
                    self.logger.info(f"Order {remote_order} is still open. No fills yet.")
            else:# 未处理状态
                self.logger.warning(f"Unhandled order status '{remote_order.status}' for order {remote_order.identifier}.")

        except Exception as e:
            self.logger.error(f"Error handling order status change: {e}", exc_info=True)

    def _create_task(self, coro):
        """
        Creates a managed asyncio task and adds it to the active task set.

        Args:
            coro: Coroutine to be scheduled as a task.
        """
        task = asyncio.create_task(coro)
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        return task

    async def _cancel_active_tasks(self):
        """
        Cancels all active tasks tracked by the tracker.
        """
        for task in self._active_tasks:
            task.cancel()
        await asyncio.gather(*self._active_tasks, return_exceptions=True)
        self._active_tasks.clear()

    def start_tracking(self) -> None:
        """
        Starts the order tracking task.
        启动订单状态追踪
        """
        if self._monitoring_task and not self._monitoring_task.done():
            self.logger.warning("OrderStatusTracker is already running.")
            return
        self._monitoring_task = asyncio.create_task(self._track_open_order_statuses())
        self.logger.info("OrderStatusTracker has started tracking open orders.")

    async def stop_tracking(self) -> None:
        """
        Stops the order tracking task.
        停止订单状态追踪
        """
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                self.logger.info("OrderStatusTracker monitoring task was cancelled.")
            await self._cancel_active_tasks()
            self._monitoring_task = None
            self.logger.info("OrderStatusTracker has stopped tracking open orders.")