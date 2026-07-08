# DSMR Smart Meter Bridge for Sagemcom T210-D-R

This project is a decrypter and Home Assistant bridge for Sagemcom T210-D-R smart meters from Energie Steiermark. It reads encrypted DSMR telegrams from the meter, decrypts them, and publishes the parsed values to MQTT so Home Assistant can pick them up automatically.

This project builds on the original work by Michel Weimerskirch and the later adaptation by Matthias K. Scharrer. Thank you both for the original implementation and inspiration.

It works with a direct serial connection or with a socket-based virtual serial endpoint such as ser2net.

## What it does

- decrypts DSMR telegrams using the provider keys GUEK and GAK/AAD
- reads from a local serial device or a socket endpoint
- parses the decrypted values into meaningful meter readings
- publishes values to MQTT with Home Assistant discovery support
- can be run as a systemd service

## Prerequisites

Before you start, make sure you have:

- a Sagemcom T210-D-R smart meter with access to the P1 port
- a suitable cable or adapter for the P1 port
  - e.g. https://www.amazon.de/dp/B07JGKJ6SM (I used this one)
  - or this one: https://www.amazon.de/dp/B08FB741QM
- the decryption keys from Energie Steiermark: GUEK and GAK/AAD
- a running MQTT broker if you want to publish values to Home Assistant

## Installation

### 1. Install system dependencies

On Debian/Ubuntu-based systems, install the basic runtime packages:

```bash
sudo apt update
sudo apt install python3-venv python3-pip
```

### 2. Clone the project and enter the folder

```bash
cd /opt
git clone https://github.com/zup2/dsmr-smartmeter-bridge.git
cd /opt/dsmr-smartmeter-bridge
```

### 3. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install Python dependencies

```bash
pip install --upgrade pip
pip install pycryptodomex paho-mqtt pyserial dsmr-parser
```

### 5. Create your local configuration

Copy the example file and edit it with your values:

```bash
cp .env.example .env
nano .env
```

Example values:

```env
GUEK=YOUR_GUEK_HERE
GAK=YOUR_GAK_HERE
SERIAL_INPUT_PORT=/dev/ttyUSB0
DEBUG=false
MQTT_BROKER=localhost
MQTT_PORT=1883
MQTT_USER=
MQTT_PASSWORD=
MQTT_DISCOVERY_PREFIX=homeassistant
MQTT_BASE_TOPIC=Smartmeter
MQTT_DEVICE_NAME=Smartmeter
```

### 6. Run it manually once

```bash
python decrypt.py --env-file .env
```

If everything works, you should see decoded meter values printed to the console.

## Supported input ports

The value of SERIAL_INPUT_PORT can be one of the following:

- a local serial device, for example /dev/ttyUSB0 or /dev/ttyS0
- a socket endpoint such as socket://host:port for ser2net or similar tools
- other pyserial-compatible URL-based serial backends, if you use them

## P1 RJ11 wiring notes (Sagemcom T210-D-R)

For the RJ11 socket on the Sagemcom T210-D-R, three pins are required:

- Pin 2 = RX: must be held high (5V) to trigger data transmission from the meter
- Pin 3 = GND
- Pin 5 = TX: uses an inverted signal level, so the TTL adapter input must support inverted logic

If your adapter does not support inverted RX input directly, use hardware that supports signal inversion, or invert the signal yourself (for example with an NPN transistor).

## Running as a systemd service

The project includes a service file at [dsmr-smartmeter-bridge.service](dsmr-smartmeter-bridge.service). To install it on the system, copy it to the systemd directory and enable it:

```bash
sudo cp dsmr-smartmeter-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dsmr-smartmeter-bridge.service
```

The service expects the local .env file in the project directory and uses it automatically.

## Home Assistant setup

If MQTT is enabled in the .env file, the script publishes Home Assistant MQTT discovery messages automatically.

### Status topics and meaning

The bridge publishes two different MQTT status topics:

- Smartmeter/status
  - Purpose: bridge process and MQTT connection availability
  - online: the bridge is connected to MQTT and running
  - offline: the bridge process stopped unexpectedly or lost MQTT availability (last will / reconnect failure)
- Smartmeter/input_status
  - Purpose: health of the meter input source (serial or socket input)
  - online: input connection could be opened successfully
  - offline: input connection failed, read errors occurred repeatedly, or reads stalled repeatedly and reconnect was triggered

In short:

- Smartmeter/status tells you whether the bridge itself is available in MQTT.
- Smartmeter/input_status tells you whether meter input data can currently be read reliably.

### What should appear in Home Assistant?

You should see entities under the MQTT integration, typically with names such as:

- ELECTRICITY_IMPORTED_TOTAL
- ELECTRICITY_DELIVERED_TOTAL
- CURRENT_ELECTRICITY_USAGE
- CURRENT_ELECTRICITY_DELIVERY
- P1_MESSAGE_TIMESTAMP

The bridge publishes the main energy and power values in a Home Assistant-friendly way:

- total imported energy as an energy sensor with total_increasing
- total delivered energy as an energy sensor with total_increasing
- current usage and delivery as power sensors
- timestamp as a timestamp entity when available

### Recommended Home Assistant steps

1. Make sure MQTT is enabled in Home Assistant.
2. Add the MQTT integration if it is not already present.
3. Start the bridge with MQTT configured in .env.
4. Wait for the discovery messages to appear.
5. Check the MQTT integration entity list and the Energy dashboard.

### Values that are useful for the Energy dashboard

For the Energy dashboard, the most relevant values are:

- ELECTRICITY_IMPORTED_TOTAL
- ELECTRICITY_DELIVERED_TOTAL
- CURRENT_ELECTRICITY_USAGE
- CURRENT_ELECTRICITY_DELIVERY

The energy values are the best candidates for the imported and exported energy meters, while the current power values are useful for live consumption and delivery monitoring.

## Notes

- The script prints concise summaries by default and can be run in verbose mode with --debug.
- If you do not want to publish to MQTT, leave MQTT_BROKER empty.
- The real .env file is local and should not be committed to Git.

## Further information

- [P1 Port Pin-Out for Energie Steiermark's electricity meters](EN_Update_Kundenschnittstelle_Smart_Meter_(03_2024)_WEB_RGB.pdf)
