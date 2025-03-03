# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) 2015, ARM Limited and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import fileinput
import json
import logging
import os
import re

from wlgen import Workload
from devlib.utils.misc import ranges_to_list

class RTA(Workload):

    def __init__(self,
                 target,
                 name,
                 calibration=None):

        self.logger = logging.getLogger('rtapp')

        # rt-app calibration
        self.pload = calibration

        # TODO: Assume rt-app is pre-installed on target
        # self.target.setup('rt-app')

        super(RTA, self).__init__(target, name, calibration)

        # rt-app executor
        self.wtype = 'rtapp'
        self.executor = 'rt-app'

        # Default initialization
        self.json = None
        self.rta_profile = None
        self.loadref = None
        self.rta_cmd  = None
        self.rta_conf = None
        self.test_label = None

        # Setup RTA callbacks
        self.setCallback('postrun', self.__postrun)

    @staticmethod
    def calibrate(target):
        pload_regexp = re.compile(r'pLoad = ([0-9]+)ns')
        pload = {}

        # target.cpufreq.save_governors()
        target.cpufreq.set_all_governors('performance')

        for cpu in target.list_online_cpus():

            logging.info('CPU%d calibration...', cpu)

            max_rtprio = int(target.execute('ulimit -Hr').split('\r')[0])
            logging.debug('Max RT prio: %d', max_rtprio)
            if max_rtprio > 10:
                max_rtprio = 10

            rta = RTA(target, 'rta_calib')
            rta.conf(kind='profile',
                    params = {
                        'task1': RTA.periodic(
                            period_ms=100,
                            duty_cycle_pct=50,
                            duration_s=1,
                            sched={
                                'policy': 'FIFO',
                                'prio' : max_rtprio
                            }
                        )
                    },
                    cpus=[cpu])
            rta.run(as_root=True)

            for line in rta.getOutput().split('\n'):
                pload_match = re.search(pload_regexp, line)
                if pload_match is None:
                    continue
                pload[cpu] = int(pload_match.group(1))
                logging.debug('>>> cpu%d: %d', cpu, pload[cpu])

        # target.cpufreq.load_governors()

        logging.info('Target RT-App calibration:')
        logging.info('%s',
                "{" + ", ".join('"%r": %r' % (key, pload[key]) for key in pload) + "}")

        return pload

    def __postrun(self, params):
        destdir = params['destdir']
        if destdir is None:
            return
        self.logger.debug('Pulling logfiles to [%s]...', destdir)
        for task in self.tasks.keys():
            logfile = "'{0:s}/*{1:s}*.log'"\
                    .format(self.run_dir, task)
            self.target.pull(logfile, destdir)
        self.logger.debug('Pulling JSON to [%s]...', destdir)
        self.target.pull('{}/{}'.format(self.run_dir, self.json), destdir)
        logfile = '{}/output.log'.format(destdir)
        self.logger.debug('Saving output on [%s]...', logfile)
        with open(logfile, 'w') as ofile:
            for line in self.output['executor'].split('\n'):
                ofile.write(line+'\n')

    def _getFirstBiggest(self, cpus):
        # Non big.LITTLE system:
        if 'bl' not in self.target.modules:
            # return the first CPU of the last cluster
            platform = self.target.platform
            cluster_last = list(set(platform.core_clusters))[-1]
            cluster_cpus = [cpu_id
                    for cpu_id, cluster_id in enumerate(platform.core_clusters)
                                           if cluster_id == cluster_last]
            # If CPUs have been specified': return the fist in the last cluster
            if cpus:
                for cpu_id in cpus:
                    if cpu_id in cluster_cpus:
                        return cpu_id
            # Otherwise just return the first cpu of the last cluster
            return cluster_cpus[0]

        # big.LITTLE system:
        for c in cpus:
             if c not in self.target.bl.bigs:
                continue
             return c
        # Only LITTLE CPUs, thus:
        #  return the first possible cpu
        return cpus[0]

    def _getFirstBig(self, cpus=None):
        # Non big.LITTLE system:
        if 'bl' not in self.target.modules:
            return self._getFirstBiggest(cpus)
        if cpus:
            for c in cpus:
                if c not in self.target.bl.bigs:
                    continue
                return c
        # Only LITTLE CPUs, thus:
        #  return the first big core of the system
        if self.target.big_core:
            # Big.LITTLE system
            return self.target.bl.bigs[0]
        return 0

    def _getFirstLittle(self, cpus=None):
        # Non big.LITTLE system:
        if 'bl' not in self.target.modules:
            # return the first CPU of the first cluster
            platform = self.target.platform
            cluster_first = list(set(platform.core_clusters))[0]
            cluster_cpus = [cpu_id
                    for cpu_id, cluster_id in enumerate(platform.core_clusters)
                                           if cluster_id == cluster_first]
            # If CPUs have been specified': return the fist in the first cluster
            if cpus:
                for cpu_id in cpus:
                    if cpu_id in cluster_cpus:
                        return cpu_id
            # Otherwise just return the first cpu of the first cluster
            return cluster_cpus[0]

        # Try to return one LITTLE CPUs among the specified ones
        if cpus:
            for c in cpus:
                if c not in self.target.bl.littles:
                    continue
                return c
        # Only big CPUs, thus:
        #  return the first LITTLE core of the system
        if self.target.little_core:
            # Big.LITTLE system
            return self.target.bl.littles[0]
        return 0

    def getTargetCpu(self, loadref):
        # Select CPU for task calibration, which is the first little
        # of big depending on the loadref tag
        if self.pload is not None:
            if loadref and loadref.upper() == 'LITTLE':
                target_cpu = self._getFirstLittle()
                self.logger.debug('ref on LITTLE cpu: %d', target_cpu)
            else:
                target_cpu = self._getFirstBig()
                self.logger.debug('ref on big cpu: %d', target_cpu)
            return target_cpu

        # These options are selected only when RTApp has not been
        # already calibrated
        if self.cpus is None:
            target_cpu = self._getFirstBig()
            self.logger.debug('ref on cpu: %d', target_cpu)
        else:
            target_cpu = self._getFirstBiggest(self.cpus)
            self.logger.debug('ref on (possible) biggest cpu: %d', target_cpu)
        return target_cpu

    def getCalibrationConf(self, target_cpu=0):
        if self.pload is None:
            return 'CPU{0:d}'.format(target_cpu)
        return self.pload[target_cpu]

    def _confCustom(self):

        if self.duration is None:
            raise ValueError('Workload duration not specified')

        target_cpu = self.getTargetCpu(self.loadref)
        calibration = self.getCalibrationConf(target_cpu)

        self.json = '{0:s}_{1:02d}.json'.format(self.name, self.exc_id)
        ofile = open(self.json, 'w')
        ifile = open(self.params['custom'], 'r')
        replacements = {
            '__DURATION__' : str(self.duration),
            '__PVALUE__'   : str(calibration),
            '__LOGDIR__'   : str(self.run_dir),
            '__WORKDIR__'  : '"'+self.target.working_directory+'"',
        }

        for line in ifile:
            for src, target in replacements.iteritems():
                line = line.replace(src, target)
            ofile.write(line)
        ifile.close()
        ofile.close()

        return self.json

    def _confProfile(self):

        # Task configuration
        target_cpu = self.getTargetCpu(self.loadref)
        self.rta_profile = {
            'tasks': {},
            'global': {}
        }

        # Initialize global configuration
        global_conf = {
                'default_policy': 'SCHED_OTHER',
                'duration': -1,
                'calibration': 'CPU'+str(target_cpu),
                'logdir': self.run_dir,
            }

        # Setup calibration data
        calibration = self.getCalibrationConf(target_cpu)
        global_conf['calibration'] = calibration
        if self.duration is not None:
            global_conf['duration'] = self.duration
            self.logger.warn('Limiting workload duration to %d [s]',
                    global_conf['duration'])
        else:
            self.logger.info('Workload duration defined by longest task')

        # Setup default scheduling class
        if 'policy' in self.sched:
            policy = self.sched['policy'].upper()
            if policy not in ['OTHER', 'FIFO', 'RR', 'DEADLINE']:
                raise ValueError('scheduling class {} not supported'\
                        .format(policy))
            global_conf['default_policy'] = 'SCHED_' + self.sched['policy']

        self.logger.info('Default policy: %s', global_conf['default_policy'])

        # Setup global configuration
        self.rta_profile['global'] = global_conf

        # Setup tasks parameters
        for tid in sorted(self.params['profile'].keys()):
            task = self.params['profile'][tid]

            # Initialize task configuration
            task_conf = {}

            if 'sched' not in task:
                policy = 'DEFAULT'
            else:
                policy = task['sched']['policy'].upper()
            if policy == 'DEFAULT':
                task_conf['policy'] = global_conf['default_policy']
                sched_descr = 'sched: using default policy'
            elif policy not in ['OTHER', 'FIFO', 'RR', 'DEADLINE']:
                raise ValueError('scheduling class {} not supported'\
                        .format(task['sclass']))
            else:
                task_conf.update(task['sched'])
                task_conf['policy'] = 'SCHED_' + policy
                sched_descr = 'sched: {0:s}'.format(task['sched'])

            # Initialize task phases
            task_conf['phases'] = {}

            self.logger.info('------------------------')
            self.logger.info('task [%s], %s', tid, sched_descr)

            if 'delay' in task.keys():
                if task['delay'] > 0:
                    task['delay'] = int(task['delay'] * 1e6)
                    task_conf['phases']['p000000'] = {}
                    task_conf['phases']['p000000']['sleep'] = task['delay']
                    self.logger.info(' | start delay: %.6f [s]',
                            task['delay'] / 1e6)

            self.logger.info(' | calibration CPU: %d', target_cpu)

            if 'loops' not in task.keys():
                task['loops'] = 1
            task_conf['loop'] = task['loops']
            self.logger.info(' | loops count: %d', task['loops'])

            # Setup task affinity
            if 'cpus' in task and task['cpus']:
                task_conf['cpus'] = ranges_to_list(task['cpus'])
                self.logger.info(' | CPUs affinity: %s', task['cpus'])

            # Setup task configuration
            self.rta_profile['tasks'][tid] = task_conf

            # Getting task phase descriptor
            pid=1
            for phase in task['phases']:
                (duration, period, duty_cycle) = phase

                # Convert time parameters to integer [us] units
                duration = int(duration * 1e6)
                period = int(period * 1e3)

                # A duty-cycle of 0[%] translates on a 'sleep' phase
                if duty_cycle == 0:

                    self.logger.info(' + phase_%06d: sleep %.6f [s]',
                            pid, duration/1e6)

                    task_phase = {
                        'loop': 1,
                        'sleep': duration,
                    }

                # A duty-cycle of 100[%] translates on a 'run-only' phase
                elif duty_cycle == 100:

                    self.logger.info(' + phase_%06d: batch %.6f [s]',
                            pid, duration/1e6)

                    task_phase = {
                        'loop': 1,
                        'run': duration,
                    }

                # A certain number of loops is requires to generate the
                # proper load
                else:

                    cloops = -1
                    if duration >= 0:
                        cloops = int(duration / period)

                    sleep_time = period * (100 - duty_cycle) / 100
                    running_time = period - sleep_time

                    self.logger.info(
                            ' + phase_%06d: duration %.6f [s] (%d loops)',
                            pid, duration/1e6, cloops)
                    self.logger.info(
                            ' |  period   %6d [us], duty_cycle %3d %%',
                            period, duty_cycle)
                    self.logger.info(
                            ' |  run_time %6d [us], sleep_time %6d [us]',
                            running_time, sleep_time)

                    task_phase = {
                        'loop': cloops,
                        'run': running_time,
                        'timer': {'ref': tid, 'period': period},
                    }

                self.rta_profile['tasks'][tid]['phases']\
                    ['p'+str(pid).zfill(6)] = task_phase

                pid+=1

            # Append task name to the list of this workload tasks
            self.tasks[tid] = {'pid': -1}

        # Generate JSON configuration on local file
        self.json = '{0:s}_{1:02d}.json'.format(self.name, self.exc_id)
        with open(self.json, 'w') as outfile:
            json.dump(self.rta_profile, outfile,
                    sort_keys=True, indent=4, separators=(',', ': '))

        return self.json

    @staticmethod
    def ramp(start_pct=0, end_pct=100, delta_pct=10, time_s=1, period_ms=100,
            delay_s=0, loops=1, sched=None, cpus=None):
        """
        Configure a ramp load.

        This class defines a task which load is a ramp with a configured number
        of steps according to the input parameters.

        Args:
            start_pct (int, [0-100]): the initial load [%], (default 0[%])
            end_pct   (int, [0-100]): the final load [%], (default 100[%])
            delta_pct (int, [0-100]): the load increase/decrease [%],
                                      default: 10[%]
                                      increase if start_prc < end_prc
                                      decrease  if start_prc > end_prc
            time_s    (float): the duration in [s] of each load step
                               default: 1.0[s]
            period_ms (float): the period used to define the load in [ms]
                               default: 100.0[ms]
            delay_s   (float): the delay in [s] before ramp start
                               default: 0[s]
            loops     (int):   number of time to repeat the ramp, with the
                               specified delay in between
                               default: 0
            sched     (dict): the scheduler configuration for this task
            cpus      (list): the list of CPUs on which task can run
        """
        task = {}

        task['cpus'] = cpus
        if not sched:
            sched = {'policy' : 'DEFAULT'}
        task['sched'] = sched
        task['delay'] = delay_s
        task['loops'] = loops
        task['phases'] = {}

        if start_pct not in range(0,101) or end_pct not in range(0,101):
            raise ValueError('start_pct and end_pct must be in [0..100] range')

        if start_pct >= end_pct:
            if delta_pct > 0:
                delta_pct = -delta_pct
            delta_adj = -1
        if start_pct <= end_pct:
            if delta_pct < 0:
                delta_pct = -delta_pct
            delta_adj = +1

        phases = []
        steps = range(start_pct, end_pct+delta_adj, delta_pct)
        for load in steps:
            if load == 0:
                phase = (time_s, 0, 0)
            else:
                phase = (time_s, period_ms, load)
            phases.append(phase)

        task['phases'] = phases

        return task;

    @staticmethod
    def step(start_pct=0, end_pct=100, time_s=1, period_ms=100,
            delay_s=0, loops=1, sched=None, cpus=None):
        """
        Configure a step load.

        This class defines a task which load is a step with a configured
        initial and final load.

        Args:
            start_pct (int, [0-100]): the initial load [%]
                                      default 0[%])
            end_pct   (int, [0-100]): the final load [%]
                                      default 100[%]
            time_s    (float): the duration in [s] of the start and end load
                               default: 1.0[s]
            period_ms (float): the period used to define the load in [ms]
                               default 100.0[ms]
            delay_s   (float): the delay in [s] before ramp start
                               default 0[s]
            loops     (int):   number of time to repeat the ramp, with the
                               specified delay in between
                               default: 0
            sched     (dict): the scheduler configuration for this task
            cpus      (list): the list of CPUs on which task can run
        """
        delta_pct = abs(end_pct - start_pct)
        return RTA.ramp(start_pct, end_pct, delta_pct, time_s,
                period_ms, delay_s, loops, sched, cpus)

    @staticmethod
    def pulse(start_pct=100, end_pct=0, time_s=1, period_ms=100,
            delay_s=0, loops=1, sched=None, cpus=None):
        """
        Configure a pulse load.

        This class defines a task which load is a pulse with a configured
        initial and final load.

        The main difference with the 'step' class is that a pulse workload is
        by definition a 'step down', i.e. the workload switch from an finial
        load to a final one which is always lower than the initial one.
        Moreover, a pulse load does not generate a sleep phase in case of 0[%]
        load, i.e. the task ends as soon as the non null initial load has
        completed.

        Args:
            start_pct (int, [0-100]): the initial load [%]
                                      default: 0[%]
            end_pct   (int, [0-100]): the final load [%]
                                      default: 100[%]
                      NOTE: must be lower than start_pct value
            time_s    (float): the duration in [s] of the start and end load
                               default: 1.0[s]
                               NOTE: if end_pct is 0, the task end after the
                               start_pct period completed
            period_ms (float): the period used to define the load in [ms]
                               default: 100.0[ms]
            delay_s   (float): the delay in [s] before ramp start
                               default: 0[s]
            loops     (int):   number of time to repeat the ramp, with the
                               specified delay in between
                               default: 0
            sched     (dict):  the scheduler configuration for this task
            cpus      (list):  the list of CPUs on which task can run
        """

        if end_pct >= start_pct:
            raise ValueError('end_pct must be lower than start_pct')

        task = {}

        task['cpus'] = cpus
        if not sched:
            sched = {'policy' : 'DEFAULT'}
        task['sched'] = sched
        task['delay'] = delay_s
        task['loops'] = loops
        task['phases'] = {}

        if end_pct not in range(0,101) or start_pct not in range(0,101):
            raise ValueError('end_pct and start_pct must be in [0..100] range')
        if end_pct >= start_pct:
            raise ValueError('end_pct must be lower than start_pct')

        phases = []
        for load in [start_pct, end_pct]:
            if load == 0:
                continue
            phase = (time_s, period_ms, load)
            phases.append(phase)

        task['phases'] = phases

        return task;

    @staticmethod
    def periodic(duty_cycle_pct=50, duration_s=1, period_ms=100,
            delay_s=0, sched=None, cpus=None):
        """
        Configure a periodic load.

        This class defines a task which load is periodic with a configured
        period and duty-cycle.

        This class is a specialization of the 'pulse' class since a periodic
        load is generated as a sequence of pulse loads.

        Args:
            cuty_cycle_pct  (int, [0-100]): the pulses load [%]
                                            default: 50[%]
            duration_s  (float): the duration in [s] of the entire workload
                                 default: 1.0[s]
            period_ms   (float): the period used to define the load in [ms]
                                 default: 100.0[ms]
            delay_s     (float): the delay in [s] before ramp start
                                 default: 0[s]
            sched       (dict):  the scheduler configuration for this task

        """

        return RTA.pulse(duty_cycle_pct, 0, duration_s,
                period_ms, delay_s, 1, sched, cpus)

    def conf(self,
             kind,
             params,
             duration=None,
             cpus=None,
             sched=None,
             run_dir=None,
             exc_id=0,
             loadref='big'):
        """
        Configure a workload of a specified kind.

        The rt-app based workload allows to define different classes of
        workloads. The classes supported so far are detailed hereafter.

        Periodic workloads
        ------------------


        Custom workloads
        ----------------


        Profile based workloads
        -----------------------
        When 'kind' is 'profile' the tasks generated by this workload have a
        profile which is defined by a sequence of phases and they are defined
        according to the following grammar:

          params := {task, ...}
          task   := NAME : {SCLASS, PRIO, [phase, ...]}
          phase  := (PTIME, PRIOD, DCYCLE)

        where the terminals are:

          NAME   : string, the task name (max 16 chars)
          SCLASS : string, the scheduling class (OTHER, FIFO, RR)
          PRIO   : int, the priority of the task
          PTIME  : float, length of the current phase in [s]
          PERIOD : float, task activation interval in [ms]
          DCYCLE : int, task running interval in [0..100]% within each period

        """

        if not sched:
            sched = {'policy' : 'OTHER'}

        super(RTA, self).conf(kind, params, duration,
                cpus, sched, run_dir, exc_id)

        self.loadref = loadref

        # Setup class-specific configuration
        if kind == 'custom':
            self._confCustom()
        elif kind == 'profile':
            self._confProfile()

        # Move configuration file to target
        self.target.push(self.json, self.run_dir)

        self.rta_cmd  = self.target.executables_directory + '/rt-app'
        self.rta_conf = self.run_dir + '/' + self.json
        self.command = '{0:s} {1:s}'.format(self.rta_cmd, self.rta_conf)

        # Set and return the test label
        self.test_label = '{0:s}_{1:02d}'.format(self.name, self.exc_id)
        return self.test_label

