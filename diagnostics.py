"""File-only failure reporting, independent of Tk and console handles."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys
import threading


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return re.sub(r'https?://\S+', '[URL redacted]', super().format(record))


def make_logger(path):
    logger = logging.getLogger('monitor.diagnostics.' + str(path))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(RedactingFormatter('%(asctime)s %(levelname)s [%(threadName)s] %(message)s'))
        logger.addHandler(handler)
    return logger


log = make_logger(Path(__file__).with_name('monitor_diagnostics.log'))


def install(console):
    def main_exception(kind, value, traceback):
        log.critical('Unhandled main-thread exception', exc_info=(kind, value, traceback))

    def thread_exception(args):
        log.critical('Unhandled thread exception: %s', args.thread.name if args.thread else 'unknown',
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    def tk_exception(kind, value, traceback):
        log.error('Unhandled Tk callback exception', exc_info=(kind, value, traceback))

    sys.excepthook = main_exception
    threading.excepthook = thread_exception
    console.report_callback_exception = tk_exception


def loop_exception(loop, context):
    error = context.get('exception')
    info = (type(error), error, error.__traceback__) if error is not None else None
    log.error('Async background task failed: %s', context.get('message', 'unknown'), exc_info=info)
