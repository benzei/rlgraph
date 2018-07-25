# Copyright 2018 The RLGraph-Project, All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os.path

from rlgraph import RLGraphError
from rlgraph.components import Optimizer, MultiGpuSyncOptimizer
from rlgraph.utils.util import default_dict


def parse_saver_spec(saver_spec):
    default_spec = dict(
        # The directory in which to store model checkpoint files.
        directory=os.path.expanduser("~/rlgraph_checkpoints/"),  # default=home dir
        # The base file name for a saved checkpoint.
        checkpoint_basename="model.ckpt",
        # How many files to maximally store for one graph.
        max_checkpoints=5,
        # Every how many seconds do we save? None if saving frequency should be step based.
        save_secs=600,
        # Every how many steps do we save? None if saving frequency should be time (seconds) based.
        save_steps=None
    )
    return default_dict(saver_spec, default_spec)


def get_optimizer_from_device_strategy(optimizer_spec, device_strategy='default'):
    """
    Returns optimizer object based on optimizer_spec and device strategy.

    Depending on the device strategy, the default optimizer object (e.g. an AdamOptimizer)
    will be wrapped into a specific device optimizer

    Args:
        optimizer_spec (dict): Optimizer configuration options.
        device_strategy (str): Device strategy to apply onto the optimizer.

    Returns:
        Optimizer: Optimizer object.
    """
    if device_strategy == 'default' or device_strategy == 'custom':
        return Optimizer.from_spec(optimizer_spec)
    elif device_strategy == 'multi_gpu_sync':
        local_optimizer = Optimizer.from_spec(optimizer_spec)
        # Wrap local optimizer in multi device optimizer.
        return MultiGpuSyncOptimizer(local_optimizer=local_optimizer)
    else:
        raise RLGraphError("Device strategy {} is not allowed. Allowed strategies are 'default', 'custom',"
                        "and 'multi_gpu_sync'".format(device_strategy))


def parse_summary_spec(summary_spec):
    """
    Expands summary spec with default values where necessary.

    Args:
        summary_spec (dict): Summary options.

    Returns:
        dict: Summary spec updated with default values.
    """
    default_spec = dict(
        # The directory in which to store the summary files.
        directory=os.path.expanduser("~/rlgraph_summaries/"),  # default=home dir
        # A regexp pattern that a summary op (including its global scope) has to match in order for it to
        # be included in the graph's summaries.
        summaries_regexp="",
        # Every how many seconds do we save a summary? None if saving frequency should be step based.
        save_secs=120,
        # Every how many steps do we save a summary? None if saving frequency should be time (seconds) based.
        save_steps=None
    )
    return default_dict(summary_spec, default_spec)


def parse_execution_spec(execution_spec):
    """
    Parses execution parameters and inserts default values where necessary.

    Args:
        execution_spec (Optional[dict]): Execution spec dict. Must specify an execution mode
            "single" or "distributed". If mode "distributed", must specify a "distributed_spec"
            containing:
             - a key cluster_spec mapping to a ClusterSpec object,
             - a "job" for the job name,
             - an integer "task_index"
             - a boolean "global_shared_memory" to indicate if data collection is shared globally
               or workers collect and update locally. Defaults to true.

    Returns:
        dict: The sanitized execution_spec dict.
    """
    # TODO these are tensorflow specific
    # If no spec given.
    default_spec = dict(
        mode="single",
        distributed_spec=None,

        # Device placement settings.
        device_strategy="default",
        default_device=None,
        device_map=None,

        session_config=None,
        # Random seed for the tf graph.
        seed=None,
        # Enabling the tf profiler?
        enable_profiler=False,
        # With which frequency do we print out profiler information?
        profiler_frequency=1000
    )
    execution_spec = default_dict(execution_spec, default_spec)

    # Sub specifications:

    # Distributed specifications.
    if execution_spec.get("mode") == "distributed":
        default_distributed = dict(
            job="ps",
            task_index=0,
            cluster_spec=dict(
                ps=["localhost:22222"],
                worker=["localhost:22223"]
            ),
            global_shared_memory=True,
            protocol=None
        )
        execution_spec["distributed_spec"] = default_dict(execution_spec.get("distributed_spec"), default_distributed)

    # Session config.
    default_session_config = dict(
        allow_soft_placement=True,
        log_device_placement=False
    )
    execution_spec["session_config"] = default_dict(execution_spec.get("session_config"), default_session_config)

    return execution_spec


def parse_observe_spec(observe_spec):
    """
    Parses parameters for `Agent.observe()` calls and inserts default values where necessary.

    Args:
        observe_spec (Optional[dict]): Observe spec dict.

    Returns:
        dict: The sanitized observe_spec dict.
    """
    # If no spec given.
    default_spec = dict(
        # Do we buffer observations in python before sending them through the graph?
        buffer_enabled=True,
        # Fill buffer with n records before sending them through the graph.
        buffer_size=100,  # only if buffer_enabled=True
        # The unit in which we measure frequency: one of "timesteps", "episodes", "sec".
    )
    observe_spec = default_dict(observe_spec, default_spec)

    return observe_spec


def parse_update_spec(update_spec):
    """
    Parses update/learning parameters and inserts default values where necessary.

    Args:
        update_spec (Optional[dict]): Update/Learning spec dict.

    Returns:
        dict: The sanitized update_spec dict.
    """
    # If no spec given.
    default_spec = dict(
        # Whether to perform calls to `Agent.update()` at all.
        do_updates=True,
        # The unit in which we measure frequency: one of "timesteps", "episodes", "sec".
        #unit="timesteps", # TODO: not supporting any other than timesteps
        # The number of 'units' to wait before we do any updating at all.
        steps_before_update=0,
        # The frequency with which we update (given in `unit`).
        update_interval=4,
        # The number of consecutive `Agent.update()` calls per update.
        update_steps=1,
        # The batch size with which to update (e.g. when pulling records from a memory).
        batch_size=64,
        sync_interval=128
    )
    update_spec = default_dict(update_spec, default_spec)
    # Assert that the synch interval is a multiple of the update_interval.
    if update_spec["sync_interval"] / update_spec["update_interval"] != \
        update_spec["sync_interval"] // update_spec["update_interval"]:
        raise RLGraphError(
            "ERROR: sync_interval ({}) must be multiple of update_interval "
            "({})!".format(update_spec["sync_interval"], update_spec["update_interval"])
        )

    return update_spec
