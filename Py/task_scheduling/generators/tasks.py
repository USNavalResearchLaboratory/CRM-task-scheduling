"""Generator objects for tasks."""

from types import MethodType
from abc import ABC, abstractmethod
from collections import namedtuple

import numpy as np
import pandas as pd

from task_scheduling.util.generic import RandomGeneratorMixin
from task_scheduling import tasks as task_types

np.set_printoptions(precision=2)


class Base(RandomGeneratorMixin, ABC):
    def __init__(self, cls_task, param_lims=None, rng=None):
        """
        Base class for generation of task objects.

        Parameters
        ----------
        cls_task : class
            Class for instantiating task objects.
        param_lims : dict, optional
            Maps parameter name strings to 2-tuples of parameter lower and upper bounds.
        rng : int or RandomState or Generator, optional
            Random number generator seed or object.

        """

        super().__init__(rng)
        self.cls_task = cls_task

        if param_lims is None:
            self.param_lims = {name: (-float('inf'), float('inf')) for name in self.cls_task.param_names}
        else:
            self.param_lims = param_lims

    @abstractmethod
    def __call__(self, n_tasks, rng=None):
        """Yield tasks."""
        raise NotImplementedError

    @property
    def default_features(self):
        """Returns a NumPy structured array of default features, the task parameters."""
        features = np.array(list(zip(self.cls_task.param_names,
                                     [lambda task, name=name_: getattr(task, name)
                                      for name_ in self.cls_task.param_names],  # note: late-binding closure
                                     self.param_lims.values())),
                            dtype=[('name', '<U16'), ('func', object), ('lims', np.float, 2)])

        return features


class BaseIID(Base, ABC):
    """Base class for generation of independently and identically distributed random task objects."""

    def __call__(self, n_tasks, rng=None):
        """Randomly generate tasks."""
        rng = self._get_rng(rng)
        for _ in range(n_tasks):
            yield self.cls_task(**self._param_gen(rng))

    @abstractmethod
    def _param_gen(self, rng):
        """Randomly generate task parameters."""
        raise NotImplementedError


class GenericIID(BaseIID):
    """
    Generic generator of independently and identically distributed random task objects.

    Parameters
    ----------
    cls_task : class
        Class for instantiating task objects.
    param_gen : callable
        Callable object with 'self' argument, for use as the '_param_gen' method.
    param_lims : dict, optional
        Maps parameter name strings to 2-tuples of parameter lower and upper bounds.
    rng : int or RandomState or Generator, optional
        Random number generator seed or object.

    """

    def __init__(self, cls_task, param_gen, param_lims=None, rng=None):
        super().__init__(cls_task, param_lims, rng)
        self._param_gen_init = MethodType(param_gen, self)

    def _param_gen(self, rng):
        return self._param_gen_init(rng)

    @classmethod
    def relu_drop(cls, param_gen, param_lims=None, rng=None):
        return cls(task_types.ReluDrop, param_gen, param_lims, rng)


class ContinuousUniformIID(BaseIID):
    """Generates I.I.D. tasks with independently uniform continuous parameters."""

    def _param_gen(self, rng):
        """Randomly generate task parameters."""
        return {name: rng.uniform(*self.param_lims[name]) for name in self.cls_task.param_names}

    def __eq__(self, other):
        if isinstance(other, ContinuousUniformIID):
            return self.cls_task == other.cls_task and self.param_lims == other.param_lims
        else:
            return NotImplemented

    @classmethod
    def relu_drop(cls, duration_lim=(3, 6), t_release_lim=(0, 4), slope_lim=(0.5, 2),
                  t_drop_lim=(6, 12), l_drop_lim=(35, 50), rng=None):
        """Factory constructor for ReluDrop task objects."""

        param_lims = {'duration': duration_lim, 't_release': t_release_lim,
                      'slope': slope_lim, 't_drop': t_drop_lim, 'l_drop': l_drop_lim}

        return cls(task_types.ReluDrop, param_lims, rng)


class DiscreteIID(BaseIID):
    """
    Generates I.I.D. tasks with independently discrete parameters.

    Parameters
    ----------
    cls_task : class
        Class for instantiating task objects.
    param_probs: dict of str to dict
        Maps parameter name strings to dictionaries mapping values to probabilities.
    rng : int or RandomState or Generator, optional
        Random number generator seed or object.

    """

    def __init__(self, cls_task, param_probs, rng=None):
        param_lims = {name: (min(param_probs[name].keys()), max(param_probs[name].keys()))
                      for name in self.cls_task.param_names}

        super().__init__(cls_task, param_lims, rng)

        self.param_probs = param_probs

    def _param_gen(self, rng):
        """Randomly generate task parameters."""
        return {name: rng.choice(list(self.param_probs[name].keys()), p=list(self.param_probs[name].values()))
                for name in self.cls_task.param_names}

    def __eq__(self, other):
        if isinstance(other, DiscreteIID):
            return self.cls_task == other.cls_task and self.param_probs == other.param_probs
        else:
            return NotImplemented

    @classmethod
    def uniform_relu_drop(cls, duration_vals, t_release_vals, slope_vals, t_drop_vals, l_drop_vals, rng=None):
        """Factory constructor for ReluDrop task objects."""

        param_probs = {'duration': dict(zip(duration_vals, np.ones(len(duration_vals)) / len(duration_vals))),
                       't_release': dict(zip(t_release_vals, np.ones(len(t_release_vals)) / len(t_release_vals))),
                       'slope': dict(zip(slope_vals, np.ones(len(slope_vals)) / len(slope_vals))),
                       't_drop': dict(zip(t_drop_vals, np.ones(len(t_drop_vals)) / len(t_drop_vals))),
                       'l_drop': dict(zip(l_drop_vals, np.ones(len(l_drop_vals)) / len(l_drop_vals))),
                       }
        return cls(task_types.ReluDrop, param_probs, rng)


class SearchTrackIID(BaseIID):
    """Search and Track tasks based on 2020 TSRS paper."""

    def __init__(self, probs=None, t_release_lim=(0., 0.), rng=None):
        self.targets = [{'duration': .036, 't_revisit': 2.5},
                        {'duration': .036, 't_revisit': 5.0},
                        {'duration': .018, 't_revisit': 5.0},
                        {'duration': .018, 't_revisit': 1.0},
                        {'duration': .018, 't_revisit': 2.0},
                        {'duration': .018, 't_revisit': 4.0},
                        ]

        durations, t_revisits = zip(*[target.values() for target in self.targets])
        param_lims = {'duration': (min(durations), max(durations)),
                      't_release': t_release_lim,
                      'slope': (1 / max(t_revisits), 1 / min(t_revisits)),
                      't_drop': (min(t_revisits) + 0.1, max(t_revisits) + 0.1),
                      'l_drop': (300., 300.)
                      }

        super().__init__(task_types.ReluDrop, param_lims, rng)

        if probs is None:
            self.probs = [.1, .2, .4, .1, .1, .1]
        else:
            self.probs = probs

    def _param_gen(self, rng):
        """Randomly generate task parameters."""
        duration, t_revisit = rng.choice(self.targets, p=self.probs).values()
        params = {'duration': duration,
                  't_release': rng.uniform(*self.param_lims['t_release']),
                  'slope': 1 / t_revisit,
                  't_drop': t_revisit + 0.1,
                  'l_drop': 300.
                  }
        return params


class Fixed(Base, ABC):
    def __init__(self, tasks, param_lims=None, rng=None):
        """
        Permutation task generator.

        Parameters
        ----------
        tasks : Iterable of tasks.Generic
        param_lims : dict, optional
            Maps parameter name strings to 2-tuples of parameter lower and upper bounds.
        rng : int or RandomState or Generator, optional
            Random number generator seed or object.
        """

        self.tasks = list(tasks)

        cls_task = self.tasks[0].__class__
        if not all(isinstance(task, cls_task) for task in self.tasks[1:]):
            raise TypeError("All tasks must be of the same type.")

        if param_lims is None:
            param_lims = {}
            for name in cls_task.param_names:
                values = [getattr(task, name) for task in tasks]
                param_lims[name] = (min(values), max(values))

        super().__init__(cls_task, param_lims, rng)

    @abstractmethod
    def __call__(self, n_tasks, rng=None):
        """Yield tasks."""
        raise NotImplementedError

    def __eq__(self, other):
        if isinstance(other, Fixed):
            return self.tasks == other.tasks
        else:
            return NotImplemented

    @classmethod
    def _task_gen_to_fixed(cls, n_tasks, task_gen, rng):
        tasks = list(task_gen(n_tasks, rng))
        param_lims = task_gen.param_lims
        return cls(tasks, param_lims, rng)

    @classmethod
    def relu_drop(cls, n_tasks, rng=None, **relu_lims):
        task_gen = ContinuousUniformIID.relu_drop(**relu_lims)
        return cls._task_gen_to_fixed(n_tasks, task_gen, rng)

    @classmethod
    def search_track(cls, n_tasks, probs=None, t_release_lim=(0., 0.), rng=None):
        task_gen = SearchTrackIID(probs, t_release_lim)
        return cls._task_gen_to_fixed(n_tasks, task_gen, rng)


class Deterministic(Fixed):
    def __call__(self, n_tasks, rng=None):
        """Yields the tasks in deterministic order."""
        if n_tasks != len(self.tasks):
            raise ValueError(f"Number of tasks must be {len(self.tasks)}.")

        for task in self.tasks:
            yield task


class Permutation(Fixed):
    def __call__(self, n_tasks, rng=None):
        """Yields the tasks in a uniformly random order."""
        if n_tasks != len(self.tasks):
            raise ValueError(f"Number of tasks must be {len(self.tasks)}.")

        rng = self._get_rng(rng)
        for task in rng.permutation(self.tasks).tolist():
            yield task


class TaskParameters:  # Initializes to something like matlab structure. Enables dot indexing
    pass


class FlexDAR(Base):
    ##

    def __init__(self, n_track=0, param_lims=None, rng=None):
        super().__init__(cls_task=task_types.ReluDropRadar, param_lims=None, rng=None)

        self.n_track = n_track

        # SearchParams = namedtuple('SearchParams', ['NbeamsPerRow', 'DwellTime'])

        # Generate Search Tasks
        SearchParams = TaskParameters()
        SearchParams.NbeamsPerRow = np.array([28, 29, 14, 9, 10, 9, 8, 7, 6])
        # SearchParams.NbeamsPerRow = [208 29 14 9 10 9 8 7 6]; % Overload
        SearchParams.DwellTime = np.array([36, 36, 36, 18, 18, 18, 18, 18, 18]) * 1e-3
        SearchParams.RevistRate = np.array([2.5, 5, 5, 5, 5, 5, 5, 5, 5])
        SearchParams.RevisitRateUB = SearchParams.RevistRate + 0.1  # Upper Bound on Revisit Rate
        SearchParams.Penalty = 300 * np.ones(np.shape(SearchParams.RevistRate))  # Penalty for exceeding UB
        SearchParams.Slope = 1. / SearchParams.RevistRate

        n_search = np.sum(SearchParams.NbeamsPerRow)
        SearchParams.JobDuration = np.array([])
        SearchParams.JobSlope = np.array([])
        SearchParams.DropTime = np.array([])  # Task dropping time. Will get updated as tasks get processed
        # Used to update DropTimes. Fixed for a given task e.x. always 2.6 process task at time 1 DropTime becomes 3.6
        # SearchParams.DropTimeFixed = np.array([])
        SearchParams.DropCost = np.array([])
        for jj in range(len(SearchParams.NbeamsPerRow)):
            SearchParams.JobDuration = np.append(SearchParams.JobDuration,
                                                 np.repeat(SearchParams.DwellTime[jj], SearchParams.NbeamsPerRow[jj]))
            SearchParams.JobSlope = np.append(SearchParams.JobSlope,
                                              np.repeat(SearchParams.Slope[jj], SearchParams.NbeamsPerRow[jj]))
            SearchParams.DropTime = np.append(SearchParams.DropTime,
                                              np.repeat(SearchParams.RevisitRateUB[jj], SearchParams.NbeamsPerRow[jj]))
            # SearchParams.DropTimeFixed = np.append(SearchParams.DropTimeFixed, np.repeat(SearchParams.RevisitRateUB[jj],
            #                                                                              SearchParams.NbeamsPerRow[jj]))
            SearchParams.DropCost = np.append(SearchParams.DropCost,
                                              np.repeat(SearchParams.Penalty[jj], SearchParams.NbeamsPerRow[jj]))


        tasks_master = []
        idx = 0
        for n_beams, t_dwell, revisit_rate, penalty, slope in zip(SearchParams.NbeamsPerRow,
                                                                  SearchParams.DwellTime,
                                                                  SearchParams.RevisitRateUB,
                                                                  SearchParams.Penalty,
                                                                  SearchParams.Slope):
            for __ in range(n_beams):
                tasks_master.append(self.cls_task(duration=t_dwell, t_release=0., slope=slope, t_drop=revisit_rate,
                                                  l_drop=penalty, id_=idx))
                idx += 1

        for t_dwell, revisit_rate, penalty, slope in zip(TrackParams.DwellTime,
                                                         TrackParams.RevisitRateUB,
                                                         TrackParams.Penalty,
                                                         TrackParams.Slope):

            for __ in range(n_beams):
                tasks_master.append(self.cls_task(duration=t_dwell, t_release=0., slope=slope, t_drop=revisit_rate,
                                                  l_drop=penalty, id_=idx))
                idx += 1



        # %% Generate Track Tasks
        TrackParams = TaskParameters()  # Initializes to something like matlab structure
        # Ntrack = 10

        # Spawn tracks with uniformly distributed ranges and velocity
        MaxRangeNmi = 200  #
        MaxRangeRateMps = 343  # Mach 1 in Mps is 343

        truth = TaskParameters
        truth.rangeNmi = MaxRangeNmi * self.rng.uniform(0, 1, n_track)
        truth.rangeRateMps = 2 * MaxRangeRateMps * self.rng.uniform(0, 1, n_track) - MaxRangeRateMps

        TrackParams.DwellTime = np.array([18, 18, 18]) * 1e-3
        TrackParams.RevisitRate = np.array([1, 2, 4])
        TrackParams.RevisitRateUB = TrackParams.RevisitRate + 0.1
        TrackParams.Penalty = 300 * np.ones(np.shape(TrackParams.DwellTime))
        TrackParams.Slope = 1. / TrackParams.RevisitRate
        TrackParams.JobDuration = []
        TrackParams.JobSlope = []
        TrackParams.DropTime = []
        TrackParams.DropTimeFixed = []
        TrackParams.DropCost = []
        for jj in range(n_track):
            if truth.rangeNmi[jj] <= 50:
                TrackParams.JobDuration = np.append(TrackParams.JobDuration, TrackParams.DwellTime[0])
                TrackParams.JobSlope = np.append(TrackParams.JobSlope, TrackParams.Slope[0])
                TrackParams.DropTime = np.append(TrackParams.DropTime, TrackParams.RevisitRateUB[0])
                TrackParams.DropTimeFixed = np.append(TrackParams.DropTimeFixed, TrackParams.RevisitRateUB[0])
                TrackParams.DropCost = np.append(TrackParams.DropCost, TrackParams.Penalty[0])
            elif truth.rangeNmi[jj] > 50 and abs(truth.rangeRateMps[jj]) >= 100:
                TrackParams.JobDuration = np.append(TrackParams.JobDuration, TrackParams.DwellTime[1])
                TrackParams.JobSlope = np.append(TrackParams.JobSlope, TrackParams.Slope[1])
                TrackParams.DropTime = np.append(TrackParams.DropTime, TrackParams.RevisitRateUB[1])
                TrackParams.DropTimeFixed = np.append(TrackParams.DropTimeFixed, TrackParams.RevisitRateUB[1])
                TrackParams.DropCost = np.append(TrackParams.DropCost, TrackParams.Penalty[1])
            else:
                TrackParams.JobDuration = np.append(TrackParams.JobDuration, TrackParams.DwellTime[2])
                TrackParams.JobSlope = np.append(TrackParams.JobSlope, TrackParams.Slope[2])
                TrackParams.DropTime = np.append(TrackParams.DropTime, TrackParams.RevisitRateUB[2])
                TrackParams.DropTimeFixed = np.append(TrackParams.DropTimeFixed, TrackParams.RevisitRateUB[2])
                TrackParams.DropCost = np.append(TrackParams.DropCost, TrackParams.Penalty[2])

    def __call__(self, rng=None):

        n_track = self.n_track

        # Begin Scheduler Loop

        # rng = self.rng.default_rng(100)
        # task_gen = ReluDropGenerator(duration_lim=(3, 6), t_release_lim=(0, 4), slope_lim=(0.5, 2),
        #                              t_drop_lim=(12, 20), l_drop_lim=(35, 50), rng=rng)       # task set generator
        # tasks = task_gen.rand_tasks(N)

        # A = list()
        tasks = []
        cnt = 0  # Make 0-based, saves a lot of trouble later when indexing into python zero-based vectors
        for ii in range(Nsearch):
            # job.append(0, self.cls_task(SearchParams.JobDuration[ii], SearchParams.JobSlope[ii], SearchParams.DropTime[ii], SearchParams.DropTimeFixed[ii], SearchParams.DropCost[ii]))
            tasks.append(self.cls_task(SearchParams.JobDuration[ii], 0, SearchParams.JobSlope[ii], SearchParams.DropTime[ii],
                                SearchParams.DropCost[ii]))
            tasks[ii].Id = cnt  # Numeric Identifier for each job
            cnt = cnt + 1
            if tasks[ii].slope == 0.4:
                tasks[ii].Type = 'HS'  # Horizon Search (Used to determine revisit rates by job type
            else:
                tasks[ii].Type = 'AHS'  # Above horizon search
            tasks[ii].Priority = tasks[ii](0)  # Priority used to select which jobs to give to scheduler

            # tasks = self.cls_task(SearchParams.JobDuration[ii], 0, SearchParams.JobSlope[ii], SearchParams.DropTime[ii], SearchParams.DropCost[ii])
            # A.append(tasks)
            # del tasks

        for ii in range(n_track):
            # job.append(self.cls_task(0, TrackParams.JobDuration[ii], TrackParams.JobSlope[ii], TrackParams.DropTime[ii], TrackParams.DropTimeFixed[ii], TrackParams.DropCost[ii]))
            tasks.append(self.cls_task(TrackParams.JobDuration[ii], 0, TrackParams.JobSlope[ii], TrackParams.DropTime[ii],
                                TrackParams.DropCost[ii]))
            tasks[cnt].Id = cnt  # Numeric Identifier for each job
            if tasks[cnt].slope == 0.25:
                tasks[cnt].Type = 'Tlow'  # Low Priority Track
            elif tasks[cnt].slope == 0.5:
                tasks[cnt].Type = 'Tmed'  # Medium Priority Track
            else:
                tasks[cnt].Type = 'Thigh'  # High Priority Track
            tasks[cnt].Priority = tasks[cnt](0)
            cnt = cnt + 1

            self.tasks = list(tasks)

            cls_task = self.tasks[0].__class__
            if not all(isinstance(task, cls_task) for task in self.tasks[1:]):
                raise TypeError("All tasks must be of the same type.")

            if param_lims is None:
                param_lims = {}
                for name in cls_task.param_names:
                    values = [getattr(task, name) for task in tasks]
                    param_lims[name] = (min(values), max(values))

            # super().__init__(cls_task, param_lims, rng)

            # self.tasks = tasks

        return tasks

    def summary(self):  # TODO: Fix this

        df = pd.DataFrame({name: [getattr(task, name) for task in self.tasks]
                           for name in self._cls_task.param_names})
        print(df)

    # for task in
    #     yield task


def main():
    task_gen = FlexDAR(n_track=1)


if __name__ == '__main__':
    main()
