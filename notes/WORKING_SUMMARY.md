# Working Summary — VPN Converter / Archetype CSV Download Tool
*Delete this file when task is complete.*

---

## Resolution

**The vpn-converter was not needed.** The regular `audio-converter` (port 5007) downloads YouTube clips fine without VPN. The VPN was actually making things worse — NordVPN datacenter exit nodes are flagged by YouTube and get bot-detected. Regular home ISP traffic goes through undetected.

The "VPN OK" notes in `archetype-person.csv` were misleading.

---

## What Is Now Working

- `process_archetype_csv.py` points back to **port 5007** (audio-converter, no VPN)
- Test clip downloaded successfully: `https://youtu.be/7fADm6ubBE0` 6:28–7:00
- `vpn-converter` (port 5008) still running — kept in compose in case geo-restricted content comes up later

---

## What Was Built Tonight (vpn-converter)

A new Docker service `docker/vpn-converter/` that:
- Runs OpenVPN inside the container using NordVPN config from `/mnt/user/appdata/openvpn-client/vpn.ovpn`
- Fixes routing after VPN connects (restores LAN + Docker routes that NordVPN's `redirect-gateway def1` hijacks)
- Exposes same `/download` + `/convert` API as audio-converter on port 5008
- NordVPN credentials hardcoded in `docker-compose.yml` as `VPN_AUTH`
- Cookies support at `/mnt/user/appdata/vpn-converter/cookies/cookies.txt`

It works as a service (VPN connects, port reachable) — just YouTube doesn't like VPN IPs.

---

## Immediate Next Step

Run the batch download:
```powershell
python process_archetype_csv.py
```

Some YouTube URLs in the CSV may be unavailable (deleted videos) — these will need replacement URLs found manually.

---

## Broader Project State (Bumblebee Bot)

The bumblebee stack on UR1 has these services running:
| Service | Port | Status |
|---------|------|--------|
| f5-tts | 5003 | ✅ Running |
| parler-tts | 5004 | ✅ Running |
| coqui-tts | 5002 | ✅ Running |
| chatterbox | 5006 | ✅ Running |
| audio-converter | 5007 | ✅ Running |
| bumblebee-orchestrator | 5005 | ✅ Running |
| vpn-converter | 5008 | ✅ Running (not needed for CSV work) |

The reference clip library is the current focus — building up voice samples for each character from `archetype-person.csv` so TTS cloning can be trained per character.
