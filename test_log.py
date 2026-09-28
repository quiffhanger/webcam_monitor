"""Log webcam events to a file so we can inspect after killing the process"""
import asyncio
import logging
import sys

sys.path.insert(0, r'C:\Users\rossmckerchar\projects')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(r'C:\Users\rossmckerchar\projects\webcam_monitor\test_log.txt', mode='w'),
        logging.StreamHandler(sys.stdout),
    ]
)

from webcam_monitor.webcam import watch_queue

async def main():
    logging.info('Starting - toggle camera on/off...')
    count = 0
    async for webcam_key, key_name, on in watch_queue():
        count += 1
        short = key_name.split('\\')[-1]
        status = 'ON' if on else 'OFF'
        logging.info(f'EVENT #{count} [{status}] {short}')

asyncio.run(main())
