import logging
from abc import ABC, abstractmethod
from typing import Tuple

class TradingStrategyInterface(ABC):
    """
    Abstract base class for all trading strategies.
    Requires implementation of key methods for any concrete strategy.

    所有交易策略的抽象基类。
    要求具体策略必须实现关键方法。
    """
    def __init__(self, config_manager, balance_tracker):
        """
        Initializes the strategy with the given configuration manager and balance tracker.

        Args:
            config_manager: Provides access to the trading configuration (e.g., exchange, fees).
            balance_tracker: Tracks the balance and crypto balance for the strategy.
        
        使用给定的配置管理器和余额追踪器初始化策略。

        参数：
            config_manager: 提供对交易配置的访问（如交易所、手续费等）。
            balance_tracker: 追踪策略的法币和加密货币余额。
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_manager = config_manager
        self.balance_tracker = balance_tracker

    @abstractmethod
    def initialize_strategy(self):
        """
        Method to initialize the strategy with specific settings (grids, limits, etc.).
        Must be implemented by any subclass.

        使用特定设置（网格、限制等）初始化策略的方法。
        必须由任何子类实现。
        """
        pass

    @abstractmethod
    async def run(self):
        """
        Run the strategy with historical or live data.
        Must be implemented by any subclass.

        使用历史数据或实时数据运行策略。
        必须由任何子类实现。
        """
        pass

    @abstractmethod
    def plot_results(self):
        """
        Plots the strategy performance after simulation.
        Must be implemented by any subclass.

        模拟后绘制策略性能图表。
        必须由任何子类实现。
        """
        pass

    @abstractmethod
    def generate_performance_report(self) -> Tuple[dict, list]:
        """
        Generates a report summarizing the strategy's performance (ROI, max drawdown, etc.).
        Must be implemented by any subclass.

        生成总结策略性能的报告（投资回报率、最大回撤等）。
        必须由任何子类实现。
        """
        pass