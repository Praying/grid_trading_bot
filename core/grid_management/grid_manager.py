import logging
from typing import List, Optional, Tuple
import numpy as np
from config.config_manager import ConfigManager
from strategies.strategy_type import StrategyType
from strategies.spacing_type import SpacingType
from .grid_level import GridLevel, GridCycleState
from ..order_handling.order import Order, OrderSide

class GridManager:
    def __init__(
        self, 
        config_manager: ConfigManager, 
        strategy_type: StrategyType
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config_manager: ConfigManager = config_manager
        self.strategy_type: StrategyType = strategy_type
        self.price_grids: List[float]
        self.central_price: float
        self.sorted_buy_grids: List[float]
        self.sorted_sell_grids: List[float]
        self.grid_levels: dict[float, GridLevel] = {}
    
    def initialize_grids_and_levels(self) -> None:
        """
        Initializes the grid levels and assigns their respective states based on the chosen strategy.

        For the `SIMPLE_GRID` strategy:
        - Buy orders are placed on grid levels below the central price.
        - Sell orders are placed on grid levels above the central price.
        - Levels are initialized with `READY_TO_BUY` or `READY_TO_SELL` states.

        For the `HEDGED_GRID` strategy:
        - Grid levels are divided into buy levels (all except the top grid) and sell levels (all except the bottom grid).
        - Buy grid levels are initialized with `READY_TO_BUY`, except for the topmost grid.
        - Sell grid levels are initialized with `READY_TO_SELL`.
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
    
    def get_trigger_price(self) -> float:
        # 返回中心价格作为触发价格
        return self.central_price

    def get_order_size_for_grid_level(
        self,
        total_balance: float,
        current_price: float
    ) -> float:
        """
        根据总余额、网格总数和当前价格计算网格级别的订单大小。

        订单大小通过将总余额平均分配到所有网格级别并根据当前价格进行调整来确定。

        参数:
            current_price: 交易对的当前价格。

        返回:
            计算出的订单大小（浮点数）。
        """
        # 获取网格总数
        total_grids = len(self.grid_levels)
        # 计算订单大小：总余额 / 网格总数 / 当前价格
        order_size = total_balance / total_grids / current_price
        return order_size

    def get_initial_order_quantity(
        self, 
        current_fiat_balance: float, 
        current_crypto_balance: float,
        current_price: float
    ) -> float:
        """
        计算网格初始化时要购买的加密货币的初始数量。

        参数:
            current_fiat_balance (float): 当前法币余额。
            current_crypto_balance (float): 当前加密货币余额。
            current_price (float): 加密货币的当前市场价格。

        返回:
            float: 要购买的加密货币数量。
        """
        # 计算当前加密货币的法币价值
        current_crypto_value_in_fiat = current_crypto_balance * current_price
        # 计算总投资组合价值（法币 + 加密货币价值）
        total_portfolio_value = current_fiat_balance + current_crypto_value_in_fiat
        # 为初始购买分配 50% 的余额
        target_crypto_allocation_in_fiat = total_portfolio_value / 2 # Allocate 50% of balance for initial buy
        # 计算需要分配的法币金额
        fiat_to_allocate_for_purchase = target_crypto_allocation_in_fiat - current_crypto_value_in_fiat
        # 确保分配的法币金额在合理范围内（0 到当前法币余额之间）
        fiat_to_allocate_for_purchase = max(0, min(fiat_to_allocate_for_purchase, current_fiat_balance))
        # 计算要购买的加密货币数量
        return fiat_to_allocate_for_purchase / current_price

    def pair_grid_levels(
        self, 
        source_grid_level: GridLevel, 
        target_grid_level: GridLevel, 
        pairing_type: str
    ) -> None:
        """
        动态配对网格级别以进行买入或卖出。

        参数:
            source_grid_level: 发起配对的网格级别。
            target_grid_level: 被配对的网格级别。
            pairing_type: "buy" 或 "sell"，指定配对类型。
            """
        if pairing_type == "buy":
            # 将源网格级别的买入配对设置为目标网格级别
            source_grid_level.paired_buy_level = target_grid_level
            # 将目标网格级别的卖出配对设置为源网格级别
            target_grid_level.paired_sell_level = source_grid_level
            self.logger.info(f"Paired sell grid level {source_grid_level.price} with buy grid level {target_grid_level.price}.")
            
        elif pairing_type == "sell":
            # 将源网格级别的卖出配对设置为目标网格级别
            source_grid_level.paired_sell_level = target_grid_level
            # 将目标网格级别的买入配对设置为源网格级别
            target_grid_level.paired_buy_level = source_grid_level
            self.logger.info(f"Paired buy grid level {source_grid_level.price} with sell grid level {target_grid_level.price}.")

        else:
            raise ValueError(f"Invalid pairing type: {pairing_type}. Must be 'buy' or 'sell'.")
    
    def get_paired_sell_level(
        self, 
        buy_grid_level: GridLevel
    ) -> Optional[GridLevel]:
        """
        根据策略类型为给定的买入网格级别确定配对的卖出网格级别。

        参数:
            buy_grid_level: 需要配对卖出级别的买入网格级别。

        返回:
            配对的卖出网格级别，如果不存在则返回 None。
        """
        if self.strategy_type == StrategyType.SIMPLE_GRID:
            self.logger.info(f"Looking for paired sell level for buy level at {buy_grid_level}")
            self.logger.info(f"Available sell grids: {self.sorted_sell_grids}")
            
            for sell_price in self.sorted_sell_grids:
                sell_level = self.grid_levels[sell_price]
                self.logger.info(f"Checking sell level {sell_price}, state: {sell_level.state}")

                if sell_level and not self.can_place_order(sell_level, OrderSide.SELL):
                    self.logger.info(f"Skipping sell level {sell_price} - cannot place order. State: {sell_level.state}")
                    continue

                if sell_price > buy_grid_level.price:
                    self.logger.info(f"Paired sell level found at {sell_price} for buy level {buy_grid_level}.")
                    return sell_level

            self.logger.warning(f"No suitable sell level found above {buy_grid_level}")
            return None
    
        elif self.strategy_type == StrategyType.HEDGED_GRID:
            self.logger.info(f"Available price grids: {self.price_grids}")
            sorted_prices = sorted(self.price_grids)
            current_index = sorted_prices.index(buy_grid_level.price)
            self.logger.info(f"Current index of buy level {buy_grid_level.price}: {current_index}")

            if current_index + 1 < len(sorted_prices):
                paired_sell_price = sorted_prices[current_index + 1]
                sell_level = self.grid_levels[paired_sell_price]
                self.logger.info(f"Paired sell level for buy level {buy_grid_level.price} is at {paired_sell_price} (state: {sell_level.state})")
                return sell_level
        
            self.logger.warning(f"No suitable sell level found for buy grid level {buy_grid_level}")
            return None

        else:
            self.logger.error(f"Unsupported strategy type: {self.strategy_type}")
            return None
    
    def get_grid_level_below(self, grid_level: GridLevel) -> Optional[GridLevel]:
        """
        返回给定网格级别正下方的网格级别。

        参数:
            grid_level: 当前网格级别。

        返回:
            下方的网格级别，如果不存在则返回 None。
        """
        # 对网格价格进行排序
        sorted_levels = sorted(self.grid_levels.keys())
        # 获取当前网格级别的索引
        current_index = sorted_levels.index(grid_level.price)

        if current_index > 0:
            # 返回下方网格级别的价格
            lower_price = sorted_levels[current_index - 1]
            return self.grid_levels[lower_price]
        return None
    
    def mark_order_pending(
        self, 
        grid_level: GridLevel, 
        order: Order
    ) -> None:
        """
        将网格级别标记为有待定订单（买入或卖出）。

        参数:
            grid_level: 要更新的网格级别。
            order: 表示待定订单的 Order 对象。
            order_side: 订单的类型（买入或卖出）。
        """
        # 将订单添加到网格级别
        grid_level.add_order(order)
        # 更新状态为等待买入订单成交
        if order.side == OrderSide.BUY:
            # 更新状态为等待买入订单成交
            grid_level.state = GridCycleState.WAITING_FOR_BUY_FILL
            self.logger.info(f"Buy order placed and marked as pending at grid level {grid_level.price}.")
        elif order.side == OrderSide.SELL:
            # 更新状态为等待卖出订单成交
            grid_level.state = GridCycleState.WAITING_FOR_SELL_FILL
            self.logger.info(f"Sell order placed and marked as pending at grid level {grid_level.price}.")

    def complete_order(
        self, 
        grid_level: GridLevel, 
        order_side: OrderSide
    ) -> None:
        """
        标记订单（买入或卖出）完成并转换网格级别状态。

        参数:
            grid_level: 订单完成的网格级别。
            order_side: 完成订单的类型（买入或卖出）。
        """
        if self.strategy_type == StrategyType.SIMPLE_GRID:
            if order_side == OrderSide.BUY:
                # 买入订单完成后，状态转换为 READY_TO_SELL
                grid_level.state = GridCycleState.READY_TO_SELL
                self.logger.info(f"Buy order completed at grid level {grid_level.price}. Transitioning to READY_TO_SELL.")
            elif order_side == OrderSide.SELL:
                # 卖出订单完成后，状态转换为 READY_TO_BUY
                grid_level.state = GridCycleState.READY_TO_BUY
                self.logger.info(f"Sell order completed at grid level {grid_level.price}. Transitioning to READY_TO_BUY.")
        
        elif self.strategy_type == StrategyType.HEDGED_GRID:
            if order_side == OrderSide.BUY:
                # 买入订单完成后，状态转换为 READY_TO_BUY_OR_SELL
                grid_level.state = GridCycleState.READY_TO_BUY_OR_SELL
                self.logger.info(f"Buy order completed at grid level {grid_level.price}. Transitioning to READY_TO_BUY_OR_SELL.")

                # 将配对的买入级别转换为 "READY_TO_SELL"
                if grid_level.paired_sell_level:
                    grid_level.paired_sell_level.state = GridCycleState.READY_TO_SELL
                    self.logger.info(f"Paired sell grid level {grid_level.paired_sell_level.price} transitioned to READY_TO_SELL.")

            elif order_side == OrderSide.SELL:
                # 卖出订单完成后，状态转换为 READY_TO_BUY_OR_SELL
                grid_level.state = GridCycleState.READY_TO_BUY_OR_SELL
                self.logger.info(f"Sell order completed at grid level {grid_level.price}. Transitioning to READY_TO_BUY_OR_SELL.")

                # 将配对的卖出级别转换为 "READY_TO_BUY"
                if grid_level.paired_buy_level:
                    grid_level.paired_buy_level.state = GridCycleState.READY_TO_BUY
                    self.logger.info(f"Paired buy grid level {grid_level.paired_buy_level.price} transitioned to READY_TO_BUY.")

        else:
            self.logger.error("Unexpected strategy type")

    def can_place_order(
        self, 
        grid_level: GridLevel, 
        order_side: OrderSide, 
    ) -> bool:
        """
        确定是否可以在给定的网格级别上放置订单。

        参数:
            grid_level: 被评估的网格级别。
            order_side: 订单的类型（买入或卖出）。

        返回:
            bool: 如果可以放置订单则为 True，否则为 False。
        """
        if self.strategy_type == StrategyType.SIMPLE_GRID:
            # 对于 SIMPLE_GRID 策略，买入订单要求状态为 READY_TO_BUY
            if order_side == OrderSide.BUY:
                return grid_level.state == GridCycleState.READY_TO_BUY
            elif order_side == OrderSide.SELL:
                # 对于 SIMPLE_GRID 策略，卖出订单要求状态为 READY_TO_SELL
                return grid_level.state == GridCycleState.READY_TO_SELL

        elif self.strategy_type == StrategyType.HEDGED_GRID:
            if order_side == OrderSide.BUY:
                # 对于 HEDGED_GRID 策略，买入订单要求状态为 READY_TO_BUY 或 READY_TO_BUY_OR_SELL
                return grid_level.state in {GridCycleState.READY_TO_BUY, GridCycleState.READY_TO_BUY_OR_SELL}
            elif order_side == OrderSide.SELL:
                # 对于 HEDGED_GRID 策略，卖出订单要求状态为 READY_TO_SELL 或 READY_TO_BUY_OR_SELL
                return grid_level.state in {GridCycleState.READY_TO_SELL, GridCycleState.READY_TO_BUY_OR_SELL}

        else:
            return False

    def _extract_grid_config(self) -> Tuple[float, float, int, str]:
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
        根据配置计算价格网格和中心价格。

        返回:
            Tuple[List[float], float]: 包含以下内容的元组：
                - grids (List[float]): 计算出的网格价格列表。
                - central_price (float): 网格的中心价格。
        """
        # 提取网格配置参数
        bottom_range, top_range, num_grids, spacing_type = self._extract_grid_config()
        
        if spacing_type == SpacingType.ARITHMETIC:
            # 使用等差数列生成网格价格
            grids = np.linspace(bottom_range, top_range, num_grids)
            # 计算中心价格为顶部和底部范围的平均值
            central_price = (top_range + bottom_range) / 2

        elif spacing_type == SpacingType.GEOMETRIC:
            grids = []
            # 计算等比数列的比率
            ratio = (top_range / bottom_range) ** (1 / (num_grids - 1))
            current_price = bottom_range
            # 生成等比数列的网格价格
            for _ in range(num_grids):
                grids.append(current_price)
                current_price *= ratio
                
            central_index = len(grids) // 2
            if num_grids % 2 == 0:
                # 如果网格数量为偶数，中心价格为中间两个网格的平均值
                central_price = (grids[central_index - 1] + grids[central_index]) / 2
            else:
                # 如果网格数量为奇数，中心价格为中间网格的价格
                central_price = grids[central_index]

        else:
            raise ValueError(f"Unsupported spacing type: {spacing_type}")

        return grids, central_price
