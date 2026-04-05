# QCAR x DualShock 4 Controller

Controla el auto a control remoto **Shell QCAR** (promocion Shell V-Power) desde un **DualShock 4 (PS4)** por Bluetooth o USB, con feedback haptico (vibracion en turbo).

![Windows](https://img.shields.io/badge/Windows-10%2F11-blue)
![.NET](https://img.shields.io/badge/.NET-7.0-purple)
![BLE](https://img.shields.io/badge/BLE-4.0-green)

## Que es esto?

Los autos Shell QCAR son autos a escala de las promociones de Shell V-Power que se controlan por Bluetooth Low Energy. El protocolo oficial esta encriptado con AES-128 y solo funciona con la app oficial de Shell.

Este proyecto hace ingenieria inversa del protocolo BLE y permite controlar el auto con un **DualShock 4 de PS4** (por Bluetooth o USB), con soporte de **turbo**, **luces** y **vibracion haptica**.

## Descargar

Descarga el ejecutable desde [Releases](../../releases) - no necesita instalacion, solo ejecutar `QCARController.exe`.

> Requiere Windows 10/11 con Bluetooth. Si queres compilar desde source, necesitas .NET 7.0 SDK.

## Controles

### DualShock 4 (PS4) - Bluetooth o USB

| Boton | Accion |
|-------|--------|
| **R1** | Acelerar |
| **L1** | Reversa |
| **Stick izquierdo** | Direccion |
| **D-Pad** | Direccion |
| **X (Cruz)** | Toggle Turbo (80% ↔ 100%) |
| **O (Circulo)** | Toggle Luces |
| **Triangle** | Turbo momentaneo (mientras se mantiene) |
| **PS** | Salir |

> Cuando el turbo esta activo y acelerás, **el control vibra**.

### Teclado (siempre activo como fallback)

| Tecla | Accion |
|-------|--------|
| W / ↑ | Adelante |
| S / ↓ | Reversa |
| A / ← | Izquierda |
| D / → | Derecha |
| SPACE | Toggle Turbo |
| L | Toggle Luces |
| ESC | Salir |

## Como usar

### 1. Conectar el DualShock 4

**Por Bluetooth (recomendado):**
1. En el DS4, mantene **SHARE + PS** hasta que la luz parpadee rapido
2. En Windows: Configuracion → Bluetooth → Agregar dispositivo
3. Selecciona **"Wireless Controller"**

**Por USB:** simplemente conectalo con el cable.

### 2. Encender el QCAR
Prende el autito Shell con el switch. La luz del auto deberia parpadear.

### 3. Ejecutar

```bash
# Desde el exe
./release/QCARController.exe

# O compilar y ejecutar desde source
dotnet run
```

El programa va a:
1. Detectar el DualShock 4 (Bluetooth o USB)
2. Escanear y conectarse al QCAR por BLE
3. Estabilizar la conexion (500ms de idle)
4. Darte el control!

## Ingenieria inversa del protocolo

### Dispositivo

| Propiedad | Valor |
|-----------|-------|
| Nombre BLE | `QCAR-XXXXXX` (ultimos 6 hex del MAC) |
| Chip | TR1911R02 (RivieraWaves BLE SoC) |
| Fabricante ID | `0x5452` ("TR") |
| Bateria | 3.7V 86mAh LiPo (~20 min) |
| Bluetooth | 4.0 LE |

### Servicios GATT

| Servicio | UUID | Uso |
|----------|------|-----|
| Generic Access | `0x1800` | Nombre del dispositivo |
| Generic Attribute | `0x1801` | Service Changed |
| **Control** | `0xFFF0` | Control del auto |
| Config | `0xFD00` | Firmware/OTA |

### Caracteristicas del servicio de control (`0xFFF0`)

| UUID | Propiedad | Uso |
|------|-----------|-----|
| `d44bc439-...-925416129600` | Write | **Comandos de control** |
| `d44bc439-...-92541612960a` | Write | Secundaria (desc: "11A") |
| `d44bc439-...-92541612960b` | Write | Terciaria |
| `d44bc439-...-925416129601` | Notify | **Bateria** (cada 60s) |

### Encriptacion

- **Algoritmo**: AES-128 ECB (bloques de 16 bytes, sin padding)
- **Key**: `34 52 2a 5b 7a 6e 49 2c 08 09 0a 9d 8d 2a 23 f8`

### Formato del comando (16 bytes plaintext → AES encrypt → write)

| Byte | Campo | Valores |
|------|-------|---------|
| 0 | Reservado | `0x00` |
| 1-3 | Header | `0x43 0x54 0x4C` ("CTL") |
| 4 | Forward | `0x00` = off, `0x01` = on |
| 5 | Backward | `0x00` = off, `0x01` = on |
| 6 | Left | `0x00` = off, `0x01` = on |
| 7 | Right | `0x00` = off, `0x01` = on |
| 8 | Lights | `0x00` = ON, `0x01` = OFF (invertido!) |
| 9 | Speed | `0x50` (80%) = normal, `0x64` (100%) = turbo |
| 10-15 | Padding | `0x00` |

> Los comandos se envian cada **10ms**. Si se deja de enviar, el auto se detiene.

### Formato de notificacion de bateria (16 bytes encriptados)

| Byte | Campo |
|------|-------|
| 0 | Contador secuencial |
| 1-3 | Header `0x56 0x42 0x54` ("VBT") |
| 4 | Porcentaje bateria (0-100) |
| 5-15 | Padding |

### Comandos pre-encriptados (listos para enviar)

| Comando | Bytes encriptados |
|---------|-------------------|
| Stop | `02 5e 69 5a 48 ff 2a 43 8c a6 80 f8 3e 04 e4 5d` |
| Forward | `29 60 9c 66 48 52 cf f1 b0 f0 cb b9 80 14 bd 2c` |
| Forward Turbo | `e6 55 67 da 8e 6c 56 0d 09 d3 73 3a 7f 47 ff 06` |
| Backward | `03 20 99 09 ba 9d a1 c8 b9 86 16 3c 6d 48 46 55` |
| Backward Turbo | `ce c2 ff 1d 7a cc 16 3c d1 3b 7e 61 53 ad 5c 45` |
| Left | `51 38 21 12 13 5c cc db 46 cf 89 21 b7 05 49 9a` |
| Right | `1b 57 69 cd f1 3e 8a b6 27 08 0f f3 ce fc 3b c0` |
| Forward+Left | `99 28 e5 90 df e8 21 48 5f 41 4f bb 63 3d 5c 4e` |
| Forward+Right | `0f 2c e5 66 62 d4 fd 9d 32 a4 4f 10 2b f2 0a a7` |

## Limitaciones

- **Throttle es binario** — no hay control de velocidad analogico, solo on/off + turbo
- **Steering es binario** — izquierda o derecha, sin angulo proporcional
- **Bateria limitada** — ~20 minutos de uso continuo
- **Alcance BLE** — ~10 metros en condiciones normales

## Build desde source

```bash
# Requisitos: .NET 7.0 SDK + Windows 10/11

# Compilar
dotnet build

# Ejecutar
dotnet run

# Publicar exe standalone
dotnet publish -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true
```

## Creditos

- Protocolo documentado por la comunidad: [scrool/qcar-docs](https://github.com/scrool/qcar-docs)
- Implementaciones de referencia: [tmk907/RacingCarsController](https://github.com/tmk907/RacingCarsController), [csabigee/shell-rc](https://github.com/csabigee/shell-rc)

## Licencia

MIT
