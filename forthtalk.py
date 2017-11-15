#!/usr/bin/python3
import serial
import sys, os
import threading
from device328p import MCUREGS 
from time import *

portName = "/dev/ttyACM0"
portSpeed = "38400"

try:
   open(portName)
except (FileNotFoundError):
   print("Could not open serial port: Ensure Forth system is connected to serial port and port name is correct")
   exit()

serial_port = serial.Serial(portName, portSpeed, timeout=0.1, writeTimeout=1.0, rtscts=False, xonxoff=False)
 
class ForthTalk():
 
   def __init__(self):
      self.exit = False # Exit the program if True
      self.displayOutput = False # Display received data to terminal if True
      self.command_args = "" # Last '#' command argument(s), if any
      self.pathList = [] # List of paths to be searched for files
      self.lastLines = []   # FIFO buffer of recently received lines from Forth system
      self.maxLastLines = 10 # Maxmimum lines retained in lastLines FIFO buffer
      self.newlineCount = 0 # Used to rate limit data sending
      # Words in the base system that compile new words that don't end with ':'
      self.compileWords = ["constant","variable","value","2constant","2variable"]
      self.definedWords = [] # Loaded on startup. Also command '#words' populates this list
      self.newDefinedWords = [] # Words defined within a file being analysed
      self.unknownWords = [] # Populated with undefined words when analysing files
      self.compileFiles = [] # List of files to be compiled, i.e. sent to the Forth system
      self.wordFiles = {}
      self.configFile = "config.ftk" # Optional file of startup commands (typ. #path commands)

      # Start the keyboard/serial thread and the serial receive thread
      self.serial_receive() # Start the serial receive/terminal output thread
      self.keybd_serial_send() # Start the keyboard/serial send thread
      self.waitNewline(3,0.3) # Wait for Forth system to start up - 3 x NL or 0.3 seconds
      if os.path.isfile(self.configFile): # Upload config file, if there is one
         self.file_upload(self.configFile)
      self.displayOutput = True # Turn on the serial output display
      self.memory_stats() # Print current memory statistics

      

   def _keybd_serial_send(self):
      ''' Thread for receiving input from keyboard and either forward to Forth system serial port
         or, if there is a command preceded by '#', executing the command.
         '##' terminates the program.
      '''
      print("Keyboard thread started")
      try:
         while self.exit == False: # Keep running unless self.exit is True
            keybd_input = input()
            if keybd_input == "##": # Exit program keyboard sequence
               self.exit = True
               break
            else:
               
               current_line = LineProcessor(keybd_input) # Pass the line to LineProcessor object
               if current_line.is_command:
                  self.run_command(current_line.text) # If it's a command, run it
               else:
                  # Move up one line and output spaces so echo overwrites input
                  sys.stdout.write('\r\033\133\101                                               \r') 
                  sys.stdout.flush()
                  current_line.substitute_registers() # Substitute register names with literals
                  current_line.hex_convert() # Convert upper case hex to lower case
                  self.send_data(current_line.text) # Send it to the Forth system

      except (KeyboardInterrupt,EOFError):
         print("InputError")
         self.running = False
      print("Keyboard thread stopped!")

   def keybd_serial_send(self):
      ''' Start keyboard serial thread '''
      threading.Thread(target=self._keybd_serial_send).start()

   def send_data(self,sendBuffer):
      ''' Send data to Forth system followed by NL and wait for NL received or timeout '''
      serial_port.write((sendBuffer + "\n").encode('utf-8'))
      serial_port.flush()
      # Wait for Forth system to process line sent
      self.waitNewline(1,0.3) # 1 x NL or 0.3 seconds

   def _serial_receive(self):
      ''' Thread to receive serial data from Forth system, maintaining a list of up to
         'self.maxLastLines' (Default=10) last lines received. Received lines are
         available in 'self.lastLines'. Useful for debugging, but also used
         by commands such as '#words'. Can be displayed using command '#last'
      '''
      print("Receive thread started")
      recvBuffer = ""
      while self.exit == False:  # Keep looping unless '##'' (exit) received
         serialInput = serial_port.read(serial_port.in_waiting or 1).decode('utf-8')
         serialInput = self.strip_nonprinting(serialInput)

         # Send what ever is received to the terminal unless dislayOutput is False
         if self.displayOutput == True:
            sys.stdout.write(serialInput)
            sys.stdout.flush()

         recvBuffer = recvBuffer + serialInput # Fill the receive buffer with everything received
         # Split the receive buffer into complete lines and store in lastLines list
         while "\n" in recvBuffer:
            self.newlineCount += 1  # Increment newline counter which is cleared by other methods esp. waitNewline()
            self.lastLines.append(recvBuffer.partition('\n')[0]) # Add the last full line to the list
            recvBuffer = recvBuffer.partition('\n')[2] # Save any additional characters to an empty buffer
            # Delete lastLines more than max (default = 10)
            while len(self.lastLines) > self.maxLastLines:
               del self.lastLines[0] # Delete oldest line

      print("Receive thread stopped!")

   def serial_receive(self):
      ''' Start serial receive thread '''
      threading.Thread(target=self._serial_receive).start()

# ============================== Utilities===========================

   def waitNewline(self,nlRecvd,timeout):
      ''' Block thread until required number of NL's received or timeout expires '''
      self.newlineCount = 0  # Incremented by _serial_receive
      nlTimeout = 0.0
      while self.newlineCount < nlRecvd and nlTimeout < timeout:
         sleep(0.05)
         nlTimeout = nlTimeout + 0.05 # 50 mS granularity
      # print("NLs:",self.newlineCount,"nlTimeout:",nlTimeout) # Debug line

   def strip_nonprinting(self,text):
      ''' Strip non-printable characters apart from NL and CR '''
      printable = ""
      for c in text:
         if ord(c)>=ord(' ') or c=='\n' or c=='\r':
            printable = printable + c
      return printable

   def output(self,*args):
      ''' Print messages controlled by the state of 'displayOutput' '''
      if self.displayOutput:
         for arg in args:
            print(arg,end="")

# ========================== Command Handler ========================

   def run_command(self,text):
      ''' Lookup command and run it. Additional commands can easily be added by writing a
         a new method. 'self.command_args' contains the rest of the line after
         the command word. Synonyms or short versions can also be added if required.
      '''

      commandList = {
         "#send":self.send_file, # Uploads a file to the Forth system
         "#include":self.send_file, # Same as #send
         "#require":self.send_file, # Same as #send
         "#comp":self.compile_file, # Compiles a list of required files and sends them to the Forth system
         "#file":self.analyse_file, # Analyses a file for words that need other files to be uploaded
         "#defs":self.find_definitions, # Searches the pathList for files that have definitions
         "#lits":self.add_lits, # Add literal definitions to MCUREGS. Format: litName:litDef e.g. SPI_MOSI:$3
         "#path":self.add_path, # Adds a path to the pathList
         "#warm":self.warm_start, # Initiates a warm start. Same as sending 'warm' directly to the Forth system
         "#empty":self.empty, # Sends 'empty' to the Forth system and removes user defined words from definedWords
         '#list':self.list_words, # Shorthand for '#words list'
         "#words":self.defined_words, # Default is to send 'words' to the Forth system and save these in definedWords
         "#find":self.find_words, # Find a word or words in the definedWords list
         "#hex":self.hex_convert, # Search a file for hex literals prefix by $ and convert to lower case if necessary
         "#last":self.last_lines, # Copies of last lines received from the Forth system
         "#stats":self.memory_stats # Prints out free memory statistics after interrogating the Forth system
         }

      if len(text.split(" ",1)) > 1 :       # Check for argument(s)
         self.command_args = text.split(" ",1)[1] # Command argument(s)
      command = text.split(" ",1)[0] # Command
      try:
         execute_command = commandList[command]
         errorMessage = execute_command()  # Some commands return an error message
         if errorMessage:
            print("Error executing command:",command," - ",errorMessage)
      except KeyError as e:
         print("\nCommand not recognised:",command,"\n")
      self.command_args = "" # Clear the command argument
      self.send_data("") # Get a new prompt - send_data sends "\n"

# =========================== # Command Methods ===========================

   def send_file(self):
      ''' Uploads the given file to the Forth system '''
      pathfile = self.find_file()
      if pathfile:
         self.file_upload(pathfile)
      else:
         return ("File not found: " + self.command_args)

   def compile_file(self):
      '''Analyses a given file and tries to determine any additional files required
         to allow all of the definitions to be compiled. Creates a list 'compileFiles'
         which are then uploaded in reverse order.
      '''
      pathfile = self.find_file()
      if pathfile:
         self.analyse_file(pathfile)
         for file in reversed(self.compileFiles):
            self.run_command("#send " + file)
      else:
         return ("File not found: " + self.command_args)

   def find_file(self,filename=None):
      '''Can take a filename passed as an argument or will try to get a filename
         from command_args. It first checks if it has an extension  i.e a '.'
         in the last four characters.
         If not, add '.frt' as the standard Forth file extension.
         Look to see if the filename is specified with a full pathname or if it's in 
         the current working directory.
         If not, search the pathList and return the fullpath to the first file match.
         If the file is not found, return False.
      '''
      if filename == None: # If no filename provided
         if self.command_args:
            filename = self.command_args # Get the (path+)filename from command_args
         else:
            return

      if filename.find(".",-4) == -1:  # Does the file have an extension of 1-3 letters?
         filename = filename + ".frt"  # If not, search with default extension

      # If there is a full path, or the file is in the current directory, return the filename
      if os.path.dirname(filename) or os.path.isfile(filename): 
         return filename
      else:
         if self.pathList:
            for path in self.pathList: # Work through the pathList
               pathfile = os.path.join(path,filename) # Creating a path+filename
               if os.path.isfile(pathfile): # If it exists in this path, stop searching
                  break
               else:
                  pathfile = False
         else:
            pathfile = False
         return pathfile

   def analyse_file(self,pathfile=None):
      '''Analyse a file looking for words which are not compiled on the Forth system.
         Each word found is added to 'self.unknownWords'. The dictionary self.wordFiles is searched
         to see if there is a file which will allow the undefined word to be compliled. If there is,
         this file is added to the self.compileFiles list. This file in turn is analysed for more
         words. 'self.unknownWords' is therefore used like a FIFO until no more undefined words are found
         without files. Words without files are reported.
      '''
      if pathfile == None: # If no filename provided
         pathfile = self.find_file() # Get the full path+filename from command_args
         if not pathfile:      # Either no filename in command_args or file not found
            return ("File not found: " + self.command_args)

      self.unknownWords = [] # Empty unknown words list
      self.newDefinedWords = []
      self.compileFiles = [pathfile] # Start with the user supplied file in the to-be-compiled list
      
      # Analyse all the files in the compileFiles list adding new files to the list as required
      for file in self.compileFiles:
         pathfile = self.find_file(file)
         if pathfile:
            self._analyse_file(pathfile)
         else:
            return ("File not found: " + file)
      
      if self.unknownWords:
         print("No definitions found for the following words:",self.unknownWords)
         print("Compile files:",self.compileFiles)
      else:
         print("Compile files:",self.compileFiles)



   def _analyse_file(self,filename):
      ''' Processes a file, stripping out comments, registers, literals and quotes and then
         searches for any unknown words.
      '''

      if filename:
         try:
            with open(filename, 'rb') as f:
               sys.stderr.write('--- Analysing file {} ---\n'.format(filename))

               while True:
                  line = f.readline()
                  if not line:
                     break                 
                  current_line = LineProcessor(line.decode('utf-8'))
                  if current_line.is_command: # Command lines don't need analysing
                     continue
                  current_line.strip_comments() # Strip comments
                  current_line.strip_registers() # Strip register names as they end up as literals
                  current_line.strip_literals() # Strip literals as they don't need to be analysed
                  if current_line.strip_quotes(): # Contents of quote strings don't need to be analysed
                     # If there are any words left, analyse these to see if they are known or unknown
                     self.unknownWords.extend(self.known_words(current_line.text)) # Add unknown words to the list

         except IOError as e:
            sys.stderr.write('--- ERROR opening file {}: {} ---\n'.format(filename, e))
   
   def known_words(self,text):
      '''Searches the line for words that are not known either in the definedWords list, the newDefinedWords
         list or the wordFiles list. At the same time, if there are words in the wordFiles list, the
         associated filename is added to the compileList'''
      wordsNotFound = []
      newWord = False
      splitLine = text.split()
      for word in splitLine:
         #print(word,":" ,end="")
         if newWord == True: # Set by the previous word - this is a new word being defined
            #print("New word",end="")
            self.newDefinedWords.append(word)
            newWord = False

         # Check if the word is a known defining word
         elif word in self.compileWords or word[-1:] == ":" :
            #print("Defining word",end="")
            newWord = True # Next word is being defined
         
         elif word in self.definedWords or word in self.newDefinedWords:
            #print("Known word")
            newWord = False

         # Check if the word is already defined or defined in this file
         elif word in self.wordFiles: # Is there a known file which allows the word to be defined?
            #print("in wordFiles",end="")
            if not self.wordFiles[word] in self.compileFiles: # If not already in the list
               self.compileFiles.append(self.wordFiles[word]) # add the file to the compile list
         
         else:
            #print("!!!!Unknown word:",end="")
            wordsNotFound.append(word) # Add the word to the not found list
         #print(self.newDefinedWords)
      return wordsNotFound

   def find_definitions(self):
      ''' Search the directories in self.paths for word definitions storing the words
         and file paths in self.wordFiles
      '''
      for path in self.pathList:
         for name in os.listdir(path):
            pathfile = os.path.join(path,name)
            if os.path.isfile(pathfile) and name[-4:] == ".frt":
               self._find_definitions(pathfile)
      self.output("Words defined in files:")
      if self.displayOutput:
         for word in self.wordFiles:
            print(word,end=" ")

   def _find_definitions(self,filename):
      ''' Processes a file to find any words defined within the file'''

      if filename:
         try:
            with open(filename, 'rb') as f:
               while True:
                  line = f.readline()
                  if not line:
                     break                 
                  current_line = LineProcessor(line.decode('utf-8'))
                  if current_line.is_command: # Command lines don't need analysing
                     continue
                  if current_line.strip_comments(): # Returns False if line is empty
                        splitLine = current_line.text.split()
                        for i in range(len(splitLine)):
                           if splitLine[i] == ':' :  # Line contains a definition
                              if splitLine[i+1]:     # Check there is a word after the colon
                                 # If so, add the word and the filename to the dictionary: wordFiles
                                 self.wordFiles[splitLine[i+1]] = os.path.basename(filename)
                              else:
                                 print("No word after defining word!!!")
         except IOError as e:
            sys.stderr.write('--- ERROR opening file {}: {} ---\n'.format(filename, e))

   def add_lits(self):
      if self.command_args == "":
         print("No literal definitions provided!")
         return
      litDefs = self.command_args.split()
      for lit in litDefs:
         if len(lit.split(":")) != 2:
            print("Incorrectly formed literal definition!",lit)
         else:
            litName = lit.split(":")[0]
            litValue = lit.split(":")[1]
            if litName in MCUREGS and litValue != MCUREGS[litName]:
               print("Literal",litName,"value",MCUREGS[litName],"overwritten with:",litValue)
            MCUREGS[litName] = litValue

   def add_path(self):
      if self.command_args == "" and self.displayOutput :  # No arguments and displayOuput = True
         for path in self.pathList:                        # Print pathList
            print(path)
      else:
         self.pathList.append(self.command_args)

   def warm_start(self):
      print("Warm start...")
      self.send_data('\017')          # flashforth warm start = CTRL-O

   def empty(self):
      print("Defined words back to 'marker' removed")
      self.send_data('empty')
      endUser = self.definedWords.index("marker") # Find index for marker
      self.definedWords = self.definedWords[endUser:]

   def list_words(self):
      self.command_args = "list"
      self.defined_words()

   def defined_words(self):
      ''' Get the current list of words from the Forth System.
         Arguments (Only the first letter is needed):
            No args: Upload the list from the Forth system and print number of user defined words
            get    : Get words from Forth system and print definedWords list
            list   : Print the current definedWords list (latest first)
            user   : Print the current definedWords list before 'marker' i.e. user defined
            alpha  : Print the current definedWords list sorted alphabetically
         '''

      if self.command_args == "" or self.command_args.startswith("g"):
         # Get words from the Forth system. Use 'self.output' rather than 'print'
         displayOutput = self.displayOutput  # Save current state of DisplayOutput
         self.displayOutput = False # Turn off terminal display
         self.send_data("words\n")
         self.waitNewline(3,3.0) # 3 x NL or 3.0 seconds
         self.displayOutput = displayOutput # Restore displayOutput state

         if len(self.lastLines) > 4 and self.lastLines[-4].startswith("words"):   # Check we've received the output in the lastLines buffer
            # 'marker' is the last word in Flashforth user defined word list. Use it to drop the prompt.
            lastLine = self.lastLines[-2].partition("marker") # tuple with 3 elements. Only need [1] & [2]
            self.definedWords = (lastLine[0] + lastLine[1]).split() # User defined words
            self.output("Words received... ",len(self.definedWords)-1, " user defined words")
            self.definedWords.extend(self.lastLines[-3].split()) # All words
         else:
            print("\n**** Words not received!!! ***")
      # Other arguments
      if self.command_args.startswith("l") or self.command_args.startswith("g"):
         print("\nDefined words (latest first):",self.definedWords)
      elif self.command_args.startswith("u"):
         endUser = self.definedWords.index("marker")
         print("\nUser defined words (latest first):",self.definedWords[:endUser])
      elif self.command_args.startswith("a"):
         print("\nDefined words (alphabetical):",sorted(self.definedWords))

   def find_words(self):
      words = self.command_args.split()
      if len(words) == 0:
         print("!!!   No words to find   !!!")
         return
      for word in words:
         if word in self.definedWords:
            print("Found: '",word,"'",sep="")
         else:
            print("Not found:'",word,"'",sep="")

   def hex_convert(self):
      ''' Searches a file for hex literals in upper case. Prefixes literal with
         '$' and converts to lower case if necessary. '''
      pathfile = self.find_file()
      if not pathfile:
         return ("File not found: " + self.command_args)

      try:
         with open(pathfile, 'rb+') as f:
            self.output(' ===> Hex conversion on file: ',pathfile, "\n")
            lines = f.readlines()
            f.seek(0)
            f.truncate()
            converted_file = ""
            for line in lines:
               current_line = LineProcessor(line.decode('utf-8'))
               current_line.hex_convert()
               converted_file = converted_file + current_line.text + "\n"
            f.write(converted_file.encode('utf-8'))

         self.output(' ===> Finished hex conversion on file: ',pathfile,"\n")

      except IOError as e:
         sys.stderr.write('--- ERROR opening file {}: {} ---\n'.format(pathfile, e))


   def clear_last(self):
      ''' Clear the lastLines buffer and reset the newlineCount
          Mainly intended for debug purposes
      '''
      self.newlineCount = 0
      self.lastLines = []
      self.last_lines()

   def last_lines(self):
      ''' Print out the last lines in the serial receive buffer '''
      numLines = len(self.lastLines)
      print(self.lastLines,"NL count:",self.newlineCount)
      print("\nLast lines (last first):",numLines," NL count:",self.newlineCount)
      for i in range(numLines):
         print(numLines-i,": ",self.lastLines[numLines-1-i])

   def memory_stats(self):
      self.displayOutput = False
      self._stats("flash")
      self._stats("eeprom")
      self._stats("ram")
      self.waitNewline(2,0.3)  # Wait 2 x NL or 0.3 seconds
      self.displayOutput = True # Turn on terminal display
      # Pick up data from lastLines buffer. Line format: "flash hi here - u. 1535"
      print("Memory stats:")
      for i in range(-2, -8, -2):
         lineSplit = self.lastLines[i].split()
         print("Free",lineSplit[0],": ",lineSplit[5],"bytes")
      print(self.lastLines[-1]) # Print the last line received (prompt)

   def _stats(self,memory):
      self.send_data(memory + " hi here - u. \n")

   def file_upload(self,filename):
      if filename:
         try:
            with open(filename, 'rb') as f:
               self.output(' ===> Reading file: ',filename, "\n")
               while True:
                  line = f.readline()
                  if not line:
                     break                 
                  current_line = LineProcessor(line.decode('utf-8'))
                  if current_line.is_command:
                     self.output("Command: ",current_line.text)
                     self.run_command(current_line.text)
                  else:
                     if (current_line.strip_comments()): # Returns False if line is empty
                        current_line.substitute_registers() # Substitute registers with literals
                        current_line.hex_convert() # Convert any upper case hex to lower case
                        self.send_data(current_line.text)
            self.waitNewline(1,0.3) # 1 x NL or 0.3 secs - give the system time to respond
            self.output(' ===> Finished reading file: ',filename,"\n")

         except IOError as e:
            sys.stderr.write('--- ERROR opening file {}: {} ---\n'.format(filename, e))

   ''' ======================== End Command Methods ======================= '''


class LineProcessor(ForthTalk):
   ''' Provides methods for processing an input line whether received
      from the keyboard in interactive mode, or from a file being send to
      the Forth system. Initialisation includes identifying command lines with a
      '#' at the beginning of the line or immediately after a comment: '\ #',
      or lines which consist only of white space. Methods include stripping
      comments and substituting registers with literals from the device file.
     '''

   def __init__(self,line):
      self.is_command = False
      if line.strip() == "":  # Empty line - nothing to do
         self.text = ""
         return
         # Commands start at the beginning of the line, but can be in a comment line
         # preceded by '\ '. e.g. |#words  or |\ #words but not |\  #words
         # '#' characters anywhere else in the line are ignored
      if (line[:1] == "#" or line[:3] == "\\ #"):  
         self.is_command = True
         if line[:3] == "\\ #":
            line = line[2:]  # Drop the '\ ' to leave a line starting with '#'
      self.text = line.rstrip("\n\r") # Strip NL and CR from end of line

   def strip_registers(self):
      ''' When analysing files registers can be stripped as they will be end up as literals '''
      self.substitute_registers(True)

   def substitute_registers(self,strip=False):
      ''' Scan a line for references to device registers and substitute literals
         from the device file imported dictionary MCUREGS or strip them if 'strip' is True
      '''
      
      if self.text == "":
         return  # Empty line - nothing to do

      splitLine = self.text.split(" ") # Convert the line into a list of words
      line = ""                   # Clear line ready for rebuild
      for i in range(len(splitLine)):
         if splitLine[i] in MCUREGS:  # Check if word is register for device
            if strip == True:
               continue # No need to add anything to line
            else:
               splitLine[i] = MCUREGS[splitLine[i]] # Substitute register references with literals
         # Rebuild line
         line = line + splitLine[i] + " "
      self.text = line[:-1] # Drop last space

   def strip_comments(self):
      ''' To reduce the amount of text that the Forth system has to handle
         scan a line for comments. Text after "\" or between "(" & ")" is removed before
         sending to the Forth system. Ignores "\" and "(" between quotes.
         Empty lines return False to allow calling methods to skip further processing.
      '''
      if self.text == "":
         return False # Empty line - nothing to do
      
      # Analyse other lines for comments and quotes
      parentheses = False  # At start of line we're not inside parentheses or quotes
      quotes = False

      splitLine = self.text.split() # Convert the line into a list of words
      line = ""                   # Clear line ready for rebuild

      for word in splitLine:
         if word == "\\" and quotes == False and parentheses == False: # No need to process rest of line
            break
         elif word == "(" and quotes == False: # Start of inline comment, strip words & ignore quotes and backslash
            parentheses = True
         elif word[-1:] == ")" and parentheses == True: # End of parentheses
            parentheses = False
         elif word[-1:] == "\"" and quotes == True: # End of quotes must be checked before start of quotes
            quotes = False
            line = line + word + " "  # Add word
         elif word[-1:] == "\"" and parentheses == False: #Start of quotes, so ignore parentheses or backslash
            quotes = True
            line = line + word + " "  # Add word
         elif parentheses == False:
            line = line + word + " "  # Not inside parentheses so add word
   
      # At end of processing we should have parsed matched pairs of quotes or parentheses
      if parentheses == True or quotes == True:
         print("Unmatched pairs of quotes or parentheses!")
         print(self.text) # Prints original text

      if line.strip() == "": # If line is just spaces
         self.text = ""  # Set text to empty and return False
         return False
      else:
          self.text = line[:-1]
      return self.text

   def strip_literals(self):
      ''' Look for literals and remove them. Note: this will not strip hex literals which may be
         used after the 'hex' word unless explicitly prefixed by '$', but will strip upper hex
         literals such as FF00, 1AE
      '''
      splitLine = self.text.split()
      line = ""
      for word in splitLine:
         if not (
               (word[:1] == '%' and word[1:].strip("01.") == "") or # Explicit binary literal
               (word[:1] == '#' and word[1:].strip("0123456789.") == "") or # Explicit decimal literal
               (word[:1] == '$' and word[1:].strip("1234567890abcdef.") == "") or  # Explicit hex literal
               word.strip("0123456789ABCDEF.") == "" # Upper case hex literals, decimal and binary literals
               ):
            line = line + word + " "

      if line.strip() == "": # If line is just spaces
         self.text = ""  # Set text to empty and return False
         return False
      else:
          self.text = line[:-1]
      return self.text

   def strip_quotes(self):
      ''' Strips out text between double quotes. '''
      splitLine = self.text.split()
      line = ""
      quotes = False
      for word in splitLine:
         if word[-1:] == "\"" and quotes == True:
            quotes = False # End of quotes
         elif word[-1:] == "\"" and quotes == False:
            quotes = True # Start of quotes
            line = line + word + " " # Keep start of quotes word
         elif quotes == False:
            line = line + word + " " # Keep words not in quotes

      if line.strip() == "": # If line is just spaces
         self.text = ""  # Set text to empty and return False
         return False
      else:
          self.text = line[:-1]
      return self.text


   def hex_convert(self):
      '''Valid hex literals using upper case (A-F only) are recognised and 
         these letters are converted to lower case. Hex may have a trailing '.' 
         signifying a double number e.g. 1AE -> 1ae , FF1EA. -> ff1ea.
         Warning: D. will convert to 'd.' a valid word, so always use a
         leading '0', e.g. 0D.
      '''
      splitLine = self.text.split()
      newLine = ""
      for word in splitLine:
         newWord = word
         if len(word) > 1 and word.endswith('.') : # Hex may have a trailing '.'
            newWord = newWord[:-1]
         if len(word) > 1 and word.startswith('$') : # Hex literals may start with '$'
            newWord = newWord[1:]
         # If valid hex then convert to lower case
         if newWord.strip('0123456789ABCDEF') == "" :
            newLine = newLine + word.lower() + " "
         else:  # Else keep the original word
            newLine = newLine + word + " "

      self.text = newLine[:-1] # Hex conversion complete
      return self.text

forthtalk = ForthTalk()






