"""
Task scheduling example.

Define a set of task objects and scheduling algorithms. Assess achieved loss and runtime.

"""

from time import perf_counter       # TODO: use builtin module timeit instead? or cProfile?
from math import factorial, floor
from functools import partial

import numpy as np
import matplotlib.pyplot as plt

from util.generic import algorithm_repr, check_rng
from util.results import check_valid, eval_loss
from util.plot import plot_task_losses, plot_schedule, scatter_loss_runtime

from tasks import ReluDropGenerator
from tree_search import branch_bound, mcts_orig, mcts, random_sequencer, earliest_release
from env_tasking import SeqTaskingEnv, StepTaskingEnv, wrap_agent, RandomAgent

plt.style.use('seaborn')


# %% Inputs
n_gen = 2      # number of task scheduling problems

n_tasks = 8
n_channels = 2

task_gen = ReluDropGenerator(duration_lim=(3, 6), t_release_lim=(0, 4), slope_lim=(0.5, 2),
                             t_drop_lim=(6, 12), l_drop_lim=(35, 50), rng=None)       # task set generator


def ch_avail_gen(n_ch, rng=check_rng(None)):     # channel availability time generator
    # TODO: rng is a mutable default argument!
    return rng.uniform(0, 2, n_ch)


# Algorithms

env = StepTaskingEnv(n_tasks, task_gen, n_channels, ch_avail_gen)
random_agent = wrap_agent(env, RandomAgent(env.action_space))

alg_funcs = [partial(branch_bound, verbose=False),
             partial(random_agent)]

alg_n_iter = [1, 2]       # number of runs per problem

# alg_funcs = [partial(branch_bound, verbose=False),
#              partial(mcts_orig, n_mc=[floor(.1 * factorial(n)) for n in range(n_tasks, 0, -1)], verbose=False),
#              partial(mcts, n_mc=.1*factorial(n_tasks), verbose=False),
#              partial(earliest_release, do_swap=True),
#              partial(random_sequencer),
#              partial(random_agent)]
#
# alg_n_iter = [2, 2, 1, 1, 5, 5]       # number of runs per problem

alg_reprs = list(map(algorithm_repr, alg_funcs))    # string representations


# %% Evaluate
t_run_iter = np.array(list(zip(*[np.empty((n_gen, n_iter)) for n_iter in alg_n_iter])),
                      dtype=list(zip(alg_reprs, len(alg_reprs) * [np.float], [(n_run,) for n_run in alg_n_iter])))

l_ex_iter = np.array(list(zip(*[np.empty((n_gen, n_iter)) for n_iter in alg_n_iter])),
                     dtype=list(zip(alg_reprs, len(alg_reprs) * [np.float], [(n_run,) for n_run in alg_n_iter])))

t_run_mean = np.array(list(zip(*np.empty((len(alg_reprs), n_gen)))),
                      dtype=list(zip(alg_reprs, len(alg_reprs) * [np.float])))

l_ex_mean = np.array(list(zip(*np.empty((len(alg_reprs), n_gen)))),
                     dtype=list(zip(alg_reprs, len(alg_reprs) * [np.float])))

for i_gen in range(n_gen):      # Generate new scheduling problem
    print(f'Task Set: {i_gen + 1}/{n_gen}')

    tasks = task_gen.rand_tasks(n_tasks)
    ch_avail = ch_avail_gen(n_channels)

    _, ax_gen = plt.subplots(2, 1, num=f'Task Set: {i_gen + 1}', clear=True)
    plot_task_losses(tasks, ax=ax_gen[0])

    for alg_repr, alg_func, n_iter in zip(alg_reprs, alg_funcs, alg_n_iter):
        for iter_ in range(n_iter):      # Perform new algorithm runs
            print(f'  {alg_repr} - Iteration: {iter_ + 1}/{n_iter}', end='\r')

            t_start = perf_counter()
            t_ex, ch_ex = alg_func(tasks, ch_avail)
            t_run = perf_counter() - t_start

            check_valid(tasks, t_ex, ch_ex)
            l_ex = eval_loss(tasks, t_ex)

            t_run_iter[alg_repr][i_gen, iter_] = t_run
            l_ex_iter[alg_repr][i_gen, iter_] = l_ex

            # plot_schedule(tasks, t_ex, ch_ex, l_ex=l_ex, alg_repr=alg_repr, ax=None)

        t_run_mean[alg_repr][i_gen] = t_run_iter[alg_repr][i_gen].mean()
        l_ex_mean[alg_repr][i_gen] = l_ex_iter[alg_repr][i_gen].mean()

        print('')
        print(f"    Avg. Runtime: {t_run_mean[alg_repr][i_gen]:.2f} (s)")
        print(f"    Avg. Execution Loss: {l_ex_mean[alg_repr][i_gen]:.2f}")

    scatter_loss_runtime(t_run_iter[i_gen], l_ex_iter[i_gen], ax=ax_gen[1])

print('')

_, ax_results = plt.subplots(num='Results', clear=True)
scatter_loss_runtime(t_run_mean, l_ex_mean,
                     ax=ax_results, ax_kwargs={'title': 'Average performance on random task sets'})
