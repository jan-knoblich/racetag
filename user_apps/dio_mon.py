#!/usr/bin/python -u
#
# sample reader python script.
#
# purpose: monitor a dio input pin, take action on change of state of the pin 
#
# inputs: pin - digital i/o pin number (1-4) 
#         optional, logic level for digital input  0 or 1. Default is 1 
#
# The script has two modes of operation, depending on the value of
# reader variable user.varx, where the "x" in varx is equal to the
# input pin number
#
# Mode 1 - user.varx = 0
# 
# This routine will monitor the state of the digital i/o pin specified as
# the input parameter.  If the state of the pin is low, set the operating
# mode to standby.  If the i/o pin changes to a high state, set the
# operating mode to polled.  
#
# Mode 2 - user.varx > 0
#
# Use the value of user.varx as the  
# time in seconds for a timer.  When the i/o pin is set high, start the
# timer and set the operating mode to polled.  While the timer is running,
# state changes to the i/o pin are ignored.  When the timer expires, set
# the operating mode to standby
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
    # turn of the timer running indicator
    timer_running = 0
    #
    # get the current operating mode
    #
    rc = cmd.sendcommand('setup.operating_mode')
    if rc[0] == "ok":
        if rc[1] == "polled":
            # timer popped and we're in polled mode.
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
   
    # if the timer is still running from previous events,
    # ignore this event
    if timer_running == 1 :
        return
    
    try:
      # parse the respone
      dio_string = re.split('([=])', data)
      if dio_string[2] == dio_ll:
         # the io pin is high, go to polled mode
         print "dio.in.%s:  %s - " % (dio_pin, dio_string[2]),
         rc = cmd.sendcommand('setup.operating_mode=polled') 
         if rc[0] != "ok":
             print "setup error: %s " % rc[0]
         #
         # get the current status of user.varx 
         #
         rc = cmd.sendcommand('user.var'+dio_pin+'')
         if rc[0] == "ok":
             if rc[1] != "0":
                 # debug variable is on, start the timer
                 timeout=int(rc[1])
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
   
def db_mon():
   
   global dio_pin 
   rc = cmd.sendcommand('user.var'+dio_pin+'')
   print "dio=%s" % rc[1]
   
#
# work around for problem passing more than 1 parm
#
try:
   dio_pin = argv[1]
except:
   dio_pin = "1"
#
# default for input logic level
#
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

# set the timer running variable off
timer_running = 0

rc = cmd.sendcommand('user.var'+dio_pin+'')
if rc[0] == "ok":
    try: 
        if rc[1] != "0":
            pass
    except IndexError:
        rc = cmd.sendcommand('user.var'+dio_pin+'=0')
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
   #
   # get the current status of user.varx 
   #
   rc = cmd.sendcommand('user.var'+dio_pin+'')
   if rc[0] == "ok":
       try: 
           if rc[1] != "0":
               # debug variable is on, start the timer
               timeout=int(rc[1])
               t = threading.Timer(timeout, polled_timer)
               t.start()
               timer_running = 1
       except IndexError:
           rc = cmd.sendcommand('user.var'+dio_pin+'=0')
else: 
   print "dio.in.%s:  %s    - " % (dio_pin, rc[1]),
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