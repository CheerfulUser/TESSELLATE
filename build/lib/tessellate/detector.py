import lightkurve as lk
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import pandas as pd
from PRF import TESS_PRF
from copy import deepcopy
from photutils.detection import StarFinder
from astropy.stats import sigma_clipped_stats
from scipy.ndimage import center_of_mass
import multiprocessing
from joblib import Parallel, delayed 
import warnings
warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", category=RuntimeWarning) 
from tqdm import tqdm
from time import time as t

from .dataprocessor import DataProcessor

# -- Primary Detection Functions -- #

def _correlation_check(res,data,prf,corlim=0.8,psfdifflim=0.5):
    """
    Iterates over sources picked up by StarFinder in parent function.
    Cuts around the coordinates (currently size is 5x5).
    Finds CoM of cut to generate PSF.
    Compares cut with generated PSF, uses np.corrcoef (pearsonr) to judge similarity.
    """

    cors = []
    diff = []
    xcentroids = []
    ycentroids = []
    for _,source in res.iterrows():
        try:
            x = np.round(source['xcentroid']).astype(int)
            y = np.round(source['ycentroid']).astype(int)
            cut = deepcopy(data)[y-2:y+3,x-2:x+3]
            cut[cut<0] = 0
            
            if np.nansum(cut) > 0.95:
                cut /= np.nansum(cut)
                cm = center_of_mass(cut)

                if (cm[0]>0) & (cm[0]<5) & (cm[1]>0) & (cm[1]<5):
                    localpsf = prf.locate(cm[1],cm[0],(5,5))
                    if np.nansum(localpsf) != 0:
                        localpsf /= np.nansum(localpsf)

                        if cut.shape == localpsf.shape:
                            r = np.corrcoef(cut.flatten(), localpsf.flatten())
                            r = r[0,1]
                            cors += [r]
                            diff += [np.nansum(abs(cut-localpsf))]
                        else:
                            cors += [0]
                            diff += [2]
                            xcentroids += [x]
                            ycentroids += [y]
                    else:
                        cors += [0]
                        diff += [2]
                        xcentroids += [x]
                        ycentroids += [y]
                else:
                    cors += [0]
                    diff += [2]
                    xcentroids += [x]
                    ycentroids += [y]
            else:
                cors += [0]
                diff += [2]
                xcentroids += [x]
                ycentroids += [y]
        except:
            cors += [0]
            diff += [2]
            xcentroids += [x]
            ycentroids += [y]

    cors = np.array(cors)
    cors = np.round(cors,2)
    diff = np.array(diff)
    ind = (cors >= corlim) & (diff < psfdifflim)

    return ind,cors,diff

def _spatial_group(result,distance=0.5):
    """
    Groups events based on proximity.
    """

    d = np.sqrt((result.xcentroid.values[:,np.newaxis] - result.xcentroid.values[np.newaxis,:])**2+ 
               (result.ycentroid.values[:,np.newaxis] - result.ycentroid.values[np.newaxis,:])**2)

    indo = d < distance
    positions = np.unique(indo,axis=1)
    counter = 1

    obj = np.zeros(result.shape[0])
    for i in range(positions.shape[1]):
        obj[positions[:,i]] = counter 
        counter += 1
    result['objid'] = obj
    result['objid'] = result['objid'].astype(int)
    return result

def _star_finding_procedure(data,prf):

    mean, med, std = sigma_clipped_stats(data, sigma=5.0)

    psfCentre = prf.locate(5,5,(11,11))
    finder = StarFinder(med + 5*std,kernel=psfCentre)
    res1 = finder.find_stars(deepcopy(data))

    psfUR = prf.locate(5.25,5.25,(11,11))
    finder = StarFinder(med + 5*std,kernel=psfUR)
    res2 = None#finder.find_stars(deepcopy(data))

    psfUL = prf.locate(4.75,5.25,(11,11))
    finder = StarFinder(med + 5*std,kernel=psfUL)
    res3 = None#finder.find_stars(deepcopy(data))

    psfDR = prf.locate(5.25,4.75,(11,11))
    finder = StarFinder(med + 5*std,kernel=psfDR)
    res4 = None#finder.find_stars(deepcopy(data))

    psfDL = prf.locate(4.75,4.75,(11,11))
    finder = StarFinder(med + 5*std,kernel=psfDL)
    res5 = None#finder.find_stars(deepcopy(data))

    tables = [res1, res2, res3, res4, res5]
    good_tables = [table.to_pandas() for table in tables if table is not None]
    total = pd.concat(good_tables)
    grouped = _spatial_group(total,distance=2)
    res = grouped.groupby('objid').head(1)
    res = res.reset_index(drop=True)
    res = res.drop(['id','objid'],axis=1)

    return res

def _frame_correlation(data,prf,corlim,psfdifflim,frameNum):
    """
    Acts on a frame of data. Uses StarFinder to find bright sources, then on each source peform correlation check.
    """

    if np.nansum(data) != 0.0:
        t1 = t()
        res = _star_finding_procedure(data,prf)
        #print(f'StarFinding: {(t()-t1):.1f} sec')
        if res is not None:
            res['frame'] = frameNum
            t2 = t()    
            ind, cors,diff = _correlation_check(res,data,prf,corlim=corlim,psfdifflim=psfdifflim)
            #print(f'Correlation Check: {(t()-t2):.1f} sec - {len(res)} events')
            res['psflike'] = cors
            res['psfdiff'] = diff
            res = res[ind]
            return res
        else:
            return None
    else:
        return None
    
def _source_mask(res,mask):

    xInts = res['xint'].values
    yInts = res['yint'].values

    newmask = deepcopy(mask)

    c1 = newmask.shape[0]//2
    c2 = newmask.shape[1]//2

    newmask[c1-2:c1+3,c2-2:c2+3] -= 1
    newmask[(newmask>3)&(newmask<7)] -= 4
    maskResult = np.array([newmask[yInts[i],xInts[i]] for i in range(len(xInts))])

    for id in res['objid'].values:
        index = (res['objid'] == id).values
        subset = maskResult[index]
        maxMask = np.nanmax(subset)
        maskResult[index] = maxMask

    res['source_mask'] = maskResult

    return res

def _count_detections(result):

    ids = result['objid'].values
    unique = np.unique(ids, return_counts=True)
    unique = list(zip(unique[0],unique[1]))

    array = np.zeros_like(ids)

    for id,count in unique:
        index = (result['objid'] == id).values
        array[index] = count

    result['n_detections'] = array

    return result

def detect(flux,cam,ccd,sector,column,row,mask,inputNums=None,corlim=0.8,psfdifflim=0.5):
    """
    Main Function.
    """

    if inputNums is not None:
        flux = flux[inputNums]
        inputNum = inputNums[-1]
    else:
        inputNum = 0

    if sector < 4:
        prf = TESS_PRF(cam,ccd,sector,column,row,localdatadir='/fred/oz100/_local_TESS_PRFs/Sectors1_2_3')
    else:
        prf = TESS_PRF(cam,ccd,sector,column,row,localdatadir='/fred/oz100/_local_TESS_PRFs/Sectors4+')
        
    result = None

    length = np.linspace(0,flux.shape[0]-1,flux.shape[0]).astype(int)

    t1 = t()

    results = Parallel(n_jobs=int(multiprocessing.cpu_count()/2))(delayed(_frame_correlation)(flux[i],prf,corlim,psfdifflim,inputNum+i) for i in tqdm(length))
    #results = Parallel(n_jobs=1)(delayed(_frame_correlation)(flux[i],prf,corlim,psfdifflim,inputNum+i) for i in tqdm(length))

    print(f'Main Correlation: {(t()-t1):.1f} sec')

    frame = None

    t1 = t()

    for result in results:
        if frame is None:
            frame = result
        else:
            frame = pd.concat([frame,result])

    print(f'Concatenation: {(t()-t1):.1f} sec')

    
    frame['xint'] = deepcopy(np.round(frame['xcentroid'].values)).astype(int)
    frame['yint'] = deepcopy(np.round(frame['ycentroid'].values)).astype(int)
    data = flux[0]
    ind = (frame['xint'].values >3) & (frame['xint'].values < data.shape[1]-3) & (frame['yint'].values >3) & (frame['yint'].values < data.shape[0]-3)
    frame = frame[ind]

    t1 = t()
    frame = _spatial_group(frame,distance=1.5)
    print(f'Spatial Group: {(t()-t1):.1f} sec')

    t1 = t()
    frame = _source_mask(frame,mask)
    print(f'Source Mask: {(t()-t1):.1f} sec')

    t1 = t()
    frame = _count_detections(frame)
    print(f'Detection Count: {(t()-t1):.1f} sec')

    return frame

# -- Secondary Functions (Just for functionality in testing) -- #

class Detector():

    def __init__(self,sector,cam,ccd,data_path,n):

        self.sector = sector
        self.cam = cam
        self.ccd = ccd
        self.data_path = data_path
        self.n = n

        self.flux = None
        self.times = None
        self.mask = None
        self.results = None
        self.cut = None

        self.path = f'{self.data_path}/Sector{self.sector}/Cam{self.cam}/Ccd{self.ccd}'

    def _wcs_time_info(self,result,cut):
        
        cut_path = f'{self.path}/Cut{cut}of{self.n**2}/sector{self.sector}_cam{self.cam}_ccd{self.ccd}_cut{cut}_of{self.n**2}.fits'
        times = np.load(f'{self.path}/Cut{cut}of{self.n**2}/sector{self.sector}_cam{self.cam}_ccd{self.ccd}_cut{cut}_of{self.n**2}_Times.npy')

        tpf = lk.TessTargetPixelFile(cut_path)
        coords = tpf.wcs.all_pix2world(result['xcentroid'],result['ycentroid'],0)
        result['ra'] = coords[0]
        result['dec'] = coords[1]
        result['mjd'] = times[result['frame']]

        return result
    
    def _gather_data(self,cut):

        self.flux = np.load(f'{self.path}/Cut{cut}of{self.n**2}/sector{self.sector}_cam{self.cam}_ccd{self.ccd}_cut{cut}_of{self.n**2}_ReducedFlux.npy')
        self.mask = np.load(f'{self.path}/Cut{cut}of{self.n**2}/sector{self.sector}_cam{self.cam}_ccd{self.ccd}_cut{cut}_of{self.n**2}_Mask.npy')
        self.times = np.load(f'{self.path}/Cut{cut}of{self.n**2}/sector{self.sector}_cam{self.cam}_ccd{self.ccd}_cut{cut}_of{self.n**2}_Times.npy')

    def _gather_results(self,cut):

        self.result = pd.read_csv(f'{self.path}/Cut{cut}of{self.n**2}/detected_sources.csv')

    def source_detect(self,cut):

        if cut != self.cut:
            self._gather_data(cut)
            self.cut = cut

        processor = DataProcessor(sector=self.sector,path='/fred/oz100/TESSdata',verbose=2)
        _, cutCentrePx, _, _ = processor.find_cuts(cam=self.cam,ccd=self.ccd,n=self.n,plot=False)

        column = cutCentrePx[1-1][0]
        row = cutCentrePx[1-1][1]

        results = detect(self.flux,cam=self.cam,ccd=self.ccd,sector=self.sector,column=column,row=row,mask=self.mask,inputNums=None)
        results = self._wcs_time_info(results,cut)
        results.to_csv(f'{self.path}/Cut{cut}of{self.n**2}/detected_sources.csv')

    def plot_results(self,cut):

        if cut != self.cut:
            self._gather_data(cut)
            self._gather_results(cut)
            self.cut = cut

        fig,ax = plt.subplots(figsize=(12,6),ncols=2)
        ax[0].scatter(self.result['xcentroid'],self.result['ycentroid'],c=self.result['frame'],s=5)
        ax[0].imshow(self.flux[0],cmap='gray',origin='lower',vmin=-10,vmax=10)
        ax[0].set_xlabel(f'Frame 0')

        newmask = deepcopy(self.mask)

        c1 = newmask.shape[0]//2
        c2 = newmask.shape[1]//2

        newmask[c1-2:c1+3,c2-2:c2+3] -= 1

        ax[1].imshow(newmask,origin='lower')

        ax[1].scatter(self.result['xcentroid'],self.result['ycentroid'],c=self.result['source_mask'],s=5,cmap='Reds')
        ax[1].set_xlabel('Source Mask')

    def count_detections(self,cut,lower=None,upper=None):

        if cut != self.cut:
            self._gather_data(cut)
            self._gather_results(cut)
            self.cut = cut

        array = self.result['objid'].values
        id,count = np.unique(array, return_counts=True)
        dictionary = dict(zip(id, count))

        if upper is not None:
            if lower is not None:
                dictionary = dict((k, v) for k, v in dictionary.items() if lower < v < upper)
            else:
                dictionary = dict((k, v) for k, v in dictionary.items() if v < upper)
        elif lower is not None:
            dictionary = dict((k, v) for k, v in dictionary.items() if lower < v)

        return dictionary

    def plot_source(self,cut,id):

        if cut != self.cut:
            self._gather_data(cut)
            self._gather_results(cut)
            self.cut = cut

        source = self.result[self.result['objid']==id]

        x = source.iloc[0]['xint'].astype(int)
        y = source.iloc[0]['yint'].astype(int)

        frames = source['frame'].values
        brightestframe = np.where(self.flux==np.nanmax(self.flux[frames,y-1:y+2,x-1:x+2]))[0][0]

        fig,ax = plt.subplot_mosaic([[0,0,0,2,2],[1,1,1,3,3]],figsize=(10,7))

        frameStart = min(source['frame'].values)
        frameEnd = max(source['frame'].values)

        f = np.sum(self.flux[:,y-2:y+3,x-2:x+3],axis=(2,1))
        ax[0].plot(f)
        ax[1].plot(f[frameStart-10:frameEnd+20])

        ax[0].axvline(frameStart,color='r',linestyle='--',alpha=0.2)
        ax[0].axvline(frameEnd,color='r',linestyle='--',alpha=0.2) 
        
        ax[2].plot(source['xcentroid'],source['ycentroid'],'C1.')
        ax[2].imshow(self.flux[brightestframe],cmap='gray',origin='lower',vmin=-10,vmax=10)
        ax[2].set_xlabel(f'Frame {brightestframe}')
        
        vmax = np.max(self.flux[brightestframe,y-2:y+3,x-2:x+3])/2
        im = ax[3].imshow(self.flux[brightestframe,y-2:y+3,x-2:x+3],cmap='grey',vmin=-10,vmax=vmax)
        plt.colorbar(im)
        ax[3].set_xlabel(f'Object {id}')

        return source

    def locate_transient(self,cut,xcentroid,ycentroid,threshold=3):

        if cut != self.cut:
            self._gather_data(cut)
            self._gather_results(cut)
            self.cut = cut

        return self.result[(self.result['ycentroid'].values < ycentroid+threshold) & (self.result['ycentroid'].values > ycentroid-threshold) & (self.result['xcentroid'].values < xcentroid+threshold) & (self.result['xcentroid'].values > xcentroid-threshold)]

    def full_ccd(self):

        p = DataProcessor(sector=self.sector,path=self.data_path)
        lb,_,_,_ = p.find_cuts(cam=self.cam,ccd=self.ccd,n=self.n,plot=False)

        cutCorners, cutCentrePx, cutCentreCoords, cutSize = p.find_cuts(cam=self.cam,ccd=self.ccd,n=self.n,plot=False)

        fig,ax = plt.subplots(figsize=(10,10))
        ax.set_xlim(44,2076)
        ax.set_ylim(0,2048)

        # -- Adds cuts -- #
        #colours = iter(plt.cm.rainbow(np.linspace(0, 1, self.n**2)))
        for corner in cutCorners:
            #c = next(colours)
            c='black'
            rectangle = patches.Rectangle(corner,2*cutSize,2*cutSize,edgecolor=c,
                                            facecolor='none',alpha=1)
            ax.add_patch(rectangle)

        for cut in range(1,17):
            try:
                path = f'{self.data_path}/Sector{self.sector}/Cam{self.cam}/Ccd{self.ccd}/Cut{cut}of{self.n**2}'
                r = pd.read_csv(f'{path}/detected_sources.csv')
                r['xcentroid'] += lb[cut-1][0]
                r['ycentroid'] += lb[cut-1][1]
                ax.scatter(r['xcentroid'],r['ycentroid'],c=r['frame'],s=5)
            except:
                pass
