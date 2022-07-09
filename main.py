# Copyright 2019 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#https://jayconsystems.com/blog/understanding-a-gas-sensor
#https://github.com/robert-hh/ads1x15/blob/master/ads1x15.py
#https://github.com/micropython-IMU/micropython-bmp180
#https://github.com/dvsu/sps30
#https://github.com/RequestForCoffee/scd30
import esp32 
from third_party import string
import utime
from umqtt.simple import MQTTClient
from third_party import rsa
from ubinascii import b2a_base64
from machine import RTC, Pin, SoftI2C, UART, WDT
import ntptime
import json
import config
import gc
import time
import network
import usocket as socket
import ustruct as struct
from ubinascii import hexlify

class AirPoll:
    def __new__(cls):
        """ creates a singleton object, if it is not created,
        or else returns the previous singleton object"""
        if not hasattr(cls, 'instance'):
            cls.instance = super(AirPoll, cls).__new__(cls)
            cls.instance.initialize()
        return cls.instance
    
    def initialize(self):
        self.MODEM_PWKEY_PIN = Pin(4, Pin.OUT)
        self.MODEM_RST_PIN = Pin(5, Pin.OUT)
        self.MODEM_POWER_ON_PIN = Pin(23, Pin.OUT)
        self.CONNECT_ATTEMPTS = 20
        self.rtc = RTC()
        self.NTP_DELTA = 3155673600
        
        self.device_id = config.google_cloud_config['device_id']
        self.project_id = config.google_cloud_config['project_id']
        self.cloud_region = config.google_cloud_config['cloud_region']
        self.registry_id = config.google_cloud_config['registry_id']
        
        # The NTP host can be configured at runtime by doing: ntptime.host = 'myhost.org'
        self.host = "pool.ntp.org"
        self.mqtt_topic = '/devices/{}/{}'.format(self.device_id, 'events')
        gc.enable() #enable garbage automatic coolector
    
    def ppp_connect(self):
        self.uart = UART(1, tx=27, rx=26, baudrate=115200, timeout=1000)
        self.ppp = network.PPP(self.uart)

    def timet(self):
        NTP_QUERY = bytearray(48)
        NTP_QUERY[0] = 0x1B
        addr = socket.getaddrinfo(self.host, 123)[0][-1]
        s = None
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(5)
            res = s.sendto(NTP_QUERY, addr)
            msg = s.recv(48)
        finally:
            s.close()
        val = struct.unpack("!I", msg[40:44])[0]
        return val - self.NTP_DELTA


    # There's currently no timezone support in MicroPython, and the RTC is set in UTC time.
    def settime(self):
        t = self.timet()
        tm = utime.gmtime(t)
        RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3], tm[4], tm[5], 0))
        
    def on_message(self, topic, message):
        print((topic,message))

    def b42_urlsafe_encode(self, payload):
        return string.translate(b2a_base64(payload)[:-1].decode('utf-8'),{ ord('+'):'-', ord('/'):'_' })

    def create_jwt(self, project_id, private_key, algorithm, token_ttl):
        print("Creating JWT...")
        private_key = rsa.PrivateKey(*private_key)

        # Epoch_offset is needed because micropython epoch is 2000-1-1 and unix is 1970-1-1. Adding 946684800 (30 years)
        epoch_offset = 946684800
        claims = {
                # The time that the token was issued at
                'iat': utime.time() + epoch_offset,
                # The time the token expires.
                'exp': utime.time() + epoch_offset + token_ttl,
                # The audience field should always be set to the GCP project id.
                'aud': project_id
        }

        #This only supports RS256 at this time.
        header = { "alg": algorithm, "typ": "JWT" }
        content = self.b42_urlsafe_encode(json.dumps(header).encode('utf-8'))
        content = content + '.' + self.b42_urlsafe_encode(json.dumps(claims).encode('utf-8'))
        signature = self.b42_urlsafe_encode(rsa.sign(content,private_key,'SHA-256'))
        return content + '.' + signature #signed JWT

    def get_mqtt_client(self, project_id, cloud_region, registry_id, device_id, jwt):
        """Create our MQTT client. The client_id is a unique string that identifies
        this device. For Google Cloud IoT Core, it must be in the format below."""
        client_id = 'projects/{}/locations/{}/registries/{}/devices/{}'.format(project_id, cloud_region, registry_id, device_id)
        print('Sending message with password {}'.format(jwt))
        client = MQTTClient(client_id.encode('utf-8'),server=config.google_cloud_config['mqtt_bridge_hostname'],port=config.google_cloud_config['mqtt_bridge_port'],user=b'ignored',password=jwt.encode('utf-8'),ssl=True)
        client.set_callback(self.on_message)
        client.connect()
        client.subscribe('/devices/{}/config'.format(device_id), 1)
        client.subscribe('/devices/{}/commands/#'.format(device_id), 1)
        return client

    def receive(self):
        x = self.uart.read()
        if x is not None:
            print('Received: {}\n'.format(x))
        return x

    def send(self, data):
        print('Send: {}'.format(data))
        self.uart.write(data)
        time.sleep(0.3)

    def modem_connect(self):
        self.ppp_connect()
        #if MODEM_PWKEY_PIN:
        self.MODEM_PWKEY_PIN.value(0)
        #if MODEM_RST_PIN:
        self.MODEM_RST_PIN.value(1)
        self.MODEM_RST_PIN.value(0)
        #if MODEM_POWER_ON_PIN:
        self.MODEM_POWER_ON_PIN.value(1)
        pp = 0
        pp_2 = 0
        while True:
            if pp == 0:
                self.send('AT')
                x = self.receive()
                if x is not None:
                    if 'AT' in x:
                        pp += 1
                        pp_2 = 0
            elif pp == 1:
                cmds = ['ATE0', 'ATI\r\n', 'AT+CPIN?\r\n', 'AT+CREG=0\r\n', 'AT+CGREG=0\r\n']
                self.send(cmds[pp_2])
                x = self.receive()
                if x is not None:
                    pp_2 += 1
                if pp_2 == len(cmds):
                    pp += 1
                    pp_2 = 0
            elif pp == 2:
                self.send("AT+CREG?\r\n")
                x = self.receive()
                if x is not None:
                    if ('+CREG: 0,5' in x) or ('+CREG: 0,1' in x):
                        pp += 1
                        pp_2 = 0
            elif pp == 3:
                self.send("AT+CGREG?\r\n")
                x = self.receive()
                if x is not None:
                    if ('+CGREG: 0,5' in x) or ('+CGREG: 0,1' in x):
                        pp += 1
                        pp_2 = 0
            elif pp == 4:
                cmds = [
                        'AT+COPS?\r\n', 'AT+CSQ\r\n',
                        #'AT+CNMI=0,0,0,0,0\r\n',
                        'AT+QICSGP=1,1,\"uinternet\",\"\",\"\",0\r\n', #'AT+CGDATA=?\r\n',
                        #'AT+CGDATA="PPP",1',
                        'ATD*99#\r\n'
                        ]
                self.send(cmds[pp_2])
                x = self.receive()
                if x is not None:
                    pp_2 += 1
                if pp_2 == len(cmds):
                    pp += 1
                    pp_2 = 0
            elif pp == 5:
                print('Start PPP')
                self.ppp.active(True)
                self.ppp.connect()
                i = 0
                while i < 30:
                    time.sleep(1)
                    i += 1
                    if self.ppp.isconnected():
                        print(self.ppp.ifconfig())
                        pp += 1
                        break
            elif pp == 6:
                pp += 1
            elif pp == 7:
                print('bye bye')
                return

    def subscribe(self):
        print("ppp status:" + str(self.ppp.isconnected()))
        self.settime()
        print("time:" + str(self.rtc.datetime()))
        jwt = self.create_jwt(config.google_cloud_config['project_id'], config.jwt_config['private_key'], config.jwt_config['algorithm'], config.jwt_config['token_ttl'])

        connect_attempts = self.CONNECT_ATTEMPTS
        while connect_attempts != 0:
            try:
                gc.collect()
                print(gc.mem_free())
                self.client = self.get_mqtt_client(self.project_id, self.cloud_region, self.registry_id, self.device_id, jwt)
                print("sucsess")
                break
            except Exception as e:
                print("client connect error:")
                print(str(e))
                print(gc.mem_free())
                time.sleep(0.2)
                connect_attempts -= 1
                if connect_attempts == 0:
                    gc.collect()
                    import machine
                    machine.reset()
                    
    def timestamp(self):
        return utime.time()+946728000 #Convert J2000 time to epoch
    
    def sensors_init(self):
        from scd30 import SCD30
        from sps30 import SPS30
        from bme680 import BME680_I2C
        self.boot_time = self.timestamp() #Convert J2000 time to epoch
        self.bme680 = BME680_I2C(SoftI2C(scl=Pin(22), sda=Pin(21), freq=100000))
        self.co2_sensor = SCD30()
        self.pm_sensor = SPS30()
        self.co2_sensor.start_measurement()
        self.pm_sensor.start_measurement()
        self.gps_uart = UART(2, 9600)
        self.gps_uart.init(9600, tx=12, rx=14)
                 
    def compose_message(self):
        """from scd30 import SCD30
        from sps30 import SPS30
        from bme680 import BME680_I2C
        
        self.bme680 = BME680_I2C(SoftI2C(scl=Pin(22), sda=Pin(21), freq=100000))
        self.co2_sensor = SCD30()
        self.pm_sensor = SPS30()
        self.co2_sensor.start_measurement()
        self.pm_sensor.start_measurement()
        self.gps_uart = UART(2, 9600)
        self.gps_uart.init(9600, tx=12, rx=14)"""
        message = '{{"device_id":{0},"boot_time":{1},"timestamp":{2},"esp32_temp":{3},"esp32_gc_free_mem":0,"sps30_data":{4},"scd30_data":{5},"gps_data":{6},"bme680":{{"bme680_temperature":{7},"bme680_humidity":{8},"bme680_pressure":{9},"bme680_gas":{10}}}}}'.format(self.device_id,self.boot_time,self.timestamp(),esp32.raw_temperature(),self.pm_sensor.get_measurement(),self.co2_sensor.read_measurement(),self.gps_uart.read(),self.bme680.temperature,self.bme680.humidity,self.bme680.pressure,self.bme680.gas)
            
        """del self.co2_sensor
        del self.pm_sensor
        del self.bme680
        del self.gps_uart
        del SCD30
        del SPS30
        del BME680_I2C"""
        return message
    
    def publish_sensors_data(self):
        self.wdt = WDT(timeout=20000)  # enable it with a timeout of 2s    
        gc.collect()
        num_of_times = 0
        while True:
            try:
                
                #gc.collect()
                print('1',gc.mem_free())
                msg = self.compose_message()
                print(msg)
                self.client.publish(self.mqtt_topic.encode('utf-8'), msg.encode('utf-8'))
                #print('2',gc.mem_free())
                #print("message published")
                #gc.collect()
                #print("message published gc.collect() ")
                
                #print("message published wdt.feed() ")
                #print('3',gc.mem_free())
                self.wdt.feed()
                gc.collect()
                print('2',gc.mem_free())
                time.sleep(7)
                num_of_times = 0
            except Exception as e:
                gc.mem_free()
                gc.collect()
                print("message publish error:")
                print(str(e))
                
                if num_of_times == 0:#For first Exception wait for resources to be released and try again
                    num_of_times += 1
                    time.sleep(12)
                    continue
                else:
                    import machine
                    machine.reset()
    
oAirPoll = AirPoll()
oAirPoll.modem_connect()
oAirPoll.subscribe()
oAirPoll.sensors_init()
oAirPoll.publish_sensors_data()