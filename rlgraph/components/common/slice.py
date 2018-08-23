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

from rlgraph import get_backend
from rlgraph.components.component import Component
from rlgraph.spaces.space_utils import sanity_check_space

if get_backend() == "tf":
    import tensorflow as tf


class Slice(Component):
    """
    A simple slicer layer. Slices off a piece from the input along the 0th rank returns it.
    """
    def __init__(self, squeeze=False, scope="slice", **kwargs):
        """
        Args:
            squeeze (bool): Whether to squeeze a possibly size=1 slice so that its rank disappears.
                Default: False.
        """
        super(Slice, self).__init__(scope=scope, **kwargs)

        self.squeeze = squeeze

        self.define_api_method("slice", self._graph_fn_slice, flatten_ops=True, split_ops=True)

    #def check_input_spaces(self, input_spaces, action_space=None):
    #    in_space = input_spaces["preprocessing_inputs"]

    #    # OBSOLETE: as we can slice the batch rank as well, which is not part of `rank`.
    #    ##Must have at least rank 1.
    #    #sanity_check_space(in_space, rank=(1, None))

    def _graph_fn_slice(self, preprocessing_inputs, start_index=0, end_index=None):
        if end_index is None:
            # Return a single slice removing the rank.
            if self.squeeze is True:
                slice_ = preprocessing_inputs[start_index]
            # Return a single slice but embedded in the rank now with dim=1.
            else:
                slice_ = preprocessing_inputs[start_index:(start_index+1)]
        else:
            slice_ = preprocessing_inputs[start_index:end_index]

            if self.squeeze is True:
                if self.backend == "python" or get_backend() == "python":
                    if end_index is None or end_index - start_index == 1:
                        slice_ = np.squeeze(slice_, axis=0)
                elif get_backend() == "tf":
                    if end_index is None:
                        slice_ = tf.squeeze(slice_, axis=0)
                    else:
                        slice_ = tf.cond(
                            pred=tf.equal(end_index - start_index, 1),
                            true_fn=lambda: tf.squeeze(slice_, axis=0),
                            false_fn=lambda: slice_
                    )
        return slice_
