using System;
using System.Linq;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Runtime.InteropServices;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Storage.Streams;
using SharpDX.DirectInput;

class Program
{
    static readonly byte[] AES_KEY = {
        0x34, 0x52, 0x2a, 0x5b, 0x7a, 0x6e, 0x49, 0x2c,
        0x08, 0x09, 0x0a, 0x9d, 0x8d, 0x2a, 0x23, 0xf8
    };

    static GattCharacteristic controlChar = null!;
    static GattCharacteristic notifyChar = null!;
    static bool forward, backward, left, right;
    static bool lights = true;
    static bool turbo = false;  // arranca en normal, activar con X o Triangle
    static bool running = true;
    static int batteryPercent = -1;

    [DllImport("user32.dll")]
    static extern short GetAsyncKeyState(int vKey);
    static bool IsKeyDown(int vk) => (GetAsyncKeyState(vk) & 0x8000) != 0;

    static byte[] BuildCommand(bool fwd, bool bwd, bool lft, bool rgt, bool lightsOn, byte speed)
    {
        var plain = new byte[16];
        plain[1] = 0x43; plain[2] = 0x54; plain[3] = 0x4C;
        plain[4] = fwd ? (byte)1 : (byte)0;
        plain[5] = bwd ? (byte)1 : (byte)0;
        plain[6] = lft ? (byte)1 : (byte)0;
        plain[7] = rgt ? (byte)1 : (byte)0;
        plain[8] = lightsOn ? (byte)0 : (byte)1;
        plain[9] = speed;
        using var aes = Aes.Create();
        aes.Key = AES_KEY; aes.Mode = CipherMode.ECB; aes.Padding = PaddingMode.None;
        return aes.CreateEncryptor().TransformFinalBlock(plain, 0, 16);
    }

    static byte[] AesDecrypt(byte[] data)
    {
        using var aes = Aes.Create();
        aes.Key = AES_KEY; aes.Mode = CipherMode.ECB; aes.Padding = PaddingMode.None;
        return aes.CreateDecryptor().TransformFinalBlock(data, 0, 16);
    }

    static async Task Main()
    {
        Console.Title = "QCAR x DualShock 4 - MAX TUNING";
        Console.WriteLine(@"
  ╔════════════════════════════════════════════════════╗
  ║      QCAR x DUALSHOCK 4 - MAXIMO TUNING           ║
  ╠════════════════════════════════════════════════════╣
  ║                                                    ║
  ║  DUALSHOCK 4 (PS4):                                ║
  ║    Stick izq / D-Pad  = Direccion                  ║
  ║    R1                 = Acelerar                   ║
  ║    L1                 = Reversa                    ║
  ║    X (Cruz)           = Toggle Turbo               ║
  ║    O (Circulo)        = Toggle Luces               ║
  ║    Triangle           = Turbo momentaneo           ║
  ║    PS button          = Salir                      ║
  ║                                                    ║
  ║  TECLADO (siempre activo):                         ║
  ║    WASD / Flechas | SPACE=Turbo | L=Luces | ESC    ║
  ║                                                    ║
  ╠════════════════════════════════════════════════════╣
  ║  TURBO: OFF (80%)  |  X=Turbo  |  Triangle=Boost  ║
  ╚════════════════════════════════════════════════════╝
");

        // === FIND DS4 ===
        Joystick? gamepad = null;
        var di = new DirectInput();
        Console.Write("Buscando DualShock 4...");

        var gamepads = di.GetDevices(DeviceType.Gamepad, DeviceEnumerationFlags.AllDevices)
            .Concat(di.GetDevices(DeviceType.Joystick, DeviceEnumerationFlags.AllDevices))
            .Concat(di.GetDevices(DeviceType.FirstPerson, DeviceEnumerationFlags.AllDevices))
            .ToList();

        if (gamepads.Count > 0)
        {
            foreach (var d in gamepads)
                Console.WriteLine($"\n  Found: {d.ProductName}");

            var sel = gamepads.FirstOrDefault(d =>
                d.ProductName.Contains("Wireless Controller", StringComparison.OrdinalIgnoreCase) ||
                d.ProductName.Contains("DualShock", StringComparison.OrdinalIgnoreCase) ||
                d.ProductName.Contains("PS4", StringComparison.OrdinalIgnoreCase))
                ?? gamepads.First();

            gamepad = new Joystick(di, sel.InstanceGuid);
            gamepad.Properties.BufferSize = 128;
            gamepad.Acquire();
            Console.WriteLine($"  Usando: {sel.ProductName}");
        }
        else
        {
            Console.WriteLine(" No encontrado. Solo teclado.");
        }

        // === CONNECT QCAR ===
        Console.Write("\nBuscando QCAR...");
        var watcher = new BluetoothLEAdvertisementWatcher();
        watcher.ScanningMode = BluetoothLEScanningMode.Active;
        var found = new TaskCompletionSource<ulong>();
        watcher.Received += (s, e) =>
        {
            if (!string.IsNullOrEmpty(e.Advertisement.LocalName) && e.Advertisement.LocalName.StartsWith("QCAR"))
            {
                Console.WriteLine($" {e.Advertisement.LocalName} (RSSI: {e.RawSignalStrengthInDBm} dBm)");
                found.TrySetResult(e.BluetoothAddress);
            }
        };
        watcher.Start();
        if (await Task.WhenAny(found.Task, Task.Delay(15000)) != found.Task)
        { Console.WriteLine("\nQCAR no encontrado!"); return; }
        watcher.Stop();

        Console.Write("Conectando...");
        var device = await BluetoothLEDevice.FromBluetoothAddressAsync(found.Task.Result);
        if (device == null) { Console.WriteLine(" FALLO!"); return; }
        Console.WriteLine(" OK!");

        var svcResult = await device.GetGattServicesAsync(BluetoothCacheMode.Uncached);
        foreach (var svc in svcResult.Services)
        {
            var chars = await svc.GetCharacteristicsAsync(BluetoothCacheMode.Uncached);
            if (chars.Status != GattCommunicationStatus.Success) continue;
            foreach (var c in chars.Characteristics)
            {
                var u = c.Uuid.ToString();
                if (u.Contains("925416129600") && !u.Contains("960a") && !u.Contains("960b")) controlChar = c;
                else if (u.Contains("9601")) notifyChar = c;
            }
        }
        if (controlChar == null) { Console.WriteLine("Control char not found!"); return; }

        if (notifyChar != null)
        {
            notifyChar.ValueChanged += (s, e) =>
            {
                var r = DataReader.FromBuffer(e.CharacteristicValue);
                var b = new byte[r.UnconsumedBufferLength]; r.ReadBytes(b);
                if (b.Length == 16) try { var d = AesDecrypt(b); if (d[1]==0x56&&d[2]==0x42&&d[3]==0x54) batteryPercent=d[4]; } catch{}
            };
            await notifyChar.WriteClientCharacteristicConfigurationDescriptorAsync(
                GattClientCharacteristicConfigurationDescriptorValue.Notify);
        }

        // Warmup: mandar IDLE por 500ms para que no salga disparado
        Console.Write("Estabilizando...");
        for (int i = 0; i < 50; i++)
        {
            var idle = BuildCommand(false, false, false, false, true, 0x50);
            var iw = new DataWriter(); iw.WriteBytes(idle);
            try { await controlChar.WriteValueAsync(iw.DetachBuffer(), GattWriteOption.WriteWithoutResponse); } catch { }
            await Task.Delay(10);
        }
        Console.WriteLine(" OK!");
        Console.WriteLine("LISTO! A correr!\n");

        bool prevX = false, prevO = false, prevSp = false, prevL = false;
        const int DEAD = 8000;

        while (running)
        {
            bool momentTurbo = false;
            forward = backward = left = right = false;

            if (gamepad != null)
            {
                try
                {
                    gamepad.Poll();
                    var st = gamepad.GetCurrentState();

                    // DS4 USB: Left stick X=axis0, Y=axis1, center ~32767
                    int lx = st.X - 32767;
                    int ly = st.Y - 32767;

                    // D-Pad
                    int pov = st.PointOfViewControllers[0];
                    if (pov >= 0)
                    {
                        if (pov >= 31500 || pov <= 4500) forward = true;
                        if (pov >= 4500 && pov <= 13500) right = true;
                        if (pov >= 13500 && pov <= 22500) backward = true;
                        if (pov >= 22500 && pov <= 31500) left = true;
                    }

                    // Stick
                    if (ly < -DEAD) forward = true;
                    if (ly > DEAD) backward = true;
                    if (lx < -DEAD) left = true;
                    if (lx > DEAD) right = true;

                    // DS4 buttons USB: [0]=Square, [1]=X, [2]=O, [3]=Triangle
                    // [4]=L1, [5]=R1, [6]=L2, [7]=R2, [8]=Share, [9]=Options
                    // [10]=L3, [11]=R3, [12]=PS, [13]=Touchpad
                    var btn = st.Buttons;

                    // R1 = acelerar, L1 = reversa
                    if (btn.Length > 5 && btn[5]) forward = true;   // R1
                    if (btn.Length > 4 && btn[4]) backward = true;  // L1

                    bool xBtn = btn.Length > 1 && btn[1];
                    bool oBtn = btn.Length > 2 && btn[2];
                    bool tri  = btn.Length > 3 && btn[3];
                    bool ps   = btn.Length > 12 && btn[12];

                    if (xBtn && !prevX) turbo = !turbo;
                    prevX = xBtn;
                    if (oBtn && !prevO) lights = !lights;
                    prevO = oBtn;
                    momentTurbo = tri;
                    if (ps) { running = false; break; }
                }
                catch { }
            }

            // Keyboard always works too
            if (IsKeyDown(0x57) || IsKeyDown(0x26)) forward = true;
            if (IsKeyDown(0x53) || IsKeyDown(0x28)) backward = true;
            if (IsKeyDown(0x41) || IsKeyDown(0x25)) left = true;
            if (IsKeyDown(0x44) || IsKeyDown(0x27)) right = true;
            bool sp = IsKeyDown(0x20); if (sp && !prevSp) turbo = !turbo; prevSp = sp;
            bool ll = IsKeyDown(0x4C); if (ll && !prevL) lights = !lights; prevL = ll;
            if (IsKeyDown(0x1B)) { running = false; break; }

            bool t = turbo || momentTurbo;
            var enc = BuildCommand(forward, backward, left, right, lights, t ? (byte)0x64 : (byte)0x50);
            var w = new DataWriter(); w.WriteBytes(enc);
            try { await controlChar.WriteValueAsync(w.DetachBuffer(), GattWriteOption.WriteWithoutResponse); } catch { }

            string dir = (forward, backward, left, right) switch
            {
                (true, _, true, _) => "↗ FWD+LEFT ",
                (true, _, _, true) => "↖ FWD+RIGHT",
                (_, true, true, _) => "↘ REV+LEFT ",
                (_, true, _, true) => "↙ REV+RIGHT",
                (true, _, _, _)    => "↑ FORWARD  ",
                (_, true, _, _)    => "↓ REVERSE  ",
                (_, _, true, _)    => "← LEFT     ",
                (_, _, _, true)    => "→ RIGHT    ",
                _                  => "■ IDLE     "
            };
            string bat = batteryPercent >= 0 ? $"{batteryPercent}%" : "??";
            string inp = gamepad != null ? "DS4" : "KBD";
            Console.Write($"\r  {dir} | {(t?"TURBO 100%":"NORMAL 80%"),-10} | Luces:{(lights?"ON ":"OFF")} | Bat:{bat,-4} | {inp}  ");

            await Task.Delay(10);
        }

        var stop = BuildCommand(false, false, false, false, lights, 0x50);
        var sw2 = new DataWriter(); sw2.WriteBytes(stop);
        try { await controlChar.WriteValueAsync(sw2.DetachBuffer(), GattWriteOption.WriteWithoutResponse); } catch { }
        gamepad?.Unacquire(); gamepad?.Dispose(); di?.Dispose(); device.Dispose();
        Console.WriteLine("\n\n  Desconectado!");
    }
}
