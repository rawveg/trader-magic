import asyncio
import re
import time
import threading
from typing import Optional, Dict
from datetime import datetime

from src.utils import get_logger, RSIData, TradeSignal, TradingDecision
from src.utils.redis_client import redis_client
from src.ai_decision.ollama_client import ollama_client
from src.config.settings import config

logger = get_logger("ai_decision_service")

# Import version from setup.py
import sys
sys.path.insert(0, '..')
from setup import VERSION

if len(sys.argv) > 1 and sys.argv[1] == '--license':
    print(f"TraderMagic v{VERSION} - Licensed under AGPL-3.0")
    sys.exit(0)

# System prompt for the LLM
SYSTEM_PROMPT = """
You are a world expert at stock and cryptocurrency trading.
Your task is to analyze RSI (Relative Strength Index) data and make trading decisions.
You should respond ONLY with one of these three decisions: "buy", "sell", or "hold".
DO NOT include any explanations, analysis, or additional text in your response.
Just respond with a single word: "buy", "sell", or "hold".

Guidelines for RSI trading:
- RSI below 30 typically indicates oversold conditions (potential buy)
- RSI above 70 typically indicates overbought conditions (potential sell)
- RSI between 30-70 is generally neutral (potential hold)
- Consider recent trends when making decisions
"""

class AIDecisionService:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
    async def analyze_rsi(self, rsi_data: RSIData) -> Optional[TradeSignal]:
        """
        Analyze RSI data and return a trade signal
        
        Args:
            rsi_data: RSI data for analysis
            
        Returns:
            TradeSignal object with the decision
        """
        # Check Redis first for latest trading_enabled status, default to False for safety
        trading_enabled_data = redis_client.get("trading_enabled")
        trading_enabled = trading_enabled_data == "true" if trading_enabled_data is not None else False
        
        if not trading_enabled:
            logger.info(f"Trading is disabled. Skipping signal generation for {rsi_data.symbol}")
            return None
            
        prompt = f"The current Relative Strength Index (RSI) for {rsi_data.symbol} is {rsi_data.value:.2f}. Based on this information alone, should I buy, sell, or hold?"
        
        try:
            response = await ollama_client.generate(prompt, SYSTEM_PROMPT)
            logger.info(f"LLM response for {rsi_data.symbol}: {response}")
            
            # Extract the decision using regex to find 'buy', 'sell', or 'hold'
            match = re.search(r'\b(buy|sell|hold)\b', response.lower())
            if match:
                decision_str = match.group(1)
                
                # Map the decision string to TradingDecision enum
                decision_map = {
                    "buy": TradingDecision.BUY,
                    "sell": TradingDecision.SELL,
                    "hold": TradingDecision.HOLD
                }
                
                decision = decision_map.get(decision_str, TradingDecision.HOLD)
                
                # Create the trade signal
                signal = TradeSignal(
                    symbol=rsi_data.symbol,
                    decision=decision,
                    rsi_value=rsi_data.value
                )
                
                # Store the signal in Redis
                redis_key = f"signal:{rsi_data.symbol}"
                redis_client.set_json(redis_key, signal.dict())
                
                logger.info(f"Generated trade signal for {rsi_data.symbol}: {decision.value}")
                return signal
            else:
                logger.error(f"Could not extract a clear decision from LLM response: {response}")
                return None
                
        except Exception as e:
            logger.error(f"Error analyzing RSI data: {e}")
            return None
    
    def get_decision(self, rsi_data: RSIData) -> Optional[TradeSignal]:
        """
        Get a trading decision based on RSI data
        
        Args:
            rsi_data: RSI data for analysis
            
        Returns:
            TradeSignal object or None if analysis fails
        """
        try:
            return self.loop.run_until_complete(self.analyze_rsi(rsi_data))
        except Exception as e:
            logger.error(f"Error in get_decision: {e}")
            return None
    

    def get_latest_signal(self, symbol: str) -> Optional[TradeSignal]:
        """
        Get the latest trade signal for a symbol from Redis
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            TradeSignal object or None if not found
        """
        redis_key = f"signal:{symbol}"
        data = redis_client.get_json(redis_key)
        if data:
            return TradeSignal(**data)
        return None

# Singleton instance
ai_decision_service = AIDecisionService()

# Add this for module execution
if __name__ == "__main__":
    import time
    logger.info("Starting AI Decision Service as standalone")
    
    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down AI Decision Service")