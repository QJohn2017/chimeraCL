from numpy import uint32, double, complex128
from numpy import arange, pi, eye, sqrt
from numpy.fft import fftfreq
from numpy.linalg import pinv as inv
from scipy.special import jn_zeros, jn, j1

from chimeraCL.methods.grid_methods_cl import GridMethodsCL


class Grid(GridMethodsCL):
    def __init__(self, configs_in, comm):
        if comm is not None:
            self.comm = comm
            self.queue = comm.queue
            self.ctx = comm.ctx
            self.thr = comm.thr
            self.dev_type = comm.dev_type
            self.plat_name = comm.plat_name

        self._process_configs(configs_in)
        self._make_spectral_axes()
        self._make_DHT()
        self._send_grid_to_dev()
        self._compile_methods()

    def depose_charge(self, species=[]):
        for m in range(self.Args['M']+1):
            self.set_to_zero(self.DataDev['rho_m'+str(m)])

        for parts in species:
            self.depose_scalar(parts, 'w', 'rho')

    def depose_currents(self, species=[]):
        for m in range(self.Args['M']+1):
            for arg in ['Jx', 'Jy', 'Jz']:
                self.set_to_zero(self.DataDev[arg+'_m'+str(m)])
        for parts in species:
            self.depose_vector(parts, ['px', 'py', 'pz'],
                               ['g_inv', 'w'], 'J')

    def project_fields(self, species=[]):
        for parts in species:
            for arg in ['Ex', 'Ey', 'Ez', 'Bx', 'By', 'Bz']:
                self.set_to_zero(parts.DataDev[arg])

            self.project_vec6(parts, ['E', 'B'], ['E', 'B'])

    def fb_transform(self, comps=[], dir=0):
        for comp in comps:
            self.transform_field(comp, dir=dir)

    def _process_configs(self, configs_in):
        self.Args = configs_in

        if 'M' not in self.Args:
            self.Args['M'] = 0

        self.Args['dx'] = (self.Args['Xmax']-self.Args['Xmin']) / \
                          (self.Args['Nx']-1)
        self.Args['dx_inv'] = 1./self.Args['dx']

        self.Args['dr'] = self.Args['Rmax'] / (self.Args['Nr']-1.5)
        self.Args['dr_inv'] = 1./self.Args['dr']

        if 'dt' not in self.Args:
            self.Args['dt'] = self.Args['dx']
        self.Args['dt_inv'] = 1.0/self.Args['dt']

        self.Args['Xgrid'] = self.Args['Xmin'] + self.Args['dx'] * \
            arange(self.Args['Nx'])

        self.Args['Rmin'] = -0.5*self.Args['dr']
        self.Args['Rgrid'] = self.Args['Rmin'] + self.Args['dr'] * \
            arange(self.Args['Nr'])
        self.Args['Rmax'] = self.Args['Rgrid'].max()

        self.Args['Rgrid_inv'] = (self.Args['Rgrid'] > 0) / self.Args['Rgrid']
        self.Args['R_period'] = self.Args['Rmax'] + self.Args['dr']

        self.Args['NxNr'] = self.Args['Nr'] * self.Args['Nx']
        self.Args['Nxm1Nrm1'] = (self.Args['Nr']-1) * (self.Args['Nx']-1)
        self.Args['NxNr_4'] = self.Args['Nr']//2 * self.Args['Nx']//2

    def _send_grid_to_dev(self):

        self.DataDev = {}

        args_int_imprt = ['Nx', 'Nr', 'NxNr', 'Nxm1Nrm1', 'NxNr_4']
        args_dbl_imprt = ['Xmin', 'Xmax', 'dx', 'dx_inv', 'Xgrid',
                          'Rmin', 'Rmax', 'dr', 'dr_inv', 'Rgrid',
                          'Rgrid_inv', 'kx_env',
                          'kx', 'kx0', 'dkx', 'dt']

        args_dbl_m_imprt = ['DHT', 'DHT_inv', 'dDHT_plus', 'dDHT_minus',
                            'kr', 'w']
        args_fld_init = ['rho', 'Jx', 'Jy', 'Jz',
                         'Ex', 'Ey', 'Ez', 'Bx', 'By', 'Bz']
        args_fld_aux_init = ['field_fb_aux1', 'field_fb_aux2']

        for arg in args_int_imprt:
            self.DataDev[arg] = self.dev_arr(self.Args[arg], dtype=uint32)

        for arg in args_dbl_imprt:
            self.DataDev[arg] = self.dev_arr(self.Args[arg], dtype=double)

        for arg in args_dbl_m_imprt:
            arg += '_m'
            for m in range(self.Args['M']+1):
                self.DataDev[arg+str(m)] = self.dev_arr(self.Args[arg+str(m)])

        for arg in args_fld_init:
            arg += '_m'
            self.DataDev[arg+'0'] = self.dev_arr(val=0, dtype=double,
                                                 shape=(self.Args['Nr'],
                                                        self.Args['Nx']))

            for m in range(1, self.Args['M']+1):
                self.DataDev[arg+str(m)] = self.dev_arr(val=0,
                    dtype=complex128, shape=(self.Args['Nr'], self.Args['Nx']))

        for arg in args_fld_init:
            arg += '_fb_m'
            for m in range(self.Args['M']+1):
                self.DataDev[arg+str(m)] = self.dev_arr(val=0,
                  dtype=complex128, shape=(self.Args['Nr']-1,
                                           self.Args['Nx']))

        for arg in args_fld_aux_init:
            arg += '_dbl'
            self.DataDev[arg] = self.dev_arr(val=0,
              dtype=double, shape=(self.Args['Nr']-1,self.Args['Nx']))

        for arg in args_fld_aux_init:
            arg += '_clx'
            self.DataDev[arg] = self.dev_arr(val=0, dtype=complex128,
              shape=(self.Args['Nr']-1,self.Args['Nx']))

    def _make_spectral_axes(self):
        if 'KxShift' in self.Args:
            self.Args['kx0'] = 2*pi*self.Args['KxShift']
        else:
            self.Args['kx0'] = 0.0

        self.Args['kx_env'] = 2*pi*fftfreq(self.Args['Nx']-1, self.Args['dx'])
        self.Args['kx'] = self.Args['kx0'] + self.Args['kx_env']
        self.Args['dkx'] = (self.Args['kx'][1]-self.Args['kx'][0]) / (2*pi)

        for m in range(self.Args['M']+2):
            self.Args['kr_m'+str(m)] = jn_zeros(m, self.Args['Nr']-1) / \
                                                   self.Args['R_period']
        for m in range(self.Args['M']+1):
            self.Args['w_m'+str(m)] = sqrt(self.Args['kx'][None,:]**2 + \
                                           self.Args['kr_m'+str(m)][:,None]**2)

    def _make_DHT(self):
        for m in range(self.Args['M']+1):
            self.Args['DHT_inv_m'+str(m)] = jn(m, \
                self.Args['Rgrid'][1:,None] * self.Args['kr_m'+str(m)][None,:])
            self.Args['DHT_m'+str(m)] = inv(self.Args['DHT_inv_m'+str(m)])

            self.Args['dDHT_plus_m'+str(m)] = eye(self.Args['Nr']-1)
            self.Args['dDHT_minus_m'+str(m)] = eye(self.Args['Nr']-1)
    
