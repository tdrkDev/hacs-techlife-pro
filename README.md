# HACS TechLife Pro

Custom Component for Home Assistant to control TechLife Pro LED strips via MQTT.

## Features
- On/Off control
- Full RGB color control (HS color mode) with proper XOR-checksummed frames
- Brightness control for both RGB strips and white-only bulbs
- Live state parsing from `dev_pub_<mac>` (color, brightness, on/off, light type auto-detect)
- Periodic state refresh (every 30 s) and refresh-on-command
- Helper to build the "change MQTT broker IP" frame, so devices can be re-pointed at a local broker without DNS spoofing (`TechLifeProtocol.get_change_broker_command`)

Protocol implementation is ported from
[Marcoske23/TechLifePro-for-HA](https://github.com/Marcoske23/TechLifePro-for-HA).

## DNS Redirection (REQUIRED)
To use this component, you MUST redirect the traffic from the LED strip to your local MQTT broker. The device tries to connect to `cloud.techlifepro.com` (or sometimes `clim8.techlifepro.com`, `cloud.qh-tek.com`).

### How to Redirect
1. **Identify the domain**: Check your DNS logs (e.g., Pi-hole, AdGuard Home) to see what domain the device requests. Common domains: `cloud.techlifepro.com`.
2. **Configure DNS**: Add a DNS record in your router or DNS server (like Pi-hole) to point that domain to the IP address of your Home Assistant instance (or wherever your MQTT broker is running).
3. **MQTT Broker**: Ensure your MQTT broker handles anonymous connections or that you have configured the integration if the device supports auth (usually they don't, they just connect).
4. **Validation**: Check your MQTT broker logs. You should see a connection from the device and it subscribing to `dev_sub_{MAC}` and publishing to `dev_pub_{MAC}`.

## Installation
1. Install via HACS (Custom Repository).
2. Restart Home Assistant.
3. Go to Settings -> Integrations -> Add Integration -> TechLife Pro.
4. Enter the device MAC address (e.g. `b4:0e:cf:47:38:7b`). One config entry per device — repeat for each strip.

> **Why manual MAC?** TechLife Pro devices are silent until polled — they never announce themselves on the broker. The integration must know each device's MAC up-front to subscribe to `dev_pub_<mac>` and send refreshes to `dev_sub_<mac>`. Look in your MQTT broker logs for connection lines like `... as b4:0e:cf:47:38:7b (...)` to find the MAC.
