# QCAR x DualShock 4 - Controller

Controla el auto a control remoto **Shell QCAR** (promocion Shell V-Power) desde un **DualShock 4 (PS4)** conectado por USB, o con el teclado.

## Protocolo

- **Bluetooth Low Energy** (BLE) con encriptacion **AES-128 ECB**
- Chip: TR1911R02 (RivieraWaves BLE SoC)
- Servicio GATT: `0000fff0` / Caracteristica: `d44bc439-abfd-45a2-b575-925416129600`
- Comandos de 16 bytes encriptados enviados cada 10ms

## Controles

### DualShock 4 (PS4)
| Boton | Accion |
|-------|--------|
| R1 | Acelerar |
| L1 | Reversa |
| Stick izq / D-Pad | Direccion |
| X (Cruz) | Toggle Turbo (80% / 100%) |
| O (Circulo) | Toggle Luces |
| Triangle | Turbo momentaneo |
| PS | Salir |

### Teclado (siempre activo)
| Tecla | Accion |
|-------|--------|
| W / Flecha arriba | Adelante |
| S / Flecha abajo | Reversa |
| A / Flecha izq | Izquierda |
| D / Flecha der | Derecha |
| SPACE | Toggle Turbo |
| L | Toggle Luces |
| ESC | Salir |

## Requisitos

- Windows 10/11 con Bluetooth
- .NET 7.0 SDK
- DualShock 4 por USB (opcional)

## Build y ejecutar

```bash
dotnet build
dotnet run
```

## Formato del comando (plaintext, pre-AES)

| Byte | Funcion | Valores |
|------|---------|---------|
| 0 | Reservado | `0x00` |
| 1-3 | Header | `0x43 0x54 0x4C` ("CTL") |
| 4 | Forward | `0x01` = si |
| 5 | Backward | `0x01` = si |
| 6 | Left | `0x01` = si |
| 7 | Right | `0x01` = si |
| 8 | Lights | `0x00` = ON, `0x01` = OFF |
| 9 | Speed | `0x50` = normal (80%), `0x64` = turbo (100%) |
| 10-15 | Padding | `0x00` |

## Creditos

Protocolo reverse-engineered por la comunidad: [scrool/qcar-docs](https://github.com/scrool/qcar-docs)
