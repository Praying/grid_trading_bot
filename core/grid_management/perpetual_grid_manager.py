import logging
from typing import List, Optional, Tuple
import numpy as np
from config.config_manager import ConfigManager
from strategies.strategy_type import StrategyType
from strategies.spacing_type import SpacingType
from .grid_level import GridLevel, GridCycleState
from ..order_handling.order import Order, OrderSide
from .grid_manager import GridManager

class PerpetualGridManager:
    def __init__(
        self, 
        config_manager: ConfigManager, 
        strategy_type: StrategyType,
        leverage: float = 1.0,  # 杠杆倍数
        margin_type: str = "isolated"  # 保证金模式：isolated(逐仓) 或 cross(全仓)
    ):
        self.config_manager = config_manager
        self.strategy_type = strategy_type
        self.leverage = leverage
        self.margin_type = margin_type
        self.long_positions: dict[float, float] = {}  # 多仓位管理：价格 -> 数量
        self.short_positions: dict[float, float] = {}  # 空仓位管理：价格 -> 数量
        self.funding_rates: List[float] = []  # 资金费率历史
        self.margin_ratio: float = 0.01  # 维持保证金率
        self.logger = logging.getLogger(self.__class__.__name__)
        self.price_grids: List[float] = []
        self.central_price: float = 0.0
        self.sorted_buy_grids: List[float] = []
        self.sorted_sell_grids: List[float] = []
        self.grid_levels: dict[float, GridLevel] = {}
        self.initialize_grids_and_levels()

    def get_order_size_for_grid_level(
        self,
        total_margin: float,  # 总可用保证金
        current_price: float,
        position_side: str = "long"  # 仓位方向：long或short
    ) -> float:
        """
        根据可用保证金、当前价格和杠杆计算合约数量。

        参数:
            total_margin: 可用保证金金额
            current_price: 当前价格
            position_side: 仓位方向

        返回:
            计算出的合约数量
        """
        # 计算该网格可分配的保证金
        margin_per_grid = total_margin / len(self.grid_levels)
        
        # 计算最大可开仓数量（考虑杠杆）
        max_position_size = (margin_per_grid * self.leverage) / current_price
        
        # 考虑维持保证金要求
        safe_position_size = max_position_size * (1 - self.margin_ratio)
        
        return safe_position_size

    def get_initial_order_quantity(
        self, 
        available_margin: float,
        current_positions: float,
        current_price: float,
        position_side: str = "long"
    ) -> float:
        """
        计算网格初始化时要开仓的合约数量。

        参数:
            available_margin: 可用保证金
            current_positions: 当前持仓数量
            current_price: 当前价格
            position_side: 仓位方向

        返回:
            要开仓的合约数量
        """
        # 计算目标仓位价值（基于可用保证金和杠杆）
        target_position_value = available_margin * self.leverage / 2
        
        # 计算当前仓位价值
        current_position_value = current_positions * current_price
        
        # 计算需要开仓的价值
        value_to_open = target_position_value - current_position_value
        
        # 确保不超过可用保证金限制
        max_value_to_open = available_margin * self.leverage
        value_to_open = min(value_to_open, max_value_to_open)
        
        # 转换为合约数量
        return max(0, value_to_open / current_price)

    def update_positions(
        self,
        price: float,
        quantity: float,
        position_side: str
    ) -> None:
        """
        更新仓位信息

        参数:
            price: 开仓/平仓价格
            quantity: 合约数量
            position_side: 仓位方向（'long'或'short'）
        """
        if position_side == "long":
            if price in self.long_positions:
                self.long_positions[price] += quantity
            else:
                self.long_positions[price] = quantity
        else:
            if price in self.short_positions:
                self.short_positions[price] += quantity
            else:
                self.short_positions[price] = quantity

    def calculate_funding_fee(
        self,
        position_value: float,
        funding_rate: float
    ) -> float:
        """
        计算资金费用

        参数:
            position_value: 仓位价值
            funding_rate: 资金费率

        返回:
            资金费用金额
        """
        return position_value * funding_rate

    def check_margin_safety(
        self,
        total_margin: float,
        total_position_value: float
    ) -> bool:
        """
        检查保证金安全性

        参数:
            total_margin: 总保证金
            total_position_value: 总仓位价值

        返回:
            是否安全（True/False）
        """
        # 计算当前保证金率
        current_margin_ratio = total_margin / total_position_value
        
        # 检查是否低于维持保证金率
        return current_margin_ratio >= self.margin_ratio

    def adjust_grid_spacing(
        self,
        base_spacing: float
    ) -> float:
        """
        根据杠杆调整网格间距

        参数:
            base_spacing: 基础网格间距

        返回:
            调整后的网格间距
        """
        # 随着杠杆增加，适当增加网格间距以控制风险
        return base_spacing * (1 + (self.leverage - 1) * 0.1)

    def initialize_grids_and_levels(self) -> None:
        """
        初始化网格级别并根据所选策略分配其各自的状态。

        对于 `SIMPLE_GRID` 策略：
        - 在低于中心价格的网格级别上放置买入订单。
        - 在高于中心价格的网格级别上放置卖出订单。
        - 级别初始化为 `READY_TO_BUY` 或 `READY_TO_SELL` 状态。

        对于 `HEDGED_GRID` 策略：
        - 网格级别分为买入级别（除顶部网格外）和卖出级别（除底部网格外）。
        - 买入网格级别初始化为 `READY_TO_BUY`，顶部网格除外。
        - 卖出网格级别初始化为 `READY_TO_SELL`。
        """
        # 计算网格价格和中心价格
        self.price_grids, self.central_price = self._calculate_price_grids_and_central_price()

        if self.strategy_type == StrategyType.SIMPLE_GRID:
            # 筛选出低于中心价格的买入网格
            self.sorted_buy_grids = [price_grid for price_grid in self.price_grids if price_grid <= self.central_price]
            # 筛选出高于中心价格的卖出网格
            self.sorted_sell_grids = [price_grid for price_grid in self.price_grids if price_grid > self.central_price]
            # 初始化网格级别状态，低于中心价格为 READY_TO_BUY，高于中心价格为 READY_TO_SELL
            self.grid_levels = {price: GridLevel(price, GridCycleState.READY_TO_BUY if price <= self.central_price else GridCycleState.READY_TO_SELL) for price in self.price_grids}
        
        elif self.strategy_type == StrategyType.HEDGED_GRID:
            # 买入网格为除顶部网格外的所有网格
            self.sorted_buy_grids = self.price_grids[:-1]  # 除顶部网格外
            # 卖出网格为除底部网格外的所有网格
            self.sorted_sell_grids = self.price_grids[1:]  # All except the bottom grid
            # 初始化网格级别状态，非顶部网格为 READY_TO_BUY_OR_SELL，顶部网格为 READY_TO_SELL
            self.grid_levels = {
                price: GridLevel(
                    price,
                    GridCycleState.READY_TO_BUY_OR_SELL if price != self.price_grids[-1] else GridCycleState.READY_TO_SELL
                )
                for price in self.price_grids
            }
        # 记录初始化信息
        self.logger.info(f"Grids and levels initialized. Central price: {self.central_price}")
        self.logger.info(f"Price grids: {self.price_grids}")
        self.logger.info(f"Buy grids: {self.sorted_buy_grids}")
        self.logger.info(f"Sell grids: {self.sorted_sell_grids}")
        self.logger.info(f"Grid levels: {self.grid_levels}")

    def _extract_grid_config(self) -> Tuple[float, float, int, SpacingType]:
        """
        从配置管理器中提取网格配置参数。
        """
        # 获取底部范围
        bottom_range = self.config_manager.get_bottom_range()
        # 获取顶部范围
        top_range = self.config_manager.get_top_range()
        # 获取网格数量
        num_grids = self.config_manager.get_num_grids()
        # 获取间距类型（例如 ARITHMETIC 或 GEOMETRIC）
        spacing_type = self.config_manager.get_spacing_type()
        return bottom_range, top_range, num_grids, spacing_type

    def _calculate_price_grids_and_central_price(self) -> Tuple[List[float], float]:
        """
        根据配置计算价格网格和中心价格，考虑合约特性。
        """
        bottom_range, top_range, num_grids, spacing_type = self._extract_grid_config()
        
        if spacing_type == SpacingType.ARITHMETIC:
            # 调整网格间距
            grid_spacing = (top_range - bottom_range) / (num_grids - 1)
            adjusted_spacing = self.adjust_grid_spacing(grid_spacing)
            
            # 重新计算网格价格
            grids = [bottom_range + i * adjusted_spacing for i in range(num_grids)]
            central_price = (top_range + bottom_range) / 2

        elif spacing_type == SpacingType.GEOMETRIC:
            ratio = (top_range / bottom_range) ** (1 / (num_grids - 1))
            # 调整比率以反映杠杆影响
            #adjusted_ratio = ratio * (1 + (self.leverage - 1) * 0.05)
            
            grids = []
            current_price = bottom_range
            for _ in range(num_grids):
                grids.append(current_price)
                current_price *= ratio
                #current_price *= adjusted_ratio

            central_index = len(grids) // 2
            if num_grids % 2 == 0:
                central_price = (grids[central_index - 1] + grids[central_index]) / 2
            else:
                central_price = grids[central_index]

        else:
            raise ValueError(f"不支持的间距类型: {spacing_type}")

        return grids, central_price

    def complete_order(
        self,
        grid_level: GridLevel,
        order_side: OrderSide,
        position_side: str
    ) -> None:
        """
        重写父类方法，处理合约订单完成后的状态转换

        参数:
            grid_level: 订单完成的网格级别
            order_side: 订单方向（买入/卖出）
            position_side: 仓位方向（多/空）
        """
        if self.strategy_type == StrategyType.SIMPLE_GRID:
            if order_side == OrderSide.BUY:  # 开多或平空
                if position_side == "long":
                    grid_level.state = GridCycleState.READY_TO_SELL
                    self.logger.info(f"开多仓完成，网格级别 {grid_level.price} 转换为 READY_TO_SELL")
                else:
                    grid_level.state = GridCycleState.READY_TO_BUY
                    self.logger.info(f"平空仓完成，网格级别 {grid_level.price} 转换为 READY_TO_BUY")
            
            elif order_side == OrderSide.SELL:  # 开空或平多
                if position_side == "short":
                    grid_level.state = GridCycleState.READY_TO_BUY
                    self.logger.info(f"开空仓完成，网格级别 {grid_level.price} 转换为 READY_TO_BUY")
                else:
                    grid_level.state = GridCycleState.READY_TO_SELL
                    self.logger.info(f"平多仓完成，网格级别 {grid_level.price} 转换为 READY_TO_SELL")

        elif self.strategy_type == StrategyType.HEDGED_GRID:
            if order_side == OrderSide.BUY:
                grid_level.state = GridCycleState.READY_TO_BUY_OR_SELL
                self.logger.info(f"合约订单完成，网格级别 {grid_level.price} 转换为 READY_TO_BUY_OR_SELL")

                if grid_level.paired_sell_level:
                    grid_level.paired_sell_level.state = GridCycleState.READY_TO_SELL
                    self.logger.info(f"配对的卖出网格级别 {grid_level.paired_sell_level.price} 转换为 READY_TO_SELL")

            elif order_side == OrderSide.SELL:
                grid_level.state = GridCycleState.READY_TO_BUY_OR_SELL
                self.logger.info(f"合约订单完成，网格级别 {grid_level.price} 转换为 READY_TO_BUY_OR_SELL")

                if grid_level.paired_buy_level:
                    grid_level.paired_buy_level.state = GridCycleState.READY_TO_BUY
                    self.logger.info(f"配对的买入网格级别 {grid_level.paired_buy_level.price} 转换为 READY_TO_BUY")

        else:
            self.logger.error("未知的策略类型")