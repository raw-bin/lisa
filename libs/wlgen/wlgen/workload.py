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

class Workload(object):

    def __init__(self,
                 target,
                 name,
                 calibration=None):

        # Target device confguration
        self.target = target

        # Specific class of this workload
        self.wtype = None

        # Name of this orkload
        self.name = name

        # The dictionary of tasks descriptors generated by this workload
        self.tasks = {}

        # CPU load calibration values, measured on each core
        self.calibration = calibration

        # The cpus on which the workload will be executed
        # NOTE: for the time being we support just a single CPU
        self.cpus = None

	    # The cgroup on which the workload will be executed
        # NOTE: requires cgroups to be properly configured and associated
        #       tools deployed on the target
        self.cgroup = None
        self.cgroup_cmd = ''

        # taskset configuration to constraint workload execution on a specified
        # set of CPUs
        self.taskset = None
        self.taskset_cmd = ''

        # The command to execute a workload (defined by a derived class)
        self.command = None

        # The workload executor, to be defined by a subclass
        self.executor = None

        # Output messages generated by commands executed on the device
        self.output = {}

        # Derived clasess callback methods
        self.steps = {
            'postrun': None,
        }

        # Task specific configuration parameters
        self.duration = None
        self.run_dir = None
        self.exc_id = None

        # Setup kind specific parameters
        self.kind = None

        # Scheduler class configuration
        self.sched = None

        # Map of task/s parameters
        self.params = {}

        logging.info('Setup new workload %s', self.name)

    def __callback(self, step, **kwords):
        if step not in self.steps.keys():
            raise ValueError('Callbacks for [%s] step not supported', step)
        if self.steps[step] is None:
            return
        logging.debug('Callback [%s]...', step)
        self.steps[step](kwords)

    def setCallback(self, step, func):
        logging.debug('Setup step [%s] callback to [%s] function',
                step, func.__name__)
        self.steps[step] = func

    def getCpusMask(self, cpus=None):
        mask = 0x0
        for cpu in (cpus or self.target.list_online_cpus()):
            mask |= (1 << cpu)
        # logging.debug('0x{0:X}'.format(mask))
        return mask

    def conf(self,
             kind,
             params,
             duration,
             cpus=None,
             sched={'policy': 'OTHER'},
             run_dir=None,
             exc_id=0):

        self.cpus = cpus
        self.sched = sched
        self.duration = duration
        self.run_dir = run_dir
        self.exc_id = exc_id

        # Setup kind specific parameters
        self.kind = kind

        # Map of task/s parameters
        self.params = {}

        # Initialize run folder
        if self.run_dir is None:
            self.run_dir = self.target.working_directory

        # Configure a profile workload
        if kind == 'profile':
            logging.debug('Configuring a profile-based workload...')
            self.params['profile'] = params

        # Configure a custom workload
        elif kind == 'custom':
            logging.debug('Configuring custom workload...')
            self.params['custom'] = params

        else:
            logging.error('%s is not a supported RTApp workload kind', kind)
            raise ValueError('RTApp workload kind not supported')

    def run(self,
            ftrace=None,
            cgroup=None,
            background=False,
            out_dir='./',
            as_root=False):

        self.cgroup = cgroup

        if self.command is None:
            logging.error('Error: empty executor command')

        # Prepend eventually required taskset command
        if self.cpus:
            cpus_mask = self.getCpusMask(self.cpus)
            self.taskset_cmd = '{0:s}/taskset 0x{1:X}'\
                    .format(self.target.executables_directory,
                            cpus_mask)
            self.command = '{0:s} {1:s}'\
                    .format(self.taskset_cmd, self.command)

        # Prepend eventually required taskset command
        if self.cgroup and self.cgroup_cmd == '':
            self.cgroup_cmd = '{0:s}/cgroup_run_into.sh {1:s}'\
                .format(self.target.executables_directory,
                        self.cgroup)
            self.command = '{0:s} \'{1:s}\''\
                .format(self.cgroup_cmd, self.command)

        # Start FTrace (if required)
        if ftrace:
            ftrace.start()

        # Start task in background if required
        if background:
            logging.debug('Executor [background]: %s', self.command)
            results = self.target.execute(self.command,
                    background=True, as_root=as_root)
            self.output['executor'] = results
            return results

        logging.info('Executor [start]: %s', self.command)

        # Run command and wait for it to complete
        results = self.target.execute(self.command,
                timeout=None, as_root=as_root)
        # print type(results)
        self.output['executor'] = results

        # Stop FTrace (if required)
        ftrace_dat = None
        if ftrace:
            ftrace.stop()
            ftrace_dat = out_dir + '/' + self.test_label + '.dat'
            dirname = os.path.dirname(ftrace_dat)
            if not os.path.exists(dirname):
                logging.debug('Create ftrace results folder [%s]', dirname)
                os.makedirs(dirname)
            logging.info('Pulling trace file into [%s]...', ftrace_dat)
            ftrace.get_trace(ftrace_dat)

        self.__callback('postrun', destdir=out_dir)

        logging.info('Executor [end]: %s', self.command)

        return ftrace_dat

    def getOutput(self, step='executor'):
        return self.output[step]

    def getTasks(self, dataframe=None, task_names=None,
            name_key='comm', pid_key='pid'):
        # """ Helper function to get PIDs of specified tasks
        #
        #     This method requires a Pandas dataset in input to be used to
        #     fiter our the PIDs of all the specified tasks.
        #     In a dataset is not provided, previouslt filtered PIDs are
        #     returned.  If a list of task names is not provided, the workload
        #     defined task names is used instead.
        #     The specified dataframe must provide at least two columns
        #     reporting the task name and the task PID. The default values of
        #     this colums could be specified using the provided parameters.
        #
        #     :param task_names: The list of tasks to get the PID of (by default
        #                        the workload defined tasks)
        #     :param dataframe: A Pandas datafram containing at least 'pid' and
        #                       'task name' columns
        #                       If None, the previously filtered PIDs are
        #                       returned
        #     :param name_key: The name of the dataframe columns containing
        #                      task names
        #     :param pid_key:  The name of the dataframe columns containing
        #                      task PIDs
        # """
        if dataframe is None:
            return self.tasks
        if task_names is None:
            task_names = self.tasks.keys()
        logging.debug('Lookup dataset for tasks...')
        for task_name in task_names:
            results = dataframe[dataframe[name_key] == task_name]\
                    [[name_key,pid_key]]
            if len(results)==0:
                logging.error('  task %16s NOT found', task_name)
                continue
            (name, pid) = results.head(1).values[0]
            if name != task_name:
                logging.error('  task %16s NOT found', task_name)
                continue
            if task_name not in self.tasks:
                self.tasks[task_name] = {}
            pids = list(results[pid_key].unique())
            self.tasks[task_name]['pid'] = pids
            logging.info('  task %16s found, pid: %s',
                    task_name, self.tasks[task_name]['pid'])
        return self.tasks

    def listAll(self, kill=False):
        # Show all the instances for the current executor
        tasks = self.target.run('ps | grep {0:s}'.format(self.executor))
        for task in tasks:
            task = task.split()
            logging.info('%5s: %s (%s)', task[1], task[8], task[0])
            if kill:
                self.target.run('kill -9 {0:s}'.format(task[1]))

    def killAll(self):
        if self.executor is None:
            return
        logging.info('Killing all [%s] instances:', self.executor)
        self.listAll(True)

