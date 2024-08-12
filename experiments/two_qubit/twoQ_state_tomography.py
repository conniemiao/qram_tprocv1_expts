import matplotlib.pyplot as plt
import numpy as np
from qick import *
import json
from copy import deepcopy

from slab import Experiment, NpEncoder, AttrDict
from tqdm import tqdm_notebook as tqdm

from experiments.clifford_averager_program import QutritAveragerProgram, CliffordAveragerProgram
from experiments.single_qubit.single_shot import hist

def sort_counts(shotsA, shotsB):
    # data is returned as n00, n01, n10, n11 measured for the two qubits
    n00 = np.sum(np.logical_and(np.logical_not(shotsA), np.logical_not(shotsB)))
    n01 = np.sum(np.logical_and(np.logical_not(shotsA), shotsB))
    n10 = np.sum(np.logical_and(shotsA, np.logical_not(shotsB)))
    n11 = np.sum(np.logical_and(shotsA, shotsB))
    return np.array([n00, n01, n10, n11])

def sort_counts_1q(shots):
    # data is returned as n0, n1 measured for 1 qubit
    n0 = np.sum(np.logical_not(shots))
    n1 = np.sum(shots)
    return np.array([n0, n1])

"""
See qiskit measurement error mitigation procedure: [https://qiskit.org/textbook/ch-quantum-hardware/measurement-error-mitigation.html](https://qiskit.org/textbook/ch-quantum-hardware/measurement-error-mitigation.html)
"""

def correct_readout_err(n, n_conf):
    n = np.array(n, dtype=float)
    conf_mat = np.array(n_conf, dtype=float)
    assert len(n.shape) == 2 # 2d array
    assert len(conf_mat.shape) == 2 # 2d array
    old_sum = sum(n[0])
    n_out_states = np.shape(n_conf)[0] # number of possible states that we are correcting our counts into
    for r, row in enumerate(conf_mat):
        conf_mat[r] /= sum(row) # normalize so counts for each state prep sum to 1
    conf_mat = np.transpose(conf_mat) # want counts for each state prep on columns
    # Check the determinant to make sure we are not running into machine precision
    # det = np.linalg.det(conf_mat)
    # print('DETERMINANT', det)
    if np.shape(conf_mat)[0] == np.shape(conf_mat)[1]: # square matrix
        conf_mat_inv = np.linalg.inv(conf_mat)
    else: conf_mat_inv = np.linalg.pinv(conf_mat)
    # C_id = invM . C_noisy
    n = np.array(n, dtype=float)
    out_n = np.zeros(shape=(np.shape(n)[0], n_out_states))
    for r in range(np.shape(n)[0]): # correct each set of measurements (rows of n)
        # print('n[r]', r, n[r])
        out_n[r] = (conf_mat_inv @ n[r].T).T
        out_n[r] *= old_sum/sum(n[r]) # scale so total counts in each row of out_n is same as total counts in each row of n
    return np.around(out_n, decimals=5)


def fix_neg_counts(counts):
    counts = np.array(counts)
    assert len(counts.shape) == 2 # 2d array

    for i_n, n in enumerate(counts):
        orig_sum = sum(n)
        while len(n[n<0]) > 0: # repeat while still has neg counts
            # print(i_n, n)
            assert orig_sum > 0, 'Negative sum of counts'
            most_neg_ind = np.argmin(n)
            n += abs(n[most_neg_ind]) / (len(n) - 1)
            n[most_neg_ind] = 0
        n *= orig_sum/sum(n)
    return counts

"""
Infer the populations of the g, e, (and f) states given 1 (2) measurements:
Obtain counts sorted into bins specified by calib_order.
Apply counts_calib if not None. Apply fix_neg_counts if True.
1. counts1: measure population with just the standard pulse sequence. Will take the g state population as the true g state population. If no counts2 is specified, will take e state population as true e state population and f will not be measured.
2. counts2: measure population with adding a ge pulse on the measure_f_qubits. Will take the g state pouplation as the true e state population, and the f state population as 1-g-e

qubits: qubits on which the counts were measured, should be ordered in the same way the calib_order strings are listed
measure_f_qubits: qubits on which ge pulses were applied to allow distinguishing e/f populations
    NOTE because of the nature of the qubit couplings it's best practice to do this 1 qubit at a time and make sure the ge pulse you apply actually is doing what you think it is doing
If post processing is scale instead of threshold, interprets counts1 and counts2 as g/e population percentages from the same 2 measurements for the qubits measured.

Returns gpop, epop, fpop which are arrays of length 4 (so only 1 state preparation at a time allowed!), with only qubit indices specified populated
"""
def infer_gef_popln(qubits, counts1, calib_order=None, counts2=None, measure_f_qubits=None, counts_calib=None, fix_neg_counts_flag=True, post_process='threshold'):

    gpop_q = [0]*4
    epop_q = [0]*4
    fpop_q = [0]*4
    if measure_f_qubits is not None and len(measure_f_qubits) > 0: assert counts2 is not None, 'cannot calculate f population without counts2'
    if counts2 is None: assert measure_f_qubits is None, 'cannot calculate f population without counts2'

    if post_process == 'scale':
        for i_q, q in enumerate(qubits):
            gpop_q[q] = 1-counts1[q]
            epop_q[q] = counts1[q]
            if measure_f_qubits is not None and q in measure_f_qubits:
                epop_q[q] = 1 - counts2[q]
                fpop_q[q] = 1 - gpop_q[q] - epop_q[q]
            
        return gpop_q, epop_q, fpop_q
    

    # ====== post_process = 'threshold' ====== #

    assert calib_order is not None
    assert len(calib_order) == np.shape(counts1)[1]
    assert np.shape(counts1)[0] == 1 # set of counts for 1 state preparation
    if fix_neg_counts_flag is not None: assert np.shape(calib_order)[0] == np.shape(counts1)[1]

    if counts_calib is not None: counts1 = correct_readout_err(counts1, counts_calib)
    if fix_neg_counts_flag: counts1 = fix_neg_counts(counts1)
    counts1 = counts1[0] # go back to just 1d array
    # print('corrected counts1', counts1)

    tot_counts1 = sum(counts1)
    for i_counts_state, counts_state in enumerate(calib_order):
        for i_q, q in enumerate(qubits):
            # counts_state is a string like xx (2q), xxx (3q) or xxxx (4q)
            if counts_state[i_q] == 'g':
                gpop_q[q] += counts1[i_counts_state] / tot_counts1
            else: epop_q[q] += counts1[i_counts_state] / tot_counts1 # this is the final answer if we don't care about distinguishing e/f

    if measure_f_qubits is not None and len(measure_f_qubits) > 0:
        # if we care about distinguishing e/f, the "g" popln of the 2nd experiment is the real e popln, and the real f popln is whatever is left
        for q in measure_f_qubits: epop_q[q] = 0 # reset this to recalculate e population

        if counts_calib is not None: counts2 = correct_readout_err(counts2, counts_calib)
        if fix_neg_counts_flag: counts2 = fix_neg_counts(counts2)
        counts2 = counts2[0] # go back to just 1d array
        # print('corrected counts2', counts2)

        tot_counts2 = sum(counts2)
        for i_counts_state, counts_state in enumerate(calib_order):
            for i_q, q in enumerate(qubits):
                if q not in measure_f_qubits: continue
                if counts_state[i_q] == 'g':
                    epop_q[q] += counts2[i_counts_state] / tot_counts2 # e population shows up as g population
        for i_q, q in enumerate(qubits):
            if q not in measure_f_qubits: continue
            fpop_q[q] = 1 - epop_q[q] - gpop_q[q]
    
    return gpop_q, epop_q, fpop_q


"""
Given 2 qubits, infer the g/e population of 1st qubit and the g/e/f populations of the 2nd qubit
(requires ordering in this way!)
Requires a single set of counts_raw_total [ggA, geA, egA, eeA, ggB, gfB, egB, efB]:
For experiment A, measure both qubits using the standard g/e readout.
For experiment B, measure the 1st qubit with standard g/e readout and the 2nd qubit with g/f readout.
Requires a calib count matrix which prepares gg, ge, eg, ee, gf, and ef and measures in the [ggA, geA, ... efB]
bins (so also requires 2 calibration experiments).

Returns gpop_q, epop_q, fpop_q which are the g/e/f populations of all 4 qubits.
"""
def infer_gef_popln_2readout(qubits, counts_raw_total, calib_order, counts_calib, fix_neg_counts_flag=True):

    gpop_q = [0]*4
    epop_q = [0]*4
    fpop_q = [0]*4

    assert calib_order is not None
    assert counts_calib is not None, 'if you do not correct the readout you will have nonsensical info since we have a non-square calib matrix'
    assert len(calib_order) == np.shape(counts_calib)[0]
    assert len(np.shape(counts_raw_total)) == 1 # 1d array for counts_raw_total
    assert np.shape(counts_calib)[1] == np.shape(counts_raw_total)[0]

    counts_corrected = correct_readout_err([counts_raw_total], counts_calib)
    # after correcting readout error, counts corrected should correspond to counts in [gg, ge, eg, ee, gf, ef] (the calib_order)
    # instead of [ggA, geA, egA, eeA, ggB, gfB, egB, efB] (the raw counts)
    if fix_neg_counts_flag: counts_corrected = fix_neg_counts(counts_corrected)

    counts_corrected = counts_corrected[0] # go back to just 1d array
    assert np.shape(counts_corrected) == np.shape(calib_order), f'shape of counts_corrected is {np.shape(counts_corrected)} and shape of calib_order is {np.shape(calib_order)}'

    tot_counts = sum(counts_corrected)
    for i_counts_state, counts_state in enumerate(calib_order):
        for i_q, q in enumerate(qubits):
            # counts_state is a string like xx (2q), xxx (3q) or xxxx (4q)
            if counts_state[i_q] == 'g':
                gpop_q[q] += counts_corrected[i_counts_state] / tot_counts
            elif counts_state[i_q] == 'e':
                # print(counts_corrected[i_counts_state], tot_counts, q, counts_state)
                epop_q[q] += counts_corrected[i_counts_state] / tot_counts
            else:
                fpop_q[q] += counts_corrected[i_counts_state] / tot_counts

    return gpop_q, epop_q, fpop_q




class AbstractStateTomo2QProgram(QutritAveragerProgram):
    """
    Performs a state_prep_pulse (abstract method) on two qubits, then measures in a desired basis.
    Repeat this program multiple times in the experiment to loop over all the bases necessary for tomography.
    Experimental Config:
    expt = dict(
        reps: number averages per measurement basis iteration
        qubits: the qubits to perform the two qubit tomography on (drive applied to the second qubit)
        basis: 'ZZ', 'ZX', 'ZY', 'XZ', 'XX', 'XY', 'YZ', 'YX', 'YY' the measurement bases for the 2 qubits
        state_prep_kwargs: dictionary containing kwargs for the state_prep_pulse function
    )
    """

    def setup_measure(self, qubit, basis:str, play=False, ZZ_qubit=None, flag='ZZcorrection'):
        """
        Convert string indicating the measurement basis into the appropriate single qubit pulse (pre-measurement pulse)
        Make sure to reload the pulses to ensure the pulse with flag ZZ correction is saved!
        ZZ_qubit is used to play the ZZ shifted pi pulses (this is mostly for when you have a known ZZ from a qubit not in the tomo)
        """
        assert basis in 'IXYZ'
        assert len(basis) == 1
        if basis == 'X':
            self.Y_pulse(qubit, pihalf=True, play=play, ZZ_qubit=ZZ_qubit, neg=True, flag=flag, reload=True) # -Y/2 pulse to get from +X to +Z
            # self.Y_pulse(2, pihalf=True, play=play, neg=True, flag=flag)
        elif basis == 'Y':
            self.X_pulse(qubit, pihalf=True, ZZ_qubit=ZZ_qubit, neg=False, play=play, flag=flag, reload=True) # X/2 pulse to get from +Y to +Z
            # print('WARNING FUNKY THINGS')
            # self.X_pulse(2, pihalf=True, play=play, neg=False, flag=flag)
        else: pass # measure in I/Z basis
        self.sync_all()

    def state_prep_pulse(self, qubits, **kwargs):
        """
        Plays the pulses to prepare the state we want to do tomography on.
        Pass in kwargs to state_prep_pulse through cfg.expt.state_prep_kwargs
        """
        raise NotImplementedError('Inherit this class and implement the state prep method!')

    def initialize(self):
        super().initialize()
        self.sync_all(200)
    
    def body(self):
        # Collect single shots and measure throughout pulses
        qubits = self.cfg.expt.tomo_qubits
        self.basis = self.cfg.expt.basis

        self.reset_and_sync()

        if 'cool_qubits' in self.cfg.expt and self.cfg.expt.cool_qubits is not None:
            cool_idle = [self.cfg.device.qubit.pulses.pi_f0g1.idle[q] for q in self.cfg.expt.cool_qubits]
            if 'cool_idle' in self.cfg.expt and self.cfg.expt.cool_idle is not None:
                cool_idle = self.cfg.expt.cool_idle
            self.active_cool(cool_qubits=self.cfg.expt.cool_qubits, cool_idle=cool_idle)

        # Prep state to characterize
        if 'state_prep_kwargs' not in self.cfg.expt or self.cfg.expt.state_prep_kwargs is None: kwargs = dict()
        else: kwargs = self.cfg.expt.state_prep_kwargs
        self.state_prep_pulse(qubits, **kwargs)
        self.sync_all() # DO NOT HAVE A WAIT TIME HERE

        # Go to the basis for the tomography measurement
        ZZ_qubit = None
        if 'ZZ_qubit' in self.cfg.expt and self.cfg.expt.ZZ_qubit is not None:
            ZZ_qubit = self.cfg.expt.ZZ_qubit
        self.setup_measure(qubit=qubits[0], basis=self.basis[0], ZZ_qubit=ZZ_qubit, play=True)
        self.setup_measure(qubit=qubits[1], basis=self.basis[1], ZZ_qubit=ZZ_qubit, play=True)

        # Simultaneous measurement
        syncdelay = self.us2cycles(max(self.cfg.device.readout.relax_delay))
        self.measure(pulse_ch=self.measure_chs, adcs=self.adc_chs, adc_trig_offset=self.cfg.device.readout.trig_offset[0], wait=True, syncdelay=syncdelay) 

    def collect_counts(self, angle=None, threshold=None):
        shots, avgq = self.get_shots(angle=angle, threshold=threshold)
        # collect shots for all adcs, then sorts into e, g based on >/< threshold and angle rotation
        # shots = np.array([np.heaviside(avgi[i] - threshold[i], 0) for i in range(len(self.adc_chs))])

        qubits = self.cfg.expt.tomo_qubits
        # get the shots for the qubits we care about
        shots = np.array([shots[self.adc_chs[q]] for q in qubits])

        return sort_counts(shots[0], shots[1])
    
    # def acquire(self, soc, angle=None, threshold=None, shot_avg=1, load_pulses=True, progress=False):
    #     avgi, avgq = super().acquire(soc, load_pulses=load_pulses, progress=progress)
    #     # print()
    #     # print(avgi)
    #     return self.collect_counts(angle=angle, threshold=threshold, shot_avg=shot_avg)

# ===================================================================== #

class ErrorMitigationStateTomo2QProgram(AbstractStateTomo2QProgram):
    """
    Prep the error mitigation matrix state and then perform 2Q state tomography.
    Experimental Config:
    expt = dict(
        reps: number averages per measurement basis iteration
        qubits: the qubits to perform the two qubit tomography on (drive applied to the second qubit)
        state_prep_kwargs.prep_state: gg, ge, eg, ee - the state to prepare in before measuring
        state_prep_kwargs.apply_q1_pi2: whether to initialize Q1 in 0+1 prior to measuring
    )
    """
    def state_prep_pulse(self, qubits, **kwargs):
        # pass in kwargs via cfg.expt.state_prep_kwargs
        prep_state = kwargs['prep_state'] # should be gg, ge, eg, or ee

        # Do all the calibrations with Q1 in 0+1
        if 'apply_q1_pi2' in kwargs: apply_q1_pi2 = kwargs['apply_q1_pi2']
        else: apply_q1_pi2 = False
        if apply_q1_pi2:
            assert 1 not in qubits
            
        if prep_state == 'gg':
            if apply_q1_pi2:
                self.Y_pulse(q=1, pihalf=True, play=True)

        elif prep_state == 'ge':
            self.X_pulse(q=qubits[1], play=True)
            if apply_q1_pi2:
                self.setup_and_pulse(ch=self.qubit_chs[1], style='arb', freq=self.f_Q1_ZZ_regs[qubits[1]], phase=self.deg2reg(-90, gen_ch=self.qubit_chs[1]), gain=self.cfg.device.qubit.pulses.pi_Q1_ZZ.gain[qubits[1]] // 2, waveform=f'qubit1_ZZ{qubits[1]}')
                self.sync_all()

        elif prep_state == 'gf':
            self.X_pulse(q=qubits[1], play=True)
            self.Xef_pulse(q=qubits[1], play=True)

        elif prep_state == 'eg':
            self.X_pulse(q=qubits[0], play=True)
            if apply_q1_pi2:
                self.setup_and_pulse(ch=self.qubit_chs[1], style='arb', freq=self.f_Q1_ZZ_regs[qubits[0]], phase=self.deg2reg(-90, gen_ch=self.qubit_chs[1]), gain=self.cfg.device.qubit.pulses.pi_Q1_ZZ.gain[qubits[0]] // 2, waveform=f'qubit1_ZZ{qubits[0]}')
                self.sync_all()

        elif prep_state == 'fg':
            self.X_pulse(q=qubits[0], play=True)
            self.Xef_pulse(q=qubits[0], play=True)

        elif prep_state == 'ee' or prep_state == 'ef' or prep_state == 'fe':
            self.X_pulse(q=qubits[0], play=True)

            ZZs = np.reshape(self.cfg.device.qubit.ZZs, (4,4))
            freq = self.freq2reg(self.cfg.device.qubit.f_ge[qubits[1]] + ZZs[qubits[1], qubits[0]], gen_ch=self.qubit_chs[qubits[1]])
            waveform = f'qubit{qubits[1]}_ZZ{qubits[0]}'
            if waveform not in self.envelopes:
                sigma_cycles = self.us2cycles(self.pi_sigmas_us[qubits[1]], gen_ch=self.qubit_chs[qubits[1]])
                self.add_gauss(ch=self.qubit_chs[qubits[1]], name=waveform, sigma=sigma_cycles, length=4*sigma_cycles)
                gain = self.cfg.device.qubit.pulses.pi_ge.gain[qubits[1]]
            elif qubits[1] == 1:
                gain = self.cfg.device.qubit.pulses.pi_Q1_ZZ.gain[qubits[0]]
            elif qubits[0] == 1:
                gain = self.cfg.device.qubit.pulses.pi_Q_ZZ1.gain[qubits[1]]
            else: assert False, "There's probably a bug in your conditional statements"
            self.setup_and_pulse(ch=self.qubit_chs[qubits[1]], style='arb', freq=freq, phase=0, gain=gain, waveform=waveform)
            self.sync_all()

            if apply_q1_pi2:
                freq = (self.cfg.device.qubit.f_Q1_ZZ[qubits[0]] + self.cfg.device.qubit.f_Q1_ZZ[qubits[1]]) / 2
                freq = self.freq2reg(freq, gen_ch=self.qubit_chs[1])
                waveform = f'qubit1_ZZ{qubits[0]}_ZZ{qubits[1]}'
                sigma_cycles = self.us2cycles(self.pi_sigmas_us[1], gen_ch=self.qubit_chs[1])
                self.add_gauss(ch=self.qubit_chs[1], name=waveform, sigma=sigma_cycles, length=4*sigma_cycles)
                gain = self.cfg.device.qubit.pulses.pi_ge.gain[1] // 2
                self.setup_and_pulse(ch=self.qubit_chs[1], style='arb', freq=freq, phase=self.deg2reg(-90, gen_ch=self.qubit_chs[1]), gain=gain, waveform=waveform)
                self.sync_all()

            if prep_state == 'ef':
                assert qubits[0] == 1 and qubits[1] in [2, 3], 'Only implemented this state prep for Q1=e, Q2/Q3=f'
                waveform = f'qubit{qubits[1]}_ef_ZZ{qubits[0]}'
                gain = self.cfg.device.qubit.pulses.pi_ef_Q_ZZ1.gain[qubits[1]]
                freq = self.freq2reg(self.cfg.device.qubit.f_ef_Q_ZZ1[qubits[1]], gen_ch=self.qubit_chs[qubits[1]])
                if waveform not in self.envelopes:
                    sigma_cycles = self.us2cycles(self.cfg.device.qubit.pulses.pi_ef_Q_ZZ1.sigma[qubits[1]], gen_ch=self.qubit_chs[qubits[1]])
                    self.add_gauss(ch=self.qubit_chs[qubits[1]], name=waveform, sigma=sigma_cycles, length=4*sigma_cycles)
                self.setup_and_pulse(ch=self.qubit_chs[qubits[1]], style='arb', freq=freq, phase=0, gain=gain, waveform=waveform)
                self.sync_all()

            elif prep_state == 'fe':
                assert qubits[0] in [2, 3] and qubits[1] == 1, 'Only implemented this state prep for Q1=e, Q2/Q3=f'
                waveform = f'qubit{qubits[0]}_ef_ZZ{qubits[1]}'
                gain = self.cfg.device.qubit.pulses.pi_ef_Q_ZZ1.gain[qubits[0]]
                freq = self.freq2reg(self.cfg.device.qubit.f_ef_Q_ZZ1[qubits[0]], gen_ch=self.qubit_chs[qubits[0]])
                if waveform not in self.envelopes:
                    sigma_cycles = self.us2cycles(self.cfg.device.qubit.pulses.pi_ef_Q_ZZ1.sigma[qubits[0]], gen_ch=self.qubit_chs[qubits[0]])
                    self.add_gauss(ch=self.qubit_chs[qubits[0]], name=waveform, sigma=sigma_cycles, length=4*sigma_cycles)
                self.setup_and_pulse(ch=self.qubit_chs[qubits[0]], style='arb', freq=freq, phase=0, gain=gain, waveform=waveform)
                self.sync_all()
        
        else: assert False, f'Invalid prep state {prep_state}'

    def initialize(self):
        self.cfg.expt.basis = 'ZZ'
        super().initialize()
        self.sync_all(200)

# ===================================================================== #

class EgGfStateTomo2QProgram(AbstractStateTomo2QProgram):
    """
    Perform misc pulses and then perform 2Q state tomography.
    Experimental Config:
    expt = dict(
        reps: number averages per measurement basis iteration
        qubits: the qubits to perform the two qubit tomography on (drive applied to the second qubit)
    )
    """
    def state_prep_pulse(self, qubits, **kwargs):
        qA, qB = self.cfg.expt.tomo_qubits
        
        self.Y_pulse(q=2, play=True)

        self.Y_ef_pulse(q=2, play=True)

        # self.X_pulse(q=2, play=True, pihalf=False)


    def initialize(self):

        super().initialize()
        qubits = self.cfg.expt.tomo_qubits
        qA, qB = qubits
        if 'state_prep_kwargs' not in self.cfg.expt: self.cfg.expt.state_prep_kwargs = None

        self.swap_chs = self.cfg.hw.soc.dacs.swap.ch
        self.swap_ch_types = self.cfg.hw.soc.dacs.swap.type

        # initialize ef pulse on qB
        qA, qB = qubits
        # self.handle_gauss_pulse(ch=self.qubit_chs[qB], name=f"ef_qubit{qB}", sigma=self.us2cycles(self.cfg.device.qubit.pulses.pi_ef.sigma[qB], gen_ch=self.qubit_chs[qB]), freq_MHz=self.cfg.device.qubit.f_ef[qB], phase_deg=0, gain=self.cfg.device.qubit.pulses.pi_ef.gain[qB], play=False)

        # initialize EgGf pulse
        # apply the sideband drive on qB, indexed by qA
        if qA != 1 and qB == 1:
            type = self.cfg.device.qubit.pulses.pi_EgGf.type[qA]
            freq_MHz = self.cfg.device.qubit.f_EgGf[qA]
            gain = self.cfg.device.qubit.pulses.pi_EgGf.gain[qA]
            if type == 'const':
                sigma = self.us2cycles(self.cfg.device.qubit.pulses.pi_EgGf.sigma[qA], gen_ch=self.swap_chs[qA])
                self.handle_const_pulse(name=f'pi_EgGf_{qA}{qB}', ch=self.swap_chs[qA], length=sigma, freq_MHz=freq_MHz, phase_deg=0, gain=gain, play=False) 
            elif type == 'gauss':
                sigma = self.us2cycles(self.cfg.device.qubit.pulses.pi_EgGf.sigma[qA], gen_ch=self.swap_chs[qA])
                self.handle_gauss_pulse(name=f'pi_EgGf_{qA}{qB}', ch=self.swap_chs[qA], sigma=sigma, freq_MHz=freq_MHz, phase_deg=0, gain=gain, play=False)
            elif type == 'flat_top':
                flat_length = self.us2cycles(self.cfg.device.qubit.pulses.pi_EgGf.sigma[qA], gen_ch=self.swap_chs[qA]) - 3*4
                self.handle_flat_top_pulse(name=f'pi_EgGf_{qA}{qB}', ch=self.swap_chs[qA], flat_length=flat_length, freq_MHz=freq_MHz, phase_deg=0, gain=gain, play=False) 
            else: assert False, f'Pulse type {type} not supported.'
        self.sync_all(200)

# ===================================================================== #

class EgGfStateTomographyExperiment(Experiment):
# outer loop over measurement bases
# set the state prep pulse to be preparing the gg, ge, eg, ee states for confusion matrix
    """
    Perform state tomography on the EgGf state with error mitigation.
    Experimental Config:
    expt = dict(
        reps: number averages per measurement basis iteration
        singleshot_reps: number averages in single shot calibration
        calib_apply_q1_pi2: initialize Q1 in 0+1 for all calibrations
        tomo_qubits: the qubits to perform the two qubit tomography on (drive applied to the second qubit)
    )
    """

    def __init__(self, soccfg=None, path='', prefix='EgGfStateTomography2Q', config_file=None, progress=None):
        super().__init__(path=path, soccfg=soccfg, prefix=prefix, config_file=config_file, progress=progress)

    def acquire(self, progress=False):
        # expand entries in config that are length 1 to fill all qubits
        num_qubits_sample = len(self.cfg.device.qubit.f_ge)
        qA, qB = self.cfg.expt.tomo_qubits

        for subcfg in (self.cfg.device.readout, self.cfg.device.qubit, self.cfg.hw.soc):
            for key, value in subcfg.items() :
                if isinstance(value, dict):
                    for key2, value2 in value.items():
                        for key3, value3 in value2.items():
                            if not(isinstance(value3, list)):
                                value2.update({key3: [value3]*num_qubits_sample})                                
                elif not(isinstance(value, list)):
                    subcfg.update({key: [value]*num_qubits_sample})
        
        self.meas_order = ['ZZ', 'ZX', 'ZY', 'XZ', 'XX', 'XY', 'YZ', 'YX', 'YY']
        self.calib_order = ['gg', 'ge', 'eg', 'ee'] # should match with order of counts for each tomography measurement 
        data={'counts_tomo':[], 'counts_calib':[]}
        self.pulse_dict = dict()

        # Error mitigation measurements: prep in gg, ge, eg, ee to recalibrate measurement angle and measure confusion matrix
        calib_prog_dict = dict()
        for prep_state in tqdm(self.calib_order):
            # print(prep_state)
            cfg = AttrDict(deepcopy(self.cfg))
            cfg.expt.reps = self.cfg.expt.singleshot_reps
            cfg.expt.state_prep_kwargs = dict(prep_state=prep_state, apply_q1_pi2=cfg.expt.calib_apply_q1_pi2)
            err_tomo = ErrorMitigationStateTomo2QProgram(soccfg=self.soccfg, cfg=cfg)
            err_tomo.acquire(self.im[self.cfg.aliases.soc], load_pulses=True, progress=False)
            calib_prog_dict.update({prep_state:err_tomo})

        g_prog = calib_prog_dict['gg']
        Ig, Qg = g_prog.get_shots(verbose=False)
        threshold = [0]*num_qubits_sample
        angle = [0]*num_qubits_sample

        # Get readout angle + threshold for qubit A
        e_prog = calib_prog_dict['eg']
        Ie, Qe = e_prog.get_shots(verbose=False)
        shot_data = dict(Ig=Ig[qA], Qg=Qg[qA], Ie=Ie[qA], Qe=Qe[qA])
        print(f'Qubit  ({qA})')
        fid, thresholdA, angleA = hist(data=shot_data, plot=progress, verbose=False)
        threshold[qA] = thresholdA[0]
        angle[qA] = angleA

        # Get readout angle + threshold for qubit B
        e_prog = calib_prog_dict['ge']
        Ie, Qe = e_prog.get_shots(verbose=False)
        shot_data = dict(Ig=Ig[qB], Qg=Qg[qB], Ie=Ie[qB], Qe=Qe[qB])
        print(f'Qubit  ({qB})')
        fid, thresholdB, angleB = hist(data=shot_data, plot=progress, verbose=False)
        threshold[qB] = thresholdB[0]
        angle[qB] = angleB

        print('thresholds', threshold)
        print('angles', angle)

        # Process the shots taken for the confusion matrix with the calibration angles
        for prep_state in self.calib_order:
            counts = calib_prog_dict[prep_state].collect_counts(angle=angle, threshold=threshold)
            data['counts_calib'].append(counts)

        # Tomography measurements
        for basis in tqdm(self.meas_order):
            # print(basis)
            cfg = AttrDict(deepcopy(self.cfg))
            cfg.expt.basis = basis
            # assert 'Icontrols' in self.cfg.expt and 'Qcontrols' in self.cfg.expt and 'times_us' in self.cfg.expt
            # cfg.expt.state_prep_kwargs = dict(I_mhz_vs_us=cfg.expt.Icontrols, Q_mhz_vs_us=cfg.expt.Qcontrols, times_us=cfg.expt.times_us)
            # initialize registers
            # cfg.expt.update(dict(
            #     start=0,
            #     step=int(32000/101),
            #     expts=101,
            #     rounds=10,
            # ))
            tomo = EgGfStateTomo2QProgram(soccfg=self.soccfg, cfg=cfg)
            # print(tomo)
            # from qick.helpers import progs2json
            # print(progs2json([tomo.dump_prog()]))
            # xpts, avgi, avgq = tomo.acquire(self.im[self.cfg.aliases.soc], load_pulses=True, progress=False)
            avgi, avgq = tomo.acquire(self.im[self.cfg.aliases.soc], load_pulses=True, progress=False)

            # print(basis)
            adc_chs = self.cfg.hw.soc.adcs.readout.ch
            # avgi, avgq = tomo.get_shots(angle=None, avg_shots=True)
            # for q in self.cfg.expt.tomo_qubits:
            #     print('q', q, 'avgi', avgi[adc_chs[q]])
            #     print('q', q, 'avgq', avgq[adc_chs[q]])
            #     print('q', q, 'amps', np.abs(avgi[adc_chs[q]]+1j*avgi[adc_chs[q]]))

            counts = tomo.collect_counts(angle=angle, threshold=threshold)
            data['counts_tomo'].append(counts)
            self.pulse_dict.update({basis:tomo.pulse_dict})

        self.data=data
        return data

    def analyze(self, data=None, **kwargs):
        if data is None: data = self.data
        print('Analyze function does nothing, use the analysis notebook.')
        return data

    def display(self, qubit, data=None, fit=True, **kwargs):
        if data is None: data=self.data 
        print('Display function does nothing, use the analysis notebook.')
    
    def save_data(self, data=None):
        print(f'Saving {self.fname}')
        super().save_data(data=data)
        # print(self.pulse_dict)
        with self.datafile() as f:
            f.attrs['pulse_dict'] = json.dumps(self.pulse_dict, cls=NpEncoder)
            f.attrs['meas_order'] = json.dumps(self.meas_order, cls=NpEncoder)
            f.attrs['calib_order'] = json.dumps(self.calib_order, cls=NpEncoder)
        return self.fname

# ===================================================================== #
# 1 QUBIT TOMOGRAPHY CLASSES #
# ===================================================================== #

class AbstractStateTomo1QProgram(AbstractStateTomo2QProgram):
    """
    Performs a state_prep_pulse (abstract method) on 1 qubit, then measures in a desired basis.
    Repeat this program multiple times in the experiment to loop over all the bases necessary for tomography.
    Experimental Config:
    expt = dict(
        reps: number averages per measurement basis iteration
        qubits: the qubits to perform the two qubit tomography on (drive applied to the second qubit)
        basis: 'Z', 'X', 'Y' the measurement bases for the qubit
        state_prep_kwargs: dictionary containing kwargs for the state_prep_pulse function
    )
    """

    def setup_measure(self, basis:str, play=False):
        """
        Convert string indicating the measurement basis into the appropriate single qubit pulse (pre-measurement pulse)
        """
        ZZ_qubit = None
        if 'ZZ_qubit' in self.cfg.expt and self.cfg.expt.ZZ_qubit is not None:
            ZZ_qubit = self.cfg.expt.ZZ_qubit
        super().setup_measure(qubit=self.qubit, basis=basis, ZZ_qubit=ZZ_qubit, play=play)

    def state_prep_pulse(self, **kwargs):
        """
        Plays the pulses to prepare the state we want to do tomography on.
        Pass in kwargs to state_prep_pulse through cfg.expt.state_prep_kwargs
        """
        raise NotImplementedError('Inherit this class and implement the state prep method!')

    def initialize(self):
        super().initialize()
        assert len(np.shape(self.cfg.expt.qubit)) == 0
        self.qubit = self.cfg.expt.qubit
    
    def body(self):
        # Collect single shots and measure throughout pulses
        self.basis = self.cfg.expt.basis

        self.reset_and_sync()

        if 'cool_qubits' in self.cfg.expt and self.cfg.expt.cool_qubits is not None:
            cool_idle = [self.cfg.device.qubit.pulses.pi_f0g1.idle[q] for q in self.cfg.expt.cool_qubits]
            if 'cool_idle' in self.cfg.expt and self.cfg.expt.cool_idle is not None:
                cool_idle = self.cfg.expt.cool_idle
            self.active_cool(cool_qubits=self.cfg.expt.cool_qubits, cool_idle=cool_idle)

        # Prep state to characterize
        kwargs = self.cfg.expt.state_prep_kwargs
        if kwargs is None: kwargs = dict()
        self.state_prep_pulse(**kwargs)
        self.sync_all() # DO NOT HAVE A WAIT TIME HERE

        # Go to the basis for the tomography measurement
        self.setup_measure(basis=self.basis[0], play=True)
        self.sync_all()

        # Simultaneous measurement
        syncdelay = self.us2cycles(max(self.cfg.device.readout.relax_delay))
        self.measure(pulse_ch=self.measure_chs, adcs=self.adc_chs, adc_trig_offset=self.cfg.device.readout.trig_offset[0], wait=True, syncdelay=syncdelay) 

    def collect_counts(self, angle=None, threshold=None):
        avgi, avgq = self.get_shots(angle=angle)
        # collect shots for all adcs, then sorts into e, g based on >/< threshold and angle rotation
        shots = np.array([np.heaviside(avgi[i] - threshold[i], 0) for i in range(len(self.adc_chs))])

        # get the shots for the qubits we care about
        shots = np.array(shots[self.adc_chs[self.qubit]])

        # data is returned as n00, n01, n10, n11 measured for the two qubits
        n0 = np.sum(np.logical_not(shots))
        n1 = np.sum(shots)
        return np.array([n0, n1])

# ===================================================================== #

class ErrorMitigationStateTomo1QProgram(AbstractStateTomo1QProgram):
    """
    Prep the error mitigation matrix state and then perform 2Q state tomography.
    Experimental Config:
    expt = dict(
        reps: number averages per measurement basis iteration
        qubits: the qubits to perform the two qubit tomography on (drive applied to the second qubit)
        state_prep_kwargs.prep_state: gg, ge, eg, ee - the state to prepare in before measuring
    )
    """
    def state_prep_pulse(self, **kwargs):
        # pass in kwargs via cfg.expt.state_prep_kwargs
        prep_state = kwargs['prep_state'] # should be gg, ge, eg, or ee
        if prep_state == 'e':
            # print('q0: e')
            self.X_pulse(q=self.qubit, play=True)
            self.sync_all() # necessary for ZZ?
        elif prep_state == 'f':
            # print('q0: e')
            self.X_pulse(q=self.qubit, play=True)
            self.Xef_pulse(q=self.qubit, play=True)
            self.sync_all() # necessary for ZZ?
        else:
            # print('q0: g')
            assert prep_state == 'g'
            
    def initialize(self):
        super().initialize()
        self.cfg.expt.basis = 'Z'
        self.sync_all(200)

# ===================================================================== #

class StateTomo1QProgram(AbstractStateTomo1QProgram):
    """
    Setup a state and then perform 1Q state tomography.
    Experimental Config:
    expt = dict(
        reps: number averages per measurement basis iteration
    )
    """
    def handle_next_pulse(self, count_us, ch, freq_reg, type, phase, gain, sigma_us, waveform):
        if type == 'gauss':
            self.setup_and_pulse(ch=ch, style='arb', freq=freq_reg, phase=phase, gain=gain, waveform=waveform)
        elif type == 'flat_top':
            sigma_ramp_cycles = 3
            flat_length_cycles = self.us2cycles(sigma_us, gen_ch=ch) - sigma_ramp_cycles*4
            self.setup_and_pulse(ch=ch, style='flat_top', freq=freq_reg, phase=phase, gain=gain, length=flat_length_cycles, waveform=f"{waveform}_ramp")
        elif type == 'const':
            self.setup_and_pulse(ch=ch, style='const', freq=freq_reg, phase=phase, gain=gain, length=self.us2cycles(sigma_us, gen_ch=ch))

    def state_prep_pulse(self, **kwargs):
        cfg = self.cfg
        # pass in kwargs via cfg.expt.state_prep_kwargs
        # self.X_pulse(q=0, play=True)
        # self.X_pulse(q=1, play=True)
        # self.X_pulse(q=1, special='pulseiq', play=True, **kwargs)

        self.X_pulse(q=0, play=True, pihalf=True)
        self.Z_pulse(q=0, play=True, pihalf=True)
        self.Y_pulse(q=0, play=True, pihalf=True, neg=True)


    def initialize(self):
        super().initialize()
        if 'state_prep_kwargs' not in self.cfg.expt: self.cfg.expt.state_prep_kwargs = None
        self.swap_chs = self.cfg.hw.soc.dacs.swap.ch
        self.swap_ch_types = self.cfg.hw.soc.dacs.swap.type
        self.f_EgGf_regs = [self.freq2reg(f, gen_ch=ch) for f, ch in zip(self.cfg.device.qubit.f_EgGf, self.swap_chs)]

        self.swap_Q_chs = self.cfg.hw.soc.dacs.swap_Q.ch
        self.swap_Q_ch_types = self.cfg.hw.soc.dacs.swap_Q.type
        self.f_EgGf_Q_regs = [self.freq2reg(f, gen_ch=ch) for f, ch in zip(self.cfg.device.qubit.f_EgGf_Q, self.swap_chs)]

        # get aliases for the sigmas we need in clock cycles
        self.pi_EgGf_types = self.cfg.device.qubit.pulses.pi_EgGf.type
        assert all(type == 'flat_top' for type in self.pi_EgGf_types)
        self.pi_EgGf_sigmas_us = self.cfg.device.qubit.pulses.pi_EgGf.sigma

        self.pi_EgGf_Q_types = self.cfg.device.qubit.pulses.pi_EgGf_Q.type
        assert all(type == 'flat_top' for type in self.pi_EgGf_Q_types)
        self.pi_EgGf_Q_sigmas_us = self.cfg.device.qubit.pulses.pi_EgGf_Q.sigma

        # add qubit pulses to respective channels
        for q in range(4):
            if q != 1:
                if self.pi_EgGf_types[q] == 'gauss':
                    pi_EgGf_sigma_cycles = self.us2cycles(self.pi_EgGf_sigmas_us[q], gen_ch=self.swap_chs[1])
                    self.add_gauss(ch=self.swap_chs[q], name=f"pi_EgGf_swap{q}", sigma=pi_EgGf_sigma_cycles, length=pi_EgGf_sigma_cycles*4)
                elif self.pi_EgGf_types[q] == 'flat_top':
                    sigma_ramp_cycles = 3
                    self.add_gauss(ch=self.swap_chs[q], name=f"pi_EgGf_swap{q}_ramp", sigma=sigma_ramp_cycles, length=sigma_ramp_cycles*4)

                if self.pi_EgGf_Q_types[q] == 'flat_top':
                    sigma_ramp_cycles = 3
                    self.add_gauss(ch=self.swap_Q_chs[q], name=f"pi_EgGf_Q_swap{q}_ramp", sigma=sigma_ramp_cycles, length=sigma_ramp_cycles*4)

        self.sync_all(200)


# ===================================================================== #

class StateTomography1QExperiment(Experiment):
# outer loop over measurement bases
# set the state prep pulse to be preparing the gg, ge, eg, ee states for confusion matrix
    """
    Perform state tomography on 1Q state with error mitigation.
    Experimental Config:
    expt = dict(
        reps: number averages per measurement basis iteration
        singleshot_reps: number averages in single shot calibration
    )
    """

    def __init__(self, soccfg=None, path='', prefix='StateTomography1Q', config_file=None, progress=None):
        super().__init__(path=path, soccfg=soccfg, prefix=prefix, config_file=config_file, progress=progress)

    def acquire(self, progress=False):
        # expand entries in config that are length 1 to fill all qubits
        num_qubits_sample = len(self.cfg.device.qubit.f_ge)
        q = self.cfg.expt.qubit

        for subcfg in (self.cfg.device.readout, self.cfg.device.qubit, self.cfg.hw.soc):
            for key, value in subcfg.items() :
                if isinstance(value, dict):
                    for key2, value2 in value.items():
                        for key3, value3 in value2.items():
                            if not(isinstance(value3, list)):
                                value2.update({key3: [value3]*num_qubits_sample})                                
                elif not(isinstance(value, list)):
                    subcfg.update({key: [value]*num_qubits_sample})
        
        self.meas_order = ['Z', 'X', 'Y']
        self.calib_order = ['g', 'e'] # should match with order of counts for each tomography measurement 
        data={'counts_tomo':[], 'counts_calib':[]}
        self.pulse_dict = dict()

        # Error mitigation measurements: prep in g, e to recalibrate measurement angle and measure confusion matrix
        calib_prog_dict = dict()
        for prep_state in tqdm(self.calib_order):
            # print(prep_state)
            cfg = AttrDict(deepcopy(self.cfg))
            cfg.expt.reps = self.cfg.expt.singleshot_reps
            cfg.expt.state_prep_kwargs = dict(prep_state=prep_state)
            err_tomo = ErrorMitigationStateTomo1QProgram(soccfg=self.soccfg, cfg=cfg)
            err_tomo.acquire(self.im[self.cfg.aliases.soc], load_pulses=True, progress=False)
            calib_prog_dict.update({prep_state:err_tomo})

        g_prog = calib_prog_dict['g']
        Ig, Qg = g_prog.get_shots(verbose=False)
        threshold = [0]*num_qubits_sample
        angle = [0]*num_qubits_sample

        # Get readout angle + threshold for qubit
        e_prog = calib_prog_dict['e']
        Ie, Qe = e_prog.get_shots(verbose=False)
        shot_data = dict(Ig=Ig[q], Qg=Qg[q], Ie=Ie[q], Qe=Qe[q])
        fid, thresholdq, angleq = hist(data=shot_data, plot=progress, verbose=False)
        threshold[q] = thresholdq
        angle[q] = angleq

        if progress:
            print('thresholds', threshold)
            print('angles', angle)

        # Process the shots taken for the confusion matrix with the calibration angles
        for prep_state in self.calib_order:
            counts = calib_prog_dict[prep_state].collect_counts(angle=angle, threshold=threshold)
            data['counts_calib'].append(counts)

        # Tomography measurements
        for basis in tqdm(self.meas_order):
            # print(basis)
            cfg = AttrDict(deepcopy(self.cfg))
            cfg.expt.basis = basis
            if 'Icontrols' in cfg.expt and 'Qcontrols' in cfg.expt and 'times_us' in self.cfg.expt:
                cfg.expt.state_prep_kwargs = dict(I_mhz_vs_us=cfg.expt.Icontrols, Q_mhz_vs_us=cfg.expt.Qcontrols, times_us=cfg.expt.times_us)
            tomo = StateTomo1QProgram(soccfg=self.soccfg, cfg=cfg)
            # print(tomo)
            tomo.acquire(self.im[self.cfg.aliases.soc], load_pulses=True, progress=False)
            counts = tomo.collect_counts(angle=angle, threshold=threshold)
            data['counts_tomo'].append(counts)
            self.pulse_dict.update({basis:tomo.pulse_dict})

        self.data=data
        return data

    def analyze(self, data=None, **kwargs):
        if data is None: data = self.data
        print('Analyze function does nothing, use the analysis notebook.')
        return data

    def display(self, qubit, data=None, fit=True, **kwargs):
        if data is None: data=self.data 
        print('Display function does nothing, use the analysis notebook.')
    
    def save_data(self, data=None):
        print(f'Saving {self.fname}')
        super().save_data(data=data)
        # print(self.pulse_dict)
        with self.datafile() as f:
            f.attrs['pulse_dict'] = json.dumps(self.pulse_dict, cls=NpEncoder)
            f.attrs['meas_order'] = json.dumps(self.meas_order, cls=NpEncoder)
            f.attrs['calib_order'] = json.dumps(self.calib_order, cls=NpEncoder)
        return self.fname