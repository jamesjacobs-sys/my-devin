from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from env vars."""

    # Database
    database_url: str = "sqlite+aiosqlite:////data/app.db"

    # Polymarket endpoints
    polymarket_clob_host: str = "https://clob.polymarket.com"
    polymarket_gamma_host: str = "https://gamma-api.polymarket.com"
    polymarket_clob_ws: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    polymarket_rtds_ws: str = "wss://ws-live-data.polymarket.com"

    # Binance endpoints
    binance_ws: str = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
    binance_futures_ws: str = "wss://fstream.binance.com/ws/btcusdt@bookTicker"

    # Trading
    polymarket_private_key: str = ""
    paper_starting_balance: float = 1000.0
    paper_max_position: float = 50.0
    paper_max_concurrent: int = 3
    polymarket_taker_fee_rate: float = 0.02

    # Signal engine
    signal_min_edge: float = 0.03
    signal_dislocation_threshold: float = 0.002

    # Misc
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
