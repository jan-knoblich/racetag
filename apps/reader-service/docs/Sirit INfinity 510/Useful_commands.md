
# Useful Commands for Sirit INfinity 510 RFID Reader

To set the timezone
```
info.time_zone=Europe/Berlin
```

The device needs to be firsta activated:
```
setup.operating_mode=active
```

To print the current tag database:
```
tag.db.get() 
```

If you want to receive events instead of polling the tag database, you can register for events.

Types of events: 
* report (all events, while tag is present),
* arrive, depart, 
* portal_cross
* antenna_cross

Tag report event needs to be bound to the channel ID received when starting and stablishing the connection with the reader. For example, if the channel ID is 21:
```serial
reader.events.bind(id = 21) 
```

To get the event notifications for tag reports, the tag report event needs to be registered:
```
reader.events.register(name = event.tag.report)
reader.events.register(21, event.tag.report)
```

Also to confirgure the fields to be included in the tag report:
```
tag.reporting.report_fields=tag_id time type antenna
tag.reporting.antenna_cross_fields = tag_id time antenna  
tag.reporting.arrive_fields = tag_id time antenna
tag.reporting.depart_fields = tag_id time repeat antenna
tag.reporting.depart_time = 300
tag.reporting.portal_cross_fields = tag_id time antenna
```

To be reported in all event types:
```
tag.reporting.taglist_fields=tag_id time type antenna
```

```
tag.reporting.raw_tag_data = true  
```

To reboot the reader:
```
reader.reboot() 
```

reader.license.status()
reader.profile.show_running_config()

## SSH Access

The device has an SSH server running on port 22. The default user is _cliuser_ without password