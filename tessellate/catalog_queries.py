import pandas as pd
from astropy.coordinates import SkyCoord
import astropy.units as u
from astroquery.vizier import Vizier
import numpy as np
 
 
def cross_match(obs_cat, viz_cat,tol=30):
    dist = np.sqrt((obs_cat.ra[:,np.newaxis] - viz_cat.ra[np.newaxis,:])**2 + (obs_cat.dec[:,np.newaxis] - viz_cat.dec[np.newaxis,:])**2)
    closest = np.argmin(dist,axis=1)
    valid = np.nanmin(dist,axis=1) < tol/60**2
    matched = closest[valid]

    obs_cat['Type'] = 'none'
    obs_cat['Prob'] = 0
    obs_cat['Type'].iloc[valid] = viz_cat['Type'].iloc[matched]
    obs_cat['Prob'].iloc[valid] = viz_cat['Prob'].iloc[matched]
    return obs_cat
 
def get_catalog(catalog, centre, width, height):
    coords = SkyCoord(ra=centre[0]*u.deg,
                      dec=centre[1]*u.deg)
    v = Vizier(row_limit=-1)
    t_result = v.query_region(coords, width=width*u.deg,
                              height=height*u.deg,
                              catalog=catalog)
 
    if len(t_result) > 0:
        return t_result[catalog].to_pandas()
    else:
        return None
 
def find_variables(coords,viz_cat,width,height):
    # Fetch and save catalogs from Vizier.
    # Gaia Variable Catalog
    varisum = get_catalog("I/358/varisum",coords,width,height)
    # ASASN Variable Catalog
    asasn = get_catalog("II/366/catalog",coords,width,height)
    # DES RRLyrae Catalog
    des_var = get_catalog("J/AJ/158/16/table11",coords,width,height)
    if (varisum is None) & (asasn is None) & (des_var is None):
        print("No known variables found ...")
        varcat = [0]
    else:
        if varisum is not None:
            varisum['Type'] = 'None'
            varisum['Prob'] = 0
            keys = list(varisum.keys())[12:-2]
            for key in keys:
                t = varisum[key].values > 0
                varisum['Type'].iloc[t] = key
                varisum['Prob'].iloc[t] = varisum[key].values[t]
            varisum = varisum.rename(columns={'RA_ICRS': 'ra',
                                          'DE_ICRS': 'dec'})
            varisum = pd.DataFrame(varisum, columns=['ra','dec','Type','Prob'])
 
        if asasn is not None:
            asasn = asasn.rename(columns={'RAJ2000': 'ra',
                                      'DEJ2000': 'dec'})
            asasn = pd.DataFrame(asasn, columns=['ra','dec','Type','Prob'])
        else:
            asasn = None
 
        if des_var is not None:
            des_var = des_var.rename(columns={'RAJ2000': 'ra',
                                          'DEJ2000': 'dec'})
            des_var = pd.DataFrame(des_var, columns=['ra','dec'])
        else:
            des_var = None
 
        variables = pd.concat([varisum, asasn, des_var])
    
    obs = cross_match(viz_cat, variables)
    return obs