# Trader Magic
#
# Copyright (C) 2025 Tim Green <rawveg@gmail.com>
#
# This file is part of Trader Magic.
#
# Trader Magic is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Trader Magic is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Trader Magic.  If not, see <https://www.gnu.org/licenses/>.

import os
import json
import time
import uuid
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import redis
import traceback
import threading
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev_key')
socketio = SocketIO(app, cors_allowed_origins="*")

# Redis connection
redis_host = os.getenv('REDIS_HOST', 'redis')
redis_port = int(os.getenv('REDIS_PORT', 6379))
redis_db = int(os.getenv('REDIS_DB', 0))
redis_client = redis.Redis(host=redis_host, port=redis_port, db=redis_db, decode_responses=True)

# Test if Redis is working correctly
print("Testing Redis connection...")
test_key = redis_client.get("test_key")
print(f"Test key from Redis: {test_key}")
if test_key:
    try:
        test_json = json.loads(test_key)
        print(f"Test JSON parse successful: {test_json}")
    except Exception as e:
        print(f"Test JSON parse failed: {e}")

# Add Redis manual key lookup
def scan_keys(pattern=None):
    """Get all keys from Redis matching a pattern"""
    pattern = pattern or "*"
    try:
        return redis_client.keys(pattern)
    except Exception as e:
        print(f"Error scanning Redis keys: {e}")
        return []

# Add Redis JSON support functions
def get_json_data(key):
    """Get JSON data from Redis"""
    try:
        # Get the raw data
        raw_data = redis_client.get(key)
        if raw_data:
            try:
                return json.loads(raw_data)
            except json.JSONDecodeError as e:
                print(f"JSON decode error for {key}: {e}")
                return {}
        return None
    except Exception as e:
        print(f"Error getting JSON from Redis: {e}")
        return None

# Add methods to redis_client
redis_client.get_json = get_json_data
redis_client.scan_iter = scan_keys

# Add pubsub method to redis_client
def get_pubsub():
    """Get a pubsub object from Redis"""
    return redis_client.pubsub()

redis_client.get_pubsub = get_pubsub

# Frontend configuration
frontend_host = os.getenv('FRONTEND_HOST', '0.0.0.0')
frontend_port = int(os.getenv('FRONTEND_PORT', 9753))

def get_all_trading_data():
    """Get all trading data from Redis"""
    try:
        symbols = os.getenv('SYMBOLS', 'BTC/USD').split(',')
        data = {}
        
        # Get Ollama model status
        ollama_status = None
        try:
            ollama_status_data = redis_client.get("ollama:status")
            if ollama_status_data:
                try:
                    ollama_status = json.loads(ollama_status_data)
                    print(f"Successfully loaded Ollama status: {ollama_status}")
                except Exception as e:
                    print(f"Error parsing Ollama status: {ollama_status_data}")
                    print(f"Exception: {e}")
                    print(traceback.format_exc())
        except Exception as e:
            print(f"Error getting Ollama status from Redis: {e}")
            print(traceback.format_exc())
        
        # Get trading enabled status from Redis - default to false
        trading_enabled_redis = redis_client.get("trading_enabled") 
        trading_enabled = trading_enabled_redis == "true" if trading_enabled_redis is not None else False
        
        # Add system status data
        data['_system'] = {
            'ollama_status': ollama_status,
            'trading_enabled': trading_enabled
        }
        
        for symbol in symbols:
            symbol_data = {
                'symbol': symbol,
                'rsi': None,
                'signal': None,
                'result': None,
                'timestamp': None,
                'price': None,
                'price_history': None,
                'alpaca_valid': True  # Default to True, will be set to False if invalid
            }
            
            # Simple check for known unsupported symbols
            # This is a simplified approach since we can't import the Alpaca client directly
            if symbol == 'QTUM/USDT':
                # QTUM/USDT is not supported by Alpaca
                symbol_data['alpaca_valid'] = False
                print(f"Symbol {symbol} is not valid for Alpaca")
            
            # Use get_json for all Redis data to match data_retrieval.service
            
            # Get RSI data
            rsi_json = redis_client.get_json(f"rsi:{symbol}")
            if rsi_json:
                symbol_data['rsi'] = rsi_json
            
            # Get signal data
            signal_json = redis_client.get_json(f"signal:{symbol}")
            if signal_json:
                symbol_data['signal'] = signal_json
            
            # Get trade result data
            result_json = redis_client.get_json(f"trade_result:{symbol}")
            if result_json:
                symbol_data['result'] = result_json
                
            # Get current price data
            price_json = redis_client.get_json(f"price:{symbol}")
            if price_json:
                symbol_data['price'] = price_json
                
            # Check if Redis has the price history key
            redis_key = f"price_history:{symbol}"
            
            # Get all keys to debug (just once)
            if symbol == symbols[0]:
                all_keys = redis_client.scan_iter("*")
                print(f"All Redis keys: {all_keys}")
                
                # Specifically check price history keys
                price_history_keys = redis_client.scan_iter("price_history:*")
                if price_history_keys:
                    print(f"Found price history keys: {price_history_keys}")
                else:
                    print("No price history keys found!")
                
            # Try to directly get the key data 
            price_history_data = redis_client.get(redis_key)
            if price_history_data:
                print(f"Raw price history data found for {symbol} (length: {len(price_history_data)})")
                
                # Create fresh instance for each symbol
                price_history_json = {}
                
                try:
                    price_history_json = json.loads(price_history_data)
                    print(f"Successfully parsed price history JSON for {symbol}")
                    
                    if 'candles' in price_history_json:
                        print(f"Found {len(price_history_json['candles'])} candles in price history for {symbol}")
                        
                        # Debug the first candle
                        if price_history_json['candles']:
                            print(f"Sample candle for {symbol}: {price_history_json['candles'][0]}")
                        
                        # Create formatted data for the chart
                        formatted_history = {
                            'timestamps': [],
                            'prices': [],
                            'market_statuses': []
                        }
                        
                        try:
                            # Sort candles by timestamp (oldest first)
                            candles = sorted(price_history_json['candles'], 
                                            key=lambda x: x['timestamp'] if isinstance(x['timestamp'], str) 
                                            else x['timestamp'].get('isoformat', ''))
                            
                            for candle in candles:
                                # Handle timestamp
                                timestamp = candle['timestamp']
                                if isinstance(timestamp, dict) and 'isoformat' in timestamp:
                                    # Handle python-serialized datetime
                                    timestamp = timestamp['isoformat']
                                
                                # Add data points
                                formatted_history['timestamps'].append(timestamp)
                                formatted_history['prices'].append(float(candle['close']))
                                
                                # Add market status
                                market_status = candle.get('market_status', 'open')
                                if isinstance(market_status, str):
                                    formatted_market_status = market_status
                                elif isinstance(market_status, dict) and 'value' in market_status:
                                    formatted_market_status = market_status['value']
                                else:
                                    formatted_market_status = 'open'
                                    
                                formatted_history['market_statuses'].append(formatted_market_status)
                                
                            print(f"!!! PRICE HISTORY DATA FOR {symbol}: {len(formatted_history['prices'])} data points !!!")
                            
                            # Add the formatted data to symbol_data
                            symbol_data['price_history'] = formatted_history
                        except Exception as e:
                            print(f"ERROR processing candles for {symbol}: {e}")
                            import traceback
                            print(traceback.format_exc())
                            # Set empty price history if processing failed
                            symbol_data['price_history'] = {
                                'timestamps': [],
                                'prices': [],
                                'market_statuses': []
                            }
                    else:
                        print(f"ERROR: No 'candles' key found in price history data for {symbol}")
                        # Set empty price history if no candles
                        symbol_data['price_history'] = {
                            'timestamps': [],
                            'prices': [],
                            'market_statuses': []
                        }
                except Exception as e:
                    print(f"Error parsing price history JSON: {e}")
                    # Set empty price history if JSON parsing failed
                    symbol_data['price_history'] = {
                        'timestamps': [],
                        'prices': [],
                        'market_statuses': []
                    }
            else:
                print(f"No raw price history data found for {symbol}")
                # Set empty price history if none found
                symbol_data['price_history'] = {
                    'timestamps': [],
                    'prices': [],
                    'market_statuses': []
                }
            
            # Get latest timestamp from any of the data sources
            timestamps = []
            for src in [symbol_data['rsi'], symbol_data['signal'], symbol_data['result'], symbol_data['price']]:
                if src and 'timestamp' in src:
                    # Convert all timestamps to strings to avoid comparison issues
                    timestamps.append(str(src['timestamp']))
            
            if timestamps:
                symbol_data['timestamp'] = max(timestamps)
            
            data[symbol] = symbol_data
        
        return data
    except Exception as e:
        print(f"Error in api_data: {e}")
        return {
            '_system': {
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
        }

def redis_listener():
    """Background thread to monitor Redis and send updates via Socket.IO"""
    pubsub = redis_client.pubsub()
    
    # Subscribe to all Redis keyspace notifications
    pubsub.psubscribe('__keyspace@0__:*')
    
    # Also subscribe to direct trading notifications channel 
    pubsub.subscribe('trade_notifications')
    print("Subscribed to 'trade_notifications' channel for direct updates")
    
    # Track last periodic update time
    last_periodic_update = float(time.time())
    periodic_update_interval = float(15)  # Send periodic updates every 15 seconds
    
    # Throttle updates from keyspace events
    last_keyspace_update = float(time.time())
    keyspace_update_interval = float(2)  # Minimum seconds between keyspace-triggered updates
    
    # Track relevant keys for trading data 
    # IMPORTANT: Include trading_enabled key to ensure UI gets notified of state changes
    relevant_prefixes = ['rsi:', 'signal:', 'trade_result:', 'ollama:', 'trading_enabled']
    
    while True:
        try:
            # Get messages with a timeout to avoid busy-waiting
            message = pubsub.get_message(timeout=1.0)
            
            current_time = float(time.time())
            
            # Handle direct trade notifications - IMMEDIATELY forward with no throttling
            if message and message['type'] == 'message' and message['channel'] == 'trade_notifications':
                print(f"⚠️ Direct trading notification received: {message['data']}")
                try:
                    # This is a direct notification that needs immediate forwarding
                    data = get_all_trading_data()  # Get complete dashboard data
                    socketio.emit('data_update', data)
                    print("✅ Sent immediate dashboard update due to trading notification")
                except Exception as e:
                    print(f"Error handling trade notification: {e}")
                    
            # Handle keyspace notifications (with throttling)
            elif message and message['type'] == 'pmessage':
                channel = message['channel']
                # Extract the key from the keyspace notification channel
                # Format is "__keyspace@0__:actual:key"
                key = channel.split(':', 1)[1] if ':' in channel else ''
                
                # Only react to relevant keys and respect rate limiting
                is_relevant = any(key.startswith(prefix) for prefix in relevant_prefixes)
                try:
                    # Debug keyspace update comparison
                    print(f"DEBUG KEYSPACE: current_time: {current_time} ({type(current_time).__name__}), "
                          f"last_keyspace_update: {last_keyspace_update} ({type(last_keyspace_update).__name__}), "
                          f"keyspace_update_interval: {keyspace_update_interval} ({type(keyspace_update_interval).__name__})")
                    
                    # Convert all to float explicitly
                    current_time_float = float(current_time)
                    last_keyspace_float = float(last_keyspace_update)
                    keyspace_interval_float = float(keyspace_update_interval)
                    
                    time_diff = current_time_float - last_keyspace_float
                    update_needed = is_relevant and time_diff > keyspace_interval_float
                    
                    if update_needed:
                        print(f"Sending update to clients for key: {key}")
                        socketio.emit('data_update', get_all_trading_data())
                        last_keyspace_update = current_time_float
                except Exception as type_error:
                    print(f"Error in keyspace update comparison: {type_error}")
                    # Reset the last update time to avoid continuous errors
                    last_keyspace_update = float(time.time())
            
            # Periodically send updates even without Redis events
            try:
                # Debug the types
                print(f"DEBUG: current_time: {current_time} ({type(current_time).__name__}), "
                      f"last_periodic_update: {last_periodic_update} ({type(last_periodic_update).__name__}), "
                      f"periodic_update_interval: {periodic_update_interval} ({type(periodic_update_interval).__name__})")
                
                # Convert all to float to ensure they're the same type
                current_time_float = float(current_time)
                last_update_float = float(last_periodic_update)
                interval_float = float(periodic_update_interval)
                
                if current_time_float - last_update_float > interval_float:
                    print(f"Sending periodic update to clients (every {interval_float} seconds)")
                    socketio.emit('data_update', get_all_trading_data())
                    last_periodic_update = current_time_float
            except Exception as debug_error:
                print(f"Error in periodic update: {debug_error}")
                # Reset the last update time to avoid continuous errors
                last_periodic_update = float(time.time())
                
            # Reduced sleep time since we're already using timeouts in get_message
            time.sleep(0.05)
            
        except Exception as e:
            print(f"Error in Redis listener: {e}")
            time.sleep(1)

@app.route('/')
def index():
    """Render main dashboard page"""
    symbols = os.getenv('SYMBOLS', 'BTC/USD').split(',')
    enforce_pdt = os.getenv('ALPACA_ENFORCE_PDT_RULES', 'true').lower() == 'true'
    paper_trading = os.getenv('ALPACA_PAPER_TRADING', 'true').lower() == 'true'
    debug_mode = os.getenv('ALPACA_DEBUG_MODE', 'false').lower() == 'true'
    
    # Get trading settings
    fixed_amount_mode = os.getenv('TRADE_USE_FIXED', 'false').lower() == 'true'
    trade_percentage = float(os.getenv('TRADE_PERCENTAGE', '2.0'))
    fixed_amount = float(os.getenv('TRADE_FIXED_AMOUNT', '10.0'))
    
    # IMPORTANT SAFETY FEATURE: Force trading to disabled state when page loads
    try:
        # Force set trading to disabled in Redis
        redis_client.set("trading_enabled", "false")
        print("SAFETY: Forced trading disabled when loading dashboard")
        trading_enabled = False
        
        # IMPORTANT: Create disabled trade messages in Redis for all symbols on page load
        for symbol in symbols:
            try:
                # Create standard disabled message
                result = {
                    "symbol": symbol,
                    "decision": "buy",  # Default to buy for displaying messages
                    "order_id": f"page-load-{uuid.uuid4()}",
                    "quantity": None,
                    "price": None,
                    "status": "skipped",
                    "error": "Trading is currently disabled",
                    "timestamp": datetime.now().isoformat()
                }
                # Save to Redis
                redis_key = f"trade_result:{symbol}"
                redis_client.set(redis_key, json.dumps(result))
                print(f"Created disabled message for {symbol} on page load")
            except Exception as e:
                print(f"Error creating disabled message: {e}")
    except Exception as e:
        print(f"Error setting trading disabled: {e}")
        trading_enabled = False
    
    # We no longer create mock trades at startup to avoid confusion
    # Users will only see real trades or trades they explicitly trigger for testing
    
    return render_template(
        'index.html', 
        symbols=symbols,
        enforce_pdt=enforce_pdt,
        paper_trading=paper_trading,
        debug_mode=debug_mode,
        fixed_amount_mode=fixed_amount_mode,
        trade_percentage=trade_percentage,
        fixed_amount=fixed_amount,
        trading_enabled=trading_enabled
    )

@app.route('/api/data')
def api_data():
    """API endpoint to get all trading data"""
    try:
        # Clean approach - no emergency hacks   
        data = get_all_trading_data()
        
        # Add debug information
        try:
            keys = {
                'keys': {
                    'all': [key for key in redis_client.scan_iter()],
                    'rsi': [key for key in redis_client.scan_iter('rsi:*')],
                    'signal': [key for key in redis_client.scan_iter('signal:*')],
                    'trade_result': [key for key in redis_client.scan_iter('trade_result:*')],
                    'price_history': [key for key in redis_client.scan_iter('price_history:*')]
                }
            }
            data['_debug'] = keys
            
            # DEBUG: Directly check price history data
            price_history_keys = [key for key in redis_client.scan_iter('price_history:*')]
            if price_history_keys:
                print(f"FOUND {len(price_history_keys)} price_history keys in Redis: {price_history_keys}")
                # Check first price history key
                sample_key = price_history_keys[0]
                sample_data = redis_client.get(sample_key)
                if sample_data:
                    try:
                        parsed_data = json.loads(sample_data)
                        print(f"Sample price history data for {sample_key}: Keys = {parsed_data.keys()}")
                        if 'candles' in parsed_data:
                            print(f"Candle count: {len(parsed_data['candles'])}")
                        else:
                            print(f"ERROR: No 'candles' key in Redis data for {sample_key}")
                    except Exception as parse_error:
                        print(f"Error parsing price history data: {parse_error}")
                else:
                    print(f"No data found for price history key {sample_key}")
            else:
                print("NO price_history keys found in Redis!")
            
        except Exception as e:
            print(f"Error adding debug keys: {e}")
            print(traceback.format_exc())
    
        # Add key contents for all trade results for easier debugging
        try:
            trade_results = {}
            for key in keys['keys']['trade_result']:
                trade_data = redis_client.get(key)
                if trade_data:
                    try:
                        trade_results[key] = json.loads(trade_data)
                    except json.JSONDecodeError:
                        trade_results[key] = {'error': 'Could not parse JSON', 'raw': trade_data}
            
            data['_trade_results'] = trade_results
        except Exception as e:
            print(f"Error adding trade results: {e}")
            print(traceback.format_exc())
        
        return jsonify(data)
    except Exception as e:
        print(f"Error in api_data: {e}")
        print(traceback.format_exc())
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

@app.route('/api/redis/<path:key>')
def redis_key(key):
    """Debug endpoint to view Redis key contents"""
    value = redis_client.get(key)
    if value:
        try:
            return jsonify({'key': key, 'value': json.loads(value)})
        except json.JSONDecodeError:
            return jsonify({'key': key, 'value': value, 'type': 'string'})
    return jsonify({'error': 'Key not found'}), 404

@app.route('/api/account')
def account_info():
    """API endpoint to get Alpaca account information"""
    try:
        # Instead of importing the alpaca_client directly, we'll use Redis to communicate with the trade_execution service
        # First, check if we have a cached account summary
        cached_summary = redis_client.get('account_summary')
        if cached_summary:
            try:
                return jsonify(json.loads(cached_summary))
            except Exception as e:
                print(f"Error parsing cached account summary: {e}")
        
        # If no cached data, request it from the trade_execution service
        # We'll publish a request to a Redis channel and wait for a response
        request_id = str(uuid.uuid4())
        request_data = {
            'request_id': request_id,
            'type': 'account_info_request',
            'timestamp': time.time()
        }
        
        # Publish the request
        redis_client.publish('trade_execution_requests', json.dumps(request_data))
        
        # Wait for a response (with timeout)
        max_wait_time = 5  # seconds
        start_time = time.time()
        response_key = f'account_info_response:{request_id}'
        
        while time.time() - start_time < max_wait_time:
            response = redis_client.get(response_key)
            if response:
                try:
                    account_data = json.loads(response)
                    # Cache the response
                    redis_client.set('account_summary', response, ex=300)  # 5 minute TTL
                    return jsonify(account_data)
                except Exception as e:
                    print(f"Error parsing account response: {e}")
                    break
            time.sleep(0.1)
        
        # If we reach here, we didn't get a response in time
        # Return a placeholder response
        return jsonify({
            'cash': 0.0,
            'portfolio_value': 0.0,
            'equity': 0.0,
            'buying_power': 0.0,
            'position_value': 0.0,
            'positions': [],
            'status': 'unavailable',
            'paper_trading': os.getenv('PAPER_TRADING', 'true').lower() == 'true'
        })
    except Exception as e:
        print(f"Error getting account info: {e}")
        print(traceback.format_exc())
        
        # Try to get from Redis cache if API call fails
        cached_summary = redis_client.get('account_summary')
        if cached_summary:
            try:
                return jsonify({
                    'cached': True,
                    **json.loads(cached_summary)
                })
            except:
                pass
        
        return jsonify({
            'error': str(e),
            'trace': traceback.format_exc(),
            'cash': 0.0,
            'portfolio_value': 0.0,
            'equity': 0.0,
            'buying_power': 0.0,
            'position_value': 0.0,
            'positions': [],
            'status': 'error',
            'paper_trading': os.getenv('PAPER_TRADING', 'true').lower() == 'true'
        }), 500

@app.route('/debug/execute-trade/<symbol>/<decision>')
def debug_execute_trade(symbol, decision):
    """Debug endpoint to execute a trade directly"""
    try:
        # Validate the decision
        decision = decision.lower()
        if decision not in ['buy', 'sell', 'hold']:
            return jsonify({
                'status': 'error',
                'message': f'Invalid decision: {decision}. Must be one of: buy, sell, hold'
            }), 400
            
        # Return a simulated response for the debug interface
        return jsonify({
            'status': 'success',
            'message': f'Debug trade executed successfully',
            'details': {
                'symbol': symbol,
                'decision': decision,
                'timestamp': datetime.now().isoformat(),
                'paper_trading': os.getenv('PAPER_TRADING', 'true').lower() == 'true'
            }
        })
    except Exception as e:
        print(f"Error in debug trade execution: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

        
@app.route('/api/toggle_trading', methods=['POST'])
def toggle_trading():
    """Toggle trading on or off"""
    try:
        # Get the request data
        data = request.get_json()
        if data is None:
            data = {}
            
        enabled = data.get('enabled', False)
        
        # Don't update .env file - only use Redis for temporary state
        # The trading_enabled will reset to default (false) when containers restart
        print(f"===== TRADING STATUS TOGGLED TO: {'ENABLED' if enabled else 'DISABLED'} =====")
        print(f"Trading toggled to {'enabled' if enabled else 'disabled'} (temporary, not saving to .env)")
        
        # Store trading status directly in Redis for immediate access
        print(f"Setting trading_enabled in Redis to: {'true' if enabled else 'false'}")
        try:
            redis_client.set("trading_enabled", "true" if enabled else "false")
            print("Successfully set trading_enabled in Redis")
        except Exception as e:
            print(f"Error setting trading_enabled in Redis: {e}")
            print(traceback.format_exc())
        
        # SIMPLE SERVICE APPROACH:
        # When trading state changes, create appropriate trade result messages
        try:
            # 1. Publish standard message for services
            redis_client.client.publish('settings:update', json.dumps({
                'trading_enabled': enabled
            }))
            print(f"Published trading status update to Redis: {'enabled' if enabled else 'disabled'}")
            
            # 2. If trading was DISABLED, create "disabled" messages for all symbols
            if not enabled:
                # Get symbols and create disabled messages for each
                symbols = os.getenv('SYMBOLS', 'BTC/USD').split(',')
                for symbol in symbols:
                    try:
                        # Get current signal for this symbol to include its decision
                        signal_data = redis_client.client.get(f"signal:{symbol}")
                        if signal_data:
                            # Only create disabled messages for non-HOLD signals
                            signal_json = json.loads(signal_data)
                            if signal_json.get('decision', '').lower() != 'hold':
                                # Create standard disabled message
                                result = {
                                    "symbol": symbol,
                                    "decision": signal_json.get('decision'),
                                    "order_id": f"service-disabled-{uuid.uuid4()}",
                                    "quantity": None,
                                    "price": None,
                                    "status": "skipped",
                                    "error": "Trading is currently disabled",
                                    "timestamp": datetime.now().isoformat()
                                }
                                # Save to Redis as standard result 
                                redis_key = f"trade_result:{symbol}"
                                redis_client.set(redis_key, json.dumps(result))
                                # Force a keyspace notification to refresh UI immediately
                                redis_client.publish('__keyspace@0__:' + redis_key, 'set')
                                print(f"Created standard disabled message for {symbol} and published notification")
                    except Exception as e:
                        print(f"Error creating disabled message for {symbol}: {e}")
        except Exception as e:
            print(f"Warning: Failed to publish trading status update to Redis: {str(e)}")
            print(traceback.format_exc())
        
        return jsonify({
            'message': f'Trading {"enabled" if enabled else "disabled"}',
            'status': 'success',
            'trading_enabled': enabled
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to toggle trading: {str(e)}'}), 500

@app.route('/api/trading-settings', methods=['POST'])
def update_trading_settings():
    """Update trading settings"""
    try:
        data = request.json
        mode = data.get('mode')
        
        if mode == 'fixed':
            # Update to fixed amount mode
            amount = float(data.get('fixed_amount', 10.0))
            if amount < 1.0:
                return jsonify({'error': 'Fixed amount must be at least $1.00'}), 400
                
            # Update .env file
            update_env_file('TRADE_USE_FIXED', 'true')
            update_env_file('TRADE_FIXED_AMOUNT', str(amount))
            
            # Also update environment variables in memory
            os.environ['TRADE_USE_FIXED'] = 'true'
            os.environ['TRADE_FIXED_AMOUNT'] = str(amount)
            
            # Publish update event to Redis
            try:
                redis_client.publish('settings:update', json.dumps({
                    'mode': 'fixed',
                    'amount': amount
                }))
                print(f"Published settings update to Redis: fixed mode with amount ${amount}")
            except Exception as e:
                print(f"Warning: Failed to publish settings update to Redis: {str(e)}")
            
            return jsonify({
                'message': f'Updated to fixed amount mode (${amount:.2f})',
                'status': 'success'
            })
            
        elif mode == 'percentage':
            # Update to percentage mode
            percentage = float(data.get('percentage', 2.0))
            if percentage <= 0 or percentage > 100:
                return jsonify({'error': 'Percentage must be between 0 and 100'}), 400
                
            # Update .env file
            update_env_file('TRADE_USE_FIXED', 'false')
            update_env_file('TRADE_PERCENTAGE', str(percentage))
            
            # Also update environment variables in memory
            os.environ['TRADE_USE_FIXED'] = 'false'
            os.environ['TRADE_PERCENTAGE'] = str(percentage)
            
            # Publish update event to Redis
            try:
                redis_client.publish('settings:update', json.dumps({
                    'mode': 'percentage',
                    'percentage': percentage
                }))
                print(f"Published settings update to Redis: percentage mode with {percentage}%")
            except Exception as e:
                print(f"Warning: Failed to publish settings update to Redis: {str(e)}")
            
            return jsonify({
                'message': f'Updated to portfolio percentage mode ({percentage:.1f}%)',
                'status': 'success'
            })
            
        else:
            return jsonify({'error': 'Invalid mode specified'}), 400
            
    except Exception as e:
        return jsonify({'error': f'Failed to update settings: {str(e)}'}), 500
        
def update_env_file(key, value):
    """Update a specific key in the .env file"""
    # Try multiple possible locations for the .env file
    possible_paths = [
        # Docker container paths - check these first since we're likely in Docker
        '/app/.env',
        # Direct project root (for Docker)
        '/.env',
        # Standard path calculation for development environment (parent of parent directory)
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'),
        # Current working directory
        os.path.join(os.getcwd(), '.env'),
    ]
    
    # Debug paths
    for path in possible_paths:
        print(f"Checking for .env file at: {path}, exists: {os.path.exists(path)}")
    
    # Find the first existing .env file
    env_file_path = None
    for path in possible_paths:
        if os.path.exists(path):
            env_file_path = path
            print(f"Found .env file at: {env_file_path}")
            break
    
    if env_file_path and os.path.exists(env_file_path):
        try:
            # Read the current content
            with open(env_file_path, 'r') as file:
                lines = file.readlines()
                
            # Replace or add the key
            key_found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                    lines[i] = f"{key}={value}\n"
                    key_found = True
                    break
                    
            # Add the key if not found
            if not key_found:
                lines.append(f"{key}={value}\n")
                
            # Write back to the file
            with open(env_file_path, 'w') as file:
                file.writelines(lines)
                
            print(f"SUCCESS: Updated {key}={value} in .env file at {env_file_path}")
            # Verify the update by reading the file again
            try:
                with open(env_file_path, 'r') as verify_file:
                    content = verify_file.read()
                    if f"{key}={value}" in content:
                        print(f"VERIFICATION: Successfully verified {key}={value} is in the file")
                    else:
                        print(f"WARNING: Could not verify {key}={value} in the file after writing. Content sample: {content[:100]}...")
            except Exception as verify_err:
                print(f"WARNING: Could not verify file contents after update: {verify_err}")
            return True
        except Exception as e:
            print(f"Error updating {key}={value} in .env file at {env_file_path}: {str(e)}")
            return False
    else:
        print(f"Error: .env file not found at any of the expected locations")
        
        # First fallback: try to create a new .env file in the current directory
        fallback_path = os.path.join(os.getcwd(), '.env')
        try:
            print(f"Fallback 1: Attempting to create/update .env file at {fallback_path}")
            # Check if file exists and read existing content
            if os.path.exists(fallback_path):
                with open(fallback_path, 'r') as file:
                    lines = file.readlines()
                # Update or add the key
                key_found = False
                for i, line in enumerate(lines):
                    if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                        lines[i] = f"{key}={value}\n"
                        key_found = True
                        break
                if not key_found:
                    lines.append(f"{key}={value}\n")
                # Write back
                with open(fallback_path, 'w') as file:
                    file.writelines(lines)
            else:
                # Create new file
                with open(fallback_path, 'w') as file:
                    file.write(f"{key}={value}\n")
            print(f"Fallback 1: Created/updated {key}={value} in {fallback_path}")
            return True
        except Exception as e:
            print(f"Fallback 1 failed: {str(e)}")
            
        # Second fallback: Try Docker container path
        fallback_path = "/app/.env"
        try:
            print(f"Fallback 2: Attempting to create/update .env file at {fallback_path}")
            with open(fallback_path, 'a') as file:
                file.write(f"{key}={value}\n")
            print(f"Fallback 2: Appended {key}={value} to {fallback_path}")
            return True
        except Exception as e:
            print(f"Fallback 2 failed: {str(e)}")
            
        # Third fallback: Store in Redis temporarily
        try:
            print(f"Fallback 3: Storing setting in Redis (temporary)")
            redis_key = f"settings:{key}"
            redis_client.set(redis_key, value)
            print(f"Fallback 3: Stored {key}={value} in Redis under key {redis_key}")
            return True
        except Exception as e:
            print(f"Fallback 3 failed: {str(e)}")
            return False

@app.route('/debug')
def debug_page():
    """Debug page with buttons to execute test trades"""
    symbols = os.getenv('SYMBOLS', 'BTC/USD').split(',')
    
    # Add current trading settings for debugging
    fixed_amount_mode = os.getenv('TRADE_USE_FIXED', 'false').lower() == 'true'
    trade_percentage = float(os.getenv('TRADE_PERCENTAGE', '2.0'))
    fixed_amount = float(os.getenv('TRADE_FIXED_AMOUNT', '10.0'))
    trading_enabled = os.getenv('TRADING_ENABLED', 'false').lower() == 'true'
    
    settings_info = f"""
    <div class="settings-panel">
        <h2>Current Trading Settings</h2>
        <ul>
            <li><strong>Trading Enabled:</strong> {trading_enabled}</li>
            <li><strong>Trade Mode:</strong> {'Fixed Amount' if fixed_amount_mode else 'Portfolio Percentage'}</li>
            <li><strong>Fixed Amount:</strong> ${fixed_amount:.2f}</li>
            <li><strong>Trade Percentage:</strong> {trade_percentage:.1f}%</li>
            <li><strong>Environment File:</strong> .env</li>
        </ul>
    </div>
    """
    
    # Define styles separately to avoid f-string/CSS brace conflicts
    styles = '''
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1, h2 { color: #333; }
        .button-row { margin: 10px 0; }
        button { padding: 8px 12px; margin-right: 5px; cursor: pointer; }
        .buy { background-color: #4CAF50; color: white; border: none; }
        .sell { background-color: #f44336; color: white; border: none; }
        .hold { background-color: #2196F3; color: white; border: none; }
        #result { margin-top: 20px; padding: 15px; border: 1px solid #ddd; background: #f9f9f9; white-space: pre-wrap; }
        .settings-panel { 
            background-color: #e9f5ff; 
            padding: 15px; 
            border-radius: 5px;
            margin-bottom: 20px;
            border-left: 4px solid #2196F3;
        }
        .settings-panel h2 { margin-top: 0; }
        .settings-panel ul { padding-left: 20px; }
    '''
    
    # Define JavaScript separately
    script = '''
        async function executeTrade(symbol, decision) {
            document.getElementById('result').textContent = `Executing ${decision} for ${symbol}...`;
            try {
                const response = await fetch(`/debug/execute-trade/${symbol}/${decision}`);
                const data = await response.json();
                document.getElementById('result').textContent = JSON.stringify(data, null, 2);
            } catch (error) {
                document.getElementById('result').textContent = `Error: ${error.message}`;
            }
        }
    '''
    
    # Generate buttons for each symbol
    buttons = ''
    for symbol in symbols:
        buttons += f'''
        <div class="button-row">
            <strong>{symbol}:</strong>
            <button class="buy" onclick="executeTrade('{symbol}', 'buy')">Buy</button>
            <button class="sell" onclick="executeTrade('{symbol}', 'sell')">Sell</button>
            <button class="hold" onclick="executeTrade('{symbol}', 'hold')">Hold</button>
        </div>
        '''
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TraderMagic Debug</title>
        <style>
            {styles}
        </style>
    </head>
    <body>
        <h1>TraderMagic Debug</h1>
        
        {settings_info}
        
        <p>Use the buttons below to simulate trades. These will help test if your trading settings are working correctly.</p>
        
        {buttons}
        
        <div id="result">Results will appear here...</div>
        
        <script>
            {script}
        </script>
    </body>
    </html>
    """

@socketio.on('connect')
def handle_connect(sid=None):
    """Handle client connection"""
    # Send initial data to the client
    socketio.emit('data_update', get_all_trading_data())

if __name__ == '__main__':
    # Start Redis listener in a background thread
    threading.Thread(target=redis_listener, daemon=True).start()
    
    # Start Flask app
    socketio.run(app, host=frontend_host, port=frontend_port, debug=False, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    # Start Redis listener in a background thread
    threading.Thread(target=redis_listener, daemon=True).start()
    
    # Start Flask app
    socketio.run(app, host=frontend_host, port=frontend_port, debug=False, allow_unsafe_werkzeug=True)