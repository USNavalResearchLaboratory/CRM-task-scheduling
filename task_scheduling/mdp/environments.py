from abc import ABC, abstractmethod
# from collections import OrderedDict
from math import factorial
from operator import attrgetter

import numpy as np
from gym import Env
from gym.spaces import MultiDiscrete, Box, Dict
from matplotlib import pyplot as plt

import task_scheduling.spaces as spaces_tasking
from task_scheduling import tree_search
from task_scheduling.mdp.features import param_features, normalize as normalize_features
from task_scheduling.util import plot_task_losses, plot_schedule


# TODO: move masking op to policies?


# Gym Environments
class Base(Env, ABC):
    def __init__(self, problem_gen, features=None, normalize=True, sort_func=None, time_shift=False, masking=False):
        """Base environment for task scheduling.

        Parameters
        ----------
        normalize
        problem_gen : generators.problems.Base
            Scheduling problem generation object.
        features : numpy.ndarray, optional
            Structured numpy array of features with fields 'name', 'func', and 'space'.
        normalize : bool, optional
            Rescale task features to unit interval.
        sort_func : function or str, optional
            Method that returns a sorting value for re-indexing given a task index 'n'.
        time_shift : bool, optional
            Enables task re-parameterization after sequence updates.
        masking : bool, optional
            If True, features are zeroed out for scheduled tasks.

        """
        self._problem_gen = problem_gen

        # Set features
        # TODO: custom features combining release times and chan availabilities?
        if features is not None:
            self.features = features
        else:
            self.features = param_features(self.problem_gen.task_gen, time_shift, masking)

        if any(space.shape != () for space in self.features['space']):
            raise ValueError("Features must be scalar valued")

        self.normalize = normalize
        if self.normalize:
            self.features = normalize_features(self.features)

        # Set sorting method
        if callable(sort_func):
            self.sort_func = sort_func
            self._sort_func_str = 'Custom'
        elif isinstance(sort_func, str):
            self.sort_func = attrgetter(sort_func)
            self._sort_func_str = sort_func
        else:
            self.sort_func = None
            self._sort_func_str = None

        self.time_shift = time_shift
        self.masking = masking

        self.reward_range = (-np.inf, 0)
        self._loss_agg = None

        self.node = None  # MDP state
        self._tasks_init = None
        self._ch_avail_init = None

        self._seq_opt = None

        # Observation space
        space_ch = self.problem_gen.ch_avail_gen.space
        max_duration = spaces_tasking.get_space_lims(self.problem_gen.task_gen.param_spaces['duration'])[1]
        self._obs_space_ch = Box(space_ch.low.item(), space_ch.high + self.n_tasks * max_duration, shape=(self.n_ch,),
                                 dtype=float)

        self._obs_space_seq = MultiDiscrete(np.full(self.n_tasks, 2))

        _obs_space_features = spaces_tasking.stack(self.features['space'])
        self._obs_space_tasks = spaces_tasking.broadcast_to(_obs_space_features,
                                                            shape=(self.n_tasks, len(self.features)))

        self.observation_space = Dict(  # note: `spaces` attribute is `OrderedDict` with sorted keys
            ch_avail=self._obs_space_ch,
            seq=self._obs_space_seq,
            tasks=self._obs_space_tasks,
        )

        # Action space
        self.steps_per_episode = None  # TODO: deprecate?
        self.action_space = None

    n_tasks = property(lambda self: self.problem_gen.n_tasks)
    n_ch = property(lambda self: self.problem_gen.n_ch)

    n_features = property(lambda self: len(self.features))

    tasks = property(lambda self: self.node.tasks)
    ch_avail = property(lambda self: self.node.ch_avail)

    def __str__(self):
        if self.node is None:
            _status = 'Initialized'
        else:
            _status = f'{len(self.node.seq)}/{self.n_tasks}'
        return f"{self.__class__.__name__}({_status})"

    def summary(self):
        str_ = f"{self.__class__.__name__}"
        str_ += f"\n- Features: {self.features['name'].tolist()}"
        str_ += f"\n- Sorting: {self._sort_func_str}"
        str_ += f"\n- Task shifting: {self.time_shift}"
        str_ += f"\n- Masking: {self.masking}"
        return str_

    @property
    def problem_gen(self):
        return self._problem_gen

    @problem_gen.setter
    def problem_gen(self, value):
        if self._problem_gen.task_gen != value.task_gen or self._problem_gen.ch_avail_gen != value.ch_avail_gen:
            raise ValueError('New generator must match.')
        self._problem_gen = value

    @property
    def sorted_index(self):
        """Indices for re-ordering of observation rows."""
        if callable(self.sort_func):
            values = np.array([self.sort_func(task) for task in self.tasks])
            values[self.node.seq] = np.inf  # scheduled tasks to the end
            return np.argsort(values)
        else:
            return np.arange(self.n_tasks)

    @property
    def sorted_index_inv(self):
        _idx_list = self.sorted_index.tolist()
        return np.array([_idx_list.index(n) for n in range(self.n_tasks)])

    def _obs_ch_avail(self):
        if self.normalize:
            low, high = self._obs_space_ch.low, self._obs_space_ch.high
            return (self.ch_avail - low) / (high - low)
        else:
            return self.ch_avail

    def _obs_seq(self):
        return np.array([1 if n in self.node.seq else 0 for n in self.sorted_index])

    def _obs_tasks(self):
        """Observation tensor for task features."""
        obs_tasks = np.array([[func(task) for func in self.features['func']] for task in self.tasks])
        if self.masking:
            obs_tasks[self.node.seq] = 0.  # zero out observation rows for scheduled tasks

        return obs_tasks[self.sorted_index]  # sort individual task observations

    def obs(self):
        """Complete observation."""
        # data = tuple(getattr(self, f"_obs_{key}")() for key in self.observation_space)  # invoke `_obs_tasks`, etc.
        # dtype = [(key, space.dtype, space.shape) for key, space in self.observation_space.spaces.items()]
        # return np.array(data, dtype=dtype)

        # return OrderedDict([(key, getattr(self, f"_obs_{key}")()) for key in self.observation_space])
        return {key: getattr(self, f"_obs_{key}")() for key in self.observation_space}

    @staticmethod
    @abstractmethod
    def infer_valid_mask(obs):
        """Create a binary valid action mask from an observation."""
        raise NotImplementedError

    # @abstractmethod
    # def infer_action_space(self, obs):
    #     """Determines the action `gym.Space` from an observation."""
    #     raise NotImplementedError

    def _update_spaces(self):
        """Update observation and action spaces."""
        pass  # TODO: varying `n_tasks` control

    def reset(self, tasks=None, ch_avail=None, solve=False, rng=None):
        """
        Reset environment by re-initializing node object with random (or user-specified) tasks/channels.

        Parameters
        ----------
        tasks : Sequence of task_scheduling.tasks.Base, optional
            Optional task set for non-random reset.
        ch_avail : Sequence of float, optional
            Optional initial channel availabilities for non-random reset.
        solve : bool
            Solves for and stores the Branch & Bound optimal schedule.
        rng : int or RandomState or Generator, optional
            NumPy random number generator or seed. Instance RNG if None.

        Returns
        -------
        numpy.ndarray
            Observation.

        """

        if tasks is None or ch_avail is None:  # generate new scheduling problem
            out = list(self.problem_gen(1, solve=solve, rng=rng))[0]
            if solve:
                (tasks, ch_avail), (sch, *__) = out
                self._seq_opt = np.argsort(sch['t'])  # optimal schedule (see `test_tree_nodes.test_argsort`)
            else:
                tasks, ch_avail = out
                self._seq_opt = None
        elif len(tasks) != self.n_tasks:
            raise ValueError(f"Input `tasks` must be None or a list of {self.n_tasks} tasks")
        elif len(ch_avail) != self.n_ch:
            raise ValueError(f"Input `ch_avail` must be None or an array of {self.n_ch} channel availabilities")

        self._tasks_init, self._ch_avail_init = tasks, ch_avail  # store problem before any in-place operations

        if self.time_shift:
            self.node = tree_search.ScheduleNodeShift(tasks, ch_avail)
        else:
            self.node = tree_search.ScheduleNode(tasks, ch_avail)

        self._loss_agg = self.node.loss  # Loss can be non-zero due to time origin shift during node initialization

        self._update_spaces()

        return self.obs()

    def step(self, action):
        """
        Updates environment state (node) based on task index input.

        Parameters
        ----------
        action : int or Sequence of int
            Task indices.

        Returns
        -------
        ndarray
            Observation.
        float
            Reward (negative loss) achieved by the complete sequence.
        bool
            Indicates the end of the learning episode.
        dict
            Auxiliary diagnostic information (helpful for debugging, and sometimes learning).

        """

        action = self.sorted_index[action]  # decode task index to original order
        self.node.seq_extend(action)  # updates sequence, loss, task parameters, etc.

        loss_step, self._loss_agg = self.node.loss - self._loss_agg, self.node.loss
        reward = -loss_step

        done = len(self.node.seq_rem) == 0  # sequence is complete

        self._update_spaces()

        return self.obs(), reward, done, {}

    def render(self, mode='human'):
        if mode != 'human':
            raise NotImplementedError("Render `mode` must be 'human'")

        fig, axes = plt.subplots(2, num='render', clear=True,
                                 figsize=[12.8, 6.4], gridspec_kw={'left': .05, 'right': 0.7})
        # fig.subplots_adjust(right=0.7)
        # fig.set_size_inches(12.8, 6.4)

        fig.suptitle(", ".join((str(self), f"Loss = {self._loss_agg:.3f}")), y=0.95)

        plot_schedule(self._tasks_init, self.node.sch, n_ch=self.n_ch, loss=self._loss_agg, ax=axes[1],
                      ax_kwargs=dict(title=''))

        lows, highs = zip(axes[1].get_xlim(), *(task.plot_lim for task in self._tasks_init))
        t_plot = np.arange(min(*lows), max(*highs), 0.01)
        plot_task_losses(self._tasks_init, t_plot, ax=axes[0], ax_kwargs=dict(xlabel=''))

        lows, highs = zip(*(ax.get_xlim() for ax in axes))
        x_lims = min(lows), max(highs)
        for ax in axes:
            ax.set(xlim=x_lims)

        fig.legend(*axes[0].get_legend_handles_labels(), loc='center right', bbox_to_anchor=(1., .5))
        axes[0].get_legend().remove()
        axes[1].get_legend().remove()

        return fig

    def close(self):
        self.node = None

    def seed(self, seed=None):
        self.problem_gen.rng = seed

    @abstractmethod
    def opt_action(self):  # TODO: implement a optimal policy calling obs?
        """Optimal action based on current state."""
        raise NotImplementedError

    def data_gen(self, n_batch, batch_size=1, weight_func=None, verbose=0, rng=None):
        """
        Generate observation-action data for learner training and evaluation.

        Parameters
        ----------
        n_batch : int
            Number of batches of observation-action pair data to generate.
        batch_size : int
            Number of scheduling problems to make data from per yielded batch.
        weight_func : callable, optional
            Function mapping environment object to a training weight.
        verbose : {0, 1, 2}, optional
            0: silent, 1: add batch info, 2: add problem info
        rng : int or RandomState or Generator, optional
            NumPy random number generator or seed. Instance RNG if None.

        Yields
        ------
        ndarray
            Predictor data.
        ndarray
            Target data.
        ndarray, optional
            Sample weights.

        """

        # TODO: refactor SL data gen to `mdp.supervised.Base`?

        for i_batch in range(n_batch):
            # if verbose >= 1:
            #     print(f'Batch: {i_batch + 1}/{n_batch}', end='\n')

            steps_total = batch_size * self.steps_per_episode

            if isinstance(self.observation_space, Dict):
                # data = list(zip(*(np.empty((steps_total, *space.shape), dtype=space.dtype)
                #                 for space in self.observation_space.spaces.values())))
                # dtype = [(key, space.dtype, space.shape) for key, space in self.observation_space.spaces.items()]
                # x_set = np.array(data, dtype=dtype)

                # x_set = OrderedDict([(key, np.empty((steps_total, *space.shape), dtype=space.dtype))
                #                      for key, space in self.observation_space.spaces.items()])
                x_set = {key: np.empty((steps_total, *space.shape), dtype=space.dtype)
                         for key, space in self.observation_space.spaces.items()}
            else:
                x_set = np.empty((steps_total, *self.observation_space.shape), dtype=self.observation_space.dtype)

            y_set = np.empty((steps_total, *self.action_space.shape), dtype=self.action_space.dtype)
            w_set = np.empty(steps_total, dtype=float)

            for i_gen in range(batch_size):
                # if verbose >= 2:
                #     print(f'  Problem: {i_gen + 1}/{batch_size}', end='\r')
                if verbose >= 1:
                    print(f'Problem: {batch_size * i_batch + i_gen + 1}/{n_batch * batch_size}', end='\r')

                obs = self.reset(solve=True, rng=rng)  # generates new scheduling problem

                done = False
                i_step = 0
                while not done:
                    i = i_gen * self.steps_per_episode + i_step

                    action = self.opt_action()

                    # x_set[i] = obs
                    for key in self.observation_space:
                        x_set[key][i] = obs[key]
                    y_set[i] = action

                    obs, reward, done, info = self.step(action)  # updates environment state
                    if callable(weight_func):
                        w_set[i] = weight_func(obs, action, reward)  # TODO: use rewards for weighting!?!

                    i_step += 1

            if callable(weight_func):
                yield x_set, y_set, w_set
            else:
                yield x_set, y_set

    def data_gen_full(self, n_gen, weight_func=None, verbose=0):
        """Generate observation-action data, return in single feature/class arrays."""
        data, = self.data_gen(n_batch=1, batch_size=n_gen, weight_func=weight_func, verbose=verbose)
        return data

    # def mask_probability(self, p):  # TODO: deprecate?
    #     """Returns masked action probabilities."""
    #     return np.array(p)


class Index(Base):
    def __init__(self, problem_gen, features=None, normalize=True, sort_func=None, time_shift=False, masking=False):
        """Tasking environment with actions of single task indices.

        Parameters
        ----------
        problem_gen : generators.problems.Base
            Scheduling problem generation object.
        features : numpy.ndarray, optional
            Structured numpy array of features with fields 'name', 'func', and 'space'.
        normalize : bool, optional
            Rescale task features to unit interval.
        sort_func : function or str, optional
            Method that returns a sorting value for re-indexing given a task index 'n'.
        time_shift : bool, optional
            Enables task re-parameterization after sequence updates.
        masking : bool, optional
            If True, features are zeroed out for scheduled tasks.

        """
        super().__init__(problem_gen, features, normalize, sort_func, time_shift, masking)

        # Action space
        self.steps_per_episode = self.n_tasks
        self.action_space = spaces_tasking.DiscreteMasked(self.n_tasks)  # TODO: necessary with valid models?

    def _update_spaces(self):
        """Update observation and action spaces."""
        seq_rem_sort = self.sorted_index_inv[list(self.node.seq_rem)]
        self.action_space.mask = np.isin(np.arange(self.n_tasks), seq_rem_sort, invert=True)

    def opt_action(self):
        """Optimal action based on current state."""
        if self._seq_opt is None:
            raise ValueError("Optimal action cannot be determined unless `reset` was called with `solve=True`.")

        n = self._seq_opt[len(self.node.seq)]  # next optimal task index
        return self.sorted_index_inv[n]  # encode task index to sorted action

    @staticmethod
    def infer_valid_mask(obs):
        """Create a binary valid action mask from an observation."""
        return obs['seq']

    # def infer_action_space(self, obs):
    #     """Determines the action Gym.Space from an observation."""
    #     obs = np.asarray(obs)
    #     if obs.ndim > 3:
    #         raise ValueError("Input must be a single observation.")
    #
    #     mask = self.infer_valid_mask(obs).astype(bool)
    #     return spaces_tasking.DiscreteMasked(self.n_tasks, mask)

    # def mask_probability(self, p):
    #     """Returns masked action probabilities based on unscheduled task indices."""
    #     return np.ma.masked_array(p, self.action_space.mask)


def seq_to_int(seq, check_input=True):
    """
    Map an index sequence permutation to a non-negative integer.

    Parameters
    ----------
    seq : Sequence of int
        Elements are unique in range(len(seq)).
    check_input : bool
        Enables value checking of input sequence.

    Returns
    -------
    int
        Takes values in range(factorial(len(seq))).
    """

    length = len(seq)
    seq_rem = list(range(length))  # remaining elements
    if check_input and set(seq) != set(seq_rem):
        raise ValueError(f"Input must have unique elements in range({length}).")

    num = 0
    for i, n in enumerate(seq):
        k = seq_rem.index(n)  # position of index in remaining elements
        num += k * factorial(length - 1 - i)
        seq_rem.remove(n)

    return num


def int_to_seq(num, length, check_input=True):
    """
    Map a non-negative integer to an index sequence permutation.

    Parameters
    ----------
    num : int
        In range(factorial(length))
    length : int
        Length of the output sequence.
    check_input : bool
        Enables value checking of input number.

    Returns
    -------
    tuple
        Elements are unique in factorial(len(seq)).
    """

    if check_input and num not in range(factorial(length)):
        raise ValueError(f"Input 'num' must be in range(factorial({length})).")

    seq_rem = list(range(length))  # remaining elements
    seq = []
    while len(seq_rem) > 0:
        radix = factorial(len(seq_rem) - 1)
        i, num = num // radix, num % radix

        n = seq_rem.pop(i)
        seq.append(n)

    return tuple(seq)


# TODO: deprecate environments below?

# class IndexUni(Index):
#     def __init__(self, problem_gen, features=None, normalize=True, sort_func=None, time_shift=False, masking=False):
#         """`Index` environment with single tensor observations. Concatenates sequence and task feature tensors.
#
#         Parameters
#         ----------
#         problem_gen : generators.problems.Base
#             Scheduling problem generation object.
#         features : numpy.ndarray, optional
#             Structured numpy array of features with fields 'name', 'func', and 'space'.
#         normalize : bool, optional
#             Rescale task features to unit interval.
#         sort_func : function or str, optional
#             Method that returns a sorting value for re-indexing given a task index 'n'.
#         time_shift : bool, optional
#             Enables task re-parameterization after sequence updates.
#         masking : bool, optional
#             If True, features are zeroed out for scheduled tasks.
#
#         """
#         super().__init__(problem_gen, features, normalize, sort_func, time_shift, masking)
#
#         # Observation space
#         _space_seq_reshape = spaces_tasking.reshape(self._obs_space_seq, (1, self.n_tasks, 1))
#         self.observation_space = spaces_tasking.concatenate((_space_seq_reshape, self._obs_space_tasks), axis=-1)
#
#     def obs(self):
#         """Complete observation."""
#         _obs_seq_reshape = self._obs_seq().reshape((1, self.n_tasks, 1))
#         return np.concatenate((_obs_seq_reshape, self._obs_tasks()), axis=-1)


# class Seq(Base):
#     def __init__(self, problem_gen, features=None, normalize=True, sort_func=None, time_shift=False, masking=False,
#                  action_type='int'):
#         """Tasking environment with single action of a complete task index sequence.
#
#         Parameters
#         ----------
#         problem_gen : generators.problems.Base
#             Scheduling problem generation object.
#         features : numpy.ndarray, optional
#             Structured numpy array of features with fields 'name', 'func', and 'space'.
#         normalize : bool, optional
#             Rescale task features to unit interval.
#         sort_func : function or str, optional
#             Method that returns a sorting value for re-indexing given a task index 'n'.
#         time_shift : bool, optional
#             Enables task re-parameterization after sequence updates.
#         masking : bool, optional
#             If True, features are zeroed out for scheduled tasks.
#         action_type : {'seq', 'int'}, optional
#             If 'seq', action type is index sequence `Permutation`; if 'int', action space is `Discrete` and
#             index sequences are mapped to integers.
#
#         """
#         super().__init__(problem_gen, features, normalize, sort_func, time_shift, masking)
#
#         self.action_type = action_type  # 'seq' for sequences, 'int' for integers
#         if self.action_type == 'int':
#             self._action_space_map = lambda n: Discrete(factorial(n))
#         elif self.action_type == 'seq':
#             raise NotImplementedError("Deprecated.")
#             # self._action_space_map = lambda n: spaces_tasking.Permutation(n)
#         else:
#             raise ValueError
#
#         # Action space
#         self.steps_per_episode = 1
#         self.action_space = self._action_space_map(self.n_tasks)
#
#     def summary(self):
#         str_ = super().summary()
#         str_ += f"\n- Action type: {self.action_type}"
#         return str_
#
#     def step(self, action):
#         if self.action_type == 'int':
#             action = list(int_to_seq(action, self.n_tasks))  # decode integer to sequence
#
#         return super().step(action)
#
#     def opt_action(self):
#         """Optimal action based on current state."""
#         seq_action = self.sorted_index_inv[self._seq_opt]  # encode sequence to sorted actions
#         if self.action_type == 'int':
#             return seq_to_int(seq_action)
#         elif self.action_type == 'seq':
#             return seq_action
#
#     @staticmethod
#     def infer_valid_mask(obs):
#         """Create a binary valid action mask from an observation."""
#         return np.ones(factorial(len(obs)))
#
#     # def infer_action_space(self, obs):
#     #     """Determines the action Gym.Space from an observation."""
#     #     return self._action_space_map(len(obs))
