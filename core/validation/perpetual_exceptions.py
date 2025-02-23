"""永续合约交易相关的异常类型定义"""

class InsufficientMarginError(Exception):
    """保证金不足异常"""
    pass

class InsufficientPositionError(Exception):
    """持仓不足异常，用于平仓时持仓数量不足的情况"""
    pass

class InvalidContractQuantityError(Exception):
    """无效的合约数量异常，用于合约张数不符合要求的情况"""
    pass

class MarginRatioError(Exception):
    """保证金率异常，用于开仓后保证金率低于维持保证金率的情况"""
    pass