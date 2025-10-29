#!/usr/bin/python -u
#  
# sample reader python script.
#
# purpose: set a digital output pin on event.tag.alarm event and start timer, 
#          Cancel timer on each event reception, when timer expires (no more events received)
#          clear the digital output pin.
#
# inputs: optional, pin - digital output pin number (1-4). Default is digital in 1. 
#         optional, time - time, in milliseconds for the timer to run. Default
#         is 1000 milliseconds (1 sec), so after 1 second of no alarms, turn off the digital output pin. 
#         Timer resolution is only seconds, so 1500 will time out after 1 second.
#
# examples: 
#    eas_alarm.py          # sets digital output 1 on EASalarm event, timer 1000 milliseconds
#    eas_alarm.py 2        # sets digital output 2 on EASalarm event, timer 1000 milliseconds
#    eas_alarm.py 4 2000   # sets digital output 4 on EASalarm event, timer 2000 milliseconds
#    eas_alarm.py 3 4000 0 # sets digital output 3 on EASalarm event, timer 4000 milliseconds,
#

import saturn
import re
import commands
import threading
import sys
from sys import argv

count = 0                                                                               

#                                                                                       
# function to respond to timer pops                                                     
#                                                                                       
def polled_timer():                                                                     
    global timer_running                                                                
    global dio_pin                                                                      
    global cmd                                                                          
    global count
                                                                                        
    # turn off the timer running indicator                                              
    timer_running = 0                                                                   
    count = 0
                                                                                        
    # turn off dio output                                                                
    print "Timer expired: turnoff DIO"                                                                    
    rc = cmd.sendcommand('dio.out.'+dio_pin+'=0')                                       
    if rc[0] != "ok":                                                                   
        print "setup error timer: %s " % rc[0]                                          
                                                                                        
                                                                                        
#                                                 
# callback for dio events                        
#                                                
def dio_mon(data):                               
                                                 
    global timer_running                         
    global dio_ll                                
    global dio_pin                               
    global cmd                                   
    global runtime                               
    global count                                 
    global t                                     
                                                 
    count = count + 1                            
                                                 
    if ((count & 31) == 0):
        print "event # %d" % count + " = "+data      
                                                 
    # if the timer is still running from previous events,
    # cancel the timer                                  
    if timer_running == 1 :                              
        if (t.isAlive()):                                
            #print "Cancel timer"                         
            t.cancel()                                   
            timer_running = 0                            
                                                         
    # turn on dio output                                 
    rc = cmd.sendcommand('dio.out.'+dio_pin+'=1')        
    if rc[0] != "ok":                                    
        print "setup error monitor: %s " % rc[0]         
                                                         
    # start the timer                                    
    timeout=runtime                                      
    t = threading.Timer(timeout, polled_timer)           
    t.start()                                            
    timer_running = 1                                    
                                                         

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
# register for dio events
#
cmd.sendcommand('reader.events.register(id='+evtid+',name=event.tag.alarm'+')')
evt.receive(dio_mon)

cmd.close()
evt.close()