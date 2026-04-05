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
using HidSharp;

class Program
{
    static readonly byte[] AES_KEY = {
        0x34, 0x52, 0x2a, 0x5b, 0x7a, 0x6e, 0x49, 0x2c,
        0x08, 0x09, 0x0a, 0x9d, 0x8d, 0x2a, 0x23, 0xf8
    };

    static GattCharacteristic controlChar = null!;
    static GattCharacteristic notifyChar = null!;
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

    // DS4 rumble via HID output report
    // smallMotor = high freq vibration (0-255), bigMotor = low freq rumble (0-255)
    static void SetRumble(HidStream? stream, bool bluetooth, byte smallMotor, byte bigMotor)
    {
        if (stream == null) return;
        try
        {
            if (bluetooth)
            {
                // BT output report 0x11, 78 bytes
                var buf = new byte[78];
                buf[0] = 0x11;  // report id
                buf[1] = 0x80;  // protocol flags
                buf[3] = 0xFF;  // flags: enable rumble + LED
                buf[6] = bigMotor;    // right/big motor
                buf[7] = smallMotor;  // left/small motor
                // LED color (keep blue when turbo)
                buf[8] = 0x00;   // R
                buf[9] = 0x00;   // G
                buf[10] = 0x40;  // B
                stream.Write(buf);
            }
            else
            {
                // USB output report 0x05, 32 bytes
                var buf = new byte[32];
                buf[0] = 0x05;
                buf[1] = 0xFF;  // flags
                buf[4] = bigMotor;
                buf[5] = smallMotor;
                stream.Write(buf);
            }
        }
        catch { }
    }

    // DS4 Bluetooth HID report parsing
    // BT report: [0]=0x11, [1-2]=protocol, [3]=LX, [4]=LY, [5]=RX, [6]=RY
    // [7]=buttons1 (hat + square/cross/circle/triangle)
    // [8]=buttons2 (L1/R1/L2/R2/Share/Options/L3/R3)
    // [9]=buttons3 (PS/Touchpad)
    // [10]=L2 analog, [11]=R2 analog
    struct DS4State
    {
        public byte LX, LY, RX, RY;
        public byte Hat;       // 0=N,1=NE,2=E,3=SE,4=S,5=SW,6=W,7=NW,8=neutral
        public bool Square, Cross, Circle, Triangle;
        public bool L1, R1, L2Btn, R2Btn;
        public bool Share, Options, L3, R3;
        public bool PS, Touchpad;
        public byte L2, R2;

        public static DS4State Parse(byte[] buf, int offset)
        {
            var s = new DS4State();
            s.LX = buf[offset + 0];
            s.LY = buf[offset + 1];
            s.RX = buf[offset + 2];
            s.RY = buf[offset + 3];

            byte b1 = buf[offset + 4];
            s.Hat = (byte)(b1 & 0x0F);
            s.Square = (b1 & 0x10) != 0;
            s.Cross = (b1 & 0x20) != 0;
            s.Circle = (b1 & 0x40) != 0;
            s.Triangle = (b1 & 0x80) != 0;

            byte b2 = buf[offset + 5];
            s.L1 = (b2 & 0x01) != 0;
            s.R1 = (b2 & 0x02) != 0;
            s.L2Btn = (b2 & 0x04) != 0;
            s.R2Btn = (b2 & 0x08) != 0;
            s.Share = (b2 & 0x10) != 0;
            s.Options = (b2 & 0x20) != 0;
            s.L3 = (b2 & 0x40) != 0;
            s.R3 = (b2 & 0x80) != 0;

            byte b3 = buf[offset + 6];
            s.PS = (b3 & 0x01) != 0;
            s.Touchpad = (b3 & 0x02) != 0;

            s.L2 = buf[offset + 7];
            s.R2 = buf[offset + 8];
            return s;
        }
    }

    static async Task Main()
    {
        Console.Title = "QCAR x DS4 Bluetooth - MAX TUNING";
        Console.WriteLine(@"
  ╔════════════════════════════════════════════════════╗
  ║     QCAR x DUALSHOCK 4 (Bluetooth) - TUNING       ║
  ╠════════════════════════════════════════════════════╣
  ║                                                    ║
  ║  DUALSHOCK 4:                                      ║
  ║    Stick izq / D-Pad  = Direccion                  ║
  ║    R1                 = Acelerar                   ║
  ║    L1                 = Reversa                    ║
  ║    X (Cruz)           = Toggle Turbo               ║
  ║    O (Circulo)        = Toggle Luces               ║
  ║    Triangle           = Turbo momentaneo           ║
  ║    PS                 = Salir                      ║
  ║                                                    ║
  ║  TECLADO (siempre activo):                         ║
  ║    WASD / Flechas | SPACE=Turbo | L=Luces | ESC    ║
  ║                                                    ║
  ╚════════════════════════════════════════════════════╝
");

        // === FIND DS4 via HID ===
        HidStream? ds4Stream = null;
        bool ds4Bluetooth = false;

        Console.Write("Buscando DualShock 4...");
        var ds4Device = DeviceList.Local.GetHidDevices()
            .FirstOrDefault(d => d.VendorID == 0x054C &&
                (d.ProductID == 0x05C4 || d.ProductID == 0x09CC || d.ProductID == 0x0CE6));

        if (ds4Device != null)
        {
            ds4Bluetooth = ds4Device.GetMaxInputReportLength() > 64;
            Console.WriteLine($" {ds4Device.GetProductName()} ({(ds4Bluetooth ? "Bluetooth" : "USB")})");

            ds4Stream = ds4Device.Open();
            ds4Stream.ReadTimeout = 15;
        }
        else
        {
            Console.WriteLine(" No encontrado. Solo teclado.");
        }

        // === CONNECT QCAR ===
        Console.Write("Buscando QCAR...");
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

        // Battery notifications
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

        // Warmup IDLE
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

        bool forward = false, backward = false, left = false, right = false;
        bool lights = true, turbo = false;
        bool prevX = false, prevO = false, prevSp = false, prevL = false;
        const int DEAD = 40; // stick deadzone (0-255 range, center=128)

        byte[] hidBuf = new byte[ds4Bluetooth ? 547 : 64];

        while (running)
        {
            bool momentTurbo = false;
            forward = backward = left = right = false;

            // Read DS4 HID
            if (ds4Stream != null)
            {
                try
                {
                    int read = ds4Stream.Read(hidBuf, 0, hidBuf.Length);
                    if (read > 0)
                    {
                        // BT: report 0x11, data starts at offset 3
                        // USB: report 0x01, data starts at offset 1
                        int offset = ds4Bluetooth ? 3 : 1;

                        var st = DS4State.Parse(hidBuf, offset);

                        // Stick izquierdo (128 = center)
                        if (st.LY < 128 - DEAD) forward = true;
                        if (st.LY > 128 + DEAD) backward = true;
                        if (st.LX < 128 - DEAD) left = true;
                        if (st.LX > 128 + DEAD) right = true;

                        // D-Pad
                        if (st.Hat == 0 || st.Hat == 1 || st.Hat == 7) forward = true;   // N,NE,NW
                        if (st.Hat == 4 || st.Hat == 3 || st.Hat == 5) backward = true;  // S,SE,SW
                        if (st.Hat == 6 || st.Hat == 5 || st.Hat == 7) left = true;      // W,SW,NW
                        if (st.Hat == 2 || st.Hat == 1 || st.Hat == 3) right = true;     // E,NE,SE

                        // R1 = acelerar, L1 = reversa
                        if (st.R1) forward = true;
                        if (st.L1) backward = true;

                        // X = toggle turbo
                        if (st.Cross && !prevX) turbo = !turbo;
                        prevX = st.Cross;

                        // O = toggle luces
                        if (st.Circle && !prevO) lights = !lights;
                        prevO = st.Circle;

                        // Triangle = turbo momentaneo
                        momentTurbo = st.Triangle;

                        // PS = salir
                        if (st.PS) { running = false; break; }
                    }
                }
                catch (TimeoutException) { }
                catch { }
            }

            // Keyboard siempre activo
            if (IsKeyDown(0x57) || IsKeyDown(0x26)) forward = true;
            if (IsKeyDown(0x53) || IsKeyDown(0x28)) backward = true;
            if (IsKeyDown(0x41) || IsKeyDown(0x25)) left = true;
            if (IsKeyDown(0x44) || IsKeyDown(0x27)) right = true;
            bool sp = IsKeyDown(0x20); if (sp && !prevSp) turbo = !turbo; prevSp = sp;
            bool ll = IsKeyDown(0x4C); if (ll && !prevL) lights = !lights; prevL = ll;
            if (IsKeyDown(0x1B)) { running = false; break; }

            // Send command
            bool t = turbo || momentTurbo;
            var enc = BuildCommand(forward, backward, left, right, lights, t ? (byte)0x64 : (byte)0x50);
            var w = new DataWriter(); w.WriteBytes(enc);
            try { await controlChar.WriteValueAsync(w.DetachBuffer(), GattWriteOption.WriteWithoutResponse); } catch { }

            // Rumble: vibra cuando hay turbo + movimiento
            bool moving = forward || backward;
            if (t && moving)
                SetRumble(ds4Stream, ds4Bluetooth, 50, 120);  // vibracion constante suave
            else
                SetRumble(ds4Stream, ds4Bluetooth, 0, 0);     // sin vibracion

            // Display
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
            string inp = ds4Stream != null ? "DS4-BT" : "KBD";
            Console.Write($"\r  {dir} | {(t?"TURBO!":"normal"),-7} | Luces:{(lights?"ON ":"OFF")} | Bat:{bat,-4} | {inp}  ");

            await Task.Delay(10);
        }

        // Stop
        var stop = BuildCommand(false, false, false, false, lights, 0x50);
        var sw2 = new DataWriter(); sw2.WriteBytes(stop);
        try { await controlChar.WriteValueAsync(sw2.DetachBuffer(), GattWriteOption.WriteWithoutResponse); } catch { }

        ds4Stream?.Close();
        device.Dispose();
        Console.WriteLine("\n\n  Desconectado!");
    }
}
