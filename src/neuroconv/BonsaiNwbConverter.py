"""
Functions modified from NWBRecordingExtractor from spikeextractors

Example
-------
from neuroconv.BonsaiRecordingExtractor import BonsaiRecordingExtractor
from neuroconv.BonsaiNwbConverter import *

bonsai_params = {'bonsai_dir': 'data/',
                'metadata_file': 'aquisition_oni.bonsai',
                'traces_file': 'intan.raw',
                'time':'intan-first-time.csv'}

rx = BonsaiRecordingExtractor(**bonsai_params) 
create_nwb(rx, 'bonsai.nwb')

"""

from neuroconv.BonsaiRecordingExtractor import BonsaiRecordingExtractor
from spikeextractors import NwbRecordingExtractor
from spikeextractors.extractors.nwbextractors import *

import os
import uuid
from datetime import datetime
from collections import defaultdict, abc
from pathlib import Path
import numpy as np
import dateutil.parser as dp
from packaging.version import Version

try:
    import pynwb
    from pynwb import NWBHDF5IO
    from pynwb import NWBFile
    from pynwb import TimeSeries
    from pynwb.ecephys import ElectricalSeries
    from pynwb.ecephys import ElectrodeGroup
    from pynwb.behavior import SpatialSeries
    from pynwb.behavior import Position

    HAVE_NWB = True
except ModuleNotFoundError:
    HAVE_NWB = False


def add_nwb_devices(recording, nwbfile):
    """ 
    Add relevant device info from Bonsai recording to NWB file & 
    updates NWB metadata in recording  

    Note: only adds ephys devices at the moment

    Parameters
    ----------
    recording: RecordingExtractor
    save_path: NWB object
    """

    if "Ecephys" not in recording.nwb_metadata:
        recording.nwb_metadata["Ecephys"] = dict()

    if "Device" not in recording.nwb_metadata["Ecephys"]:
        recording.nwb_metadata["Ecephys"]["Device"] = []

    # TODO: handle cases with multiple ephys devices?
    for dev in recording.metadata["devices"]:
        # only ephys devices are recorded in NWB
        if dev["type"] == "ephys":
            # nwbfile.create_device(name=dev["name"])
            if "Device" not in recording.nwb_metadata["Ecephys"]:
                recording.nwb_metadata["Ecephys"]["Device"] = []
            # save metadata to recording
            recording.nwb_metadata["Ecephys"]["Device"].append({"name": dev["name"]})

    return NwbRecordingExtractor.add_devices(
        recording=recording, nwbfile=nwbfile, metadata=recording.nwb_metadata
    )


def add_nwb_electrode_groups(recording, nwbfile):
    """ 
    Adds relevant device info from Bonsai recording to NWB file & 
    updates NWB metadata in recording  

    Note: assumes electrode group information not available in recording.metadata

    Parameters
    ----------
    recording: RecordingExtractor
    save_path: NWB object
    """
    metadata = recording.nwb_metadata["Ecephys"]
    channel_ids = recording.get_channel_ids()

    # Electrode groups
    ephys_metadata = recording.nwb_metadata["Ecephys"]
    if "ElectrodeGroup" not in ephys_metadata:
        ephys_metadata["ElectrodeGroup"] = []

        # Check if 'groups' property exists in self._channel_properties
        if "group" in recording.get_shared_channel_property_names():
            RX_groups_names = list(np.unique(recording.get_channel_groups()))
        else:
            RX_groups_names = ["0"]
            # Electrode groups are required for NWB, for consistency we create group for Recording channels
            vals = [0] * len(channel_ids)
            recording.set_channel_groups(channel_ids=channel_ids, groups=vals)
        for grp_name in RX_groups_names:
            ephys_metadata["ElectrodeGroup"].append(
                {
                    "name": grp_name,
                    "description": "electrode_group_description",
                    "location": "electrode_group_location",
                    "device": ephys_metadata["Device"][0]["name"],
                }
            )

    # Tests if electrode groups exist in nwbfile, if not create them from metadata
    for grp in ephys_metadata["ElectrodeGroup"]:
        if str(grp["name"]) not in nwbfile.electrode_groups:
            nwbfile.create_electrode_group(
                name=str(grp["name"]),
                location=grp["location"],
                device=nwbfile.devices[grp["device"]],
                description=grp["description"],
            )

    recording.nwb_metadata["Ecephys"] = ephys_metadata
    return nwbfile


def add_nwb_electrodes(recording, nwbfile):
    """
    Note: NwbRecordingExtractor.add_electrodes doesn't add information to NWB metadata
    """
    return NwbRecordingExtractor.add_electrodes(
        recording=recording, nwbfile=nwbfile, metadata=None
    )


def add_nwb_electrical_series(recording, nwbfile):
    """
        Auxiliary static method for nwbextractor.
        Adds traces from recording object as ElectricalSeries to nwbfile object.
    """
    # ElectricalSeries aka traces data

    if "ElectricalSeries" not in recording.nwb_metadata["Ecephys"]:
        recording.nwb_metadata["Ecephys"]["ElectricalSeries"] = [
            {"name": "ElectricalSeries", "description": "electrical_series_description"}
        ]
    # Tests if ElectricalSeries already exists in acquisition
    channel_ids = list(recording.channel_ids)
    nwb_es_names = [ac for ac in nwbfile.acquisition]
    es = recording.nwb_metadata["Ecephys"]["ElectricalSeries"][0]
    if es["name"] not in nwb_es_names:
        # Creates an electrode table region with specified ids
        curr_ids = channel_ids
        table_ids = [list(nwbfile.electrodes.id[:]).index(id) for id in curr_ids]
        electrode_table_region = nwbfile.create_electrode_table_region(
            region=table_ids, description="electrode_table_region"
        )

        # Only name, data and electrodes are required
        ephys_ts = ElectricalSeries(
            name="ElectricalSeries",
            data=recording.get_traces().T,  # transpose
            electrodes=electrode_table_region,
            starting_time=recording.frame_to_time(0),
            rate=recording.get_sampling_frequency(),
            comments="Generated from BonsaiRecordingExtractor",
            description="acquisition_description",
        )
        nwbfile.add_acquisition(ephys_ts)

    return nwbfile


# TODO finish
def add_nwb_time_series(recording, nwbfile, bonsai_metadata=None):
    """ 
    Adds relevant time series data & metadata from Bonsai recording to NWB file & 
    updates NWB metadata in recording  

    Note: assumes electrode group information not available in recording.metadata

    Parameters
    ----------
    recording: RecordingExtractor
    nwbfile: NWB object
    bonsai_metadata: dictionary in the recording.metadata format
    """

    if bonsai_metadata:
        recording.metadata = bonsai_metadata

    # look for TimeSeries csv files
    ts_csv_files = [
        f
        for f in recording.metadata["files"]
        if f.get("nwb_class") == "TimeSeries"
        and (f.get("ext") == "csv" or "csv" in f.get("bonsai_type"))
    ]

    # TODO deal with cases when timestamps are relative e.g. heartbeat_2019-12-05T09_28_34.csv
    if ts_csv_files:
        for ts_file in ts_csv_files:
            dat = recording.parse_csv(ts_file)

            try:
                # 'Timestamps' columns are in ISO-8601 format (absolute time)
                dat_clean, ts_delta = recording.parse_csv_timestamps(dat)

                ts = TimeSeries(
                    name=ts_file["filename"],
                    data=dat_clean,
                    timestamps=ts_delta,
                    description=f'Data parsed from {ts_file["filename"]}',
                    comments="Timestamps are in seconds",
                )

                nwbfile.add_acquisition(ts)
            except Exception as e:
                print(
                    f'Fail to convert data to pynwb.base.TimeSeries: {ts_file["filename"]}'
                )
                print(e)

    # look for TimeSeries csv files
    ts_matrix_files = [
        f
        for f in recording.metadata["files"]
        if f.get("nwb_class") == "TimeSeries" and "matrix" in f.get("bonsai_type")
    ]

    if ts_matrix_files:
        for ts_file in ts_matrix_files:
            dat = recording.parse_matrix(ts_file)

            try:
                print(ts_file["filename"])
                # FOR NOW, GET DEFAULT TIME STAMPS FOR BNO55Device FROM quaterion-time_2019-12-05T09_28_34.csv
                ts_dat = recording.parse_csv(
                    {
                        "bonsai_type": "csvwriter",
                        "filename": "quaterion-time_2019-12-05T09_28_34.csv",
                        "nwb_class": "TimeSeries",
                        "includeheader": False,
                        "selector": ["Timestamp", "Value"],
                    }
                )
                ts_delta = recording.parse_csv_timestamps(ts_dat, timestamps_only=True)

                ts = TimeSeries(
                    name=ts_file["filename"],
                    data=dat,
                    timestamps=ts_delta,
                    description=f'Data parsed from {ts_file["filename"]}',
                )
                nwbfile.add_acquisition(ts)
            except Exception as e:
                print(
                    f'Fail to convert data to pynwb.base.TimeSeries: {ts_file["filename"]}'
                )
                print(e)

    return nwbfile


def add_nwb_behavior_module(recording, nwbfile):

    recording.nwb_metadata["Behavior"] = [
        {"name": "Behavior", "description": "behavior_module_description"}
    ]
    behavior_module = nwbfile.create_processing_module(
        name="behavior", description="processed behavioral data"
    )

    return nwbfile


def create_nwb(recording, save_path):
    """
    Use metadata in BonsaiRecordingExtractor to create a NWB files

    Parameters
    ----------
    recording: BonsaiRecordingExtractor
    save_path: str
    nwb_metadata: dict
        extra metadata info for constructing the nwb file (optional).
    """
    assert HAVE_NWB, NwbRecordingExtractor.installation_mesg

    assert (
        Version(pynwb.__version__) >= Version("1.3.3")
    ), "'write_recording' not supported for version < 1.3.3. Run pip install --upgrade pynwb"

    # if os.path.exists(save_path):
    #    read_mode = "r+"
    # else:
    #    read_mode = "w"

    # Update any previous metadata with user passed dictionary
    if recording.nwb_metadata is None:
        recording.nwb_metadata = dict()

    with NWBHDF5IO(save_path, mode="w") as io:
        # if read_mode == "r+":
        #    nwbfile = io.read()
        # else:
        if "NWBFile" not in recording.nwb_metadata:
            recording.nwb_metadata["NWBFile"] = {
                "session_description": "no description",
                "identifier": str(uuid.uuid4()),
                "session_start_time": dp.parse(recording.session_start_time),
            }
        nwbfile = NWBFile(**recording.nwb_metadata["NWBFile"])

        # Required
        # Add devices
        nwbfile = add_nwb_devices(recording=recording, nwbfile=nwbfile)

        # Add electrode groups
        nwbfile = add_nwb_electrode_groups(recording=recording, nwbfile=nwbfile)

        # Add electrodes
        nwbfile = add_nwb_electrodes(recording=recording, nwbfile=nwbfile)

        # Add electrical series
        nwbfile = add_nwb_electrical_series(recording=recording, nwbfile=nwbfile)

        # Add time series (if any)
        nwbfile = add_nwb_time_series(recording=recording, nwbfile=nwbfile)

        # Write to file
        io.write(nwbfile)
