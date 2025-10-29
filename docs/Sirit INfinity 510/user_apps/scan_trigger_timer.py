#!/usr/bin/python -u
#
# sample reader python script.
#
# purpose: monitor a digital inpu pin, take action on change of state of the pin,
# or at the expiration of the timer.
#
# inputs: optional, pin - digital input pin number (1-4). Default is digital in 1. 
#         optinal, time - time, in milliseconds for the timer to run. Default
#         is 1000 milliseconds (1 sec). 
#         optional, logic level for trigger, 0 or 1. Default is trigger on 1
#
#
# When the i/o pin is set high, start the
# timer and set the operating mode to autonomous.  While the timer is running,
# state changes to the i/o pin are ignored.  When the timer expires, set
# the operating mode to standby.  The minimum value for the timer is 10
# milliseconds.
#
# examples: 
#    scan_trigger_timer.py          # monitors digital input 1, timer 1000 milliseconds
#    scan_trigger_timer.py 2        # monitors digital input 2, timer 1000 milliseconds
#    scan_trigger_timer.py 4 2000   # monitors digital input 4, timer 2000 milliseconds
#    scan_trigger_timer.py 3 4000 0 # monitors digital input 3, timer 4000 milliseconds,
#                                   # trigger on 0
#

import saturn
import re
import commands
import threading
import sys
from sys import argv

#
# function to respond to timer pops  
#
def polled_timer():
    global timer_running
    # turn off the timer running indicator
    timer_running = 0
    #
    # get the current operating mode
    #
    rc = cmd.sendcommand('setup.operating_mode')
    if rc[0] == "ok":
        if rc[1] == "autonomous":
            # timer popped and we're in autonomous mode.
            # switch to standby
            print "timer popped, switching to standby"
            rc = cmd.sendcommand('setup.operating_mode=standby')
            if rc[0] != "ok":
                print "setup error: %s " % rc[0]
            else :
                print "reader is in standby mode"
    else:
        print "mode error: " % rc
#
# callback for dio events
#
def dio_mon(data):

    global timer_running
    global dio_ll
   
    # if the timer is still running from previous events,
    # ignore this event
    if timer_running == 1 :
        return
    
    try:
      # parse the respone
      dio_string = re.split('([=])', data)
      if dio_string[2] == dio_ll:
         # the io pin is high, go to autonomous mode
         print "dio.in.%s:  %s - " % (dio_pin, dio_string[2]),
         rc = cmd.sendcommand('setup.operating_mode=autonomous') 
         if rc[0] != "ok":
             print "setup error: %s " % rc[0]
         # start the timer
         timeout=runtime
         t = threading.Timer(timeout, polled_timer)
         t.start()
         timer_running = 1
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
# if not specified on the command line, set the defaults for dio pin and timer
# run time
#
try:
   dio_pin = argv[1]
except:
   dio_pin = "1"
try:
   runtime_ms = float(argv[2])
   runtime = float(runtime_ms/1000)
except:
   runtime = 1
try:
   if int(argv[3]) not in range(0,2):
       dio_ll = "1"
   else:
       dio_ll = str(argv[3])
except:
   dio_ll = "1"
#
# setup the command and event session
#
cmd = saturn.Command('localhost',50007)
evt = saturn.Event('localhost',50008)

evtid = evt.getid()
print "Event Id = ",evtid
print "Runtime = %f" % runtime

# set the timer running variable off
timer_running = 0

#
# get the current status from the command session
#
rc = cmd.sendcommand('dio.in.'+dio_pin+'')
if rc[1] == dio_ll:
   # i/o is high, set mode to autonomous 
   print "dio.in.%s:  %s - " % (dio_pin, rc[1]),
   rc = cmd.sendcommand('setup.operating_mode=autonomous')
   if rc[0] != "ok":
       print "setup error: %s " % rc[0]
   # start the timer
   timeout=runtime
   t = threading.Timer(timeout, polled_timer)
   t.start()
   timer_running = 1
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