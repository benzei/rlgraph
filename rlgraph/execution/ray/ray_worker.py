# Copyright 2018 The RLgraph authors. All Rights Reserved.
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

from rlgraph.components import PreprocessorStack
from rlgraph.environments import VectorEnv, SequentialVectorEnv
from six.moves import xrange as range_
import numpy as np
import time

from rlgraph import SMALL_NUMBER
from rlgraph.backend_system import get_distributed_backend
from rlgraph.execution.environment_sample import EnvironmentSample
from rlgraph.execution.ray import RayExecutor
from rlgraph.execution.ray.ray_actor import RayActor
from rlgraph.execution.ray.ray_util import ray_compress

if get_distributed_backend() == "ray":
    import ray


@ray.remote
class RayWorker(RayActor):
    """
    Ray wrapper for single threaded worker, provides further api methods to interact
    with the agent used in the worker.
    """

    def __init__(self, agent_config, env_spec, worker_spec, frameskip=1):
        """
        Creates agent and environment for Ray worker.

        Args:
            agent_config (dict): Agent configuration dict.
            env_spec (dict): Environment config for environment to run.
            worker_spec (dict): Worker parameters.
            frameskip (int): How often actions are repeated after retrieving them from the agent.
        """
        assert get_distributed_backend() == "ray"
        # Internal frameskip of env.
        self.env_frame_skip = env_spec.get("frameskip", 1)

        # Worker computes weights for prioritized sampling.
        self.worker_computes_weights = worker_spec.pop("worker_computes_weights", True)
        self.n_step_adjustment = worker_spec.pop("n_step_adjustment", 1)
        self.num_environments = worker_spec.pop("num_worker_environments", 1)
        num_background_envs = worker_spec.pop("num_background_envs", 1)

        # TODO from spec once we decided on vectorization.
        self.vector_env = SequentialVectorEnv(self.num_environments, env_spec, num_background_envs)

        # Then update agent config.
        agent_config['state_space'] = self.vector_env.state_space
        agent_config['action_space'] = self.vector_env.action_space

        self.discount = agent_config.get("discount", 0.99)
        self.agent = self.setup_agent(agent_config, worker_spec)

        # Python based preprocessor.
        self.preprocessor = self.setup_preprocessor(agent_config.get("preprocessing_spec", None),
                                                    self.vector_env.state_space)
        self.worker_frameskip = frameskip

        # Save these so they can be fetched after training if desired.
        self.episode_rewards = list()
        self.episode_timesteps = list()
        self.total_worker_steps = 0
        self.episodes_executed = 0

        # Step time and steps done per call to execute_and_get to measure throughput of this worker.
        self.sample_times = list()
        self.sample_steps =  list()
        self.sample_env_frames =  list()

        # To continue running through multiple exec calls.
        self.last_states = self.vector_env.reset_all()
        self.agent.reset()

        # Was the last state a terminal state so env should be reset in next call?
        self.env_ids = ["env_".format(i) for i in range_(self.num_environments)]
        self.last_ep_timesteps = [0 for _ in range_(self.num_environments)]
        self.last_ep_rewards = [0 for _ in range_(self.num_environments)]
        self.last_terminals = [False for _ in range_(self.num_environments)]

    def get_constructor_success(self):
        """
        For debugging: fetch the last attribute. Will fail if constructor failed.
        """
        return not self.last_terminals[0]

    def setup_preprocessor(self, preprocessing_spec, in_space):
        if preprocessing_spec is not None:
            # TODO move ingraph for python component assembly.
            # Set scopes.
            scopes = [preprocessor["scope"] for preprocessor in preprocessing_spec]
            # Set backend to python.
            for spec in preprocessing_spec:
                spec["backend"] = "python"
            processor_stack = PreprocessorStack(*preprocessing_spec, backend="python")
            for sub_comp_scope in scopes:
                processor_stack.sub_components[sub_comp_scope].create_variables(input_spaces=dict(
                    apply=[in_space]
                ), action_space=None)

            return processor_stack
        else:
            return None

    def setup_agent(self, agent_config, worker_spec):
        """
        Sets up agent, potentially modifying its configuration via worker specific settings.
        """
        sample_exploration = worker_spec.pop("sample_exploration", False)
        # Adjust exploration for this worker.
        if sample_exploration:
            exploration_min_value = worker_spec.pop("exploration_min_value", 0.0)
            epsilon_spec = agent_config["exploration_spec"]["epsilon_spec"]

            if "decay_spec" in epsilon_spec:
                decay_from = epsilon_spec["decay_spec"]["from"]
                assert decay_from >= exploration_min_value, \
                    "Min value for exploration sampling must be smaller than" \
                    "decay_from {} in exploration_spec but is {}.".format(decay_from, exploration_min_value)

                # Sample a new initial epsilon from the interval [exploration_min_value, decay_from).
                sampled_from = np.random.uniform(low=exploration_min_value, high=decay_from)
                epsilon_spec["decay_spec"]["from"] = sampled_from

        # Worker execution spec may differ from controller/learner.
        worker_exec_spec = worker_spec.get("execution_spec", None)
        if worker_exec_spec is not None:
            agent_config.update(execution_spec=worker_exec_spec)

        return RayExecutor.build_agent_from_config(agent_config)

    def execute_and_get_timesteps(
        self,
        num_timesteps,
        max_timesteps_per_episode=0,
        use_exploration=True,
        break_on_terminal=False
    ):
        """
        Collects and returns time step experience.

        Args:
            break_on_terminal (Optional[bool]): If true, breaks when a terminal is encountered. If false,
                executes exactly 'num_timesteps' steps.
        """
        start = time.monotonic()
        timesteps_executed = 0
        episodes_executed = [0 for _ in range_(self.num_environments)]
        env_frames = 0

        # Dict of env_index -> trajectory for that environment during this call.
        sample_states, sample_actions, sample_rewards, sample_terminals = dict(), dict(), dict(), dict()
        for env_id in self.env_ids:
            sample_states[env_id] = list()
            sample_actions[env_id] = list()
            sample_rewards[env_id] = list()
            sample_terminals[env_id] = list()

        break_loop = False
        next_states = [np.zeros_like(self.last_states) for _ in range_(self.num_environments)]

        while timesteps_executed < num_timesteps:
            # Reset envs and Agent either if finished an episode in current loop or if last state
            # from previous execution was terminal for that environment.
            env_states, episode_rewards, episode_timesteps = list(), list(), list()
            is_reset = False

            # Check continuation of episodes between calls.
            for i in range_(self.num_environments):
                if self.last_terminals[i] is True or episodes_executed[i] > 0:
                    # Reset this environment.
                    env_states.append(self.vector_env.reset(i))

                    if not is_reset:
                        self.agent.reset()
                    else:
                        is_reset = True
                    # The reward accumulated over one episode.
                    self.last_ep_rewards[i] = 0
                    episode_rewards.append(0)
                    episode_timesteps.append(0)
                else:
                    # Continue training between calls.
                    env_states.append(self.last_states[i])
                    episode_rewards.append(self.last_ep_rewards[i])
                    episode_timesteps.append(self.last_ep_timesteps[i])

            # Whether the episode in each env has terminated.
            terminals = [False for _ in range_(self.num_environments)]
            while True:
                state_batch = self.agent.state_space.force_batch(env_states)
                actions, preprocessed_states = self.agent.get_action(
                    states=state_batch, use_exploration=use_exploration, extra_returns="preprocessed_states")

                rewards = dict()
                for i, env_id in enumerate(self.env_ids):
                    sample_states[env_id].append(preprocessed_states[i])
                    sample_actions[env_id].append(actions[i])
                    # Also init step rewards here for frame skip accumulation.
                    rewards[env_id] = 0

                # Accumulate the reward over n env-steps and envs (equals one action pick). n=self.frameskip.
                for _ in range_(self.worker_frameskip):
                    next_states, step_rewards, terminals, infos = self.vector_env.step(actions=actions)
                    env_frames += 1

                    for i, env_id in enumerate(self.env_ids):
                        rewards[env_id] += step_rewards[i]
                    # TODO Break when all or any are terminal?
                    if np.any(terminals):
                        break

                timesteps_executed += self.num_environments
                # Update samples.
                for i, env_id in enumerate(self.env_ids):
                    episode_timesteps[i] += 1
                    # Each position is the running episode reward of that episosde. Add step reward.
                    episode_rewards[i] += rewards[env_id]
                    sample_rewards[env_id].append(rewards[env_id])
                    sample_terminals[env_id].append(terminals[i])

                env_states = next_states

                # Account for all finished episodes.
                for i, env_id in enumerate(self.env_ids):
                    # Conclude episode for that worker
                    if terminals[i] or (0 < max_timesteps_per_episode <= episode_timesteps[i]):
                        self.episode_rewards.append(episode_rewards[i])
                        self.episode_timesteps.append(episode_timesteps[i])
                        episodes_executed[i] += 1
                        self.episodes_executed += 1
                    # TODO Do we need to break here? While True is only broken when we are fully done atm, see below.

                if 0 < num_timesteps <= timesteps_executed:
                    self.total_worker_steps += timesteps_executed
                    break

                # If any episode finished, stop rollout.
                if np.any(terminals) and break_on_terminal:
                    break_loop = True
                    break

            if break_loop:
                break

        self.last_terminals = terminals
        self.last_states = env_states
        self.last_ep_rewards = episode_rewards

        total_time = (time.monotonic() - start) or 1e-10
        self.sample_steps.append(timesteps_executed)
        self.sample_times.append(total_time)
        self.sample_env_frames.append(env_frames)

        # Merge results into one batch.
        batch_states, batch_actions, batch_rewards, batch_next_states, batch_terminals = list(), list(), list(),\
            list(), list()

        to_preprocess_list = list()
        next_state_fragments = list()
        for i, env_id in enumerate(self.env_ids):
            env_sample_states = sample_states[env_id]

            # Get next states for this environment's trajectory.
            env_sample_next_states = env_sample_states[1:]
            batch_states.extend(env_sample_states)
            if terminals[i]:
                to_preprocess = np.zeros_like(next_states[0])
            else:
                to_preprocess = next_states[i]

            # Append this state so we can preprocess all with one session call.
            to_preprocess_list.append(to_preprocess)
            next_state_fragments.append(env_sample_next_states)
            batch_actions.extend(sample_actions[env_id])
            batch_rewards.extend(sample_rewards[env_id])
            batch_terminals.extend(sample_terminals[env_id])

        # TODO this is really inconvenient -> maybe should do in python.
        next_states = self.agent.preprocessed_state_space.force_batch(to_preprocess_list)
        if self.preprocessor is not None:
            next_states = self.preprocessor.preprocess(next_states)

        # Finally assemble next states full sample: [env_0_ep_next, env_0_final_next, env_1_ep_next, env_1_final_next]
        for i in range_(self.num_environments):
            batch_next_states.extend(next_state_fragments[i])
            next_fragment = self.agent.preprocessed_state_space.force_batch(next_states[i])
            batch_next_states.extend(next_fragment)

        sample_batch, batch_size = self._process_sample_if_necessary(batch_states, batch_actions,
            batch_rewards, batch_next_states, batch_terminals)

        # Note that the controller already evaluates throughput so there is no need
        # for each worker to calculate expensive statistics now.
        return EnvironmentSample(
            sample_batch=sample_batch,
            batch_size=batch_size,
            metrics=dict(
                runtime=total_time,
                # Agent act/observe throughput.
                timesteps_executed=timesteps_executed,
                ops_per_second=(timesteps_executed / total_time),
            )
        )

    @ray.method(num_return_vals=2)
    def execute_and_get_with_count(
        self,
        num_timesteps,
        max_timesteps_per_episode=0,
        use_exploration=True,
        break_on_terminal=False
    ):
        sample = self.execute_and_get_timesteps(num_timesteps, max_timesteps_per_episode,
                                                use_exploration, break_on_terminal)
        return sample, sample.batch_size

    def set_policy_weights(self, weights):
        self.agent.set_policy_weights(weights)

    def get_workload_statistics(self):
        """
        Returns performance results for this worker.

        Returns:
            dict: Performance metrics.
        """
        # Adjust env frames for internal env frameskip:
        adjusted_frames = [env_frames * self.env_frame_skip for env_frames in self.sample_env_frames]
        return dict(
            episode_timesteps=self.episode_timesteps,
            episode_rewards=self.episode_rewards,
            min_episode_reward=np.min(self.episode_rewards),
            max_episode_reward=np.max(self.episode_rewards),
            mean_episode_reward=np.mean(self.episode_rewards),
            final_episode_reward=self.episode_rewards[-1],
            episodes_executed=self.episodes_executed,
            worker_steps=self.total_worker_steps,
            mean_worker_ops_per_second=sum(self.sample_steps) / sum(self.sample_times),
            mean_worker_env_frames_per_second=sum(adjusted_frames) / sum(self.sample_times)
        )

    def _process_sample_if_necessary(self, states, actions, rewards, next_states, terminals):
        """
        Post-processes sample, e.g. by computing priority weights, compressing, applying
        n-step corrections, ported from ray RLLib.

        Args:
            states (list): List of states.
            actions (list): List of actions.
            rewards (list): List of rewards.
            next_states: (list): List of next_states.
            terminals (list): List of terminals.

        Returns:
            dict: Sample batch dict.
        """
        if self.n_step_adjustment > 1:
            for i in range_(len(rewards) - self.n_step_adjustment + 1):
                # Ignore terminals.
                if terminals[i]:
                    continue
                for j in range_(1, self.n_step_adjustment):
                    states[i] = states[i + j]
                    rewards[i] += self.discount ** j * rewards[i + j]

                    # Set remaining reward to 0.
                    if terminals[i + j]:
                        break

            # Truncate.
            new_len = len(states) - self.n_step_adjustment + 1
            for arr in [states, actions, rewards, next_states, terminals]:
                del arr[new_len:]

        weights = np.ones_like(rewards)

        # Compute loss-per-item.
        if self.worker_computes_weights:
            # Next states were just collected, we batch process them here.
            # TODO we can merge this preprocessing into the same call.
            _, loss_per_item = self.agent.update(
                dict(
                    states=states,
                    actions=actions,
                    rewards=rewards,
                    terminals=terminals,
                    next_states=next_states,
                    importance_weights=weights
                )
            )
            weights = np.abs(loss_per_item) + SMALL_NUMBER

        return dict(
            states=[ray_compress(state) for state in states],
            actions=actions,
            rewards=rewards,
            terminals=terminals,
            importance_weights=weights
        ), len(rewards)