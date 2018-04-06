from os import system
import sys
from larpix.quickstart import board_info_map
import time

specifier = time.strftime('%Y_%m_%d_%H_%M')

global_threshold_correction = 1
if not sys.argv[2] is None:
    global_threshold_correction = sys.argv[2]

for chip in board_info_map['pcb-10']['chip_list']:
    command = ('python check_channel_sensitivity.py pcb-10_chip_info.json '
               'datalog/channel_sensitivty_%s '
               '--global_threshold_correction %d '
               '--configuration_file %s '
               '-v --chips "%d"' % (specifier, global_threshold_correction, sys.argv[1],
                                    chip[0]))
    print command
    system(command)
               