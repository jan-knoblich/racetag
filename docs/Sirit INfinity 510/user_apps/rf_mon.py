#!/usr/bin/python -u
#
# sample reader python script.
#
# purpose: monitor a rf status, set/clear digital output based on status. 
#
# inputs: optional, pin - digital output pin number (1-4). Default is digital out 1. 
#         optional, logic level for digital out "on", 0 or 1. Default is 1 for
#         "on"
#
# This routine will monitor the state of the transmitter. 
# If the transmitter is on, set the appropriate digital output pin high.  If the state
# is low, set the digital output pin low.
#
# examples: 
#    rf_mon.py         # monitors rf status, set/clear digital output 1 on change
#    rf_mon.py 1       # monitors rf status, set/clear digital output 1 on change
#    rf_mon.py 2       # monitors rf status, set/clear digital output 2 on change
#    rf_mon.py 3 0     # monitors rf status, set/clear digital output 3 on
#                        change, logic level 0 for "on"
#

import saturn
import re
import commands
import threading
import sys
import time
from sys import argv

#
# callback for dio events
#
def tx_mon(data):

    try:
      # parse the respone
      rx_string = re.split('([=])', data)
      if rx_string[2] == "1":
         rc = cmd.sendcommand('dio.out.'+dio_pin+'='+dio_hi)
         if rc[0] != "ok":
             print "setup error for dio out: %s " % rc[0]
      else: 
         rc = cmd.sendcommand('dio.out.'+dio_pin+'='+dio_lo)
         if rc[0] != "ok":
             print "setup error for dio out: %s " % rc[0]
    except: 
      print "setup error", 
      print data
#
# if not set on the command line, set the default for the dio pin 
#
try:
   dio_pin = argv[1]
except:
   dio_pin = "1"
#
# default for output logic level
#
try:
   if int(argv[2]) not in range(0,2):
       dio_hi = "1"
   else:
       dio_hi = str(argv[2])
except:
   dio_hi = "1"

if dio_hi == "1":
  dio_lo = "0"
else:
  dio_lo = "1"

    
#
# setup the command and event session
#
cmd = saturn.Command('localhost',50007)
evt = saturn.Event('localhost',50008)

evtid = evt.getid()
print "Event Id = ",evtid

#
#
print "starting RX monitor, using digital output pin %s" % dio_pin
  
rc = cmd.sendcommand('dio.out.'+dio_pin+'='+dio_lo)
if rc[0] != "ok":
    print "setup error for dio out: %s " % rc[0]

#
# register for tx events
#
cmd.sendcommand('reader.events.register(id='+evtid+',name=event.status.tx_active)')
evt.receive(tx_mon)

cmd.close()
evt.close()