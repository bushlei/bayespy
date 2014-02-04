######################################################################
# Copyright (C) 2013-2014 Jaakko Luttinen
#
# This file is licensed under Version 3.0 of the GNU General Public
# License. See LICENSE for a text of the license.
######################################################################

######################################################################
# This file is part of BayesPy.
#
# BayesPy is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# BayesPy is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BayesPy.  If not, see <http://www.gnu.org/licenses/>.
######################################################################

"""
Demonstrate linear Gaussian state-space model with drifting dynamics.
"""

import numpy as np
import scipy
import matplotlib.pyplot as plt

from bayespy.nodes import GaussianMarkovChain
from bayespy.nodes import GaussianArrayARD
from bayespy.nodes import Gamma
from bayespy.nodes import SumMultiply

from bayespy.utils import utils
from bayespy.utils import random

from bayespy.inference.vmp.vmp import VB
from bayespy.inference.vmp import transformations

import bayespy.plot.plotting as bpplt


def simulate_drifting_lssm(M, N):
    """
    Simulate some data with changing dynamics.
    """
    D = 3
    c = np.random.randn(M,D)
    a = np.empty((N-1,D,D))
    n = 0
    for l in np.linspace(5, 1, num=N-1):
        w = 1/l
        a[n] = np.array([[np.cos(w), -np.sin(w), 0], 
                         [np.sin(w), np.cos(w),  0], 
                         [0,         0,          1]])
        n = n + 1
    x = np.empty((N,D))
    f = np.empty((M,N))
    y = np.empty((M,N))
    x[0] = 10*np.random.randn(D)
    f[:,0] = np.dot(c,x[0])
    y[:,0] = f[:,0] + 3*np.random.randn(M)
    for n in range(N-1):
        x[n+1] = np.dot(a[n],x[n]) + np.random.randn(D)
        f[:,n+1] = np.dot(c,x[n+1])
        y[:,n+1] = f[:,n+1] + 3*np.random.randn(M)
    return (y, f)

def run_dlssm(y, f, mask, D, K, maxiter,
             rotate=False, debug=False, precompute=False,
             drift_c=False):
    """
    Run VB inference for linear state space model with drifting dynamics.
    """
        
    (M, N) = np.shape(y)

    # Dynamics matrix with ARD
    # alpha : (K) x ()
    alpha = Gamma(1e-5,
                  1e-5,
                  plates=(K,),
                  name='alpha')
    # A : (K) x (K)
    A = GaussianArrayARD(np.identity(K),
                         alpha,
                         shape=(K,),
                         plates=(K,),
                         name='A_S',
                         initialize=False)
    A.initialize_from_value(np.identity(K))

    # State of the drift
    # S : () x (N,K)
    S = GaussianMarkovChain(np.ones(K),
                            1e-6*np.identity(K),
                            A,
                            np.ones(K),
                            n=N,
                            name='S',
                            initialize=False)
    S.initialize_from_value(np.ones((N,K))+0.01*np.random.randn(N,K))

    # Projection matrix of the dynamics matrix
    # Initialize S and B such that BS is identity matrix
    # beta : (K) x ()
    beta = Gamma(1e-5,
                 1e-5,
                 plates=(D,K),
                 name='beta')
    # B : (D) x (D,K)
    b = np.zeros((D,D,K))
    b[np.arange(D),np.arange(D),np.zeros(D,dtype=int)] = 1
    B = GaussianArrayARD(0,
                         beta,
                         shape=(D,K),
                         plates=(D,),
                         name='B',
                         initialize=False)
    B.initialize_from_value(np.reshape(1*b, (D,D,K)))
    # BS : (N-1,D) x (D)
    BS = SumMultiply('dk,k->d',
                     B, 
                     S.as_gaussian()[...,None],
                     name='BS')

    # Latent states with dynamics
    # X : () x (N,D)
    X = GaussianMarkovChain(np.zeros(D),         # mean of x0
                            1e-3*np.identity(D), # prec of x0
                            BS,                  # dynamics
                            np.ones(D),          # innovation
                            n=N+1,               # time instances
                            name='X',
                            initialize=False)
    X.initialize_from_value(np.random.randn(N+1,D))

    # Observation noise
    # tau : () x ()
    tau = Gamma(1e-5,
                1e-5,
                name='tau')

    if drift_c:
        # Mixing matrix from latent space to observation space using ARD
        # gamma : (D,K) x ()
        gamma = Gamma(1e-5,
                      1e-5,
                      plates=(D,K),
                      name='gamma')
        # C : (M,1) x (D,K)
        C = GaussianArrayARD(0,
                             gamma,
                             shape=(D,K),
                             plates=(M,1),
                             name='C',
                             initialize=False)
        C.initialize_from_random()

        # Observations
        # Y : (M,N) x ()
        F = SumMultiply('dk,d,k',
                        C,
                        X.as_gaussian()[1:],
                        S.as_gaussian(),
                        name='F')
    else:
        # Mixing matrix from latent space to observation space using ARD
        # gamma : (D) x ()
        gamma = Gamma(1e-5,
                      1e-5,
                      plates=(D,),
                      name='gamma')
        # C : (M,1) x (D)
        C = GaussianArrayARD(0,
                             gamma,
                             shape=(D,),
                             plates=(M,1),
                             name='C',
                             initialize=False)
        C.initialize_from_random()

        # Observations
        # Y : (M,N) x ()
        F = SumMultiply('d,d',
                        C,
                        X.as_gaussian()[1:],
                        name='F')
                  
    Y = GaussianArrayARD(F,
                         tau,
                         name='Y')

    #
    # RUN INFERENCE
    #

    # Observe data
    Y.observe(y, mask=mask)
    # Construct inference machine
    Q = VB(Y, X, S, A, alpha, B, beta, C, gamma, tau)

    #
    # Run inference with rotations.
    #

    if rotate:
        # Rotate the D-dimensional state space (C, X)
        rotB = transformations.RotateGaussianArrayARD(B, beta, axis=-2,
                                                      precompute=precompute)
        rotX = transformations.RotateDriftingMarkovChain(X, 
                                                         B, 
                                                         S.as_gaussian()[...,None], 
                                                         rotB)
        if drift_c:
            rotC = transformations.RotateGaussianArrayARD(C, gamma, axis=-2)
        else:
            rotC = transformations.RotateGaussianArrayARD(C, gamma, axis=-1)
        R_X = transformations.RotationOptimizer(rotX, rotC, D)

        # Rotate the K-dimensional latent dynamics space (B, S)
        rotA = transformations.RotateGaussianArrayARD(A, alpha, 
                                                      precompute=precompute)
        rotS = transformations.RotateGaussianMarkovChain(S, rotA)
        rotB = transformations.RotateGaussianArrayARD(B, beta, axis=-1,
                                                      precompute=precompute)
        if drift_c:
            # TODO: ALSO ROTATE C!!! That is, C+B and S
            raise NotImplementedError()
            rotC = None
            rotBC = None
            R_S = transformations.RotationOptimizer(rotS, rotBC, K)
        else:
            R_S = transformations.RotationOptimizer(rotS, rotB, K)
            
        if debug:
            rotate_kwargs = {'check_bound': True,
                             'check_gradient': True}
        else:
            rotate_kwargs = {}

    # Iterate
    for ind in range(0):
        Q.update(X, B, beta, C, gamma, tau)
        if rotate:
            R_X.rotate(**rotate_kwargs)
        
    for ind in range(maxiter):
        Q.update()
        if rotate:
            R_X.rotate(**rotate_kwargs)
            R_S.rotate(**rotate_kwargs)

    #
    # SHOW RESULTS
    #

    # Plot observations space
    plt.figure()
    bpplt.timeseries_normal(F, scale=2)
    bpplt.timeseries(f, 'b-')
    bpplt.timeseries(y, 'r.')
    
    # Plot latent space
    plt.figure()
    bpplt.timeseries_gaussian_mc(X, scale=2)
    
    # Plot drift space
    plt.figure()
    bpplt.timeseries_gaussian_mc(S, scale=2)
    

def run_lssm(y, f, mask, D, maxiter, 
             rotate=False, debug=False, precompute=False):
    """
    Run VB inference for linear state space model.
    """

    (M, N) = np.shape(y)

    #
    # CONSTRUCT THE MODEL
    #

    # Dynamic matrix
    # alpha: (D) x ()
    alpha = Gamma(1e-5,
                  1e-5,
                  plates=(D,),
                  name='alpha')
    # A : (D) x (D)
    A = GaussianArrayARD(0,
                         alpha,
                         shape=(D,),
                         plates=(D,),
                         name='A')
    A.initialize_from_value(np.identity(D))

    # Latent states with dynamics
    # X : () x (N,D)
    X = GaussianMarkovChain(np.zeros(D),         # mean of x0
                            1e-3*np.identity(D), # prec of x0
                            A,                   # dynamics
                            np.ones(D),          # innovation
                            n=N,                 # time instances
                            name='X',
                            initialize=False)
    X.initialize_from_value(np.random.randn(N,D))

    # Mixing matrix from latent space to observation space using ARD
    # gamma : (D) x ()
    gamma = Gamma(1e-5,
                  1e-5,
                  plates=(D,),
                  name='gamma')
    # C : (M,1) x (D)
    C = GaussianArrayARD(0,
                         gamma,
                         shape=(D,),
                         plates=(M,1),
                         name='C')
    C.initialize_from_value(np.random.randn(M,1,D))

    # Observation noise
    # tau : () x ()
    tau = Gamma(1e-5,
                1e-5,
                name='tau')

    # Observations
    # Y : (M,N) x ()
    F = SumMultiply('i,i',
                    C,
                    X.as_gaussian())
    Y = GaussianArrayARD(F,
                         tau,
                         name='Y')

    if rotate:
        # Rotate the D-dimensional latent space
        rotA = transformations.RotateGaussianArrayARD(A, alpha,
                                                      precompute=precompute)
        rotX = transformations.RotateGaussianMarkovChain(X, rotA)
        rotC = transformations.RotateGaussianArrayARD(C, gamma)
        R = transformations.RotationOptimizer(rotX, rotC, D)

    #
    # RUN INFERENCE
    #

    # Observe data
    Y.observe(y, mask=mask)
    # Construct inference machine
    Q = VB(Y, X, A, alpha, C, gamma, tau)

    # Iterate
    for ind in range(maxiter):
        Q.update(X, A, alpha, C, gamma, tau)
        if rotate:
            if debug:
                R.rotate(check_bound=True,
                         check_gradient=True)
            else:
                R.rotate()

    #
    # SHOW RESULTS
    #

    plt.figure()
    bpplt.timeseries_normal(F, scale=2)
    bpplt.timeseries(f, 'b-')
    bpplt.timeseries(y, 'r.')
    

def run(M=10, N=200, D=4, K=None, seed=42, maxiter=50, 
        rotate=False, debug=False, precompute=False):

    # Seed for random number generator
    if seed is not None:
        np.random.seed(seed)

    # Create data
    (y, f) = simulate_drifting_lssm(M, N)

    # Add missing values randomly
    mask = random.mask(M, N, p=0.8)
    # Add missing values to a period of time
    #mask[:,100:110] = False
    mask[:,70:120] = False
    #mask[:] = True # DEBUG
    #mask[:] = True
    y[~mask] = np.nan # BayesPy doesn't require NaNs, they're just for plotting.

    # Run the method
    if K is not None:
        run_dlssm(y, f, mask, D, K, maxiter,
                  rotate=rotate,
                  debug=debug,
                  precompute=precompute)
    else:
        run_lssm(y, f, mask, D, maxiter, 
                 rotate=rotate, 
                 debug=debug,
                 precompute=precompute)
        
    plt.show()

if __name__ == '__main__':
    import sys, getopt, os
    try:
        opts, args = getopt.getopt(sys.argv[1:],
                                   "",
                                   ["m=",
                                    "n=",
                                    "d=",
                                    "k=",
                                    "seed=",
                                    "maxiter=",
                                    "debug",
                                    "precompute",
                                    "rotate"])
    except getopt.GetoptError:
        print('python demo_lssm_drift.py <options>')
        print('--m=<INT>        Dimensionality of data vectors')
        print('--n=<INT>        Number of data vectors')
        print('--d=<INT>        Dimensionality of the latent vectors in the model')
        print('--k=<INT>        Dimensionality of the latent drift space')
        print('--rotate         Apply speed-up rotations')
        print('--maxiter=<INT>  Maximum number of VB iterations')
        print('--seed=<INT>     Seed (integer) for the random number generator')
        print('--debug          Check that the rotations are implemented correctly')
        print('--precompute     Precompute some moments when rotating. May '
              'speed up or slow down.')
        sys.exit(2)

    kwargs = {}
    for opt, arg in opts:
        if opt == "--rotate":
            kwargs["rotate"] = True
        elif opt == "--maxiter":
            kwargs["maxiter"] = int(arg)
        elif opt == "--debug":
            kwargs["debug"] = True
        elif opt == "--precompute":
            kwargs["precompute"] = True
        elif opt == "--seed":
            kwargs["seed"] = int(arg)
        elif opt in ("--m",):
            kwargs["M"] = int(arg)
        elif opt in ("--n",):
            kwargs["N"] = int(arg)
        elif opt in ("--d",):
            kwargs["D"] = int(arg)
        elif opt in ("--k",):
            kwargs["K"] = int(arg)

    run(**kwargs)
