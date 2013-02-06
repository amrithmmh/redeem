'''
Pru.py file for Replicape. 

Author: Elias Bakken
email: elias.bakken@gmail.com
Website: http://www.hipstercircuits.com
License: BSD

You can use and change this, but keep this heading :)
'''
PRU0_PRU1_INTERRUPT    = 17
PRU1_PRU0_INTERRUPT    = 18
PRU0_ARM_INTERRUPT     = 19
PRU1_ARM_INTERRUPT     = 20
ARM_PRU0_INTERRUPT     = 21
ARM_PRU1_INTERRUPT     = 22

PRUSS0_PRU0_DATARAM    = 0
PRUSS0_PRU1_DATARAM    = 1
PRUSS0_PRU0_IRAM       = 2
PRUSS0_PRU1_IRAM       = 3

PRU0                   = 0
PRU1                   = 1
PRU_EVTOUT0            = 2
PRU_EVTOUT1            = 3
PRU_EVTOUT2            = 4
PRU_EVTOUT3            = 5
PRU_EVTOUT4            = 6
PRU_EVTOUT5            = 7
PRU_EVTOUT6            = 8
PRU_EVTOUT7            = 9

PRU_EVTOUT_0           = 0
PRU_EVTOUT_1           = 1
PRU_EVTOUT_2           = 2
PRU_EVTOUT_3           = 3
PRU_EVTOUT_4           = 4
PRU_EVTOUT_5           = 5
PRU_EVTOUT_6           = 6
PRU_EVTOUT_7           = 7


import os
os.system("export LD_LIBRARY_PATH=/usr/local/lib")
import pypruss      					                            # The Programmable Realtime Unit Library
import numpy as np						                            # Needed for braiding the pins with the delays
from threading import Thread
import time 
import mmap
import struct 

DDR_MAGIC			= 0xbabe7175

class Pru:
    def __init__(self):
        pru_hz 			    = 200*1000*1000		            # The PRU has a speed of 200 MHz
        self.s_pr_inst 		= 1.0/pru_hz		            # I take it every instruction is a single cycle instruction
        self.inst_pr_loop 	= 16				            # This is the minimum number of instructions needed to step. 
        self.inst_pr_delay 	= 2					            # Every loop adds two instructions: i-- and i != 0            
        self.pru_data       = [[], []]                      
        with open("/sys/class/uio/uio0/maps/map2/addr", "r") as f:
            ddr_addr = int(f.readline(), 16)

        with open("/sys/class/uio/uio0/maps/map2/size", "r") as f:
            ddr_size = int(f.readline(), 16)

        ddr_offset     = ddr_addr-0x10000000
        ddr_filelen    = ddr_size+0x10000000
        self.DDR_START      = 0x10000000
        self.DDR_END        = 0x10000000+ddr_size
        self.ddr_start      = self.DDR_START

        with open("/dev/mem", "r+b") as f:	                # Open the memory device
            self.ddr_mem = mmap.mmap(f.fileno(), ddr_filelen, offset=ddr_offset) # mmap the right area            
            self.ddr_mem[self.ddr_start:self.ddr_start+4] = struct.pack('L', 0)  # Add a zero to the first reg to make it wait

        pypruss.modprobe()							       	# This only has to be called once pr boot
        pypruss.init()										# Init the PRU
        pypruss.open(0)										# Open PRU event 0 which is PRU0_ARM_INTERRUPT
        pypruss.pruintc_init()								# Init the interrupt controller
        pypruss.pru_write_memory(0, 0, [ddr_addr])			# Put the ddr address in the first region 
        pypruss.exec_program(0, "../firmware/firmware_pru_0.bin")	# Load firmware "ddr_write.bin" on PRU 0
        print "PRU initialized"

    ''' Add some data to one of the PRUs '''
    def add_data(self, data, pru_num):
        (pins, delays) = data                       	    # Get the data
        if len(pins) == 0:
            return 
        print "Adding data len for "+str(pru_num)+": "+str(len(pins))
        delays = map(self._sec_to_inst, delays)     	    # Convert the delays in secs to delays in instructions
        data = np.array([pins, delays])		        	    # Make a 2D matrix combining the ticks and delays
        data = list(data.transpose().flatten())     	    # Braid the data so every other item is a pin and delay
        
        if len(self.pru_data[pru_num]) > 0:
            self.pru_data[pru_num] = self._braid_data(data, self.pru_data[pru_num])
        else:
            self.pru_data[pru_num] = data

    ''' Commit the data to the DDR memory '''
    def commit_data(self):
        data = struct.pack('L', len(self.pru_data[0])/2)	    # Data in string form
        for reg in self.pru_data[0]:									
            data += struct.pack('L', reg) 				        # Make the data, it needs to be a string
        data += struct.pack('L', 0)                             # Add a terminating 0, this keeps it looping.

        self.ddr_end = self.ddr_start+len(data)       
        if self.ddr_end > self.DDR_END:                         # If the data is too long, wrap it around to the start
            multiple = (self.DDR_END-self.ddr_start)%8          # Find a multiple of 8
            cut = self.DDR_END-self.ddr_start-multiple-4        # The cut must be done after a delay, so a multiple of 8 bytes +/-4
            first = struct.pack('L', cut/8)+data[4:cut]         # Update the loop count
            first += struct.pack('L', DDR_MAGIC)                # Add the magic number to force a reset of DDR memory counter
            self.ddr_mem[self.ddr_start:self.ddr_start+len(first)] = first  # Write the first part of the data to the DDR memory.

            second = struct.pack('L', (len(data[cut:-4])/8))+data[cut:]     # Add the number of steps in this iteration
            self.ddr_end = self.DDR_START+len(second)           # Update the end counter
            self.ddr_mem[self.DDR_START:self.ddr_end] = second  # Write the second half of data to the DDR memory.

            self.wait_for_event()                               # Must wait for event here  
            print "Wrapped"
        else:
            self.ddr_mem[self.ddr_start:self.ddr_end] = data    # Write the data to the DDR memory.
            if self.DDR_END-self.ddr_end < 16:                  # There is no room for a complete  step, add the magic number to wrap
                self.ddr_mem[self.ddr_end-4:self.ddr_end] = struct.pack('L', DDR_MAGIC)
                self.ddr_mem[self.DDR_START:self.DDR_START+4] = struct.pack('L', 0) # Terminate the next instruction
                self.ddr_end = self.DDR_START+4                
                print "wrapped due to insufficient DDR"

        self.ddr_start = self.ddr_end-4                         # Update the start of ddr for next time 
        self.pru_data = [[],[]]                                 # Reset the pru_data list since it has been commited         


    ''' Wait for the PRU to finish '''                
    def wait_for_event(self):
        pypruss.wait_for_event(PRU_EVTOUT_0)				    # Wait a while for it to finish.
        pypruss.clear_event(PRU0_ARM_INTERRUPT)				    # Clear the event 
        pypruss.wait_for_event(PRU_EVTOUT_0)				    # Wait a while for it to finish.
        pypruss.clear_event(PRU0_ARM_INTERRUPT)				    # Clear the event 

    ''' Close shit up '''
    def close(self):
        ddr_mem.close()                                         # Close the memory 
        f.close()                                               # Close the file
        pypruss.pru_disable(0)                                  # Disable PRU 0, this is already done by the firmware
        #pypruss.pru_disable(1)                                 # Disable PRU 0, this is already done by the firmware
        pypruss.exit()                                          # Exit, don't know what this does. 
        

    ''' Convert delay in seconds to number of instructions for the PRU '''
    def _sec_to_inst(self, s):					    # Shit, I'm missing MGP for this??
        inst_pr_step  = s/self.s_pr_inst  		    # Calculate the number of instructions of delay pr step. 
        inst_pr_step /= 2.0					        # To get a full period, we must divide by two. 
        inst_pr_step -= self.inst_pr_loop		    # Remove the "must include" number of steps
        inst_pr_step /= self.inst_pr_delay		    # Yes, this must be right..
        if inst_pr_step < 1:
            inst_pr_step = 1
        return int(inst_pr_step)			        # Make it an int


    ''' Braid together the data from the two data sets'''
    def _braid_data(self, data1, data2):
        braids = [data1[0] | data2[0]]
        del data1[0]
        del data2[0]

        while len(data1) > 1 and len(data2) > 1:                        
            if data1[0] > data2[0]:                         # If data 1 is bigger, 
                data1[0]-= data2[0]-self.inst_pr_loop       # remove the delay from data1..
                if data1[0] < 1:
                    data1[0] = 1
                braids += data2[0:2]                        # And insert data2 with 
                del data2[0:2]                              # Delete the pushed data
            elif data1[0] < data2[0]:                       # If data 2 is bigger, 
                data2[0]-= data1[0]-self.inst_pr_loop       # remove the delay from data2..
                if data2[0] < 1:
                    data2[0] = 1
                braids += data1[0:2]                        # And insert data2 with 
                del data1[0:2]
            else:
                braids += [data1[0]]
                braids += [data1[1] | data2[1]]             # Merge the pins
                del data1[0:2]
                del data2[0:2]

        braids += [max(data1[0], data2[0])]
        del data1[0]
        del data2[0]        
        braids += data2
        braids += data1

        return braids


