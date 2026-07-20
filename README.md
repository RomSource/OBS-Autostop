A small desktop app with Start/Stop/Calibrate buttons that monitors a video
capture source in OBS for a sustained "blue screen" (the idle/transition
state your Hi8 camera shows when the tape ends or pauses) and automatically
stops recording once that blue screen has held steady for a set duration.

REQUIREMENTS
    pip install obsws-python pillow
    (tkinter ships with standard Python on Windows -- no extra install needed)

SETUP
    1. In OBS: Tools > obs-websocket Settings
         - Enable WebSocket server
         - Note the Port and Password (or disable auth if you prefer)
    2. Run this script:  python obs_autostop_gui.py
    3. Fill in Host/Port/Password and the exact Source name from your OBS
       Sources list.
    4. While the camera is showing the idle blue screen, click
       "Calibrate from live frame" to auto-fill the reference color.
    5. Start your OBS recording manually as usual, then click "Start
       Monitoring" in this app. It will stop the recording automatically
       once blue has held for the configured time, and beep when it does.

<img width="442" alt="image" src="img/Screenshot 2026-07-20 191427.png">
