from typing import List, Dict, Optional, Tuple
from .perpetual_order import PerpetualOrder, PerpetualOrderSide, PerpetualOrderStatus, PerpetualOrderType
from ..grid_management.grid_level import GridLevel

"""永续合约U本位订单簿管理类，负责维护所有合约订单及其与网格层级的关联"""

class PerpetualOrderBook:
    def __init__(self):
        # 按持仓方向和操作类型分类存储订单
        self.long_orders: Dict[str, List[PerpetualOrder]] = {
            'open': [],   # 开多仓订单
            'close': []    # 平多仓订单
        }
        self.short_orders: Dict[str, List[PerpetualOrder]] = {
            'open': [],   # 开空仓订单
            'close': []    # 平空仓订单
        }
        
        # 条件订单（如止损、止盈等）
        self.conditional_orders: List[PerpetualOrder] = []
        
        # 网格订单映射关系
        self.order_to_grid_map: Dict[PerpetualOrder, GridLevel] = {}
        
        # 未关联网格的独立订单（如止盈/止损单）
        self.non_grid_orders: List[PerpetualOrder] = []
    
    def add_order(
        self,
        order: PerpetualOrder,
        grid_level: Optional[GridLevel] = None
    ) -> None:
        """添加订单到订单簿

        参数:
            order: 需要添加的永续合约订单对象
            grid_level: 可选参数，该订单关联的网格层级（None表示非网格订单）
        """
        # 根据订单类型和方向分类存储
        if order.order_type in [PerpetualOrderType.STOP_MARKET, PerpetualOrderType.STOP_LIMIT,
                         PerpetualOrderType.TAKE_PROFIT_MARKET, PerpetualOrderType.TAKE_PROFIT_LIMIT,
                         PerpetualOrderType.TRAILING_STOP]:
            self.conditional_orders.append(order)
        else:
            if order.side in [PerpetualOrderSide.BUY_OPEN, PerpetualOrderSide.SELL_CLOSE]:
                target_list = self.long_orders['open'] if order.side == PerpetualOrderSide.BUY_OPEN \
                    else self.long_orders['close']
                target_list.append(order)
            else:  # OPEN_SHORT or CLOSE_LONG
                target_list = self.short_orders['open'] if order.side == PerpetualOrderSide.SELL_OPEN \
                    else self.short_orders['close']
                target_list.append(order)
        
        # 处理网格关联逻辑
        if grid_level:
            self.order_to_grid_map[order] = grid_level
        else:
            self.non_grid_orders.append(order)
    
    def get_orders_by_side(self, side: PerpetualOrderSide) -> List[PerpetualOrder]:
        """获取指定方向的所有订单

        参数:
            side: 订单方向（开多、开空、平多、平空）
        返回值:
            符合指定方向的订单列表
        """
        if side == PerpetualOrderSide.OPEN_LONG:
            return self.long_orders['open']
        elif side == PerpetualOrderSide.CLOSE_LONG:
            return self.long_orders['close']
        elif side == PerpetualOrderSide.OPEN_SHORT:
            return self.short_orders['open']
        else:  # CLOSE_SHORT
            return self.short_orders['close']
    
    def get_conditional_orders(self) -> List[PerpetualOrder]:
        """获取所有条件订单（止损、止盈等）"""
        return self.conditional_orders
    
    def get_orders_with_grid(self, side: PerpetualOrderSide) -> List[Tuple[PerpetualOrder, Optional[GridLevel]]]:
        """获取带网格信息的指定方向订单列表

        参数:
            side: 订单方向（开多、开空、平多、平空）
        返回值:
            订单和对应网格层级的元组列表
        """
        orders = self.get_orders_by_side(side)
        return [(order, self.order_to_grid_map.get(order)) for order in orders]
    
    def get_open_orders(self) -> List[PerpetualOrder]:
        """获取所有未成交订单（包括所有方向）"""
        all_orders = []
        for orders in self.long_orders.values():
            all_orders.extend([order for order in orders if order.is_open()])
        for orders in self.short_orders.values():
            all_orders.extend([order for order in orders if order.is_open()])
        all_orders.extend([order for order in self.conditional_orders if order.is_open()])
        return all_orders
    
    def get_completed_orders(self) -> List[PerpetualOrder]:
        """获取所有已成交订单（包括所有方向）"""
        all_orders = []
        for orders in self.long_orders.values():
            all_orders.extend([order for order in orders if order.is_filled()])
        for orders in self.short_orders.values():
            all_orders.extend([order for order in orders if order.is_filled()])
        all_orders.extend([order for order in self.conditional_orders if order.is_filled()])
        return all_orders
    
    def get_grid_level_for_order(self, order: PerpetualOrder) -> Optional[GridLevel]:
        """查询订单对应的网格层级（返回None表示非网格订单）"""
        return self.order_to_grid_map.get(order)
    
    def update_order_status(
        self,
        order_id: str,
        new_status: PerpetualOrderStatus
    ) -> None:
        """更新订单状态

        参数:
            order_id: 需要更新的订单ID
            new_status: 新状态（如FILLED/CANCELED/LIQUIDATED等）
        """
        # 遍历所有可能的订单列表
        all_orders = []
        for orders in self.long_orders.values():
            all_orders.extend(orders)
        for orders in self.short_orders.values():
            all_orders.extend(orders)
        all_orders.extend(self.conditional_orders)
        
        # 查找并更新匹配的订单
        for order in all_orders:
            if order.identifier == order_id:
                order.status = new_status
                break