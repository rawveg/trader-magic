#!/usr/bin/env python3
import time
import redis
import os

# Import version from setup.py
import sys
sys.path.insert(0, '..')
from setup import VERSION

if len(sys.argv) > 1 and sys.argv[1] == '--license':
    print(f"TraderMagic v{VERSION} - Licensed under AGPL-3.0")
    sys.exit(0)

def force_trading_disabled():
    """Force trading to be disabled at startup regardless of config settings"""
    print("SAFETY: Forcing trading to DISABLED state")
    
    # Connect to Redis
    redis_host = os.getenv('REDIS_HOST', 'redis')
    redis_port = int(os.getenv('REDIS_PORT', 6379))
    redis_client = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True)
    
    # Set trading_enabled to false
    for attempt in range(5):
        try:
            redis_client.set("trading_enabled", "false")
            print("Successfully set trading_enabled to false in Redis")
            return True
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
            time.sleep(2)
    
    print("CRITICAL: Failed to disable trading in Redis after multiple attempts")
    return False

if __name__ == "__main__":
    force_trading_disabled()
