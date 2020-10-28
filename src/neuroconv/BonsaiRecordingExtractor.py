from spikeextractors import RecordingExtractor, BinDatRecordingExtractor
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime
import numpy as np
import dateutil.parser as dp
import lxml
import csv
import re
from pandas import read_csv


def find_soup(soup, **kwargs):
    """
    Raise error when no matching text is found in soup

    Parameters
    ----------
    soup : BeautifulSoup object 
    kwargs : parameters to pass into find
    """
    if soup.find(**kwargs) is None:
        raise ValueError(
            f"Cannot find text matching {kwargs} in file. Check if specified file path is correct"
        )
    return soup.find(**kwargs)


def string_to_bool(text):
    if text == "true":
        text = True
    elif text == "false":
        text = False
    return text


class BonsaiRecordingExtractor(BinDatRecordingExtractor):
    extractor_name = "BonsaiRecording"
    has_default_locations = False
    is_writable = False
    mode = "folder"
    installation_mesg = "For parsing metadata from Bonsai XML outputs"

    """
    Extract metadata from Bonsai XML and csv ouputs

    """
    # TODO

    def __init__(
        self,
        bonsai_dir,
        metadata_file,
        traces_file,
        sampling_frequency=None,
        numchan=None,
        traces_dtype=None,
        time=None,
        **kwargs,
    ):
        """

        Parameters
        ----------
        bonsai_dir: str
            Path of directory that contains all outputs from Bonsai.  
            Files shouldn't be nested
        metatdata_file : str
            Path of bonsai XML file that contains workflow metadata relative to 
            `bonsai_dir`.  Usually ends with ".bonsai".  
        traces_file : str
            Path of binary time series files to read traces from TODO?
        sampling_frequency : int or None, optional
            Directory containing all Bonsai output
        numchan: int, optional
            Number of channels
        dtype : str
            TODO
        """
        self.bonsai_dir = str(Path(bonsai_dir).absolute())
        self.metadata_file = str(Path(self.bonsai_dir) / metadata_file)
        self.traces_file = str(Path(self.bonsai_dir) / traces_file)

        with open(self.metadata_file, "r") as f:
            soup = BeautifulSoup(f, "lxml")
        # remove disabled steps
        for tag in soup.find_all("expression", {"xsi:type": "Disable"}):
            tag.decompose()

        self.nodes = find_soup(soup, name="nodes")  # workflow information
        self.edges = find_soup(soup, name="edges")  # how channels connect to each other

        # attributes set at init
        if sampling_frequency is None:
            self.sampling_frequency = self.get_sampling_frequency()
        if numchan is None:
            self.numchan = self.get_num_channels()
            self.channel_ids = self.get_channel_ids()
        if traces_dtype is None:
            self.traces_dtype = "uint16"  # self.get_bin_dat_dtype()
        # if self.dtype not in ["uint16", "float32"]:
        #    raise ValueError(
        #        f"dtype {self.dtype} not valid. Choose 'uint16' or 'float32'"
        #    )
        self.session_start_time = self.get_session_start_time(time)
        super().__init__(
            self.traces_file,
            self.sampling_frequency,
            self.numchan,
            self.traces_dtype,
            **kwargs,
        )

        # compile metadata
        self.create_metadata()

    # TODO: make file or device specific?
    def get_bin_dat_dtype(self):
        """ 
        Determine data type of binary data that contains time series.

        Default is `uint16`.  For any binary data saved after an `AdcScale` operator, which 
        applies the 0.195 scale factor for spike detection, dtype is `float32`

        """
        pat = re.compile(r".*adcscale", re.IGNORECASE)
        try:
            adc = find_soup(self.nodes, name="combinator", attrs={"xsi:type": pat})
        except ValueError:  # no scaling in workflow
            return "uint16"
        return "float32"

    # TODO: check all instances of channel ?
    # QUESTION: how to figure out channel count when it's not speciftied
    def get_num_channels(self, device=None):
        """ 
        Return number of channels in int 

        Notes
        --------
        This assumes channel counts are in tag 'dsp:channelcount'
        """
        text = find_soup(self.nodes, name=re.compile(r".*channelcount$"))
        numchan = int(text.get_text())
        return numchan

    # TODO: get device names from <q1:DeviceIndex> and id # from <q1:SelectedIndex>
    def get_channel_ids(self):
        """ Return list of channel ids. If channel ids are not specified, return range(num_channels) """
        channel_ids = range(self.get_num_channels())
        return channel_ids

    def get_sampling_frequency(self, device="RHD", index=None):
        """ 
        Return sampling rate of recordings in Hertz 

        Parameters
        ----------
        device : str, optional
            Name of device. Case insensitive, supports regular expression.

        """
        pat = re.compile(r".*" + device + ".*", re.IGNORECASE)
        device_info = self.nodes.find_all("combinator", {"xsi:type": pat})
        if len(device_info) != 1:
            raise Exception("More than one RHD device, revise code")

        sf = find_soup(device_info[0], name=re.compile(r".*samplerate$")).get_text()
        sf = re.findall(r"\d+", sf)[0]  # samplerate can be a string
        return float(sf)

    def get_file_start_time(self, file_path=None, file_metadata=None):
        """ 
        Return first timestamp found in a csv file.
        Needs to specific either `file_path` and/or `file_metadata`.  

        Parameters
        ----------
        time : datetime object or str that can be converted to datetime object
        file : csv file path  
        """
        # parse file start time from filepath ( file_metadata

        if file_metadata:
            file_path = str(Path(self.bonsai_dir) / file_metadata["filename"])

        try:
            dat = parse_csv(file_path)
            if "Timestamp" in file_metadata["selector"] or "Timestamp" in dat.columns:
                return str(dp.parse(dat["Timestamp"][0]))
        except:
            # return first time stamp found in file
            with open(file_path) as f:
                reader = csv.reader(f)
                for row in reader:
                    for col in row:
                        return str(dp.parse(col))
        else:
            raise ValueError(f"Timestamp not found in file: {file_path}")

    def get_session_start_time(self, time=None):
        """ 
        Start time of a file or return first timestamp found in a csv file.

        Parameters
        ----------
        time : 
            datetime object or str that can be converted to datetime object OR
            file path to timestamp file
        """
        # not initialized
        if time is None:
            return str(datetime.now())

        try:
            return str(dp.parse(time))  # as datetime object
        except:
            fp = str(Path(self.bonsai_dir) / time)  # file path
            return self.get_file_start_time(fp)

    def create_device_metadata(self, ephys_device="rhd", file=None):
        """ 
        Add device information from Bonsai XML metadata 


        Parameters
        ----------
        ephys_device: str, optional
        file: str, optional
            Path of XML file to grab metadata from relative to self.bonsai_dir
            If None, defaults to self.metadata_file
        """
        # clean file
        if file is None:
            soup = self.nodes  # grab all
        else:
            with open(str(Path(self.bonsai_dir) / file), "r") as f:
                soup = BeautifulSoup(f, "lxml")
            # remove disabled steps
            for tag in soup.find_all("expression", {"xsi:type": "Disable"}):
                tag.decompose()

        # all devices, including ephys device.
        self.metadata["devices"] = []

        for d in soup.find_all(re.compile(".*:deviceindex")):
            device_md = dict()

            # device name is in the parent tag  e.g. <Combinator xsi:type="q1:InfoDevice">
            device_md["name"] = list(d.parent.attrs.values())[0].split(":")[-1]
            d_id = find_soup(d, name=re.compile(".*:selectedindex"))
            device_md["id"] = int(d_id.get_text())

            # TODO raise Exception('device already in index')
            # loop over attribute information in a device
            for s in d.next_siblings:
                if s is not None and s.name:
                    attr_name = d.next_sibling.name.split(":")[-1]
                    text = d.next_sibling.text
                    # convert strings to bool when necessary
                    text = string_to_bool(text)
                    device_md[attr_name] = text
                d = d.next_sibling

            # only two types for now
            if re.search(ephys_device, device_md["name"], re.IGNORECASE) is not None:
                device_md["type"] = "ephys"
            else:
                device_md["type"] = "other"

            self.metadata["devices"].append(device_md)

    def get_valid_files(self, exclude="bonsai"):
        """ 
        Filter out empty files and bonsai output files


        Parameters
        ----------
        exclude: str
            exclude files with this string in filename 
        """
        all_files = []
        for f in Path(self.bonsai_dir).iterdir():
            if (
                f.is_file() and (f.stat().st_size > 0) and exclude not in str(f.name)
            ):  # non-empty files only
                all_files.append(str(f.name))
        return all_files

    def _match_filename(self, md):
        # extract relevant information from file metadata
        # currently support csv and matrix

        # if there isn't a suffix, 'filename' or 'path' is the actual filename
        if "suffix" in md:
            try:
                md["file_pattern"] = md.pop("filename")
            except KeyError:
                md["file_pattern"] = md.pop("path")
            md["prefix"], md["ext"] = md["file_pattern"].rsplit(".")

        if "selector" in md:
            md["selector"] = list(md["selector"].split(","))

        # save metadata if file name matches pattern
        for f in self.get_valid_files():
            try:
                if f.startswith(md["prefix"]) and f.endswith(md["ext"]):
                    md["filename"] = f
                    return md
            except KeyError:
                if f == md["filename"]:
                    return md
            except:
                continue

        # file metadata not added when it doesn't match any non-empty file
        return None

    def _match_filetype(self, bonsai_type, bs, md):
        # extract type of file from the "ExternalizedMapping" node directly above
        # reader - inputs, writer - outputs
        md["bonsai_type"] = bonsai_type

        if "reader" in bonsai_type:
            md["filetype"] = "input"
        # elif md['filename'] == Path(self.traces_file).name:
        #    md['filetype'] = 'ephys'
        else:
            try:
                ext_map = bs.find_previous_sibling()
                # print(md['filename'])
                # print(ext_map)
                md["filetype"] = ext_map.property.attrs["displayname"]
            except:
                md["filetype"] = "other"

        return md

    def _match_file_metadata(self, bonsai_type):
        # TODO add dtype
        # TODO add file type (position, pulse etc)
        # find binary files & match non-empty files with metadata
        """
        Example
        ---------

        {'ext': 'raw',
         'file_name': 'intan_2019-12-05T09_28_34.raw',
         'layout': 'ColumnMajor',
         'overwrite': False,
         'path': 'intan_.raw',
         'prefix': 'intan_',
         'suffix': 'Timestamp'},

        TO ADD 
        {'bonsai_type': 'ephys', 'position', 'pulse', other,
         'combinator': matrix/csv reader or writer,
         'input': 

        }

        """
        tag = "expression" if "csv" in bonsai_type else "combinator"
        matching_files = self.nodes.find_all(
            tag, {"xsi:type": re.compile(f".*{bonsai_type}", re.IGNORECASE)}
        )

        if not matching_files:
            return None
        else:
            metadata_list = list()
            for f in matching_files:
                md = dict()
                for attr in f.children:
                    if attr.name is not None:
                        md[attr.name.split(":")[-1]] = string_to_bool(attr.string)

                # file type specifc operations
                md = self._match_filename(md)
                if md is not None:
                    md = self._match_filetype(bonsai_type, f, md)
                    metadata_list.append(md)

            # if all files are matched, len(matching_files) and len(metadata_list)
            return metadata_list

    def create_file_metadata(self):
        # Assumes self.files isn't initialized
        self.all_files = self.get_valid_files()

        if "files" not in self.metadata:
            self.metadata["files"] = list()
        # self.metadata["files"]["csv"] = self.get_csv_metadata()
        # self.metadata["files"]["matrix"] = self.get_matrix_metadata()

        # combinators that saves or reads files
        combinators = ["matrixwriter", "matrixreader", "csvwriter", "csvreader"]
        for comb in combinators:
            matched_metadata = self._match_file_metadata(comb)
            if matched_metadata is not None:
                self.metadata["files"].extend(matched_metadata)

    # TODO - not hard code?
    def create_metadata(self, **kwargs):
        self.metadata = dict()  # contains as much metadata as possible
        self.nwb_metadata = None  # contains a subset of self.metadata in a NWB conversion friendly format

        self.metadata["ephys"] = {
            "name": "Electrical Series",
            "starting_time": self.session_start_time,
            "sampling_frequency": self.sampling_frequency,
            "comments": "Generated from BonsaiRecordingExtractor",
        }

        self.create_device_metadata()
        self.create_file_metadata()

    def parse_csv(self, file_metadata):
        """ 
        Parameters
        ----------
        file_metadata: a dict in self.metadata['files']
        """
        fp = str(Path(self.bonsai_dir) / file_metadata["filename"])
        # col names saved in selector
        if file_metadata["includeheader"]:
            return read_csv(fp)
        else:
            try:
                return read_csv(fp, names=file_metadata["selector"])
            except:
                return read_csv(fp, header=None)

    def parse_csv_timestamps(
        self, dat, delta=True, timestamps_only=False, timestamp_col="Timestamp"
    ):
        """ 
        Parameters
        ----------
        dat: Dataframe from self.parse_csv
        delta: bool
            Whether the timestamp col represents time difference relative to session start time
        timestamps_only: bool
        """

        if delta:
            ts_delta = [
                dp.parse(t) - dp.parse(self.session_start_time) for t in dat[timestamp_col]
            ]
            ts_delta = np.array(ts_delta, dtype="timedelta64[ms]") / np.timedelta64(
                1, "s"
            )
            dat_clean = dat.drop("Timestamp", axis=1).squeeze().to_numpy()

        if timestamps_only:
            return ts_delta
        else:
            return dat_clean, ts_delta

    def parse_matrix(self, file_metadata):
        """ 
        Parameters
        ----------
        file_metadata: dict in self.metadata['files']
        """
        fp = str(Path(self.bonsai_dir) / file_metadata["filename"])

        # memmap order ‘C’ - row major, ‘F’ - column major
        order = "C" if (file_metadata["layout"] == "RowMajor") else "F"

        # TODO account for other dtypes
        if "dtype" in file_metadata:
            dtype = file_metadata["dtype"]
        else:
            dtype = "float64"
        #if "depth" in file_metadata:
        #    if file_metadata["depth"] == "S32":
        #        dtype = "int32"
        #else:
        #    dtype = "float64"

        try:
            dat = np.memmap(fp, dtype=dtype, order=order, shape=tuple(file_metadata["shape"]))
            return dat

        except Exception as e:
            print(f"Fail to read data: {fp}")
            print(e)
