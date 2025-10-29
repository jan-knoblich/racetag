#!/usr/bin/python -u
#
# sample reader python script.
#
# purpose: turn on a digital output pin upon a successful tag read
#
# inputs: optional, pin - digital output pin number (1-4). Default is digital out 1
#         optional, time, in miliseconds, to keep the digital output high. Default
#         is 1000 milliseconds (1 sec).
#         optional, logic level for digital out "on", 0 or 1. Default is 1 for
#         "on"
#         
##
# This routine will turn on a dio output if a tag is successfully read.  The
# dio output pin number can be specified on the command line.  If not
# specified, a dio output 1 is used.
# The output pin will remain high for n milliseconds, where n is either the default
# of 1000 milliseconds, or the value supplied on the command line.  Minimum
# value for n is 10 milliseconds.  

# examples: 
#    signal_read.py         # turns on digital output 1 for 1000 milliseconds on tag reads
#    signal_read.py 2       # turns on digital output 2 for 1000 milliseconds on tag reads
#    signal_read.py 1 5000  # turns on digital output 1 for 5000 milliseconds on tag reads
#    signal_read.py 1 500   # turns on digital output 1 for 500 milliseconds on tag reads
#    signal_read.py 1 800 0 # turns on digital output 1, logic level 0, for 800 milliseconds on tag reads
# 

import saturn
import re
import commands
import time 
import sys
from sys import argv

#
# callback for dio events
#
def tag_arrive(data):

    try:
      # parse the respone
      print "tag arrive  %s " % (data)
    except: 
      print "setup error" 
    rc = cmd.sendcommand('dio.out.'+dio_pin+'='+dio_hi)
    if rc[0] != "ok":
        print "setup error for dio.out: %s " % rc[0]
    print "sleep for : %f " % on_time 
    time.sleep(on_time)
    print "done sleep for : %f " % on_time 
    rc = cmd.sendcommand('dio.out.'+dio_pin+'='+dio_lo)
    if rc[0] != "ok":
        print "setup error for dio.out: %s " % rc[0]
   
#
# default for dio out pin number 
#
try:
   dio_pin = argv[1]
except:
   dio_pin = "1"
#
# default for on time for dio out pulse 
#
try:
   on_time_ms = float(argv[2])
   on_time = float(on_time_ms/1000)
except:
   on_time = 1
#
# default for output logic level
#
try:
   if int(argv[3]) not in range(0,2):
       dio_hi = "1"
   else:
       dio_hi = str(argv[3])
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
print "on time = %f" % on_time

#
# make sure the dio output pin is off 
#
rc = cmd.sendcommand('dio.out.'+dio_pin+'='+dio_lo)
if rc[0] != "ok":
    print "setup error for dio.out: %s " % rc[0]

#
# register for tag arrive events
#
cmd.sendcommand('reader.events.register(id='+evtid+',name=event.tag.arrive)')
evt.receive(tag_arrive)

cmd.close()
evt.close()