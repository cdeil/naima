# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import numpy as np
import astropy.units as u
from astropy.extern import six
from astropy import log
import warnings
from .extern.validator import validate_array, validate_scalar

__all__ = ["generate_energy_edges", "sed_conversion",
           "build_data_table", "generate_diagnostic_plots"]

# Input validation tools


def validate_column(data_table, key, pt, domain='positive'):
    try:
        column = data_table[key]
        array = validate_array(key, u.Quantity(column, unit=column.unit),
                               physical_type=pt, domain=domain)
    except KeyError as e:
        raise TypeError(
            'Data table does not contain required column "{0}"'.format(key))

    return array


def validate_data_table(data_table):

    data = {}

    flux_types = ['flux', 'differential flux', 'power', 'differential power']

    # Energy and flux arrays
    data['energy'] = validate_column(data_table, 'energy', 'energy')
    data['flux'] = validate_column(data_table, 'flux', flux_types)

    # Flux uncertainties
    if 'flux_error' in data_table.keys():
        dflux = validate_column(data_table, 'flux_error', flux_types)
        data['dflux'] = u.Quantity((dflux, dflux))
    elif 'flux_error_lo' in data_table.keys() and 'flux_error_hi' in data_table.keys():
        data['dflux'] = u.Quantity((
            validate_column(data_table, 'flux_error_lo', flux_types),
            validate_column(data_table, 'flux_error_hi', flux_types)))
    else:
        raise TypeError('Data table does not contain required column'
                        ' "flux_error" or columns "flux_error_lo" and "flux_error_hi"')

    # Energy bin edges
    if 'ene_width' in data_table.keys():
        ene_width = validate_column(data_table, 'ene_width', 'energy')
        data['dene'] = u.Quantity((ene_width / 2., ene_width / 2.))
    elif 'ene_lo' in data_table.keys() and 'ene_hi' in data_table.keys():
        ene_lo = validate_column(data_table, 'ene_lo', 'energy')
        ene_hi = validate_column(data_table, 'ene_hi', 'energy')
        data['dene'] = u.Quantity(
            (data['energy'] - ene_lo, ene_hi - data['energy']))
    else:
        data['dene'] = generate_energy_edges(data['energy'])

    # Upper limit flags
    if 'ul' in data_table.keys():
        # Check if it is a integer or boolean flag
        ul_col = data_table['ul']
        if ul_col.dtype.type is np.int_ or ul_col.dtype.type is np.bool_:
            data['ul'] = np.array(ul_col, dtype=np.bool)
        elif ul_col.dtype.type is np.str_:
            strbool = True
            for ul in ul_col:
                if ul != 'True' and ul != 'False':
                    strbool = False
            if strbool:
                data['ul'] = np.array((eval(ul)
                                      for ul in ul_col), dtype=np.bool)
            else:
                raise TypeError('UL column is in wrong format')
        else:
            raise TypeError('UL column is in wrong format')
    else:
        data['ul'] = np.array([False, ] * len(data['energy']))

    HAS_CL = False
    if 'keywords' in data_table.meta.keys():
        if 'cl' in data_table.meta['keywords'].keys():
            HAS_CL = True
            data['cl'] = validate_scalar(
                'cl', data_table.meta['keywords']['cl']['value'])

    if not HAS_CL:
        data['cl'] = 0.9
        if 'ul' in data_table.keys():
            log.warning('"cl" keyword not provided in input data table, upper limits'
                        'will be assumed to be at 90% confidence level')

    return data


# Convenience tools

def sed_conversion(energy, model_unit, sed):
    """
    Manage conversion between differential spectrum and SED
    """

    model_pt = model_unit.physical_type

    ones = np.ones(energy.shape)

    if sed:
        # SED
        f_unit = u.Unit('erg/s')
        if model_pt == 'power' or model_pt == 'flux' or model_pt == 'energy':
            sedf = ones
        elif 'differential' in model_pt:
            sedf = (energy ** 2)
        else:
            raise u.UnitsError(
                'Model physical type ({0}) is not supported'.format(model_pt),
                'Supported physical types are: power, flux, differential'
                ' power, differential flux')

        if 'flux' in model_pt:
            f_unit /= u.cm ** 2
        elif 'energy' in model_pt:
            # particle energy distributions
            f_unit = u.erg

    elif sed is None:
        # Use original units
        f_unit = model_unit
        sedf = ones
    else:
        # Differential spectrum
        f_unit = u.Unit('1/(s TeV)')
        if 'differential' in model_pt:
            sedf = ones
        elif model_pt == 'power' or model_pt == 'flux' or model_pt == 'energy':
            # From SED to differential
            sedf = 1 / (energy ** 2)
        else:
            raise u.UnitsError(
                'Model physical type ({0}) is not supported'.format(model_pt),
                'Supported physical types are: power, flux, differential'
                ' power, differential flux')

        if 'flux' in model_pt:
            f_unit /= u.cm ** 2
        elif 'energy' in model_pt:
            # particle energy distributions
            f_unit = u.Unit('1/TeV')

    log.debug(
        'Converted from {0} ({1}) into {2} ({3}) for sed={4}'.format(model_unit, model_pt,
                                                                     f_unit, f_unit.physical_type, sed))

    return f_unit, sedf


def trapz_loglog(y, x, axis=-1, intervals=False):
    """
    Integrate along the given axis using the composite trapezoidal rule in
    loglog space.

    Integrate `y` (`x`) along given axis in loglog space.

    Parameters
    ----------
    y : array_like
        Input array to integrate.
    x : array_like, optional
        Independent variable to integrate over.
    axis : int, optional
        Specify the axis.

    Returns
    -------
    trapz : float
        Definite integral as approximated by trapezoidal rule in loglog space.
    """
    try:
        y_unit = y.unit
        y = y.value
    except AttributeError:
        y_unit = 1.
    try:
        x_unit = x.unit
        x = x.value
    except AttributeError:
        x_unit = 1.

    y = np.asanyarray(y)
    x = np.asanyarray(x)

    slice1 = [slice(None)] * y.ndim
    slice2 = [slice(None)] * y.ndim
    slice1[axis] = slice(None, -1)
    slice2[axis] = slice(1, None)

    if x.ndim == 1:
        shape = [1] * y.ndim
        shape[axis] = x.shape[0]
        x = x.reshape(shape)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # Compute the power law indices in each integration bin
        b = np.log10(y[slice2] / y[slice1]) / np.log10(x[slice2] / x[slice1])

        # if local powerlaw index is -1, use \int 1/x = log(x); otherwise use normal
        # powerlaw integration
        trapzs = np.where(np.abs(b+1.) > 1e-10,
                  (y[slice1] * (x[slice2] * (x[slice2]/x[slice1]) ** b - x[slice1]))/(b+1),
                  x[slice1] * y[slice1] * np.log(x[slice2]/x[slice1]))

    tozero = (y[slice1] == 0.) + (y[slice2] == 0.) + (x[slice1] == x[slice2])
    trapzs[tozero] = 0.

    if intervals:
        return trapzs * x_unit * y_unit

    ret = np.add.reduce(trapzs, axis) * x_unit * y_unit

    return ret


def generate_energy_edges(ene):
    """Generate energy bin edges from given energy array.

    Generate an array of energy edges from given energy array to be used as
    abcissa error bar limits when no energy uncertainty or energy band is
    provided.

    Parameters
    ----------
    ene : `astropy.units.Quantity` array instance
        1-D array of energies with associated phsyical units.

    Returns
    -------
    edge_array : `astropy.units.Quantity` array instance of shape ``(2,len(ene))``
        Array of energy edge pairs corresponding to each given energy of the
        input array.
    """
    midene = np.sqrt((ene[1:] * ene[:-1]))
    elo, ehi = np.zeros(len(ene)) * ene.unit, np.zeros(len(ene)) * ene.unit
    elo[1:] = ene[1:] - midene
    ehi[:-1] = midene - ene[:-1]
    elo[0] = ene[0] * ( 1 - ene[0] / (ene[0] + ehi[0]))
    ehi[-1] = elo[-1]
    return np.array((elo, ehi)) * ene.unit


def build_data_table(energy, flux, flux_error=None, flux_error_lo=None,
                     flux_error_hi=None, ene_width=None, ene_lo=None, ene_hi=None, ul=None,
                     cl=None):
    """
    Read data into data dict.

    Parameters
    ----------

    energy : :class:`~astropy.units.Quantity` array instance
        Observed photon energy array [physical type ``energy``]

    flux : :class:`~astropy.units.Quantity` array instance
        Observed flux array [physical type ``flux`` or ``differential flux``]

    flux_error, flux_error_hi, flux_error_lo : :class:`~astropy.units.Quantity` array instance
        68% CL gaussian uncertainty of the flux [physical type ``flux`` or
        ``differential flux``]. Either ``flux_error`` (symmetrical uncertainty) or
        ``flux_error_hi`` and ``flux_error_lo`` (asymmetrical uncertainties) must be
        provided.

    ene_width, ene_lo, ene_hi : :class:`~astropy.units.Quantity` array instance, optional
        Width of the energy bins [physical type ``energy``]. Either ``ene_width``
        (bin width) or ``ene_lo`` and ``ene_hi`` (Energies of the lower and upper
        bin edges) can be provided. If none are provided,
        ``generate_energy_edges`` will be used.

    ul : boolean or int array, optional
        Boolean array indicating which of the flux values given in ``flux``
        correspond to upper limits.

    cl : float, optional
        Confidence level of the flux upper limits given by ``ul``.

    Returns
    -------
    data : dict
        Data stored in a `dict`.
    """

    from astropy.table import Table, Column

    table = Table()

    if cl is not None:
        cl = validate_scalar('cl', cl)
        table.meta['keywords'] = {'cl': {'value': cl}}

    table.add_column(Column(name='energy', data=energy))

    if ene_width is not None:
        table.add_column(Column(name='ene_width', data=ene_width))
    elif ene_lo is not None and ene_hi is not None:
        table.add_column(Column(name='ene_lo', data=ene_lo))
        table.add_column(Column(name='ene_hi', data=ene_hi))

    table.add_column(Column(name='flux', data=flux))

    if flux_error is not None:
        table.add_column(Column(name='flux_error', data=flux_error))
    elif flux_error_lo is not None and flux_error_hi is not None:
        table.add_column(Column(name='flux_error_lo', data=flux_error_lo))
        table.add_column(Column(name='flux_error_hi', data=flux_error_hi))
    else:
        raise TypeError('Flux error not provided!')

    if ul is not None:
        ul = np.array(ul, dtype=np.int)
        table.add_column(Column(name='ul', data=ul))

    table.meta['comments'] = [
        'Table generated with naima.build_data_table', ]

    # test table units, format, etc
    data = validate_data_table(table)

    return table


def generate_diagnostic_plots(outname, sampler, modelidxs=None, pdf=False, sed=None, **kwargs):
    """
    Generate diagnostic plots.

    - A corner plot of sample density in the two dimensional parameter space of
      all parameter pairs of the run: ``outname_corner.png``
    - A plot for each of the chain parameters showing walker progression, final
      sample distribution and several statistical measures of this distribution:
      ``outname_chain_parN.png``
    - A plot for each of the models returned as blobs by the model function. The
      maximum likelihood model is shown, as well as the 1 and 3 sigma confidence
      level contours. The first model will be compared with observational data
      and residuals shown. ``outname_fit_modelN.png``

    Parameters
    ----------
    outname : str
        Name to be used to save diagnostic plot files.

    sampler : `emcee.EnsembleSampler` instance
        Sampler instance from which chains, blobs and data are read.

    modelidxs : iterable (optional)
        Model numbers to be plotted. Default: All returned in sampler.blobs

    pdf : bool (optional)
        Whether to save plots to multipage pdf.
    """

    from .plot import plot_chain, plot_blob

    if pdf:
        from matplotlib import pyplot as plt
        plt.rc('pdf', fonttype=42)
        print(
            'Generating diagnostic plots in file {0}_plots.pdf'.format(outname))
        from matplotlib.backends.backend_pdf import PdfPages
        outpdf = PdfPages('{0}_plots.pdf'.format(outname))

    # Chains

    for par, label in zip(six.moves.range(sampler.chain.shape[-1]), sampler.labels):
        try:
            log.info('Plotting chain of parameter {0}...'.format(label))
            f = plot_chain(sampler, par, **kwargs)
            if pdf:
                f.savefig(outpdf, format='pdf')
            else:
                if 'log(' in label or 'log10(' in label:
                    label = label.split('(')[-1].split(')')[0]
                f.savefig('{0}_chain_{1}.png'.format(outname, label))
            del f
        except Exception as e:
            log.warning('plot_chain failed for paramter {0} ({1}): {2}'.format(label,par,e))

    # Corner plot

    try:
        from triangle import corner
        from .plot import find_ML

        log.info('Plotting corner plot...')

        ML, MLp, MLvar, model_ML = find_ML(sampler, 0)
        f = corner(sampler.flatchain, labels=sampler.labels,
                   truths=MLp, quantiles=[0.16, 0.5, 0.84],
                   verbose=False, **kwargs)
        if pdf:
            f.savefig(outpdf, format='pdf')
        else:
            f.savefig('{0}_corner.png'.format(outname))
        del f
    except ImportError:
        print('triangle_plot not installed, corner plot not available')

    # Fit

    if modelidxs is None:
        nmodels = len(sampler.blobs[-1][0])
        modelidxs = list(range(nmodels))

    if sed is None:
        sed = [None for idx in modelidxs]
    elif isinstance(sed, bool):
        sed = [sed for idx in modelidxs]

    for modelidx, plot_sed in zip(modelidxs, sed):

        try:
            log.info('Plotting model output {0}...'.format(modelidx))
            f = plot_blob(sampler, blobidx=modelidx, label='Model output {0}'.format(modelidx),
                          sed=plot_sed, n_samples=100, **kwargs)
            if pdf:
                f.savefig(outpdf, format='pdf')
            else:
                f.savefig('{0}_model{1}.png'.format(outname, modelidx))
            del f
        except Exception as e:
            log.warning('plot_blob failed for model output {0}: {1}'.format(par,e))

    if pdf:
        outpdf.close()
