import asyncio, psutil, logging
from typing import Dict, List
from dataclasses import dataclass
from datetime import datetime
from core.bot_management.grid_trading_bot import GridTradingBot
from core.bot_management.notification.notification_handler import NotificationHandler
from core.bot_management.notification.notification_content import NotificationType
from core.bot_management.event_bus import EventBus, Events
from utils.constants import RESSOURCE_THRESHOLDS

@dataclass
class ResourceMetrics:
    # Resource metrics data class to store monitoring information
    # 资源指标数据类，用于存储监控信息
    timestamp: datetime  # Timestamp of the metrics collection
                        # 指标收集的时间戳
    cpu_percent: float  # System-wide CPU usage percentage
                       # 系统整体CPU使用率百分比
    memory_percent: float  # System-wide memory usage percentage
                          # 系统整体内存使用率百分比
    disk_percent: float  # System-wide disk usage percentage
                        # 系统整体磁盘使用率百分比
    bot_cpu_percent: float  # Bot process CPU usage percentage
                           # 机器人进程CPU使用率百分比
    bot_memory_mb: float  # Bot process memory usage in MB
                         # 机器人进程内存使用量（MB）
    open_files: int  # Number of files opened by the bot process
                    # 机器人进程打开的文件数量
    thread_count: int  # Number of threads in the bot process
                      # 机器人进程的线程数量
    
class HealthCheck:
    """
    Periodically checks the bot's health and system resource usage and sends alerts if thresholds are exceeded.
    定期检查机器人的健康状况和系统资源使用情况，当超过阈值时发送警报。
    """

    def __init__(
        self, 
        bot: GridTradingBot, 
        notification_handler: NotificationHandler,
        event_bus: EventBus,
        check_interval: int = 60,
        metrics_history_size: int = 60  # Keep 1 hour of metrics at 1-minute intervals
                                       # 保存1小时的指标数据，每分钟一个数据点
    ):
        """
        Initializes the HealthCheck.
        初始化健康检查器。

        Args:
            bot: The GridTradingBot instance to monitor.
                 要监控的网格交易机器人实例
            notification_handler: The NotificationHandler for sending alerts.
                                 用于发送警报的通知处理器
            event_bus: The EventBus instance for listening to bot lifecycle events.
                       用于监听机器人生命周期事件的事件总线
            check_interval: Time interval (in seconds) between health checks.
                           健康检查的时间间隔（秒）
            metrics_history_size: Number of metrics to keep in the history.
                                 历史指标保存的数量
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.bot = bot
        self.notification_handler = notification_handler
        self.event_bus = event_bus
        self.check_interval = check_interval
        self._is_running = False
        self._stop_event = asyncio.Event()
        self.process = psutil.Process()
        self._metrics_history: List[ResourceMetrics] = []
        self.metrics_history_size = metrics_history_size
        self.process.cpu_percent()  # First call to initialize CPU monitoring
                                   # 首次调用以初始化CPU监控
        self.event_bus.subscribe(Events.STOP_BOT, self._handle_stop)
        self.event_bus.subscribe(Events.START_BOT, self._handle_start)
    
    async def start(self):
        """
        Starts the health check monitoring loop.
        启动健康检查监控循环。
        """
        if self._is_running:
            self.logger.warning("HealthCheck is already running.")
            return

        self._is_running = True
        self._stop_event.clear()
        self.logger.info("HealthCheck started.")

        try:
            while self._is_running:
                await self._perform_checks()
                stop_task = asyncio.create_task(self._stop_event.wait())
                done, _ = await asyncio.wait([stop_task], timeout=self.check_interval)

                if stop_task in done:
                    # Stop event was triggered; exit loop
                    # 停止事件被触发，退出循环
                    break

        except asyncio.CancelledError:
            self.logger.info("HealthCheck task cancelled.")
            
        except Exception as e:
            self.logger.error(f"Unexpected error in HealthCheck: {e}")
            await self.notification_handler.async_send_notification(NotificationType.ERROR_OCCURRED, error_details=f"Health check encountered an error: {e}")

    async def _perform_checks(self):
        """
        Performs bot health and resource usage checks.
        执行机器人健康状况和资源使用情况检查。
        """
        self.logger.info("Starting health checks for bot and system resources.")

        bot_health = await self.bot.get_bot_health_status()
        self.logger.info(f"Fetched bot health status: {bot_health}")
        await self._check_and_alert_bot_health(bot_health)

        resource_usage = self._check_resource_usage()        
        self.logger.info(f"System resource usage: {resource_usage}")
        await self._check_and_alert_resource_usage(resource_usage)

    async def _check_and_alert_bot_health(self, health_status: dict):
        """
        Checks the bot's health status and sends alerts if necessary.
        检查机器人的健康状态，必要时发送警报。

        Args:
            health_status: A dictionary containing the bot's health status.
                          包含机器人健康状态的字典
        """
        alerts = []

        if not health_status["strategy"]:
            alerts.append("Trading strategy has encountered issues.")
            self.logger.warning("Trading strategy is not functioning properly.")

        if not health_status["exchange_status"] == "ok":
            alerts.append(f"Exchange status is not ok: {health_status['exchange_status']}")
            self.logger.warning(f"Exchange status issue detected: {health_status['exchange_status']}")

        if alerts:
            self.logger.info(f"Bot health alerts generated: {alerts}")
            await self.notification_handler.async_send_notification(NotificationType.HEALTH_CHECK_ALERT, alert_details=" | ".join(alerts))
        else:
            self.logger.info("Bot health is within acceptable parameters.")

    def _check_resource_usage(self) -> dict:
        """
        Collects detailed system and bot resource usage metrics.
        收集系统和机器人的详细资源使用指标。
        
        Returns:
            Dictionary containing various resource metrics.
            包含各种资源指标的字典
        """
        # Get system-wide metrics
        # 获取系统级指标
        cpu_percent = psutil.cpu_percent(interval=1)  # 1 second interval for accurate measurement
                                                     # 1秒间隔以获得准确测量
        virtual_memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Get process-specific metrics
        # 获取进程特定的指标
        try:
            bot_memory_info = self.process.memory_info()
            bot_cpu_percent = self.process.cpu_percent()
            open_files = len(self.process.open_files())
            thread_count = self.process.num_threads()
            
            metrics = ResourceMetrics(
                timestamp=datetime.now(),
                cpu_percent=cpu_percent,
                memory_percent=virtual_memory.percent,
                disk_percent=disk.percent,
                bot_cpu_percent=bot_cpu_percent,
                bot_memory_mb=bot_memory_info.rss / (1024 * 1024),  # Convert to MB
                                                                    # 转换为MB
                open_files=open_files,
                thread_count=thread_count
            )
            
            # Store metrics history
            # 存储指标历史
            self._metrics_history.append(metrics)
            if len(self._metrics_history) > self.metrics_history_size:
                self._metrics_history.pop(0)
            
            return {
                "cpu": cpu_percent,
                "memory": virtual_memory.percent,
                "disk": disk.percent,
                "bot_cpu": bot_cpu_percent,
                "bot_memory_mb": bot_memory_info.rss / (1024 * 1024),
                "bot_memory_percent": (bot_memory_info.rss / virtual_memory.total) * 100,
                "open_files": open_files,
                "thread_count": thread_count,
                "memory_available_mb": virtual_memory.available / (1024 * 1024)
            }
            
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            self.logger.error(f"Failed to get process metrics: {e}")
            return {
                "cpu": cpu_percent,
                "memory": virtual_memory.percent,
                "disk": disk.percent,
                "error": str(e)
            }

    def get_resource_trends(self) -> Dict[str, float]:
        """
        Calculate resource usage trends over the stored history.
        计算存储历史记录中的资源使用趋势。
        
        Returns:
            Dictionary containing trend metrics (positive values indicate increasing usage).
            包含趋势指标的字典（正值表示使用量增加）
        """
        if len(self._metrics_history) < 2:
            return {}
            
        recent = self._metrics_history[-1]
        old = self._metrics_history[0]
        time_diff = (recent.timestamp - old.timestamp).total_seconds() / 3600  # hours
                                                                             # 小时

        if time_diff < 0.016667:  # Less than 1 minute
                                 # 小于1分钟
            return {}
        
        return {
            "cpu_trend": (recent.cpu_percent - old.cpu_percent) / time_diff,
            "memory_trend": (recent.memory_percent - old.memory_percent) / time_diff,
            "bot_cpu_trend": (recent.bot_cpu_percent - old.bot_cpu_percent) / time_diff,
            "bot_memory_trend": (recent.bot_memory_mb - old.bot_memory_mb) / time_diff
        }

    async def _check_and_alert_resource_usage(self, usage: dict):
        """
        Enhanced resource monitoring with trend analysis and detailed alerts.
        增强的资源监控，包含趋势分析和详细警报。
        """
        alerts = []
        trends = self.get_resource_trends()
        
        # Check current values against thresholds
        # 检查当前值是否超过阈值
        for resource, threshold in RESSOURCE_THRESHOLDS.items():
            current_value = usage.get(resource, 0)
            if current_value > threshold:
                trend = trends.get(f"{resource}_trend", 0)
                trend_direction = "increasing" if trend > 1 else "decreasing" if trend < -1 else "stable"
                message = (
                    f"{resource.upper()} usage is high: {current_value:.1f}% "
                    f"(Threshold: {threshold}%, Trend: {trend_direction})"
                )
                alerts.append(message)

        # Check for CPU spikes
        # 检查CPU突增
        if trends.get("bot_cpu_trend", 0) > 10:  # %/hour
                                                # 每小时百分比
            alerts.append(f"High CPU usage trend: Bot CPU usage increasing by "
                        f"{trends['bot_cpu_trend']:.1f}%/hour")

        if alerts:
            self.logger.warning(f"Resource alerts: {alerts}")
            await self.notification_handler.async_send_notification(
                NotificationType.HEALTH_CHECK_ALERT,
                alert_details=" | ".join(alerts)
            )

    def _handle_stop(self, reason: str) -> None:
        """
        Handles the STOP_BOT event to stop the HealthCheck.
        处理STOP_BOT事件以停止健康检查。

        Args:
            reason: The reason for stopping the bot.
                    停止机器人的原因
        """
        if not self._is_running:
            self.logger.warning("HealthCheck is not running.")
            return

        self._is_running = False
        self._stop_event.set()
        self.logger.info(f"HealthCheck stopped: {reason}")

    async def _handle_start(self, reason: str) -> None:
        """
        Handles the START_BOT event to start the HealthCheck.
        处理START_BOT事件以启动健康检查。

        Args:
            reason: The reason for starting the bot.
                    启动机器人的原因
        """
        if self._is_running:
            self.logger.warning("HealthCheck is already running.")
            return

        self.logger.info(f"HealthCheck starting: {reason}")
        await self.start()