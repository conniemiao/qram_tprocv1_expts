# Author: Connie 2022/02/17

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit
from copy import deepcopy
import json

from qick import *
from qick.helpers import gauss

from slab import Experiment, NpEncoder, AttrDict
from tqdm import tqdm_notebook as tqdm

from experiments.single_qubit.single_shot import hist
from experiments.clifford_averager_program import CliffordAveragerProgram, CliffordEgGfAveragerProgram
from experiments.two_qubit.length_rabi_EgGf import LengthRabiEgGfProgram
from experiments.two_qubit.twoQ_state_tomography import AbstractStateTomo2QProgram, ErrorMitigationStateTomo1QProgram, ErrorMitigationStateTomo2QProgram, sort_counts, sort_counts_1q, correct_readout_err, fix_neg_counts, infer_gef_popln_2readout

import experiments.fitting as fitter
default_colors = plt.rcParams['axes.prop_cycle'].by_key()['color']

"""
Define matrices representing (all) Clifford gates for single
qubit in the basis of Z, X, Y, -Z, -X, -Y, indicating
where on the 6 cardinal points of the Bloch sphere the
+Z, +X, +Y axes go after each gate. Each Clifford gate
can be uniquely identified just by checking where +X and +Y
go.
"""
clifford_1q = dict()
clifford_1q['Z'] = np.matrix([[1, 0, 0, 0, 0, 0],
                             [0, 0, 0, 0, 1, 0],
                             [0, 0, 0, 0, 0, 1],
                             [0, 0, 0, 1, 0, 0],
                             [0, 1, 0, 0, 0, 0],
                             [0, 0, 1, 0, 0, 0]])
clifford_1q['X'] = np.matrix([[0, 0, 0, 1, 0, 0],
                             [0, 1, 0, 0, 0, 0],
                             [0, 0, 0, 0, 0, 1],
                             [1, 0, 0, 0, 0, 0],
                             [0, 0, 0, 0, 1, 0],
                             [0, 0, 1, 0, 0, 0]])
clifford_1q['Y'] = np.matrix([[0, 0, 0, 1, 0, 0],
                             [0, 0, 0, 0, 1, 0],
                             [0, 0, 1, 0, 0, 0],
                             [1, 0, 0, 0, 0, 0],
                             [0, 1, 0, 0, 0, 0],
                             [0, 0, 0, 0, 0, 1]])
clifford_1q['Z/2'] = np.matrix([[1, 0, 0, 0, 0, 0],
                             [0, 0, 0, 0, 0, 1],
                             [0, 1, 0, 0, 0, 0],
                             [0, 0, 0, 1, 0, 0],
                             [0, 0, 1, 0, 0, 0],
                             [0, 0, 0, 0, 1, 0]])
clifford_1q['X/2'] = np.matrix([[0, 0, 1, 0, 0, 0],
                             [0, 1, 0, 0, 0, 0],
                             [0, 0, 0, 1, 0, 0],
                             [0, 0, 0, 0, 0, 1],
                             [0, 0, 0, 0, 1, 0],
                             [1, 0, 0, 0, 0, 0]])
clifford_1q['Y/2'] = np.matrix([[0, 0, 0, 0, 1, 0],
                             [1, 0, 0, 0, 0, 0],
                             [0, 0, 1, 0, 0, 0],
                             [0, 1, 0, 0, 0, 0],
                             [0, 0, 0, 1, 0, 0],
                             [0, 0, 0, 0, 0, 1]])
clifford_1q['-Z/2'] = np.matrix([[1, 0, 0, 0, 0, 0],
                             [0, 0, 1, 0, 0, 0],
                             [0, 0, 0, 0, 1, 0],
                             [0, 0, 0, 1, 0, 0],
                             [0, 0, 0, 0, 0, 1],
                             [0, 1, 0, 0, 0, 0]])
clifford_1q['-X/2'] = np.matrix([[0, 0, 0, 0, 0, 1],
                             [0, 1, 0, 0, 0, 0],
                             [1, 0, 0, 0, 0, 0],
                             [0, 0, 1, 0, 0, 0],
                             [0, 0, 0, 0, 1, 0],
                             [0, 0, 0, 1, 0, 0]])
clifford_1q['-Y/2'] = np.matrix([[0, 1, 0, 0, 0, 0],
                             [0, 0, 0, 1, 0, 0],
                             [0, 0, 1, 0, 0, 0],
                             [0, 0, 0, 0, 1, 0],
                             [1, 0, 0, 0, 0, 0],
                             [0, 0, 0, 0, 0, 1]])
clifford_1q['I'] = np.diag([1]*6)

# Read pulse as a matrix product acting on state (meaning apply pulses in reverse order of the tuple)
two_step_pulses= [
    ('X','Z/2'), ('X/2','Z/2'), ('-X/2','Z/2'),
    ('Y','Z/2'), ('Y/2','Z/2'), ('-Y/2','Z/2'),
    ('X','Z'), ('X/2','Z'), ('-X/2','Z'),
    ('Y','Z'), ('Y/2','Z'), ('-Y/2','Z'),
    ('X','-Z/2'), ('X/2','-Z/2'), ('-X/2','-Z/2'),
    ('Y','-Z/2'), ('Y/2','-Z/2'), ('-Y/2','-Z/2'),
]
# Get rid of repeats
for pulse in two_step_pulses:
    new_mat = clifford_1q[pulse[0]] @ clifford_1q[pulse[1]]
    repeat = False
    for existing_pulse_name, existing_pulse in clifford_1q.items():
        if np.array_equal(new_mat, existing_pulse):
            # print(pulse, existing_pulse_name)
            repeat = True
    if not repeat: clifford_1q[pulse[0]+','+pulse[1]] = new_mat
clifford_1q_names = list(clifford_1q.keys())

for name, matrix in clifford_1q.items():
    z_new = np.argmax(matrix[:,0]) # +Z goes to row where col 0 is 1
    x_new = np.argmax(matrix[:,1]) # +X goes to row where col 1 is 1
    # print(name, z_new, x_new)
    clifford_1q[name] = (matrix, (z_new, x_new))

def gate_sequence(rb_depth, pulse_n_seq=None, debug=False):
    """
    Generate RB forward gate sequence of length rb_depth as a list of pulse names;
    also return the Clifford gate that is equivalent to the total pulse sequence.
    The effective inverse is pi phase + the total Clifford.
    Optionally, provide pulse_n_seq which is a list of the indices of the Clifford
    gates to apply in the sequence.
    """
    if pulse_n_seq == None: 
        pulse_n_seq = (len(clifford_1q_names)*np.random.rand(rb_depth)).astype(int)
    if debug: print('pulse seq', pulse_n_seq)
    pulse_name_seq = [clifford_1q_names[n] for n in pulse_n_seq]
    psi_nz = np.matrix([[1, 0, 0, 0, 0, 0]]).transpose()
    psi_nx = np.matrix([[0, 1, 0, 0, 0, 0]]).transpose()
    for n in pulse_n_seq: # n is index in clifford_1q_names
        gates = clifford_1q_names[n].split(',')
        for gate in reversed(gates): # Apply matrices from right to left of gates
            psi_nz = clifford_1q[gate][0] @ psi_nz
            psi_nx = clifford_1q[gate][0] @ psi_nx
    psi_nz = psi_nz.flatten()
    psi_nx = psi_nx.flatten()
    if debug: print('+Z axis after seq:', psi_nz, '+X axis after seq:', psi_nx)
    for clifford in clifford_1q_names: # Get the clifford equivalent to the total seq
        if clifford_1q[clifford][1] == (np.argmax(psi_nz), np.argmax(psi_nx)):
            total_clifford = clifford
            break
    if debug: print('Total gate matrix:\n', clifford_1q[total_clifford][0])
    return pulse_name_seq, total_clifford

def interleaved_gate_sequence(rb_depth, gate_char:str, debug=False):
    """
    Generate RB gate sequence with rb_depth random gates interleaved with gate_char
    Returns the total gate list (including the interleaved gates) and the total
    Clifford gate equivalent to the total pulse sequence.
    """
    pulse_n_seq_rand = (len(clifford_1q_names)*np.random.rand(rb_depth)).astype(int)
    pulse_n_seq = []
    assert gate_char in clifford_1q_names
    n_gate_char = clifford_1q_names.index(gate_char)
    if debug: print('n gate char:', n_gate_char, clifford_1q_names[n_gate_char])
    for n_rand in pulse_n_seq_rand:
        pulse_n_seq.append(n_rand)
        pulse_n_seq.append(n_gate_char)
    return gate_sequence(len(pulse_n_seq), pulse_n_seq=pulse_n_seq, debug=debug)    

if __name__ == '__main__':
    print('Clifford gates:', clifford_1q_names)
    print('Total number Clifford gates:', len(clifford_1q_names))
    pulse_name_seq, total_clifford = gate_sequence(2, debug=True)
    print('Pulse sequence:', pulse_name_seq)
    print('Total clifford of seq:', total_clifford)
    gate_char = 'X/2'
    print()
    print('Interleaved RB with gate', gate_char)
    pulse_name_seq, total_clifford = interleaved_gate_sequence(2, gate_char=gate_char, debug=True)
    print('Pulse sequence:', pulse_name_seq)
    print('Total clifford of seq:', total_clifford)

# ===================================================================== #

class SimultaneousRBProgram(CliffordAveragerProgram):
    """
    RB program for single qubit gates
    """

    def clifford(self, qubit, pulse_name:str, extra_phase=0, inverted=False, play=False):
        """
        Convert a clifford pulse name into the function that performs the pulse.
        If inverted, play the inverse of this gate (the extra phase is added on top of the inversion)
        """
        pulse_name = pulse_name.upper()
        assert pulse_name in clifford_1q_names
        gates = pulse_name.split(',')

        # Normally gates are applied right to left, but if inverted apply them left to right
        gate_order = reversed(gates)
        if inverted:
            gate_order = gates
        for gate in gate_order:
            pulse_func = None
            if gate == 'I': continue
            if 'X' in gate: pulse_func = self.X_pulse
            elif 'Y' in gate: pulse_func = self.Y_pulse
            elif 'Z' in gate: pulse_func = self.Z_pulse
            else: assert False, 'Invalid gate'

            neg = '-' in gate
            if inverted: neg = not neg
            pulse_func(qubit, pihalf='/2' in gate, neg=neg, extra_phase=extra_phase, play=play, reload=False) # very important to not reload unless necessary to save memory on the gen
            # print(self.overall_phase[qubit])

    def __init__(self, soccfg, cfg, gate_list, qubit_list):
        # gate_list should include the total gate!
        # qubit_list should specify the qubit on which each random gate will be applied
        self.gate_list = gate_list
        self.qubit_list = qubit_list
        super().__init__(soccfg, cfg)

    def body(self):
        # Phase reset all channels except readout DACs (since mux ADCs can't be phase reset)
        self.reset_and_sync()

        # Do all the gates given in the initialize except for the total gate, measure
        cfg=AttrDict(self.cfg)
        for i in range(len(self.gate_list) - 1):
            self.clifford(qubit=self.qubit_list[i], pulse_name=self.gate_list[i], play=True)
            self.sync_all()

        # Do the inverse by applying the total gate with pi phase
        # This is actually wrong!!! need to apply an inverse total gate for each qubit!!
        self.clifford(qubit=self.qubit_list[-1], pulse_name=self.gate_list[-1], inverted=True, play=True)
        self.sync_all() # align channels and wait 10ns

        self.measure(
            pulse_ch=self.measure_chs, 
            adcs=self.adc_chs,
            adc_trig_offset=cfg.device.readout.trig_offset[0],
            wait=True,
            syncdelay=self.us2cycles(max([cfg.device.readout.relax_delay[q] for q in self.qubits])))

# ===================================================================== #

class SimultaneousRBExperiment(Experiment):
    """
    Simultaneous Randomized Benchmarking Experiment
    Experimental Config:
    expt = dict(
        start: rb depth start - for interleaved RB, depth specifies the number of random gates
        step: step rb depth
        expts: number steps
        reps: number averages per unique sequence
        variations: number different sequences per depth
        gate_char: a single qubit clifford gate (str) to characterize. If not None, runs interleaved RB instead of regular RB.
        qubits: the qubits to perform simultaneous RB on. If using EgGf subspace, specify just qA (where qA, qB represents the Eg->Gf qubits)
        singleshot_reps: reps per state for singleshot calibration
        post_process: 'threshold' (uses single shot binning), 'scale' (scale by ge_avgs), or None
        thresholds: (optional) don't rerun singleshot and instead use this
        ge_avgs: (optional) don't rerun singleshot and instead use this
        angles: (optional) don't rerun singleshot and instead use this
    )
    """

    def __init__(self, soccfg=None, path='', prefix='SimultaneousRB', config_file=None, progress=None):
        super().__init__(path=path, soccfg=soccfg, prefix=prefix, config_file=config_file, progress=progress)

    def acquire(self, progress=False, debug=False):
        qubits = self.cfg.expt.qubits

        # expand entries in config that are length 1 to fill all qubits
        num_qubits_sample = len(self.cfg.device.qubit.f_ge)
        for subcfg in (self.cfg.device.readout, self.cfg.device.qubit, self.cfg.hw.soc):
            for key, value in subcfg.items() :
                if isinstance(value, dict):
                    for key2, value2 in value.items():
                        for key3, value3 in value2.items():
                            if not(isinstance(value3, list)):
                                value2.update({key3: [value3]*num_qubits_sample})                                
                elif not(isinstance(value, list)):
                    subcfg.update({key: [value]*num_qubits_sample})


        if self.cfg.expt.use_EgGf_subspace:
            assert False, 'use the RbEgGfExperiment!'
            qA, qB = qubits
            qSort = qA
            if qA == 1: qSort = qB
            qDrive = 1
            if 'qDrive' in self.cfg.expt and self.cfg.expt.qDrive is not None:
                qDrive = self.cfg.expt.qDrive
            qNotDrive = -1
            if qA == qDrive: qNotDrive = qB
            else: qNotDrive = qA
            self.qDrive = qDrive
            self.qNotDrive = qNotDrive

        # ================= #
        # Get single shot calibration for all qubits
        # ================= #
        data={'counts_calib':[], 'counts_raw':[]}

        thresholds_q = ge_avgs_q = angles_q = fids_q = None
        if 'post_process' not in self.cfg.expt.keys(): # threshold or scale
            self.cfg.expt.post_process = None

        self.calib_order = ['gg', 'ge', 'eg', 'ee']
        if 'angles' in self.cfg.expt and 'thresholds' in self.cfg.expt and 'ge_avgs' in self.cfg.expt and 'counts_calib' in self.cfg.expt:
            angles_q = self.cfg.expt.angles
            thresholds_q = self.cfg.expt.thresholds
            ge_avgs_q = np.asarray(self.cfg.expt.ge_avgs)
            data['counts_calib'] = self.cfg.expt.counts_calib
            print('Re-using provided angles, thresholds, ge_avgs')
        else:
            thresholds_q = [0]*4
            ge_avgs_q = [np.zeros(4), np.zeros(4), np.zeros(4), np.zeros(4)]
            angles_q = [0]*4
            fids_q = [0]*4

            # We really just need the single shot plots here, but convenient to use the ErrorMitigation tomo to do it
            sscfg = AttrDict(deepcopy(self.cfg))
            sscfg.expt.reps = sscfg.expt.singleshot_reps
            sscfg.expt.tomo_qubits = self.cfg.expt.qubits
            sscfg.expt.tomo_qubits = [sscfg.expt.tomo_qubits[0], (sscfg.expt.tomo_qubits[0] + 1) % 4] # super hacky way to just add another qubit so can use the error tomo program to do single shot calib
            qA, qB = sscfg.expt.tomo_qubits

            calib_prog_dict = dict()
            for prep_state in tqdm(self.calib_order):
                # print(prep_state)
                sscfg.expt.state_prep_kwargs = dict(prep_state=prep_state, apply_q1_pi2=False)
                err_tomo = ErrorMitigationStateTomo2QProgram(soccfg=self.soccfg, cfg=sscfg)
                err_tomo.acquire(self.im[sscfg.aliases.soc], load_pulses=True, progress=False)
                calib_prog_dict.update({prep_state:err_tomo})

            g_prog = calib_prog_dict['gg']
            Ig, Qg = g_prog.get_shots(verbose=False)

            # Get readout angle + threshold for qubits
            for qi, q in enumerate(sscfg.expt.tomo_qubits):
                calib_e_state = 'gg'
                calib_e_state = calib_e_state[:qi] + 'e' + calib_e_state[qi+1:]
                e_prog = calib_prog_dict[calib_e_state]
                Ie, Qe = e_prog.get_shots(verbose=False)
                shot_data = dict(Ig=Ig[q], Qg=Qg[q], Ie=Ie[q], Qe=Qe[q])
                print(f'Qubit  ({q})')
                fid, threshold, angle = hist(data=shot_data, plot=True, verbose=False)
                thresholds_q[q] = threshold[0]
                ge_avgs_q[q] = [np.average(Ig[q]), np.average(Qg[q]), np.average(Ie[q]), np.average(Qe[q])]
                angles_q[q] = angle
                fids_q[q] = fid[0]
                print(f'ge fidelity (%): {100*fid[0]}')
            
            # Process the shots taken for the confusion matrix with the calibration angles
            for prep_state in self.calib_order:
                counts = calib_prog_dict[prep_state].collect_counts(angle=angles_q, threshold=thresholds_q)
                data['counts_calib'].append(counts)

            print(f'thresholds={thresholds_q},')
            print(f'angles={angles_q},')
            print(f'ge_avgs={ge_avgs_q},')
            print(f"counts_calib={np.array(data['counts_calib']).tolist()}")

            data['thresholds'] = thresholds_q
            data['angles'] = angles_q
            data['ge_avgs'] = ge_avgs_q
            data['counts_calib'] = np.array(data['counts_calib'])

        # ================= #
        # Begin RB
        # ================= #

        if 'shot_avg' not in self.cfg.expt: self.cfg.expt.shot_avg=1
        data.update({"xpts":[], "popln":[], "popln_err":[]})

        depths = self.cfg.expt.start + self.cfg.expt.step * np.arange(self.cfg.expt.expts)
        for depth in tqdm(depths):
            # print(f'depth {depth} gate list (last gate is the total gate)')
            data['xpts'].append([])
            data["popln"].append([])
            data["popln_err"].append([])
            for var in range(self.cfg.expt.variations):
                if 'gate_char' in self.cfg.expt and self.cfg.expt.gate_char is not None:
                    gate_list, total_gate = interleaved_gate_sequence(depth, gate_char=self.cfg.expt.gate_char)
                else: gate_list, total_gate = gate_sequence(depth)
                gate_list.append(total_gate) # make sure to do the inverse gate

                # print(gate_list)

                # gate_list = ['X', '-X/2,Z', 'Y/2', '-X/2,-Z/2', '-Y/2,Z', '-Z/2', 'X', 'Y']
                # gate_list = ['X/2,Z/2', 'X/2,Z/2']
                # gate_list = ['I', 'I']
                # gate_list = ['X', 'I']
                # gate_list = ['X', '-X/2,Z', 'X/2']
                # gate_list = ['X', '-X/2,Z', 'Y/2', 'X/2']
                # gate_list = ['X', '-X/2,Z', 'Y/2', '-X/2,-Z/2', '-Y/2']

                # gate_list = ['X/2']*depth
                # if depth % 4 == 0: gate_list.append('I')
                # elif depth % 4 == 1: gate_list.append('X/2')
                # elif depth % 4 == 2: gate_list.append('X')
                # elif depth % 4 == 3: gate_list.append('-X/2')

                # print('variation', var)
                qubit_list = np.random.choice(self.cfg.expt.qubits, size=len(gate_list)-1)

                randbench = SimultaneousRBProgram(soccfg=self.soccfg, cfg=self.cfg, gate_list=gate_list, qubit_list=qubit_list)
                # print(randbench)
                # from qick.helpers import progs2json
                # print(progs2json([randbench.dump_prog()]))

                # print('angles', angles_q)
                # print('ge avgs', ge_avgs_q)
                # angles_q = thresholds_q = ge_avgs_q = None

                # print(gate_list)
                assert self.cfg.expt.post_process is not None, 'need post processing for RB to make sense!'
                popln, popln_err = randbench.acquire_rotated(soc=self.im[self.cfg.aliases.soc], progress=False, angle=angles_q, threshold=thresholds_q, ge_avgs=ge_avgs_q, post_process=self.cfg.expt.post_process)

                adcDrive_ch = self.cfg.hw.soc.adcs.readout.ch[qubits[0]]
                adcNotDrive_ch = self.cfg.hw.soc.adcs.readout.ch[(qubits[0] + 1)%4]

                if self.cfg.expt.post_process == 'threshold':
                    shots, _ = randbench.get_shots(angle=angles_q, threshold=thresholds_q)
                    # 00, 01, 10, 11
                    counts = np.array([sort_counts(shots[adcDrive_ch], shots[adcNotDrive_ch])])
                    data['counts_raw'].append(counts)
                    counts = fix_neg_counts(correct_readout_err(counts, data['counts_calib']))
                    counts = counts[0] # go back to just 1d array
                    data["popln"][-1].append((counts[2] + counts[3])/sum(counts))
                    # print('gate list', gate_list, 'popln', data["popln"][-1][-1])
                else:
                    data["popln"][-1].append(popln[adcDrive_ch])
                    # print(depth, var, iq, avgi)
                    data["popln_err"][-1].append(popln_err[adcDrive_ch])
                data['xpts'][-1].append(depth)

                # try:
                #     avgi, avgi_err = randbench.acquire_rotated(soc=self.im[self.cfg.aliases.soc], progress=False, angle=angles_q, threshold=thresholds_q, ge_avgs=ge_avgs_q, post_process=post_process)
                #     for iq, q in enumerate(qubits):
                #         avgi = avgi[adc_chs[q]]
                #         data["avgi"][iq][-1].append(avgi)
                #         # print(depth, var, iq, avgi)
                #         data["avgi_err"][iq][-1].append(avgi_err[adc_chs[q]])
                #     data['xpts'][-1].append(depth)
                # except Exception as e:
                #     print(e)
                #     print('Varation', var, 'failed in depth', depth)
                #     continue


                # print(1-data['avgi'][0][-1], gate_list)
            # data['xpts'].append(depth)

        # for k, arr in data.items():
        #     if isinstance(arr, tuple):
        #         data[k]=(np.array(a) for a in arr)
        #     else: data[k] = np.array(arr)
        # print(data['avgi'])
        for k, a in data.items():
            data[k] = np.array(a)
        # print(np.shape(data['avgi'][iq]))

        self.data=data
        return data

    def analyze(self, data=None, fit=True, **kwargs):
        if data is None:
            data=self.data
        
        qubits = self.cfg.expt.qubits
        data['probs'] = [None] * len(qubits)
        data['fit'] = [None] * len(qubits)
        data['fit_err'] = [None] * len(qubits)
        data['error'] = [100.] * len(qubits)
        data['std_dev_probs'] = [None] * len(qubits)
        data['med_probs'] = [None] * len(qubits)
        data['avg_probs'] = [None] * len(qubits)

        probs = np.zeros_like(data['popln'])
        for depth in range(len(data['popln'])):
            probs[depth] = 1 - np.asarray(data['popln'][depth])
        probs = np.asarray(probs)
        data['xpts'] = np.asarray(data['xpts'])
        data['probs'] = probs
        # probs = np.reshape(probs, (self.cfg.expt.expts, self.cfg.expt.variations))
        std_dev_probs = []
        med_probs = []
        avg_probs = []
        working_depths = []
        depths = data['xpts']
        for depth in range(len(probs)):
            probs_depth = probs[depth]
            if len(probs_depth) > 0:
                std_dev_probs.append(np.std(probs_depth))
                med_probs.append(np.median(probs_depth))
                avg_probs.append(np.average(probs_depth))
                working_depths.append(depths[depth][0])
        std_dev_probs = np.asarray(std_dev_probs)
        med_probs = np.asarray(med_probs)
        avg_probs = np.asarray(avg_probs)
        working_depths = np.asarray(working_depths)
        flat_depths = np.concatenate(depths)
        flat_probs = np.concatenate(data['probs'])
        # depths = self.cfg.expt.start + self.cfg.expt.step * np.arange(self.cfg.expt.expts)
        # popt, pcov = fitter.fitrb(depths[:-4], med_probs[:-4])
        # popt, pcov = fitter.fitrb(depths, med_probs)
        # print(working_depths, avg_probs)
        # popt, pcov = fitter.fitrb(working_depths, avg_probs)
        data['std_dev_probs'] = std_dev_probs
        data['med_probs'] = med_probs
        data['avg_probs'] = avg_probs
        data['working_depths'] = working_depths
        if fit:
            popt, pcov = fitter.fitrb(flat_depths, flat_probs)
            data['fit'] = popt
            data['fit_err'] = pcov
            data['error'] = fitter.rb_error(popt[0], d=2)
        return data

    def display(self, data=None, fit=True, **kwargs):
        if data is None:
            data=self.data 
        
        plt.figure(figsize=(8,6))
        irb = 'gate_char' in self.cfg.expt and self.cfg.expt.gate_char is not None
        title = f'{"Interleaved " + self.cfg.expt.gate_char + " Gate" if irb else ""} RB on Q{self.cfg.expt.qubits[0]}'

        plt.subplot(111, title=title, xlabel="Sequence Depth", ylabel="Population in g")
        depths = data['xpts']
        flat_depths = np.concatenate(depths)
        flat_probs = np.concatenate(data['probs'])
        plt.plot(flat_depths, flat_probs, 'x', color='tab:grey')

        probs_vs_depth = data['probs']
        std_dev_probs = data['std_dev_probs']
        med_probs = data['med_probs']
        avg_probs = data['avg_probs']
        working_depths = data['working_depths']
        # plt.errorbar(working_depths, avg_probs, fmt='o', yerr=2*std_dev_probs, color='k', elinewidth=0.75)
        plt.errorbar(working_depths, med_probs, fmt='o', yerr=std_dev_probs, color='k', elinewidth=0.75)

        if fit:
            cov_p = data['fit_err'][0][0]
            fit_plt_xpts = range(working_depths[-1]+1)
            # plt.plot(depths, avg_probs, 'o-', color='tab:blue')
            plt.plot(fit_plt_xpts, fitter.rb_func(fit_plt_xpts, *data["fit"]))
            print(f'Running {"interleaved " + self.cfg.expt.gate_char + " gate" if irb else "regular"} RB')
            print(f'Depolarizing parameter p from fit: {data["fit"][0]} +/- {np.sqrt(cov_p)}')
            print(f'Average RB gate error: {data["error"]} +/- {np.sqrt(fitter.error_fit_err(cov_p, 2**(len(self.cfg.expt.qubits))))}')
            print(f'\tFidelity=1-error: {1-data["error"]} +/- {np.sqrt(fitter.error_fit_err(cov_p, 2**(len(self.cfg.expt.qubits))))}')

        plt.grid(linewidth=0.3)
        # if self.cfg.expt.post_process is not None: plt.ylim(-0.1, 1.1)
        plt.ylim(-0.1, 1.1)
        plt.show()
    
    def save_data(self, data=None):
        print(f'Saving {self.fname}')
        super().save_data(data=data)
        with self.datafile() as f:
            f.attrs['calib_order'] = json.dumps(self.calib_order, cls=NpEncoder)
        return self.fname

# ===================================================================== #

class RBEgGfProgram(CliffordEgGfAveragerProgram):
    """
    RB program for single qubit gates, treating the Eg/Gf subspace as the TLS
    """

    def __init__(self, soccfg, cfg, gate_list, qubits, qDrive):
        # gate_list should include the total gate!
        # qA should specify the the qubit that is not q1 for the Eg-Gf swap
        self.gate_list = gate_list
        self.cfg = cfg

        qA, qB = qubits
        qSort = qA
        if qA == 1: qSort = qB
        qDrive = 1
        if 'qDrive' in self.cfg.expt and self.cfg.expt.qDrive is not None:
            qDrive = self.cfg.expt.qDrive
        qNotDrive = -1
        if qA == qDrive: qNotDrive = qB
        else: qNotDrive = qA
        self.qDrive = qDrive
        self.qNotDrive = qNotDrive
        self.qSort = qSort
        super().__init__(soccfg, cfg)

    def cliffordEgGf(self, qDrive, qNotDrive, pulse_name:str, extra_phase=0, add_virtual_Z=False, inverted=False, play=False):
        """
        Convert a clifford pulse name (in the Eg-Gf subspace) into the function that performs the pulse.
        swap_phase defines the additional phase needed to calibrate each swap
        If inverted, play the inverse of this gate (the extra phase is added on top of the inversion)
        """
        pulse_name = pulse_name.upper()
        assert pulse_name in clifford_1q_names
        gates = pulse_name.split(',')

        # Normally gates are applied right to left, but if inverted apply them left to right
        gate_order = reversed(gates)
        if inverted:
            gate_order = gates
        for gate in gate_order:
            pulse_func = None
            if gate == 'I': continue
            if 'X' in gate:
                pulse_func = self.XEgGf_pulse
            elif 'Y' in gate:
                pulse_func = self.YEgGf_pulse
            elif 'Z' in gate:pulse_func = self.ZEgGf_pulse
            else: assert False, 'Invalid gate'

            neg = '-' in gate
            if inverted: neg = not neg
            pulse_func(qDrive=qDrive, qNotDrive=qNotDrive, pihalf='/2' in gate, neg=neg, extra_phase=extra_phase, add_virtual_Z=add_virtual_Z, play=play, reload=False)
            # print(self.overall_phase[qubit])

    def body(self):
        cfg=AttrDict(self.cfg)

        self.reset_and_sync()

        if 'cool_qubits' in self.cfg.expt and self.cfg.expt.cool_qubits is not None:
            cool_idle = [self.cfg.device.qubit.pulses.pi_f0g1.idle[q] for q in self.cfg.expt.cool_qubits]
            if 'cool_idle' in self.cfg.expt and self.cfg.expt.cool_idle is not None:
                cool_idle = self.cfg.expt.cool_idle
            self.active_cool(cool_qubits=self.cfg.expt.cool_qubits, cool_idle=cool_idle)

        # Get into the Eg-Gf subspace
        self.X_pulse(self.qNotDrive, extra_phase=-self.overall_phase[self.qSort], pihalf=False, play=True) # this is the g->e pulse from CliffordAveragerProgram, always have the "overall phase" of the normal qubit subspace be 0 because it is just a state prep pulse
        self.sync_all()

        # self.setup_and_pulse(ch=self.qubit_chs[1], style='arb', freq=self.f_Q1_ZZ_regs[self.qA], phase=self.deg2reg(-90, gen_ch=self.qA), gain=self.cfg.device.qubit.pulses.pi_Q1_ZZ.gain[self.qA] // 2, waveform=f'qubit1_ZZ{self.qA}')
        # self.sync_all(10)

        add_virtual_Z = False
        if 'add_phase' in self.cfg.expt and self.cfg.expt.add_phase: 
            add_virtual_Z = True

        # Do all the gates given in the initialize except for the total gate
        for i in range(len(self.gate_list) - 1):
            self.cliffordEgGf(qDrive=self.qDrive, add_virtual_Z=add_virtual_Z, qNotDrive=self.qNotDrive, pulse_name=self.gate_list[i], play=True)
            self.sync_all()

        # self.Xef_pulse(q=1, play=True)
        # qB = 1
        # self.setup_and_pulse(ch=self.qubit_chs[qB], style="arb", freq=self.f_ef_regs[qB], phase=0, gain=cfg.device.qubit.pulses.pi_ef.gain[qB], waveform=f"pi_ef_qubit{qB}") #, phrst=1)

        # Do the inverse by applying the total gate with pi phase
        self.cliffordEgGf(qDrive=self.qDrive, add_virtual_Z=add_virtual_Z, qNotDrive=self.qNotDrive, pulse_name=self.gate_list[-1], inverted=True, play=True)
        self.sync_all()

        # Measure the population of just the e state when e/f are not distinguishable - check the g population
        setup_measure = None
        if 'setup_measure' in self.cfg.expt: setup_measure = self.cfg.expt.setup_measure
        if setup_measure != None and 'qDrive_ge' in setup_measure:
            self.X_pulse(q=self.qDrive, play=True) # not sure whether needs some phase adjustment

        # Bring Gf to Ge, or stays in same state.
        if setup_measure != None and 'qDrive_ef' in setup_measure:
            self.Xef_pulse(self.qDrive, play=True) # not sure whether needs some phase adjustment

        self.measure(
            pulse_ch=self.measure_chs, 
            adcs=self.adc_chs,
            adc_trig_offset=cfg.device.readout.trig_offset[0],
            wait=True,
            syncdelay=self.us2cycles(max([cfg.device.readout.relax_delay[q] for q in self.qubits])))

# ===================================================================== #

class SimultaneousRBEgGfExperiment(Experiment):
    """
    Simultaneous Randomized Benchmarking Experiment
    Experimental Config:
    expt = dict(
        start: rb depth start - for interleaved RB, depth specifies the number of random gates
        step: step rb depth
        expts: number steps
        reps: number averages per unique sequence
        variations: number different sequences per depth
        gate_char: a single qubit clifford gate (str) to characterize. If not None, runs interleaved RB instead of regular RB.
        use_EgGf_subspace: specifies whether to run RB treating EgGf as the TLS subspace
        qubits: the qubits to perform simultaneous RB on. If using EgGf subspace, specify just qA (where qA, qB represents the Eg->Gf qubits)
        singleshot_reps: reps per state for singleshot calibration
        post_process: 'threshold' (uses single shot binning), 'scale' (scale by ge_avgs), or None
        measure_f: qubit: if not None, calibrates the single qubit f state measurement on this qubit and also runs the measurement twice to distinguish e and f states
        thresholds: (optional) don't rerun singleshot and instead use this
        ge_avgs: (optional) don't rerun singleshot and instead use this
        angles: (optional) don't rerun singleshot and instead use this
    )
    """

    def __init__(self, soccfg=None, path='', prefix='SimultaneousRBEgGf', config_file=None, progress=None):
        super().__init__(path=path, soccfg=soccfg, prefix=prefix, config_file=config_file, progress=progress)

    def acquire(self, progress=False, debug=False):
        qubits = self.cfg.expt.qubits

        # expand entries in config that are length 1 to fill all qubits
        num_qubits_sample = len(self.cfg.device.qubit.f_ge)
        for subcfg in (self.cfg.device.readout, self.cfg.device.qubit, self.cfg.hw.soc):
            for key, value in subcfg.items() :
                if isinstance(value, dict):
                    for key2, value2 in value.items():
                        for key3, value3 in value2.items():
                            if not(isinstance(value3, list)):
                                value2.update({key3: [value3]*num_qubits_sample})                                
                elif not(isinstance(value, list)):
                    subcfg.update({key: [value]*num_qubits_sample})

        qA, qB = self.cfg.expt.qubits
        self.measure_f = False
        if self.cfg.expt.measure_f is not None and len(self.cfg.expt.measure_f) >= 0:
            self.measure_f = True
            assert len(self.cfg.expt.measure_f) == 1
            q_measure_f = self.cfg.expt.measure_f[0]
            q_other = qA if q_measure_f == qB else qB
            # Need to make sure qubits are in the right order for all of the calibrations if we want to measure f! Let's just rename the cfg.expt.qubits so it's easy for the rest of this.
            self.cfg.expt.qubits = [q_other, q_measure_f]
        qA, qB = self.cfg.expt.qubits

        qA, qB = qubits
        qSort = qA
        if qA == 1: qSort = qB
        qDrive = 1
        if 'qDrive' in self.cfg.expt and self.cfg.expt.qDrive is not None:
            qDrive = self.cfg.expt.qDrive
        qNotDrive = -1
        if qA == qDrive: qNotDrive = qB
        else: qNotDrive = qA
        self.qDrive = qDrive
        self.qNotDrive = qNotDrive
        
        # ================= #
        # Get single shot calibration for all qubits
        # ================= #
        data={'counts_calib':[], 'counts_raw':[[]]}
        if self.cfg.expt.measure_f is not None:
            for i in range(len(self.cfg.expt.measure_f)): # measure g of everybody, second measurement of each measure_f qubit using the g/f readout
                data['counts_raw'].append([])

        thresholds_q = ge_avgs_q = angles_q = fids_q = None
        if 'post_process' not in self.cfg.expt.keys(): # threshold or scale
            self.cfg.expt.post_process = None

        self.calib_order = ['gg', 'ge', 'eg', 'ee']
        if self.measure_f: self.calib_order += ['gf', 'ef'] # assumes the 2nd qubit is the measure_f

        if 'angles' in self.cfg.expt and 'thresholds' in self.cfg.expt and 'ge_avgs' in self.cfg.expt and 'counts_calib' in self.cfg.expt:
            angles_q = self.cfg.expt.angles
            thresholds_q = self.cfg.expt.thresholds
            ge_avgs_q = np.asarray(self.cfg.expt.ge_avgs)
            data['counts_calib'] = self.cfg.expt.counts_calib
            print('Re-using provided angles, thresholds, ge_avgs')
        else:
            thresholds_q = [0]*4
            ge_avgs_q = [np.zeros(4), np.zeros(4), np.zeros(4), np.zeros(4)]
            angles_q = [0]*4
            fids_q = [0]*4

            # We really just need the single shot plots here, but convenient to use the ErrorMitigation tomo to do it
            sscfg = AttrDict(deepcopy(self.cfg))
            sscfg.expt.reps = sscfg.expt.singleshot_reps
            sscfg.expt.tomo_qubits = self.cfg.expt.qubits
            qA, qB = sscfg.expt.tomo_qubits

            calib_prog_dict = dict()
            for prep_state in tqdm(self.calib_order):
                # print(prep_state)
                sscfg.expt.state_prep_kwargs = dict(prep_state=prep_state, apply_q1_pi2=False)
                err_tomo = ErrorMitigationStateTomo2QProgram(soccfg=self.soccfg, cfg=sscfg)
                err_tomo.acquire(self.im[sscfg.aliases.soc], load_pulses=True, progress=False)
                calib_prog_dict.update({prep_state:err_tomo})

            g_prog = calib_prog_dict['gg']
            Ig, Qg = g_prog.get_shots(verbose=False)

            # Get readout angle + threshold for qubits
            for qi, q in enumerate(sscfg.expt.tomo_qubits):
                calib_e_state = 'gg'
                calib_e_state = calib_e_state[:qi] + 'e' + calib_e_state[qi+1:]
                e_prog = calib_prog_dict[calib_e_state]
                Ie, Qe = e_prog.get_shots(verbose=False)
                shot_data = dict(Ig=Ig[q], Qg=Qg[q], Ie=Ie[q], Qe=Qe[q])
                print(f'Qubit ({q}) ge')
                fid, threshold, angle = hist(data=shot_data, plot=True, verbose=False)
                thresholds_q[q] = threshold[0]
                ge_avgs_q[q] = [np.average(Ig[q]), np.average(Qg[q]), np.average(Ie[q]), np.average(Qe[q])]
                angles_q[q] = angle
                fids_q[q] = fid[0]
                print(f'ge fidelity (%): {100*fid[0]}')

            # Process the shots taken for the confusion matrix with the calibration angles
            for prep_state in self.calib_order:
                counts = calib_prog_dict[prep_state].collect_counts(angle=angles_q, threshold=thresholds_q)
                data['counts_calib'].append(counts)

            print(f'thresholds={thresholds_q},')
            print(f'angles={angles_q},')
            print(f'ge_avgs={ge_avgs_q},')
            print(f"counts_calib={np.array(data['counts_calib']).tolist()}")

            data['thresholds'] = thresholds_q
            data['angles'] = angles_q
            data['ge_avgs'] = ge_avgs_q
            data['counts_calib'] = np.array(data['counts_calib'])

            
        # ================= #
        # Begin RB
        # ================= #

        if 'shot_avg' not in self.cfg.expt: self.cfg.expt.shot_avg=1
        data.update({"xpts":[]})

        depths = self.cfg.expt.start + self.cfg.expt.step * np.arange(self.cfg.expt.expts)
        gate_list_variations = [None]*len(depths)
        if 'loops' not in self.cfg.expt: self.cfg.expt.loops = 1
        print('running', self.cfg.expt.loops, 'loops')
        for loop in tqdm(range(self.cfg.expt.loops), disable=not progress or self.cfg.expt.loops == 1):
            for i_depth, depth in enumerate(tqdm(depths, disable=not progress or self.cfg.expt.loops > 1)):
                # print(f'depth {depth} gate list (last gate is the total gate)')
                if loop == 0:
                    data['xpts'].append([])
                    gate_list_variations[i_depth] = []
                for var in range(self.cfg.expt.variations):
                    if loop == 0:
                        if 'gate_char' in self.cfg.expt and self.cfg.expt.gate_char is not None:
                            gate_list, total_gate = interleaved_gate_sequence(depth, gate_char=self.cfg.expt.gate_char)
                        else: gate_list, total_gate = gate_sequence(depth)
                        gate_list.append(total_gate) # make sure to do the inverse gate

                        # gate_list = ['X', '-X/2,Z', 'Y/2', '-X/2,-Z/2', '-Y/2,Z', '-Z/2', 'X', 'Y']
                        # gate_list = ['X', 'X', 'I']
                        # print('variation', var)
                        # print(gate_list)
                        # gate_list = ['X/2', 'Z/2', '-Y/2', 'I']

                        gate_list_variations[i_depth].append(gate_list)
                    else: gate_list = gate_list_variations[i_depth][var]

                    randbench = RBEgGfProgram(soccfg=self.soccfg, cfg=self.cfg, gate_list=gate_list, qubits=self.cfg.expt.qubits, qDrive=self.cfg.expt.qDrive)
                    # print(randbench)
                    # from qick.helpers import progs2json
                    # print(progs2json([randbench.dump_prog()]))

                    assert self.cfg.expt.post_process is not None, 'need post processing for RB to make sense!'
                    popln, popln_err = randbench.acquire_rotated(soc=self.im[self.cfg.aliases.soc], progress=False, angle=angles_q, threshold=thresholds_q, ge_avgs=ge_avgs_q, post_process=self.cfg.expt.post_process)
                    assert self.cfg.expt.post_process == 'threshold', 'Can only bin EgGf RB properly using threshold'

                    adcDrive_ch = self.cfg.hw.soc.adcs.readout.ch[qDrive]
                    adcNotDrive_ch = self.cfg.hw.soc.adcs.readout.ch[qNotDrive]

                    if self.cfg.expt.post_process == 'threshold':
                        shots, _ = randbench.get_shots(angle=angles_q, threshold=thresholds_q)
                        # 00, 01, 10, 11
                        counts = np.array([sort_counts(shots[adcNotDrive_ch], shots[adcDrive_ch])])
                        data['counts_raw'][0].append(counts)
                        # print('variation', var, 'gate list', gate_list, 'counts', counts)

                    if loop == 0: data['xpts'][-1].append(depth)

        # ================= #
        # Measure the same thing with g/f distinguishing
        # ================= #

        if self.measure_f:
            data.update({'counts_calib_f':[]})

            # ================= #
            # Get f state single shot calibration (this must be re-run if you just ran measurement with the standard readout)
            # ================= #

            thresholds_f_q = [0]*4
            gf_avgs_q = [np.zeros(4), np.zeros(4), np.zeros(4), np.zeros(4)]
            angles_f_q = [0]*4
            fids_f_q = [0]*4

            # We really just need the single shot plots here, but convenient to use the ErrorMitigation tomo to do it
            sscfg = AttrDict(deepcopy(self.cfg))
            sscfg.expt.reps = sscfg.expt.singleshot_reps
            sscfg.expt.tomo_qubits = self.cfg.expt.qubits # the order of this was set earlier in code so 2nd qubit is the measure f qubit
            sscfg.device.readout.frequency[q_measure_f] = sscfg.device.readout.frequency_ef[q_measure_f]
            sscfg.device.readout.readout_length[q_measure_f] = sscfg.device.readout.readout_length_ef[q_measure_f]

            calib_prog_dict = dict()
            for prep_state in tqdm(self.calib_order):
                # print(prep_state)
                sscfg.expt.state_prep_kwargs = dict(prep_state=prep_state, apply_q1_pi2=False)
                err_tomo = ErrorMitigationStateTomo2QProgram(soccfg=self.soccfg, cfg=sscfg)
                err_tomo.acquire(self.im[sscfg.aliases.soc], load_pulses=True, progress=False)
                calib_prog_dict.update({prep_state:err_tomo})

            g_prog = calib_prog_dict['gg']
            Ig, Qg = g_prog.get_shots(verbose=False)

            # Get readout angle + threshold for qubits to distinguish g/f on one of the qubits
            for qi, q in enumerate(sscfg.expt.tomo_qubits):
                calib_f_state = 'gg'
                calib_f_state = calib_f_state[:qi] + f'{"f" if q == q_measure_f else "e"}' + calib_f_state[qi+1:]
                f_prog = calib_prog_dict[calib_f_state]
                If, Qf = f_prog.get_shots(verbose=False)
                shot_data = dict(Ig=Ig[q], Qg=Qg[q], Ie=If[q], Qe=Qf[q])
                print(f'Qubit ({q}){f" gf" if q == q_measure_f else " ge"}')
                fid, threshold, angle = hist(data=shot_data, plot=True, verbose=False)
                thresholds_f_q[q] = threshold[0]
                gf_avgs_q[q] = [np.average(Ig[q]), np.average(Qg[q]), np.average(If[q]), np.average(Qf[q])]
                angles_f_q[q] = angle
                fids_f_q[q] = fid[0]
                print(f'{"gf" if q == q_measure_f else "ge"} fidelity (%): {100*fid[0]}')

            # Process the shots taken for the confusion matrix with the calibration angles
            for prep_state in self.calib_order:
                counts = calib_prog_dict[prep_state].collect_counts(angle=angles_f_q, threshold=thresholds_f_q)
                data['counts_calib_f'].append(counts)

            print(f'thresholds_f={thresholds_f_q},')
            print(f'angles_f={angles_f_q},')
            print(f'gf_avgs={gf_avgs_q},')
            print(f"counts_calib_f={np.array(data['counts_calib_f']).tolist()}")

            data['thresholds_f'] = thresholds_f_q
            data['angles_f'] = angles_f_q
            data['gf_avgs'] = gf_avgs_q
            data['counts_calib_f'] = np.array(data['counts_calib_f'])

            # ================= #
            # Begin RB for measure f, using same gate list as measure with g/e
            # ================= #

            assert q_measure_f == qDrive, 'this code assumes we will be processing to distinguish gf from ge'
            for loop in tqdm(range(self.cfg.expt.loops), disable=not progress or self.cfg.expt.loops == 1):
                for i_depth, depth in enumerate(tqdm(depths, disable=not progress or self.cfg.expt.loops > 1)):
                    for var in range(self.cfg.expt.variations):
                        gate_list = gate_list_variations[i_depth][var]

                        rbcfg = deepcopy(self.cfg)
                        rbcfg.device.readout.frequency[q_measure_f] = rbcfg.device.readout.frequency_ef[q_measure_f]
                        rbcfg.device.readout.readout_length[q_measure_f] = rbcfg.device.readout.readout_length_ef[q_measure_f]

                        randbench = RBEgGfProgram(soccfg=self.soccfg, cfg=rbcfg, gate_list=gate_list, qubits=self.cfg.expt.qubits, qDrive=self.cfg.expt.qDrive)
                        # print(randbench)
                        # from qick.helpers import progs2json
                        # print(progs2json([randbench.dump_prog()]))

                        assert self.cfg.expt.post_process is not None, 'need post processing for RB to make sense!'
                        popln, popln_err = randbench.acquire_rotated(soc=self.im[self.cfg.aliases.soc], progress=False, angle=angles_f_q, threshold=thresholds_f_q, ge_avgs=gf_avgs_q, post_process=self.cfg.expt.post_process)
                        assert self.cfg.expt.post_process == 'threshold', 'Can only bin EgGf RB properly using threshold'

                        if self.cfg.expt.post_process == 'threshold':
                            shots, _ = randbench.get_shots(angle=angles_f_q, threshold=thresholds_f_q)
                            # 00, 02, 10, 12
                            counts = np.array([sort_counts(shots[adcNotDrive_ch], shots[adcDrive_ch])])
                            data['counts_raw'][1].append(counts)
                            # print('variation', var, 'gate list', gate_list, 'counts', counts)

        # print('shape', np.shape(data['counts_raw']))
        for icounts in range(len(data['counts_raw'])):
            data['counts_raw'][icounts] = np.average(np.reshape(data['counts_raw'][icounts], (self.cfg.expt.loops, len(depths), self.cfg.expt.variations, 4)), axis=0)

        for k, a in data.items():
            # print(k)
            # print(a)
            # print(np.shape(a))
            data[k] = np.array(a)

        self.data=data
        return data

    def analyze(self, data=None, fit=True, **kwargs):
        if data is None:
            data=self.data

        qA, qB = self.cfg.expt.qubits
        self.measure_f = False
        if self.cfg.expt.measure_f is not None and len(self.cfg.expt.measure_f) >= 0:
            self.measure_f = True
            assert len(self.cfg.expt.measure_f) == 1
            q_measure_f = self.cfg.expt.measure_f[0]
            q_other = qA if q_measure_f == qB else qB
            # Need to make sure qubits are in the right order for all of the calibrations if we want to measure f! Let's just rename the cfg.expt.qubits so it's easy for the rest of this.
            self.cfg.expt.qubits = [q_other, q_measure_f]
        qA, qB = self.cfg.expt.qubits

        data['xpts'] = np.asarray(data['xpts'])
        unique_depths = np.average(data['xpts'], axis=1)

        assert self.measure_f

        data['counts_calib_total'] = np.concatenate((data['counts_calib'], data['counts_calib_f']), axis=1)
        data['counts_raw_total'] = np.concatenate((data['counts_raw'][0], data['counts_raw'][1]), axis=2)
        print('counts calib total', np.shape(data['counts_calib_total']))
        # print(data['counts_calib_total'])
        print('counts raw', np.shape(data['counts_raw_total']))
        # print(data['counts_raw'])
        # print('counts raw total', np.shape(data['counts_raw_total']))

        # gg, ge, eg, ee, gf, ef
        data['poplns_2q'] = np.zeros(shape=(len(unique_depths), self.cfg.expt.variations, 6))

        for idepth, depth in enumerate(unique_depths):
            for ivar in range(self.cfg.expt.variations):
                # after correcting readout error, counts corrected should correspond to counts in [gg, ge, eg, ee, gf, ef] (the calib_order)
                # instead of [ggA, geA, egA, eeA, ggB, gfB, egB, efB] (the raw counts)
                counts_corrected = correct_readout_err([data['counts_raw_total'][idepth, ivar]], data['counts_calib_total'])
                counts_corrected = fix_neg_counts(counts_corrected)
                data['poplns_2q'][idepth, ivar, :] = counts_corrected/np.sum(counts_corrected)

        print('poplns_2q', np.shape(data['poplns_2q']))
        # [gg, ge, eg, ee, gf, ef]
        probs_eg = data['poplns_2q'][:, :, 2]
        probs_gf = data['poplns_2q'][:, :, 4]

        data['popln_eg_std'] = np.std(probs_eg, axis=1)
        data['popln_eg_avg'] = np.average(probs_eg, axis=1)

        sum_prob_subspace = probs_eg + probs_gf
        data['popln_subspace'] = sum_prob_subspace
        data['popln_subspace_std'] = np.std(sum_prob_subspace, axis=1)
        data['popln_subspace_avg'] = np.average(sum_prob_subspace, axis=1)
        data['popln_eg_subspace'] = probs_eg/sum_prob_subspace
        data['popln_eg_subspace_std'] = np.std(probs_eg/sum_prob_subspace, axis=1)
        data['popln_eg_subspace_avg'] = np.average(probs_eg/sum_prob_subspace, axis=1)
        print('shape sum prob_eg + prob_gf', np.shape(sum_prob_subspace))
        print('shape average sum over each depth', np.shape(data['popln_subspace_avg']), 'should equal', np.shape(unique_depths))

        if not fit: return data

        popt1, pcov1 = fitter.fitrb(unique_depths, data['popln_subspace_avg'])
        print('fit1 p1, a, offset', popt1)
        data['fit1'] = popt1
        data['fit1_err'] = pcov1
        p1, a, offset = popt1
        data['l1'] = fitter.leakage_err(p1, offset)
        data['l2'] = fitter.seepage_err(p1, offset)

        popt2, pcov2 = fitter.fitrb_l1_l2(unique_depths, data['popln_eg_avg'], p1=p1, offset=offset)
        print('fit2 a0, b0, c0, p2', popt2)
        data['fit2'] = popt2
        data['fit2_err'] = pcov2
        a0, b0, c0, p2 = popt2
        
        data['fidelity'], data['fidelity_err'] = fitter.rb_fidelity_l1_l2(d=2, p2=p2, l1=data['l1'], p2_err=pcov2[3][3], l1_err=pcov1[0][0])

        popt3, pcov3 = fitter.fitrb(unique_depths, data['popln_eg_subspace_avg'])
        data['fit3'] = popt3
        data['fit3_err'] = pcov3

        return data


    def display(self, data=None, fit=True, show_all_vars=False):
        if data is None:
            data=self.data 

        plt.figure(figsize=(8,6))
        irb = 'gate_char' in self.cfg.expt and self.cfg.expt.gate_char is not None
        title = f'{"Interleaved " + self.cfg.expt.gate_char + " Gate" if irb else ""} EgGf RB on {self.cfg.expt.qubits[0]}, {self.cfg.expt.qubits[1]}'

        plt.subplot(111, title=title, xlabel="Sequence Depth", ylabel="Population")
        depths = data['xpts']
        unique_depths = np.average(depths, axis=1)
        flat_depths = np.concatenate(depths)
        # [gg, ge, eg, ee, gf, ef]
        flat_probs_eg = np.concatenate(data['poplns_2q'][:, :, 2])
        flat_probs_subspace = np.concatenate(data['popln_subspace'])
        if show_all_vars:
            plt.plot(flat_depths, flat_probs_eg, 'x', color='tab:grey')
            plt.plot(flat_depths, flat_probs_subspace, 'v', color='tab:grey')

        probs_eg_avg = data['popln_eg_avg']
        probs_eg_std = data['popln_eg_std']
        probs_subspace_avg = data['popln_subspace_avg']
        probs_subspace_std = data['popln_subspace_std']
        probs_eg_subspace_avg = data['popln_eg_subspace_avg']
        probs_eg_subspace_std = data['popln_eg_subspace_std']

        print('prob_eg_avg', probs_eg_avg, '+/-', probs_eg_std)
        print('prob_subspace_avg', probs_subspace_avg, '+/-', probs_subspace_std)
        print('prob_eg_subspace_avg', probs_eg_subspace_avg, '+/-', probs_eg_subspace_std)
        # plt.errorbar(working_depths, avg_probs, fmt='o', yerr=2*std_dev_probs, color='k', elinewidth=0.75)
        plt.errorbar(unique_depths, probs_eg_avg, fmt='x', yerr=probs_eg_std, color=default_colors[0], elinewidth=0.75, label='eg probability')
        plt.errorbar(unique_depths, probs_subspace_avg, fmt='v', yerr=probs_subspace_std, color=default_colors[1], elinewidth=0.75, label='subspace probability')
        plt.errorbar(unique_depths, probs_eg_subspace_avg, fmt='o', yerr=probs_eg_subspace_std, color=default_colors[2], elinewidth=0.75, label='eg/subspace probability')

        if fit:
            pcov1 = data['fit1_err']
            # plt.plot(depths, avg_probs, 'o-', color='tab:blue')
            plt.plot(unique_depths, fitter.rb_func(unique_depths, *data['fit1']), color=default_colors[1])
            print(f'Running {"interleaved " + self.cfg.expt.gate_char + " gate" if irb else "regular"} RB on EgGf subspace')
            p1 = data['fit1'][0]
            print(f'Depolarizing parameter p1 from fit: {p1} +/- {np.sqrt(pcov1[0][0])}')
            # print(f'Average RB gate error: {data["error"]} +/- {np.sqrt(fitter.error_fit_err(pcov1, 2**(len(self.cfg.expt.qubits))))}')
            # print(f'\tFidelity=1-error: {1-data["error"]} +/- {np.sqrt(fitter.error_fit_err(pcov1, 2**(len(self.cfg.expt.qubits))))}')

            pcov2 = data['fit2_err']
            # plt.plot(depths, avg_probs, 'o-', color='tab:blue')
            plt.plot(unique_depths, fitter.rb_decay_l1_l2(unique_depths, p1, *data['fit2']), color=default_colors[0])
            print(f'Running {"interleaved " + self.cfg.expt.gate_char + " gate" if irb else "regular"} RB on EgGf subspace')
            print(f'Depolarizing parameter p2 from fit: {data["fit2"][3]} +/- {np.sqrt(pcov2[3][3])}')
            print(f'Fidelity: {data["fidelity"]} +/- {data["fidelity_err"]}')
            print(f'Leakage L1: {data["l1"]}')
            print(f'Seepage L2: {data["l2"]}')


            pcov3 = data['fit3_err'][0][0]
            # plt.plot(depths, avg_probs, 'o-', color='tab:blue')
            plt.plot(unique_depths, fitter.rb_func(unique_depths, *data['fit3']), color=default_colors[2])
            p = data['fit3'][0]
            print(f'Depolarizing parameter p from eg/subspace fit: {p} +/- {np.sqrt(pcov3)}')
            err = fitter.rb_error(p, d=2)
            print(f'Average RB gate error on eg/subspace: {err} +/- {np.sqrt(fitter.error_fit_err(pcov3, 2))}')
            print(f'\tFidelity of eg/subspace=1-error: {1-err} +/- {np.sqrt(fitter.error_fit_err(pcov3, 2))}')

        plt.grid(linewidth=0.3)
        plt.ylim(-0.1, 1.1)
        plt.legend()
        plt.show()

    def save_data(self, data=None):
        print(f'Saving {self.fname}')
        super().save_data(data=data)
        with self.datafile() as f:
            f.attrs['calib_order'] = json.dumps(self.calib_order, cls=NpEncoder)
        return self.fname
