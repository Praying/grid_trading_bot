import logging
from typing import Any, Dict, List, Tuple, Union, Optional
import pandas as pd
import numpy as np
from tabulate import tabulate
from config.config_manager import ConfigManager
from core.order_handling.order_book import OrderBook
from core.grid_management.grid_level import GridLevel
from core.order_handling.order import Order
from core.order_handling.perpetual_order_book import PerpetualOrderBook

ANNUAL_RISK_FREE_RATE = 0.03  # annual risk free rate 3%
# 年化无风险利率 3%

class PerpetualTradingPerformanceAnalyzer:
    def __init__(
        self, 
        config_manager: ConfigManager, 
        order_book: PerpetualOrderBook
    ):
        """初始化交易性能分析器

        参数:
            config_manager: 配置管理器实例
            order_book: 订单簿实例
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_manager: ConfigManager = config_manager
        self.order_book: PerpetualOrderBook = order_book
        self.base_currency, self.quote_currency, self.trading_fee = self._extract_config()
    
    def _extract_config(self) -> Tuple[str, str, float]:
        """
        Extract trading-related configuration values.

        Returns:
            Tuple[str, str, float]: Base currency, quote currency, and trading fee.
        
        提取交易相关的配置值。

        返回:
            Tuple[str, str, float]: 基础货币、计价货币和交易费用。
        """
        base_currency = self.config_manager.get_base_currency()
        quote_currency = self.config_manager.get_quote_currency()
        trading_fee = self.config_manager.get_trading_fee()
        return base_currency, quote_currency, trading_fee
    
    def _calculate_roi(
        self, 
        initial_balance: float, 
        final_balance: float
    ) -> float:
        """
        Calculate the return on investment (ROI) percentage.

        Args:
            Initial_balance (float): The initial account balance.
            final_balance (float): The final account balance.

        Returns:
            float: The calculated ROI percentage.
        
        计算投资回报率(ROI)百分比。

        参数:
            Initial_balance (float): 初始账户余额
            final_balance (float): 最终账户余额

        返回:
            float: 计算得出的ROI百分比
        """
        roi = (final_balance - initial_balance) / initial_balance * 100
        return round(roi, 2)
    
    def _calculate_trading_gains(self) -> str:
        """
        Calculates the total trading gains from completed buy and sell orders.

        The computation uses only closed orders to determine the net profit or loss 
        from executed trades.

        Returns:
            str: The total grid trading gains as a formatted string, or "N/A" if there are no sell orders.
        
        计算已完成买入和卖出订单的总交易收益。

        计算仅使用已关闭的订单来确定已执行交易的净利润或损失。

        返回：
            str: 网格交易总收益的格式化字符串，如果没有卖出订单则返回"N/A"。
        """
        total_buy_cost = 0.0  # 总买入成本
        total_sell_revenue = 0.0  # 总卖出收入
        # 获取所有已成交的买入订单
        closed_buy_orders = [order for order in self.order_book.get_all_buy_orders() if order.is_filled()]
        # 获取所有已成交的卖出订单
        closed_sell_orders = [order for order in self.order_book.get_all_sell_orders() if order.is_filled()]

        # 计算买入订单的总成本（包括交易费用）
        for buy_order in closed_buy_orders:
            trade_value = buy_order.amount * buy_order.price
            buy_fee = buy_order.fee.get('cost', 0.0) if buy_order.fee else 0.0
            total_buy_cost += trade_value + buy_fee

        # 计算卖出订单的总收入（扣除交易费用）
        for sell_order in closed_sell_orders:
            trade_value = sell_order.amount * sell_order.price
            sell_fee = sell_order.fee.get('cost', 0.0) if sell_order.fee else 0.0
            total_sell_revenue += trade_value - sell_fee
        
        # 如果没有卖出订单返回"N/A"，否则返回总收益
        return "N/A" if total_sell_revenue == 0 else f"{total_sell_revenue - total_buy_cost:.2f}"

    def _calculate_drawdown(self, data: pd.DataFrame) -> float:
        """计算最大回撤百分比

        参数:
            data: 包含账户价值数据的DataFrame

        返回:
            最大回撤百分比
        """
        peak = data['account_value'].expanding(min_periods=1).max()
        drawdown = (peak - data['account_value']) / peak * 100
        max_drawdown = drawdown.max()
        return max_drawdown

    def _calculate_runup(self, data: pd.DataFrame) -> float:
        """计算最大涨幅百分比

        参数:
            data: 包含账户价值数据的DataFrame

        返回:
            最大涨幅百分比
        """
        trough = data['account_value'].expanding(min_periods=1).min()
        runup = (data['account_value'] - trough) / trough * 100
        max_runup = runup.max()
        return max_runup

    def _calculate_time_in_profit_loss(
        self, 
        initial_balance: float, 
        data: pd.DataFrame
    ) -> Tuple[float, float]:
        """计算盈利和亏损时间的百分比

        参数:
            initial_balance: 初始余额
            data: 包含账户价值数据的DataFrame

        返回:
            Tuple[float, float]: 盈利时间百分比和亏损时间百分比
        """
        time_in_profit = (data['account_value'] > initial_balance).mean() * 100
        time_in_loss = (data['account_value'] <= initial_balance).mean() * 100
        return time_in_profit, time_in_loss
    
    def _calculate_sharpe_ratio(self, data: pd.DataFrame) -> float:
        """
        Calculate the Sharpe ratio based on the account value.

        Args:
            data (pd.DataFrame): Historical account value data.

        Returns:
            float: The Sharpe ratio.
        
        基于账户价值计算夏普比率。

        参数:
            data (pd.DataFrame): 历史账户价值数据

        返回:
            float: 夏普比率
        """
        returns = data['account_value'].pct_change(fill_method=None)
        excess_returns = returns - ANNUAL_RISK_FREE_RATE / 252 # Adjusted daily
        std_dev = excess_returns.std()
        if std_dev == 0:
            return 0.0
        sharpe_ratio = excess_returns.mean() / std_dev * np.sqrt(252)
        return round(sharpe_ratio, 2)
    
    def _calculate_sortino_ratio(self, data: pd.DataFrame) -> float:
        """
        Calculate the Sortino ratio based on the account value.

        Args:
            data (pd.DataFrame): Historical account value data.

        Returns:
            float: The Sortino ratio.
        
        基于账户价值计算索提诺比率。

        参数:
            data (pd.DataFrame): 历史账户价值数据

        返回:
            float: 索提诺比率
        """
        returns = data['account_value'].pct_change(fill_method=None)
        excess_returns = returns - ANNUAL_RISK_FREE_RATE / 252  # Adjusted daily
        downside_returns = excess_returns[excess_returns < 0]
        
        if len(downside_returns) == 0 or downside_returns.std() == 0:
            return round(excess_returns.mean() * np.sqrt(252), 2)  # Positive ratio if no downside
        
        sortino_ratio = excess_returns.mean() / downside_returns.std() * np.sqrt(252)
        return round(sortino_ratio, 2)

    def get_formatted_orders(self) -> List[List[Union[str, float]]]:
        """
        Retrieve a formatted list of filled buy and sell orders.

        Returns:
            List[List[Union[str, float]]]: Formatted orders with details like side, type, status, price, quantity, timestamp, etc.
        
        获取已成交买入和卖出订单的格式化列表。

        返回：
            List[List[Union[str, float]]]: 格式化的订单列表，包含订单方向、类型、状态、价格、数量、时间戳等详细信息。
        """
        orders = []  # 存储格式化的订单列表
        # 获取带网格级别的买入订单
        buy_orders_with_grid = self.order_book.get_buy_orders_with_grid()
        # 获取带网格级别的卖出订单
        sell_orders_with_grid = self.order_book.get_sell_orders_with_grid()

        # 处理已成交的买入订单
        for buy_order, grid_level in buy_orders_with_grid:
            if buy_order.is_filled():
                orders.append(self._format_order(buy_order, grid_level))

        # 处理已成交的卖出订单
        for sell_order, grid_level in sell_orders_with_grid:
            if sell_order.is_filled():
                orders.append(self._format_order(sell_order, grid_level))
        
        # 按时间戳排序，将None值排在最后
        orders.sort(key=lambda x: (x[5] is None, x[5]))  # x[5] is the timestamp, sort None to the end
        return orders
    
    def _format_order(self, order: Order, grid_level: Optional[GridLevel]) -> List[Union[str, float]]:
        grid_level_price = grid_level.price if grid_level else "N/A"
        if grid_level and order.average is not None:
            # Assuming order.price is the execution price and grid level price the expected price
            slippage = ((order.average - grid_level_price) / grid_level_price) * 100
            slippage_str = f"{slippage:.2f}%"
        else:
            slippage = "N/A"
            slippage_str = "N/A"
        return [
            order.side.name,
            order.order_type.name,
            order.status.name,
            order.price, 
            order.filled, 
            order.format_last_trade_timestamp(), 
            grid_level_price, 
            slippage_str
        ]
    
    def _calculate_trade_counts(self) -> Tuple[int, int]:
        """
        Count the number of filled buy and sell orders.

        Returns:
            Tuple[int, int]: Number of buy trades and number of sell trades.
        
        统计已成交的买入和卖出订单数量。

        返回:
            Tuple[int, int]: 买入交易数量和卖出交易数量。
        """
        num_buy_trades = len([order for order in self.order_book.get_all_buy_orders() if order.is_filled()])
        num_sell_trades = len([order for order in self.order_book.get_all_sell_orders() if order.is_filled()])
        return num_buy_trades, num_sell_trades
    
    def _calculate_buy_and_hold_return(
        self, 
        data: pd.DataFrame, 
        initial_price: float,
        final_price: float
    ) -> float:
        """
        Calculate the buy-and-hold return percentage.

        Args:
            data (pd.DataFrame): Historical price data.
            initial_price (float): The initial cryptocurrency price.
            final_price (float): The final cryptocurrency price.

        Returns:
            float: The buy-and-hold return percentage.
        
        计算买入并持有策略的收益率百分比。

        参数:
            data (pd.DataFrame): 历史价格数据
            initial_price (float): 初始加密货币价格
            final_price (float): 最终加密货币价格

        返回:
            float: 买入并持有策略的收益率百分比
        """
        return ((final_price - initial_price) / initial_price) * 100

    def generate_performance_summary(
        self, 
        data: pd.DataFrame, 
        initial_price: float,
        final_fiat_balance: float, 
        final_crypto_balance: float, 
        final_crypto_price: float, 
        total_fees: float
    ) -> Tuple[Dict[str, Any], List[List[Union[str, float]]]]:
        """
        Generate a detailed performance summary for the trading session.

        Args:
            data (pd.DataFrame): Account value and price data.
            final_fiat_balance (float): Final fiat currency balance.
            final_crypto_balance (float): Final cryptocurrency balance.
            final_crypto_price (float): Final cryptocurrency price.
            total_fees (float): Total trading fees incurred.

        Returns:
            Tuple[Dict[str, Any], List[List[Union[str, float]]]]: A dictionary of performance metrics and a list of formatted orders.
        
        生成交易会话的详细表现总结。

        参数：
            data (pd.DataFrame): 账户价值和价格数据
            final_fiat_balance (float): 最终法币余额
            final_crypto_balance (float): 最终加密货币余额
            final_crypto_price (float): 最终加密货币价格
            total_fees (float): 产生的总交易费用

        返回：
            Tuple[Dict[str, Any], List[List[Union[str, float]]]]: 包含表现指标的字典和格式化订单列表的元组
        """
        # 计算基本信息
        pair = f"{self.base_currency}/{self.quote_currency}"  # 交易对
        start_date = data.index[0]  # 开始日期
        end_date = data.index[-1]  # 结束日期
        initial_balance = data["account_value"].iloc[0]  # 初始余额
        duration = end_date - start_date  # 交易持续时间
        
        # 计算最终资产价值
        final_crypto_value = final_crypto_balance * final_crypto_price  # 最终加密货币价值
        final_balance = final_fiat_balance + final_crypto_value  # 最终总资产价值
        
        # 计算各项表现指标
        roi = self._calculate_roi(initial_balance, final_balance)  # 投资回报率
        grid_trading_gains = self._calculate_trading_gains()  # 网格交易收益
        max_drawdown = self._calculate_drawdown(data)  # 最大回撤
        max_runup = self._calculate_runup(data)  # 最大涨幅
        time_in_profit, time_in_loss = self._calculate_time_in_profit_loss(initial_balance, data)  # 盈利和亏损时间占比
        sharpe_ratio = self._calculate_sharpe_ratio(data)  # 夏普比率
        sortino_ratio = self._calculate_sortino_ratio(data)  # 索提诺比率
        buy_and_hold_return = self._calculate_buy_and_hold_return(data, initial_price, final_crypto_price)  # 买入持有收益率
        num_buy_trades, num_sell_trades = self._calculate_trade_counts()  # 买入和卖出交易次数
        
        # 构建表现总结字典
        performance_summary = {
            "Pair": pair,
            "Start Date": start_date,
            "End Date": end_date,
            "Duration": duration,
            "ROI": f"{roi:.2f}%",
            "Max Drawdown": f"{max_drawdown:.2f}%",
            "Max Runup": f"{max_runup:.2f}%",
            "Time in Profit %": f"{time_in_profit:.2f}%",
            "Time in Loss %": f"{time_in_loss:.2f}%",
            "Buy and Hold Return %": f"{buy_and_hold_return:.2f}%",
            "Grid Trading Gains": f"{grid_trading_gains}",
            "Total Fees": f"{total_fees:.2f}",
            "Final Balance (Fiat)": f"{final_balance:.2f}",
            "Final Crypto Balance": f"{final_crypto_balance:.4f} {self.base_currency}",
            "Final Crypto Value (Fiat)": f"{final_crypto_value:.2f} {self.quote_currency}",
            "Remaining Fiat Balance": f"{final_fiat_balance:.2f} {self.quote_currency}",
            "Number of Buy Trades": num_buy_trades,
            "Number of Sell Trades": num_sell_trades,
            "Sharpe Ratio": f"{sharpe_ratio:.2f}",
            "Sortino Ratio": f"{sortino_ratio:.2f}"
        }

        # 获取格式化的订单列表
        formatted_orders = self.get_formatted_orders()

        # 生成订单表格并记录日志
        orders_table = tabulate(formatted_orders, headers=["Order Side", "Type", "Status", "Price", "Quantity", "Timestamp", "Grid Level", "Slippage"], tablefmt="pipe")
        self.logger.info("\nFormatted Orders:\n" + orders_table)

        # 生成总结表格并记录日志
        summary_table = tabulate(performance_summary.items(), headers=["Metric", "Value"], tablefmt="grid")
        self.logger.info("\nPerformance Summary:\n" + summary_table)

        return performance_summary, formatted_orders