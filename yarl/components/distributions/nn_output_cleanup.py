# Copyright 2018 The YARL-Project, All Rights Reserved.
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
from math import log

from yarl import backend, YARLError, SMALL_NUMBER
from yarl.utils.util import get_shape
from yarl.components import Component
from yarl.components.layers import DenseLayer
from yarl.spaces import Space, IntBox


class NNOutputCleanup(Component):
    """
    A Component that cleans up neural network output and gets it ready for parameterizing a distribution Component.
    Cleanup includes reshaping (for the desired action space), adding a distribution bias, making sure probs are not
    0.0 or 1.0, etc..

    API:
    ins:
        nn_output (SingleDataOp): The raw neural net output to be cleaned up for further processing in a Distribution.
    outs:
        parameters (SingleDataOp): The cleaned up output (translated into Distribution-readable parameters).
    """
    def __init__(self, target_space, bias=None, scope="nn-output-cleanup", **kwargs):
        """
        Args:
            target_space (Space): The target Space that the NN output tries to reach (by sending out parameters
                for a distribution that matches this target Space).
            bias (any): An optional bias that will be added to the output of the network.
            TODO: For now, only IntBoxes -> Categorical are supported. We'll add support for continuous action spaces later
        """
        super(NNOutputCleanup, self).__init__(scope=scope, **kwargs)

        self.target_space = target_space

        if not isinstance(self.target_space, IntBox):
            raise YARLError("ERROR: `target_space` must be IntBox. Continuous target spaces will be supported later!")

        # Discrete action space. Make sure, all dimensions have the same bounds and the lower bound is 0.
        if self.target_space.global_bounds is False:
            raise YARLError("ERROR: `target_space` must not have individual lower and upper bounds!")
        elif self.target_space.global_bounds[0] != 0:
            raise YARLError("ERROR: `target_space` must have a lower bound of 0!")

        self.num_categories_per_dim = self.target_space.global_bounds[1] - self.target_space.global_bounds[0]

        # Define our interface.
        self.define_inputs("nn_output")
        self.define_outputs("parameters")

        # If we have a bias layer, connect it before the actual cleanup.
        if bias is not None:
            bias_layer = DenseLayer(units=self.num_categories_per_dim, biases_spec=bias if np.isscalar(bias) else
                                    [log(b) for _ in range(self.target_space.flat_dim) for b in bias])
            self.add_component(bias_layer, connect=dict(input="nn_output"))
            # Place our cleanup after the bias layer.
            self.add_computation((bias_layer, "output"), "parameters", self._computation_cleanup)
        # Place our cleanup directly after the nn-output.
        else:
            self.add_computation("nn_outputs", "parameters", self._computation_cleanup)

    def create_variables(self, input_spaces):
        input_space = input_spaces["nn_output"]  # type: Space
        assert input_space.has_batch_rank, "ERROR: Incoming Space `nn_output` must have a batch rank!"

    def _computation_cleanup(self, nn_outputs_plus_bias):
        """
        Cleans up the output coming from a NN and gets it ready for some Distribution Component (creates distribution
        parameters from the NN-output).

        Args:
            nn_outputs (SingleDataOp): The flattened data coming from an NN, but already biased?.

        Returns:
            SingleDataOp: The parameters, ready to be passed to a Distribution object's in-Socket "parameters".
        """
        # Apply optional bias.
        #if self.bias_layer is not None:
        #    nn_outputs = self.bias_layer.call("apply", inputs=nn_outputs)

        # Reshape logits to action shape
        shape = self.target_space.shape_with_batch_rank + (self.num_categories_per_dim,)
        if backend() == "tf":
            import tensorflow as tf
            logits = tf.reshape(tensor=nn_outputs_plus_bias, shape=shape)

            # Convert logits into probabilities and clamp them at SMALL_NUMBER.
            return tf.maximum(x=tf.nn.softmax(logits=logits, axis=-1), y=SMALL_NUMBER)
