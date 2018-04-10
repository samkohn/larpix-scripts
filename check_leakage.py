'''
This script runs noise_tests.test_leakage_current on a specified chip set
Requires noise_tests library
Requires a .json file containing chip-ids and daisy chain data formatted like
{
    'board': <board-name>,
    'chip_set': [
        [<chip-id>, <io-chain>],
        ...
        ]
}
'''

from __future__ import print_function
import argparse
import logging
import time
import larpix.larpix as larpix
import helpers.noise_tests as noise_tests
from sys import (exit, stdout)
import json
import os

def clear_buffer_quick(controller):
    controller.run(0.05,'clear buffer (quick)')

def clear_buffer(controller):
    buffer_clear_attempts = 5
    clear_buffer_quick(controller)
    while len(controller.reads[-1]) > 0 and buffer_clear_attempts > 0:
        clear_buffer_quick(controller)
        buffer_clear_attempts -= 1

def verify_chip_configuration(controller):
    clear_buffer(controller)
    config_ok, different_registers = controller.verify_configuration()
    if not config_ok:
        log.warn('chip configurations were not verified - retrying')
        clear_buffer(controller)
        config_ok, different_registers = controller.verify_configuration()
        if not config_ok:
            log.warn('chip configurations could not be verified')
            log.warn('different registers: %s' % str(different_registers))

parser = argparse.ArgumentParser()
parser.add_argument('infile',
                    help='input file containing chipset info (required)')
parser.add_argument('outdir', nargs='?', default='.',
                    help='output directory for log file'
                    '(optional, default: %(default)s)')
parser.add_argument('-v', '--verbose', action='store_true')
parser.add_argument('--reset_cycles', default=None, type=int,
                    help='(optional, default: %(default)s)')
parser.add_argument('--global_threshold', default=125, type=int,
                    help='(optional, default: %(default)s)')
parser.add_argument('--pixel_trim', default=16, type=int,
                    help='(optional, default: %(default)s)')
parser.add_argument('--run_time', default=1, type=int,
                    help='(optional, units: sec,  default: %(default)s)')
parser.add_argument('--configuration_file', default='physics.json',
                    help='initial chip configuration file to load '
                    '(optional, default: %(default)s)')
parser.add_argument('--chips', default=None, type=str,
                    help='chips to include in scan, string of chip_ids separated by commas'
                    '(optional, default: None=all chips in chipset file)')
args = parser.parse_args()

infile = args.infile
outdir = args.outdir
verbose = args.verbose
reset_cycles = args.reset_cycles
global_threshold = args.global_threshold
pixel_trim = args.pixel_trim
run_time = args.run_time
config_file = args.configuration_file
if not args.chips is None:
    chips_to_scan = [int(chip_id) for chip_id in args.chips.split(',')]
else:
    chips_to_scan = None

return_code = 0

if not os.path.exists(outdir):
    os.makedirs(outdir)
logfile = outdir + '/.check_leakage_%s.log' % \
    str(time.strftime('%Y_%m_%d_%H_%M_%S',time.localtime()))
log = logging.getLogger(__name__)
fhandler = logging.FileHandler(logfile)
shandler = logging.StreamHandler(stdout)
formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
fhandler.setFormatter(formatter)
shandler.setFormatter(formatter)
log.addHandler(fhandler)
log.addHandler(shandler)
log.setLevel(logging.DEBUG)
log.info('start of new run')
log.info('logging to %s' % logfile)

try:
    larpix.enable_logger()
    controller = larpix.Controller(timeout=0.01)
    # Initial configuration of chips
    chip_set = json.load(open(infile,'r'))
    board_info = chip_set['board']
    log.info('begin initial configuration of chips for board %s' % board_info)
    for chip_tuple in chip_set['chip_set']:
        chip_id = chip_tuple[0]
        io_chain = chip_tuple[1]
        controller.chips.append(larpix.Chip(chip_id, io_chain))
        chip = controller.chips[-1]
        chip.config.load(config_file)
        controller.write_configuration(chip)
        controller.disable(chip_id=chip_id, io_chain=io_chain)
    log.info('initial configuration of chips complete')

    verify_chip_configuration(controller)

    # Run leakage current test on each chip
    board_results = []
    for chip_idx,chip in enumerate(controller.chips):
        try:
            start_time = time.time()
            chip_id = chip.chip_id
            io_chain = chip.io_chain
            chip_info = (chip_id, io_chain)
            if chips_to_scan is None:
                pass
            else:
                if not chip_id in chips_to_scan:
                    log.info('skipping c%d-%d' % chip_info)
                    board_results += [None]
                    continue

            clear_buffer(controller)
            chip_results = noise_tests.test_leakage_current(controller=controller,
                                                            chip_idx=chip_idx,
                                                            reset_cycles=reset_cycles,
                                                            global_threshold=global_threshold,
                                                            trim=pixel_trim,
                                                            run_time=run_time)
            board_results += [chip_results]
            finish_time = time.time()
            if verbose:
                log.debug('c%d-%d leakage test took %.2f s' % \
                              (chip_id, io_chain, finish_time - start_time))
        except Exception as error:
            log.exception(error)
            log.error('c%d-%d leakage test failed!' % chip_info)
            controller.disable(chip_id=chip_id, io_chain=io_chain)
            return_code = 2
            continue

    log.info('all chips leakage check complete')

    # Print leakage test results
    log.info('leakage rate threshold (global, trim): %d - %d' % 
             (global_threshold, pixel_trim))
    for chip_idx,chip in enumerate(controller.chips):
        chip_id = chip.chip_id
        io_chain = chip.io_chain
        if board_results[chip_idx] is None:
            log.('%s-c%d-%d skipped' % (board_info, chip_id, io_chain))
            continue
        chip_mean = sum(board_results[chip_idx]['rate']) /\
            len(board_results[chip_idx]['rate'])
        chip_rms = sum(abs(rate - chip_mean) for rate in board_results[chip_idx]['rate'])\
            /len(board_results[chip_idx]['rate'])
        log.info('%s-c%d-%d mean leakage rate: %.2f Hz, rms: %.2f Hz' % \
                     (board_info, chip_id, io_chain, chip_mean, chip_rms))
        for channel_idx,channel in enumerate(board_results[chip_idx]['channel']):
            log.info('%s-c%d-%d-ch%d rate: %.2f Hz' % \
                         (board_info, chip_id, io_chain, channel,
                          board_results[chip_idx]['rate'][channel_idx]))
except Exception as error:
    log.exception(error)
    return_code = 1

exit(return_code)
