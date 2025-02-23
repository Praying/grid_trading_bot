import logging
from typing import Optional, Tuple
import pandas as pd
import numpy as np
from .trading_strategy_interface import TradingStrategyInterface
from config.trading_mode import TradingMode
from core.bot_management.event_bus import EventBus, Events
from config.config_manager import ConfigManager
from core.services.exchange_interface import ExchangeInterface
from core.grid_management.grid_manager import GridManager
from core.order_handling.order_manager import OrderManager
from core.order_handling.balance_tracker import BalanceTracker
from strategies.trading_performance_analyzer import TradingPerformanceAnalyzer
from strategies.plotter import Plotter

class GridTradingStrategy(TradingStrategyInterface):
    TICKER_REFRESH_INTERVAL = 3  # in seconds
                                 # 以秒为单位

    def __init__(
        self,
        config_manager: ConfigManager,
        event_bus: EventBus,
        exchange_service: ExchangeInterface,
        grid_manager: GridManager,
        order_manager: OrderManager,
        balance_tracker: BalanceTracker,
        trading_performance_analyzer: TradingPerformanceAnalyzer,
        trading_mode: TradingMode,
        trading_pair: str,
        plotter: Optional[Plotter] = None
    ):
        super().__init__(config_manager, balance_tracker)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.event_bus = event_bus
        self.exchange_service = exchange_service
        self.grid_manager = grid_manager
        self.order_manager = order_manager
        self.trading_performance_analyzer = trading_performance_analyzer
        self.trading_mode = trading_mode
        self.trading_pair = trading_pair
        self.plotter = plotter
        self.data = self._initialize_historical_data()
        self.live_trading_metrics = [] 
        self._running = True
    
    def _initialize_historical_data(self) -> Optional[pd.DataFrame]:
        """
        Initializes historical market data (OHLCV).
        In LIVE or PAPER_TRADING mode returns None.
        
        初始化历史市场数据（开高低收成交量）。
        在实盘或模拟交易模式下返回None。
        """
        if self.trading_mode != TradingMode.BACKTEST:
            return None

        try:
            timeframe, start_date, end_date = self._extract_config()
            return self.exchange_service.fetch_ohlcv(self.trading_pair, timeframe, start_date, end_date)
        except Exception as e:
            self.logger.error(f"Failed to initialize data for backtest trading mode: {e}")
            return None
    
    def _extract_config(self) -> Tuple[str, str, str]:
        """
        Extracts configuration values for timeframe, start date, and end date.

        Returns:
            tuple: A tuple containing the timeframe, start date, and end date as strings.
        
        提取时间周期、开始日期和结束日期的配置值。

        返回：
            tuple：包含时间周期、开始日期和结束日期的字符串元组。
        """
        timeframe = self.config_manager.get_timeframe()
        start_date = self.config_manager.get_start_date()
        end_date = self.config_manager.get_end_date()
        return timeframe, start_date, end_date

    def initialize_strategy(self):
        """
        Initializes the trading strategy by setting up the grid and levels.
        This method prepares the strategy to be ready for trading.
        
        通过设置网格和价格层级来初始化交易策略。
        此方法为交易做好准备。
        """
        self.grid_manager.initialize_grids_and_levels()
    
    async def stop(self):
        """
        Stops the trading execution.

        This method halts all trading activities, closes active exchange 
        connections, and updates the internal state to indicate the bot 
        is no longer running.
        
        停止交易执行。

        此方法会停止所有交易活动，关闭活跃的交易所连接，
        并更新内部状态以表明机器人不再运行。
        """
        self._running = False
        await self.exchange_service.close_connection()
        self.logger.info("Trading execution stopped.")

    async def restart(self):
        """
        Restarts the trading session. If the strategy is not running, starts it.
        
        重启交易会话。如果策略未运行，则启动它。
        """
        if not self._running:
            self.logger.info("Restarting trading session.")
            await self.run()

    async def run(self):
        """
        Starts the trading session based on the configured mode.

        For backtesting, this simulates the strategy using historical data.
        For live or paper trading, this interacts with the exchange to manage
        real-time trading.

        Raises:
            Exception: If any error occurs during the trading session.
        
        根据配置的模式启动交易会话。

        对于回测，使用历史数据模拟策略。
        对于实盘或模拟交易，与交易所交互以管理实时交易。

        异常：
            Exception：交易会话期间发生任何错误时抛出。
        """
        self._running = True        
        trigger_price = self.grid_manager.get_trigger_price()

        if self.trading_mode == TradingMode.BACKTEST:
            await self._run_backtest(trigger_price)
            self.logger.info("Ending backtest simulation")
            self._running = False
        else:
            await self._run_live_or_paper_trading(trigger_price)
    
    async def _run_live_or_paper_trading(self, trigger_price: float):
        """
        Executes live or paper trading sessions based on real-time ticker updates.

        The method listens for ticker updates, initializes grid orders when 
        the trigger price is reached, and manages take-profit and stop-loss events.

        Args:
            trigger_price (float): The price at which grid orders are triggered.
        
        基于实时行情更新执行实盘或模拟交易会话。

        该方法监听行情更新，在达到触发价格时初始化网格订单，
        并管理止盈和止损事件。

        参数：
            trigger_price (float)：触发网格订单的价格。
        """
        self.logger.info(f"Starting {'live' if self.trading_mode == TradingMode.LIVE else 'paper'} trading")
        last_price: Optional[float] = None
        grid_orders_initialized = False

        async def on_ticker_update(current_price):
            nonlocal last_price, grid_orders_initialized
            try:
                if not self._running:
                    self.logger.info("Trading stopped; halting price updates.")
                    return
                
                account_value = self.balance_tracker.get_total_balance_value(current_price)
                self.live_trading_metrics.append((pd.Timestamp.now(), account_value, current_price))
                
                grid_orders_initialized = await self._initialize_grid_orders_once(
                    current_price, 
                    trigger_price, 
                    grid_orders_initialized, 
                    last_price
                )

                if not grid_orders_initialized:
                    last_price = current_price
                    return

                if await self._handle_take_profit_stop_loss(current_price):
                    return
                
                last_price = current_price

            except Exception as e:
                self.logger.error(f"Error during ticker update: {e}", exc_info=True)
        
        try:
            await self.exchange_service.listen_to_ticker_updates(
                self.trading_pair, 
                on_ticker_update, 
                self.TICKER_REFRESH_INTERVAL
            )
        
        except Exception as e:
            self.logger.error(f"Error in live/paper trading loop: {e}", exc_info=True)
        
        finally:
            self.logger.info("Exiting live/paper trading loop.")

    async def _run_backtest(self, trigger_price: float) -> None:
        """
        Executes the backtesting simulation based on historical OHLCV data.

        This method simulates trading using preloaded data, managing grid levels,
        executing orders, and updating account values over the timeframe.

        Args:
            trigger_price (float): The price at which grid orders are triggered.
        
        基于历史OHLCV数据执行回测模拟。

        此方法使用预加载的数据进行交易模拟，管理网格层级，
        执行订单，并在时间范围内更新账户价值。

        参数：
            trigger_price (float)：触发网格订单的价格。
        """
        if self.data is None:
            self.logger.error("No data available for backtesting.")
            return

        self.logger.info("Starting backtest simulation")
        self.data['account_value'] = np.nan
        self.close_prices = self.data['close'].values
        high_prices = self.data['high'].values
        low_prices = self.data['low'].values
        timestamps = self.data.index
        self.data.loc[timestamps[0], 'account_value'] = self.balance_tracker.get_total_balance_value(price=self.close_prices[0])
        grid_orders_initialized = False
        last_price = None

        for i, (current_price, high_price, low_price, timestamp) in enumerate(zip(self.close_prices, high_prices, low_prices, timestamps)):
            grid_orders_initialized = await self._initialize_grid_orders_once(
                current_price, 
                trigger_price,
                grid_orders_initialized,
                last_price
            )

            if not grid_orders_initialized:
                self.data.loc[timestamps[i], 'account_value'] = self.balance_tracker.get_total_balance_value(price=current_price)
                last_price = current_price
                continue

            await self.order_manager.simulate_order_fills(high_price, low_price, timestamp)

            if await self._handle_take_profit_stop_loss(current_price):
                break

            self.data.loc[timestamp, 'account_value'] = self.balance_tracker.get_total_balance_value(current_price)
            last_price = current_price
    
    async def _initialize_grid_orders_once(
        self, 
        current_price: float, 
        trigger_price: float, 
        grid_orders_initialized: bool,
        last_price: Optional[float] = None
    ) -> bool:
        """
        Extracts configuration values for timeframe, start date, and end date.

        Returns:
            tuple: A tuple containing the timeframe, start date, and end date as strings.
        """
        if grid_orders_initialized:
            return True
        
        if last_price is None:
            self.logger.debug("No previous price recorded yet. Waiting for the next price update.")
            return False

        if last_price <= trigger_price <= current_price or last_price == trigger_price:
            self.logger.info(f"Current price {current_price} reached trigger price {trigger_price}. Will perform initial purhcase")
            await self.order_manager.perform_initial_purchase(current_price)
            self.logger.info(f"Initial purchase done, will initialize grid orders")
            await self.order_manager.initialize_grid_orders(current_price)
            return True

        self.logger.info(f"Current price {current_price} did not cross trigger price {trigger_price}. Last price: {last_price}.")
        return False

    def generate_performance_report(self) -> Tuple[dict, list]:
        """
        Generates a performance report for the trading session.

        It evaluates the strategy's performance by analyzing
        the account value, fees, and final price over the given timeframe.

        Returns:
            tuple: A dictionary summarizing performance metrics and a list of formatted order details.
        
        生成交易会话的性能报告。

        通过分析给定时间范围内的账户价值、费用和最终价格来评估策略的表现。

        返回：
            tuple：包含性能指标摘要的字典和格式化订单详情的列表。
        """
        if self.trading_mode == TradingMode.BACKTEST:
            initial_price = self.close_prices[0]
            final_price = self.close_prices[-1]
            return self.trading_performance_analyzer.generate_performance_summary(
                self.data, 
                initial_price,
                self.balance_tracker.get_adjusted_fiat_balance(), 
                self.balance_tracker.get_adjusted_crypto_balance(), 
                final_price,
                self.balance_tracker.total_fees
            )
        else:
            if not self.live_trading_metrics:
                self.logger.warning("No account value data available for live/paper trading mode.")
                return {}, []
            
            live_data = pd.DataFrame(self.live_trading_metrics, columns=["timestamp", "account_value", "price"])
            live_data.set_index("timestamp", inplace=True)
            initial_price = live_data.iloc[0]["price"]
            final_price = live_data.iloc[-1]["price"]

            return self.trading_performance_analyzer.generate_performance_summary(
                live_data, 
                initial_price,
                self.balance_tracker.get_adjusted_fiat_balance(), 
                self.balance_tracker.get_adjusted_crypto_balance(), 
                final_price,
                self.balance_tracker.total_fees
            )

    def plot_results(self) -> None:
        """
        Plots the backtest results using the provided plotter.

        This method generates and displays visualizations of the trading 
        strategy's performance during backtesting. If the bot is running
        in live or paper trading mode, plotting is not available.
        
        使用提供的绘图器绘制回测结果。

        此方法生成并显示回测期间交易策略表现的可视化图表。
        如果机器人在实盘或模拟交易模式下运行，则不提供绘图功能。
        """
        if self.trading_mode == TradingMode.BACKTEST:
            self.plotter.plot_results(self.data)
        else:
            self.logger.info("Plotting is not available for live/paper trading mode.")
    
    async def _handle_take_profit_stop_loss(self, current_price: float) -> bool:
        """
        Handles take-profit or stop-loss events based on current price.
        Publishes STOP_BOT event if either condition is triggered.

        根据当前价格处理止盈或止损事件。
        如果触发任一条件，则发布STOP_BOT事件。
        """
        tp_or_sl_triggered = await self._evaluate_tp_or_sl(current_price)
        if tp_or_sl_triggered:
            self.logger.info("Take-profit or stop-loss triggered, ending trading session.")
            await self.event_bus.publish(Events.STOP_BOT, "TP or SL hit.")
            return True
        return False

    async def _evaluate_tp_or_sl(self, current_price: float) -> bool:
        """评估止盈止损条件

        该方法检查当前价格是否触发止盈或止损条件：
        1. 检查加密货币余额
        2. 评估止盈条件
        3. 评估止损条件

        参数:
            current_price: float - 当前市场价格

        返回:
            bool: 如果触发了任一条件返回True，否则返回False
        """
        if self.balance_tracker.crypto_balance == 0:
            self.logger.debug("No crypto balance available; skipping TP/SL checks.")
            return False

        if await self._handle_take_profit(current_price):
            return True
        if await self._handle_stop_loss(current_price):
            return True
        return False

    async def _handle_take_profit(self, current_price: float) -> bool:
        """处理止盈逻辑

        该方法实现止盈功能：
        1. 检查止盈功能是否启用
        2. 比较当前价格与止盈阈值
        3. 触发止盈时执行市价卖单

        参数:
            current_price: float - 当前市场价格

        返回:
            bool: 是否触发了止盈操作
        """
        if self.config_manager.is_take_profit_enabled() and current_price >= self.config_manager.get_take_profit_threshold():
            self.logger.info(f"Take-profit triggered at {current_price}. Executing TP order...")
            await self.order_manager.execute_take_profit_or_stop_loss_order(current_price=current_price, take_profit_order=True)
            return True
        return False

    async def _handle_stop_loss(self, current_price: float) -> bool:
        """处理止损逻辑

        该方法实现止损功能：
        1. 检查止损功能是否启用
        2. 比较当前价格与止损阈值
        3. 触发止损时执行市价卖单

        参数:
            current_price: float - 当前市场价格

        返回:
            bool: 是否触发了止损操作
        """
        if self.config_manager.is_stop_loss_enabled() and current_price <= self.config_manager.get_stop_loss_threshold():
            self.logger.info(f"Stop-loss triggered at {current_price}. Executing SL order...")
            await self.order_manager.execute_take_profit_or_stop_loss_order(current_price=current_price, stop_loss_order=True)
            return True
        return False
    
    def get_formatted_orders(self):
        """获取格式化的订单记录

        该方法返回所有订单的格式化摘要：
        1. 订单类型(买入/卖出)
        2. 价格和数量
        3. 执行时间
        4. 订单状态
        5. 网格层级信息

        返回:
            list: 包含所有订单详细信息的列表
        """
        return self.trading_performance_analyzer.get_formatted_orders()