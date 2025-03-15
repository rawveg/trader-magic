from typing import Dict, Optional
import time
import uuid
import threading
from datetime import datetime

from src.utils import get_logger, TradeSignal, TradeResult, redis_client

logger = get_logger("trade_execution_service")

# Import version from setup.py
import sys
sys.path.insert(0, '..')
from setup import VERSION

if len(sys.argv) > 1 and sys.argv[1] == '--license':
    print(f"TraderMagic v{VERSION} - Licensed under AGPL-3.0")
    sys.exit(0)

# Import alpaca_client inside the class methods to avoid circular imports

class TradeExecutionService:
    def __init__(self):
        self.last_execution_time: Dict[str, float] = {}
        self.min_execution_interval = 300  # Minimum seconds between trades for the same symbol
        
        # SAFETY: Force trading to disabled state at startup
        # This is a critical safety feature
        redis_client.client.set("trading_enabled", "false")
        logger.warning("SAFETY: Trading initialized to DISABLED in trade execution service")

    def execute_trade(self, signal: TradeSignal) -> Optional[TradeResult]:
        """
        Execute a trade based on a signal if enough time has passed since last execution
        
        Args:
            signal: Trade signal
            
        Returns:
            TradeResult or None if trade was skipped due to time constraints
        """
        # Import alpaca_client here to avoid circular imports
        from src.trade_execution.alpaca_client import alpaca_client
        
        # Check if we recently executed a trade for this symbol
        current_time = time.time()
        last_time = self.last_execution_time.get(signal.symbol, 0)
        time_since_last_trade = current_time - last_time
        
        if time_since_last_trade < self.min_execution_interval:
            logger.info(f"Skipping trade for {signal.symbol} - too soon after last trade ({time_since_last_trade:.1f}s < {self.min_execution_interval}s)")
            return None
        
        # Execute the trade
        logger.info(f"Calling alpaca_client.execute_trade for {signal.symbol}")
        result = alpaca_client.execute_trade(signal)
        logger.info(f"Trade execution result: {result}")
        
        # Update last execution time
        if result and result.status == "executed":
            self.last_execution_time[signal.symbol] = current_time
            logger.info(f"Updated last execution time for {signal.symbol}")
        
        # Store the result in Redis with explicit TTL
        if result:
            redis_key = f"trade_result:{signal.symbol}"
            success = redis_client.set_json(redis_key, result.dict(), ttl=3600)
            logger.info(f"Saved trade result to Redis key {redis_key}: {success}")
        
        return result
    
    def get_latest_result(self, symbol: str) -> Optional[TradeResult]:
        """
        Get the latest trade result for a symbol from Redis
        
        Args:
            symbol: Asset symbol
            
        Returns:
            TradeResult or None if not found
        """
        redis_key = f"trade_result:{symbol}"
        data = redis_client.get_json(redis_key)
        if data:
            return TradeResult(**data)
        return None
        
# Listen for account info requests and settings updates via Redis
def start_listeners():
    """Start background threads to listen for various Redis requests"""
    import json
    import threading
    from dotenv import load_dotenv
    
    def settings_listener_thread():
        from src.utils import redis_client
        logger.info("Starting settings listener thread")
        pubsub = redis_client.get_pubsub()
        pubsub.subscribe('settings:update')
        
        for message in pubsub.listen():
            try:
                if message['type'] == 'message':
                    logger.info(f"Received settings update: {message['data']}")
                    # Reload environment variables
                    load_dotenv(override=True)
                    logger.info("Environment variables reloaded from .env file")
                    
                    # Get updated values for logging
                    from src.config import config
                    logger.info(f"Updated settings: use_fixed_amount={config.trading.use_fixed_amount}, " +
                               f"fixed_amount={config.trading.trade_fixed_amount}, " +
                               f"percentage={config.trading.trade_percentage}")
            except Exception as e:
                logger.error(f"Error processing settings update: {e}")
    
    def account_info_listener_thread():
        from src.utils import redis_client
        from src.trade_execution.alpaca_client import alpaca_client
        import json
        
        logger.info("Starting account info listener thread")
        pubsub = redis_client.get_pubsub()
        pubsub.subscribe('trade_execution_requests')
        
        for message in pubsub.listen():
            try:
                if message['type'] == 'message':
                    data = json.loads(message['data'])
                    logger.info(f"Received trade execution request: {data}")
                    
                    # Handle account info requests
                    if data.get('type') == 'account_info_request':
                        request_id = data.get('request_id')
                        if request_id:
                            try:
                                # Get account summary from Alpaca
                                account_summary = alpaca_client.get_account_summary()
                                
                                # Store the response in Redis with the request ID
                                response_key = f'account_info_response:{request_id}'
                                redis_client.set_json(response_key, account_summary, ttl=60)  # 1 minute TTL
                                logger.info(f"Sent account info response for request {request_id}")
                            except Exception as e:
                                logger.error(f"Error getting account info: {e}")
                                # Store error response
                                response_key = f'account_info_response:{request_id}'
                                error_response = {
                                    'error': str(e),
                                    'status': 'error'
                                }
                                redis_client.set_json(response_key, error_response, ttl=60)
            except Exception as e:
                logger.error(f"Error processing trade execution request: {e}")
    
    # Start settings listener in a daemon thread
    settings_thread = threading.Thread(target=settings_listener_thread, daemon=True)
    settings_thread.start()
    logger.info("Settings listener thread started")
    
    # Start account info listener in a daemon thread
    account_thread = threading.Thread(target=account_info_listener_thread, daemon=True)
    account_thread.start()
    logger.info("Account info listener thread started")
    
    return settings_thread, account_thread

# Entry point for running as a standalone module
def run_standalone():
    import time
    import json
    import uuid
    logger.info("Starting Trade Execution Service as standalone")
    
    # Create the service for standalone use
    service = TradeExecutionService()
    
    # Start all listeners
    settings_thread, account_thread = start_listeners()
    
    # Keep the main thread alive and actively check for signals
    try:
        logger.info("Starting active poll for trade signals")
        poll_interval = 5  # seconds
        running = True
        iteration = 0
        
        while running:
            try:
                iteration += 1
                if iteration % 4 == 0:  # Log less frequently 
                    logger.info(f"Polling for trade signals (iteration {iteration})")
                
                # Get all signal keys from Redis
                from src.utils import redis_client
                from src.utils import TradeSignal, TradingDecision
                from src.config import config
                
                # Process each trading symbol
                for symbol in config.trading.symbols:
                    signal_key = f"signal:{symbol}"
                    try:
                        # Get the signal data
                        signal_data = redis_client.get_json(signal_key)
                        if signal_data:
                            if iteration % 4 == 0:  # Log less frequently
                                logger.info(f"Found signal for {symbol}: {json.dumps(signal_data)}")
                            
                            # Create a trade signal object
                            try:
                                trade_signal = TradeSignal(**signal_data)
                                
                                # Only execute if this is a BUY or SELL (not HOLD)
                                if trade_signal.decision.value != "hold":
                                    # Check if trading is enabled from Redis (not config)
                                    trading_enabled_redis = redis_client.client.get("trading_enabled")
                                    trading_enabled = trading_enabled_redis == "true" if trading_enabled_redis is not None else False
                                    if not trading_enabled:
                                        logger.info(f"Trading is disabled. Not executing {trade_signal.decision.value} for {symbol}")
                                        
                                        # SIMPLE SERVICE APPROACH:
                                        # Create standard service messages that look like any other trade result
                                        from src.utils import TradeResult
                                        import uuid
                                        
                                        # Create a standard skipped trade result
                                        result = TradeResult(
                                            symbol=symbol,
                                            decision=trade_signal.decision,
                                            order_id=f"skipped-disabled-{uuid.uuid4()}",
                                            quantity=None,
                                            price=None,
                                            status="skipped",
                                            error="Trading is currently disabled",
                                            timestamp=datetime.now()
                                        )
                                        
                                        # Save to Redis - UI will process this like any other message
                                        redis_key = f"trade_result:{symbol}"
                                        success = redis_client.set_json(redis_key, result.dict(), ttl=3600)
                                        logger.info(f"Created standard disabled service message for {symbol}")
                                        
                                        # Push an immediate keyspace notification to refresh UI
                                        redis_client.client.publish('__keyspace@0__:' + redis_key, 'set')
                                        logger.info(f"Sent keyspace notification for immediate refresh: {redis_key}")
                                        continue
                                    
                                    # Find most recent result
                                    result_key = f"trade_result:{symbol}"
                                    recent_result = redis_client.get_json(result_key)
                                    
                                    # Only execute if we don't have a recent result or the signal is newer
                                    should_execute = True
                                    if recent_result and 'timestamp' in recent_result and 'timestamp' in signal_data:
                                        signal_time = signal_data['timestamp']
                                        result_time = recent_result['timestamp']
                                        # If signal is older than result, don't execute again
                                        if signal_time <= result_time:
                                            should_execute = False
                                            if iteration % 20 == 0:  # Log very infrequently
                                                logger.info(f"Signal for {symbol} is not newer than last result, skipping")
                                    
                                    if should_execute:
                                        # Force debug mode execution
                                        import os
                                        os.environ["ALPACA_DEBUG_MODE"] = "true"
                                        
                                        logger.info(f"Executing trade for {symbol}: {trade_signal.decision.value}")
                                        # Import alpaca_client here to avoid circular imports
                                        from src.trade_execution.alpaca_client import alpaca_client
                                        result = alpaca_client.execute_trade(trade_signal)
                                        
                                        if result:
                                            logger.info(f"Trade result for {symbol}: {result.status}")
                                            
                                            # Ensure order_id is always a string and all required fields are present
                                            if not result:
                                                logger.warning(f"No result returned from trade execution for {symbol}")
                                                continue
                                                
                                            if result.order_id is None:
                                                result.order_id = f"unknown-{uuid.uuid4()}"
                                                
                                            # Ensure all fields are valid
                                            if not isinstance(result.order_id, str):
                                                result.order_id = str(result.order_id)
                                            
                                            # Save to Redis
                                            redis_key = f"trade_result:{symbol}"
                                            success = redis_client.set_json(redis_key, result.dict(), ttl=86400)
                                            logger.info(f"Saved trade result to Redis key: {redis_key}: {success}")
                            except Exception as signal_error:
                                logger.error(f"Error processing signal for {symbol}: {signal_error}")
                    except Exception as e:
                        logger.error(f"Error checking signal for {symbol}: {e}")
                            
                # Sleep between polling cycles
                time.sleep(poll_interval)
                
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Shutting down Trade Execution Service")

# Add this for module execution
if __name__ == "__main__":
    run_standalone()