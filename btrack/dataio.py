#!/usr/bin/env python
#-------------------------------------------------------------------------------
# Name:     BayesianTracker
# Purpose:  A multi object tracking library, specifically used to reconstruct
#           tracks in crowded fields. Here we use a probabilistic network of
#           information to perform the trajectory linking. This method uses
#           positional and visual information for track linking.
#
# Authors:  Alan R. Lowe (arl) a.lowe@ucl.ac.uk
#
# License:  See LICENSE.md
#
# Created:  14/08/2014
#-------------------------------------------------------------------------------


__author__ = "Alan R. Lowe"
__email__ = "code@arlowe.co.uk"

import re
import os
import numpy as np
import h5py
import json
import logging

# import core
from . import btypes
from . import constants

from collections import OrderedDict
from scipy.io import savemat



# get the logger instance
logger = logging.getLogger('worker_process')


class _PyTrackObjectFactory(object):
    def __init__(self):
        self.reset()

    def get(self, txyz, label=None, obj_type=0):
        """ get an instatiated object """
        if label is not None:
            class_label = label[0].astype('int')
            probability = label[1:].astype('float32')
        else:
            class_label = constants.States.NULL.value
            probability = np.zeros((1,))

        new_object = btypes.PyTrackObject()
        new_object.ID = self._ID
        new_object.t = txyz[0].astype('int')
        new_object.x = txyz[1]
        new_object.y = txyz[2]
        new_object.z = txyz[3]
        new_object.dummy = False
        new_object.label = class_label          # from the classifier
        new_object.probability = probability
        new_object.type = int(obj_type)

        self._ID += 1
        return new_object

    def reset(self):
        self._ID = 0

# instatiate the factory
ObjectFactory = _PyTrackObjectFactory()



def export_delegator(filename, tracker, obj_type=None):
    """ Export data from the tracker using the appropriate exporter """
    # assert(isinstance(tracker, BayesianTracker))
    assert(isinstance(filename, str))

    export_dir, export_fn = os.path.split(filename)
    _, ext = os.path.splitext(filename)

    assert(os.path.exists(export_dir))

    if ext == '.json':
        export_all_tracks_JSON(export_dir, tracker.tracks, cell_type=obj_type)
    elif ext == '.mat':
        export_MATLAB(filename, tracker.tracks)
    elif ext == '.hdf5' or ext == '.hdf':
        with HDF5FileHandler(filename, read_write='a') as hdf:
            hdf.write_tracks(tracker, obj_type=obj_type)
    else:
        logger.error(f'Export file format {ext} not recognized.')





def check_track_type(tracks):
    return isinstance(tracks[0], btypes.Tracklet)


def export_single_track_JSON(filename, track):
    """ export a single track as a JSON file """

    if not isinstance(filename, str):
        raise TypeError('Filename must be a string')

    if not isinstance(track, btypes.Tracklet):
        raise TypeError('Tracks must be of type btypes.Tracklet')

    json_export = track.to_dict()
    with open(filename, 'w') as json_file:
        json.dump(json_export, json_file, indent=2)


def export_JSON(filename, tracks):
    """ JSON Exporter for track data. """
    if not check_track_type(tracks):
        raise TypeError('Tracks must be of type btypes.Tracklet')

    # make a list of all track object data, sorted by track ID
    d = {"Tracklet_"+str(trk.ID):trk.to_dict() for trk in tracks}
    json_export = OrderedDict(sorted(list(d.items()), key=lambda t: t[1]['ID']))

    with open(filename, 'w') as json_file:
        json.dump(json_export, json_file, indent=2, separators=(',', ': '))


def export_all_tracks_JSON(export_dir,
                           tracks,
                           cell_type=None,
                           as_zip_archive=True):

    """ export_all_tracks_JSON

    Export all tracks as individual JSON files.

    Args:
        export_dir: the directory to export the tracks to
        tracks: a list of Track objects
        cell_type: a string representing the object (cell) type
        as_zip_archive: a boolean to enable saving to a zip archive

    Returns:
        None

    """

    assert(cell_type in ['GFP','RFP','iRFP','Phase',None])
    filenames = []

    logger.info(f'Writing out JSON files to dir: {export_dir}')
    for track in tracks:
        fn = "track_{}_{}.json".format(track.ID, cell_type)
        track_fn = os.path.join(export_dir, fn)
        export_single_track_JSON(track_fn, track)
        filenames.append(fn)

    # make a zip archive of the files
    if as_zip_archive:
        import zipfile
        zip_fn = "tracks_{}.zip".format(cell_type)
        full_zip_fn = os.path.join(export_dir, zip_fn)
        with zipfile.ZipFile(full_zip_fn, 'w') as zip:
            for fn in filenames:
                src_json_file = os.path.join(export_dir, fn)
                zip.write(src_json_file, arcname=fn)
                os.remove(src_json_file)

    file_stats_fn = "tracks_{}.json".format(cell_type)
    file_stats = {}
    file_stats[str(cell_type)] = {"path": export_dir,
                                  "zipped": as_zip_archive,
                                  "files": filenames}

    logger.info('Writing out JSON file list to: {}'.format(file_stats_fn))
    with open(os.path.join(export_dir, file_stats_fn), 'w') as filelist:
        json.dump(file_stats, filelist, indent=2, separators=(',', ': '))


def import_all_tracks_JSON(folder, cell_type='GFP'):
    """ import_all_tracks_JSON

    import all of the tracks as Tracklet objects, for further analysis.

    Args:
        folder: the directory where the tracks are

    Returns:
        tracks: a list of Tracklet objects
    """

    file_stats_fn = os.path.join(folder, "tracks_{}.json".format(cell_type))
    if not os.path.exists(file_stats_fn):
        raise IOError('Tracking data file not found: {}'.format(file_stats_fn))

    with open(file_stats_fn, 'r') as json_file:
        track_files = json.load(json_file)

    tracks = []
    # check to see whether this is a zipped file
    as_zipped = track_files[cell_type]['zipped']
    if as_zipped:
        import zipfile
        zip_fn = os.path.join(folder,"tracks_{}.zip".format(cell_type))
        with zipfile.ZipFile(zip_fn, 'r') as zipped_tracks:
            for track_fn in track_files[cell_type]['files']:
                track_file = zipped_tracks.read(track_fn)
                d = json.loads(track_file)
                d['cell_type'] = cell_type
                d['filename'] = track_fn
                tracks.append(btypes.Tracklet.from_dict(d))
        return tracks

    # iterate over the track files and create Track objects
    for track_fn in track_files[cell_type]['files']:
        with open(os.path.join(folder, track_fn), 'r') as track_file:
            d = json.load(track_file)
            d['cell_type'] = cell_type
            d['filename'] = track_fn
            tracks.append(btypes.Tracklet.from_dict(d))

    return tracks


def fate_table(tracks):
    """ Create a fate table of all of the tracks. This is used by the MATLAB
    exporter.
    """

    fate_table = {}
    for t in tracks:
        if t.fate not in list(fate_table.keys()):
            fate_table[t.fate] = [t.ID]
        else:
            fate_table[t.fate].append(t.ID)
    return fate_table


def export_MATLAB(filename, tracks):
    """ MATLAB Exporter for track data. """

    if not check_track_type(tracks):
        raise TypeError('Tracks must be of type btypes.Tracklet')
    logger.info(f'Writing out MATLAB files to dir: {filename}')
    export_track = np.vstack([trk.to_array() for trk in tracks])
    output = {'tracks': export_track,
              'track_labels':['x','y','frm','ID','parentID','rootID',
                              'class_label'],
              'class_labels':['interphase','prometaphase','metaphase',
                              'anaphase','apoptosis'],
              'fate_table': fate_table(tracks)}
    savemat(filename, output)



def export_LBEP(filename, tracks):
    """ export the LBEP table described here:
    https://public.celltrackingchallenge.net/documents/
        Naming%20and%20file%20content%20conventions.pdf
    """
    tracks.sort(key=lambda t: t.ID)
    if not filename.endswith('.txt'): filename+='.txt'
    with open(filename, 'w') as lbep_file:
        logger.info(f'Writing LBEP file: {filename}...')
        for track in tracks:
            lbep = f'{track.ID} {track.t[0]} {track.t[-1]} {track.parent}'
            lbep_file.write(f'{lbep}\n')



class _HDFHandler(object):
    def __init__(self, filename, read_write='r'):
        self.filename = filename
        logger.info('Opening HDF file: {0:s}'.format(self.filename))
        self._hdf = h5py.File(filename, read_write)
        self._states = list(constants.States)

    @property
    def object_types(self):
        return list(self._hdf['objects'].keys())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        if not self._hdf: return
        logger.info('Closing HDF file: {0:s}'.format(self.filename))
        self._hdf.close()

    def new_PyTrackObject(self, txyz, label=None, obj_type=0):
        """ Set up a new PyTrackObject quickly using data from a file """
        raise DeprecationWarning("Use 'get' function instead")





class HDF5FileHandler(_HDFHandler):
    """ HDF5FileHandler

    Generic HDF5 file hander for reading and writing datasets. This is
    inter-operable between segmentation, tracking and analysis code.

    LBEPR is a modification of the LBEP format to also include the root node
    of the tree.

        I - number of objects
        J - number of frames
        K - number of tracks

    Basic format of the HDF file is:
        segmentation/
            images          - (J x h x w) uint8 images of the segmentation
        objects/
            obj_type_1/
                coords      - (I x 5) [t, x, y, z, object_type]
                labels      - (I x D) [label, (softmax scores ...)]
                map         - (J x 2) [start_index, end_index] -> coords array
            ...
        tracks/
            obj_type_1/
                tracks      - (I x 1) [index into coords]
                dummies     - similar to coords, but for dummy objects
                map         - (K x 2) [start_index, end_index] -> tracks array
                LBEPR       - (K x 5) [L, B, E, P, R]
                fates       - (K x n) [fate_from_tracker, ...future_expansion]
            ...

    Notes:
        NOTE(arl): the final slice [:] reads the whole file in one go,
        since we are unlikely to have more objects than memory and we
        need to load them all anyway.

        NOTE(arl): should dummies be moved to coords?
    """

    def __init__(self, filename=None, read_write='r'):
        _HDFHandler.__init__(self, filename, read_write=read_write)

    @property
    def objects(self):
        """ Return the objects in the file """
        objects = []
        for ci, c in enumerate(self.object_types):
            # read the whole dataset into memory
            txyz = self._hdf['objects'][c]['coords'][:]
            labels = self._hdf['objects'][c]['labels'][:]
            n_obj = txyz.shape[0]
            assert(txyz.shape[0] == labels.shape[0])
            logger.info('Loading {} {}...'.format(c, txyz.shape))
            obj = [ObjectFactory.get(txyz[i,:], label=labels[i,:], obj_type=ci+1) for i in range(n_obj)]
            objects += obj
        return objects

    @property
    def dummies(self):
        """ Return the dummy objects in the file """
        raise NotImplementedError

    @property
    def tracks(self):
        """ Return the tracks in the file """
        raise NotImplementedError


    def write_tracks(self, tracker, obj_type=None):
        """ Write tracks to HDF file """
        logger.info(self.object_types)
        assert(obj_type in self.object_types)

        tracks = tracker.refs
        dummies = tracker.dummies
        lbep_table = np.stack(tracker.lbep, axis=0).astype(np.int32)

        # sanity check
        assert(lbep_table.shape[0] == len(tracks))

        logger.info(f'Writing tracks to HDF file: {self.filename}')
        hdf_tracks = np.concatenate(tracks, axis=0)

        hdf_frame_map = np.zeros((len(tracks),2), dtype=np.int32)
        for i, track in enumerate(tracks):
            if i > 0:
                offset = hdf_frame_map[i-1,1]
            else: offset = 0
            hdf_frame_map[i,:] = np.array([0, len(track)]) + offset

        if 'tracks' not in self._hdf:
            self._hdf.create_group('tracks')

        if obj_type in self._hdf['tracks']:
            logger.warning('Removing {} from HDF file. '.format(obj_type))
            del self._hdf['tracks'][obj_type]

        grp = self._hdf['tracks'].create_group(obj_type)
        grp.create_dataset('tracks', data=hdf_tracks, dtype='int32')
        grp.create_dataset('map', data=hdf_frame_map, dtype='uint32')

        # write out dummies
        if dummies:
            logger.info(f'Writing dummies to HDF file: {self.filename}')
            o = self.object_types.index(obj_type) + 1
            txyz = np.stack([[d.t, d.x, d.y, d.z, o] for d in dummies], axis=0)
            grp.create_dataset('dummies', data=txyz, dtype='float32')

        # write out the LBEP table
        logger.info(f'Writing LBEPR to HDF file: {self.filename}')
        grp.create_dataset('LBEPR', data=lbep_table, dtype='int32')

        # write out cell fates
        logger.info(f'Writing track fates to HDF file: {self.filename}')
        fate_table = np.stack([t.fate.value for t in tracker.tracks], axis=0)
        grp.create_dataset('fates', data=fate_table, dtype='int32')




def import_JSON(filename):
    """ generic JSON importer for localisations from other software """
    with open(filename, 'r') as json_file:
        data = json.load(json_file)
    objects = []
    for obj in data:
        txyz = [float(obj[k]) for k in ['t','x','y','z']]
        objects.append(ObjectFactory.get(txyz))
    return objects



if __name__ == "__main__":
    pass
