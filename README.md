# forthtalk
Python shell for communicating with Forth based systems via serial communications
Inspired by amforth-shell.py (Keith Amidon) and ff-shell.py (Mikael Nordman) but pretty much written from the ground up.
Primarily developed to work with flashforth (Mikael Nordman) but should work with other Forth's over serial communications with a few tweaks. Developed on Linux, so may need modifying for other OS's.

Configuration of the serial port and speed is currently in the forthtalk.py file.
The program looks for a file called config.ftk in the current working directory and can load any intial forthtalk commands from there as it starts up and/or send Forth code to the Forth system. A sample config.ftk file is provided.
The preferred extension for Forth files is '.frt' which is appended to file requests if no extension is specified.

forthtalk commands start with an '&'. Commands can be entered directly from the keyboard or can be embedded in files. In files the '&' must be the first character on the line, or the thrid character following a backslash (i.e. in a comment line). E.g.
&send filename
\ &send filename
would result in the &send command being executed.

Commands:
&send filename (synonyms: &include, &require) Sends the specified file to the Forth system. If an extension is specified it will be used, but if no extension is spcified '.frt' will be appended to the filename. If a path is specified then the path is used. If no path is specified then the file will be searched for in the current working directory and then in the list of paths (see &path). The first matching file found is sent. Paths are searched in the order they are added to the path list.

&path pathname  Adds a path to the path list.
