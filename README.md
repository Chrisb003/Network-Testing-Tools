This is an app I created for basic network testing. I put it together mainly so I had a tool I could use to do basic network tests for trouble shooting issues at home and down at the church I manage the tech at. My coding skills are not great so I realy on the use of ai for most of my coding and I can not garentee it works perfectly or there isnt bugs.
I built this mainly to run on Windows but also Mac OS. I have tried to include support for linux but it wasnt my priorty just something I thought was useful if I could easily do.

It lists local IP, router IP and wan IP, ISP name. It can also be used to run speed tests with using speedtest cli (this is used to support download/upload speeds higher than 1G), it lists network adaptors and what IP address and dns each have. It can do a network scan and list network devices IP address and mac addresses. Store a history of speed tests completed which can be exported as a csv. Do ping test, DNS lookups.

Not all features will work with all OS's. Below is a list of what works with what.

Fully works with Windows 11. Just need to have python installed and run the setup script it should install the rest of the requirements. In order for the network scanning to work you need to make sure location services are enabled and the script needs to run as admin.

Works with Mac OS. Just install python and run the script it should install the rest. Wifi network scanning does not function due to limitations with Mac OS but all other features work.

Linux, Tested with Linux minit. Basic tests were done. It may require extra work to get functional. Intial tests see to show most functions but still need more testing. May require speed test cli downloading manually and putting into the venv/bin folder within the folder you have run this script from if it doesnt download on its own.

On my list to add/get working. Linux - do full testing.