# Sirit INfinity 510 RFID Reader


## Initial tests

The device seems to be initially configured to use the static IP address `169.254.1.2`. Since my local network is in a different range, I am not able to connect my PC to it.

I need to change my PC's IP address under the Ethernet adapter configurations in the Control Panel. I set my PC's IPv4 properties to use an address like `169.254.1.100` and a subnet mask of `255.255.255.0`. After that, I am able to establish an SSH connection using _PuTTY_, providing the username `cliuser` and no password.

Subsequently, using the SSH command `com.network.1.set(dhcp)`, I can change the network connection to DHCP instead of static and then connect to the device using the IP address assigned by my router, via SSH. It is also possible to access it using a web browser and the hostname as well, which in this case is the serial number for example: http://00179e0037d2.local.

## Web portal:

The device provides a UI that it is accessible using its IP address, host name or serial number:

![INfinity 510 webportal](<INfinity 510 webportal.png>)

To perform certain configurations, the user need to be logged as administrator. The default user/pass for the web interface for admin is:
* user: `admin`
* pass: `readeradmin`

