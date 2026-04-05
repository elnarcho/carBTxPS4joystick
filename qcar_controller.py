"""
QCAR Controller - Shell RC Car via BLE
Control any Shell QCAR with any gamepad/joystick + keyboard fallback.
Visual GUI with customizable button mapping and autopilot circuit editor.
"""

import asyncio
import json
import math
import threading
import time
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import customtkinter as ctk
import pygame

# ── BLE Protocol ──────────────────────────────────────────────────────────────

AES_KEY = bytes([0x34,0x52,0x2a,0x5b,0x7a,0x6e,0x49,0x2c,
                 0x08,0x09,0x0a,0x9d,0x8d,0x2a,0x23,0xf8])

CONTROL_CHAR = "d44bc439-abfd-45a2-b575-925416129600"
NOTIFY_CHAR  = "d44bc439-abfd-45a2-b575-925416129601"

def aes_encrypt(plaintext: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(AES_KEY), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(plaintext) + enc.finalize()

def aes_decrypt(ciphertext: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(AES_KEY), modes.ECB())
    dec = cipher.decryptor()
    return dec.update(ciphertext) + dec.finalize()

def build_command(fwd, bwd, lft, rgt, lights_on, speed):
    plain = bytearray(16)
    plain[1:4] = b'CTL'
    plain[4] = 1 if fwd else 0
    plain[5] = 1 if bwd else 0
    plain[6] = 1 if lft else 0
    plain[7] = 1 if rgt else 0
    plain[8] = 0 if lights_on else 1
    plain[9] = speed
    return aes_encrypt(bytes(plain))


# ── Config / Mapping ──────────────────────────────────────────────────────────

CONFIG_DIR = Path(__file__).parent
CONFIG_FILE = CONFIG_DIR / "controller_mapping.json"
CIRCUIT_FILE = CONFIG_DIR / "circuits.json"

DEFAULT_MAPPING = {
    "forward":       {"type": "button", "index": 5,  "label": "R1"},
    "backward":      {"type": "button", "index": 4,  "label": "L1"},
    "left":          {"type": "axis",   "index": 0,  "direction": -1, "threshold": 0.5, "label": "Stick L izq"},
    "right":         {"type": "axis",   "index": 0,  "direction": 1,  "threshold": 0.5, "label": "Stick L der"},
    "turbo_toggle":  {"type": "button", "index": 1,  "label": "X / A"},
    "turbo_hold":    {"type": "button", "index": 3,  "label": "Triangle / Y"},
    "lights_toggle": {"type": "button", "index": 2,  "label": "O / B"},
    "exit":          {"type": "button", "index": 12, "label": "PS / Home"},
}

def load_mapping():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except:
            pass
    return DEFAULT_MAPPING.copy()

def save_mapping(mapping):
    with open(CONFIG_FILE, "w") as f:
        json.dump(mapping, f, indent=2)

def load_circuits():
    if CIRCUIT_FILE.exists():
        try:
            with open(CIRCUIT_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

def save_circuits(circuits):
    with open(CIRCUIT_FILE, "w") as f:
        json.dump(circuits, f, indent=2)


# ── App ───────────────────────────────────────────────────────────────────────

class QCARApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("QCAR Controller")
        self.geometry("1100x620")
        self.resizable(False, False)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # State
        self.ble_client = None
        self.connected = False
        self.qcar_name = ""
        self.battery = -1
        self.forward = False
        self.backward = False
        self.left = False
        self.right = False
        self.lights = True
        self.turbo = False
        self.speed_normal = 0x50
        self.speed_turbo = 0x64
        self.mapping = load_mapping()
        self.joystick = None
        self.joystick_name = ""
        self.joystick_index = 0
        self.sending = False
        self.prev_buttons = {}

        # Autopilot
        self.autopilot_running = False
        self.autopilot_sequence = []  # list of (action, duration_ms)

        # Pygame init
        pygame.init()
        pygame.joystick.init()

        self._build_ui()
        self._refresh_joystick_list()

        self.after(16, self._poll_gamepad)
        self.after(2000, self._periodic_joystick_check)

    def _build_ui(self):
        # ── Header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(10,5))
        ctk.CTkLabel(header, text="QCAR Controller", font=("Consolas", 22, "bold")).pack(side="left")
        ctk.CTkLabel(header, text="WASD/Flechas=Dir  SPACE=Turbo  L=Luces  ESC=Desconectar",
                     font=("Consolas", 10), text_color="gray50").pack(side="right")

        # ── Main 2-column layout ──
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=10, pady=5)
        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=1)

        # ════ LEFT COLUMN ════
        left = ctk.CTkFrame(main, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=5)

        # ── Connection ──
        conn_frame = ctk.CTkFrame(left)
        conn_frame.pack(fill="x", pady=(0,5))
        ctk.CTkLabel(conn_frame, text="QCAR (BLE)", font=("Consolas", 13, "bold")).pack(anchor="w", padx=10, pady=(6,0))
        row1 = ctk.CTkFrame(conn_frame, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=3)
        self.ble_status = ctk.CTkLabel(row1, text="● Desconectado", text_color="red", font=("Consolas", 12))
        self.ble_status.pack(side="left")
        self.bat_label = ctk.CTkLabel(row1, text="Bat: ??", font=("Consolas", 12))
        self.bat_label.pack(side="right")
        self.btn_connect = ctk.CTkButton(conn_frame, text="Conectar QCAR", command=self._connect_qcar, height=30)
        self.btn_connect.pack(padx=10, pady=(0,6), fill="x")

        # ── Gamepad ──
        joy_frame = ctk.CTkFrame(left)
        joy_frame.pack(fill="x", pady=5)
        joy_header = ctk.CTkFrame(joy_frame, fg_color="transparent")
        joy_header.pack(fill="x", padx=10, pady=(6,0))
        ctk.CTkLabel(joy_header, text="Gamepad", font=("Consolas", 13, "bold")).pack(side="left")
        self.joy_status_dot = ctk.CTkLabel(joy_header, text="● Desconectado", text_color="red", font=("Consolas", 11))
        self.joy_status_dot.pack(side="right")

        row_joy = ctk.CTkFrame(joy_frame, fg_color="transparent")
        row_joy.pack(fill="x", padx=10, pady=3)
        self.joy_dropdown = ctk.CTkComboBox(row_joy, values=["Ninguno"], width=340,
                                             command=self._on_joystick_select, state="readonly")
        self.joy_dropdown.pack(side="left", padx=(0,3))
        ctk.CTkButton(row_joy, text="↻", command=self._refresh_joystick_list, width=32, height=28).pack(side="left", padx=2)
        ctk.CTkButton(row_joy, text="BT", command=self._open_bt_settings, width=32, height=28,
                      fg_color="gray30", hover_color="gray40").pack(side="left", padx=2)

        self.joy_info = ctk.CTkLabel(joy_frame, text="", font=("Consolas", 10), text_color="gray")
        self.joy_info.pack(anchor="w", padx=10)
        self.btn_map = ctk.CTkButton(joy_frame, text="Configurar Mapeo de Botones", command=self._open_mapping, height=30)
        self.btn_map.pack(padx=10, pady=(2,6), fill="x")

        # ── Visual Control ──
        ctrl_frame = ctk.CTkFrame(left)
        ctrl_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(ctrl_frame, text="Control", font=("Consolas", 13, "bold")).pack(anchor="w", padx=10, pady=(6,0))

        ctrl_inner = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        ctrl_inner.pack(fill="x", padx=10, pady=5)

        # Direction arrows (left side)
        dir_frame = ctk.CTkFrame(ctrl_inner, fg_color="transparent")
        dir_frame.pack(side="left", padx=(10,20))
        self.dir_labels = {}
        for r, c, key, txt in [(0,1,"fwd","▲"),(1,0,"lft","◄"),(1,1,"center","●"),(1,2,"rgt","►"),(2,1,"bwd","▼")]:
            lbl = ctk.CTkLabel(dir_frame, text=txt, font=("Consolas", 26), width=45, height=45, text_color="gray40")
            lbl.grid(row=r, column=c, padx=2, pady=2)
            self.dir_labels[key] = lbl

        # Status (right of arrows)
        status_col = ctk.CTkFrame(ctrl_inner, fg_color="transparent")
        status_col.pack(side="left", fill="y", pady=5)
        self.dir_text = ctk.CTkLabel(status_col, text="IDLE", font=("Consolas", 18, "bold"), text_color="gray")
        self.dir_text.pack(anchor="w", pady=(5,3))
        self.turbo_label = ctk.CTkLabel(status_col, text="NORMAL 80%", font=("Consolas", 14, "bold"), text_color="gray")
        self.turbo_label.pack(anchor="w", pady=2)
        self.lights_label = ctk.CTkLabel(status_col, text="Luces: ON", font=("Consolas", 14), text_color="yellow")
        self.lights_label.pack(anchor="w", pady=2)

        # ════ RIGHT COLUMN ════
        right = ctk.CTkFrame(main, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=5)

        # ── Autopilot ──
        auto_frame = ctk.CTkFrame(right)
        auto_frame.pack(fill="x", pady=(0,5))
        ctk.CTkLabel(auto_frame, text="Piloto Automatico", font=("Consolas", 13, "bold")).pack(anchor="w", padx=10, pady=(6,0))
        auto_btns = ctk.CTkFrame(auto_frame, fg_color="transparent")
        auto_btns.pack(fill="x", padx=10, pady=5)
        self.btn_circuit = ctk.CTkButton(auto_btns, text="Editor de Circuito", command=self._open_circuit_editor,
                                          height=30, width=200)
        self.btn_circuit.pack(side="left", padx=(0,5))
        self.btn_autopilot = ctk.CTkButton(auto_btns, text="▶ Iniciar", command=self._toggle_autopilot,
                                            height=30, width=110, fg_color="green", hover_color="darkgreen")
        self.btn_autopilot.pack(side="left", padx=5)
        self.btn_stop_auto = ctk.CTkButton(auto_btns, text="■ Parar", command=self._stop_autopilot,
                                            height=30, width=90, fg_color="red", hover_color="darkred")
        self.btn_stop_auto.pack(side="left", padx=5)
        self.auto_status = ctk.CTkLabel(auto_frame, text="Sin circuito cargado", font=("Consolas", 11), text_color="gray")
        self.auto_status.pack(anchor="w", padx=10, pady=(0,6))

        # ── Log ──
        ctk.CTkLabel(right, text="Log", font=("Consolas", 13, "bold")).pack(anchor="w", padx=5, pady=(5,0))
        self.log_text = ctk.CTkTextbox(right, height=350, font=("Consolas", 11))
        self.log_text.pack(fill="both", expand=True, padx=0, pady=(3,0))
        self._log("Listo. Conecta un gamepad y el QCAR.")

        # Keyboard binds
        self.bind("<KeyPress>", self._on_key_press)
        self.bind("<KeyRelease>", self._on_key_release)
        self.focus_set()
        self._kb_state = {"w":False,"s":False,"a":False,"d":False,"Up":False,"Down":False,"Left":False,"Right":False}
        self._kb_turbo_prev = False
        self._kb_lights_prev = False

    def _log(self, msg):
        self.log_text.insert("end", f"{msg}\n")
        self.log_text.see("end")

    # ── Joystick Selection ────────────────────────────────────────────────────

    def _refresh_joystick_list(self):
        pygame.joystick.quit()
        pygame.joystick.init()
        count = pygame.joystick.get_count()

        names = []
        for i in range(count):
            j = pygame.joystick.Joystick(i)
            j.init()
            names.append(f"{i}: {j.get_name()}")

        if names:
            self.joy_dropdown.configure(values=names)
            self.joy_dropdown.set(names[0])
            self._select_joystick(0)
        else:
            self.joy_dropdown.configure(values=["Ninguno - click BT para vincular"])
            self.joy_dropdown.set("Ninguno - click BT para vincular")
            self.joystick = None
            self.joy_info.configure(text="")
            self.joy_status_dot.configure(text="● Desconectado", text_color="red")

    def _on_joystick_select(self, value):
        if value.startswith("Ninguno"):
            return
        idx = int(value.split(":")[0])
        self._select_joystick(idx)

    def _select_joystick(self, idx):
        try:
            self.joystick = pygame.joystick.Joystick(idx)
            self.joystick.init()
            self.joystick_index = idx
            self.joystick_name = self.joystick.get_name()
            info = f"{self.joystick.get_numbuttons()} botones, {self.joystick.get_numaxes()} ejes, {self.joystick.get_numhats()} hats"
            self.joy_info.configure(text=info)
            self.joy_status_dot.configure(text=f"● {self.joystick_name}", text_color="green")
            self._log(f"Gamepad conectado: {self.joystick_name}")
        except Exception as e:
            self.joy_info.configure(text=f"Error: {e}")
            self.joy_status_dot.configure(text="● Error", text_color="red")

    def _open_bt_settings(self):
        """Open Windows Bluetooth settings to pair a controller."""
        import subprocess
        subprocess.Popen(["explorer", "ms-settings:bluetooth"], shell=True)
        self._log("Abriendo Bluetooth settings...")

    def _check_joystick_connected(self):
        """Check if current joystick is still connected, auto-refresh if not."""
        if self.joystick:
            try:
                pygame.event.pump()
                # Try to read - if fails, joystick disconnected
                self.joystick.get_numbuttons()
            except:
                self._log(f"Gamepad desconectado: {self.joystick_name}")
                self.joystick = None
                self.joy_status_dot.configure(text="● Desconectado", text_color="red")
                self.joy_info.configure(text="")
                self._refresh_joystick_list()
        else:
            # Check if a new one appeared
            pygame.joystick.quit()
            pygame.joystick.init()
            if pygame.joystick.get_count() > 0:
                self._refresh_joystick_list()

    def _periodic_joystick_check(self):
        """Periodically check if joystick is still connected or new one appeared."""
        self._check_joystick_connected()
        self.after(2000, self._periodic_joystick_check)

    # ── Gamepad Polling ───────────────────────────────────────────────────────

    def _poll_gamepad(self):
        pygame.event.pump()

        # If autopilot is running, skip manual input
        if self.autopilot_running:
            self._update_visuals()
            self.after(16, self._poll_gamepad)
            return

        fwd = bwd = lft = rgt = False
        moment_turbo = False

        if self.joystick:
            m = self.mapping
            for action, cfg in m.items():
                val = False
                if cfg["type"] == "button":
                    idx = cfg["index"]
                    if idx < self.joystick.get_numbuttons():
                        val = bool(self.joystick.get_button(idx))
                elif cfg["type"] == "axis":
                    idx = cfg["index"]
                    if idx < self.joystick.get_numaxes():
                        axis_val = self.joystick.get_axis(idx)
                        threshold = cfg.get("threshold", 0.5)
                        direction = cfg.get("direction", 1)
                        val = (axis_val * direction) > threshold
                elif cfg["type"] == "hat":
                    hi = cfg.get("hat_index", 0)
                    if hi < self.joystick.get_numhats():
                        hx, hy = self.joystick.get_hat(hi)
                        axis = cfg.get("axis", "x")
                        direction = cfg.get("direction", 1)
                        val = (hx if axis == "x" else hy) == direction

                if action == "forward": fwd = fwd or val
                elif action == "backward": bwd = bwd or val
                elif action == "left": lft = lft or val
                elif action == "right": rgt = rgt or val
                elif action == "turbo_toggle":
                    prev = self.prev_buttons.get("turbo_toggle", False)
                    if val and not prev: self.turbo = not self.turbo
                    self.prev_buttons["turbo_toggle"] = val
                elif action == "turbo_hold":
                    moment_turbo = val
                elif action == "lights_toggle":
                    prev = self.prev_buttons.get("lights_toggle", False)
                    if val and not prev: self.lights = not self.lights
                    self.prev_buttons["lights_toggle"] = val
                elif action == "exit":
                    if val: self._disconnect_qcar()

            # D-Pad always active
            for hat_i in range(self.joystick.get_numhats()):
                hx, hy = self.joystick.get_hat(hat_i)
                if hy > 0: fwd = True
                if hy < 0: bwd = True
                if hx < 0: lft = True
                if hx > 0: rgt = True

        # Keyboard
        kb = self._kb_state
        if kb["w"] or kb["Up"]: fwd = True
        if kb["s"] or kb["Down"]: bwd = True
        if kb["a"] or kb["Left"]: lft = True
        if kb["d"] or kb["Right"]: rgt = True

        self.forward = fwd
        self.backward = bwd
        self.left = lft
        self.right = rgt
        self._use_turbo = self.turbo or moment_turbo

        self._update_visuals()
        self.after(16, self._poll_gamepad)

    def _update_visuals(self):
        fwd, bwd, lft, rgt = self.forward, self.backward, self.left, self.right
        use_turbo = getattr(self, '_use_turbo', self.turbo)

        self.dir_labels["fwd"].configure(text_color="lime" if fwd else "gray40")
        self.dir_labels["bwd"].configure(text_color="lime" if bwd else "gray40")
        self.dir_labels["lft"].configure(text_color="lime" if lft else "gray40")
        self.dir_labels["rgt"].configure(text_color="lime" if rgt else "gray40")
        self.dir_labels["center"].configure(text_color="red" if any([fwd,bwd,lft,rgt]) else "gray40")

        self.turbo_label.configure(
            text="TURBO 100%" if use_turbo else "NORMAL 80%",
            text_color="red" if use_turbo else "gray")
        self.lights_label.configure(
            text="Luces: ON" if self.lights else "Luces: OFF",
            text_color="yellow" if self.lights else "gray")

        dirs = []
        if fwd: dirs.append("FWD")
        if bwd: dirs.append("REV")
        if lft: dirs.append("LEFT")
        if rgt: dirs.append("RIGHT")
        self.dir_text.configure(text=" + ".join(dirs) if dirs else "IDLE",
                                text_color="lime" if dirs else "gray")

        if self.battery >= 0:
            color = "lime" if self.battery > 30 else ("yellow" if self.battery > 10 else "red")
            self.bat_label.configure(text=f"Bat: {self.battery}%", text_color=color)

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def _on_key_press(self, event):
        key = event.keysym
        if key in self._kb_state:
            self._kb_state[key] = True
        elif key == "space":
            if not self._kb_turbo_prev: self.turbo = not self.turbo
            self._kb_turbo_prev = True
        elif key == "l":
            if not self._kb_lights_prev: self.lights = not self.lights
            self._kb_lights_prev = True
        elif key == "Escape":
            self._disconnect_qcar()

    def _on_key_release(self, event):
        key = event.keysym
        if key in self._kb_state:
            self._kb_state[key] = False
        elif key == "space": self._kb_turbo_prev = False
        elif key == "l": self._kb_lights_prev = False

    # ── BLE ───────────────────────────────────────────────────────────────────

    def _connect_qcar(self):
        self.btn_connect.configure(state="disabled", text="Buscando...")
        self._log("Escaneando BLE...")
        threading.Thread(target=self._ble_thread, daemon=True).start()

    def _ble_thread(self):
        asyncio.run(self._ble_connect_and_run())

    async def _ble_connect_and_run(self):
        from bleak import BleakScanner, BleakClient

        device = None
        devices = await BleakScanner.discover(timeout=10)
        for d in devices:
            if d.name and d.name.startswith("QCAR"):
                device = d
                break

        if not device:
            self.after(0, lambda: self._log("QCAR no encontrado."))
            self.after(0, lambda: self.btn_connect.configure(state="normal", text="Conectar QCAR"))
            return

        self.qcar_name = device.name
        self.after(0, lambda: self._log(f"Encontrado: {device.name} (RSSI: {device.rssi} dBm)"))
        self.after(0, lambda: self.btn_connect.configure(text="Conectando..."))

        try:
            async with BleakClient(device, timeout=15) as client:
                self.ble_client = client
                self.connected = True
                self.after(0, lambda: self.ble_status.configure(text=f"● {self.qcar_name}", text_color="green"))
                self.after(0, lambda: self.btn_connect.configure(text="Desconectar", state="normal", command=self._disconnect_qcar))
                self.after(0, lambda: self._log(f"Conectado!"))

                try:
                    await client.start_notify(NOTIFY_CHAR, self._on_battery_notify)
                except:
                    pass

                # Warmup
                for _ in range(50):
                    cmd = build_command(False, False, False, False, True, 0x50)
                    try: await client.write_gatt_char(CONTROL_CHAR, cmd, response=False)
                    except: pass
                    await asyncio.sleep(0.01)

                self.after(0, lambda: self._log("Listo para controlar!"))
                self.sending = True

                while self.connected:
                    use_turbo = getattr(self, '_use_turbo', self.turbo)
                    speed = self.speed_turbo if use_turbo else self.speed_normal
                    cmd = build_command(self.forward, self.backward, self.left, self.right, self.lights, speed)
                    try:
                        await client.write_gatt_char(CONTROL_CHAR, cmd, response=False)
                    except Exception as e:
                        self.after(0, lambda: self._log(f"Error BLE: {e}"))
                        break
                    await asyncio.sleep(0.01)

                self.sending = False
                try:
                    await client.write_gatt_char(CONTROL_CHAR, build_command(False,False,False,False,self.lights,0x50), response=False)
                except: pass

        except Exception as e:
            self.after(0, lambda: self._log(f"Error: {e}"))

        self.connected = False
        self.ble_client = None
        self.after(0, lambda: self.ble_status.configure(text="● Desconectado", text_color="red"))
        self.after(0, lambda: self.btn_connect.configure(text="Conectar QCAR", state="normal", command=self._connect_qcar))

    def _on_battery_notify(self, sender, data):
        if len(data) == 16:
            try:
                dec = aes_decrypt(bytes(data))
                if dec[1:4] == b'VBT': self.battery = dec[4]
            except: pass

    def _disconnect_qcar(self):
        self.connected = False
        self._log("Desconectando...")

    # ── Mapping UI ────────────────────────────────────────────────────────────

    def _open_mapping(self):
        win = ctk.CTkToplevel(self)
        win.title("Mapeo de Controles")
        win.geometry("520x560")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(win, text="Mapeo de Controles", font=("Consolas", 18, "bold")).pack(pady=(15,5))
        ctk.CTkLabel(win, text="Click en el boton y presiona el input del gamepad",
                     font=("Consolas", 11), text_color="gray").pack()

        actions = [
            ("forward","Adelante"), ("backward","Reversa"), ("left","Izquierda"), ("right","Derecha"),
            ("turbo_toggle","Toggle Turbo"), ("turbo_hold","Turbo Momentaneo"),
            ("lights_toggle","Toggle Luces"), ("exit","Salir"),
        ]

        frame = ctk.CTkFrame(win)
        frame.pack(fill="both", expand=True, padx=15, pady=10)

        self._map_buttons = {}
        for i, (action, label) in enumerate(actions):
            ctk.CTkLabel(frame, text=label, font=("Consolas", 13), anchor="w", width=180).grid(
                row=i, column=0, padx=(15,5), pady=6, sticky="w")
            current = self.mapping.get(action, {})
            btn = ctk.CTkButton(frame, text=current.get("label","Sin asignar"), width=240, height=32,
                                fg_color="gray30", hover_color="gray40",
                                command=lambda a=action: self._start_listen(win, a))
            btn.grid(row=i, column=1, padx=(5,15), pady=6)
            self._map_buttons[action] = btn

        bot = ctk.CTkFrame(win, fg_color="transparent")
        bot.pack(fill="x", padx=15, pady=(0,15))
        ctk.CTkButton(bot, text="Restablecer Default", command=lambda: self._reset_mapping(),
                      width=150, fg_color="gray30").pack(side="left")
        ctk.CTkButton(bot, text="Guardar", command=lambda: self._save_and_close(win), width=150).pack(side="right")

    def _start_listen(self, win, action):
        btn = self._map_buttons[action]
        btn.configure(text=">>> Presiona algo <<<", fg_color="darkred")

        def listen():
            deadline = time.time() + 10
            while time.time() < deadline:
                pygame.event.pump()
                if self.joystick:
                    for i in range(self.joystick.get_numbuttons()):
                        if self.joystick.get_button(i):
                            name = f"Boton {i}"
                            self.mapping[action] = {"type":"button","index":i,"label":name}
                            self.after(0, lambda n=name: btn.configure(text=n, fg_color="gray30"))
                            return
                    for i in range(self.joystick.get_numaxes()):
                        v = self.joystick.get_axis(i)
                        if abs(v) > 0.7:
                            d = 1 if v > 0 else -1
                            name = f"Eje {i} {'(+)' if d>0 else '(-)'}"
                            self.mapping[action] = {"type":"axis","index":i,"direction":d,"threshold":0.5,"label":name}
                            self.after(0, lambda n=name: btn.configure(text=n, fg_color="gray30"))
                            return
                    for i in range(self.joystick.get_numhats()):
                        hx, hy = self.joystick.get_hat(i)
                        if hx != 0 or hy != 0:
                            name = f"Hat {i} ({hx},{hy})"
                            ax = "x" if abs(hx)>0 else "y"
                            dr = hx if ax=="x" else hy
                            self.mapping[action] = {"type":"hat","hat_index":i,"axis":ax,"direction":dr,"label":name}
                            self.after(0, lambda n=name: btn.configure(text=n, fg_color="gray30"))
                            return
                time.sleep(0.05)
            self.after(0, lambda: btn.configure(text="Timeout", fg_color="gray30"))

        threading.Thread(target=listen, daemon=True).start()

    def _reset_mapping(self):
        self.mapping = DEFAULT_MAPPING.copy()
        for action, btn in self._map_buttons.items():
            btn.configure(text=self.mapping.get(action,{}).get("label","?"))
        self._log("Mapeo restablecido")

    def _save_and_close(self, win):
        save_mapping(self.mapping)
        self._log(f"Mapeo guardado")
        win.destroy()

    # ── Circuit Editor & Autopilot ────────────────────────────────────────────

    def _open_circuit_editor(self):
        win = ctk.CTkToplevel(self)
        win.title("Editor de Circuito - Piloto Automatico")
        win.geometry("650x700")
        win.resizable(False, False)
        win.transient(self)

        ctk.CTkLabel(win, text="Editor de Circuito", font=("Consolas", 18, "bold")).pack(pady=(10,0))
        ctk.CTkLabel(win, text="Dibuja el recorrido del auto con el mouse en el canvas",
                     font=("Consolas", 11), text_color="gray").pack()

        # Canvas for drawing
        import tkinter as tk
        canvas_frame = ctk.CTkFrame(win)
        canvas_frame.pack(padx=15, pady=10)

        self._canvas = tk.Canvas(canvas_frame, width=600, height=400, bg="#1a1a2e", highlightthickness=1,
                                  highlightbackground="gray50")
        self._canvas.pack()

        # Draw grid
        for x in range(0, 601, 50):
            self._canvas.create_line(x, 0, x, 400, fill="#2a2a3e", dash=(2,4))
        for y in range(0, 401, 50):
            self._canvas.create_line(0, y, 600, y, fill="#2a2a3e", dash=(2,4))

        # Drawing state
        self._draw_points = []
        self._drawing = False
        self._canvas.bind("<ButtonPress-1>", self._canvas_start_draw)
        self._canvas.bind("<B1-Motion>", self._canvas_draw)
        self._canvas.bind("<ButtonRelease-1>", self._canvas_end_draw)

        # Controls
        ctrl_row = ctk.CTkFrame(win, fg_color="transparent")
        ctrl_row.pack(fill="x", padx=15, pady=5)
        ctk.CTkButton(ctrl_row, text="Limpiar", command=self._canvas_clear, width=100,
                      fg_color="gray30").pack(side="left", padx=3)

        ctk.CTkLabel(ctrl_row, text="Velocidad:", font=("Consolas", 12)).pack(side="left", padx=(15,5))
        self._circuit_speed = ctk.CTkComboBox(ctrl_row, values=["Lento","Normal","Turbo"], width=100, state="readonly")
        self._circuit_speed.set("Normal")
        self._circuit_speed.pack(side="left")

        ctk.CTkLabel(ctrl_row, text="Vueltas:", font=("Consolas", 12)).pack(side="left", padx=(15,5))
        self._circuit_laps = ctk.CTkEntry(ctrl_row, width=50, placeholder_text="1")
        self._circuit_laps.insert(0, "1")
        self._circuit_laps.pack(side="left")

        # Sequence preview
        ctk.CTkLabel(win, text="Secuencia generada:", font=("Consolas", 12, "bold")).pack(anchor="w", padx=15, pady=(10,0))
        self._seq_text = ctk.CTkTextbox(win, height=100, font=("Consolas", 11))
        self._seq_text.pack(fill="x", padx=15, pady=5)

        # Bottom
        bot = ctk.CTkFrame(win, fg_color="transparent")
        bot.pack(fill="x", padx=15, pady=(0,15))
        ctk.CTkButton(bot, text="Generar Secuencia", command=self._generate_sequence, width=180).pack(side="left", padx=3)
        ctk.CTkButton(bot, text="Cargar y Cerrar", command=lambda: self._load_circuit(win), width=180,
                      fg_color="green", hover_color="darkgreen").pack(side="right", padx=3)

        # Save/Load circuits
        save_row = ctk.CTkFrame(win, fg_color="transparent")
        save_row.pack(fill="x", padx=15, pady=(0,10))
        self._circuit_name = ctk.CTkEntry(save_row, width=200, placeholder_text="Nombre del circuito")
        self._circuit_name.pack(side="left", padx=(0,5))
        ctk.CTkButton(save_row, text="Guardar", command=self._save_circuit_named, width=80).pack(side="left", padx=3)
        self._circuit_list = ctk.CTkComboBox(save_row, values=list(load_circuits().keys()) or ["(vacio)"],
                                              width=150, state="readonly")
        self._circuit_list.pack(side="left", padx=(15,5))
        ctk.CTkButton(save_row, text="Cargar", command=self._load_circuit_named, width=80).pack(side="left", padx=3)

    def _canvas_start_draw(self, event):
        self._drawing = True
        self._draw_points = [(event.x, event.y)]
        self._canvas.delete("path")

    def _canvas_draw(self, event):
        if not self._drawing:
            return
        pts = self._draw_points
        pts.append((event.x, event.y))
        if len(pts) >= 2:
            self._canvas.create_line(pts[-2][0], pts[-2][1], pts[-1][0], pts[-1][1],
                                      fill="cyan", width=3, tags="path")

    def _canvas_end_draw(self, event):
        self._drawing = False
        if len(self._draw_points) > 5:
            # Close the loop: draw line from last to first
            p0 = self._draw_points[0]
            pn = self._draw_points[-1]
            self._canvas.create_line(pn[0], pn[1], p0[0], p0[1], fill="cyan", width=3, dash=(5,3), tags="path")
            # Mark start
            self._canvas.create_oval(p0[0]-6, p0[1]-6, p0[0]+6, p0[1]+6, fill="lime", outline="white", tags="path")
            self._canvas.create_text(p0[0]+12, p0[1], text="START", fill="lime", font=("Consolas",9), anchor="w", tags="path")

    def _canvas_clear(self):
        self._canvas.delete("path")
        self._draw_points = []
        self._seq_text.delete("1.0", "end")

    def _generate_sequence(self):
        """Convert drawn path into a sequence of (direction, duration_ms) commands."""
        pts = self._draw_points
        if len(pts) < 10:
            self._seq_text.delete("1.0", "end")
            self._seq_text.insert("end", "Dibuja un recorrido mas largo!")
            return

        # Simplify path: sample every N points
        step = max(1, len(pts) // 80)
        sampled = pts[::step]
        if sampled[-1] != pts[-1]:
            sampled.append(pts[-1])
        # Close the loop
        sampled.append(sampled[0])

        # Speed setting
        speed_name = self._circuit_speed.get()
        speed_map = {"Lento": 0x50, "Normal": 0x50, "Turbo": 0x64}
        speed = speed_map.get(speed_name, 0x50)
        use_turbo = speed_name == "Turbo"

        # Time scale: pixels to ms. Estimate: ~30px per 100ms at normal, ~20px at turbo
        px_per_100ms = 20 if use_turbo else 30
        if speed_name == "Lento":
            px_per_100ms = 40

        sequence = []
        prev_angle = None

        for i in range(len(sampled) - 1):
            x1, y1 = sampled[i]
            x2, y2 = sampled[i + 1]
            dx = x2 - x1
            dy = y2 - y1
            dist = math.sqrt(dx*dx + dy*dy)
            if dist < 3:
                continue

            angle = math.atan2(-dy, dx)  # -dy because canvas Y is inverted
            angle_deg = math.degrees(angle) % 360

            # Determine direction: fwd/bwd/left/right
            # Assume "forward" is the current heading, we compute turns
            fwd = True
            bwd = False
            lft = False
            rgt = False

            if prev_angle is not None:
                delta = angle_deg - prev_angle
                # Normalize to -180..180
                while delta > 180: delta -= 360
                while delta < -180: delta += 360

                if delta > 20:
                    lft = True
                elif delta < -20:
                    rgt = True

                # If angle change is > 90 degrees, it's more of a reverse
                if abs(delta) > 120:
                    fwd = False
                    bwd = True
                    lft = not lft
                    rgt = not rgt

            prev_angle = angle_deg
            duration_ms = int((dist / px_per_100ms) * 100)
            duration_ms = max(50, min(duration_ms, 2000))

            sequence.append({
                "fwd": fwd, "bwd": bwd, "lft": lft, "rgt": rgt,
                "turbo": use_turbo, "duration_ms": duration_ms
            })

        # Laps
        try:
            laps = int(self._circuit_laps.get())
        except:
            laps = 1
        laps = max(1, min(laps, 100))

        full_sequence = sequence * laps
        self.autopilot_sequence = full_sequence

        # Display
        self._seq_text.delete("1.0", "end")
        total_ms = sum(s["duration_ms"] for s in full_sequence)
        self._seq_text.insert("end", f"Pasos: {len(full_sequence)} | Duracion: {total_ms/1000:.1f}s | Vueltas: {laps}\n")
        self._seq_text.insert("end", f"Velocidad: {speed_name}\n\n")

        for i, s in enumerate(full_sequence[:20]):
            dirs = []
            if s["fwd"]: dirs.append("FWD")
            if s["bwd"]: dirs.append("REV")
            if s["lft"]: dirs.append("LEFT")
            if s["rgt"]: dirs.append("RIGHT")
            turbo_s = " TURBO" if s["turbo"] else ""
            self._seq_text.insert("end", f"  {i+1:3d}. {'+'.join(dirs):<15} {s['duration_ms']:4d}ms{turbo_s}\n")
        if len(full_sequence) > 20:
            self._seq_text.insert("end", f"  ... y {len(full_sequence)-20} pasos mas\n")

        self.auto_status.configure(text=f"Circuito: {len(full_sequence)} pasos, {total_ms/1000:.1f}s")

    def _load_circuit(self, win):
        if not self.autopilot_sequence:
            self._generate_sequence()
        if self.autopilot_sequence:
            self._log(f"Circuito cargado: {len(self.autopilot_sequence)} pasos")
        win.destroy()

    def _save_circuit_named(self):
        name = self._circuit_name.get().strip()
        if not name:
            return
        circuits = load_circuits()
        circuits[name] = {
            "points": self._draw_points,
            "sequence": self.autopilot_sequence,
            "speed": self._circuit_speed.get(),
            "laps": self._circuit_laps.get(),
        }
        save_circuits(circuits)
        self._circuit_list.configure(values=list(circuits.keys()))
        self._log(f"Circuito '{name}' guardado")

    def _load_circuit_named(self):
        name = self._circuit_list.get()
        circuits = load_circuits()
        if name in circuits:
            data = circuits[name]
            self._draw_points = data.get("points", [])
            self.autopilot_sequence = data.get("sequence", [])
            # Redraw
            self._canvas.delete("path")
            pts = self._draw_points
            for i in range(len(pts)-1):
                self._canvas.create_line(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1],
                                          fill="cyan", width=3, tags="path")
            if pts:
                self._canvas.create_oval(pts[0][0]-6, pts[0][1]-6, pts[0][0]+6, pts[0][1]+6,
                                          fill="lime", outline="white", tags="path")
            self._circuit_speed.set(data.get("speed", "Normal"))
            self._circuit_laps.delete(0, "end")
            self._circuit_laps.insert(0, data.get("laps", "1"))
            self._generate_sequence()
            self._log(f"Circuito '{name}' cargado")

    # ── Autopilot Execution ───────────────────────────────────────────────────

    def _toggle_autopilot(self):
        if not self.autopilot_sequence:
            self._log("No hay circuito cargado. Abri el editor primero.")
            return
        if not self.connected:
            self._log("Conecta el QCAR primero.")
            return
        self.autopilot_running = True
        self.btn_autopilot.configure(text="▶ Corriendo...", state="disabled", fg_color="orange")
        self._log(f"Autopilot iniciado: {len(self.autopilot_sequence)} pasos")
        threading.Thread(target=self._run_autopilot, daemon=True).start()

    def _run_autopilot(self):
        for i, step in enumerate(self.autopilot_sequence):
            if not self.autopilot_running or not self.connected:
                break

            self.forward = step["fwd"]
            self.backward = step["bwd"]
            self.left = step["lft"]
            self.right = step["rgt"]
            self._use_turbo = step.get("turbo", False)

            # Update status
            pct = int((i+1) / len(self.autopilot_sequence) * 100)
            self.after(0, lambda p=pct, idx=i: self.auto_status.configure(
                text=f"Autopilot: paso {idx+1}/{len(self.autopilot_sequence)} ({p}%)"))

            time.sleep(step["duration_ms"] / 1000.0)

        # Stop
        self.forward = self.backward = self.left = self.right = False
        self._use_turbo = False
        self.autopilot_running = False
        self.after(0, lambda: self.btn_autopilot.configure(text="▶ Iniciar", state="normal", fg_color="green"))
        self.after(0, lambda: self.auto_status.configure(text="Autopilot finalizado"))
        self.after(0, lambda: self._log("Autopilot terminado"))

    def _stop_autopilot(self):
        self.autopilot_running = False
        self.forward = self.backward = self.left = self.right = False
        self._use_turbo = False
        self._log("Autopilot detenido")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QCARApp()
    app.mainloop()
    pygame.quit()
