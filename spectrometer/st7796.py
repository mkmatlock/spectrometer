import time
import spidev
import numpy as np
from gpiozero import *

from .performance import PerformanceMetrics


SPI_Freq = 40000000     # SPI 时钟频率
BL_Freq  = 1000         # PWM 频率（背光）
RST_PIN  = 27
DC_PIN   = 25
BL_PIN   = 18
METRICS = PerformanceMetrics('lcd-driver')



class st7796():
    def __init__(self):
        self.np=np
        self.width  = 320
        self.height = 480 
        self._orientation = None
        self._window_x = self._window_y = None
        self._packed_buffers = {}
        
        self.GPIO_RST_PIN = DigitalOutputDevice(RST_PIN,active_high = True,initial_value =True)    # RST 设置为输出 参数：引脚，高电平有效，默认高          # 使用GPIO Zero库中的DigitalOutputDevice类
        self.GPIO_DC_PIN  = DigitalOutputDevice(DC_PIN,active_high = True,initial_value =True)     # DC 设置为输出 参数：引脚，高电平有效，默认高           # 使用GPIO Zero库中的DigitalOutputDevice类
        self.GPIO_BL_PIN  = PWMOutputDevice(BL_PIN,frequency = BL_Freq)                            # BL 设置为PWM  参数：引脚，PWM 频率                    # 使用GPIO Zero库中的PWMOutputDevice类
        self.bl_DutyCycle(100)    
        #Initialize SPI
        self.SPI = spidev.SpiDev(0,0)
        self.SPI.max_speed_hz = SPI_Freq  
        self.SPI.mode = 0b00   
        
        self.lcd_init()
    
    def bl_DutyCycle(self, duty):                   # 设置 PWM 占空比
        self.GPIO_BL_PIN.value = duty / 100

    
    
    def digital_write(self, Pin, value):
        if value:
            Pin.on()
        else:
            Pin.off()
            
    def spi_writebyte(self, data):
        if self.SPI!=None :
            self.SPI.writebytes(data)
    
    def command(self, cmd):
        self.digital_write(self.GPIO_DC_PIN, False)
        self.spi_writebyte([cmd])   
        
    def data(self, val):
        self.digital_write(self.GPIO_DC_PIN, True)
        self.spi_writebyte([val])  
        
    def reset(self):
        """Reset the display"""
        self.digital_write(self.GPIO_RST_PIN,True)
        time.sleep(0.01)
        self.digital_write(self.GPIO_RST_PIN,False)
        time.sleep(0.01)
        self.digital_write(self.GPIO_RST_PIN,True)
        time.sleep(0.01)
    
    def lcd_init(self):
        self.reset()
        self.command(0x11)      
        time.sleep(0.12)

        self.command(0x36)      # Memory Data Access Control MY,MX~~
        self.data(0x08)    

        self.command(0x3A)      
        self.data(0x05)    # self.data(0x66) 

        self.command(0xF0)      # Command Set Control
        self.data(0xC3)    

        self.command(0xF0)      
        self.data(0x96)    

        self.command(0xB4)      
        self.data(0x01)    

        self.command(0xB7)      
        self.data(0xC6)    

        self.command(0xC0)      
        self.data(0x80)    
        self.data(0x45)    

        self.command(0xC1)      
        self.data(0x13)    # 18  #00

        self.command(0xC2)      
        self.data(0xA7)    

        self.command(0xC5)      
        self.data(0x0A)    

        self.command(0xE8)      
        self.data(0x40) 
        self.data(0x8A) 
        self.data(0x00) 
        self.data(0x00) 
        self.data(0x29) 
        self.data(0x19) 
        self.data(0xA5) 
        self.data(0x33) 

        self.command(0xE0) 
        self.data(0xD0) 
        self.data(0x08) 
        self.data(0x0F) 
        self.data(0x06) 
        self.data(0x06) 
        self.data(0x33) 
        self.data(0x30) 
        self.data(0x33) 
        self.data(0x47) 
        self.data(0x17) 
        self.data(0x13) 
        self.data(0x13) 
        self.data(0x2B) 
        self.data(0x31) 

        self.command(0xE1) 
        self.data(0xD0) 
        self.data(0x0A) 
        self.data(0x11) 
        self.data(0x0B) 
        self.data(0x09) 
        self.data(0x07) 
        self.data(0x2F) 
        self.data(0x33) 
        self.data(0x47) 
        self.data(0x38) 
        self.data(0x15) 
        self.data(0x16) 
        self.data(0x2C) 
        self.data(0x32) 
    
        self.command(0xF0)      
        self.data(0x3C)    

        self.command(0xF0)      
        self.data(0x69)    
        
        
        self.command(0x21)

        self.command(0x11)

        time.sleep(0.1)

        self.command(0x29)
        
    def set_windows(self, Xstart, Ystart, Xend, Yend):
        # Send each four-byte coordinate payload in one SPI transaction. The
        # original driver used one transaction per byte, which dominates the
        # two small dirty-region updates on the Pi Zero.
        coordinates = ((0x2A, Xstart, Xend, '_window_x'),
                       (0x2B, Ystart, Yend, '_window_y'))
        for command, start, end, cache_name in coordinates:
            value = (start, end)
            if getattr(self, cache_name) == value:
                continue
            self.digital_write(self.GPIO_DC_PIN, False)
            self.spi_writebyte([command])
            self.digital_write(self.GPIO_DC_PIN, True)
            self.spi_writebyte([start >> 8, start & 0xff, end >> 8, end & 0xff])
            setattr(self, cache_name, value)
        self.digital_write(self.GPIO_DC_PIN, False)
        self.spi_writebyte([0x2C])
    
    
    def prepare_rgb565(self, rgb, mirror=False):
        """Return an owned RGB565 buffer ready for a later SPI transfer."""
        started = time.monotonic()
        if mirror:
            rgb = rgb[:, ::-1]
        shape = (*rgb.shape[:2], 2)
        buffers = self._packed_buffers
        cached = buffers.get(shape)
        if cached is None:
            cached = buffers[shape] = (self.np.empty(shape, dtype=self.np.uint8),
                                       self.np.empty(shape[:2], dtype=self.np.uint8))
        packed, scratch = cached
        high, low = packed[..., 0], packed[..., 1]
        self.np.left_shift(rgb[..., 1], 3, out=low)
        self.np.bitwise_and(low, 0xe0, out=low)
        self.np.right_shift(rgb[..., 2], 3, out=scratch)
        self.np.bitwise_or(low, scratch, out=low)
        self.np.bitwise_and(rgb[..., 0], 0xf8, out=high)
        self.np.right_shift(rgb[..., 1], 5, out=scratch)
        self.np.bitwise_or(high, scratch, out=high)
        METRICS.add('pack_ms', (time.monotonic() - started) * 1000)
        return packed.tobytes()

    def _send_rgb565(self, pixels):
        self.digital_write(self.GPIO_DC_PIN, True)
        # writebytes2 accepts a buffer and splits it according to spidev's
        # transfer limit, avoiding Python lists and per-chunk Python calls.
        started = time.monotonic()
        self.SPI.writebytes2(pixels)
        METRICS.add('spi_ms', (time.monotonic() - started) * 1000)
        METRICS.add('bytes', len(pixels))

    def _set_orientation(self, value):
        if self._orientation != value:
            self.command(0x36)
            self.data(value)
            self._orientation = value
            self._window_x = self._window_y = None

    def prepare_array(self, width, height, rgb, mirror=False):
        if rgb.shape != (height, width, 3) or rgb.dtype != self.np.uint8:
            raise ValueError('RGB array shape or type does not match its dimensions')
        return self.prepare_rgb565(rgb, mirror=mirror)

    def write_prepared(self, x, y, width, height, pixels, full=False):
        """Transfer an immutable RGB565 buffer prepared by the UI thread."""
        if len(pixels) != width * height * 2:
            raise ValueError('RGB565 data length does not match its dimensions')
        self._set_orientation(0x78)
        if full:
            self.set_windows(0, 0, width - 1, height - 1)
        else:
            self.set_windows(self.height - x - width, y,
                             self.height - x - 1, y + height - 1)
        self._send_rgb565(pixels)

    def close(self):
        """Release SPI and GPIO resources and turn off the backlight."""
        self.GPIO_BL_PIN.off()
        self.SPI.close()
        self.GPIO_BL_PIN.close()
        self.GPIO_DC_PIN.close()
        self.GPIO_RST_PIN.close()
    
    
