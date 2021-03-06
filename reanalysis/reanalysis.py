import gcsfs
import boto3
import os
from glob import glob
from multiprocessing import Pool, cpu_count
import pandas as pd
from time import time
import xarray as xr


def s3List(bucketName, prefixName, nameSelector, fileformat):
    """
        This function takes an S3 bucket name and prefix (flat directory path) and returns a list of netcdf file.
            This function utilizes boto3's continuation token to iterate over an unlimited number of records.

        BUCKETNAME -- A bucket on S3 containing GeoTiffs of interest
        PREFIXNAME -- A S3 prefix.
        NAMESELECTOR -- A string used for selecting specific files. E.g. 'SC' for SC_R_001.tif.
        FILEFORMAT -- A string variant of a file format.
    """
    # Set the Boto3 client
    s3_client = boto3.client('s3')
    # Get a list of objects (keys) within a specific bucket and prefix on S3
    keys = s3_client.list_objects_v2(Bucket=bucketName, Prefix=prefixName)
    # Store keys in a list
    keysList = [keys]
    # While the boto3 returned objects contains a value of true for 'IsTruncated'
    while keys['IsTruncated'] is True:
        # Append to the list of keys
        # Note that this is a repeat of the above line with a contuation token
        keys = s3_client.list_objects_v2(Bucket=bucketName, Prefix=prefixName,
                                         ContinuationToken=keys['NextContinuationToken'])
        keysList.append(keys)
    # Create a list of GeoTiffs from the supplied keys
    #     While tif is hardcoded now, this could be easily changed.
    pathsList = []
    for key in keysList:
        paths = ['s3://' + bucketName + '/' + elem['Key'] for elem in key['Contents'] \
                 if elem['Key'].find('{}'.format(nameSelector)) >= 0 and elem['Key'].endswith(fileformat)]
        pathsList = pathsList + paths
    return pathsList


def get_reanalysis_paths(start_date: str, end_date: str, frequency: str):
    """Get a list of paths on s3 that represent a time series of reanalysis data.
    Example of correct format:
        `get_reanalysis_paths('2003-09-10', '2004-09-10', '12HR)`
    """
    bucket = 'nwm-archive'
    s3client = boto3.client("s3")
    years = sorted(list(set([d.split("-")[0] for d in [start_date, end_date]])))
    if len(years) > 1:
        years = list(range(int(years[0]), int(years[1]) + 1))
    all_paths = []
    for yr in years:
        all_paths += s3List(bucket, str(yr), 'CHRTOUT', 'DOMAIN1.comp')
    dates_wanted = list(pd.date_range(start_date, end_date, freq=frequency).strftime('%Y%m%d%H'))
    s3paths = [path for path in all_paths if path.split("/")[-1][:10] in dates_wanted]
    return s3paths


def s3download(s3paths: list, download_dir: str):
    """Downloads a all s3 files in a given list of s3paths to a specified directory"""
    s3client = boto3.client("s3")
    for f in s3paths:
        bucket = f.split(r"s3://")[1].split(r"/")[0]
        key = f.split(f's3://{bucket}/')[-1]
        filename = os.path.basename(f)
        if not os.path.exists(os.path.join(download_dir, filename)):
            s3client.download_file(bucket, key, os.path.join(download_dir, filename))
    return


def s3_single_dl(args: tuple):
    """Unpack tuple of arguments (s3path, download directory) to allow parallel download from s3"""
    file = args[0]
    download_dir = args[1]
    s3client = boto3.client("s3")
    bucket = file.split(r"s3://")[1].split(r"/")[0]
    key = file.split(f's3://{bucket}/')[-1]
    filename = os.path.basename(file)
    if not os.path.exists(os.path.join(download_dir, filename)):
        s3client.download_file(bucket, key, os.path.join(download_dir, filename))


def s3download_parallel(s3paths: list, download_dir: str):
    """Downloads a all s3 files in a given list of s3paths to a specified directory in parallel"""
    args = [(f, download_dir) for f in s3paths]
    with Pool(int(cpu_count()*2)) as p:
        p.map(s3_single_dl, args)
    return


def plotReanalysis(df: pd.DataFrame, comid: int, freq: str, flow: bool = True):
    """Function for plotting Reanalysis datasets"""
    start = str(df.index[0]).split(' ')[0]
    end = str(df.index[-1]).split(' ')[0]
    ax = df.plot(figsize=(20, 6),
                 title=f"NWM Reanalysis for COMID: {comid} from dates {start} to {end} in {freq} steps")
    ax.grid(True, which="both")
    if flow:
        ax.set(xlabel="Date", ylabel="Streamflow (cms)")
    else:
        ax.set(xlabel="Date", ylabel="Depth (m)")
        

def get_reanalysis_paths_gs(start_date, end_date, freq, target_analysis):
    fs = gcsfs.GCSFileSystem(project='national-water-model-v2')
    subfolders=fs.ls('national-water-model-v2')
    
    if target_analysis == 'full_physics':
        target_dir = subfolders[0]
    elif target_analysis == 'long_range':
        target_dir = subfolders[1]
    else:
        raise('unavailable target analysis directory. Please double check!')
        
    records_wanted = list(pd.date_range(start_date, end_date, freq=freq).strftime('%Y%m%d%H%M'))
    gs_paths = []
    for record in records_wanted:
        gs_paths.append(target_dir + record[:4] + "/" + record +'.CHRTOUT_DOMAIN1.comp')
    return gs_paths    


def gcp_single_dl(file_path, download_dir = './data'):
    fs = gcsfs.GCSFileSystem(project='national-water-model-v2')
    output_path = download_dir + "/" + file_path.split("/")[-1]
    fs.download(file_path, output_path)


def data_access_gs(file_paths_list, gs_access_method='real-time', download_dir='./data'):
    start = time()
    fs = gcsfs.GCSFileSystem(project='national-water-model-v2')
    if gs_access_method == 'real-time':
        openfiles = [fs.open(f, "rb") for f in file_paths_list]
        all_data = xr.open_mfdataset(openfiles)
        print(round((time()-start), 2), 'seconds to access the data for the given duration')
    if gs_access_method == 'download':
        with Pool(int(cpu_count()*2)) as p:
            p.map(gcp_single_dl, file_paths_list)
        files = glob(download_dir + '/*')
        assert len(files) == len(file_paths_list), 'Downloading error'
        all_data = xr.open_mfdataset(files)
        print(round((time()-start), 2), 'seconds to download', sum([os.path.getsize(f) for f in files])/1e9, 'GB of data for the given duration')
    return all_data
