#!/usr/bin/env python
import serial
import binascii
import argparse
import paho.mqtt.client as mqtt
from Cryptodome.Cipher import AES
from traceback import print_exc

# DSMR parsing
has_dsmr_parser = True
try:
    from dsmr_parser import telegram_specifications
    from dsmr_parser.parsers import TelegramParser

    from dsmr_parser import obis_references
    import dsmr_parser.obis_name_mapping
    print("Using DSMR parser")
except ImportError:
    has_dsmr_parser = False
    print("No DSMR parser found")

class SmartyProxy():
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
        self._buffer = ""
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

    def main(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('key', help="Decryption key")
        parser.add_argument('-i', '--serial-input-port', required=False, default="/dev/ttyUSB0", help="Input port. Defaults to /dev/ttyUSB0.")
        parser.add_argument('-o', '--serial-output-port', required=False, help="Output port, e.g. /dev/pts/2.")
        parser.add_argument('-a', '--aad', required=False, default="3000112233445566778899AABBCCDDEEFF", help="Additional authenticated data")
        parser.add_argument('-p', '--parse', action='store_true', required=False, default=False, help="Parse and pretty print DSMR v5 telegram")
        parser.add_argument('-m', '--mqtt-broker', required=False, help="MQTT broker ip/dns")
        parser.add_argument('-q', '--mqtt-port', required=False, default=1883, type=int, help="MQTT broker port")
        parser.add_argument('-u', '--mqtt-user', required=False, help="MQTT broker username")
        parser.add_argument('-v', '--mqtt-password', required=False, help="MQTT broker password")
        
        self._args = parser.parse_args()
        
        self.connect()
        
        self._useMQTT = self._args.mqtt_broker is not None
        if self._useMQTT:
            print("Using broker: {}".format(self._args.mqtt_broker))
            if has_dsmr_parser:
                try:
                    print("Connect MQTT...")
                    self._client = mqtt.Client("SmartMeter")
                    self._client.username_pw_set(self._args.mqtt_user, self._args.mqtt_password)
                    self._client.connect(self._args.mqtt_broker, self._args.mqtt_port)
                    print("MQTT Connection successful")
                except:
                    print("MQTT-connection went wrong!")
                    sys.exit()
            else:
                print("DSMR-parser not found. MQTT disabled!")
                self._useMQTT = False
            
        while True:
            self.process()

    # Connect to the serial port when we run the script
    def connect(self):
        try:
            self._connection = serial.Serial(
                port=self._args.serial_input_port,
                baudrate=115200,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
            )
        except (serial.SerialException, OSError) as err:
            print("ERROR")

    # Start processing incoming data
    def process(self):
        try:
            raw_data = self._connection.read()
        except serial.SerialException:
            return

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
                print("Framecounter")
                print(self._frame_counter)
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
        key = binascii.unhexlify(self._args.key)
        additional_data = binascii.unhexlify(self._args.aad)
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
            if has_dsmr_parser:
                try:
                    parser = TelegramParser(telegram_specifications.V5)

                    telegram = parser.parse(decryption.decode())
                    for key in telegram:
                        myname = dsmr_parser.obis_name_mapping.EN[key]
                        try:
                            myvalue = int(telegram[key].value)
                            myvalue_is_int = True
                        except:
                            myvalue = telegram[key].value
                            myvalue_is_int = False
                        myunit = telegram[key].unit
                        if self._args.parse:
                            print("%s: %s [%s]" % (myname, myvalue, myunit))
                        if self._useMQTT and myvalue_is_int:
                            self._client.publish("Smartmeter/{}".format(myname), myvalue)
                        elif self._useMQTT and key == "P1_MESSAGE_TIMESTAMP":
                            self._client.publish("Smartmeter/{}".format(myname), myvalue.isoformat().encode('utf-8'))
                        elif self._useMQTT and not myvalue_is_int:
                            self._client.publish("Smartmeter/{}".format(myname), str(myvalue).encode('utf-8'))
                except:
                    print("ERROR: Cannot parse DSMR Telegram")
                    print(decryption)
                    print_exc()
            else:
                print(decryption)


            if self._args.serial_output_port:
                self.write_to_serial_port(decryption)
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

    # Write the decrypted data to a serial port (e.g. one created with socat)
    def write_to_serial_port(self, decryption):
        ser = serial.Serial(
            port=self._args.serial_output_port,
            baudrate=115200,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
        ser.write(decryption)
        ser.close()


if __name__ == '__main__':
    smarty_proxy = SmartyProxy()
    smarty_proxy.main()
