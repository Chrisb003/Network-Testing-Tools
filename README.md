This is an app I created for basic network testing. I put it together mainly so I had a tool I could use to do basic network tests for trouble shooting issues at home, at church and various people I support with tech bits. My coding skills are not great, I can understand it but not write much of it so I really on the use of AI for most of my coding and I can’t guarantee it works perfectly or there isn’t bugs.
I built this mainly to run on Windows but also Mac OS. Linux support is there and it seems to function but not a priority.
Internet is required the first time it runs so it can install all the requirements but after that it can function without internet.

It lists local IP, router IP and wan IP, ISP name. It can also be used to run speed tests with using speedtest cli (this is used to support download/upload speeds higher than 1G), it lists network adaptors and what IP address and dns each have. It can do a network scan and list network devices IP address and mac addresses. Store a history of speed tests completed which can be exported as a csv. Do ping test, DNS lookups.
All data is stored in a .db file using sqlite and you can export more bits as csv files should you need to.

Not all features will work with all OS's. Below is a list of what works with what so far. It will need to run directly on a host OS. I don’t think it will function if made into a docker app.

This app runss on port 81. To access it once started open a web broswer and go to 127.0.0.1:81

I have no plans to add https. It is meant to be run and accessed from the same device so no need for the secure web page.

I have included a .sh script for mac/linux to make sure python is installed and then run the setup script which should install the rest. You need to make sure you use "chmod ugo+x" on the .sh file to make it executable then run it from the terminal.
There is also a bat file for windows to install python and then start the setup script.

Fully seems to works with Windows 11 haven’t verified with previous versions. Just need to have python installed and run the setup script it should install the rest of the requirements. Or run the bat file and it will install python for you and run the app. In order for the network scanning to work you need to make sure location services are enabled and the script needs to run as admin.

Works with Mac OS. Just install python and run the script it should install the rest. WIFI network scanning will require you allowing location permissions. Network scanning requires admin permissions. Due to a limitation within Mac OS although the wifi scan works it cant list the Mac addresses (BSSID) of the networks.

Linux, Tested with Linux Mint. Everything functions as far as I can tell. Speed test should download on its own but if it fails you may need to download it separately. Ran as sudo due to need to setup packages but unsure if can run without. Can’t be sure it will run on every distro but I have tested with Linux mint and setup script uses apt-get to install requirements so using a distro that doesn’t have that will mean you will need to install the required packages manually.
It may work on a raspberry pi to but this has not been tested.

On my list to add/get working. 
Currently the app functions as I want it. Until I am fully happy with it then it will remain a beta. The only changes that may happen now are general bug fixes. If I choose to release this publicly I will switch to the github being public so I will update the updater script to update from it.


There are a few useful bits I have added where you can add files with specific names into the main folder and it will have various actions on startup.
autostart - This file will be created automatically anyway but if you create it before you first run the application. By have a 0 in the file it will disable the automatic opening to the webpage for the dashboard when the app starts. It can also be changed in the settings but a useful thing if you want to run it on a seperate machine and run it headless.

dev - this will force the setup script to download from the dev branch rather than release. This only does something if you do not have the rest of the files downloaded.

resetpassword - This will disable and reset the user account

cleardatabase - This deletes your entire database

webport - The application defaults to 81 but should you need to change this before opening the app create a file called webport and in the file enter the port number you want it to use.