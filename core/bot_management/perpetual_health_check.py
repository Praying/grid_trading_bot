import logging
from typing import Dict
from datetime import datetime
from core.bot_management.health_check import HealthCheck, ResourceMetrics
from core.bot_management.perpetual_grid_trading_bot import PerpetualGridTradingBot
from core.bot_management.notification.notification_handler import NotificationHandler
from core.bot_management.notification.notification_content import NotificationType
from core.bot_management.event_bus import EventBus

class PerpetualHealthCheck(HealthCheck):
    """
    Specialized health check for perpetual futures trading bot.
    永续合约交易机器人的专用健康检查器。
    
    Extends the base HealthCheck with additional monitoring for:
    在基础健康检查的基础上增加以下监控：
    - Margin ratio monitoring
      保证金率监控
    - Liquidation risk monitoring
      强平风险监控
    - Funding rate monitoring
      资金费率监控
    """

    def __init__(
        self,
        bot: PerpetualGridTradingBot,
        notification_handler: NotificationHandler,
        event_bus: EventBus,
        check_interval: int = 60,
        metrics_history_size: int = 60,
        margin_ratio_threshold: float = 0.1,  # Alert when margin ratio below 10%
                                             # 当保证金率低于10%时发出警报
        funding_rate_threshold: float = 0.001  # Alert when funding rate exceeds 0.1%
                                              # 当资金费率超过0.1%时发出警报
    ):
        """
        Initialize PerpetualHealthCheck.
        初始化永续合约健康检查器。

        Args:
            bot: The PerpetualGridTradingBot instance to monitor.
                 要监控的永续合约网格交易机器人实例
            notification_handler: The NotificationHandler for sending alerts.
                                 用于发送警报的通知处理器
            event_bus: The EventBus instance for listening to bot lifecycle events.
                       用于监听机器人生命周期事件的事件总线
            check_interval: Time interval (in seconds) between health checks.
                           健康检查的时间间隔（秒）
            metrics_history_size: Number of metrics to keep in the history.
                                 历史指标保存的数量
            margin_ratio_threshold: The threshold for margin ratio alerts.
                                   保证金率警报阈值
            funding_rate_threshold: The threshold for funding rate alerts.
                                   资金费率警报阈值
        """
        super().__init__(
            bot=bot,
            notification_handler=notification_handler,
            event_bus=event_bus,
            check_interval=check_interval,
            metrics_history_size=metrics_history_size
        )
        self.perpetual_bot = bot
        self.margin_ratio_threshold = margin_ratio_threshold
        self.funding_rate_threshold = funding_rate_threshold

    async def _perform_checks(self):
        """
        Extends the base health checks with perpetual-specific monitoring.
        扩展基础健康检查，添加永续合约特有的监控。
        """
        # Perform base health checks
        # 执行基础健康检查
        await super()._perform_checks()

        # Perform perpetual-specific checks
        # 执行永续合约特有的检查
        await self._check_perpetual_metrics()

    async def _check_perpetual_metrics(self):
        """
        Check perpetual-specific metrics including margin ratio and funding rate.
        检查永续合约特有的指标，包括保证金率和资金费率。
        """
        try:
            # Get current perpetual metrics
            # 获取当前永续合约指标
            perpetual_metrics = await self.perpetual_bot.get_perpetual_metrics()
            self.logger.info(f"Perpetual metrics: {perpetual_metrics}")

            alerts = []

            # Check margin ratio
            # 检查保证金率
            margin_ratio = perpetual_metrics.get('margin_ratio', 0)
            if margin_ratio < self.margin_ratio_threshold:
                message = (
                    f"Low margin ratio alert: Current margin ratio is {margin_ratio:.2%} "
                    f"(Threshold: {self.margin_ratio_threshold:.2%})"
                )
                alerts.append(message)
                self.logger.warning(message)

            # Check funding rate
            # 检查资金费率
            funding_rate = abs(perpetual_metrics.get('funding_rate', 0))
            if funding_rate > self.funding_rate_threshold:
                message = (
                    f"High funding rate alert: Current funding rate is {funding_rate:.3%} "
                    f"(Threshold: ±{self.funding_rate_threshold:.3%})"
                )
                alerts.append(message)
                self.logger.warning(message)

            # Check liquidation risk
            # 检查强平风险
            liquidation_price = perpetual_metrics.get('liquidation_price')
            current_price = perpetual_metrics.get('current_price')
            if liquidation_price and current_price:
                price_distance = abs(current_price - liquidation_price) / current_price
                if price_distance < 0.1:  # Alert if within 10% of liquidation price
                                         # 如果价格接近强平价格的10%范围内，发出警报
                    message = (
                        f"Liquidation risk alert: Current price {current_price} is within "
                        f"{price_distance:.2%} of liquidation price {liquidation_price}"
                    )
                    alerts.append(message)
                    self.logger.warning(message)

            if alerts:
                await self.notification_handler.async_send_notification(
                    NotificationType.HEALTH_CHECK_ALERT,
                    alert_details=" | ".join(alerts)
                )

        except Exception as e:
            error_message = f"Error checking perpetual metrics: {str(e)}"
            self.logger.error(error_message)
            await self.notification_handler.async_send_notification(
                NotificationType.ERROR_OCCURRED,
                error_details=error_message
            )