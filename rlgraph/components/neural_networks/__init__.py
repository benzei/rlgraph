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

from rlgraph.components.neural_networks.stack import Stack
from rlgraph.components.neural_networks.preprocessor_stack import PreprocessorStack
from rlgraph.components.neural_networks.neural_network import NeuralNetwork
from rlgraph.components.neural_networks.policy import Policy
from rlgraph.components.neural_networks.action_adapter import ActionAdapter

# The Stacks.
Stack.__lookup_classes__ = dict(
    preprocessorstack=PreprocessorStack
)

__all__ = ["Stack", "PreprocessorStack", "NeuralNetwork", "Policy", "ActionAdapter"]
