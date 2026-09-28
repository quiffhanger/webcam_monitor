"""Minimal test: run the async webcam queue without tkinter/pystray/webhooks"""
import asyncio
import logging
import sys
sys.path.insert(0, r'C:\Users\rossmckerchar\projects')
from webcam_monitor.webcam import watch_queue

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', stream=sys.stdout)

async def main():
    logging.info('Starting - toggle your camera on/off...')
    async for webcam_key, key_name, on in watch_queue():
        short = key_name.split('\\')[-1]
        status = 'ON' if on else 'OFF'
        logging.info(f'[{status}] {short}')

asyncio.run(main())
