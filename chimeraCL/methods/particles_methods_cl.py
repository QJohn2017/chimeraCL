import numpy as np

from pyopencl.clrandom import ThreefryGenerator
from pyopencl.array import arange, cumsum, to_device
from pyopencl import enqueue_marker, enqueue_barrier
from pyopencl import Program
from pyopencl.clmath import sqrt as sqrt

from .generic_methods_cl import GenericMethodsCL
from .generic_methods_cl import compiler_options

from chimeraCL import __path__ as src_path
src_path = src_path[0] + '/kernels/'


class ParticleMethodsCL(GenericMethodsCL):

    def init_particle_methods(self):
        self.init_generic_methods()
        self.set_global_working_group_size()

        self._generator_knl = ThreefryGenerator(context=self.ctx)

        particles_sources = ''.join(
                open(src_path + "particles_generic.cl").readlines())

        particles_sources = self.block_def_str + particles_sources

        prg = Program(self.ctx, particles_sources).\
            build(options=compiler_options)

        self._data_align_dbl_knl = prg.data_align_dbl
        self._data_align_int_knl = prg.data_align_int
        self._index_and_sum_knl = prg.index_and_sum_in_cell
        self._sort_knl = prg.sort
        self._push_xyz_knl = prg.push_xyz
        self._fill_grid_knl = prg.fill_grid
        self._profile_by_interpolant_knl = prg.profile_by_interpolant

    def add_new_particles(self, source=None):

        if source is None:
            DataSrc = self.DataDev
        else:
            DataSrc = source.DataDev

        old_Np = self.DataDev['x'].size
        new_Np = DataSrc['x_new'].size
        full_Np = old_Np + new_Np

        if 'Immobile' not in self.Args.keys():
            args_strs = ['x', 'y', 'z', 'px', 'py', 'pz', 'w', 'g_inv']
        else:
            args_strs = ['x', 'y', 'z','w']

        for arg in args_strs:
            buff = self.dev_arr(dtype=self.DataDev[arg].dtype,
                                shape=full_Np)
            buff[:old_Np] = self.DataDev[arg]
            buff[old_Np:] = DataSrc[arg+'_new']
            self.DataDev[arg] = buff

        self.reset_num_parts()
        self.flag_sorted = False

    def make_new_domain(self, parts_in, density_profiles=None):

        xmin, xmax, rmin, rmax = \
          [parts_in[arg] for arg in ['Xmin', 'Xmax', 'Rmin', 'Rmax']]
        Nx_loc = int( np.ceil((xmax-xmin) / self.Args['dx']) + 1)
        Nr_loc = int( np.round((rmax-rmin) / self.Args['dr']) + 1)
        Xgrid_loc = self.dev_arr(val=(xmin+self.Args['dx']*np.arange(Nx_loc)))
        Rgrid_loc = self.dev_arr(val=(rmin+self.Args['dr']*np.arange(Nr_loc)))

        self.Args['right_lim'] = Xgrid_loc[-1].get()

        Ncells_loc = (Nx_loc-1)*(Nr_loc-1)
        Np = int(Ncells_loc*np.prod(self.Args['Nppc']))

        gn_strs = ['x', 'y', 'z', 'w']
        for arg in gn_strs:
            self.DataDev[arg+'_new'] = self.dev_arr(shape=Np,
                dtype=np.double)

        theta_variator = self.dev_arr(shape=Ncells_loc,
            dtype=np.double)
        self._fill_arr_rand(theta_variator, xmin=0, xmax=2*np.pi)

        gn_args = [self.DataDev[arg+'_new'].data for arg in gn_strs]
        gn_args += [theta_variator.data, ]
        gn_args += [Xgrid_loc.data, Rgrid_loc.data,
                    np.uint32(Nx_loc), np.uint32(Ncells_loc)]
        gn_args += list(np.array(self.Args['Nppc'], dtype=np.uint32))

        WGS, WGS_tot = self.get_wgs(Ncells_loc)
        self._fill_grid_knl(self.queue, (WGS_tot, ), (WGS, ), *gn_args).wait()

        self.DataDev['w_new'] *= self.Args['w0']

        if density_profiles is not None:
            for profile in density_profiles:
                if profile['coord'] == 'x':
                    xmin, xmax = parts_in['Xmin'], parts_in['Xmax']
                else:
                    print('Only longitudinal profiling is implemented')
                    continue

                coord = profile['coord'] + '_new'
                x_prf = profile['points']
                f_prf = profile['values']
                self.dens_profile(x_prf, f_prf, xmin, xmax,
                                  coord=coord, weight='w_new')

        if 'Immobile' not in self.Args.keys():

            for arg in ['px', 'py', 'pz']:

                if 'd'+arg not in parts_in and arg+'_c' not in parts_in:
                    self.DataDev[arg+'_new'] = self.dev_arr(shape=Np, val=0,
                                                            dtype=np.double)
                    parts_in[arg+'_c'] = 0
                    parts_in['d'+arg] = 0
                else:
                    self.DataDev[arg+'_new'] = self.dev_arr(shape=Np,
                                                            dtype=np.double)

                    if arg+'_c' not in parts_in:
                        parts_in[arg+'_c'] = 0

                    if 'd'+arg in parts_in:
                        self._fill_arr_randn(self.DataDev[arg+'_new'],
                                             mu=parts_in[arg+'_c'],
                                             sigma=parts_in['d'+arg])
                    else:
                        parts_in['d'+arg] = 0
                        self.DataDev[arg+'_new'].fill(parts_in[arg+'_c'])

            momnt = np.sum([parts_in[key]**2 for key in ('px_c','py_c','pz_c',
                                                         'dpx','dpy','dpz',)])
            if (momnt != 0):
                self.DataDev['g_inv_new'] = 1./sqrt(
                    1 + self.DataDev['px_new']*self.DataDev['px_new']
                    + self.DataDev['py_new']*self.DataDev['py_new']
                    + self.DataDev['pz_new']*self.DataDev['pz_new'])
            else:
                self.DataDev['g_inv_new'] = self.dev_arr(shape=Np,val=1.0,
                    dtype=np.double)

    def make_new_beam(self, parts_in):
        Np = parts_in['Np']

        args_strs =  ['x', 'y', 'z', 'px', 'py', 'pz', 'w']
        for arg in args_strs:
            self.DataDev[arg+'_new'] = self.dev_arr(
                shape=Np, dtype=np.double)

        for arg in ['x', 'y', 'z']:
            self._fill_arr_randn(self.DataDev[arg+'_new'],
                                mu=parts_in[arg+'_c'],
                                sigma=parts_in['L'+arg])

        for arg in ['px', 'py', 'pz']:
            if arg+'_c' not in parts_in:
                parts_in[arg+'_c'] = 0
            if 'd'+arg not in parts_in:
                parts_in['d'+arg] = 0

            self._fill_arr_randn(self.DataDev[arg+'_new'],
                                mu=parts_in[arg+'_c'],
                                sigma=parts_in['d'+arg])

        self.DataDev['w_new'][:] = parts_in['FullCharge']/parts_in['Np']

        self.DataDev['g_inv_new'] = 1./sqrt(
            1 + self.DataDev['px_new']*self.DataDev['px_new']
            + self.DataDev['py_new']*self.DataDev['py_new']
            + self.DataDev['pz_new']*self.DataDev['pz_new'])

    def dens_profile(self, x_prf, f_prf, xmin, xmax, coord='x', weight='w'):

        x_prf = np.array(x_prf,dtype=np.double)
        f_prf = np.array(f_prf,dtype=np.double)

        i_start = (x_prf<xmin).sum()-1
        i_stop  = (x_prf<xmax).sum()+1

        x_loc = x_prf[i_start:i_stop]
        f_loc = f_prf[i_start:i_stop]
        dxm1_loc = 1./(x_loc[1:] - x_loc[:-1])

        Np = self.DataDev[coord].size
        Nx_loc = x_loc.size

        x_loc = self.dev_arr(val=x_loc)
        f_loc = self.dev_arr(val=f_loc)
        dxm1_loc = self.dev_arr(val=dxm1_loc)

        WGS, WGS_tot = self.get_wgs(Np)
        self._profile_by_interpolant_knl(self.queue, (WGS_tot, ), (WGS, ),
                                         self.DataDev[coord].data,
                                         self.DataDev[weight].data,
                                         np.uint32(Np), x_loc.data,
                                         f_loc.data, dxm1_loc.data,
                                         np.uint32(Nx_loc)).wait()

    def push_coords(self, mode='half'):
        if self.Args['Np']==0:
            return

        if 'Immobile' in self.Args.keys():
            return

        WGS, WGS_tot = self.get_wgs(self.Args['Np'])

        if mode=='half':
            which_dt = 'dt_2'
        else:
            which_dt = 'dt'

        args_strs =  ['x', 'y', 'z', 'px', 'py', 'pz', 'g_inv', which_dt, 'Np']
        args = [self.DataDev[arg].data for arg in args_strs]
        self._push_xyz_knl(self.queue, (WGS_tot, ), (WGS, ), *args).wait()
        self.flag_sorted = False

    def index_sort(self, grid):
        WGS, WGS_tot = self.get_wgs(self.Args['Np'])

        self.DataDev['indx_in_cell'] = self.dev_arr(dtype=np.uint32,
            shape=self.Args['Np'], allocator=self.DataDev['indx_in_cell_mp'])

        self.DataDev['sum_in_cell'] = self.dev_arr(val=0,
            dtype=np.uint32, shape=grid.Args['Nxm1Nrm1']+1,
            allocator=self.DataDev['sum_in_cell_mp'])

        part_strs =  ['x', 'y', 'z', 'sum_in_cell', 'Np']
        grid_strs =  ['Nx', 'Xmin', 'dx_inv',
                      'Nr', 'Rmin', 'dr_inv']

        args = [self.DataDev[arg].data for arg in part_strs] + \
               [self.DataDev['indx_in_cell'].data, ] + \
               [grid.DataDev[arg].data for arg in grid_strs]

        self._index_and_sum_knl(self.queue, (WGS_tot, ), (WGS, ), *args).wait()

        self.DataDev['cell_offset'] = self._cumsum(self.DataDev['sum_in_cell'],
            allocator=self.DataDev['cell_offset_mp'])

        self.set_to(self.DataDev['sum_in_cell'], 0)

        self.Args['Np_stay'] = self.DataDev['cell_offset'][-2].get().item()

        self.DataDev['sort_indx'] = self.dev_arr(dtype=np.uint32,
            shape=self.Args['Np'], allocator=self.DataDev['sort_indx_mp'])

        WGS, WGS_tot = self.get_wgs(self.Args['Np'])
        self._sort_knl(self.queue, (WGS_tot, ), (WGS, ),
                       self.DataDev['cell_offset'].data,
                       self.DataDev['indx_in_cell'].data,
                       self.DataDev['sum_in_cell'].data,
                       self.DataDev['sort_indx'].data,
                       np.uint32(self.Args['Np'])).wait()

    def align_and_damp(self, comps_align):
        if self.Args['Np_stay'] == 0:
            for comp in comps_align + ['sort_indx',]:
                self.DataDev[comp] = self.dev_arr(shape=0,
                    dtype=self.DataDev[comp].dtype)
            self.reset_num_parts()
            return

        WGS, WGS_tot = self.get_wgs(self.Args['Np_stay'])
        for comp in comps_align:
            buff_parts = self.dev_arr(dtype=self.DataDev[comp].dtype,
                                      shape=(self.Args['Np_stay'], ))

            self._data_align_dbl_knl(self.queue, (WGS_tot, ), (WGS, ),
                                     self.DataDev[comp].data,
                                     buff_parts.data,
                                     self.DataDev['sort_indx'].data,
                                     np.uint32(self.Args['Np_stay'])).wait()
            self.DataDev[comp] = buff_parts

        self.DataDev['sort_indx'] = arange(self.queue, 0,
                                           self.Args['Np_stay'], 1,
                                           dtype=np.uint32)
        self.reset_num_parts()

    def reset_num_parts(self, Np=None):
        if Np is None:
            Np = self.DataDev['x'].size
        self.DataDev['Np'].fill(Np)
        self.Args['Np'] = Np
        self.Args['Np_stay'] = Np

    def _fill_arr_randn(self, arr, mu=0, sigma=1):
        self._generator_knl.fill_normal(ary=arr, queue=self.queue,
                                        mu=mu, sigma=sigma).wait()

    def _fill_arr_rand(self, arr, xmin=0, xmax=1):
        self._generator_knl.fill_uniform(ary=arr, queue=self.queue,
                                         a=xmin,b=xmax).wait()

    def _cumsum(self, arr_in, allocator=None, output_dtype=np.uint32):
        evnt, arr_tmp = cumsum(arr_in, return_event=True,
                               queue=self.queue, output_dtype=output_dtype)
        arr_out = self.dev_arr(dtype=output_dtype, shape=arr_tmp.size+1,
                               allocator=allocator)
        arr_out[0] = 0
        evnt.wait()
        arr_out[1:] = arr_tmp[:]
        return arr_out

    def free_mp(self):
        for key in self.DataDev.keys():
            if key[-3:]=='_mp':
                self.DataDev[key].free_held()

    def free_added(self):
        if 'Immobile' not in self.Args.keys():
            args_strs = ['x', 'y', 'z', 'px', 'py', 'pz', 'w', 'g_inv']
        else:
            args_strs = ['x', 'y', 'z','w']

        for arg in args_strs:
            self.DataDev[arg+'_new'] = None
