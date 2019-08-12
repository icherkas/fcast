# NWM.py is a set of class objects representing ShortRange and MediumRange
# MediumRange forecasts from the National Water Model. It also includes 
# Assim, which is a class representing the model analysis assimilation
# data. This file is essentially a wrapper around xarray, for the NWM,
# reading from Google Cloud Storage using gcsfs. This file only works 
# on python version 3.6 or newer.
#
# Author: Alec Brazeau (abrazeau@dewberry.com)
#
# Copyright: Dewberry
#
# ----------------------------------------------------------------------

# Import the required libs
import gcsfs
import xarray as xr
import matplotlib.pyplot as plt
import numpy as np
import json
import pandas as pd
from scipy.interpolate import interp1d
import boto3
from fcast import GageUSGS
import sys

# Set global variables
AA = 'analysis_assim'
BUCKET = 'national-water-model'
SR = 'short_range'
MR = 'medium_range'
CR = 'channel_rt'
EXT = 'conus.nc'

class NWM:
    """A generic class object representation of a NWM netcdf file on GCS

    This is a super class for ShortRange, MediumRange and Assim.

    Parameters:
        fs (gcsfs.core.GCSFileSystem): The mounted Google Cloud Storage Bucket using gcsfs.
        comid (int): The comID that corresponds to the stream segment of interest.
        date (str): The date of the model output being used. (e.g. '20190802' for Aug 2, 2019)
        start_hr (int): The starting time (UTC) on for the date specified.
    """

    def __init__(self, fs: gcsfs.core.GCSFileSystem, comid: int, date: str, start_hr: int):

        # assert that the user system is >= python version 3.6
        ver = sys.version_info
        assert ver[0] == 3 and ver[1] >= 6, "Must be using python version 3.6 or newer"

        self._fs = fs
        self._comid = comid
        self._date = date
        self._start_hr = str(start_hr).zfill(2)

    @property
    def comid(self):
        """The comID that corresponds to the stream segment of interest."""
        return self._comid
    
    @property
    def date(self):
        """The date of the NWM output being utilized"""
        return self._date

    @property
    def start_hr(self):
        """The starting time (UTC) on the date specified"""
        return self._start_hr 

    def get_NWM_rc(self, rc_filepath = r'data/hydroprop-fulltable2D.nc'):
        """Opens the hydroprop-fulltable2D.nc file and retireves rating curves"""
        ds = xr.open_dataset(rc_filepath)
        dis_ds = ds.Discharge.sel(CatchId=self._comid)
        dis_df = dis_ds.to_dataframe().reset_index().drop(columns=['CatchId']).dropna()
        f = interp1d(dis_df.Discharge, dis_df.Stage, kind='cubic')
        return f, dis_df

class Assim(NWM):
    """A representation of an Analysis Assimilation NWM netcdf file on GCS

    This is used to get the initial time and streamflow for a forecast being made

    Parameters:
        fs (gcsfs.core.GCSFileSystem): The mounted Google Cloud Storage Bucket using gcsfs.
        comid (int): The comID that corresponds to the stream segment of interest.
        date (str): The date of the model output being used. (e.g. '20190802' for Aug 2, 2019)
        start_hr (int): The starting time (UTC) on for the date specified.
        hr (int, optional): The hour of the analysis assim of interest (e.g. 0, 1, or 2). Defaults to 0
    """

    def __init__(self, fs, comid, date, start_hr, hr = 0):

        super().__init__(fs, comid, date, start_hr)

        self._hr = hr
        self._filepath = f'{BUCKET}/nwm.{date}/{AA}/nwm.t{start_hr}z.{AA}.{CR}.tm0{self._hr}.{EXT}'
        self.__file = self._fs.open(self._filepath, 'rb')
        self.__assim = xr.open_dataset(self.__file)

    @property
    def filepath(self):
        """The filepath of the netcdf on GCS being utilized"""
        return self._filepath
    
    @property
    def assim_time(self):
        """The analysis assimilation time"""
        return self.__assim.sel(feature_id=self._comid)['time'].values[0]
    
    @property
    def assim_flow(self):
        """The streamflow at the analysis assimilation time"""
        return self.__assim['streamflow'].to_dataframe().loc[self._comid].values[0]

    @property
    def nfiles(n: int = 3):
        """The number of available files for this output (NWM v2 has 3 for analysis_assim)"""
        return n

    @property
    def hr(self):
        """The hour of the analysis assim of interest (e.g. 0, 1, or 2). Defaults to 0"""
        return self._hr

    def copy_to_local(self, folder):
        """Allows the download of the file being used to a specified folder"""
        with self._fs.open(self._filepath, 'rb') as f:
            with open(os.path.join(folder, os.path.basename(self._filepath)), 'wb') as fout:
                fout.write(f.read())

class ShortRange(NWM):
    """A representation of a Short Range forecast made using NWM netcdf files on GCS

    Pulls the relevant files from GCS to make an 18 hour streamflow forecast beginning 
    at a specified date and start time (UTC).

    Parameters:
        fs (gcsfs.core.GCSFileSystem): The mounted Google Cloud Storage Bucket using gcsfs.
        comid (int): The comID that corresponds to the stream segment of interest.
        date (str): The date of the model output being used. (e.g. '20190802' for Aug 2, 2019)
        start_hr (int): The starting time (UTC) on for the date specified.
    """

    def __init__(self, fs, comid, date, start_hr):

        super().__init__(fs, comid, date, start_hr)

        def get_filepaths():
            """Get the paths of the files used to build the forecast on GCS"""
            filepaths = []
            for i in range(1,19): # for times 1-18
                hr_from_start = str(i).zfill(3)
                filepath = f'{BUCKET}/nwm.{self._date}/{SR}/nwm.t{self._start_hr}z.{SR}.{CR}.f{hr_from_start}.{EXT}'
                filepaths.append(filepath)
            return filepaths

        self._filepaths = get_filepaths()

        def open_datas():
            """Read all forecast files into one xarray dataset"""
            openfiles = []
            for f in self._filepaths:
                file = fs.open(f, 'rb')
                openfiles.append(file)
            return xr.open_mfdataset(openfiles)

        self._ds = open_datas()

    @property
    def filepaths(self):
        """A list of the filepaths used to build the forecast"""
        return self._filepaths

    @property
    def ds(self):
        """The stacked xarray dataset representing the forecast"""
        return self._ds

    @property
    def nfiles(self):
        """the number of files that amke up the forecast"""
        return len(self._filepaths)
    
    def get_streamflow(self, assim_time, assim_flow, plot=False):
        """Get the streamflow forecast in a pandas dataframe and optionally plot it"""
        output_da = self._ds.sel(feature_id=self._comid)['streamflow']
        times = output_da['time'].values
        flows = output_da.values
        d = {**{assim_time: assim_flow}, **dict(zip(times, flows))}
        df = pd.DataFrame([d]).T.rename(columns={0:'streamflow'})
        if plot:
            ax = df.plot(figsize=(20,6), title=f'Short-range 18-hour forecast for COMID: {self._comid}')
            ax.grid(True, which="both")
            ax.set(xlabel='Date', ylabel='Streamflow (cms)')
        return df

    def copy_to_local(self, folder):
        """Allows the download of all files being used to a specified folder"""
        for file in self._filepaths:
            with self._fs.open(self._filepath, 'rb') as f:
                with open(os.path.join(folder, os.path.basename(self._filepath)), 'wb') as fout:
                    fout.write(f.read())

class MediumRange(NWM):
    """A representation of a Medium Range forecast made using NWM netcdf files on GCS

    Pulls the relevant files from GCS to make an 18 hour streamflow forecast beginning 
    at a specified date and start time (UTC).

    Parameters:
        fs (gcsfs.core.GCSFileSystem): The mounted Google Cloud Storage Bucket using gcsfs.
        comid (int): The comID that corresponds to the stream segment of interest.
        date (str): The date of the model output being used. (e.g. '20190802' for Aug 2, 2019)
        start_hr (int): The starting time (UTC) on for the date specified.
    """

    def __init__(self, fs, comid, date, start_hr):

        super().__init__(fs, comid, date, start_hr)

        def get_filepaths():
            """Get the filepaths that will be used to build the forecast. One list for each member"""
            filepaths = []
            for i in range(1,8): # ensemble members
                mem = str(i)
                mem_filepaths = []
                for i in range(3, 205, 3): # for times 3-240 or 3-204 in steps of 3
                    hr = str(i).zfill(3)
                    filepath = f'{BUCKET}/nwm.{self._date}/{MR}_mem{mem}/nwm.t{self._start_hr}z.{MR}.{CR}_{mem}.f{hr}.{EXT}'
                    mem_filepaths.append(filepath)
                filepaths.append(mem_filepaths)
            return filepaths

        self._filepaths = get_filepaths()

        def open_datas():
            """Open each members files into one xarray dataset"""
            mem_datasets = []
            for mem in self._filepaths:
                openfiles = []
                for f in mem:
                    file = fs.open(f, 'rb')
                    openfiles.append(file)
                mem_datasets.append(xr.open_mfdataset(openfiles))
            return mem_datasets

        self._mem_dsets = open_datas()

    @property
    def filepaths(self):
        """A list of lists, each containing the filepaths used for each member"""
        return self._filepaths

    @property
    def mem_dsets(self):
        """A list of stacked xarray datasets, each representing a member"""
        return self._mem_dsets

    @property
    def nfiles(self):
        """The total number of files used to build the forecast"""
        return int(len(self._filepaths) * len(self._filepaths[0]))

    def get_streamflow(self, assim_time, assim_flow, plot=False):
        """Get the forecasted streamflow for all members in one pandas dataframe. Optionally plot it."""
        outjson = []
        for ds in self._mem_dsets:
            output_da = ds.sel(feature_id=self._comid)['streamflow']
            times = output_da['time'].values.astype(str)
            arr = output_da.values
            d = {}
            d[ds.attrs['ensemble_member_number']] = {**{str(assim_time): assim_flow}, **dict(zip(times, arr))}
            outjson.append(d)
        df = pd.concat([pd.read_json(json.dumps(x), orient='index') for x in outjson]).T
        df['mean'] = df.mean(axis=1)
        if plot:
            ax = df.plot(figsize=(20,6), title=f'Medium-range seven member ensemble forecast for COMID: {self._comid}')
            ax.legend(title='Ensemble Members')
            ax.grid(True, which="both")
            ax.set(xlabel='Date', ylabel='Streamflow (cms)')
        return df

    def copy_to_local(self, folder):
        """Allows the download of all files being used to a specified folder"""
        for mem in self._filepaths:
            for file in mem:
                with self._fs.open(self._filepath, 'rb') as f:
                    with open(os.path.join(folder, os.path.basename(self._filepath)), 'wb') as fout:
                        fout.write(f.read())


## ------------------------------------------------------------------ ##
## -------------------------- Functions ----------------------------- ##
## ------------------------------------------------------------------ ##

def get_USGS_stations(comid, s3path = 's3://nwm-datasets/Data/Vector/USGS_NHDPlusv2/STATID_COMID_dict.json'):
    """Given a comid, go find the corresponding USGS gage ids"""
    s3 = boto3.resource('s3')
    bucket_name = s3path.split(r"s3://")[1].split(r"/")[0]
    key = s3path.split(r"{}/".format(bucket_name))[1]
    content_object = s3.Object(bucket_name=bucket_name, key=key)
    file_content = content_object.get()['Body'].read().decode('utf-8')
    json_content = json.loads(file_content)
    gageids = []
    for k, v in json_content.items():
        if v == comid:
            gageids.append(k)
    return gageids

def get_USGS_rc(comid):
    """Given a comid, get the rating curve for the matching USGS Gages"""
    gageids = get_USGS_stations(comid)
    rcs = []
    for gage in gageids:
        try:
            rc = GageUSGS(gage).rating_curve.dropna()
            f = interp1d(rc.DEP_cms, rc.INDEP_SHIFT_m, kind='cubic')
            rcs.append((f, rc))
        except AssertionError as e:
            print(f'{e} for station {gage}')
    if len(rcs) == 1:
        rcs = rcs[0]
    return rcs
