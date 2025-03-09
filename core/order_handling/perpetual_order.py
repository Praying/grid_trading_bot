from enum import Enum
from typing import Optional, List, Dict, Union
import pandas as pd

class PerpetualOrderSide(Enum):
    """永续合约订单方向"""
    BUY_OPEN = 'buy'  # 买入开多
    SELL_CLOSE = 'sell'  # 卖出平多
    SELL_OPEN = 'sell'  # 卖出开空
    BUY_CLOSE = 'buy'  # 买入平空

class PerpetualOrderType(Enum):
    """永续合约订单类型"""
    MARKET = 'market'  # 市价单
    LIMIT = 'limit'  # 限价单
    STOP_MARKET = 'stop_market'  # 止损市价单
    STOP_LIMIT = 'stop_limit'  # 止损限价单
    TAKE_PROFIT_MARKET = 'take_profit_market'  # 止盈市价单
    TAKE_PROFIT_LIMIT = 'take_profit_limit'  # 止盈限价单
    TRAILING_STOP = 'trailing_stop'  # 跟踪止损单

class PerpetualOrderStatus(Enum):
    """永续合约订单状态"""
    OPEN = "open"  # 未成交
    CLOSED = "closed"  # 已完全成交
    CANCELED = "canceled"  # 已取消
    EXPIRED = "expired"  # 已过期
    REJECTED = "rejected"  # 已拒绝
    UNKNOWN = "unknown"  # 未知状态
    LIQUIDATED = "liquidated"  # 已强平
    ADL = "adl"  # 自动减仓
    PARTIAL_CLOSE = "partial_close"  # 部分平仓

class MarginType(Enum):
    """保证金类型"""
    ISOLATED = 'isolated'  # 逐仓
    CROSS = 'cross'  # 全仓

class PositionSide(Enum):
    """持仓方向"""
    LONG = 'long'  # 多头
    SHORT = 'short'  # 空头
    BOTH = 'both'  # 双向持仓

class PerpetualOrder:
    """永续合约订单类"""

    def __init__(
        self,
        identifier: str,
        status: PerpetualOrderStatus,
        order_type: PerpetualOrderType,
        side: PerpetualOrderSide,
        price: float,
        average: Optional[float],
        contracts: float,  # 合约张数
        contract_size: float,  # 合约面值
        filled: float,
        remaining: float,
        timestamp: int,
        datetime: Optional[str],
        last_trade_timestamp: Optional[int],
        symbol: str,
        time_in_force: Optional[str],
        leverage: float,  # 杠杆倍数
        margin_type: MarginType,  # 保证金类型
        position_side: PositionSide,  # 持仓方向
        reduce_only: bool = False,  # 是否只减仓
        stop_price: Optional[float] = None,  # 触发价格
        activation_price: Optional[float] = None,  # 跟踪止损激活价格
        callback_rate: Optional[float] = None,  # 跟踪止损回调比例
        trades: Optional[List[Dict[str, Union[str, float]]]] = None,
        fee: Optional[Dict[str, Union[str, float]]] = None,
        cost: Optional[float] = None,
        info: Optional[Dict[str, Union[str, float, dict]]] = None
    ):
        self.identifier = identifier
        self.status = status
        self.order_type = order_type
        self.side = side
        self.price = price
        self.average = average
        self.contracts = contracts
        self.contract_size = contract_size
        self.filled = filled
        self.remaining = remaining
        self.timestamp = timestamp
        self.datetime = datetime
        self.last_trade_timestamp = last_trade_timestamp
        self.symbol = symbol
        self.time_in_force = time_in_force
        self.leverage = leverage
        self.margin_type = margin_type
        self.position_side = position_side
        self.reduce_only = reduce_only
        self.stop_price = stop_price
        self.activation_price = activation_price
        self.callback_rate = callback_rate
        self.trades = trades
        self.fee = fee
        self.cost = cost
        self.info = info

    @property
    def amount(self) -> float:
        """获取合约头寸大小（合约张数 * 合约面值）"""
        return self.contracts * self.contract_size

    def is_filled(self) -> bool:
        """检查订单是否已完全成交"""
        return self.status == PerpetualOrderStatus.CLOSED

    def is_canceled(self) -> bool:
        """检查订单是否已取消"""
        return self.status == PerpetualOrderStatus.CANCELED

    def is_open(self) -> bool:
        """检查订单是否未成交"""
        return self.status == PerpetualOrderStatus.OPEN

    def is_liquidated(self) -> bool:
        """检查订单是否已强平"""
        return self.status == PerpetualOrderStatus.LIQUIDATED

    def is_adl(self) -> bool:
        """检查订单是否已自动减仓"""
        return self.status == PerpetualOrderStatus.ADL

    def is_partial_close(self) -> bool:
        """检查订单是否部分平仓"""
        return self.status == PerpetualOrderStatus.PARTIAL_CLOSE
    
    def format_last_trade_timestamp(self) -> Optional[str]:
        """格式化最后成交时间戳"""
        if self.last_trade_timestamp is None:
            return None
        return pd.Timestamp(self.last_trade_timestamp, unit='s').isoformat()

    def __str__(self) -> str:
        return (
            f"PerpetualOrder(id={self.identifier}, status={self.status}, "
            f"type={self.order_type}, side={self.side}, price={self.price}, average={self.average}, "
            f"contracts={self.contracts}, contract_size={self.contract_size}, filled={self.filled}, "
            f"remaining={self.remaining}, timestamp={self.timestamp}, datetime={self.datetime}, "
            f"symbol={self.symbol}, time_in_force={self.time_in_force}, leverage={self.leverage}, "
            f"margin_type={self.margin_type}, position_side={self.position_side}, "
            f"reduce_only={self.reduce_only}, stop_price={self.stop_price}, "
            f"activation_price={self.activation_price}, callback_rate={self.callback_rate}, "
            f"trades={self.trades}, fee={self.fee}, cost={self.cost})"
        )

    def __repr__(self) -> str:
        return self.__str__()