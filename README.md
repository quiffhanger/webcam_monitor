# webcam_monitor
Simple module to watch for devices using your webcam (windows only) and run a webhook if any process turns your camera on or off. Use the registry keys in SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam which windows modifies when a webcam is used.

Runs with tray icon which can be used to open a console to see logs.

Useful to:
1. Turn on lighting to improve camera image
2. Turn on a warning light to notify you/others that you're on camera

## Setup

1. **Clone the repository**:
    ```sh
    git clone https://github.com/yourusername/webcam_monitor.git
    cd webcam_monitor
    ```

2. **Create a virtual environment**:
    ### For Windows:
    ```sh
    python -m venv .venv
    .venv\Scripts\activate
    ```
3. **Install dependencies**:
    ```sh
    pip install -r requirements.txt
    ```

4. **Run the application**:
    ```sh
    python -m webcam_monitor
    ```

## Configuration

Copy [config.py.example](http://_vscodecontentref_/3) to [config.py](http://_vscodecontentref_/4) and update the configuration as needed.

```sh
cp config.py.example config.py

## Launch in background at startup

Stat, run, type shell:startup
Create a new shortcut

powershell.exe -ExecutionPolicy Bypass -File "C:\path_to\webcam_monitor\launch.ps1"
Modify properties of shortcut to "Start in" "C:\path_to" - e.g directory webcam_monitor is in 

This hides the powershell window. To kill it go via the taskbar icon.

NOTE: will only monitor apps that have been installed/granted webcam access before it starts up. Todo - monitor for additional keys that are create after is starts up.