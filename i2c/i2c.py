from machine import SoftI2C, Pin

class I2C:

    def __init__(self, bus: int, address: int = 105):
        self.address = address
        self.i2c = SoftI2C(scl=Pin(22), sda=Pin(21), freq=100000)

    def write(self, data: list):
        self.i2c.writeto(self.address, bytearray(data))

    def read(self, nbytes: int) -> list:
        return list(self.i2c.readfrom(self.address, nbytes))

    def close(self):
        return None