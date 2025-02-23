from .backtest_exchange_service import BacktestExchangeService
from .live_exchange_service import LiveExchangeService
from .perpetual_exchange_service import PerpetualExchangeService
from config.config_manager import ConfigManager
from config.trading_mode import TradingMode

class ExchangeServiceFactory:
    @staticmethod
    def create_exchange_service(
        config_manager: ConfigManager,
        trading_mode: TradingMode
    ):
        if trading_mode == TradingMode.BACKTEST:
            return BacktestExchangeService(config_manager)
        elif trading_mode == TradingMode.PAPER_TRADING:
            return LiveExchangeService(config_manager, is_paper_trading_activated=True)
        elif trading_mode == TradingMode.LIVE:
            if config_manager.get_instrument_type() == "perpetual":
                return PerpetualExchangeService(config_manager, is_paper_trading_activated=False)
            else:
                return LiveExchangeService(config_manager, is_paper_trading_activated=False)
        elif trading_mode == TradingMode.PERPETUAL_LIVE:
            return PerpetualExchangeService(config_manager, is_paper_trading_activated=False)
        else:
            raise ValueError(f"Unsupported trading mode: {trading_mode}")