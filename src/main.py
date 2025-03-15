import os
import time
import signal
import threading
import sys
import uuid
from datetime import datetime

from src.config import config
from src.utils import get_logger
from src.data_retrieval import data_retrieval_service
from src.ai_decision import ai_decision_service

# Import trade execution service components directly to avoid circular imports
from src.trade_execution.service import TradeExecutionService
trade_execution_service = TradeExecutionService()

logger = get_logger("main")

# Import version from setup.py
import sys
sys.path.insert(0, '..')
from setup import VERSION

if len(sys.argv) > 1 and sys.argv[1] == '--license':
    print(f"TraderMagic v{VERSION} - Licensed under AGPL-3.0")
    sys.exit(0)

# Global flag to control the main loop
running = True

def signal_handler(sig, frame):
    """
    Handle termination signals to gracefully shutdown the application
    """
    global running
    logger.info("Received termination signal, shutting down...")
    running = False

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def create_logs_directory():
    """
    Create logs directory if it doesn't exist
    """
    if not os.path.exists("logs"):
        os.makedirs("logs")
        logger.info("Created logs directory")

def setup_services():
    """
    Initialize and start all services
    """
    logger.info("Starting services...")
    
    # IMPORTANT: ALWAYS initialize trading to disabled on startup for safety
    from src.utils import force_trading_disabled
    success = force_trading_disabled()
    logger.info("Trading initialized to DISABLED for safety")
    
    # Double-check the value was set correctly
    from src.utils import redis_client
    trading_enabled = redis_client.client.get("trading_enabled")
    logger.info(f"VERIFY: 'trading_enabled' is currently set to '{trading_enabled}' in Redis")
    
    # Start the data retrieval service
    data_retrieval_service.start()
    logger.info("Data retrieval service started")
    
    # Log key components for debugging
    import json
    logger.info("Active trading symbols: %s", config.trading.symbols)
    logger.info("Trade percentage: %s", config.trading.trade_percentage)
    logger.info("Poll interval: %s", config.trading.poll_interval)
    
    # Check if signals are already in Redis
    for symbol in config.trading.symbols:
        signal_data = redis_client.get_json(f"signal:{symbol}")
        if signal_data:
            logger.info(f"Found existing signal for {symbol}: {json.dumps(signal_data)}")
        else:
            logger.info(f"No existing signal for {symbol}")
            
        result_data = redis_client.get_json(f"trade_result:{symbol}")
        if result_data:
            logger.info(f"Found existing trade result for {symbol}: {json.dumps(result_data)}")
        else:
            logger.info(f"No existing trade result for {symbol}")

def main_loop():
    """
    Main application loop that coordinates the three components
    """
    global running
    
    logger.info(f"Starting main loop with poll interval: {config.trading.poll_interval} seconds")
    logger.info(f"Trading symbols: {config.trading.symbols}")
    
    # Check if ALPACA_DEBUG_MODE is enabled
    import os
    debug_mode = os.getenv("ALPACA_DEBUG_MODE") == "true"
    logger.info(f"ALPACA_DEBUG_MODE: {debug_mode}")
    
    # Wait initial time for the data retrieval service to get first data
    initial_wait = min(30, config.trading.poll_interval)
    logger.info(f"Waiting {initial_wait} seconds for initial data collection...")
    time.sleep(initial_wait)
    
    # Import for direct redis access
    from src.utils import redis_client
    import json
    
    # Initialize execution count for tracking
    execution_count = 0
    
    while running:
        try:
            # Log iteration start
            execution_count += 1
            logger.info(f"Main loop iteration #{execution_count} starting")
            
            # Get all signals at once to avoid decision delays
            all_signals = {}
            for symbol in config.trading.symbols:
                # Get the latest trade signal (already calculated by data_retrieval service)
                signal = ai_decision_service.get_latest_signal(symbol)
                if signal:
                    all_signals[symbol] = signal
                    logger.info(f"Retrieved signal for {symbol}: {signal.decision.value}")
                else:
                    logger.warning(f"No signal found for {symbol}")
                    # Check if RSI data is available
                    rsi_data = data_retrieval_service.get_latest_rsi(symbol)
                    if rsi_data:
                        logger.info(f"RSI data available for {symbol}: {rsi_data.value}, generating signal")
                        # Try to generate a signal
                        signal = ai_decision_service.get_decision(rsi_data)
                        if signal:
                            all_signals[symbol] = signal
                            logger.info(f"Generated new signal for {symbol}: {signal.decision.value}")
            
            # Log how many signals we found
            logger.info(f"Found {len(all_signals)} signals for execution")
            
            # Execute trades for all signals that aren't "hold"
            for symbol, signal in all_signals.items():
                try:
                    if signal.decision.value != "hold":
                        logger.info(f"Executing trade for {symbol} with decision: {signal.decision.value}")
                        
                        # FORCE SUCCESS FOR TESTING 
                        if debug_mode:
                            # Force a successful trade result in debug mode
                            from src.utils import TradeResult
                            import uuid
                            from datetime import datetime
                            
                            mock_price = 45000.0 if "BTC" in symbol else 3000.0
                            mock_quantity = 0.001 if "BTC" in symbol else 0.01
                            
                            # Create a successful trade result
                            result = TradeResult(
                                symbol=symbol,
                                decision=signal.decision,
                                order_id=f"debug-{uuid.uuid4()}",
                                quantity=mock_quantity,
                                price=mock_price,
                                status="executed",
                                error=None,
                                timestamp=datetime.now()
                            )
                            
                            logger.info(f"DEBUG MODE: Created mock trade result for {symbol}")
                        else:
                            # Normal execution
                            result = trade_execution_service.execute_trade(signal)
                        
                        # Handle the result
                        if result:
                            logger.info(f"Trade result for {symbol}: {result.status}")
                            if result.status == "executed":
                                logger.info(f"Order executed for {symbol}: {result.quantity} @ ~${result.price:.2f}")
                                
                                # Debug the result that's being saved in Redis
                                logger.info(f"Trade result object: {result}")
                                
                                # Always save the result to Redis with a long TTL
                                redis_key = f"trade_result:{symbol}"
                                success = redis_client.set_json(redis_key, result.dict(), ttl=86400)  # 24 hour TTL
                                logger.info(f"Saved trade result to Redis key: {redis_key} (success: {success})")
                                
                                # Check if it was saved properly
                                saved_data = redis_client.get_json(redis_key)
                                if saved_data:
                                    logger.info(f"Verified Redis data for {redis_key}: {json.dumps(saved_data)}")
                                else:
                                    logger.error(f"Failed to verify Redis data for {redis_key}!")
                            else:
                                logger.warning(f"Trade not executed. Status: {result.status}, Error: {result.error}")
                        else:
                            logger.error(f"No result returned from trade execution for {symbol}")
                    else:
                        logger.info(f"Decision is to hold for {symbol}, no trade executed")
                except Exception as trade_error:
                    logger.error(f"Error executing trade for {symbol}: {trade_error}")
                    import traceback
                    logger.error(f"Traceback: {traceback.format_exc()}")
            
            # Sleep less than the poll interval to check for new signals sooner
            # This ensures we catch new signals shortly after they're generated
            sleep_time = min(60, max(30, config.trading.poll_interval // 2))
            logger.info(f"Main loop iteration #{execution_count} completed. Sleeping for {sleep_time} seconds")
            time.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            time.sleep(5)  # Brief pause before continuing

def shutdown():
    """
    Perform cleanup and shutdown operations
    """
    logger.info("Shutting down...")
    
    # Stop the data retrieval service
    data_retrieval_service.stop()
    
    logger.info("Shutdown complete")

if __name__ == "__main__":
    # Make sure the logs directory exists
    create_logs_directory()
    
    # Print startup banner
    logger.info("=" * 80)
    logger.info(f"TraderMagic starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Configured to trade: {', '.join(config.trading.symbols)}")
    logger.info(f"Using Ollama model: {config.ollama.model}")
    logger.info(f"Alpaca paper trading: {config.alpaca.paper_trading}")
    logger.info("=" * 80)
    
    try:
        # Set up and start services
        setup_services()
        
        # Run the main loop
        main_loop()
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
    finally:
        # Ensure proper shutdown
        shutdown()