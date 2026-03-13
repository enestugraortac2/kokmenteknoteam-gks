import spidev
import RPi.GPIO as GPIO
import time
from PIL import Image, ImageDraw

DC_PIN = 25
RST_PIN = 24

spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 32000000
spi.mode = 0

GPIO.setmode(GPIO.BCM)
GPIO.setup(DC_PIN, GPIO.OUT)
GPIO.setup(RST_PIN, GPIO.OUT)

def command(cmd, data=None):
    GPIO.output(DC_PIN, 0)
    spi.xfer3([cmd])
    if data:
        GPIO.output(DC_PIN, 1)
        spi.xfer3(data)

# Reset
GPIO.output(RST_PIN, 1)
time.sleep(0.05)
GPIO.output(RST_PIN, 0)
time.sleep(0.05)
GPIO.output(RST_PIN, 1)
time.sleep(0.05)

command(0x01) # SWRESET
time.sleep(0.1)

command(0x11) # SLPOUT
time.sleep(0.1)

command(0x3A, [0x55]) # PIXFMT

command(0x36, [0x28]) # MADCTL landscape
command(0x29) # DISPON
time.sleep(0.05)

# Render red screen
img = Image.new("RGB", (320, 240), (255, 0, 0))
r, g, b = img.split()
r = r.point(lambda i: (i >> 3) << 11)
g = g.point(lambda i: (i >> 2) << 5)
b = b.point(lambda i: (i >> 3))

# Fast bitwise merge
# Unfortunately pure python math for pixels is slow.
