import logging, traceback
from typing import Optional, Dict, Any
from core.services.exchange_service_factory import ExchangeServiceFactory
from strategies.strategy_type import StrategyType
from strategies.grid_trading_strategy import GridTradingStrategy
from strategies.plotter import Plotter
from strategies.trading_performance_analyzer import TradingPerformanceAnalyzer
from core.order_handling.order_manager import OrderManager
from core.validation.order_validator import OrderValidator
from core.order_handling.order_status_tracker import OrderStatusTracker
from core.bot_management.event_bus import EventBus, Events
from core.order_handling.fee_calculator import FeeCalculator
from core.order_handling.balance_tracker import BalanceTracker
from core.order_handling.order_book import OrderBook
from core.grid_management.grid_manager import GridManager
from core.order_handling.execution_strategy.order_execution_strategy_factory import OrderExecutionStrategyFactory
from core.services.exceptions import UnsupportedExchangeError, DataFetchError, UnsupportedTimeframeError
from config.config_manager import ConfigManager
from config.trading_mode import TradingMode
from .notification.notification_handler import NotificationHandler

"""网格交易机器人核心实现

该类实现了一个完整的网格交易机器人，包括以下主要功能：
1. 初始化各种交易组件（订单管理、网格管理、余额追踪等）
2. 运行和停止交易策略
3. 处理交易事件（如停止、重启等）
4. 监控机器人健康状态
5. 生成交易表现报告

主要组件：
- 交易所服务：负责与交易所API交互
- 订单管理器：处理订单的创建、执行和跟踪
- 网格管理器：维护网格价格和状态
- 余额追踪器：监控账户余额变化
- 事件总线：处理系统内部事件通信
- 性能分析器：分析和报告交易表现
"""

class GridTradingBot:
    def __init__(
        self, 
        config_path: str, 
        config_manager: ConfigManager,
        notification_handler: NotificationHandler,
        event_bus: EventBus,
        save_performance_results_path: Optional[str] = None, 
        no_plot: bool = False
    ):
        """初始化网格交易机器人

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
            trading_pair = f"{base_currency}/{quote_currency}"
            strategy_type: StrategyType = self.config_manager.get_strategy_type()
            self.logger.info(f"Starting Grid Trading Bot in {self.trading_mode.value} mode with strategy: {strategy_type.value}")
            self.is_running = False

            # 创建交易所服务和订单执行策略
            self.exchange_service = ExchangeServiceFactory.create_exchange_service(self.config_manager, self.trading_mode)
            order_execution_strategy = OrderExecutionStrategyFactory.create(self.config_manager, self.exchange_service)
            
            # 创建网格管理器和订单验证器
            grid_manager = GridManager(self.config_manager, strategy_type)
            order_validator = OrderValidator()
            fee_calculator = FeeCalculator(self.config_manager)

            # 初始化余额追踪器
            self.balance_tracker = BalanceTracker(
                event_bus=self.event_bus,
                fee_calculator=fee_calculator,
                trading_mode=self.trading_mode,
                base_currency=base_currency,
                quote_currency=quote_currency
            )
            
            # 创建订单簿和订单状态追踪器
            order_book = OrderBook()
            self.order_status_tracker = OrderStatusTracker(
                order_book=order_book,
                order_execution_strategy=order_execution_strategy,
                event_bus=self.event_bus,
                polling_interval=5.0,
            )

            # 创建订单管理器
            order_manager = OrderManager(
                grid_manager,
                order_validator,
                self.balance_tracker,
                order_book,
                self.event_bus,
                order_execution_strategy,
                self.notification_handler,
                self.trading_mode,
                trading_pair,
                strategy_type
            )
            
            # 创建交易性能分析器和图表绘制器
            trading_performance_analyzer = TradingPerformanceAnalyzer(self.config_manager, order_book)
            plotter = Plotter(grid_manager, order_book) if self.trading_mode == TradingMode.BACKTEST else None
            
            # 初始化网格交易策略
            self.strategy = GridTradingStrategy(
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
        """运行网格交易机器人

        该方法执行以下步骤：
        1. 设置初始账户余额
        2. 启动订单状态追踪
        3. 初始化并运行交易策略
        4. 绘制回测结果（如果启用）
        5. 生成性能报告

        返回:
            包含配置信息、性能总结和订单记录的字典
        """
        try:
            self.is_running = True

            # 设置初始账户余额
            await self.balance_tracker.setup_balances(
                initial_balance=self.config_manager.get_initial_balance(),
                initial_crypto_balance=0.0,
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

        self.logger.info("Stopping Grid Trading Bot...")

        try:
            # 停止订单状态追踪
            await self.order_status_tracker.stop_tracking()
            # 停止策略执行
            await self.strategy.stop()
            self.is_running = False

        except Exception as e:
            self.logger.error(f"Error while stopping components: {e}", exc_info=True)

        self.logger.info("Grid Trading Bot has been stopped.")
    
    async def restart(self) -> None:
        """重启机器人
        
        如果机器人正在运行，先停止然后重新启动
        """
        if self.is_running:
            self.logger.info("Bot is already running. Restarting...")
            await self._stop()

        self.logger.info("Restarting Grid Trading Bot...")
        self.is_running = True

        try:
            # 重新启动订单状态追踪
            self.order_status_tracker.start_tracking()
            # 重启策略
            await self.strategy.restart()

        except Exception as e:
            self.logger.error(f"Error while restarting components: {e}", exc_info=True)

        self.logger.info("Grid Trading Bot has been restarted.")

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

        检查策略运行状态和交易所连接状态

        返回:
            包含策略状态、交易所状态和总体状态的字典
        """
        health_status = {
            "strategy": await self._check_strategy_health(),
            "exchange_status": await self._get_exchange_status()
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
    
    def get_balances(self) -> Dict[str, float]:
        """获取当前账户余额

        返回:
            包含法币和加密货币余额的字典，包括已保留的金额
        """
        return {
            "fiat": self.balance_tracker.balance,
            "reserved_fiat": self.balance_tracker.reserved_fiat,
            "crypto": self.balance_tracker.crypto_balance,
            "reserved_crypto": self.balance_tracker.reserved_crypto
        }