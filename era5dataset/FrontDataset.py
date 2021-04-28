from typing import final
import numpy as np
import torch
import os
import time
from datetime import datetime

import random
import numbers

from torch.utils.data import Dataset

from .ERA5Reader.readNetCDF import LatTokmPerLon
from .EraExtractors import DefaultEraExtractor


def labelnameToDataname(filename):
    return os.path.splitext(filename)[0]+".nc"

def datanameToLabelname(filename, mapTypes, removePrefix):
    return {key: os.path.join(str(x[0]), os.path.splitext(filename)[0][removePrefix:]+".txt") for key, x in mapTypes.items()}

# Dataset Class 
class WeatherFrontDataset(Dataset):
    """Front dataset."""
    def __init__(self, data_dir, label_dir = None, mapTypes = {"NA": ("", (35,70), (-40,35), (0.25,0.25), (1,1), None) }, levelRange = None, transform=None, outSize = None, printFileName = False, labelThickness = 2, label_extractor = None, asCoords = False, era_extractor = DefaultEraExtractor, has_subfolds = (False, False), removePrefix = 0):
        """
        Args:
            data_dir (string):      Directory with all the images.
            label_dir (string):     Directory with all the labls (fronts)
            validLats (int,int):    Lowest and Highest Latitude (-90 to 90) from wich the data shall be sampled
            validLons (int,int):    Lowest and Highest Longitude (0 to 360-resolution[1]) from wich the data shall be sampled
            resolution (float, float): Step Resolution in Latitudinal and Longitudinal direction
            transform (callable, optional): Optional transform to be applied on a sample.
        """
        self.data_dir = data_dir
        self.label_dir = label_dir

        # Cropsize (used before reading from ERA!)
        self.cropsize = outSize
        
        # Augmentationtuple (data-augmentation, label-augmentation)
        self.transform = transform

        # Function that extracts label data from a given range
        self.label_extractor = label_extractor
        self.asCoords = asCoords

        # Function that extracts era data from a given range
        self.era_extractor = era_extractor
        
        # Dictionary describing folder, latitudes, longitudes and resolution (signed) for different labels 
        self.mapTypes = mapTypes

        # Should labels be randomly drawn if multiple are available for the same data
        self.randomizeMapTypes = True

        
        # Levelrange of era to extract
        self.levelrange = levelRange
        # Latrange of era to extract for each mapType (uised for crop)
        self.latrange = {key: np.arange(int((90-x[1][0])*(1/np.abs(x[3][0]))),int((90-x[1][1])*(1/np.abs(x[3][0]))), 1) for key,x in self.mapTypes.items()}
        # lonrange of era to extract for each mapType (used for crop)
        self.lonrange = {key: np.arange(int(x[2][0]*(1/x[3][1])), int(x[2][1]*(1/x[3][1])), 1) for key,x in self.mapTypes.items()}

        # Print file information
        self.printFileName = printFileName

        # Extract in a km grid instead of lat lon
        self.extractRegularGrid = False

        # Are labels provided? Else do not return labels
        self.has_label = (not label_dir is None and not label_extractor is None)
        if label_extractor is None:
            print("No label extractor given, proceed without extracting labels")
        if label_dir is None:
            print("No label directory given, Labels need to be generated by the extractor")
        # Check if an era_extractor exists
        if era_extractor is None:
            print("No Era-Extractor given, abort execution!")
            exit(1)

        self.removePrefix = removePrefix
        self.hasSubfolds = has_subfolds
        # ERA Data is organized in subfolders (2017->03->20170201_00.nc)
        if(self.hasSubfolds[0]):
            self.fileList = []
            for fold in os.listdir(self.data_dir):
                for filen in os.listdir(os.path.join(self.data_dir, fold)):
                    if("B20" in filen):
                        pass
                    if(self.removePrefix == 8 and not ( "_00" in filen or "_06" in filen or "_12" in filen or "_18" in filen)):
                        pass
                    else:
                        # if the dataset extracts labels, check if the corresponding labels exist
                        if(self.has_label):
                            potLabel = datanameToLabelname(filen, self.mapTypes, self.removePrefix)
                            labelExists = False
                            for key, val in potLabel.items():
                                foldna, filena = val.split("/")
                                if filena in os.listdir(os.path.join(self.label_dir,foldna)):
                                    labelExists=True
                            if(labelExists):
                                self.fileList.append(os.path.join(fold,filen))
                        # if no labels are to be extracted simply append the data
                        else:
                            self.fileList.append(os.path.join(fold,filen))
        # ERA Data is organized without subfolders (2017 -> 20170101_00.nc)
        else:
            self.fileList = []
            for filen in os.listdir(self.data_dir):
                if(self.has_label):
                    potLabel = datanameToLabelname(filen, self.mapTypes, self.removePrefix)
                    labelExists = False
                    for key, val in potLabel.items():
                        foldna, filena = val.split("/")
                        if filena in os.listdir(os.path.join(self.label_dir, foldna)):
                            labelExists = True
                    if(labelExists):
                        self.fileList.append(filen)
                else:
                    self.fileList.append(filen)
        
        # Sort file list
        self.fileList = sorted(self.fileList)
    
    def __len__(self):
        # Length of all available Data (regardless of the existence of label!)
        return len(self.fileList)

    # Allow for slices or idx
    def __getitem__(self, idx):
        if not isinstance(idx, numbers.Number):
            print("Currently not working")
            exit(1)
            return self.getBatch(idx)
        filepath = self.fileList[idx]
        filename = ""
        if(self.hasSubfolds[0]):
            filename = filepath.split("/")[-1]
        else:
            filename = filepath
        if(filename == ""):
            print("fileNotFound")
            print(idx)
        img_name = os.path.join(self.data_dir, filepath)
        
        #Initialize projection type and seeds for possible transformations
        projection_type = 0
        extract_seed = datetime.now()
        transform_seed = datetime.now()
        mapType = list(self.mapTypes.keys())[0]
        fronts = None
        if(self.has_label):
            # all corresponding front names (Take the first them if multiple are available)
            if(self.hasSubfolds[1]):
                front_name = datanameToLabelname(filepath, self.mapTypes, self.removePrefix)
            else:
                if(self.hasSubfolds[0]):
                    front_name = datanameToLabelname(filename, self.mapTypes, self.removePrefix)
                else:
                    front_name = datanameToLabelname(filename, self.mapTypes, self.removePrefix)
            mapType, front_name = self.getProjectionTypeAndFilePath(front_name)
            # To distinguish the output name
            filename = os.path.splitext(filename)[0]+mapType+os.path.splitext(filename)[1]
            # Read Label Data
            #print("label:", filename)
            #print(front_name, mapType, filename)
            try:
                if(self.extractRegularGrid):
                    fronts = self.getRegularGridLabel(front_name, self.mapTypes[mapType][1], self.mapTypes[mapType][2], self.mapTypes[mapType][3], mapType, extract_seed )
                else:
                    fronts = self.getLabel(front_name, self.mapTypes[mapType][1], self.mapTypes[mapType][2], self.mapTypes[mapType][3], mapType, extract_seed )
            except:
                print("filename is", front_name)
            if(self.printFileName):
                print(idx)
                print(img_name)
                print(front_name)
                print()

        if(self.has_label and fronts is None):
            print("Did not extract a Front even though it should")
            print(idx, filename)
        # Read Image Data
        #print("image:", filename
        image = None
        try:
            if(self.extractRegularGrid):
                image = self.getRegularGridImage(img_name, self.mapTypes[mapType][1], self.mapTypes[mapType][2], self.mapTypes[mapType][3], extract_seed, transform_seed)
            else:
                image = self.getImage(img_name, self.mapTypes[mapType][1], self.mapTypes[mapType][2], self.mapTypes[mapType][3], extract_seed, transform_seed)
        except:
            print("filename is", filename)
        if(image is None):
            print("failed to extract image data")
            print(filename, img_name, front_name)
            print(idx)
            raise Exception("failed to extract image data {}".format(filename))
        mask = None
        if(len(self.mapTypes[mapType]) == 5 and (not self.mapTypes[mapType][4] is None)):
            mask = self.getMask(self.mapTypes[mapType][-1], self.mapTypes[mapType][1], self.mapTypes[mapType][2], self.mapTypes[mapType][3], extract_seed)
        # Perform transformation on the data (affine transformation + randm crop) => Crop enables equally sized images
        if self.transform:
            finalImage = self.transformImage(image, transform_seed)
            if(mask is None):
                finalMask = None
            else:
                finalMask = torch.from_numpy(self.transformImage(mask.reshape((1,*mask.shape)), transform_seed).reshape(*mask.shape)).detach()
            if(self.has_label):
                finalFronts = self.transformLabel(fronts, transform_seed)
                if(self.asCoords):
                    return [torch.from_numpy(finalImage), finalFronts, filename, finalMask]
                else:
                    return [torch.from_numpy(finalImage), torch.from_numpy(finalFronts), filename, finalMask]
            else:
                return [torch.from_numpy(finalImage), None, filename, finalMask]
        else:
            if(mask is None):
                pass
            else:
                mask = torch.from_numpy(mask)
            if(self.has_label):
                if(self.asCoords):
                    return [torch.from_numpy(image), fronts, filename, mask]
                else:
                    return [torch.from_numpy(image), torch.from_numpy(fronts), filename, mask]
            else:
                return [torch.from_numpy(image), None, filename, mask]


    def getCropRange(self, latrange, lonrange, res, seed):
        if(self.cropsize is None):
            return latrange, lonrange
        else:
            # perform crop before reading data, to reduce memory usage
            common_seed= seed
            h,w = int(np.abs((latrange[1]-latrange[0]+res[0]-0.001)/res[0])), int(np.abs((lonrange[1]-lonrange[0])/res[1]))
            th,tw = self.cropsize
            random.seed(common_seed)
            i = random.randint(0, h-th)*res[0]
            j = random.randint(0, w-tw)*res[1]
            th *= res[0]
            tw *= res[1]
            return (latrange[0]+i, latrange[0]+i+th), (lonrange[0]+j, lonrange[0]+j+tw)
    def getImage(self, filename, latrange, lonrange, res, seed, tseed = 0):
        tgt_latrange, tgt_lonrange = self.getCropRange(latrange, lonrange, res, seed)
        return self.era_extractor(filename, tgt_latrange, tgt_lonrange, self.levelrange, tseed)

    def getLabel(self, filename, latrange, lonrange, res, types, seed):
        tgt_latrange, tgt_lonrange = self.getCropRange(latrange, lonrange, res, seed)
        return self.label_extractor(filename, (tgt_latrange[0], tgt_latrange[1]), (tgt_lonrange[0], tgt_lonrange[1]), res, types)

    def getMask(self, mask, latrange, lonrange, res, seed):
        tgt_latrange, tgt_lonrange = self.getCropRange(latrange, lonrange, res, seed)
        return mask[int((90-tgt_latrange[0])/np.abs(res[0])):int((90-tgt_latrange[1])/np.abs(res[0])), int((180+tgt_lonrange[0])/res[1]):int((180+tgt_lonrange[1])/res[1])]


    def transformImage(self, image, seed):
        if(self.transform[0] is None):
            return image
        finalImage = np.zeros_like(image)
        for channel in range(image.shape[0]):
            #for level in range(image.shape[1]):
            random.seed(seed)
            finalImage[channel, :,:] = self.transform[0](image[channel,:,:])
        return finalImage
    
    def transformLabel(self, label, seed):
        if(self.transform[1] is None):
            return label
        if(self.asCoords):
            finalLabel = label
            for group in range(len(label)):
                random.seed(seed)
                finalLabel[group] = self.transform[1](finalLabel[group])
        else:
            finalLabel = np.zeros((label.shape))
            for channel in range(label.shape[2]):
                random.seed(seed)
                finalLabel[:,:,channel] = self.transform[1](label[:,:,channel])
        return finalLabel

    def getProjectionTypeAndFilePath(self, front_name):
        projection_type = ""
        keys, names = [], []
        for key, fname in front_name.items():
            currFold = os.path.join(self.label_dir, key)
            # get filename without path
            filename = fname.split("/")[-1]
            #print(filename, currFold, fname)
            #print(os.listdir(currFold))
            if(filename in os.listdir(currFold)):
                keys.append(key), names.append(os.path.join(self.label_dir, fname))
        idx = 0
        if(len(keys)>0):
            if(self.randomizeMapTypes):
                idx = random.randint(0,len(keys)-1)
            return keys[idx], names[idx]
        # No Label found 
        print(front_name)
        print(os.listdir(self.label_dir))
        print("Invalid label data pair, no label found!")
        return projection_type, front_name

    def __repr__(self):
        myString = "WeatherFrontDataset\n"
        myString += str(self.__dict__)
        return myString

    def getInfo(self):
        myString = "WeatherFrontDataset\n"
        myString += "data_dir :: "+ "str :: " +str(self.data_dir)+" :: end\n"
        myString += "label_dir :: "+ "str :: " +str(self.label_dir)+" :: end\n"
        myString += "map_types :: "+ "dict(str: tuple(str, tuple(float,float), tuple(float,float), tuple(float,float))) :: " +str(self.mapTypes)+" :: end\n"
        myString += "levelrange :: "+ "list(int) :: " +str(list(self.levelrange))+" :: end\n"
        myString += "transforms :: "+ "obj :: " +str(self.transform)+" :: end\n"
        myString += "outsize :: "+ "tuple(int,int) :: " +str(self.cropsize)+" :: end\n"
        myString += "translat :: "+ "tuple(int,int) :: " +str(self.label_extractor.imageCreator.maxOff)+" :: end\n"
        myString += "printFileName :: "+ "bool :: " +str(self.printFileName)+" :: end\n"
        myString += "labelThickness :: "+ "int :: " +str(self.label_extractor.imageCreator.thickness)+" :: end\n"
        myString += "labelGrouping :: "+ "str :: " +str(self.label_extractor.imageCreator.labelGrouping)+" :: end\n"
        myString += "Variables :: "+ "list(str) :: " +str(self.era_extractor.variables)+" :: end\n"
        myString += "NormType :: "+ "int :: " +str(self.era_extractor.reader.normalize_type)+" :: end\n"
        return myString

class WeatherFrontBatch:
    def __init__ (self, data, label_as_float = True, transpose_rate = 0.5, swap_indices = None):
        transposed_data = (list(zip(*data)))
        self.data = torch.stack(transposed_data[0],0).float()
        if(transposed_data[1][0] is None):
            self.labels = None
        else:
            if(label_as_float):
                self.labels = torch.stack(transposed_data[1],0).float()
            else:
                self.labels = torch.stack(transposed_data[1],0).long()
        self.filenames = transposed_data[2]
    
    def pin_memory(self):
        self.data = self.data.pin_memory()
        return [self.data, self.labels, self.filenames]


class WeatherFrontsAsCoordinatesBatch:
    def __init__ (self, data, label_as_float = True, transpose_rate = 0.5, swap_indices = None):
        transposed_data = (list(zip(*data)))
        self.data = torch.stack(transposed_data[0],0).float()

        if(transposed_data[1][0] is None):
            self.labels = None
        else:
            self.labels = transposed_data[1]
        if(transposed_data[3][0] is None):
            self.masks = None
        else:
            self.masks = torch.stack(transposed_data[3],0).float()
        self.filenames = transposed_data[2]
    
    def pin_memory(self):
        self.data = self.data.pin_memory()
        return [self.data, self.labels, self.filenames, self.masks]


class collate_wrapper:
    def __init__(self, binary = True, asCoordinates=False, transpose_rate = 0.5, swap_indices = None):
        self.label_as_float = binary
        self.transpose_rate = transpose_rate
        self.swap_indices = swap_indices
        self.asCoords = asCoordinates
    def __call__(self, batch):
        if(self.asCoords):
            return WeatherFrontsAsCoordinatesBatch(batch, label_as_float=self.label_as_float, transpose_rate=self.transpose_rate, swap_indices = self.swap_indices)
        else:
            return WeatherFrontBatch(batch, label_as_float=self.label_as_float, transpose_rate=self.transpose_rate, swap_indices = self.swap_indices)

    
    
