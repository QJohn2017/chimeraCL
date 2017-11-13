// this is a source of particles kernels for chimeraCL project
#pragma OPENCL EXTENSION cl_khr_global_int32_base_atomics : enable
#pragma OPENCL EXTENSION cl_khr_fp64:enable

// Fill given grid with uniformly distributed particles
__kernel void fill_grid(
  __global double *x,
  __global double *y,
  __global double *z,
  __global double *w,
  __global double *theta_var,
  __global double *xgrid,
  __global double *rgrid,
           uint Nx,
           uint ncells,
           uint Nppc_x,
           uint Nppc_r,
           uint Nppc_th)
{
    uint i_cell = (uint) get_global_id(0);
    if (i_cell < ncells)
    {
        uint Nx_cell = Nx-1;
        uint Nppc_loc = Nppc_x*Nppc_r*Nppc_th;

        uint ir =  i_cell/Nx_cell;
        uint ix =  i_cell - Nx_cell*ir;
        uint ip = i_cell*Nppc_loc;

        double xmin = xgrid[ix];
        double rmin = rgrid[ir];
        double thmin = theta_var[i_cell];

        double Lx = xgrid[ix+1] - xgrid[ix];
        double Lr = rgrid[ir+1] - rgrid[ir];
        double dx = 1./( (double) Nppc_x);
        double dr = 1./( (double) Nppc_r);
        double dth = 2*M_PI/( (double) Nppc_th);
        double th, rp, sin_th, cos_th, rp_s, rp_c;
        double r_cell = rmin + 0.5*dr;

        for (uint incell_th=0; incell_th<Nppc_th; incell_th++){
          th = thmin + incell_th*dth;
          sin_th = sin(th);
          cos_th = cos(th);
          for (int incell_r=0;incell_r<Nppc_r;incell_r++){
            rp = rmin + (0.5+incell_r)*dr*Lr;
            rp_s = rp*sin_th;
            rp_c = rp*cos_th;
            for (int incell_x=0;incell_x<Nppc_x;incell_x++){
              x[ip] = xmin + (0.5+incell_x)*dx*Lx;
              y[ip] = rp_s;
              z[ip] = rp_c;
              w[ip] = rp;
              ip += 1;
            }}}
  }
}

// Find cell indicies of the particles and
// sums of paricles per cell
__kernel void index_and_sum_in_cell(
  __global double *x,
  __global double *y,
  __global double *z,
  __global uint *sum_in_cell,
  __constant uint *num_p,
  __global uint *indx_in_cell,
  __constant uint *Nx,
  __constant double *xmin,
  __constant double *dx_inv,
  __constant uint *Nr,
  __constant double *rmin,
  __constant double *dr_inv)
{
  uint ip = (uint) get_global_id(0);
  if (ip < *num_p)
   {
    double r;
    int ix,ir;
    int Nx_loc = (int) *Nx-1;
    int Nr_loc = (int) *Nr-1;

    r = sqrt(y[ip]*y[ip]+z[ip]*z[ip]);

    ix = (int)floor( (x[ip] - *xmin)*(*dx_inv) );
    ir = (int)floor((r - *rmin)*(*dr_inv));

    if (ix >= 0 && ix < Nx_loc && ir < Nr_loc && ir >= 0)
     {
      indx_in_cell[ip] = ix + ir * Nx_loc;
      atom_add(&sum_in_cell[indx_in_cell[ip]], 1U);
     }
    else
     {
      indx_in_cell[ip] = (Nr_loc+1) * (Nx_loc+1) + 1;
     }
  }
}

// Advance particles momenta using Boris pusher
// and write the inverse Lorentz factor array
__kernel void push_p_boris(
  __global double *px,
  __global double *py,
  __global double *pz,
  __global double *g_inv,
  __global double *Ex,
  __global double *Ey,
  __global double *Ez,
  __global double *Bx,
  __global double *By,
  __global double *Bz,
  __constant double *dt,
  __constant uint *num_p)
{
  uint ip = (uint) get_global_id(0);
  if (ip < *num_p)
   {
    double u_p[3] = {px[ip],py[ip],pz[ip]};
    double E_p[3] = {Ex[ip],Ey[ip],Ez[ip]};
    double B_p[3] = {Bx[ip],By[ip],Bz[ip]};

    double dt_2 = 0.5*(*dt);
    double um[3], up[3], u0[3], t[3], t2p1_m1_05, s[3], g_p_inv;
    int i;

    for(i=0;i<3;i++){
      um[i] = u_p[i] + dt_2*E_p[i];
      }

    g_p_inv = 1. / sqrt(1. + um[0]*um[0] + um[1]*um[1] + um[2]*um[2]);

    for(i=0;i<3;i++){
      t[i] = dt_2 * B_p[i] * g_p_inv;
      }

    t2p1_m1_05 = 2. / (1 + (t[0]*t[0] + t[1]*t[1] + t[2]*t[2])) ;

    for(i=0;i<3;i++){
      s[i] = t[i] * t2p1_m1_05;
      }

    u0[0] = um[0] + um[1]*t[2] - um[2]*t[1];
    u0[1] = um[1] - um[0]*t[2] + um[2]*t[0];
    u0[2] = um[2] + um[0]*t[1] - um[1]*t[0];

    up[0] = um[0] +  u0[1]*s[2] - u0[2]*s[1];
    up[1] = um[1] -  u0[0]*s[2] + u0[2]*s[0];
    up[2] = um[2] +  u0[0]*s[1] - u0[1]*s[0];

    for(int i=0;i<3;i++) {
      u_p[i] = up[i] + dt_2*E_p[i];
      }

    g_p_inv = 1. / sqrt(1. + u_p[0]*u_p[0] + u_p[1]*u_p[1] + u_p[2]*u_p[2]);

    px[ip] = u_p[0];
    py[ip] = u_p[1];
    pz[ip] = u_p[2];
    g_inv[ip] = g_p_inv;
    }
}

// Advance particles coordinates
__kernel void push_xyz(
  __global double *x,
  __global double *y,
  __global double *z,
  __global double *px,
  __global double *py,
  __global double *pz,
  __global double *g_inv,
  __constant double *dt,
  __constant uint *num_p)
{
  uint ip = (uint) get_global_id(0);
  if (ip < *num_p)
   {
    double dt_g = (*dt) * g_inv[ip];

    double dx = px[ip] * dt_g;
    double dy = py[ip] * dt_g;
    double dz = pz[ip] * dt_g;

    x[ip] += dx;
    y[ip] += dy;
    z[ip] += dz;
   }
}

// Copy sorted particle data of double-type to a new array
__kernel void data_align_dbl(
  __global double *x,
  __global double *x_new,
  __global uint *sorted_indx,
  uint num_p)
{
  uint ip = (uint) get_global_id(0);
  double x_tmp;
  if (ip < num_p)
  {
   x_tmp = x[sorted_indx[ip]];
   x_new[ip] =  x_tmp;
  }
}

// Copy sorted particle data of integer-type to a new array
__kernel void data_align_int(
  __global uint *x,
  __global uint *x_new,
  __global uint *sorted_indx,
  __constant uint *num_p)
{
  uint ip = (uint) get_global_id(0);
  if (ip < *num_p)
   {
    x_new[ip] = x[sorted_indx[ip]];
   }
}

