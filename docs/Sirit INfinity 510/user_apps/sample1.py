#!/usr/bin/python -u
#
# sample reader python script.
#
# purpose: produce output in the shell window of an event channel 
#
# inputs: id of an externally started event channel
#
# output: the text user_event01 will be displayed on the externally started
# event channel
#
# setup: from an external system with IP access to the reader start an event
# channel.  For example, to start an event channel on a system with IP address
# 10.2.0.3, issue the command "telnet 10.2.0.3 50008".  the telnet session
# should connect to the reader and return a connection id:
#   > telnet 10.2.0.3 50008
#     Trying 10.2.0.3...
#     Connected to 10.2.0.3.
#     Escape character is '^]'.
#     event.connection id = 18
# 
# pass the connection id (18 in this example) as an input parameter to this
# routine.
# To invoke the routine, login as admin and then issue the reader.exec_app
#   >>> reader.login(admin,????)
#   >>> reader.exec_app(sample1.py,18)
#
# The text "user_event01" should appear in the shell of the event channel.


import saturn
import re
import commands
import sys
from sys import argv

# setup the command session
#
cmd = saturn.Command('localhost',50007)

try:
    evtid = argv[1]
except:
    print "Error: the id of an externally started event session must be passed as a parm to this routine"    

#
# register for a user defined event event
#
cmd.sendcommand('reader.events.register(id='+evtid+',name=user_event01)')

#
# trigger an event 
#
cmd.sendcommand('reader.events.trigger(name=user_event01)')

cmd.close()