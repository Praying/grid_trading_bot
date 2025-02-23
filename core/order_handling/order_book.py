from typing import List, Dict, Optional, Tuple
from .order import Order, OrderSide, OrderStatus
from ..grid_management.grid_level import GridLevel

"""订单簿管理类，负责维护所有订单及其与网格层级的关联"""

class OrderBook:
    def __init__(self):
        # 初始化订单存储结构
        self.buy_orders: List[Order] = [] # 所有买单（包含网格与非网格订单）
        self.sell_orders: List[Order] = []# 所有卖单（包含网格与非网格订单）
        self.non_grid_orders: List[Order] = []  # 未关联网格的独立订单（如止盈/止损单）
        self.order_to_grid_map: Dict[Order, GridLevel] = {}  # 订单与网格层级的映射关系（网格订单专用）

    def add_order(
        self,
        order: Order,
        grid_level: Optional[GridLevel] = None
    ) -> None:
        """
        添加订单到订单簿

        参数:
            order: 需要添加的订单对象
            grid_level: 可选参数，该订单关联的网格层级（None表示非网格订单）
        """
        # 按买卖方向分类存储
        if order.side == OrderSide.BUY:
            self.buy_orders.append(order)
        else:
            self.sell_orders.append(order)
        # 处理网格关联逻辑
        if grid_level:
            self.order_to_grid_map[order] = grid_level # Store the grid level associated with this order
        else:
            self.non_grid_orders.append(order) # This is a non-grid order like take profit or stop loss
    
    def get_buy_orders_with_grid(self) -> List[Tuple[Order, Optional[GridLevel]]]:
        """获取带网格信息的买单列表（返回格式：订单对象 + 关联的网格层级）"""
        return [(order, self.order_to_grid_map.get(order, None)) for order in self.buy_orders]
    
    def get_sell_orders_with_grid(self) -> List[Tuple[Order, Optional[GridLevel]]]:
        """获取带网格信息的卖单列表（返回格式：订单对象 + 关联的网格层级）"""
        return [(order, self.order_to_grid_map.get(order, None)) for order in self.sell_orders]

    def get_all_buy_orders(self) -> List[Order]:
        """获取全部买单（不区分网格订单）"""
        return self.buy_orders

    def get_all_sell_orders(self) -> List[Order]:
        """获取全部卖单（不区分网格订单）"""
        return self.sell_orders
    
    def get_open_orders(self) -> List[Order]:
        """获取所有未成交的订单（包含买卖双方）"""
        return [order for order in self.buy_orders + self.sell_orders if order.is_open()]

    def get_completed_orders(self) -> List[Order]:
        """获取所有已成交的订单（包含买卖双方）"""
        return [order for order in self.buy_orders + self.sell_orders if order.is_filled()]

    def get_grid_level_for_order(self, order: Order) -> Optional[GridLevel]:
        """查询订单对应的网格层级（返回None表示非网格订单）"""
        return self.order_to_grid_map.get(order)

    def update_order_status(
        self, 
        order_id: str, 
        new_status: OrderStatus
    ) -> None:
        """
        更新订单状态

        参数:
            order_id: 需要更新的订单ID
            new_status: 目标状态（例如FILLED/CANCELED）
        """
        # 遍历所有订单查找匹配ID
        for order in self.buy_orders + self.sell_orders:
            if order.identifier == order_id:
                order.status = new_status
                break