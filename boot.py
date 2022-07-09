# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()
#https://forum.micropython.org/viewtopic.php?t=10307
import micropython
micropython.alloc_emergency_exception_buf(100)