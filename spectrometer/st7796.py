import time
import spidev
import logging
import numpy as np
from gpiozero import *

from .performance import PerformanceMetrics


SPI_Freq = 40000000     # SPI 时钟频率
SPI_Mode = 0            # 模式0
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
    
    def dre_rectangle(self, Xstart, Ystart, Xend, Yend, color):
        color_high = (color >> 8) & 0xFF
        color_low = color & 0xFF
            
        self.set_windows( Xstart, Ystart, Xend, Yend) 
        for a in range (Xstart, Xend+1):
            for b in range (Ystart , Yend + 1):
                self.data(color_high)
                self.data(color_low)
    
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
        
    def set_windows(self, Xstart, Ystart, Xend, Yend, horizontal = 0):
        # Send each four-byte coordinate payload in one SPI transaction. The
        # original driver used one transaction per byte, which dominates the
        # two small dirty-region updates on the Pi Zero.
        coordinates = ((0x2A, Xstart, Xend, '_window_x'),
                       (0x2B, Ystart, Yend, '_window_y'))
        for command, start, end, cache_name in coordinates:
            value = (start, end)
            if getattr(self, cache_name, None) == value:
                continue
            self.digital_write(self.GPIO_DC_PIN, False)
            self.spi_writebyte([command])
            self.digital_write(self.GPIO_DC_PIN, True)
            self.spi_writebyte([start >> 8, start & 0xff, end >> 8, end & 0xff])
            setattr(self, cache_name, value)
        self.digital_write(self.GPIO_DC_PIN, False)
        self.spi_writebyte([0x2C])
    
    
    def show_image_windows(self, Xstart, Ystart, Xend, Yend, Image):

        # """Set buffer to value of Python Imaging Library image."""
        # """Write display buffer to physical display"""
        imwidth, imheight = Image.size
        if imwidth != self.width or imheight != self.height:
            raise ValueError('Image must be same dimensions as display \
                ({0}x{1}).' .format(self.width, self.height))
        img = self.np.asarray(Image)
        pix = self.np.zeros((imheight,imwidth , 2), dtype = self.np.uint8)
        #RGB888 >> RGB565
        pix[...,[0]] = self.np.add(self.np.bitwise_and(img[...,[0]],0xF8),self.np.right_shift(img[...,[1]],5))
        pix[...,[1]] = self.np.add(self.np.bitwise_and(self.np.left_shift(img[...,[1]],3),0xE0), self.np.right_shift(img[...,[2]],3))
        pix = pix.flatten().tolist()
            
        if Xstart > Xend:
            data = Xstart
            Xstart = Xend
            Xend = data
            
        if Ystart > Yend:        
            data = Ystart
            Ystart = Yend
            Yend = data
        
        if Xend < self.width - 1:
            Xend = Xend + 1
        if Yend < self.width - 1:
            Yend = Yend + 1
            
        self.set_windows( Xstart, Ystart, Xend, Yend)
        self.digital_write(self.GPIO_DC_PIN,True)
        
        for i in range (Ystart,Yend):             
            Addr = ((Xstart) + (i * 240)) * 2        
            self.spi_writebyte(pix[Addr : Addr+((Xend-Xstart+1)*2)])

    def _write_pixels(self, image, mirror=False):
        """Pack RGB565 without building a Python integer list for each frame."""
        rgb = self.np.asarray(image.convert("RGB"))
        self._write_rgb_pixels(rgb, mirror)

    def prepare_rgb565(self, rgb, mirror=False):
        """Return an owned RGB565 buffer ready for a later SPI transfer."""
        started = time.monotonic()
        if mirror:
            rgb = rgb[:, ::-1]
        shape = (*rgb.shape[:2], 2)
        buffers = getattr(self, '_packed_buffers', None)
        if buffers is None:
            buffers = self._packed_buffers = {}
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

    def _write_rgb_pixels(self, rgb, mirror=False):
        self._send_rgb565(self.prepare_rgb565(rgb, mirror))

    def _set_orientation(self, value):
        if getattr(self, '_orientation', None) != value:
            self.command(0x36)
            self.data(value)
            self._orientation = value
            self._window_x = self._window_y = None

    def show_rgb(self, width, height, pixels):
        """Write RGB888 bytes directly, avoiding a Pillow image allocation."""
        if (width, height) not in ((self.width, self.height), (self.height, self.width)):
            raise ValueError("Image must match the portrait or landscape display size")
        rgb = self.np.frombuffer(pixels, dtype=self.np.uint8)
        if rgb.size != width * height * 3:
            raise ValueError('RGB data length does not match its dimensions')
        self.show_array(width, height, rgb.reshape(height, width, 3))

    def show_array(self, width, height, rgb):
        """Write a height×width RGB view, including strided Pygame views."""
        if (width, height) not in ((self.width, self.height), (self.height, self.width)):
            raise ValueError("Image must match the portrait or landscape display size")
        if rgb.shape != (height, width, 3) or rgb.dtype != self.np.uint8:
            raise ValueError('RGB array shape or type does not match its dimensions')
        landscape = (width, height) == (self.height, self.width)
        self._set_orientation(0x78 if landscape else 0x08)
        self.set_windows(0, 0, width - 1, height - 1, int(landscape))
        self._write_rgb_pixels(rgb, mirror=landscape)

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
            self.set_windows(0, 0, width - 1, height - 1, 1)
        else:
            self.set_windows(self.height - x - width, y,
                             self.height - x - 1, y + height - 1, 1)
        self._send_rgb565(pixels)

    def show_image(self, Image):
        size = Image.size
        if size not in ((self.width, self.height), (self.height, self.width)):
            raise ValueError("Image must match the portrait or landscape display size")
        landscape = size == (self.height, self.width)
        self._set_orientation(0x78 if landscape else 0x08)
        self.set_windows(0, 0, size[0] - 1, size[1] - 1, int(landscape))
        self._write_pixels(Image, mirror=landscape)

    def show_region(self, x, y, image):
        """Write a landscape patch in logical (unmirrored UI) coordinates."""
        width, height = image.size
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > self.height or y + height > self.width:
            raise ValueError("Region is outside the landscape display")
        self._set_orientation(0x78)
        # Reflect both the patch position and its columns, just like a full frame.
        self.set_windows(self.height - x - width, y,
                         self.height - x - 1, y + height - 1, 1)
        self._write_pixels(image, mirror=True)

    def show_region_rgb(self, x, y, width, height, pixels):
        """Write an RGB888 landscape patch without passing through Pillow."""
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > self.height or y + height > self.width:
            raise ValueError("Region is outside the landscape display")
        rgb = self.np.frombuffer(pixels, dtype=self.np.uint8)
        if rgb.size != width * height * 3:
            raise ValueError('RGB data length does not match its dimensions')
        self.show_region_array(x, y, width, height, rgb.reshape(height, width, 3))

    def show_region_array(self, x, y, width, height, rgb):
        """Write a height×width RGB patch view in landscape coordinates."""
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > self.height or y + height > self.width:
            raise ValueError("Region is outside the landscape display")
        if rgb.shape != (height, width, 3) or rgb.dtype != self.np.uint8:
            raise ValueError('RGB array shape or type does not match its dimensions')
        self._set_orientation(0x78)
        self.set_windows(self.height - x - width, y,
                         self.height - x - 1, y + height - 1, 1)
        self._write_rgb_pixels(rgb, mirror=True)

    def clear(self):
        """Clear contents of image buffer"""
        _buffer = [0xff] * (self.width*self.height*2)
        self.command(0x36)
        self.data(0x08)
        self.set_windows(0, 0, self.width - 1, self.height - 1)
        self.digital_write(self.GPIO_DC_PIN,True)
        for i in range(0, len(_buffer), 4096):
            self.spi_writebyte(_buffer[i: i+4096])

    def close(self):
        """Release SPI and GPIO resources and turn off the backlight."""
        self.GPIO_BL_PIN.off()
        self.SPI.close()
        self.GPIO_BL_PIN.close()
        self.GPIO_DC_PIN.close()
        self.GPIO_RST_PIN.close()
    
    
