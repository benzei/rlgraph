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

import numpy as np
import unittest

from rlgraph.components.policies import Policy, SharedValueFunctionPolicy
from rlgraph.spaces import *
from rlgraph.tests import ComponentTest
from rlgraph.tests.test_util import config_from_path
from rlgraph.utils import softmax, relu


class TestPoliciesOnContainerActions(unittest.TestCase):

    def test_policy_for_discrete_container_action_space(self):
        # state_space.
        state_space = FloatBox(shape=(4,), add_batch_rank=True)

        # Container action space.
        action_space = dict(
            type="dict",
            a=IntBox(2),
            b=IntBox(3),
            add_batch_rank=True
        )

        policy = Policy(network_spec=config_from_path("configs/test_simple_nn.json"), action_space=action_space)
        test = ComponentTest(
            component=policy,
            input_spaces=dict(
                nn_input=state_space,
                actions=action_space
            ),
            action_space=action_space
        )
        policy_params = test.read_variable_values(policy.variables)

        # Some NN inputs (batch size=2).
        states = state_space.sample(2)
        # Raw NN-output.
        expected_nn_output = np.matmul(states, policy_params["policy/test-network/hidden-layer/dense/kernel"])
        test.test(("get_nn_output", states), expected_outputs=dict(output=expected_nn_output), decimals=6)

        # Raw action layers' output.
        expected_action_layer_outputs = dict(
            a=np.matmul(expected_nn_output, policy_params["policy/action-adapter-0/action-layer/dense/kernel"]),
            b=np.matmul(expected_nn_output, policy_params["policy/action-adapter-1/action-layer/dense/kernel"])
        )
        test.test(("get_action_layer_output", states), expected_outputs=dict(output=expected_action_layer_outputs),
                  decimals=5)

        # Logits, parameters (probs) and skip log-probs (numerically unstable for small probs).
        expected_probabilities_output = dict(
            a=np.array(softmax(expected_action_layer_outputs["a"], axis=-1), dtype=np.float32),
            b=np.array(softmax(expected_action_layer_outputs["b"], axis=-1), dtype=np.float32)
        )
        test.test(("get_logits_probabilities_log_probs", states, ["logits", "probabilities"]), expected_outputs=dict(
            logits=expected_action_layer_outputs, probabilities=expected_probabilities_output
        ), decimals=5)

        print("Probs: {}".format(expected_probabilities_output))

        expected_actions = dict(
            a=np.argmax(expected_action_layer_outputs["a"], axis=-1),
            b=np.argmax(expected_action_layer_outputs["b"], axis=-1)
        )
        test.test(("get_action", states), expected_outputs=dict(action=expected_actions))

        # Stochastic sample.
        out = test.test(("get_stochastic_action", states), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"]["a"].dtype == np.int32)
        self.assertTrue(out["action"]["a"].shape == (2,))
        self.assertTrue(out["action"]["b"].dtype == np.int32)
        self.assertTrue(out["action"]["b"].shape == (2,))

        # Deterministic sample.
        test.test(("get_deterministic_action", states), expected_outputs=None)  # dict(action=expected_actions))
        self.assertTrue(out["action"]["a"].dtype == np.int32)
        self.assertTrue(out["action"]["a"].shape == (2,))
        self.assertTrue(out["action"]["b"].dtype == np.int32)
        self.assertTrue(out["action"]["b"].shape == (2,))

        # Distribution's entropy.
        out = test.test(("get_entropy", states), expected_outputs=None)  # dict(entropy=expected_h), decimals=3)
        self.assertTrue(out["entropy"]["a"].dtype == np.float32)
        self.assertTrue(out["entropy"]["a"].shape == (2,))
        self.assertTrue(out["entropy"]["b"].dtype == np.float32)
        self.assertTrue(out["entropy"]["b"].shape == (2,))

        # Action log-probs.
        expected_action_log_prob_output = dict(
            a=np.log(np.array([expected_probabilities_output["a"][0][expected_actions["a"][0]],
                               expected_probabilities_output["a"][1][expected_actions["a"][1]]])),
            b=np.log(np.array([expected_probabilities_output["b"][0][expected_actions["b"][0]],
                               expected_probabilities_output["b"][1][expected_actions["b"][1]]])),
        )
        test.test(("get_action_log_probs", [states, expected_actions]),
                  expected_outputs=dict(action_log_probs=expected_action_log_prob_output), decimals=5)

    def test_shared_value_function_policy_for_discrete_container_action_space(self):
        # state_space (NN is a simple single fc-layer relu network (2 units), random biases, random weights).
        state_space = FloatBox(shape=(5,), add_batch_rank=True)

        # action_space (complex nested container action space).
        action_space = dict(
            type="dict",
            a=IntBox(2),
            b=Dict(b1=IntBox(3), b2=IntBox(4)),
            add_batch_rank=True
        )

        # Policy with baseline action adapter.
        shared_value_function_policy = SharedValueFunctionPolicy(
            network_spec=config_from_path("configs/test_lrelu_nn.json"),
            action_space=action_space
        )
        test = ComponentTest(
            component=shared_value_function_policy,
            input_spaces=dict(
                nn_input=state_space,
                actions=action_space
            ),
            action_space=action_space,
        )
        policy_params = test.read_variable_values(shared_value_function_policy.variables)

        base_scope = "shared-value-function-policy/action-adapter-"

        # Some NN inputs (batch size=2).
        states = state_space.sample(size=2)
        # Raw NN-output.
        expected_nn_output = relu(np.matmul(
            states, policy_params["shared-value-function-policy/test-network/hidden-layer/dense/kernel"]
        ), 0.1)
        test.test(("get_nn_output", states), expected_outputs=dict(output=expected_nn_output), decimals=5)

        # Raw action layers' output.
        expected_action_layer_outputs = dict(
            a=np.matmul(expected_nn_output, policy_params[base_scope + "0/action-layer/dense/kernel"]),
            b=dict(b1=np.matmul(expected_nn_output, policy_params[base_scope + "1/action-layer/dense/kernel"]),
                   b2=np.matmul(expected_nn_output, policy_params[base_scope + "2/action-layer/dense/kernel"]))
        )
        test.test(("get_action_layer_output", states), expected_outputs=dict(output=expected_action_layer_outputs),
                  decimals=5)

        # State-values.
        expected_state_value_output = np.matmul(
            expected_nn_output, policy_params["shared-value-function-policy/value-function-node/dense/kernel"]
        )
        test.test(("get_state_values", states), expected_outputs=dict(state_values=expected_state_value_output),
                  decimals=5)

        # logits-values: One for each action-choice per item in the batch (simply take the remaining out nodes).
        test.test(("get_state_values_logits_probabilities_log_probs", states, ["state_values", "logits"]),
                  expected_outputs=dict(state_values=expected_state_value_output, logits=expected_action_layer_outputs),
                  decimals=5)

        # Parameter (probabilities). Softmaxed logits.
        expected_probabilities_output = dict(
            a=softmax(expected_action_layer_outputs["a"], axis=-1),
            b=dict(
                b1=softmax(expected_action_layer_outputs["b"]["b1"], axis=-1),
                b2=softmax(expected_action_layer_outputs["b"]["b2"], axis=-1)
            )
        )
        test.test(("get_logits_probabilities_log_probs", states, ["logits", "probabilities"]), expected_outputs=dict(
            logits=expected_action_layer_outputs,
            probabilities=expected_probabilities_output
        ), decimals=5)

        print("Probs: {}".format(expected_probabilities_output))

        # Action sample.
        expected_actions = dict(
            a=np.argmax(expected_action_layer_outputs["a"], axis=-1),
            b=dict(
                b1=np.argmax(expected_action_layer_outputs["b"]["b1"], axis=-1),
                b2=np.argmax(expected_action_layer_outputs["b"]["b2"], axis=-1)
            )
        )
        test.test(("get_action", states), expected_outputs=dict(action=expected_actions))

        # Stochastic sample.
        out = test.test(("get_stochastic_action", states), expected_outputs=None)
        self.assertTrue(out["action"]["a"].dtype == np.int32)
        self.assertTrue(out["action"]["a"].shape == (2,))
        self.assertTrue(out["action"]["b"]["b1"].dtype == np.int32)
        self.assertTrue(out["action"]["b"]["b1"].shape == (2,))
        self.assertTrue(out["action"]["b"]["b2"].dtype == np.int32)
        self.assertTrue(out["action"]["b"]["b2"].shape == (2,))

        # Deterministic sample.
        out = test.test(("get_deterministic_action", states), expected_outputs=None)
        self.assertTrue(out["action"]["a"].dtype == np.int32)
        self.assertTrue(out["action"]["a"].shape == (2,))
        self.assertTrue(out["action"]["b"]["b1"].dtype == np.int32)
        self.assertTrue(out["action"]["b"]["b1"].shape == (2,))
        self.assertTrue(out["action"]["b"]["b2"].dtype == np.int32)
        self.assertTrue(out["action"]["b"]["b2"].shape == (2,))

        # Distribution's entropy.
        out = test.test(("get_entropy", states), expected_outputs=None)
        self.assertTrue(out["entropy"]["a"].dtype == np.float32)
        self.assertTrue(out["entropy"]["a"].shape == (2,))
        self.assertTrue(out["entropy"]["b"]["b1"].dtype == np.float32)
        self.assertTrue(out["entropy"]["b"]["b1"].shape == (2,))
        self.assertTrue(out["entropy"]["b"]["b2"].dtype == np.float32)
        self.assertTrue(out["entropy"]["b"]["b2"].shape == (2,))

    def test_shared_value_function_policy_for_discrete_container_action_space_with_time_rank_folding(self):
        # state_space (NN is a simple single fc-layer relu network (2 units), random biases, random weights).
        state_space = FloatBox(shape=(6,), add_batch_rank=True, add_time_rank=True)

        # action_space (4 possible actions).
        action_space = Tuple(
            IntBox(2),
            IntBox(3),
            Dict(
                a=IntBox(4),
            ),
            add_batch_rank=True,
            add_time_rank=True
        )

        # Policy with baseline action adapter AND batch-apply over the entire policy (NN + ActionAdapter + distr.).
        shared_value_function_policy = SharedValueFunctionPolicy(
            network_spec=config_from_path("configs/test_lrelu_nn.json"),
            action_space=action_space,
            batch_apply=True
        )
        test = ComponentTest(
            component=shared_value_function_policy,
            input_spaces=dict(nn_input=state_space, actions=action_space),
            action_space=action_space,
        )
        policy_params = test.read_variable_values(shared_value_function_policy.variables)
        base_scope = "shared-value-function-policy/action-adapter-"

        # Some NN inputs.
        states = state_space.sample(size=(2, 3))
        states_folded = np.reshape(states, newshape=(6, 6))
        # Raw NN-output (still folded).
        expected_nn_output = relu(np.matmul(
            states_folded, policy_params["shared-value-function-policy/test-network/hidden-layer/dense/kernel"]
        ), 0.1)
        test.test(("get_nn_output", states), expected_outputs=dict(output=expected_nn_output), decimals=5)

        # Raw action layer output; Expected shape=(3,3): 3=batch, 2=action categories + 1 state value
        expected_action_layer_output = tuple([
            np.matmul(expected_nn_output, policy_params[base_scope + "0/action-layer/dense/kernel"]),
            np.matmul(expected_nn_output, policy_params[base_scope + "1/action-layer/dense/kernel"]),
            dict(
                a=np.matmul(expected_nn_output, policy_params[base_scope + "2/action-layer/dense/kernel"])
            )
        ])
        test.test(("get_action_layer_output", states), expected_outputs=dict(output=expected_action_layer_output),
                  decimals=5)
        expected_action_layer_output_unfolded = tuple([
            np.reshape(expected_action_layer_output[0], newshape=(2, 3, 2)),
            np.reshape(expected_action_layer_output[1], newshape=(2, 3, 3)),
            dict(
                a=np.reshape(expected_action_layer_output[2]["a"], newshape=(2, 3, 4))
            )
        ])

        # State-values: One for each item in the batch.
        expected_state_value_output = np.matmul(
            expected_nn_output,
            policy_params["shared-value-function-policy/value-function-node/dense/kernel"]
        )
        expected_state_value_output_unfolded = np.reshape(expected_state_value_output, newshape=(2, 3, 1))
        test.test(("get_state_values", states),
                  expected_outputs=dict(state_values=expected_state_value_output_unfolded), decimals=5)

        test.test(
            ("get_state_values_logits_probabilities_log_probs", states, ["state_values", "logits"]),
            expected_outputs=dict(
                state_values=expected_state_value_output_unfolded, logits=expected_action_layer_output_unfolded
            ), decimals=5
        )

        # Parameter (probabilities). Softmaxed logits.
        expected_probabilities_output = tuple([
            softmax(expected_action_layer_output_unfolded[0], axis=-1),
            softmax(expected_action_layer_output_unfolded[1], axis=-1),
            dict(
                a=softmax(expected_action_layer_output_unfolded[2]["a"], axis=-1)
            )
        ])
        test.test(("get_logits_probabilities_log_probs", states, ["logits", "probabilities"]),
                  expected_outputs=dict(
                      logits=expected_action_layer_output_unfolded,
                      probabilities=expected_probabilities_output
                  ), decimals=5)

        print("Probs: {}".format(expected_probabilities_output))

        expected_actions = tuple([
            np.argmax(expected_action_layer_output_unfolded[0], axis=-1),
            np.argmax(expected_action_layer_output_unfolded[1], axis=-1),
            dict(
                a=np.argmax(expected_action_layer_output_unfolded[2]["a"], axis=-1),
            )
        ])
        test.test(("get_action", states), expected_outputs=dict(action=expected_actions))

        # Action log-probs.
        expected_action_log_prob_output = tuple([
            np.log(np.array([[
                expected_probabilities_output[0][0][0][expected_actions[0][0][0]],
                expected_probabilities_output[0][0][1][expected_actions[0][0][1]],
                expected_probabilities_output[0][0][2][expected_actions[0][0][2]],
            ], [
                expected_probabilities_output[0][1][0][expected_actions[0][1][0]],
                expected_probabilities_output[0][1][1][expected_actions[0][1][1]],
                expected_probabilities_output[0][1][2][expected_actions[0][1][2]],
            ]])),
            np.log(np.array([[
                expected_probabilities_output[1][0][0][expected_actions[1][0][0]],
                expected_probabilities_output[1][0][1][expected_actions[1][0][1]],
                expected_probabilities_output[1][0][2][expected_actions[1][0][2]],
            ], [
                expected_probabilities_output[1][1][0][expected_actions[1][1][0]],
                expected_probabilities_output[1][1][1][expected_actions[1][1][1]],
                expected_probabilities_output[1][1][2][expected_actions[1][1][2]],
            ]])),
            dict(a=np.log(np.array([[
                expected_probabilities_output[2]["a"][0][0][expected_actions[2]["a"][0][0]],
                expected_probabilities_output[2]["a"][0][1][expected_actions[2]["a"][0][1]],
                expected_probabilities_output[2]["a"][0][2][expected_actions[2]["a"][0][2]],
            ], [
                expected_probabilities_output[2]["a"][1][0][expected_actions[2]["a"][1][0]],
                expected_probabilities_output[2]["a"][1][1][expected_actions[2]["a"][1][1]],
                expected_probabilities_output[2]["a"][1][2][expected_actions[2]["a"][1][2]],
            ]])))
        ])
        test.test(("get_action_log_probs", [states, expected_actions]),
                  expected_outputs=dict(action_log_probs=expected_action_log_prob_output), decimals=5)

        # Deterministic sample.
        out = test.test(("get_deterministic_action", states), expected_outputs=None)
        self.assertTrue(out["action"][0].dtype == np.int32)
        self.assertTrue(out["action"][0].shape == (2, 3))  # Make sure output is unfolded.
        self.assertTrue(out["action"][1].dtype == np.int32)
        self.assertTrue(out["action"][1].shape == (2, 3))  # Make sure output is unfolded.
        self.assertTrue(out["action"][2]["a"].dtype == np.int32)
        self.assertTrue(out["action"][2]["a"].shape == (2, 3))  # Make sure output is unfolded.

        # Stochastic sample.
        out = test.test(("get_stochastic_action", states), expected_outputs=None)
        self.assertTrue(out["action"][0].dtype == np.int32)
        self.assertTrue(out["action"][0].shape == (2, 3))  # Make sure output is unfolded.
        self.assertTrue(out["action"][1].dtype == np.int32)
        self.assertTrue(out["action"][1].shape == (2, 3))  # Make sure output is unfolded.
        self.assertTrue(out["action"][2]["a"].dtype == np.int32)
        self.assertTrue(out["action"][2]["a"].shape == (2, 3))  # Make sure output is unfolded.

        # Distribution's entropy.
        out = test.test(("get_entropy", states), expected_outputs=None)
        self.assertTrue(out["entropy"][0].dtype == np.float32)
        self.assertTrue(out["entropy"][0].shape == (2, 3))  # Make sure output is unfolded.
        self.assertTrue(out["entropy"][1].dtype == np.float32)
        self.assertTrue(out["entropy"][1].shape == (2, 3))  # Make sure output is unfolded.
        self.assertTrue(out["entropy"][2]["a"].dtype == np.float32)
        self.assertTrue(out["entropy"][2]["a"].shape == (2, 3))  # Make sure output is unfolded.
