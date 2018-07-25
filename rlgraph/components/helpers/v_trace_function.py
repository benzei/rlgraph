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

from rlgraph import get_backend
from rlgraph.components import Component
from rlgraph.spaces.space_utils import sanity_check_space

if get_backend() == "tf":
    import tensorflow as tf


class VTraceFunction(Component):
    """
    A Helper Component that contains a graph_fn to calculate V-trace values from importance ratios (rhos).
    Based on [1] and coded analogously to: https://github.com/deepmind/scalable_agent

    [1] IMPALA: Scalable Distributed Deep-RL with Importance Weighted Actor-Learner Architectures - Espeholt, Soyer,
        Munos et al. - 2018 (https://arxiv.org/abs/1802.01561)
    """

    def __init__(self, rho_bar=1.0, rho_bar_pg=1.0, c_bar=1.0, clip_pg_rho_threshold=1.0, **kwargs):
        """
        Args:
            rho_bar (float): The maximum values of the IS-weights for the temporal differences of V.
                Use None for not applying any clipping.
            rho_bar_pg (float): The maximum values of the IS-weights for the policy-gradient loss:
                \rho_s \delta log \pi(a|x) (r + \gamma v_{s+1} - V(x_s))
                Use None for not applying any clipping.
            c_bar (float): The maximum values of the IS-weights for the time trace.
                Use None for not applying any clipping.
        """
        super(VTraceFunction, self).__init__(scope=kwargs.pop("scope", "v-trace-function"), **kwargs)

        self.rho_bar = rho_bar
        self.rho_bar_pg = rho_bar_pg
        self.c_bar = c_bar

        # Define our helper API-method, which must not be completed (we don't have variables) and thus its
        # graph_fn can be called anytime from within another graph_fn.
        self.define_api_method("calc_v_trace_values", self._graph_fn_calc_v_trace_values)

    def check_input_spaces(self, input_spaces, action_space):
        in_spaces = input_spaces["calc_v_trace_values"]
        log_is_weight_space, discounts_space, rewards_space, values_space, bootstrap_value_space = in_spaces

        sanity_check_space(log_is_weight_space, must_have_batch_rank=True)
        log_is_weight_rank = log_is_weight_space.rank

        # Sanity check our input Spaces for consistency (amongst each other).
        sanity_check_space(values_space, rank=log_is_weight_rank, must_have_batch_rank=True, must_have_time_rank=True)
        sanity_check_space(bootstrap_value_space, must_have_batch_rank=True, must_have_time_rank=False)
        sanity_check_space(discounts_space, rank=log_is_weight_rank,
                           must_have_batch_rank=True, must_have_time_rank=True)
        sanity_check_space(rewards_space, rank=log_is_weight_rank, must_have_batch_rank=True, must_have_time_rank=True)

    def _graph_fn_calc_v_trace_values(self, log_is_weights, discounts, rewards, values, bootstrapped_v):
        """
        Returns the V-trace values calculated from log importance weights (see [1] for details).
        T=time rank
        B=batch rank
        A=action Space

        Args:
            log_is_weights (DataOp): DataOp (time x batch x values) holding the log values of the IS
                (importance sampling) weights: log(target_policy(a) / behaviour_policy(a)).
                Log space is used for numerical stability (for the timesteps s=t to s=t+N-1).
            discounts (DataOp): DataOp (time x batch x values) holding the discounts collected when stepping
                through the environment (for the timesteps s=t to s=t+N-1).
            rewards (DataOp): DataOp (time x batch x values) holding the rewards collected when stepping
                through the environment (for the timesteps s=t to s=t+N-1).
            values (DataOp): DataOp (time x batch x values) holding the the value function estimates
                wrt. the learner's policy (pi) (for the timesteps s=t to s=t+N-1).
            bootstrapped_v: DataOp (batch x values) holding the last (bootstrapped) value estimate to use as a value
                function estimate after n time steps (V(xs) for s=t+N).

        Returns:
            DataOpTuple:
                v-trace values (vs) in time x batch dimensions used to train the value-function (baseline).
                PG-advantage values in time x batch dimensions used for training via policy gradient with baseline.
        """
        if get_backend() == "tf":
            is_weights = tf.exp(x=log_is_weights)

            # Apply rho-bar (also for PG) and c-bar clipping to all IS-weights.
            if self.rho_bar is not None:
                rho_t = tf.minimum(x=self.rho_bar, y=is_weights)
            else:
                rho_t = is_weights

            if self.rho_bar_pg is not None:
                rho_t_pg = tf.minimum(x=self.rho_bar_pg, y=is_weights)
            else:
                rho_t_pg = is_weights

            if self.c_bar is not None:
                c_i = tf.minimum(x=self.c_bar, y=is_weights)
            else:
                c_i = is_weights

            # This is the same vector as `values` except that it will be shifted by 1 timestep to the right and
            # include - as the last item - the bootstrapped V value at s=t+N.
            values_t_plus_1 = tf.concat(values=[values[1:], tf.expand_dims(input=bootstrapped_v, axis=0)], axis=0)
            # Calculate the temporal difference terms (delta-t-V in the paper) for each s=t to s=t+N-1.
            dt_vs = rho_t * (rewards + discounts * values_t_plus_1 - values)

            # V-trace values can be calculated recursively (starting from the end of a trajectory) via:
            #    vs = V(xs) + dsV + gamma * cs * (vs+1 - V(s+1))
            # => (vs - V(xs)) = dsV + gamma * cs * (vs+1 - V(s+1))
            # We will thus calculate all terms: [vs - V(xs)] for all timesteps first, then add V(xs) again to get the
            # v-traces.
            elements = (
                tf.reverse(tensor=discounts, axis=[0]), tf.reverse(tensor=c_i, axis=[0]),
                tf.reverse(tensor=dt_vs, axis=[0])
            )

            def scan_func(vs_minus_v_xs_, elements_):
                gamma_t, c_t, dt_v = elements_
                return dt_v + gamma_t * c_t * vs_minus_v_xs_

            vs_minus_v_xs = tf.scan(
                fn=scan_func,
                elems=elements,
                initializer=tf.zeros_like(tensor=bootstrapped_v),
                parallel_iterations=1,
                back_prop=False
            )
            # Reverse the results back to original order.
            vs_minus_v_xs = tf.reverse(tensor=vs_minus_v_xs, axis=[0])

            # Add V(xs) to get vs.
            vs = tf.add(x=vs_minus_v_xs, y=values)

            # Calculate the advantage values (for policy gradient loss term) according to:
            # A = Q - V with Q based on vs (v-trace) values: qs = rs + gamma * vs and V being the
            # approximate value function output.
            vs_t_plus_1 = tf.concat(values=[vs[1:], tf.expand_dims(input=bootstrapped_v, axis=0)], axis=0)
            pg_advantages = rho_t_pg * (rewards + discounts * vs_t_plus_1 - values)

            # Make sure no gradients back-propagated through the returned values.
            return tf.stop_gradient(input=vs), tf.stop_gradient(input=pg_advantages)
