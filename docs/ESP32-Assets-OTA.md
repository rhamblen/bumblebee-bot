# ESP32 Assets Partition OTA

> Researched 2026-06-16. Sources: `main/assets.cc`, `main/mcp_server.cc`, `main/Kconfig.projbuild` in [github.com/78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32); [github.com/78/xiaozhi-assets-generator](https://github.com/78/xiaozhi-assets-generator).

## The two flash partitions

The Xiaozhi firmware uses two completely independent partitions:

| Partition | Contents | How to update |
|---|---|---|
| `app` | Main firmware binary | Full OTA firmware swap or USB flash |
| `assets` | Wake word model + fonts + icons + emoji + themes | Assets-only OTA via MCP command — **no firmware reflash** |

The `assets` partition is loaded at runtime via `esp_partition_mmap()`. Swapping it does not touch the firmware at all. This is what the [xiaozhi.me console](https://xiaozhi.me/console/agents) does when it offers "OTA update fonts/icons".

## What the assets binary contains

The assets binary (`assets.bin`) is produced by the **assets generator** and bundles:

- `srmodels.bin` — the ESP-SR speech recognition / wake word model
- Fonts (CBinFont format)
- Emoji image sets (21 images per set)
- Theme resources — background images and colours for light/dark modes

## How to trigger an assets update on QT (no USB required)

Send two MCP commands over the existing WebSocket. The gateway already has WS MCP wiring from J2.

**Step 1 — tell the device where to fetch the new binary:**

```json
{
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "self.assets.set_download_url",
      "arguments": { "url": "http://gateway:5011/assets/current.bin" }
    },
    "id": 1
  }
}
```

The URL is persisted in device settings under `assets/download_url`.

**Step 2 — reboot to trigger the download:**

```json
{
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": { "name": "self.reboot", "arguments": {} },
    "id": 2
  }
}
```

On boot, `Assets::Download(url)` erases the assets partition sector-by-sector, writes the new binary, and calls `InitializePartition()` to validate before continuing the boot sequence.

## Wake word via the assets generator

**Preset mode** — choose from built-in WakeNet9 phrases:
- nihaoxiaozhi, Hi ESP, Alexa, Hi Lexi, Hi Lily, Jarvis, Hi Ann, Hi Max, … (~26 total)

**Custom mode (ESP32-S3 only)** — specify an arbitrary phrase; generator produces a Multinet model:
- English phrases → `mn7_en`
- Chinese phrases → `mn6_cn`
- Parameters: threshold (0–100) and timeout (ms)

QT is ESP32-S3, so custom mode applies. Target phrases: **"Hey Bee"** or **"Bumblebee"**.

> **Build-time constraint:** `USE_CUSTOM_WAKE_WORD` (required for custom phrases) is set at firmware build time and allows only one phrase. It cannot be combined with AFE multi-word mode. However, the model binary itself lives in the assets partition and can be swapped freely via OTA without rebuilding the firmware.

## Screen icons and themes

The same assets binary carries Bumblebee-branded assets for QT's display:

- **Emoji set** — replace default emoji with Bumblebee-themed expressions (21 PNGs or GIFs)
- **Fonts** — upload a custom TTF/WOFF
- **Theme colours** — background, text colour for light/dark modes
- **Background image** — static image behind the chat UI

All of these are configured in the assets generator alongside the wake word, compiled into one `assets.bin`, and pushed in a single OTA.

## Build plan

```
[assets generator]  →  assets.bin  →  served at gateway:5011/assets/current.bin
                                    →  gateway sends self.assets.set_download_url
                                    →  gateway sends self.reboot
                                    →  QT downloads, flashes assets partition, reboots
                                    →  new wake word + Bumblebee icons active
```

Gateway already provides:
- Port 5011 (OTA/discovery endpoint, `server.py`)
- WS MCP send capability (built in J2)

Remaining work:
- Run assets generator for target wake word + Bumblebee emoji/theme
- Add `/assets/current.bin` static file endpoint to gateway (port 5011)
- Add admin console action (or auto-trigger) to send the two MCP commands above
