#!/usr/bin/env python
import argparse
import binascii
import json
import os
import re
import sys
from pathlib import Path
from traceback import print_exc

import serial

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None
    print("paho-mqtt not installed; MQTT will be disabled")

from Cryptodome.Cipher import AES

# DSMR parsing
has_dsmr_parser = True
try:
    from dsmr_parser import telegram_specifications
    from dsmr_parser.parsers import TelegramParser
    print("Using DSMR parser")
except Exception as exc:
    has_dsmr_parser = False
    print("No DSMR parser found: {}".format(exc))

def load_env_file(env_path=None):
    env_path = Path(env_path or ".env")
    values = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class SmartMeterDecryptor():
    def __init__(self):
        # Constants that describe the individual steps of the state machine:

        # Initial state. Input is ignored until start byte is detected.
        self.STATE_IGNORING = 0
        # Start byte (hex "DB") has been detected.
        self.STATE_STARTED = 1
        # Length of system title has been read.
        self.STATE_HAS_SYSTEM_TITLE_LENGTH = 2
        # System title has been read.
        self.STATE_HAS_SYSTEM_TITLE = 3
        # Additional byte after the system title has been read.
        self.STATE_HAS_SYSTEM_TITLE_SUFFIX = 4
        # Length of remaining data has been read.
        self.STATE_HAS_DATA_LENGTH = 5
        # Additional byte after the remaining data length has been read.
        self.STATE_HAS_SEPARATOR = 6
        # Frame counter has been read.
        self.STATE_HAS_FRAME_COUNTER = 7
        # Payload has been read.
        self.STATE_HAS_PAYLOAD = 8
        # GCM tag has been read.
        self.STATE_HAS_GCM_TAG = 9
        # All input has been read. After this, we switch back to STATE_IGNORING and wait for a new start byte.
        self.STATE_DONE = 10

        # Command line arguments
        self._args = {}

        # Serial connection from which we read the data from the smart meter
        self._connection = None

        # Initial empty values. These will be filled as content is read
        # and they will be reset each time we go back to the initial state.
        self._state = self.STATE_IGNORING
        self._buffer = b""
        self._buffer_length = 0
        self._next_state = 0
        self._system_title_length = 0
        self._system_title = b""
        self._data_length_bytes = b""  # length of "remaining data" in bytes
        self._data_length = 0  # length of "remaining data" as an integer
        self._frame_counter = b""
        self._payload = b""
        self._gcm_tag = b""
        
        # Use MQTT (True | False)
        self._useMQTT = False
        self._client = None
        self._debug = False
        self._mqtt_connected = False
        self._mqtt_discovery_registered = set()
        self._fallback_obis_map = {
            "0-0:1.0.0": "P1_MESSAGE_TIMESTAMP",
            "1-3:0.2.8": "P1_MESSAGE_HEADER",
            "1-0:1.8.0": "ELECTRICITY_IMPORTED_TOTAL",
            "1-0:1.8.1": "ELECTRICITY_IMPORTED_TARIFF_2",
            "1-0:1.8.2": "ELECTRICITY_IMPORTED_TARIFF_3",
            "1-0:2.8.0": "ELECTRICITY_DELIVERED_TOTAL",
            "1-0:2.8.1": "ELECTRICITY_DELIVERED_TARIFF_2",
            "1-0:2.8.2": "ELECTRICITY_DELIVERED_TARIFF_3",
            "1-0:1.7.0": "CURRENT_ELECTRICITY_USAGE",
            "1-0:2.7.0": "CURRENT_ELECTRICITY_DELIVERY",
        }
        self._mqtt_discovery_prefix = "homeassistant"
        self._mqtt_base_topic = "Smartmeter"
        self._mqtt_device_name = "Smartmeter"

    def main(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('--guek', required=False, help="GUEK / decryption key")
        parser.add_argument('--gak', '--aad', dest='gak', required=False, help="GAK / additional authenticated data")
        parser.add_argument('-i', '--serial-input-port', required=False, help="Input port. Supports serial device paths and URLs such as socket://host:port.")
        parser.add_argument('-p', '--parse', action='store_true', required=False, default=False, help="Parse and pretty print DSMR v5 telegram")
        parser.add_argument('--debug', action='store_true', required=False, default=False, help="Print raw serial traffic and decrypted values to the console")
        parser.add_argument('--baudrate', required=False, default=115200, type=int, help="Serial baudrate for input/output ports")
        parser.add_argument('-m', '--mqtt-broker', required=False, help="MQTT broker ip/dns")
        parser.add_argument('-q', '--mqtt-port', required=False, default=1883, type=int, help="MQTT broker port")
        parser.add_argument('-u', '--mqtt-user', required=False, help="MQTT broker username")
        parser.add_argument('-v', '--mqtt-password', required=False, help="MQTT broker password")
        parser.add_argument('--mqtt-discovery-prefix', required=False, default="homeassistant", help="Home Assistant MQTT discovery prefix")
        parser.add_argument('--mqtt-base-topic', required=False, default="Smartmeter", help="Base MQTT topic for meter values")
        parser.add_argument('--mqtt-device-name', required=False, default="Smartmeter", help="Device name used in Home Assistant discovery")
        parser.add_argument('--env-file', required=False, default=".env", help="Path to a dotenv file with configuration values")
        
        self._args = parser.parse_args()

        env_values = load_env_file(self._args.env_file)
        for key in ["guek", "gak", "serial_input_port", "mqtt_broker", "mqtt_port", "mqtt_user", "mqtt_password", "mqtt_discovery_prefix", "mqtt_base_topic", "mqtt_device_name"]:
            env_key = key.upper()
            if key == "serial_input_port":
                env_key = "SERIAL_INPUT_PORT"
            elif key == "mqtt_broker":
                env_key = "MQTT_BROKER"
            elif key == "mqtt_port":
                env_key = "MQTT_PORT"
            elif key == "mqtt_user":
                env_key = "MQTT_USER"
            elif key == "mqtt_password":
                env_key = "MQTT_PASSWORD"
            elif key == "mqtt_discovery_prefix":
                env_key = "MQTT_DISCOVERY_PREFIX"
            elif key == "mqtt_base_topic":
                env_key = "MQTT_BASE_TOPIC"
            elif key == "mqtt_device_name":
                env_key = "MQTT_DEVICE_NAME"

            if getattr(self._args, key, None) is None and env_key in env_values:
                setattr(self._args, key, env_values[env_key])

        if self._args.guek is None or self._args.gak is None:
            parser.error("Both --guek and --gak must be provided either via CLI or in the .env file")

        self._debug = self._args.debug or str(env_values.get("DEBUG", "")).lower() in {"1", "true", "yes", "on"}
        self._mqtt_discovery_prefix = self._args.mqtt_discovery_prefix or env_values.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
        self._mqtt_base_topic = self._args.mqtt_base_topic or env_values.get("MQTT_BASE_TOPIC", "Smartmeter")
        self._mqtt_device_name = self._args.mqtt_device_name or env_values.get("MQTT_DEVICE_NAME", "Smartmeter")

        if not self.connect():
            sys.exit(1)

        self._useMQTT = self._args.mqtt_broker is not None
        if self._useMQTT:
            print("Using broker: {}".format(self._args.mqtt_broker))
            if mqtt is None:
                print("paho-mqtt is not available. MQTT disabled.")
                self._useMQTT = False
            else:
                self.connect_mqtt()
                if not self._mqtt_connected:
                    print("MQTT connection failed; continuing without MQTT")
                    self._useMQTT = False
            
        while True:
            self.process()

    def debug_log(self, message):
        if self._debug:
            print("[DEBUG] {}".format(message))

    def slugify(self, text):
        return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()

    def _on_disconnect(self, client, userdata, rc, properties=None):
        self._mqtt_connected = False
        if rc != 0:
            print("MQTT disconnected (rc={})".format(rc))

    def create_mqtt_client(self):
        if hasattr(mqtt, "CallbackAPIVersion"):
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "SmartMeter")
            except TypeError:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "SmartMeter")
        else:
            client = mqtt.Client("SmartMeter")
        client.on_disconnect = self._on_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def connect_mqtt(self):
        if not self._useMQTT:
            return
        try:
            if self._client is None:
                self._client = self.create_mqtt_client()
            self._client.username_pw_set(self._args.mqtt_user, self._args.mqtt_password)
            self._client.connect(self._args.mqtt_broker, self._args.mqtt_port, 10)
            self._client.loop_start()
            self._client.publish("{}/status".format(self._mqtt_base_topic), "online", retain=True)
            self._mqtt_connected = True
            print("MQTT Connection successful")
        except Exception as exc:
            self._mqtt_connected = False
            print("MQTT connection failed: {}".format(exc))

    def publish_mqtt_value(self, myname, myvalue, myunit, key):
        if not self._useMQTT or not self._client or not self._mqtt_connected:
            if self._useMQTT and self._client is not None:
                self.connect_mqtt()
            if not self._mqtt_connected:
                return

        topic = "{}/{}".format(self._mqtt_base_topic, myname)
        if isinstance(myvalue, int):
            payload = str(myvalue)
        elif isinstance(myvalue, float):
            payload = str(myvalue)
        elif hasattr(myvalue, "isoformat"):
            payload = myvalue.isoformat()
        else:
            payload = str(myvalue)

        info = self._client.publish(topic, payload, retain=True)
        if getattr(info, "rc", None) not in (None, mqtt.MQTT_ERR_SUCCESS):
            print("MQTT publish failed for {}".format(myname))

        object_id = self.slugify(myname)
        discovery_topic = "{}/sensor/{}/config".format(self._mqtt_discovery_prefix, object_id)
        if discovery_topic in self._mqtt_discovery_registered:
            return

        payload_config = {
            "name": myname,
            "unique_id": "{}_{}".format(self.slugify(self._mqtt_device_name), object_id),
            "state_topic": topic,
            "availability_topic": "{}/status".format(self._mqtt_base_topic),
            "device": {
                "identifiers": [self.slugify(self._mqtt_device_name)],
                "name": self._mqtt_device_name,
                "manufacturer": "Sagemcom",
                "model": "T210-D-R",
            },
            "retain": True,
            "force_update": True,
        }

        if myname in {"ELECTRICITY_IMPORTED_TOTAL", "ELECTRICITY_DELIVERED_TOTAL"}:
            payload_config["device_class"] = "energy"
            payload_config["state_class"] = "total_increasing"
            payload_config["unit_of_measurement"] = myunit or "kWh"
        elif myname in {"CURRENT_ELECTRICITY_USAGE", "CURRENT_ELECTRICITY_DELIVERY"}:
            payload_config["device_class"] = "power"
            payload_config["state_class"] = "measurement"
            payload_config["unit_of_measurement"] = myunit or "W"
        elif key == "P1_MESSAGE_TIMESTAMP":
            payload_config["device_class"] = "timestamp"

        info = self._client.publish(discovery_topic, json.dumps(payload_config), retain=True)
        if getattr(info, "rc", None) not in (None, mqtt.MQTT_ERR_SUCCESS):
            print("MQTT discovery publish failed for {}".format(myname))
        self._mqtt_discovery_registered.add(discovery_topic)

    def parse_plain_telegram(self, decryption):
        telegram = {}
        text = decryption.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("/EST5") or line.startswith("!"):
                continue
            if "(" not in line or not line.endswith(")"):
                continue
            obis = line.split("(", 1)[0]
            raw_value = line[len(obis) + 1:-1]
            obis_name = self._fallback_obis_map.get(obis, obis)
            if obis == "0-0:1.0.0":
                value = raw_value
                myunit = ""
            else:
                numeric = raw_value.split("*", 1)[0]
                try:
                    value = int(numeric)
                except ValueError:
                    value = raw_value
                myunit = raw_value.split("*", 1)[1] if "*" in raw_value else ""
            telegram[obis_name] = {"value": value, "unit": myunit}
        return telegram

    def print_telegram_summary(self, telegram):
        summary_values = {}
        for key in [
            "P1_MESSAGE_TIMESTAMP",
            "ELECTRICITY_IMPORTED_TOTAL",
            "ELECTRICITY_DELIVERED_TOTAL",
            "CURRENT_ELECTRICITY_USAGE",
            "CURRENT_ELECTRICITY_DELIVERY",
        ]:
            if key in telegram:
                summary_values[key] = telegram[key]["value"] if isinstance(telegram[key], dict) else telegram[key]

        if summary_values:
            parts = []
            if "P1_MESSAGE_TIMESTAMP" in summary_values:
                parts.append("ts={}".format(summary_values["P1_MESSAGE_TIMESTAMP"]))
            if "CURRENT_ELECTRICITY_USAGE" in summary_values:
                parts.append("usage={}W".format(summary_values["CURRENT_ELECTRICITY_USAGE"]))
            if "CURRENT_ELECTRICITY_DELIVERY" in summary_values:
                parts.append("delivery={}W".format(summary_values["CURRENT_ELECTRICITY_DELIVERY"]))
            if "ELECTRICITY_IMPORTED_TOTAL" in summary_values:
                parts.append("import={}Wh".format(summary_values["ELECTRICITY_IMPORTED_TOTAL"]))
            if "ELECTRICITY_DELIVERED_TOTAL" in summary_values:
                parts.append("export={}Wh".format(summary_values["ELECTRICITY_DELIVERED_TOTAL"]))
            print("Telegram summary: {}".format(" | ".join(parts)))

    # Connect to the serial port when we run the script
    def connect(self):
        try:
            if "://" in self._args.serial_input_port:
                self._connection = serial.serial_for_url(
                    self._args.serial_input_port,
                    baudrate=self._args.baudrate,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=1,
                )
            else:
                self._connection = serial.Serial(
                    port=self._args.serial_input_port,
                    baudrate=self._args.baudrate,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=1,
                )
            print("Connected to input port: {}".format(self._args.serial_input_port))
            return True
        except (serial.SerialException, OSError, ValueError) as err:
            print("ERROR: Could not open input port {}: {}".format(self._args.serial_input_port, err))
            return False

    # Start processing incoming data
    def process(self):
        try:
            raw_data = self._connection.read(1)
        except serial.SerialException:
            return

        if not raw_data:
            return

        if self._debug and raw_data in [b'\xdb', b'\x82', b'\x00']:
            self.debug_log("RX {}".format(raw_data.hex()))

        # Read and parse the stream from the serial port byte by byte.
        # This parsing works as a state machine (see the definitions in the __init__ method).
        # See also the official documentation on http://smarty.creos.net/wp-content/uploads/P1PortSpecification.pdf
        # For better human readability, we use the hexadecimal representation of the input.
        hex_input = binascii.hexlify(raw_data)

        # Initial state. Input is ignored until start byte is detected.
        if self._state == self.STATE_IGNORING:
            if hex_input == b'db':
                self._state = self.STATE_STARTED
                self._buffer = b""
                self._buffer_length = 1
                self._system_title_length = 0
                self._system_title = b""
                self._data_length = 0
                self._data_length_bytes = b""
                self._frame_counter = b""
                self._payload = b""
                self._gcm_tag = b""
            else:
                return

        # Start byte (hex "DB") has been detected.
        elif self._state == self.STATE_STARTED:
            self._state = self.STATE_HAS_SYSTEM_TITLE_LENGTH
            self._system_title_length = int(hex_input, 16)
            self._buffer_length = self._buffer_length + 1
            self._next_state = 2 + self._system_title_length  # start bytes + system title length

        # Length of system title has been read.
        elif self._state == self.STATE_HAS_SYSTEM_TITLE_LENGTH:
            if self._buffer_length > self._next_state:
                self._system_title += hex_input
                self._state = self.STATE_HAS_SYSTEM_TITLE
                self._next_state = self._next_state + 2  # read two more bytes
            else:
                self._system_title += hex_input

        # System title has been read.
        elif self._state == self.STATE_HAS_SYSTEM_TITLE:
            if hex_input == b'82':
                self._next_state = self._next_state + 1
                self._state = self.STATE_HAS_SYSTEM_TITLE_SUFFIX  # Ignore separator byte
            else:
                print("ERROR, expected 0x82 separator byte not found, dropping frame")
                self._state = self.STATE_IGNORING
 

        # Additional byte after the system title has been read.
        elif self._state == self.STATE_HAS_SYSTEM_TITLE_SUFFIX:
            if self._buffer_length > self._next_state:
                self._data_length_bytes += hex_input
                self._data_length = int(self._data_length_bytes, 16)
                self._state = self.STATE_HAS_DATA_LENGTH
            else:
                self._data_length_bytes += hex_input

        # Length of remaining data has been read.
        elif self._state == self.STATE_HAS_DATA_LENGTH:
            self._state = self.STATE_HAS_SEPARATOR  # Ignore separator byte
            self._next_state = self._next_state + 1 + 4  # separator byte + 4 bytes for framecounter

        # Additional byte after the remaining data length has been read.
        elif self._state == self.STATE_HAS_SEPARATOR:
            if self._buffer_length > self._next_state:
                self._frame_counter += hex_input
                self.debug_log("Framecounter {}".format(self._frame_counter.decode("ascii", errors="ignore")))
                self._state = self.STATE_HAS_FRAME_COUNTER
                self._next_state = self._next_state + self._data_length - 17
            else:
                self._frame_counter += hex_input

        # Frame counter has been read.
        elif self._state == self.STATE_HAS_FRAME_COUNTER:
            if self._buffer_length > self._next_state:
                self._payload += hex_input
                self._state = self.STATE_HAS_PAYLOAD
                self._next_state = self._next_state + 12
            else:
                self._payload += hex_input

        # Payload has been read.
        elif self._state == self.STATE_HAS_PAYLOAD:
            # All input has been read. After this, we switch back to STATE_IGNORING and wait for a new start byte.
            if self._buffer_length > self._next_state:
                self._gcm_tag += hex_input
                self._state = self.STATE_DONE
            else:
                self._gcm_tag += hex_input

        self._buffer += hex_input
        self._buffer_length = self._buffer_length + 1

        if self._state == self.STATE_DONE:
            # print(self._buffer)
            self.analyze()
            self._state = self.STATE_IGNORING

    # Once we have a full encrypted "telegram", put everything together for decryption.
    def analyze(self):
        key = binascii.unhexlify(self._args.guek)
        additional_data = binascii.unhexlify(self._args.gak)
        iv = binascii.unhexlify(self._system_title + self._frame_counter)
        payload = binascii.unhexlify(self._payload)
        gcm_tag = binascii.unhexlify(self._gcm_tag)

        try:
            decryption = self.decrypt(
                key,
                additional_data,
                iv,
                payload,
                gcm_tag
            )
            if self._debug:
                self.debug_log("Telegram decrypted")

            telegram = self.parse_plain_telegram(decryption)
            print_values = self._args.parse or self._args.debug
            if print_values:
                for key in telegram:
                    myname = key
                    entry = telegram[key]
                    myvalue = entry["value"]
                    myunit = entry["unit"]
                    print("%s: %s [%s]" % (myname, myvalue, myunit))
            else:
                self.print_telegram_summary(telegram)
            for key in telegram:
                myname = key
                entry = telegram[key]
                myvalue = entry["value"]
                myunit = entry["unit"]
                if self._useMQTT:
                    self.publish_mqtt_value(myname, myvalue, myunit, key)

        except:
            print("ERROR on decryption!")

    # Do the actual decryption (AES-GCM)
    def decrypt(self, key, additional_data, iv, payload, gcm_tag):
    #        decryptor = Cipher(
    #            algorithms.AES(key),
    #            modes.GCM(iv, gcm_tag, 12),
    #            backend=default_backend()
    #        ).decryptor()
    #
    #        decryptor.authenticate_additional_data(additional_data)##
    #
    #        return decryptor.update(payload) + decryptor.finalize()
        cipher = AES.new(key, AES.MODE_GCM, iv, mac_len=12)
        cipher.update(additional_data)
        return cipher.decrypt(payload)


if __name__ == '__main__':
    smart_meter_decryptor = SmartMeterDecryptor()
    smart_meter_decryptor.main()
