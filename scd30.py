#from datetime import timedelta
#import logging
#import smbus2
import struct
import time
from time import sleep
from i2c.i2c import I2C


def interpret_as_float(integer: int):
    return struct.unpack('!f', struct.pack('!I', integer))[0]

CMD_FIRMWARE_VERSION = [0xD1, 0x00]
CMD_READ_DATA_READY_FLAG = [0x02, 0x02]
CMD_START_MEASUREMENT = [0x00, 0x10]
CMD_READ_MEASUREMENT_INTERVAL = [0x46, 0x00]
CMD_SET_MEASUREMENT_INTERVAL = [0x46, 0x00]
CMD_READ_MEASUREMENT = [0x03, 0x00]


NBYTES_READ_DATA_READY_FLAG = 3
NBYTES_FIRMWARE_VERSION = 3
NBYTES_READ_MEASUREMENT_INTERVAL = 3
NBYTES_READ_MEASUREMENT = 18

PACKET_SIZE = 3

class SCD30:
    
    def __init__(self,  bus :int = 1, address: int = 0x61, sampling_period: int = 1, logger: str = None):
        self.logger = None
        if logger:
            self.logger = logging.getLogger(logger)

        self.sampling_period = sampling_period
        self.i2c = I2C(bus, address)
        #self.__data = Queue(maxsize=20)
        self.__valid = {
            "mass_density": False,
            "particle_count": False,
            "particle_size": False
        }

    def crc_calc(self, data: list) -> int:
        crc = 0xFF
        for i in range(2):
            crc ^= data[i]
            for _ in range(8, 0, -1):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc = crc << 1

        # The checksum only contains 8-bit,
        # so the calculated value has to be masked with 0xFF
        return (crc & 0x0000FF)    
        
    def firmware_version(self) -> str:
        self.i2c.write(CMD_FIRMWARE_VERSION)
        data = self.i2c.read(NBYTES_FIRMWARE_VERSION)

        if self.crc_calc(data[:2]) != data[2]:
            return "CRC mismatched"

        return ".".join(map(str, data[:2]))

    def read_data_ready_flag(self) -> bool:
        self.i2c.write(CMD_READ_DATA_READY_FLAG)
        data = self.i2c.read(NBYTES_READ_DATA_READY_FLAG)

        if self.crc_calc(data[:2]) != data[2]:
            if self.logger:
                self.logger.warning(
                    "'read_data_ready_flag' CRC mismatched!" +
                    "  Data: {data[:2]}" +
                    "  Calculated CRC: {self.crc_calc(data[:2])}" +
                    "  Expected: {data[2]}")
            else:
                print(
                    "'read_data_ready_flag' CRC mismatched!" +
                    "  Data: {data[:2]}" +
                    "  Calculated CRC: {self.crc_calc(data[:2])}" +
                    "  Expected: {data[2]}")

            return False

        return True if data[1] == 1 else False
    
    def start_measurement(self) -> None:
        data = CMD_START_MEASUREMENT
        data.extend([0x00, 0x00])
        data.append(self.crc_calc(data[2:4]))
        self.i2c.write(data)
        sleep(0.05)
        
    def get_measurement_interval(self) -> int:
        """Gets the interval used for periodic measurements.
        Returns:
            measurement interval in seconds or None.
        """
        self.i2c.write(CMD_READ_MEASUREMENT_INTERVAL)
        data = self.i2c.read(NBYTES_READ_MEASUREMENT_INTERVAL)
        for i in range(0, NBYTES_READ_MEASUREMENT_INTERVAL, 3):
            if self.crc_calc(data[i:i+2]) != data[i+2]:
                return "CRC mismatched"

        return data

    def set_measurement_interval(self, interval=2):
        """Sets the interval used for periodic measurements.
        Parameters:
            interval: the interval in seconds within the range [2; 1800].
        The interval setting is stored in non-volatile memory and persists
        after power-off.
        """
        if not 2 <= interval <= 1800:
            raise ValueError("Interval must be in the range [2; 1800] (sec)")

        data = CMD_SET_MEASUREMENT_INTERVAL
        data.extend([0x00, interval])
        data.append(self.crc_calc(data[2:4]))
        self.i2c.write(data)
        sleep(0.05)
        
    def read_measurement(self):
        """Reads out a CO2, temperature and humidity measurement.
        Must only be called if a measurement is available for reading, i.e.
        get_data_ready() returned 1.
        Returns:
            tuple of measurement values (CO2 ppm, Temp 'C, RH %) or None.
        """
        self.i2c.write(CMD_READ_MEASUREMENT)
        time.sleep(5 / 1000)
        data = self.i2c.read(NBYTES_READ_MEASUREMENT)
              
        if data is None or len(data) != 18:
            print("Failed to read measurement, received: " +
                          str(data))
            return None
        
        status = []
        calculated_values = []
        for i in range(0, NBYTES_READ_MEASUREMENT, PACKET_SIZE):
            if self.crc_calc(data[i:i+2]) != data[i+2]:
                return "CRC mismatched"

            status.extend(data[i:i+2])
    
        for i in range(0, 12, 4):
            value = status[i:i+4]
            calculated_values.append(interpret_as_float(value[0] << 24 | value[1] << 16 | value[2] << 8 | value[3]))   
        
        result = {
            "co2_ppm": calculated_values[0],
            "temp_celsius": calculated_values[1],
            "rh_percent": calculated_values[2]
            }
        
        return result
        