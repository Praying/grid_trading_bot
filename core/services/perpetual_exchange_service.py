import ccxt, logging, asyncio, os
from ccxt.base.errors import NetworkError, BaseError, ExchangeError, OrderNotFound
import ccxt.pro as ccxtpro
from typing import Dict, Union, Callable, Any, Optional, List
import pandas as pd
from config.config_manager import ConfigManager
from .exchange_interface import ExchangeInterface
from .exceptions import UnsupportedExchangeError, DataFetchError, OrderCancellationError, MissingEnvironmentVariableError

class PerpetualExchangeService(ExchangeInterface):
    async def get_margin_ratio(self) -> float:
        if self.exchange_name == 'okx':
            # 获取账户风险信息
            try:
                # 获取 U 本位永续合约仓位信息
                positions = await self.exchange.fetch_position(self.symbol)
                for position in positions['data']:
                    if position['instType'] == 'SWAP' and position['currency'] == 'USDT':
                        # 查找U本位永续合约仓位信息
                        print(f"Symbol: {position['instId']}")
                        print(f"Position: {position['pos']}")
                        print(f"Leverage: {position['leverage']}")
                        print(f"Margin: {position['margin']}")
                        print(f"Risk: {position['risk']}")  # 包含强平风险的信息
                        print(f"Liquidation Price: {position['liqPx']}")  # 强平价格
                        return float(position['margin'])
            except Exception as e:
                print(f"Error: {e}")
        elif self.exchange_name == 'binance':
            try:
                balance = await self.exchange.fetch_balance()
                margin_ratio = balance['info']['marginRatio']
                return float(margin_ratio)
            except BaseError as e:
                raise DataFetchError(f"Error fetching margin ratio: {str(e)}")

    def __init__(
        self, 
        config_manager: ConfigManager, 
        is_paper_trading_activated: bool
    ):
        self.config_manager = config_manager
        self.is_paper_trading_activated = is_paper_trading_activated
        self.logger = logging.getLogger(self.__class__.__name__)
        self.exchange_name = self.config_manager.get_exchange_name()
        self.api_key = self._get_env_variable("EXCHANGE_API_KEY")
        self.secret_key = self._get_env_variable("EXCHANGE_SECRET_KEY")
        self.password = self._get_env_variable("PASSWORD")
        self.exchange = self._initialize_exchange()
        self.connection_active = False
        self.base_currency = config_manager.get_base_currency()
        self.quote_currency = config_manager.get_quote_currency()
        self.symbol = f"{self.base_currency}/{self.quote_currency}:{self.quote_currency}"
    
    def _get_env_variable(self, key: str) -> str:
        value = os.getenv(key)
        if value is None:
            raise MissingEnvironmentVariableError(f"Missing required environment variable: {key}")
        return value

    def _initialize_exchange(self) -> ccxtpro.Exchange:
        try:
            exchange = getattr(ccxtpro, self.exchange_name)({
                'apiKey': self.api_key,
                'secret': self.secret_key,
                'password': self.password,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'swap',  # 设置为永续合约模式
                }
            })
            # 打开模拟交易模式（确保使用OKX模拟盘接口）
            if self.is_paper_trading_activated:
                exchange.set_sandbox_mode(True)
            return exchange
        except AttributeError:
            raise UnsupportedExchangeError(f"The exchange '{self.exchange_name}' is not supported.")

    def _enable_sandbox_mode(self, exchange) -> None:
        if self.exchange_name == 'binance':
            exchange.urls['api'] = 'https://testnet.binancefuture.com/fapi'
        elif self.exchange_name == 'bybit':
            exchange.set_sandbox_mode(True)
        else:
            self.logger.warning(f"No sandbox mode available for {self.exchange_name}. Running in live mode.")
    
    async def _subscribe_to_ticker_updates(
        self,
        pair: str, 
        on_ticker_update: Callable[[float], None], 
        update_interval: float,
        max_retries: int = 5
    ) -> None:
        self.connection_active = True
        retry_count = 0
        
        while self.connection_active:
            try:
                ticker = await self.exchange.watch_ticker(pair)
                current_price: float = ticker['last']
                self.logger.info(f"Connected to WebSocket for {pair} ticker current price: {current_price}")

                if not self.connection_active:
                    break

                await on_ticker_update(current_price)
                await asyncio.sleep(update_interval)
                retry_count = 0  # Reset retry count after a successful operation

            except (NetworkError, ExchangeError) as e:
                retry_count += 1
                retry_interval = min(retry_count * 5, 60)
                self.logger.error(f"Error connecting to WebSocket for {pair}: {e}. Retrying in {retry_interval} seconds ({retry_count}/{max_retries}).")
                
                if retry_count >= max_retries:
                    self.logger.error("Max retries reached. Stopping WebSocket connection.")
                    self.connection_active = False
                    break

                await asyncio.sleep(retry_interval)
            
            except asyncio.CancelledError:
                self.logger.error(f"WebSocket subscription for {pair} was cancelled.")
                self.connection_active = False
                break

            except Exception as e:
                self.logger.error(f"WebSocket connection error: {e}. Reconnecting...")
                await asyncio.sleep(5)

            finally:
                if not self.connection_active:
                    try:
                        self.logger.info("Connection to Websocket no longer active.")
                        await self.exchange.close()

                    except Exception as e:
                        self.logger.error(f"Error while closing WebSocket connection: {e}", exc_info=True)

    async def listen_to_ticker_updates(
        self, 
        pair: str, 
        on_price_update: Callable[[float], None],
        update_interval: float
    ) -> None:
        await self._subscribe_to_ticker_updates(pair, on_price_update, update_interval)

    async def close_connection(self) -> None:
        self.connection_active = False
        self.logger.info("Closing WebSocket connection...")

    async def get_balance(self) -> Dict[str, Any]:
        try:
            balance = await self.exchange.fetch_balance({'type': 'swap'})
            return balance

        except BaseError as e:
            raise DataFetchError(f"Error fetching balance: {str(e)}")
    
    async def get_current_price(self, pair: str) -> float:
        try:
            ticker = await self.exchange.fetch_ticker(pair)
            return ticker['last']

        except BaseError as e:
            raise DataFetchError(f"Error fetching current price: {str(e)}")

    async def place_order(
        self, 
        pair: str,
        order_type: str,
        order_side: str, 
        amount: float, 
        price: Optional[float] = None,
        leverage: int = 1,
        margin_type: str = 'isolated',  # 'isolated' or 'cross'
        reduce_only: bool = False,
        position_side: str = 'LONG'  # 'BOTH', 'LONG' or 'SHORT', 只做多
    ) -> Dict[str, Union[str, float]]:
        try:
            # 设置杠杆倍数
            await self.set_leverage(pair, leverage)
            
            # 设置保证金类型
            await self.set_margin_type(pair, margin_type)

            # 构建订单参数
            params = {
                'reduceOnly': reduce_only,
                'positionSide': position_side
            }

            order = await self.exchange.create_order(
                pair, 
                order_type, 
                order_side, 
                amount, 
                price,
                params
            )
            return order

        except NetworkError as e:
            raise DataFetchError(f"Network issue occurred while placing order: {str(e)}")

        except BaseError as e:
            raise DataFetchError(f"Error placing order: {str(e)}")

        except Exception as e:
            raise DataFetchError(f"Unexpected error placing order: {str(e)}")

    async def fetch_order(
        self, 
        order_id: str,
        pair: str
    ) -> Dict[str, Union[str, float]]:
        try:
            return await self.exchange.fetch_order(order_id, pair)

        except NetworkError as e:
            raise DataFetchError(f"Network issue occurred while fetching order status: {str(e)}")

        except BaseError as e:
            raise DataFetchError(f"Exchange-specific error occurred: {str(e)}")

        except Exception as e:
            raise DataFetchError(f"Failed to fetch order status: {str(e)}")

    async def cancel_order(
        self, 
        order_id: str, 
        pair: str
    ) -> dict:
        try:
            self.logger.info(f"Attempting to cancel order {order_id} for pair {pair}")
            cancellation_result = await self.exchange.cancel_order(order_id, pair)
            
            if cancellation_result['status'] in ['canceled', 'closed']:
                self.logger.info(f"Order {order_id} successfully canceled.")
                return cancellation_result
            else:
                self.logger.warning(f"Order {order_id} cancellation status: {cancellation_result['status']}")
                return cancellation_result

        except OrderNotFound as e:
            raise OrderCancellationError(f"Order {order_id} not found for cancellation. It may already be completed or canceled.")

        except NetworkError as e:
            raise OrderCancellationError(f"Network error while canceling order {order_id}: {str(e)}")

        except BaseError as e:
            raise OrderCancellationError(f"Exchange error while canceling order {order_id}: {str(e)}")

        except Exception as e:
            raise OrderCancellationError(f"Unexpected error while canceling order {order_id}: {str(e)}")
    
    async def get_exchange_status(self) -> dict:
        try:
            status = await self.exchange.fetch_status()
            return {
                "status": status.get("status", "unknown"),
                "updated": status.get("updated"),
                "eta": status.get("eta"),
                "url": status.get("url"),
                "info": status.get("info", "No additional info available")
            }

        except AttributeError:
            return {"status": "unsupported", "info": "fetch_status not supported by this exchange."}

        except Exception as e:
            return {"status": "error", "info": f"Failed to fetch exchange status: {e}"}

    def fetch_ohlcv(
        self, 
        pair: str, 
        timeframe: str, 
        start_date: str, 
        end_date: str
    ) -> pd.DataFrame:
        raise NotImplementedError("fetch_ohlcv is not used in live or paper trading mode.")

    # 永续合约特有的方法
    async def set_leverage(self, pair: str, leverage: int) -> dict:
        """设置杠杆倍数"""
        try:
            return await self.exchange.set_leverage(leverage, pair)
        except Exception as e:
            raise DataFetchError(f"Failed to set leverage: {str(e)}")

    async def set_margin_type(self, pair: str, margin_type: str) -> dict:
        """设置保证金类型（全仓或逐仓）"""
        try:
            return await self.exchange.set_margin_mode(margin_type.lower(), pair)
        except Exception as e:
            raise DataFetchError(f"Failed to set margin type: {str(e)}")

    async def get_positions(self, pairs: List[str]):
        """获取当前持仓信息"""
        try:
            positions = await self.exchange.fetch_positions(pairs)
            return positions
        except Exception as e:
            raise DataFetchError(f"Failed to fetch positions: {str(e)}")

    async def get_funding_rate(self, pair: str) -> float:
        """获取当前资金费率"""
        try:
            funding_rate = await self.exchange.fetch_funding_rate(pair)
            return funding_rate['fundingRate']
        except Exception as e:
            raise DataFetchError(f"Failed to fetch funding rate: {str(e)}")

    async def get_leverage_brackets(self, pair: str) -> Dict[str, Any]:
        """获取杠杆档位信息"""
        try:
            return await self.exchange.fetch_leverage_tiers([pair])
        except Exception as e:
            raise DataFetchError(f"Failed to fetch leverage brackets: {str(e)}")