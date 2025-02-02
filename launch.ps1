# Function to hide the PowerShell console window
function Hide-Console {
    $consoleWindow = Get-Process -Id $PID | ForEach-Object { $_.MainWindowHandle }
    $SW_HIDE = 0
    Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class Win32 {
            [DllImport("user32.dll")]
            public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        }
"@
    [Win32]::ShowWindow($consoleWindow, $SW_HIDE)
}

# Hide the console window
Hide-Console

# Activate the virtual environment
& webcam_monitor\venv\Scripts\Activate.ps1

# Run the module
python -m webcam_monitor
