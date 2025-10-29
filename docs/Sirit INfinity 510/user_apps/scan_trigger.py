#!/usr/bin/python -u
#
# sample reader python script.
#
# purpose: monitor a digital input pin, take action on change of state of the pin. 
#
# inputs: optional, pin - digital input pin number (1-4). Default is digital in 1 
#         optional, logic level for trigger, 0 or 1. Default is trigger on 1
#
# This routine will monitor the state of the digital input pin specified as
# the input parameter.  If the state of the pin is low, set the operating
# mode to standby.  If the i/o pin changes to a high state, set the
# operating mode to polled. 
#
# examples: 
#    scan_trigger.py      # monitors digital input pin 1, trigger on 1
#    scan_trigger.py 1    # monitors digital input pin 1, trigger on 1 
#    scan_trigger.py 4    # monitors digital input pin 4, trigger on 1
#    scan_trigger.py 3 0  # monitors digital input pin 3, trigger on 0
#



import saturn
import re
import commands
import threading
import sys
from sys import argv

#
# callback for dio events
#
def dio_mon(data):
    global dio_ll

    try:
      # parse the respone
      dio_string = re.split('([=])', data)
      if dio_string[2] == dio_ll:
         # the io pin is high, go to polled mode
         print "dio.in.%s:  %s - " % (dio_pin, dio_string[2]),
         rc = cmd.sendcommand('setup.operating_mode=polled') 
         if rc[0] != "ok":
             print "setup error: %s " % rc[0]
      else: 
         # go to standby mode
         print "dio.in.%s:  %s - " % (dio_pin, dio_string[2]),
         rc = cmd.sendcommand('setup.operating_mode=standby')
         if rc[0] != "ok":
             print "setup error: %s " % rc[0]
    except: 
      print "setup error", 
      print data
    #
    # display the current operating mode
    #
    rc = cmd.sendcommand('setup.operating_mode')
    if rc[0] == "ok":
      print "reader is in %s mode" % rc[1]
    else:
      print "mode error: " % rc
   
#
# if not specified on the command line, set the default for the dio input pin 
#
try:
   dio_pin = argv[1]
except:
   dio_pin = "1"
try:
   if int(argv[2]) not in range(0,2):
       dio_ll = "1"
   else:
       dio_ll = str(argv[2])
except:
   dio_ll = "1"
    
#
# setup the command and event session
#
cmd = saturn.Command('localhost',50007)
evt = saturn.Event('localhost',50008)

evtid = evt.getid()
print "Event Id = ",evtid

#
# get the current status from the command session
#
rc = cmd.sendcommand('dio.in.'+dio_pin+'')
if rc[1] == dio_ll:
   # i/o is high, set mode to polled
   print "dio.in.%s:  %s - " % (dio_pin, rc[1]),
   rc = cmd.sendcommand('setup.operating_mode=polled')
   if rc[0] != "ok":
       print "setup error: %s " % rc[0]
else: 
   print "dio.in.%s:  %s - " % (dio_pin, rc[1]),
   rc = cmd.sendcommand('setup.operating_mode=standby')
   if rc[0] != "ok":
       print "setup error: %s " % rc[0]
#
# display the current operating mode
#
rc = cmd.sendcommand('setup.operating_mode')
if rc[0] == "ok":
  print "reader is in %s mode" % rc[1]
else:
  print "mode error: " % rc
  

#
# register for dio events
#
cmd.sendcommand('reader.events.register(id='+evtid+',name=event.dio.in.'+dio_pin+')')
evt.receive(dio_mon)

cmd.close()
evt.close()