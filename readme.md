# Decrypter and proxy for Energie Steiermark "DSMR-P1" meters (Sagemcom T210-D-R)

This piece of code acts as a "proxy" between the Luxembourgish "Smarty" meters and existing open source software that analyse DSMR telegrams. E.g. [dsmr_parser](https://github.com/ndokter/dsmr_parser).

I run this on a small Linux-based laptop (any Raspberry Pi xxx shall work, too) that I have connected to my Sagemcom T210-D-R meter using a serial-to-USB cable.

## Before you start

* Ask Energie Steiermark for the decryption keys (GUEK and GAK/AAD). This might take a day.
* You need a cable to connect whatever you want to use to the "P1 port" of your smart meter. Those cables are available under different names: "DSMR cable", "P1 cable"... I used a "Domotica auf Himbeer P1 Poort Kabel für Slimme Meter mit FTDI Chip 5v TTL UART Logic Level Signalen (Für ISKRA AM550/Sagemcom XS210 T210-D)" from Amazon.
* You need to install the "cryptography" and "serial" libraries for Python. Ubuntu/Debian: ``sudo apt-get install python3-cryptography python3-serial``
* You might need to install "socat" for the examples below. Ubuntu/Debian: ``sudo apt-get install socat``

## Test if everything works

* Run the following on the command line and watch the telegrams appear on screen: ``python3 decrypt.py GUEK -a GAK`` (with "GUEK" being your decryption key and "GAK" being the Global Authentication Key)
* If the serial-to-usb cable is not accessible under /dev/ttyUSB0, you can use the "--serial-input-port" argument to specify which path to read from.
* Push CTRL+C to exit

## To connect your smart meter to dsmr_parser and a MQTT-broker that runs remotely

First, I do *not* use socat.
The major change is another set of command line parameters ``--mqtt-broker``, ``--mqtt-port``, ``--mqtt-user``, ``--mqtt-password``.
Using ``--mqtt-broker`` automatically activates publishing to that broker (``--mqtt-port`` defaults to ``1883``) **IFF** [dsmr_parser](https://github.com/ndokter/dsmr_parser) is installed and accessible.
Depending on your setup, you might also require ``--mqtt-user``, ``--mqtt-password`` to access your broker.

The datagrams received are automatically published with their respective EN-names as defined in [dsmr_parser](https://github.com/ndokter/dsmr_parser), e.g.,

```
P1_MESSAGE_HEADER: 50
P1_MESSAGE_TIMESTAMP: 2022-12-10 18:09:12+00:00
ELECTRICITY_IMPORTED_TOTAL: 15382794
ELECTRICITY_USED_TARIFF_1: 12454752
ELECTRICITY_USED_TARIFF_2: 2928042
ELECTRICITY_DELIVERED_TARIFF_1: 0
ELECTRICITY_DELIVERED_TARIFF_2: 76
CURRENT_ELECTRICITY_USAGE: 466
CURRENT_ELECTRICITY_DELIVERY: 0
```

## Further information

* [DSMR Parser](https://github.com/ndokter/dsmr_parser), the library used by Home Assistant to read meter data
* [DSMR Reader](http://dsmr-reader.readthedocs.io/en/latest/), stand-alone utility to log and view your energy consumption
* [P1 Port Specification for Energie Steiermark's electricity meters](https://www.e-netze.at/downloads-data/pdf.aspx?pdf=EN_Update%20Kundenschnittstelle%20Smart%20Meter_ID3282_WEB_RGB.pdf)
* [P1 Port Specification for Luxembourg's "Smarty" electricity meter](https://smarty.creos.net/wp-content/uploads/P1PortSpecification.pdf), the reference document for this library. It describes how the encryption on top of the DSMR standard works.
