import time
import smbus
from gpiozero import *
import RPi.GPIO   


FT6336U_ADDRESS = 0x38

FT6336U_LCD_TOUCH_MAX_POINTS = 2

TP_INT   = 4
TP_RST   = 17

    


class ft6336u():
    def __init__(self):
        self.GPIO = RPi.GPIO
        self.GPIO.setmode(self.GPIO.BCM)
        self.GPIO.setwarnings(False)
        self.I2C = smbus.SMBus(1)
        self.GPIO.setup(TP_RST, self.GPIO.OUT)
        self.GPIO_TP_INT = Button(TP_INT)                                                  # 使用GPIO Zero库中的Button类
        
        self.coordinates = [{"x": 0, "y": 0} for _ in range(FT6336U_LCD_TOUCH_MAX_POINTS)]
        self.point_count = 0
        self.touch_rst()
        # Use normal working mode and polling mode, matching the UI loop.
        self.I2C.write_byte_data(FT6336U_ADDRESS, 0x00, 0x00)
        self.I2C.write_byte_data(FT6336U_ADDRESS, 0xA4, 0x00)
    
    def touch_rst(self):
        self.GPIO.output(TP_RST, 0)  
        time.sleep(1 / 1000.0)
        self.GPIO.output(TP_RST, 1)  
        time.sleep(50 / 1000.0)
        
        
    def read_bytes(self, reg_addr, length):
        # 发送寄存器地址并读取多个字节
        data = self.I2C.read_i2c_block_data(FT6336U_ADDRESS, reg_addr, length)
        return data
    
    def read_touch_data(self):
        # Read TD_STATUS and both point records in one transaction so a
        # release between separate reads cannot mix two different samples.
        buf = self.read_bytes(0x02, 1 + 6 * FT6336U_LCD_TOUCH_MAX_POINTS)
        self.point_count = 0
        if not buf or len(buf) != 13:
            return
        count = (buf[0] & 0x0f) if buf else 0
        if 0 < count <= FT6336U_LCD_TOUCH_MAX_POINTS:
            for i in range(count):
                offset = 1 + 6 * i
                event = buf[offset] >> 6
                # 0 = down, 2 = contact; 1 = up, 3 = reserved.
                if event not in (0, 2):
                    continue
                self.coordinates[self.point_count]["x"] = 319 - (
                    ((buf[offset] & 0x0f) << 8) | buf[offset + 1])
                self.coordinates[self.point_count]["y"] = (
                    ((buf[offset + 2] & 0x0f) << 8) | buf[offset + 3])
                self.point_count += 1
    
    def get_touch_xy(self):
        point = self.point_count
        # 将触摸点数重置为 0
        self.point_count = 0

        if point != 0:
            # 返回触摸状态和坐标
            return point, self.coordinates
        else:
            # 返回 0 和空坐标列表
            return 0 , []

    def close(self):
        """Release only the GPIO pins and I2C bus owned by this driver."""
        self.I2C.close()
        self.GPIO_TP_INT.close()
        self.GPIO.cleanup(TP_RST)
    
    
        
