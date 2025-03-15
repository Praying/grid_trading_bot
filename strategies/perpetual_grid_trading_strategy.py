import logging
from typing import Optional, Tuple
import pandas as pd
import numpy as np

from .perpetual_trading_performance_analyzer import PerpetualTradingPerformanceAnalyzer
from .trading_strategy_interface import TradingStrategyInterface
from config.trading_mode import TradingMode
from core.bot_management.event_bus import EventBus, Events
from config.config_manager import ConfigManager
from core.services.perpetual_exchange_service import PerpetualExchangeService
from core.grid_management.perpetual_grid_manager import PerpetualGridManager
from core.order_handling.perpetual_order_manager import PerpetualOrderManager
from core.order_handling.perpetual_balance_tracker import PerpetualBalanceTracker
from strategies.trading_performance_analyzer import TradingPerformanceAnalyzer
from strategies.plotter import Plotter

class PerpetualGridTradingStrategy(TradingStrategyInterface):
    def __init__(
        self,
        config_manager: ConfigManager,
        event_bus: EventBus,
        exchange_service: PerpetualExchangeService,
        grid_manager: PerpetualGridManager,
        order_manager: PerpetualOrderManager,
        balance_tracker: PerpetualBalanceTracker,
        trading_performance_analyzer: PerpetualTradingPerformanceAnalyzer,
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
        # 订阅合约特有的事件
        self.event_bus.subscribe(Events.FUNDING_FEE_SETTLED, self._on_funding_fee_settled)
        self.event_bus.subscribe(Events.MARGIN_CALL, self._on_margin_call)
        self.event_bus.subscribe(Events.POSITION_UPDATED, self._on_position_updated)
        self.funding_rates = []
    
    async def _on_funding_fee_settled(self, fee_data: dict) -> None:
        """处理资金费用结算事件

        参数:
            fee_data: dict - 包含资金费率和结算金额的字典
        """
        self.funding_rates.append((pd.Timestamp.now(), fee_data['rate'], fee_data['amount']))
        self.logger.info(f"Funding fee settled: {fee_data}")
        # 根据资金费率调整策略
        await self._adjust_strategy_by_funding_rate(fee_data['rate'])
    
    async def _on_margin_call(self, margin_data: dict) -> None:
        """处理保证金追加通知事件

        参数:
            margin_data: dict - 包含保证金率和所需追加金额的字典
        """
        self.logger.warning(f"Margin call received: {margin_data}")
        # 尝试自动追加保证金
        await self._handle_margin_call(margin_data)
    
    async def _on_position_updated(self, position_data: dict) -> None:
        """处理仓位更新事件

        参数:
            position_data: dict - 包含仓位信息的字典
        """
        self.logger.info(f"Position updated: {position_data}")
        # 更新风险管理参数
        await self._update_risk_parameters(position_data)
    
    async def _adjust_strategy_by_funding_rate(self, funding_rate: float) -> None:
        """根据资金费率调整策略

        参数:
            funding_rate: float - 当前资金费率
        """
        # 如果资金费率过高，考虑减少对应方向的仓位
        threshold = self.config_manager.get_funding_rate_threshold()
        if abs(funding_rate) > threshold:
            if funding_rate > 0:
                # 减少多仓
                await self._reduce_long_exposure()
            else:
                # 减少空仓
                await self._reduce_short_exposure()
    
    async def _handle_margin_call(self, margin_data: dict) -> None:
        """处理保证金追加

        参数:
            margin_data: dict - 保证金相关数据
        """
        required_margin = margin_data.get('required_margin', 0)
        current_margin = margin_data.get('current_margin', 0)
        
        if required_margin > current_margin:
            # 计算需要追加的保证金
            margin_to_add = required_margin - current_margin
            # 检查是否有足够的可用余额
            if await self.balance_tracker.has_sufficient_balance(margin_to_add):
                # 追加保证金
                await self.balance_tracker.add_margin(margin_to_add)
                self.logger.info(f"Added margin: {margin_to_add}")
            else:
                # 如果没有足够余额，可能需要减仓
                self.logger.warning("Insufficient balance for margin call, reducing position")
                await self._reduce_position_size()
    
    async def _update_risk_parameters(self, position_data: dict) -> None:
        """更新风险管理参数

        参数:
            position_data: dict - 仓位信息
        """
        # 更新止盈止损价格
        await self._update_tp_sl_prices(position_data)
        # 检查是否需要调整杠杆
        await self._check_and_adjust_leverage(position_data)
    
    async def _reduce_long_exposure(self) -> None:
        """减少多头敞口"""
        # 获取当前多头仓位
        long_positions = await self.balance_tracker.get_long_positions()
        for position in long_positions:
            # 计算需要减少的仓位大小
            reduction_size = position['size'] * 0.2  # 减少20%的仓位
            # 执行减仓操作
            await self.order_manager.reduce_position(
                position_id=position['id'],
                size=reduction_size,
                is_long=True
            )
    
    async def _reduce_short_exposure(self) -> None:
        """减少空头敞口"""
        # 获取当前空头仓位
        short_positions = await self.balance_tracker.get_short_positions()
        for position in short_positions:
            # 计算需要减少的仓位大小
            reduction_size = position['size'] * 0.2  # 减少20%的仓位
            # 执行减仓操作
            await self.order_manager.reduce_position(
                position_id=position['id'],
                size=reduction_size,
                is_long=False
            )
    
    async def _reduce_position_size(self) -> None:
        """减少整体仓位规模"""
        # 获取所有仓位
        positions = await self.balance_tracker.get_all_positions()
        for position in positions:
            # 计算需要减少的仓位大小
            reduction_size = position['size'] * 0.3  # 减少30%的仓位
            # 执行减仓操作
            await self.order_manager.reduce_position(
                position_id=position['id'],
                size=reduction_size,
                is_long=position['is_long']
            )
    
    async def _update_tp_sl_prices(self, position_data: dict) -> None:
        """更新止盈止损价格

        参数:
            position_data: dict - 仓位信息
        """
        # 获取当前维持保证金率
        margin_ratio = position_data.get('margin_ratio', 0)
        # 如果保证金率接近预警线，调整止损价格
        if margin_ratio < self.config_manager.get_margin_warning_threshold():
            # 获取更保守的止损价格
            new_sl_price = self._calculate_conservative_sl_price(position_data)
            # 更新止损订单
            await self.order_manager.update_stop_loss_order(
                position_id=position_data['id'],
                new_price=new_sl_price
            )
    
    async def _check_and_adjust_leverage(self, position_data: dict) -> None:
        """检查并调整杠杆率

        参数:
            position_data: dict - 仓位信息
        """
        current_leverage = position_data.get('leverage', 1)
        position_size = position_data.get('size', 0)
        # 如果仓位较大且杠杆较高，考虑降低杠杆
        if position_size > self.config_manager.get_large_position_threshold() and \
           current_leverage > self.config_manager.get_max_safe_leverage():
            new_leverage = current_leverage * 0.8  # 降低20%的杠杆
            await self.order_manager.adjust_leverage(
                position_id=position_data['id'],
                new_leverage=new_leverage
            )
    
    def _calculate_conservative_sl_price(self, position_data: dict) -> float:
        """计算更保守的止损价格

        参数:
            position_data: dict - 仓位信息

        返回:
            float: 新的止损价格
        """
        entry_price = position_data.get('entry_price', 0)
        current_price = position_data.get('mark_price', 0)
        is_long = position_data.get('is_long', True)
        
        # 计算当前盈亏
        pnl_percent = ((current_price - entry_price) / entry_price) * (1 if is_long else -1)
        
        # 根据盈亏情况设置不同的止损比例
        if pnl_percent > 0.05:  # 盈利超过5%
            sl_percent = 0.03  # 设置3%的止损
        else:
            sl_percent = 0.05  # 设置5%的止损
        
        # 计算新的止损价格
        if is_long:
            return current_price * (1 - sl_percent)
        else:
            return current_price * (1 + sl_percent)
    
    def _initialize_historical_data(self) -> Optional[pd.DataFrame]:
        """初始化历史市场数据（开高低收成交量）
        在实盘或模拟交易模式下返回None
        """
        if self.trading_mode != TradingMode.BACKTEST:
            return None

        try:
            timeframe = self.config_manager.get_timeframe()
            start_date = self.config_manager.get_start_date()
            end_date = self.config_manager.get_end_date()
            return self.exchange_service.fetch_ohlcv(self.trading_pair, timeframe, start_date, end_date)
        except Exception as e:
            self.logger.error(f"Failed to initialize data for backtest trading mode: {e}")
            return None

    def initialize_strategy(self):
        """初始化交易策略，设置网格和价格层级"""
        self.grid_manager.initialize_grids_and_levels()
    
    async def stop(self):
        """停止交易执行，关闭连接"""
        self._running = False
        await self.exchange_service.close_connection()
        self.logger.info("Trading execution stopped.")

    async def restart(self):
        """重启交易会话"""
        if not self._running:
            self.logger.info("Restarting trading session.")
            await self.run()

    async def run(self):
        """运行交易策略"""
        self._running = True        
        #trigger_price = self.grid_manager.get_trigger_price()
        reversion_price = self.grid_manager.get_reversion_price()

        if self.trading_mode == TradingMode.BACKTEST:
            await self._run_backtest(reversion_price)
            self.logger.info("Ending backtest simulation")
            self._running = False
        else:
            await self._run_live_or_paper_trading(reversion_price)

    async def _run_live_or_paper_trading(self, reversion_price: float):
        """执行实盘或模拟交易"""
        self.logger.info(f"Starting {'live' if self.trading_mode == TradingMode.LIVE else 'paper'}  trading")
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
                    reversion_price,
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
                3  # ticker refresh interval in seconds
            )
        
        except Exception as e:
            self.logger.error(f"Error in live/paper trading loop: {e}", exc_info=True)
        
        finally:
            self.logger.info("Exiting live/paper trading loop.")

    async def _run_backtest(self, trigger_price: float) -> None:
        """执行回测模拟"""
        if self.data is None:
            self.logger.error("No data available for backtesting.")
            return

        self.logger.info("Starting backtest simulation")
        self.data['account_value'] = np.nan
        close_prices = self.data['close'].values
        high_prices = self.data['high'].values
        low_prices = self.data['low'].values
        timestamps = self.data.index
        self.data.loc[timestamps[0], 'account_value'] = self.balance_tracker.get_total_balance_value(price=close_prices[0])
        grid_orders_initialized = False
        last_price = None

        for i, (current_price, high_price, low_price, timestamp) in enumerate(zip(close_prices, high_prices, low_prices, timestamps)):
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
        reversion_price: float,
        grid_orders_initialized: bool,
        last_price: Optional[float] = None
    ) -> bool:
        """初始化网格订单"""
        if grid_orders_initialized:
            return True
        
        if last_price is None:
            self.logger.debug("No previous price recorded yet. Waiting for the next price update.")
            return False

        if current_price < reversion_price:
            self.logger.info(f"Current price {current_price} reached trigger price {reversion_price}. Will perform initial purhcase")
            await self.order_manager.perform_initial_purchase(current_price)
            self.logger.info(f"Initial purchase done, will initialize grid orders")
            await self.order_manager.initialize_grid_orders(current_price)
            return True
        # if last_price <= trigger_price <= current_price or last_price == trigger_price:
        #     self.logger.info(f"Current price {current_price} reached trigger price {trigger_price}. Will perform initial purhcase")
        #     await self.order_manager.perform_initial_purchase(current_price)
        #     self.logger.info(f"Initial purchase done, will initialize grid orders")
        #     await self.order_manager.initialize_grid_orders(current_price)
        #     return True

        self.logger.info(f"Current price {current_price} did not cross trigger price {trigger_price}. Last price: {last_price}.")
        return False

    async def _handle_take_profit_stop_loss(self, current_price: float) -> bool:
        """处理止盈止损"""
        tp_or_sl_triggered = await self._evaluate_tp_or_sl(current_price)
        if tp_or_sl_triggered:
            self.logger.info("Take-profit or stop-loss triggered, ending trading session.")
            await self.event_bus.publish(Events.STOP_BOT, "TP or SL hit.")
            return True
        return False

    def generate_performance_report(self) -> Tuple[dict, list]:
        """生成永续合约交易的性能报告

        返回:
            tuple: 包含性能指标摘要的字典和格式化订单详情的列表
        """
        if self.trading_mode == TradingMode.BACKTEST:
            initial_price = self.data['close'].values[0]
            final_price = self.data['close'].values[-1]
            performance_summary = self.trading_performance_analyzer.generate_performance_summary(
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

            performance_summary = self.trading_performance_analyzer.generate_performance_summary(
                live_data, 
                initial_price,
                self.balance_tracker.get_adjusted_fiat_balance(), 
                self.balance_tracker.get_adjusted_crypto_balance(), 
                final_price,
                self.balance_tracker.total_fees
            )
        
        # 添加合约特有的性能指标
        if self.trading_mode == TradingMode.BACKTEST:
            # 计算资金费用统计
            total_funding_fees = sum(fee for _, _, fee in self.funding_rates)
            avg_funding_rate = np.mean([rate for _, rate, _ in self.funding_rates]) if self.funding_rates else 0
            
            # 获取杠杆使用情况
            max_leverage = self.balance_tracker.get_max_leverage_used()
            avg_leverage = self.balance_tracker.get_average_leverage_used()
            
            # 获取保证金使用情况
            margin_usage_ratio = self.balance_tracker.get_margin_usage_ratio()
            min_margin_ratio = self.balance_tracker.get_minimum_margin_ratio()
            
            # 添加到性能报告
            performance_summary.update({
                "Total Funding Fees": f"{total_funding_fees:.2f}",
                "Average Funding Rate": f"{avg_funding_rate:.4%}",
                "Maximum Leverage Used": f"{max_leverage:.2f}x",
                "Average Leverage Used": f"{avg_leverage:.2f}x",
                "Margin Usage Ratio": f"{margin_usage_ratio:.2%}",
                "Minimum Margin Ratio": f"{min_margin_ratio:.2%}"
            })
        
        formatted_orders = self.trading_performance_analyzer.get_formatted_orders()
        return performance_summary, formatted_orders

    def plot_results(self) -> None:
        """绘制回测结果图表"""
        if self.trading_mode == TradingMode.BACKTEST:
            self.plotter.plot_results(self.data)
        else:
            self.logger.info("Plotting is not available for live/paper trading mode.")

    def get_formatted_orders(self):
        """获取格式化的订单记录"""
        return self.trading_performance_analyzer.get_formatted_orders()

    async def _evaluate_tp_or_sl(self, current_price)-> bool:
        return False