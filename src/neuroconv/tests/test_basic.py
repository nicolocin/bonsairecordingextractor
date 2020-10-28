# test script
# will upload data as datalad directory later

from ..BonsaiRecordingExtractor import BonsaiRecordingExtractor
from ..BonsaiNwbConverter import *
import json
from pathlib import Path

import pynwb
from pynwb import NWBHDF5IO
from pynwb import NWBFile
from pynwb.ecephys import ElectricalSeries
from pynwb.ecephys import ElectrodeGroup
from pprint import pprint
from pandas import read_csv


# BonsaiRecordingExtractor inputs  
params = {
    "bonsai_dir": "data/jv_main",
    "metadata_file": "aquisition_oni.bonsai",
    "traces_file": "intan_2019-12-05T09_28_34.raw",
    "time": "intan-first-time_2019-12-05T09_28_34.csv",
}

def test_basic():
    RX = BonsaiRecordingExtractor(**params)
    md = RX.metadata

    # saves auto generated metadata, needs editing to generate NWB file
    with open('metadata_original.json', 'w') as outfile:
        json.dump(md, outfile, indent=4, sort_keys=True)

    # read edited metadata
    with (Path(__file__).with_name('data') / 'metadata_edited.json').open('r') as infile:
        edited_md = json.load(infile)

    # use edited metadata to generate NWB
    edited_RX = RX
    edited_RX.metadata = edited_md
    create_nwb(edited_RX, 'bonsai.nwb')
