#!/usr/bin/python -u
import socket
import time, sys, readline, re, getopt, os
import select
import traceback
import commands
import re
import saturn
import string
import binascii
import threading
import serial

#print a short help message
def usage():
    sys.stderr.write("""USAGE: %s [options]
    A demonstration of reader dio and event handling.
    Version: %s
   
    options:
    --host=<addr> Set the IP address of the C2 processor (defaults to localhost)
    --din_ll_low  Set digital input active logic level to low (default is high)
    --dout_ll_low Set digital output active logic level to low (default is high)
    --noserial    disable output to the serial port (default is enabled)
    --port=<PORT> serial port, a number (default is 0)
    --baud=<BAUD> serial port baudrate (default 1200)
    --rtscts      enable RTS/CTS flow control (default disabled)
    --xonxoff     enable software flow control (default disabled)
    --debug       enable debug information (default is debug disabled)
    --repr        in debug mode, enable escape nonprintable chars (defult is disabled)
    --scantime=<sec> When in timer mode, scan time in seconds (default 1 sec) 
    --outtime=<sec>  Digital output "on" time (default 1 sec) 
    --static      disable timed mode 
                  digital input 1 high  on turns reader on
                  digital input 1 low off turns reader off
                  (default is timed mode)                    
    --eot_mode=<mode>  0=cr terminator (default) 
                       1=lf terminator           
                       2=crlf terminator           
                       3=ETX terminator           
    --fancy       enable sending of blank and flash LED control characters (default disabled)                    
    --version     display version number
    --help        display usage information
    
    Note: For this app to fuction, the reader must be configured with the serial 
    console program disabled.  This can be accomplished with the C2 command
    "com.serial.console(program=none)". 


""" % (sys.argv[0], VERSION ))

VERSION="1.0"
HOST = 'localhost' # The remote host
COMMAND_PORT = 50007 
EVENT_PORT   = 50008 
SERIAL_PORT=0
BAUDRATE=1200
rtscts=0
xonxoff=0
debug = False
repr_mode = False
bcc_mode = False
tag_found = False
serial_out = True 
timed_mode = True 
scantime=float(1)
plain=True
outtime=1
din_ll = "1"
dout_on = "1"
dout_off = "0"
dout_time = float(1)
eot_mode=0
demo_out=''
CR='\x0D'
LF='\x0A'
CRLF='\x0D\x0A'
ESC='\x1B'
BL_ON='B1'    # blank on
BL_OFF='B0'   # blank off
FL_ON='F1'    # flash on
FL_OFF='F0'   # flash off
EOT=CR

try:
    opts, args = getopt.getopt(sys.argv[1:], "", ["baud=", "sport=", "rtscts", "xonxoff",  "debug", "host=", "repr" ,"noserial", "din_ll_low", "dout_ll_low", "static", "scantime=", "outtime=", "eot_mode=", "fancy", "version"])
except getopt.GetoptError:
    usage()
    sys.exit(2)

for o, a in opts:
    if o == "--host":
        HOST = a 
    if o == "--debug":
        debug = True
    if o == "--repr":
        repr_mode = True
    if o == "--rtscts":
        rtscts=1 
    if o == "--xonxoff":
        xonxoff=1 
    if o == "--baud":
        BAUDRATE = int(a)
    if o == "--sport":
        SERIAL_PORT = int(a)
    if o == "--noserial":
        serial_out = False 
    if o == "--static":
        timed_mode = False 
    if o == "--din_ll_low":
        din_ll = "0" 
    if o == "--fancy":
        plain = False 
    if o == "--eot_mode":
        try:
          eot_mode=int(a)
          if (eot_mode) > 3 or (eot_mode < 0):
            raise ValueError
        except ValueError:
            print "\nERROR: eot_mode must be an number between 0 and 3, not %r" % a
            usage()
            sys.exit(2)
          
    if o == "--outtime":
        try:
            outtime= float(a)
        except ValueError:
            print "\nERROR: douttime must be an number, not %r" % a
            usage()
            sys.exit(2)
    if o == "--scantime":
        try:
            scantime = float(a)
        except ValueError:
            print "\nERROR: scantime must be a number, not %r" % a
            usage()
            sys.exit(2)

    if o == "--dout_ll_low":
        dout_on = "0" 
        dout_off = "1" 

    if o == "--version":    
        print "Program %s - Version: %s" % (sys.argv[0], VERSION)
        sys.exit(0)

#
# process tag info and turn on/off digital io's 
#
def process_info():
    global tag_found
    global demo_out
    
    if plain == False:
      send_serial(ESC+BL_ON) # turn blank on
      send_serial(ESC+BL_OFF) # turn blank off 
      send_serial(ESC+FL_ON) # turn flash on
      
    if tag_found == False:
        # send no tag found
        send_serial("No tag found")
        # turn on digital output 2
        cmd.sendcommand('dio.out.2='+dout_on)
        time.sleep(outtime)
        cmd.sendcommand('dio.out.2='+dout_off)
    else:
        # send the tag id
        send_serial(demo_out)
        # turn on digital output 1 
        cmd.sendcommand('dio.out.1='+dout_on)
        time.sleep(outtime)
        cmd.sendcommand('dio.out.1='+dout_off)
        tag_found = False
        print "sent output %s" % repr(demo_out)
    if plain == False:
      send_serial(ESC+FL_OFF) # turn flash off 
    # clear demo_out for the next run    
    demo_out=''


#
# function to respond to timer pops  
#
def polled_timer():
    global timer_running
    global tag_found
    global demo_out

    #
    # get the current operating mode
    #
    try:
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

              # process the info and turn on/off io's
              process_info()    
                  
          else:
              print "hmmmm... someon else popped us out of auton mode"
              if tag_found == False:
                pass
              else:  
                tag_found = False
                
          
      else:
          print "mode error: " % rc
      # turn off the timer running indicator
      print "turn timer running off"
      timer_running = 0
    except:    
      # turn off the timer running indicator
      timer_running = 0
      tag_found = False
      traceback.print_exc(sys.stdout)


#
# handle a reader mode change based on changes of the digital inputs 
# 
def mode_change(state):

    global timer_running
    global din_ll
   
    if timed_mode:
      # if the timer is still running from previous events,
      # ignore this event
      if timer_running == 1:
          print "timer already running..."
          return
    
    try:
      # parse the respone
      if state == din_ll:
         # the io pin is high, go to autonomous mode
         print "dio.in.%s:  %s - " % (dio_pin, state),
         rc = cmd.sendcommand('setup.operating_mode=autonomous') 
         if rc[0] != "ok":
             print "setup error: %s " % rc[0]
         if timed_mode:
           # start the timer
           if debug:
             print "starting timer"
           timeout=scantime
           t = threading.Timer(timeout, polled_timer)
           t.start()
           timer_running = 1
      else:
         # go to standby mode
         print "dio.in.%s:  %s - " % (dio_pin, state),
         rc = cmd.sendcommand('setup.operating_mode=standby')
         if rc[0] != "ok":
             print "setup error: %s " % rc[0]
             
         if timed_mode == False:
             # not in timed mode, process info and turn on/off dio
             process_info()
         
    except: 
      traceback.print_exc(sys.stdout)
    #
    # display the current operating mode
    #
    rc = cmd.sendcommand('setup.operating_mode')
    if rc[0] == "ok":
        print "mode change reader is in %s mode" % rc[1]
    else:
      print "mode error: " % rc

#
# parse an event, looking for a field name.  if found, return
# the value of the field, up to the field size
#
def parse_event(event, field_name, field_size):
      rdata = "NULL"
      # try to find the field name in the event  
      field_index=event.find(field_name)
      if field_index != -1:
        # field the found, skip over the field name
        field_index += len(field_name)
        # extract the value of the field
        rdata=event[field_index:field_index+field_size]
        if debug:
          print "parse_event %s" % rdata
          
      return rdata

#
# send a response out the serial port
def send_serial(response):
  try:
    if response == "":
      return
    else:
      response = STX + response 
      if bcc_mode:
        # calculate block check (xor of data)
        bcc = 0
        for i in response:
           bcc ^= ord(i)
           
        response = response + EOT + str("%02x" % bcc)
        if debug:
            print "bcc of response %02x" % bcc 
      else:     
        response += EOT 
        if debug:
            if repr_mode:
              print "sending response %s" % repr(response)
            else:
              print "sending response %s" % response 
      
      if serial_out:
        if debug:
          print "writing to serial port...."
        for char in response:
          sport.write(char)
        
      response = ""
  except:   
    traceback.print_exc(file=sys.stdout)

#
# callback to handle digital io events
#
def dio_event(event):

  try:  
    if event == "event.dio.in.1 value=0":
      mode_change("0")
    elif event == "event.dio.in.1 value=1":
      mode_change("1")
    else:  
      print "dio event: %s" % event
  except:   
    traceback.print_exc(file=sys.stdout)
    
#
# callback to handle tag arrive events
#
def tag_event(event):
  global tag_found
  global timer_running
  global timed_mode
  global demo_out 

  try:   
    # process tag event 
    tag_id=parse_event(event,'tag_id=0x',24)
    if debug:
      print "got tag event %s" % tag_id
      print "tag_found %d" % tag_found
    if tag_id != 'NULL':
      if ((timer_running)  or (timed_mode == False)):
        if (tag_found != True):
          tag_found=True
          # turn the hex tag data into ascii  
          demo_out = binascii.unhexlify(tag_id)
  except:   
    traceback.print_exc(file=sys.stdout)

#
# callback for unknown events
#
def unknown_event(event):
    print "unknown event: %s" % event
    send_serial( event)
#
# main callback for all events
#
def event_receiver(event):    
    if debug:
      print "event receiver: %s" % event
      
    # parse the first nine chars of the event.
    # use these chars to index into the event_handlers list.
    # this will invoke the proper event callback routine, passing in
    # the entire event string
    event_handlers.get(event[:9], unknown_event)(event)

#
# list of events and their callbacks
#
event_handlers = {
    'event.dio': dio_event,
    'event.tag': tag_event,
}    

#
# start of main execution
#
try:
        if eot_mode == 0:
          # cr terminator
          STX=''
          EOT=CR
        elif eot_mode == 1:
          # lf terminator
          STX=''
          EOT=LF
        elif eot_mode == 2:
          # crlf terminator
          STX=''
          EOT=CRLF
        elif eot_mode == 3:
          # etx terminator
          STX='\02'
          ETX='\03'
          EOT=ETX
          
        dio_pin="1"
        timer_running = 0

        # open the command channel
        cmd = saturn.Command(HOST,COMMAND_PORT)
        # open the event channel
        evt = saturn.Event(HOST,EVENT_PORT)
        # obtain the event channel id
        evtid = evt.getid()
        
        if debug:
          print "Event channel id = %s" % evtid

        #
        # register for tag arrive events
        #
        cmd.sendcommand('reader.events.register(id='+evtid+',name=event.tag.arrive)')
        #
        # register for dio events
        #
        cmd.sendcommand('reader.events.register(id='+evtid+',name=event.dio.in.'+dio_pin+')')

        if serial_out:
          # open the serial port
          try:
            sport = serial.Serial(SERIAL_PORT, BAUDRATE, rtscts=rtscts, xonxoff=xonxoff)
          except:
            sys.stderr.write("Could not open port\n")
            sys.exit(1) 

        # print out the startup parameters
        print "Program %s - Version: %s" % (sys.argv[0], VERSION)
        print "Startup parameters:"
        print "\tHOST = %s " % HOST
        print "\tdebug =", 
        if debug: print "enabled" 
        else: print "disabled"
        print "\trepr =", 
        if repr_mode: print "enabled" 
        else: print "disabled"
        print "\trtscts =", 
        if rtscts: print "enabled" 
        else: print "disabled"
        print "\txonxoff =", 
        if xonxoff: print "enabled" 
        else: print "disabled"
        print "\tbaud = %d" % BAUDRATE 
        print "\tsport = %d" % SERIAL_PORT 
        print "\tserial output =",
        if serial_out: print "enabled" 
        else: print "disabled"
        print "\tstatic =",
        if timed_mode: print "disabled" 
        else: print "enabled"
        print "\tdin_ll active high = %s" % din_ll 
        print "\tdout_ll active high = %s" % dout_on 
        print "\tfancy =",
        if plain: print "disabled" 
        else: print "enabled"
        print "\teot_mode = %d" % eot_mode 
        print "\touttime = %.1f" % outtime 
        print "\tscantime = %.1f" % scantime 
        print "\tversion = %s" % VERSION
        print "\n"

        # set callback for events.  go into polling loop and wait for events
        evt.receive(event_receiver)

except:
    traceback.print_exc(file=sys.stdout)

cmd.close()
evt.close()
        