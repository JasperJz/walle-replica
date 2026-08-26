#############################################
# Wall-e Robot Web-interface
#
# @file       app.py
# @brief      Flask web-interface to control Wall-e robot
# @author     Simon Bluett
# @website    https://wired.chillibasket.com
# @copyright  Copyright (C) 2021-2024 - Distributed under MIT license
# @version    3.0
# @date       9th June 2024
#############################################

from flask import Flask, request, session, redirect, url_for, jsonify, render_template

import os
import sys
from queue import Queue
from threading import Event, Lock, RLock, Thread
from serial import Serial
import serial.tools.list_ports
import subprocess
import time
import tempfile
import atexit
from picamera2_stream import PiCameraStreamer
from track_animations import TrackAnimator, ANIMATIONS, DISPLAY, animation_length
import logging
from waitress import serve


app = Flask(__name__)

# Load the configurations
if os.path.isfile("local_config.py"):
    app.config.from_pyfile("local_config.py")
else:
    app.config.from_pyfile("config.py")

# Set up global variables
volume: int = 8
startup: bool = False
camera: PiCameraStreamer = PiCameraStreamer()

# Set up logging
logger = logging.getLogger()
stream_handler = logging.StreamHandler(sys.stdout)

if app.config['APP_DEBUG']:
    logger.setLevel(logging.DEBUG)
    stream_handler.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)
    stream_handler.setLevel(logging.INFO)

logger.addHandler(stream_handler)



###############################################################
#
# Arduino Device Class
#
###############################################################

class ArduinoDevice:
    """Class used for managing communication with the Arduino"""

    _conn_lock = RLock()

    # ---------------------------------------------------------
    def __init__(self):
        """
        Constructor for Arduino serial communication thread class
        :param port:     The serial port where the Arduino is connected
        """
        self.queue: Queue = Queue()
        self.exit_flag: Event = Event()
        self.port_name: str = ""
        self.serial_port: Serial | None = None
        self.serial_thread: Thread | None = None
        self.battery_level: str | None = None
        self.exit_flag.clear()

    # ---------------------------------------------------------
    def __del__(self):
        """Destructor - ensures serial port is closed correctly"""
        self.disconnect()

    # ---------------------------------------------------------
    def connect(self, port: str | int = "") -> bool:
        """
        Connect to the serial port
        :param port: The port to connect to (leave blank to use previous port)
        :return: True if connected successfully, False otherwise
        """
        # _conn_lock: connect/disconnect 可能由 启动自连线程/路由线程 并发进入,
        # 双开同一串口 = 第二次 DTR 复位(恰是浪涌源头), 必须互斥。
        # RLock: connect() 内部会先调 self.disconnect() 清场, 需可重入。
        with self._conn_lock:
            try:
                usb_ports = [
                    p.device for p in serial.tools.list_ports.comports()
                ]

                if type(port) is str and port == "":
                    port = self.port_name

                if type(port) is int and port >= 0 and port < len(usb_ports):
                    port = usb_ports[port]

                # Check port exists and we are not already connected
                if ((not self.is_connected() or port != self.port_name) and port in usb_ports):

                   # Ensure old port is properly disconnected first
                    self.disconnect()

                    # Connect to the new port
                    # timeout: 防 USB 半死时 read/write 永久阻塞挂死线程
                    self.serial_port = Serial(port, 115200, timeout=0.1, write_timeout=1)
                    self.serial_port.flushInput()
                    self.port_name = port

                    # Start the command handler in a background thread
                    self.exit_flag.clear()
                    # daemon=True: 保证进程退出路径可达(atexit 先 join 非 daemon 线程,
                    # 通信线程只认 exit_flag, 非 daemon 会让整个进程退出时挂死)
                    self.serial_thread = Thread(target = self.__communication_thread, daemon=True)
                    self.serial_thread.start()

            except Exception as ex:
                logger.error(f'Serial connect error: {repr(ex)}')

        return self.is_connected()

    # ---------------------------------------------------------
    def disconnect(self) -> bool:
        """
        Disconnect from the serial port
        :return: True if disconnected successfully, False otherwise
        """
        # 与 connect() 同锁(见 connect() 内注释); RLock 保证 connect 内部
        # 的清场调用可重入
        with self._conn_lock:
            try:
                self.battery_level = None

                if self.serial_thread is not None:
                    self.exit_flag.set()
                    self.serial_thread.join(timeout=5)
                    self.serial_thread = None

                # 断开时清空队列, 防止下次连接时重放陈旧命令
                # (放在 close() 之前, 即使 close 抛异常队列也已清空)
                self.clear_queue()

                # 停机帧必须在 join 之后直写: 此时通信线程已死、is_connected() 为 False,
                # 任何生产者的 send_command 都会失效, 这两条必然是线上最后的命令
                # (原版固件无失联保护, 最后一条 X/Y 永久生效)
                if self.serial_port is not None and self.serial_port.is_open:
                    try:
                        self.serial_port.write(b"X0\n")
                        self.serial_port.write(b"Y0\n")
                    except Exception:
                        pass

                if self.serial_port is not None:
                    self.serial_port.close()
                    self.serial_port = None

            except Exception as ex:
                logger.error(f'Serial disconnect error: {repr(ex)}')

        return (self.serial_thread is None and self.serial_port is None)

    # ---------------------------------------------------------
    def is_connected(self) -> bool:
        """
        Check if serial device is connected
        :return: True if connected, False otherwise
        """
        return (self.serial_thread is not None and self.serial_thread.is_alive()
             and self.serial_port is not None and self.serial_port.is_open)

    # ---------------------------------------------------------
    def send_command(self, command: str) -> bool:
        """
        Send a serial command
        :param command: The command to be sent
        :return: True if port is open and message has been added to queue
        """
        success = False

        if self.is_connected():
            self.queue.put(command)
            success = True

        return success

    # ---------------------------------------------------------
    def clear_queue(self):
        """
        Clear the serial send queue
        """
        while not self.queue.empty():
            self.queue.get()

    # ---------------------------------------------------------
    def get_battery_level(self) -> str | None:
        """
        Get the robot battery level
        :return: The battery level as a string, or None
        """
        return self.battery_level

    # ---------------------------------------------------------
    def __communication_thread(self):
        """
        Handle sending and receiving data with the serial device.
        Auto-reconnects if the serial port drops (USB power glitch etc).
        """
        dataString: str = ""
        logger.info(f'Starting Arduino Thread ({self.port_name})')

        while not self.exit_flag.is_set():
            try:
                # Check if serial port is still alive; if not, attempt reconnection
                if self.serial_port is None or not self.serial_port.is_open:
                    logger.warning('Serial port lost, attempting reconnection...')
                    reconnect_attempts = 0
                    while not self.exit_flag.is_set():
                        if self.__reconnect_serial():
                            break
                        reconnect_attempts += 1
                        if reconnect_attempts == 1:
                            logger.warning('Reconnect failed, will keep retrying every 2s')
                        self._sleep_check(2.0)
                    continue

                # If there are any messages in the queue, send them
                if not self.queue.empty():
                    data = self.queue.get() + '\n'
                    self.serial_port.write(data.encode())

                # Read any incoming messages
                if self.serial_port.in_waiting > 0:
                    while (self.serial_port.in_waiting > 0):
                        data = self.serial_port.read()
                        if (data.decode() == '\n' or data.decode() == '\r'):
                            self.__parse_message(dataString)
                            dataString = ""
                        else:
                            dataString += data.decode()
                            # 防御: 设备持续输出无换行垃圾时, 行缓冲不能无限增长
                            if len(dataString) > 256: dataString = ""

            except Exception as ex:
                logger.error(f'Serial handler error: {repr(ex)}')
                try:
                    if self.serial_port is not None:
                        self.serial_port.close()
                except Exception:
                    pass
                self._sleep_check(1.0)

            time.sleep(0.01)

        logger.info(f'Stopping Arduino Thread ({self.port_name})')

    def __reconnect_serial(self):
        """
        Try to reopen the serial port after a disconnect.
        Scans USB ports, looks for the Arduino, and reconnects.
        """
        try:
            if self.serial_port is not None:
                try:
                    self.serial_port.close()
                except Exception:
                    pass
                self.serial_port = None

            self._sleep_check(1.0)
            if self.exit_flag.is_set():
                return False

            usb_ports = [p.device for p in serial.tools.list_ports.comports()]
            port_to_try = self.port_name

            if port_to_try not in usb_ports:
                arduino_ports = [
                    p.device for p in serial.tools.list_ports.comports()
                    if 'Arduino' in (p.description or '')
                    or '2341' in (p.hwid or '')
                    or 'CDC' in (p.description or '')
                ]
                if arduino_ports:
                    port_to_try = arduino_ports[0]
                    logger.info(f'Arduino port changed to {port_to_try}')
                else:
                    return False

            self.serial_port = Serial(port_to_try, 115200, timeout=0.1, write_timeout=1)
            self.serial_port.flushInput()
            self.port_name = port_to_try
            # 丢弃断线期间堆积的陈旧运动命令: 重开串口会 DTR 复位 UNO,
            # 固件无失联保护, 旧帧重放到重启后的固件上会导致电机失控
            self.clear_queue()
            self.queue.put("X0")
            self.queue.put("Y0")
            logger.info(f'Serial reconnected on {port_to_try}')
            return True

        except Exception as ex:
            logger.warning(f'Reconnect attempt failed: {repr(ex)}')
            return False

    def _sleep_check(self, secs):
        """Sleep that respects exit_flag."""
        end = time.time() + secs
        while time.time() < end and not self.exit_flag.is_set():
            time.sleep(0.1)

    def __parse_message(self, dataString: str):
        """
        Parse messages received from the connected device
        :param dataString: String containing the serial message to be parsed
        """
        try:
            # Battery level message
            if "Battery" in dataString:
                dataList = dataString.split('_')
                if len(dataList) > 1 and dataList[1].isdigit():
                    self.battery_level = dataList[1]

        except Exception as ex:
            logger.error(f'Error parsing message [{dataString}]: {repr(ex)}')

# End of class: ArduinoDevice



# 启动自连线程的取消标志: 服务关停(finally)或用户手动操作串口(/settings)时置位
_boot_cancel: Event = Event()

arduino: ArduinoDevice = ArduinoDevice()

track_animator: TrackAnimator = TrackAnimator(arduino)

# 进程退出(systemctl stop / Ctrl-C)时兜底断开: disconnect 会尽力补发停机帧,
# 防止动画播放中重启服务导致机器人带着最后速度跑走
atexit.register(arduino.disconnect)


###############################################################
#
# Flask Pages and Functions
#
###############################################################

@app.route('/')
def index():
    """
    Show the main web-interface page
    :return: Render HTML template for the webpage
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    files = []
    errors = []

    # Get list of audio files
    try:
        for item in sorted(os.listdir(app.config['SOUND_FOLDER'])):
            if item.endswith(f".{app.config['SOUND_FORMAT']}"):
                audiofiles = os.path.splitext(os.path.basename(item))[0]

                # Set up default details
                audiogroup = "Other"
                audionames = audiofiles
                audiotimes = 0

                audio_details = audiofiles.split('_')

                # Get item details from name, and make sure they are valid
                if len(audio_details) == 2:
                    if audio_details[1].isdigit():
                        audionames = audio_details[0]
                        audiotimes = float(audio_details[1]) / 1000.0
                    else:
                        audiogroup = audio_details[0]
                        audionames = audio_details[1]
                elif len(audio_details) == 3:
                    audiogroup = audio_details[0]
                    audionames = audio_details[1]
                    if audio_details[2].isdigit():
                        audiotimes = float(audio_details[2]) / 1000.0

                # Add the details to the list
                files.append((audiogroup, audiofiles, audionames, audiotimes))

    except Exception as ex:
        errors.append(repr(ex))
        logging.error(f'Failed to initialise audio files: {repr(ex)}')

    # Get list of connected USB devices
    ports = serial.tools.list_ports.comports()
    usb_ports = [
        p.device
        for p in ports
    ]

    # Ensure that the preferred Arduino port is selected by default
    selectedPort: int = 0
    for index, item in enumerate(usb_ports):
        if app.config['ARDUINO_PORT'] in item:
            selectedPort = index
            logger.info(f'Found serial port ({item}) index [{index}]')
            break

    # Automatically connect systems on startup
    global startup
    global arduino
    global camera

    if not startup:
        startup = True

        try:
            # If user has selected for the Arduino to connect by default, do so now
            if app.config['AUTOSTART_ARDUINO'] and selectedPort < len(usb_ports):
                if arduino.connect(selectedPort):
                    logging.info("Auto-start Complete: Arduino communication")
                else:
                    logging.warning("Auto-start Failed: Arduino communication")

            # If user has selected for the camera stream to be active by default, turn it on now
            if app.config['AUTOSTART_CAM'] and not camera.is_stream_active():
                if camera.start_stream():
                    logging.info("Auto-start Complete: Camera stream")
                else:
                    logging.warning("Auto-start Failed: Camera stream")

        except Exception as ex:
            errors.append(repr(ex))
            logging.error(f'Auto-start Error: {repr(ex)}')

    return render_template('index.html',
                           sounds=files,
                           ports=usb_ports,
                           portSelect=selectedPort,
                           connected=arduino.is_connected(),
                           cameraActive=camera.is_stream_active(),
                           trackItems=[{'name': n,
                                        'label': DISPLAY.get(n, n),
                                        'secs': animation_length(n)}
                                       for n in ANIMATIONS],
                           errorMessages=errors)


# =============================================================
@app.route('/login')
def login():
    """
    Show the Login page
    :return: Render HTML template for login page
    """
    if session.get('active'):
        return redirect(url_for('index'))
    else:
        return render_template('login.html', incorrectPassword=False)


# =============================================================
@app.route('/login_request', methods=['POST'])
def login_request():
    """
    Check if the login password is correct
    :return: Redirect to dashboard or login page
    """
    password = request.form.get('password')
    if password == app.config['LOGIN_PASSWORD']:
        session['active'] = True
        return redirect(url_for('index'))
    return render_template('login.html', incorrectPassword=True)


# =============================================================
@app.route('/motor', methods=['POST'])
def motor():
    """
    Control the main movement motors
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    global arduino
    stickX = request.form.get('stickX')
    stickY = request.form.get('stickY')

    if stickX is not None and stickY is not None:

        # 手动输入优先: 任何摇杆/手柄输入立即抢占正在播放的履带动画
        # (先停动画再发本条命令, 保证最终目标是操作者的手)
        if track_animator.is_running():
            track_animator.stop()
        xVal = int(float(stickX) * 100)
        yVal = int(float(stickY) * 100)

        if arduino.is_connected():
            arduino.send_command("X" + str(xVal))
            arduino.send_command("Y" + str(yVal))
            return jsonify({'status': 'OK'})
        else:
            return jsonify({'status': 'Error', 'msg': 'Arduino not connected'})
    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})


# =============================================================
@app.route('/settings', methods=['POST'])
def settings():
    """
    Update Settings
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    global arduino
    thing = request.form.get('type')
    value = request.form.get('value')

    if thing is not None and value is not None:
        # Motor deadzone threshold
        if thing == "motorOff":
            logging.info(f'Motor Offset: {value}')
            if arduino.is_connected():
                arduino.send_command("O" + value)
            else:
                return jsonify({'status': 'Error', 'msg': 'Arduino not connected'})

        # Motor steering offset/trim
        elif thing == "steerOff":
            logging.info(f'Steering Offset: {value}')
            if arduino.is_connected():
                arduino.send_command("S" + value)
            else:
                return jsonify({'status': 'Error', 'msg': 'Arduino not connected'})

        # Automatic/manual animation mode
        elif thing == "animeMode":
            logging.info(f'Animation Mode: {value}')
            if arduino.is_connected():
                arduino.send_command("M" + value)
            else:
                return jsonify({'status': 'Error', 'msg': 'Arduino not connected'})

        # Sound mode currently doesn't do anything
        # elif thing == "soundMode":
            # logger.debug(f"Sound Mode: {value}")

        # Change the sound effects volume
        elif thing == "volume":
            global volume
            volume = int(value)

        # Turn on/off the webcam
        elif thing == "streamer":
            logging.info("Turning on/off MJPG Streamer")
            global camera
            result: int = 0

            if not camera.is_stream_active():
                response, error = camera.start_stream()
                if response:
                    time.sleep(1) # Give time for the stream to start fully
                    return jsonify({'status': 'OK', 'streamer': 'Active'})
                else:
                    return jsonify({'status': 'Error', 'msg': f'Unable to start stream: {error}'})

            else:
                if camera.stop_stream():
                    return jsonify({'status': 'OK', 'streamer': 'Offline'})
                else:
                    return jsonify({'status': 'Error', 'msg': 'Unable to stop the stream'})

        # Restart the web-interface
        elif thing == "restart":
            command = "sleep 5 && sudo systemctl restart --quiet walle"
            subprocess.Popen(command, shell=True)
            return redirect(url_for('login'))

        # Shut down the Raspberry Pi
        elif thing == "shutdown":
            logging.info("Shutting down Raspberry Pi!")
            subprocess.run(['sudo', 'nohup', 'shutdown', '-h', 'now'], stdout=subprocess.PIPE).stdout.decode('utf-8')
            return jsonify({'status': 'OK', 'msg': 'Raspberry Pi is shutting down'})

        # Unknown command
        else:
            return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})

        return jsonify({'status': 'OK'})
    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})


# =============================================================
@app.route('/audio', methods=['POST'])
def audio():
    """
    Play an Audio clip on the Raspberry Pi
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    clip = request.form.get('clip')
    if clip is not None:
        clip = f"{app.config['SOUND_FOLDER']}{clip}.{app.config['SOUND_FORMAT']}"

        # Volume control only on linux via amixer
        if sys.platform == "linux":
            audiomixer_cmd = ["amixer", "sset", "Master", "{}%".format(volume * 10)]
            subprocess.run(audiomixer_cmd,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)

        p = subprocess.Popen(app.config['AUDIOPLAYER_CMD'] + [clip],
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)

        if app.config['APP_DEBUG']:
            p.wait()
            if p.stderr is not None:
                logger.error(p.stderr.readlines())
            if p.stdout is not None:
                logger.info(p.stdout.readlines())

        return jsonify({'status': 'OK'})
    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})


# =============================================================
@app.route('/tts', methods=['POST'])
def tts():
    """
    Text to Speech on the Raspberry Pi
    Requires Espeak-NG and optionally Rubberband
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    text = request.form.get('text')

    # Shell commands
    espeak_cmd = app.config['ESPEAK_CMD']
    rb_cmd = app.config['RB_CMD']

    # Don't react to empty strings
    if text is not None and text != "":

        infile = tempfile.NamedTemporaryFile()
        outfile = tempfile.NamedTemporaryFile()

        text_e = text.encode('utf8')
        espeak_args = ['-w', infile.name, text_e]

        try:
            # Generate Speech
            subprocess.run(espeak_cmd + espeak_args,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)

            if not rb_cmd:
                outfile = infile

            else:
                # Shift pitch
                subprocess.run(rb_cmd + [infile.name, outfile.name],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)

            # Volume control only on linux via amixer
            if sys.platform == "linux":
                audiomixer_cmd = ["amixer", "sset", "Master", "{}%".format(volume * 10)]
                subprocess.run(audiomixer_cmd,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)

            # Play it
            subprocess.run(app.config['AUDIOPLAYER_CMD'] + [outfile.name],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)

        finally:
            infile.close()
            outfile.close()

        return jsonify({'status': 'OK'})
    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})


# =============================================================
@app.route('/animate', methods=['POST'])
def animate():
    """
    Send an Animation command to the Arduino
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    global arduino
    clip = request.form.get('clip')

    if clip is not None:
        logger.debug(f"Animate: {clip}")

        if arduino.is_connected():
            arduino.send_command("A" + clip)
            return jsonify({'status': 'OK'})
        else:
            return jsonify({'status': 'Error', 'msg': 'Arduino not connected'})
    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})


# =============================================================
@app.route('/track', methods=['POST'])
def track():
    """
    Play a track (caterpillar) character animation
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    name = request.form.get('name')

    if name is not None:
        logger.debug(f"Track: {name}")

        if track_animator.play(name):
            return jsonify({'status': 'OK'})
        else:
            return jsonify({'status': 'Error', 'msg': 'Animation not found or Arduino not connected'})
    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})


# =============================================================
@app.route('/track/stop', methods=['POST'])
def track_stop():
    """
    Stop the current track animation and halt the motors
    :return: JSON response
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    track_animator.stop()
    return jsonify({'status': 'OK'})


# =============================================================
@app.route('/servoControl', methods=['POST'])
def servoControl():
    """
    Send a Servo Control command to the Arduino
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    global arduino
    servo = request.form.get('servo')
    value = request.form.get('value')

    if servo is not None and value is not None:
        logger.debug(f"servo: {servo}")
        logger.debug(f"value: {value}")

        if arduino.is_connected():
            arduino.send_command(servo + value)
            return jsonify({'status': 'OK'})
        else:
            return jsonify({'status': 'Error', 'msg': 'Arduino not connected'})
    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})


# =============================================================
@app.route('/arduinoConnect', methods=['POST'])
def arduinoConnect():
    """
    Connect/Disconnect the Arduino Serial Port
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    global arduino
    action = request.form.get('action')

    if action is not None:
        # Update drop-down selection with list of connected USB devices
        if action == "updateList":
            logger.debug("Reload list of connected USB ports")

            # Get list of connected USB devices
            ports = serial.tools.list_ports.comports()
            usb_ports = [p.device for p in ports]

            # Ensure that the preferred Arduino port is selected by default
            selectedPort = 0
            for index, item in enumerate(usb_ports):
                if app.config['ARDUINO_PORT'] in item:
                    selectedPort = index
                    break

            return jsonify({'status': 'OK', 'ports': usb_ports, 'portSelect': selectedPort})

        # If we want to connect/disconnect Arduino device
        elif action == "reconnect":

            logger.debug("Reconnect to Arduino")
            # 用户手动操作串口 = 明确意图, 取消启动自连线程的重试
            _boot_cancel.set()

            if arduino.is_connected():
                arduino.disconnect()
                return jsonify({'status': 'OK', 'arduino': 'Disconnected'})

            else:
                port = request.form.get('port')

                if port is not None and port.isdigit():
                    portNum = int(port)

                    # Test whether connection to the selected port is possible
                    ports = serial.tools.list_ports.comports()
                    usb_ports = [p.device for p in ports]

                    if portNum >= 0 and portNum < len(usb_ports):
                        # Try opening and closing port to see if connection is possible
                        try:
                            ser = serial.Serial(usb_ports[portNum], 115200)
                            if (ser.inWaiting() > 0):
                                ser.flushInput()
                            ser.close()
                            arduino.connect(usb_ports[portNum])
                            return jsonify({'status': 'OK', 'arduino': 'Connected'})
                        except:
                            return jsonify({'status': 'Error', 'msg': 'Unable to connect to selected serial port'})
                    else:
                        return jsonify({'status': 'Error', 'msg': 'Invalid serial port selected'})
                else:
                    return jsonify({'status': 'Error', 'msg': 'Unable to read [port] POST data'})
        else:
            return jsonify({'status': 'Error', 'msg': 'Unable to read [action] POST data'})
    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read [action] POST data'})


# =============================================================
@app.route('/arduinoStatus', methods=['POST'])
def arduinoStatus():
    """
    Update the Arduino Status
    :return: JSON containing the current battery level, or an error
    """

    if not session.get('active'):
        return redirect(url_for('login'))

    global arduino
    action = request.form.get('type')

    if action is not None:
        if action == "battery":
            
            battery_level = arduino.get_battery_level()

            if arduino.is_connected():
                if battery_level is not None:
                    return jsonify({'status': 'OK', 'battery': battery_level})
                else:
                    return jsonify({'status': 'Info', 'msg': 'No battery level available'})
            else:
                return jsonify({'status': 'Error', 'msg': 'Arduino not connected'})

    return jsonify({'status': 'Error', 'msg': 'Unable to read POST data'})



###############################################################
#
# WiFi Configuration (NetworkManager)
#
# Works together with the "WALL-E-Setup" fallback hotspot
# profile on the Pi: when no known WiFi is reachable the
# hotspot comes up automatically (autoconnect-priority -999).
# Join the hotspot, open this page and hand the Pi new WiFi
# credentials - no SSH required.
#
###############################################################

HOTSPOT_PROFILE = "WALL-E-Setup"

# Let the HTTP response reach the client before the radio switches:
# when handing over from hotspot to WiFi this host disappears instantly
_WIFI_SWITCH_DELAY = 4

_wifi_switch_lock = Lock()


def _nmcli(args, timeout=40):
    """
    Run nmcli as root (passwordless sudo is configured for this user)
    :param args:    nmcli argument list (arg-list execution, never a shell string)
    :param timeout: nmcli calls must never hang a request
    :return:        (returncode, stdout, stderr); rc 124 on timeout
    """
    try:
        result = subprocess.run(["sudo", "-n", "nmcli"] + args,
                                capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "nmcli timeout"


def _terse_split(line):
    """
    Split an nmcli terse (-t) output line: values escape ':' as '\\:' and
    '\\' as '\\\\', so split on unescaped colons only and unescape each field
    :param line: one output line without the trailing newline
    :return:      list of unescaped field values
    """
    fields, buf, i = [], "", 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            buf += line[i:i + 2]
            i += 2
        elif line[i] == ":":
            fields.append(buf)
            buf = ""
            i += 1
        else:
            buf += line[i]
            i += 1
    fields.append(buf)
    return [f.replace("\\:", ":").replace("\\\\", "\\") for f in fields]


def _wifi_status_data():
    """
    Collect wlan0 status and the list of saved WiFi profiles
    :return: dict with mode/connection/state/ip/saved keys
    """
    data = {"mode": "unknown", "connection": None, "state": None,
            "ip": None, "saved": []}

    code, out, _ = _nmcli(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION",
                           "device", "status"])
    if code == 0:
        for line in out.splitlines():
            fields = _terse_split(line)
            if len(fields) >= 4 and fields[0] == "wlan0":
                data["state"] = fields[2]
                data["connection"] = None if fields[3] == "--" else ":".join(fields[3:])
                break

    if data["connection"]:
        code, out, _ = _nmcli(["-g", "802-11-wireless.mode", "connection",
                               "show", data["connection"]])
        if code == 0 and out.strip():
            data["mode"] = out.strip()

    code, out, _ = _nmcli(["-g", "IP4.ADDRESS", "device", "show", "wlan0"])
    if code == 0 and out.strip():
        data["ip"] = " | ".join(x for x in out.split() if x)

    code, out, _ = _nmcli(["-t", "-f", "NAME,TYPE,AUTOCONNECT",
                           "connection", "show"])
    if code == 0:
        for line in out.splitlines():
            fields = _terse_split(line)
            if len(fields) >= 3 and fields[1] == "802-11-wireless":
                data["saved"].append({
                    "name": fields[0],
                    "autoconnect": fields[2] == "yes",
                    "active": fields[0] == data["connection"],
                    "hotspot": fields[0] == HOTSPOT_PROFILE,
                })

    return data


def _wifi_switch_worker(ssid, psk, hidden):
    """
    Create or update the WiFi profile and activate it (runs in background)
    :param ssid:    network name (doubles as the profile name)
    :param psk:     password (empty string for open networks)
    :param hidden:  whether the network does not broadcast its SSID
    """
    try:
        time.sleep(_WIFI_SWITCH_DELAY)

        # Open networks carry no wifi-sec setting at all ('open' is not a
        # valid key-mgmt value); an existing profile keeps stale security
        # until the whole setting is removed below
        security = (["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
                    if psk else [])
        hidden_arg = ["802-11-wireless.hidden", "yes" if hidden else "no"]

        # A profile already exists under this name; only ever touch WiFi
        # profiles, and force ssid/mode so a profile whose stored SSID or
        # mode diverges from its name cannot hijack the switch
        code, out, _ = _nmcli(["-g", "connection.type", "connection", "show", ssid])
        if code == 0:
            if out.strip() != "802-11-wireless":
                logger.warning(f'WiFi switch: profile [{ssid}] exists but is not a WiFi profile - skipped')
                return
            code, _, err = _nmcli(["connection", "modify", ssid,
                                   "802-11-wireless.ssid", ssid,
                                   "802-11-wireless.mode", "infrastructure",
                                   "autoconnect", "yes"]
                                  + security + hidden_arg)
            if code == 0 and not psk:
                code, _, err = _nmcli(["connection", "modify", ssid,
                                       "remove", "802-11-wireless-security"])
        else:
            code, _, err = _nmcli(["connection", "add", "type", "wifi",
                                   "ifname", "wlan0", "con-name", ssid,
                                   "ssid", ssid, "autoconnect", "yes"]
                                  + security + hidden_arg)

        if code != 0:
            logger.warning(f'WiFi switch: profile update failed: {err.strip()}')
            return

        code, _, err = _nmcli(["connection", "up", ssid], timeout=120)
        if code == 0:
            logger.info(f'WiFi switch: connected to {ssid}')
        else:
            # Wrong password etc. - NetworkManager re-activates the fallback
            # hotspot, so this page stays reachable for a retry
            logger.warning(f'WiFi switch: connection up failed: {err.strip()}')

    except Exception as ex:
        logger.error(f'WiFi switch error: {repr(ex)}')
    finally:
        _wifi_switch_lock.release()


def _wifi_psk_error(psk):
    """
    Password validation shared by the connect/edit actions
    :param psk: password as submitted (empty string = open network)
    :return:    None when valid, otherwise an error message
    """
    if psk and not (8 <= len(psk) <= 63 and psk.isascii() and psk.isprintable()):
        return 'WPA password must be 8-63 printable ASCII characters (empty = open network)'
    return None


def _wifi_editable_profile(name):
    """
    Check that [name] refers to a saved WiFi profile this page may edit or
    delete: it must exist, be a WiFi profile, be addressed by its exact
    profile name (the 'id' selector plus resolved-id check keeps UUID /
    D-Bus path aliases from slipping past the hotspot guard) and never be
    the fallback hotspot (deleting that would brick the offline setup path)
    :param name: profile name exactly as shown in the saved list
    :return:     (True, name) when editable, else (False, error message)
    """
    if (not name.strip() or len(name.encode("utf-8")) > 64
            or name.startswith("-") or name.startswith("/")
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        return False, 'Invalid profile name'
    if name == HOTSPOT_PROFILE:
        return False, 'This is the robot\'s own setup hotspot - it cannot be reconfigured here'
    code, out, _ = _nmcli(["-g", "connection.id,connection.type",
                           "connection", "show", "id", name])
    if code != 0:
        return False, f'No saved WiFi profile named [{name}] (or the name is ambiguous)'
    # '-g' prints one field per line and escapes ':' as '\:' and '\' as
    # '\\' inside values, so unescape each line via _terse_split and keep
    # the comparison byte-exact (no strip - leading/trailing spaces are
    # legal name bytes, the connect path deliberately preserves them)
    lines = out.splitlines()
    resolved = [_terse_split(ln)[0] if ln.strip() else "" for ln in lines]
    if len(lines) < 2 or lines[1].strip() != "802-11-wireless":
        return False, f'[{name}] is not a WiFi profile'
    if sum(1 for r in resolved if r == name) != 1:
        return False, f'[{name}] is ambiguous (multiple profiles share this name) - remove the duplicates via SSH'
    if resolved[0] != name:
        return False, 'Address the profile by its exact name'
    return True, name


def _wifi_active_connection():
    """
    Name of the connection currently active on wlan0
    :return: (True, name-or-None) when the query succeeded (None = nothing
             active on wlan0); (False, None) when the query itself failed -
             callers must not mutate anything in that case, because active
             and inactive are then indistinguishable
    """
    code, out, _ = _nmcli(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION",
                           "device", "status"])
    if code != 0:
        return False, None
    for line in out.splitlines():
        fields = _terse_split(line)
        if len(fields) >= 4 and fields[0] == "wlan0":
            return True, (None if fields[3] == "--" else ":".join(fields[3:]))
    return False, None


def _wifi_apply_psk(name, psk):
    """
    Store a new password in the saved profile [name] (empty = open network)
    :return: (returncode, stderr) of the nmcli call
    """
    if psk:
        code, _, err = _nmcli(["connection", "modify", "id", name,
                               "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk])
        return code, err
    code, _, err = _nmcli(["connection", "modify", "id", name,
                           "remove", "802-11-wireless-security"])
    return code, err


def _wifi_password_worker(name, psk):
    """
    Apply a new password to the profile that is currently active and
    re-activate it, so wrong credentials surface immediately (background)
    :param name: profile/connection name
    :param psk:  new password (empty string for open networks)
    """
    try:
        time.sleep(_WIFI_SWITCH_DELAY)

        # The active connection may have changed during the delay; when this
        # profile is no longer the one in use, only store the password
        # instead of yanking the radio back to it
        ok, conn = _wifi_active_connection()
        code, err = _wifi_apply_psk(name, psk)
        if code != 0:
            logger.warning(f'WiFi password: update failed: {err.strip()}')
            return
        if not ok or conn != name:
            logger.info(f'WiFi password: stored for {name} (no longer active, not reconnecting)')
            return

        code, _, err = _nmcli(["connection", "up", "id", name], timeout=120)
        if code == 0:
            logger.info(f'WiFi password: reconnected to {name}')
        else:
            # Wrong password etc. - NetworkManager re-activates the fallback
            # hotspot, so this page stays reachable for a retry
            logger.warning(f'WiFi password: reconnect failed: {err.strip()}')

    except Exception as ex:
        logger.error(f'WiFi password error: {repr(ex)}')
    finally:
        _wifi_switch_lock.release()


def _wifi_forget_worker(name):
    """
    Delete the profile that is currently active after the HTTP response has
    gone out (runs in background)
    :param name: profile/connection name to delete
    """
    try:
        time.sleep(_WIFI_SWITCH_DELAY)

        code, _, err = _nmcli(["connection", "delete", "id", name])
        if code == 0:
            logger.info(f'WiFi forget: deleted profile {name}')
        else:
            logger.warning(f'WiFi forget: delete failed: {err.strip()}')

    except Exception as ex:
        logger.error(f'WiFi forget error: {repr(ex)}')
    finally:
        _wifi_switch_lock.release()


# =============================================================
@app.route('/wifi')
def wifi_page():
    """
    Show the WiFi configuration page
    :return: Render HTML template for the webpage
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    return render_template('wifi.html')


# =============================================================
@app.route('/wifi/api', methods=['POST'])
def wifi_api():
    """
    WiFi status / scan / connect via NetworkManager
    :return: JSON response with success or error status
    """
    if not session.get('active'):
        return redirect(url_for('login'))

    action = request.form.get('action')

    if action == "status":
        return jsonify({'status': 'OK', 'data': _wifi_status_data()})

    elif action == "scan":
        code, out, err = _nmcli(["-t", "-f", "SSID,SIGNAL,SECURITY",
                                 "device", "wifi", "list", "--rescan", "yes"],
                                timeout=45)
        if code != 0:
            return jsonify({'status': 'Error',
                            'msg': 'Scan failed - enter the SSID manually'})

        networks = {}
        for line in out.splitlines():
            fields = _terse_split(line)
            # '--' is nmcli's placeholder for the unknown SSID of a hidden network
            if (len(fields) == 3 and fields[0]
                    and fields[0] != "--" and fields[0] != HOTSPOT_PROFILE):
                signal = int(fields[1]) if fields[1].isdigit() else 0
                if fields[0] not in networks or signal > networks[fields[0]][0]:
                    networks[fields[0]] = (signal, fields[2])

        return jsonify({'status': 'OK', 'data': [
            {"ssid": s, "signal": networks[s][0], "security": networks[s][1]}
            for s in sorted(networks, key=lambda s: -networks[s][0])
        ]})

    elif action == "connect":
        # Keep the SSID exactly as entered (leading/trailing spaces are
        # legal SSID bytes); only use strip() for the emptiness check
        ssid = request.form.get('ssid') or ""
        psk = request.form.get('password') or ""
        hidden = request.form.get('hidden') == "true"

        # Input validation - values are passed to nmcli as argv entries,
        # so shell injection is impossible, but nmcli option parsing must
        # not be confused either
        raw = ssid.encode("utf-8")
        if not ssid.strip() or not (1 <= len(raw) <= 32):
            return jsonify({'status': 'Error', 'msg': 'SSID must be 1-32 bytes long'})
        if ssid == HOTSPOT_PROFILE:
            return jsonify({'status': 'Error',
                            'msg': 'This is the robot\'s own setup hotspot - it cannot be reconfigured here'})
        if ssid.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
            return jsonify({'status': 'Error', 'msg': 'SSID contains invalid characters'})
        psk_error = _wifi_psk_error(psk)
        if psk_error:
            return jsonify({'status': 'Error', 'msg': psk_error})

        if not _wifi_switch_lock.acquire(blocking=False):
            return jsonify({'status': 'Error', 'msg': 'A WiFi switch is already in progress'})

        try:
            Thread(target=_wifi_switch_worker, args=(ssid, psk, hidden), daemon=True).start()
        except Exception as ex:
            _wifi_switch_lock.release()
            logger.error(f'WiFi switch: failed to start worker: {repr(ex)}')
            return jsonify({'status': 'Error', 'msg': 'Failed to start the WiFi switch task'})

        return jsonify({'status': 'OK',
                        'msg': f'Switching to {ssid} in {_WIFI_SWITCH_DELAY} seconds - this page may become unreachable'})

    elif action == "edit":
        # Change the stored password of a saved profile. A non-active
        # profile is only modified (used next time it connects); the
        # active one is re-activated in the background to prove the new
        # credentials while the user still watches
        name = request.form.get('name') or ""
        psk = request.form.get('password') or ""

        psk_error = _wifi_psk_error(psk)
        if psk_error:
            return jsonify({'status': 'Error', 'msg': psk_error})

        # The lock is held from classification through the mutation, so an
        # in-flight switch worker can never interleave with this edit (it
        # is transferred to the delayed worker below when one is spawned)
        if not _wifi_switch_lock.acquire(blocking=False):
            return jsonify({'status': 'Error', 'msg': 'A WiFi switch is already in progress'})
        lock_transferred = False
        try:
            ok, msg = _wifi_editable_profile(name)
            if not ok:
                return jsonify({'status': 'Error', 'msg': msg})

            # Only open and WPA-PSK profiles fit the single-password model
            # of this form; writing a psk into e.g. an 802-1x profile would
            # silently produce a broken configuration
            code, out, _ = _nmcli(["-g", "802-11-wireless-security.key-mgmt",
                                   "connection", "show", "id", name])
            key_mgmt = out.strip() if code == 0 else "?"
            if key_mgmt not in ("", "wpa-psk"):
                return jsonify({'status': 'Error',
                                'msg': f'[{name}] uses "{key_mgmt}" security - only open and WPA-PSK networks can be edited here'})

            ok, conn = _wifi_active_connection()
            if not ok:
                return jsonify({'status': 'Error',
                                'msg': 'WiFi status unavailable - try again'})

            if name == conn:
                try:
                    Thread(target=_wifi_password_worker, args=(name, psk), daemon=True).start()
                    lock_transferred = True
                except Exception as ex:
                    logger.error(f'WiFi password: failed to start worker: {repr(ex)}')
                    return jsonify({'status': 'Error', 'msg': 'Failed to start the WiFi password task'})
                return jsonify({'status': 'OK',
                                'msg': f'Saving the new password - reconnecting to {name} in {_WIFI_SWITCH_DELAY}s; '
                                       f'if it is wrong this page becomes unreachable (re-join the WALL-E-Setup hotspot)'})

            code, err = _wifi_apply_psk(name, psk)
            if code != 0:
                return jsonify({'status': 'Error',
                                'msg': f'Password update failed: {err.strip() or f"nmcli exited with code {code}"}'})
            return jsonify({'status': 'OK',
                            'msg': f'Password for {name} saved - used next time the network connects'})
        finally:
            if not lock_transferred:
                _wifi_switch_lock.release()

    elif action == "forget":
        # Delete a saved profile. Deleting the profile this page is served
        # over would cut the response off, so that case runs delayed in the
        # background like a switch; the robot then falls back to another
        # saved network or the WALL-E-Setup hotspot
        name = request.form.get('name') or ""

        if not _wifi_switch_lock.acquire(blocking=False):
            return jsonify({'status': 'Error', 'msg': 'A WiFi switch is already in progress'})
        lock_transferred = False
        try:
            ok, msg = _wifi_editable_profile(name)
            if not ok:
                return jsonify({'status': 'Error', 'msg': msg})

            ok, conn = _wifi_active_connection()
            if not ok:
                return jsonify({'status': 'Error',
                                'msg': 'WiFi status unavailable - try again'})

            if name == conn:
                try:
                    Thread(target=_wifi_forget_worker, args=(name,), daemon=True).start()
                    lock_transferred = True
                except Exception as ex:
                    logger.error(f'WiFi forget: failed to start worker: {repr(ex)}')
                    return jsonify({'status': 'Error', 'msg': 'Failed to start the WiFi delete task'})
                return jsonify({'status': 'OK',
                                'msg': f'Deleting {name} in {_WIFI_SWITCH_DELAY}s - the robot falls back to '
                                       f'another saved network or the WALL-E-Setup hotspot'})

            code, _, err = _nmcli(["connection", "delete", "id", name])
            if code != 0:
                return jsonify({'status': 'Error',
                                'msg': f'Delete failed: {err.strip() or f"nmcli exited with code {code}"}'})
            return jsonify({'status': 'OK', 'msg': f'Removed {name}'})
        finally:
            if not lock_transferred:
                _wifi_switch_lock.release()

    else:
        return jsonify({'status': 'Error', 'msg': 'Unable to read [action] POST data'})


###############################################################
#
# Program start code, which initialises the web-interface
#
###############################################################

def _boot_autostart():
    """启动后自动连接 Arduino(无人值守窗口), 共尝试 4 次。

    打开串口会复位 UNO → 7 舵机同时回位, 供电临界的机器人会被这波浪涌
    整机打崩(2026-08-21 实测)。首次连接因此从"用户首次打开网页"(config
    里 AUTOSTART_ARDUINO 已置 False)挪到启动 15s 后执行, 崩了由 systemd
    重启兜底, 登录时串口已连好、不再触发复位。用户手动重连(/settings)
    或服务关停(finally)会置位 _boot_cancel 终止本线程。
    """
    for attempt, delay in enumerate((15, 10, 15, 20)):
        time.sleep(delay)
        if _boot_cancel.is_set() or arduino.is_connected():
            return
        if _boot_cancel.is_set():  # 复审 R1: 抢锁前再查一次, 压缩与手动重连的交错窗口
            return
        if arduino.connect(app.config['ARDUINO_PORT']):
            logger.info(f'Boot auto-connect: Arduino connected on {arduino.port_name}')
            return
        logger.warning(f'Boot auto-connect failed ({attempt + 1}/4)')
    logger.warning('Boot auto-connect abandoned - connect manually in Settings')


if __name__ == '__main__':
    # Debug mode
    if app.config['APP_DEBUG']:
        app.run(port=app.config['APP_PORT'], debug=app.config['APP_DEBUG'], host='0.0.0.0')

    # Production mode
    else:
        Thread(target=_boot_autostart, daemon=True).start()
        try:
            serve(app, host='0.0.0.0', port=app.config['APP_PORT'])
        except KeyboardInterrupt:
            pass
        finally:
            _boot_cancel.set()
            # 显式停机, 不依赖 atexit(CPython 会先 join 非 daemon 线程再执行 atexit,
            # 任何遗漏的非 daemon 线程都会让兜底永远轮不到)。walle.service 用 SIGINT
            # 停服务, KeyboardInterrupt 在主线程触发, 这里的 finally 确定性执行:
            # 停动画 -> 停相机流 -> 断串口(join 后直写 X0/Y0 停机帧)
            track_animator.stop()
            camera.stop_stream()
            arduino.disconnect()
