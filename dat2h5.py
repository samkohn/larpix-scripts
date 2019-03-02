''''
A script to convert a .dat data file into the specified format with the
following data:

    channel id | chip id | pixel id | pixel x | pixel y | raw ADC | raw
    timestamp | 6-bit ADC | full timestamp | serial index | converted voltage (mV) | calib
    pedestal voltage (mV) | chip global threshold | channel trim threshold | raw cpu timestamp

Note: for the h5 output, the array type is 64-bit signed integer. This
works to hold all of the data with no problem except for the pixel x and
y. These are stored as int(10*value) as a way to save some precision.
(For ROOT output, those fields are saved as floats so no problem.)

'''

from __future__ import print_function
import argparse
import numpy as np
from os.path import splitext
import json
from larpix.serial_helpers.dataloader import DataLoader
from larpix.serialport import SerialPort
from larpix.larpix import (Controller, Configuration)
from larpix.timestamp import Timestamp
from larpixgeometry.pixelplane import PixelPlane
import larpixgeometry.layouts as layouts
from bitarray import bitarray
parse = SerialPort._parse_input

def fix_ADC(raw_adc):
    '''
    Converts the 8-bit value to the appropriate 6-bit value, formed by
    dropping the LSB (//2) and MSB (- 128).

    '''
    return (raw_adc - 128)//2

def extract_lpx_data(word):
    '''
    Parse the raw bytes from Igor's file .lpx file
    returns timestamp, packet_bytes
    '''
    bits = bitarray(endian='little')
    bits.frombytes(word)
    timestamp_bits = bits[54:64]
    packet_bits = bits[0:54]
    timestamp_bits.reverse()
    timestamp = int(timestamp_bits.to01(),2)
    packet_bytes = bits[0:54].tobytes()
    return timestamp, packet_bytes

def fix_lpx_timestamp_rollover(time, ref, time_nbit=10, late_packet_window=10):
    '''
    Corrects for rollovers in the `time_nbit`-bit timestamp `time`
    resulting in an relative timestamp within run
    Works as long as `ref` is known to be <2**`time_nbit`-`late_packet_window` seconds before time
    If `ref` - `time` is < `late_packet_window` it is assumed that this packet is out of order and
    returns -1
    '''
    rollover_dt = 2**time_nbit
    n_rollovers = 0
    if ref-time > 0:
        # skip non-sequential packets
        if ref-time < 10:
            return -1
        n_rollovers = np.ceil(float(ref - time) / rollover_dt)
    fixed_time = n_rollovers * rollover_dt + time
    return fixed_time

class lpx_loader:
    '''
    A dummy class to make DataLoader-like read blocks from .lpx data
    '''
    nbytes_word = 8

    def __init__(self, filename, t0=0):
        '''
        Reads from `filename`
        `t0` sets the t0 for the run (since .lpx data only has a 10b timestamp)
        '''
        self.file = open(filename,'rb')
        self.prev_timestamp = t0

    def close(self):
        '''
        Closes file nicely
        '''
        self.file.close()
        self.file = None

    def next_block(self):
        '''
        Reads next word from data file and formats as though it was from Dan's .dat file format
        '''
        bytes = self.file.read(self.nbytes_word)
        if bytes:
            timestamp, packet_bytes = extract_lpx_data(bytes)
            fixed_timestamp = fix_lpx_timestamp_rollover(timestamp, self.prev_timestamp)
            if fixed_timestamp >= 0:
                self.prev_timestamp = fixed_timestamp
            faux_block = {'block_type':'data',
                          'data_type':'read',
                          'data':(SerialPort.start_byte + packet_bytes + b'\x00' +
                                  SerialPort.stop_byte),
                          'time':timestamp
                          }
            return faux_block
        return None


parser = argparse.ArgumentParser()
parser.add_argument('infile', help='Input file of either Dan\'s .dat format or Igor\'s .lpx '
                    'format')
parser.add_argument('outfile', nargs='?', default=None, help='Output file format, default is '
                    'input filename with altered file extension')
parser.add_argument('-c', '--calibration', default=None, help='Calibration .json file to use '
                    '(optional)')
parser.add_argument('-v', '--verbose', action='store_true', help='Verbose mode (optional)')
parser.add_argument('-n', '--n_trans', default=None, help='Number of transmissions to process, '
                    'default is all',type=int)
parser.add_argument('-s', '--n_skip', default=0, help='Number of transmissions to skip', type=int)
parser.add_argument('--format', choices=['h5', 'root', 'ROOT'],
        required=True, help='Output file format, choose from %(choices)s')
geom_choices = {'4chip': 'sensor_plane_28_simple.yaml',
        '8chip': 'sensor_plane_28_8chip.yaml',
        '28chip': 'sensor_plane_28_full.yaml'}
parser.add_argument('-g', '--geometry', choices=geom_choices.keys(),
        required=True, help='The sensor & chip geometry layout, choose from %(choices)s')
args = parser.parse_args()

infile = args.infile
infile_fmt = splitext(infile)[1]
outfile = args.outfile
verbose = args.verbose
n_trans = args.n_trans
n_skip = args.n_skip
loader = None
if infile_fmt == '.dat':
    loader = DataLoader(infile)
elif infile_fmt == '.lpx':
    loader = lpx_loader(infile)
else:
    raise RuntimeError('Unrecognizable file format {}'.format(infile_fmt))
calib_data = {}

if outfile is None:
    outfile = splitext(infile)[0] + '.' + args.format.lower()
if args.verbose:
    print(infile + ' -> ' + outfile)
if not args.calibration is None:
    calib_data = json.load(open(args.calibration,'r'))
if args.format == 'h5':
    import h5py
    use_root = False
elif args.format.lower() == 'root':
    use_root = True
    import ROOT
    root_channelid = np.array([-1], dtype=int)
    root_chipid = np.array([-1], dtype=int)
    root_pixelid = np.array([-1], dtype=int)
    root_pixelx = np.array([0], dtype=float)
    root_pixely = np.array([0], dtype=float)
    root_rawADC = np.array([-1], dtype=int)
    root_rawTimestamp = np.array([0], dtype=np.uint64)
    root_ADC = np.array([-1], dtype=int)
    root_timestamp = np.array([0], dtype=np.uint64)
    root_serialblock = np.array([-1], dtype=int)
    root_v = np.array([-1], dtype=float)
    root_pdst_v = np.array([-1], dtype=float)
    root_pixel_trim = np.array([-1], dtype=int)
    root_global_threshold = np.array([-1], dtype=int)
    root_cpu_timestamp = np.array([0], dtype=np.uint64)
    fout = ROOT.TFile(outfile, 'recreate')
    ttree = ROOT.TTree('larpixdata', 'LArPixData')
    ttree.Branch('channelid', root_channelid, 'channelid/I')
    ttree.Branch('chipid', root_chipid, 'chipid/I')
    ttree.Branch('pixelid', root_pixelid, 'pixelid/I')
    ttree.Branch('pixelx', root_pixelx, 'pixelx/D')
    ttree.Branch('pixely', root_pixely, 'pixely/D')
    ttree.Branch('raw_adc', root_rawADC, 'raw_adc/I')
    ttree.Branch('raw_timestamp', root_rawTimestamp, 'raw_timestamp/l')
    ttree.Branch('adc', root_ADC, 'adc/I')
    ttree.Branch('timestamp', root_timestamp, 'timestamp/l')
    ttree.Branch('serialblock', root_serialblock, 'serialblock/I')
    ttree.Branch('v', root_v, 'v/D')
    ttree.Branch('pdst_v', root_pdst_v, 'pdst_v/D')
    ttree.Branch('pixel_trim', root_pixel_trim, 'pixel_trim/I')
    ttree.Branch('global_threshold', root_global_threshold, 'global_threshold/I')
    ttree.Branch('cpu_timestamp', root_cpu_timestamp, 'cpu_timestamp/l')

#geometry = PixelPlane.fromDict(layouts.load('sensor_plane_28_simple.yaml'))
geometry = PixelPlane.fromDict(layouts.load(geom_choices[args.geometry]))

numpy_arrays = []
index_limit = 10000
serialblock = -1 # serial read index
numpy_arrays.append(np.empty((index_limit, 15), dtype=np.int64))
current_array = numpy_arrays[-1]
current_index = 0
last_timestamp = {}
chip_threshold = {}
while True:
    block = loader.next_block()
    serialblock += 1
    if block is None:
        if verbose:
            print('Packets processed: {}'.format(serialblock-1))
        break
    elif not n_trans is None and serialblock >= n_trans:
        if verbose:
            print('Packets processed: {}'.format(serialblock-1))
        break
    elif verbose and serialblock % 1000 == 0:
        print('Packets processed: {}'.format(serialblock),end='\r')
    if serialblock < n_skip:
        continue
    if block['block_type'] == 'data' and block['data_type'] == 'write':
        # if write to pixel threshold -> store configuration value
        packets = parse(bytes(block['data']))
        for packet in packets:
            if packet.packet_type == packet.CONFIG_WRITE_PACKET:
                chipid = packet.chipid
                if packet.register_address in Configuration.pixel_trim_threshold_addresses:
                    channel = packet.register_address # channel id is equivalent to register address
                    try:
                        chip_threshold[chipid][channel] = packet.register_data
                    except KeyError:
                        chip_threshold[chipid] = { channel : packet.register_data }
                elif packet.register_address == Configuration.global_threshold_address:
                    try:
                        chip_threshold[chipid]['global_threshold'] = packet.register_data
                    except KeyError:
                        chip_threshold[chipid] = { 'global_threshold' : packet.register_data }
    elif block['block_type'] == 'data' and block['data_type'] == 'read':
        packets = parse(bytes(block['data']))
        for packet in packets:
            if packet.packet_type == packet.DATA_PACKET:
                current_array[current_index][0] = packet.channel_id
                current_array[current_index][1] = packet.chipid
                current_array[current_index][5] = packet.dataword
                current_array[current_index][6] = packet.timestamp
                current_array[current_index][7] = fix_ADC(packet.dataword)
                current_array[current_index][9] = serialblock

                chipid = packet.chipid
                channel = packet.channel_id
                try:
                    current_array[current_index][12] = chip_threshold[chipid][channel]
                    current_array[current_index][13] = chip_threshold[chipid]['global_threshold']
                except KeyError:
                    current_array[current_index][12] = -1
                    current_array[current_index][13] = -1
                try:
                    pixel = geometry.chips[chipid].channel_connections[channel]
                except KeyError:
                    pixel = geometry.unconnected_pixel
                except IndexError:
                    pixel = geometry.unconnected_pixel
                if pixel.pixelid is None:
                    current_array[current_index][2] = -1
                    current_array[current_index][3] = -1
                    current_array[current_index][4] = -1
                else:
                    current_array[current_index][2] = pixel.pixelid
                    current_array[current_index][3] = int(10*pixel.x)
                    current_array[current_index][4] = int(10*pixel.y)

                try:
                    current_array[current_index][10] = 1e3*((packet.dataword) * \
                        calib_data[str(chipid)][str(channel)]['gain_v'] + \
                        calib_data[str(chipid)][str(channel)]['gain_vcm'])
                except KeyError:
                    current_array[current_index][10] = -1
                try:
                    current_array[current_index][11] = 1e3 * calib_data[str(chipid)][str(\
                            channel)]['pedestal_v']
                except KeyError:
                    current_array[current_index][11] = -1

                cpu_time = block['time']
                current_array[current_index][14] = cpu_time
                ref_time = None
                if packet.chipid in last_timestamp.keys():
                    ref_time = last_timestamp[packet.chipid]
                current_timestamp = -1
                if not cpu_time < 0:
                    current_timestamp = Timestamp.from_packet(packet, cpu_time,
                                                              ref_time)
                    current_array[current_index][8] = current_timestamp.ns
                if len(last_timestamp.keys())==0 and not cpu_time < 0:
                    for chip in range(255):
                        last_timestamp[chip] = current_timestamp
                elif not cpu_time < 0:
                    last_timestamp[chipid] = current_timestamp

                if use_root:
                    (root_channelid[0], root_chipid[0], root_pixelid[0],
                            root_pixelx[0], root_pixely[0],
                            root_rawADC[0], root_rawTimestamp[0],
                            root_ADC[0], root_timestamp[0],
                            root_serialblock[0], root_v[0],
                            root_pdst_v[0], root_pixel_trim[0],
                            root_global_threshold[0],
                            root_cpu_timestamp[0]
                        ) = current_array[current_index]
                    ttree.Fill()
                else:
                    current_index += 1
                    if current_index == index_limit:
                        current_index = 0
                        numpy_arrays.append(np.empty((index_limit, 15),
                            dtype=np.int64))
                        current_array = numpy_arrays[-1]
loader.close()

if use_root:
    ttree.Write()
    fout.Write()
    fout.Close()
else:
    numpy_arrays[-1] = numpy_arrays[-1][:current_index]
    final_array = np.vstack(numpy_arrays)
    with h5py.File(outfile, 'w') as outfile:
        dset = outfile.create_dataset('data', data=final_array,
                dtype=final_array.dtype)
        dset.attrs['description'] = '''
    channel id | chip id | pixel id | int(10*pixel x) | int(10*pixel y) | raw ADC | raw
    timestamp | 6-bit ADC | full timestamp | serial index | converted voltage (mV) | calib
    pedestal voltage (mV) | chip global threshold | channel trim threshold'''

