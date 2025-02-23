from enum import Enum
from typing import List, Optional
from ..order_handling.order import Order

class GridCycleState(Enum):
    """
    网格交易中的网格级别状态枚举类

    定义了网格级别在交易过程中可能处于的不同状态：
    - 可以进行买入或卖出
    - 准备买入
    - 等待买入订单成交
    - 准备卖出
    - 等待卖出订单成交
    """
    READY_TO_BUY_OR_SELL = "ready_to_buy_or_sell"  # 网格级别可以进行买入或卖出操作
    READY_TO_BUY = "ready_to_buy"                 # 网格级别准备进行买入操作
    WAITING_FOR_BUY_FILL = "waiting_for_buy_fill"  # 买入订单已下，等待成交
    READY_TO_SELL = "ready_to_sell"               # 网格级别准备进行卖出操作
    WAITING_FOR_SELL_FILL = "waiting_for_sell_fill"  # 卖出订单已下，等待成交

class GridLevel:
    """
    网格交易中的网格级别类

    用于管理单个网格级别的状态、订单和配对关系。每个网格级别都有一个特定的价格，
    可以处于不同的交易状态（买入/卖出），并且可以与其他网格级别建立配对关系。
    """
    def __init__(self, price: float, state: GridCycleState):
        """
        初始化网格级别

        参数:
            price: 网格级别的价格
            state: 网格级别的初始状态
        """
        self.price: float = price                    # 网格级别的价格
        self.orders: List[Order] = []               # 该网格级别的所有订单记录
        self.state: GridCycleState = state          # 网格级别的当前状态
        self.paired_buy_level: Optional['GridLevel'] = None   # 配对的买入网格级别
        self.paired_sell_level: Optional['GridLevel'] = None  # 配对的卖出网格级别
    
    def add_order(self, order: Order) -> None:
        """
        在当前网格级别记录一个新订单

        参数:
            order: 要记录的订单对象
        """
        self.orders.append(order)

    def __str__(self) -> str:
        """
        返回网格级别的字符串表示

        返回值:
            包含网格级别详细信息的字符串，包括价格、状态、订单数量和配对级别信息
        """
        return (
            f"GridLevel(price={self.price}, "
            f"state={self.state.name}, "
            f"num_orders={len(self.orders)}, "
            f"paired_buy_level={self.paired_buy_level.price if self.paired_buy_level else None}), "
            f"paired_sell_level={self.paired_sell_level.price if self.paired_sell_level else None})"
        )

    def __repr__(self) -> str:
        """
        返回网格级别的字符串表示，用于调试和开发
        """
        return self.__str__()