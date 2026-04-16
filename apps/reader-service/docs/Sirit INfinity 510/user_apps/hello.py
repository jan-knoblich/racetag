#!/usr/bin/python -u
#
# sample reader python script.
#
# purpose: produce output to stdout
#
# inputs: optional, anything 
#
#

import sys
from sys import argv

# setup the command session
#

try:
    args=argv[1]
except:
    args="none"

print "\nHello! args passed: %s" % args 