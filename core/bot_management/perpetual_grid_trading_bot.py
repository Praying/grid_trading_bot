import logging, traceback
from typing import Optional, Dict, Any
from core.services.exchange_service_factory import ExchangeServiceFactory
from strategies.perpetual_plotter import PerpetualPlotter
from strategies.perpetual_trading_performance_analyzer import PerpetualTradingPerformanceAnalyzer
from strategies.strategy_type import StrategyType
from strategies.perpetual_grid_trading_strategy import PerpetualGridTradingStrategy
from strategies.plotter import Plotter
from strategies.trading_performance_analyzer import TradingPerformanceAnalyzer
from core.order_handling.perpetual_order_manager import PerpetualOrderManager
from core.validation.perpetual_order_validator import PerpetualOrderValidator
from core.order_handling.order_status_tracker import OrderStatusTracker
from core.bot_management.event_bus import EventBus, Events
from core.order_handling.fee_calculator import FeeCalculator
from core.order_handling.perpetual_balance_tracker import PerpetualBalanceTracker
from core.order_handling.perpetual_order_book import PerpetualOrderBook
from core.grid_management.perpetual_grid_manager import PerpetualGridManager
from core.order_handling.execution_strategy.order_execution_strategy_factory import OrderExecutionStrategyFactory
from core.services.exceptions import UnsupportedExchangeError, DataFetchError, UnsupportedTimeframeError
from config.config_manager import ConfigManager
from config.trading_mode import TradingMode
from .notification.notification_content import NotificationType
from .notification.notification_handler import NotificationHandler
from ..order_handling.perpetual_order_status_tracker import PerpetualOrderStatusTracker

"""永续合约U本位网格交易机器人核心实现

该类实现了一个完整的永续合约U本位网格交易机器人，包括以下主要功能：
1. 初始化各种交易组件（订单管理、网格管理、保证金追踪等）
2. 运行和停止交易策略
3. 处理交易事件（如停止、重启、强平等）
4. 监控机器人健康状态和风险
5. 生成交易表现报告

主要组件：
- 交易所服务：负责与永续合约交易所API交互
- 订单管理器：处理开平仓订单的创建、执行和跟踪
- 网格管理器：维护多空双向网格价格和状态
- 保证金追踪器：监控保证金和仓位信息
- 事件总线：处理系统内部事件通信
- 性能分析器：分析和报告交易表现
"""

class PerpetualGridTradingBot:
    def __init__(
        self, 
        config_path: str, 
        config_manager: ConfigManager,
        notification_handler: NotificationHandler,
        event_bus: EventBus,
        save_performance_results_path: Optional[str] = None, 
        no_plot: bool = False
    ):
        """初始化永续合约网格交易机器人

        参数:
            config_path: 配置文件路径
            config_manager: 配置管理器实例
            notification_handler: 通知处理器实例
            event_bus: 事件总线实例
            save_performance_results_path: 性能结果保存路径（可选）
            no_plot: 是否禁用图表绘制（默认False）
        """
        try:
            # 初始化日志记录器
            self.logger = logging.getLogger(self.__class__.__name__)
            # 保存基础配置
            self.config_path = config_path
            self.config_manager = config_manager
            self.notification_handler = notification_handler
            self.event_bus = event_bus
            # 订阅机器人停止和启动事件
            self.event_bus.subscribe(Events.STOP_BOT, self._handle_stop_bot_event)
            self.event_bus.subscribe(Events.START_BOT, self._handle_start_bot_event)
            self.save_performance_results_path = save_performance_results_path
            self.no_plot = no_plot

            # 获取交易模式和交易对信息
            self.trading_mode: TradingMode = self.config_manager.get_trading_mode()
            base_currency: str = self.config_manager.get_base_currency()
            quote_currency: str = self.config_manager.get_quote_currency()
            trading_pair = f"{base_currency}/{quote_currency}:{quote_currency}"
            strategy_type: StrategyType = self.config_manager.get_strategy_type()
            self.logger.info(f"Starting Perpetual Grid Trading Bot in {self.trading_mode.value} mode with strategy: {strategy_type.value}")
            self.is_running = False

            # 创建永续合约交易所服务和订单执行策略
            self.exchange_service = ExchangeServiceFactory.create_exchange_service(self.config_manager, self.trading_mode)
            order_execution_strategy = OrderExecutionStrategyFactory.create(self.config_manager, self.exchange_service)
            
            # 创建永续合约网格管理器和订单验证器
            grid_manager = PerpetualGridManager(self.config_manager, strategy_type, config_manager.get_trading_settings()["leverage"], config_manager.get_trading_settings()["margin_mode"])
            order_validator = PerpetualOrderValidator()
            fee_calculator = FeeCalculator(self.config_manager)

            # 初始化永续合约保证金追踪器
            self.balance_tracker = PerpetualBalanceTracker(
                event_bus=self.event_bus,
                fee_calculator=fee_calculator,
                trading_mode=self.trading_mode,
                base_currency=base_currency,
                quote_currency=quote_currency,
                leverage=config_manager.get_trading_settings()["leverage"],
            )
            
            # 创建永续合约订单簿和订单状态追踪器
            order_book = PerpetualOrderBook()
            self.order_status_tracker = PerpetualOrderStatusTracker(
                order_book=order_book,
                order_execution_strategy=order_execution_strategy,
                event_bus=self.event_bus,
                base_currency=base_currency,
                quote_currency=quote_currency,
                polling_interval=5.0,
            )

            # 创建永续合约订单管理器
            order_manager = PerpetualOrderManager(
                self.exchange_service,
                grid_manager,
                self.trading_mode,
                trading_pair,
                order_execution_strategy,
                order_book,
                self.balance_tracker,
                order_validator,
                self.event_bus,
                5.0,
            )
            
            # 创建交易性能分析器和图表绘制器
            trading_performance_analyzer = PerpetualTradingPerformanceAnalyzer(self.config_manager, order_book)
            plotter = PerpetualPlotter(grid_manager, order_book) if self.trading_mode == TradingMode.BACKTEST else None
            
            # 初始化网格交易策略
            self.strategy = PerpetualGridTradingStrategy(
                self.config_manager,
                self.event_bus,
                self.exchange_service,
                grid_manager,
                order_manager,
                self.balance_tracker,
                trading_performance_analyzer,
                self.trading_mode,
                trading_pair,
                plotter
            )

        except (UnsupportedExchangeError, DataFetchError, UnsupportedTimeframeError) as e:
            self.logger.error(f"{type(e).__name__}: {e}")
            raise

        except Exception as e:
            self.logger.error("An unexpected error occurred.")
            self.logger.error(traceback.format_exc())
            raise

    async def run(self) -> Optional[Dict[str, Any]]:
        """运行永续合约网格交易机器人

        该方法执行以下步骤：
        1. 设置初始保证金和仓位信息
        2. 启动订单状态追踪
        3. 初始化并运行交易策略
        4. 绘制回测结果（如果启用）
        5. 生成性能报告

        返回:
            包含配置信息、性能总结和订单记录的字典
        """
        try:
            self.is_running = True

            await self.exchange_service.initialize()
            # 设置初始保证金
            await self.balance_tracker.setup_balances(
                initial_margin=self.config_manager.get_initial_balance(),
                exchange_service=self.exchange_service
            )

            # 启动订单状态追踪
            self.order_status_tracker.start_tracking()
            # 初始化并运行策略
            self.strategy.initialize_strategy()
            await self.strategy.run()

            # 如果启用了图表绘制，显示回测结果
            if not self.no_plot:
                self.strategy.plot_results()

            # 生成并返回性能报告
            return self._generate_and_log_performance()

        except Exception as e:
            self.logger.error(f"An unexpected error occurred {e}")
            self.logger.error(traceback.format_exc())
            raise
        
        finally:
            self.is_running = False

    async def _handle_stop_bot_event(self, reason: str) -> None:
        """处理停止机器人事件

        参数:
            reason: 停止原因
        """
        self.logger.info(f"Handling STOP_BOT event: {reason}")
        await self._stop()

    async def _handle_start_bot_event(self, reason: str) -> None:
        """处理启动机器人事件

        参数:
            reason: 启动原因
        """
        self.logger.info(f"Handling START_BOT event: {reason}")
        await self.restart()
    
    async def _stop(self) -> None:
        """停止机器人运行
        
        停止订单追踪和策略执行
        """
        if not self.is_running:
            self.logger.info("Bot is not running. Nothing to stop.")
            return

        self.logger.info("Stopping Perpetual Grid Trading Bot...")

        try:
            # 停止订单状态追踪
            await self.order_status_tracker.stop_tracking()
            # 停止策略执行
            await self.strategy.stop()
            self.is_running = False

        except Exception as e:
            self.logger.error(f"Error while stopping components: {e}", exc_info=True)

        self.logger.info("Perpetual Grid Trading Bot has been stopped.")
    
    async def restart(self) -> None:
        """重启机器人
        
        如果机器人正在运行，先停止然后重新启动
        """
        if self.is_running:
            self.logger.info("Bot is already running. Restarting...")
            await self._stop()

        self.logger.info("Restarting Perpetual Grid Trading Bot...")
        self.is_running = True

        try:
            # 重新启动订单状态追踪
            self.order_status_tracker.start_tracking()
            # 重启策略
            await self.strategy.restart()

        except Exception as e:
            self.logger.error(f"Error while restarting components: {e}", exc_info=True)

        self.logger.info("Perpetual Grid Trading Bot has been restarted.")

    def _generate_and_log_performance(self) -> Optional[Dict[str, Any]]:
        """生成并记录性能报告

        返回:
            包含配置信息、性能总结和订单记录的字典
        """
        performance_summary, formatted_orders = self.strategy.generate_performance_report()
        return {
            "config": self.config_path,
            "performance_summary": performance_summary,
            "orders": formatted_orders
        }
    
    async def get_bot_health_status(self) -> dict:
        """获取机器人健康状态

        检查策略运行状态、交易所连接状态和保证金风险

        返回:
            包含策略状态、交易所状态、保证金风险和总体状态的字典
        """
        health_status = {
            "strategy": await self._check_strategy_health(),
            "exchange_status": await self._get_exchange_status(),
            "margin_risk": await self._check_margin_risk()
        }

        health_status["overall"] = all(health_status.values())
        return health_status
    
    async def _check_strategy_health(self) -> bool:
        """检查策略健康状态

        返回:
            如果策略正在运行返回True，否则返回False
        """
        if not self.is_running:
            self.logger.warning("Bot has stopped unexpectedly.")
            return False
        return True

    async def _get_exchange_status(self) -> str:
        """获取交易所连接状态

        返回:
            交易所状态字符串
        """
        exchange_status = await self.exchange_service.get_exchange_status()
        return exchange_status.get("status", "unknown")
    
    async def _check_margin_risk(self) -> bool:
        """检查保证金风险

        检查当前保证金率是否接近强平线

        返回:
            如果保证金安全返回True，否则返回False
        """
        try:
            margin_ratio = await self.exchange_service.get_margin_ratio()
            liquidation_threshold = self.config_manager.get_liquidation_threshold()
            
            if margin_ratio <= liquidation_threshold:
                self.logger.warning(f"Margin ratio ({margin_ratio}) is below liquidation threshold ({liquidation_threshold})")
                return False
            return True

        except Exception as e:
            self.logger.error(f"Error checking margin risk: {e}", exc_info=True)
            await self.notification_handler.async_send_notification(
                NotificationType.MARGIN_RISK,
                error_details=f"Failed to check margin risk: {str(e)}"
            )
            return False  # 发生异常时返回False表示存在风险

    async def get_perpetual_metrics(self):
        pass