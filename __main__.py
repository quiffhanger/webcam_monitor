import asyncio
import sys
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import logging
import requests
import pystray
from PIL import Image, ImageDraw

from . import webcam
from . import config

#############################################
# Console Window with stdout redirection
#############################################

class ConsoleWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Console Output")
        self.geometry("1024x768")
        # Create the ScrolledText widget and set it to read-only.
        self.text_area = ScrolledText(self, state='disabled')
        self.text_area.pack(fill=tk.BOTH, expand=True)
        # Instead of destroying, just hide the window so the application can continue.
        self.protocol("WM_DELETE_WINDOW", self.hide)
    
    def write(self, message):
        # Append the message in a thread-safe way.
        self.text_area.configure(state='normal')
        self.text_area.insert(tk.END, message)
        self.text_area.see(tk.END)
        self.text_area.configure(state='disabled')
    
    def hide(self):
        self.withdraw()

# Redirect sys.stdout so that anything printed goes to our text area.
class StdoutRedirector:
    def __init__(self, console):
        self.console = console
        self.original_stdout = sys.stdout

    def write(self, message):
        # Write to the Tkinter window (on the main thread) and to the original stdout.
        self.console.write(message)
        self.original_stdout.write(message)

    def flush(self):
        self.original_stdout.flush()

# Custom logging handler to output log messages to the Tkinter text widget.
class TkinterHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record) + "\n"
        # Use the widget's 'after' method to schedule thread-safe GUI updates.
        self.text_widget.after(0, self.append, msg)

    def append(self, msg):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, msg)
        self.text_widget.configure(state='disabled')
        self.text_widget.yview(tk.END)

# Create the console window and override stdout.
console = ConsoleWindow()
sys.stdout = StdoutRedirector(console)
console.withdraw()  # start with the console hidden

#############################################
# Tray Icon Setup (using pystray)
#############################################

def create_image():
    """
    Create an icon image for the system tray.
    Here we create a simple 64x64 blue square with a white circle.
    """
    width, height = 64, 64
    image = Image.new('RGB', (width, height), "blue")
    draw = ImageDraw.Draw(image)
    radius = 20
    center = (width // 2, height // 2)
    draw.ellipse(
        (center[0]-radius, center[1]-radius, center[0]+radius, center[1]+radius),
        fill="white"
    )
    return image

def toggle_console(icon, item):
    """
    Toggle the visibility of the console window.
    (Schedule the call on the Tkinter main loop because Tkinter is not thread-safe.)
    """
    console.after(0, lambda: console.deiconify() if console.state() == 'withdrawn' else console.withdraw())

def quit_app(icon, item):
    """
    Quit the application.
    """
    icon.stop()  # stop the tray icon loop
    console.after(0, console.destroy)  # close the console window (on the main thread)

def setup_tray_icon():
    """
    Set up the tray icon with a menu.
    """
    image = create_image()
    menu = pystray.Menu(
        pystray.MenuItem("Show/Hide Console", toggle_console),
        pystray.MenuItem("Quit", quit_app)
    )
    icon = pystray.Icon("WebcamMonitor", image, "Webcam Monitor", menu)
    return icon

def run_tray_icon():
    """
    Run the system tray icon.
    This call blocks until the icon is stopped.
    """
    icon = setup_tray_icon()
    icon.run()

#############################################
# Async Webcam Monitor & Webhook Caller
#############################################

async def process_webcam_changes():
    async for webcam_key, key_name, on in webcam.watch_queue():
        if on:
            logging.info(f"Webcam in use by {key_name} (key: {webcam_key})")
            call_webhook(config.WEBCAM_ON, {"webcam_key": webcam_key, "key_name": key_name, "status": "on"})
        else:
            logging.info(f"Webcam no longer in use by {key_name} (key: {webcam_key})")
            call_webhook(config.WEBCAM_OFF, {"webcam_key": webcam_key, "key_name": key_name, "status": "off"})

    
def call_webhook(url, data):
    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        logging.debug(f"Webhook called successfully: {response.status_code}")
    except requests.RequestException as e:
        logging.error(f"Error calling webhook: {e}")



def run_webcam_monitor():
    # This function creates and runs an event loop to run the coroutine.
    asyncio.run(process_webcam_changes())

#############################################
# Main Entry Point
#############################################

if __name__ == '__main__':
    # Set up basic logging (the default handler writes to the command window).
    logging.basicConfig(level=config.LOG_LEVEL)
    
    # Add our custom Tkinter logging handler so messages are also sent to the GUI console.
    tk_handler = TkinterHandler(console.text_area)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    tk_handler.setFormatter(formatter)
    tk_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(tk_handler)
    
    # Start the asyncio webcam monitor in a background thread.
    async_thread = threading.Thread(target=run_webcam_monitor, daemon=True)
    async_thread.start()

    # Start the tray icon in a background thread.
    tray_thread = threading.Thread(target=run_tray_icon, daemon=True)
    tray_thread.start()

    # Start the Tkinter mainloop (this call is blocking).
    console.mainloop()
