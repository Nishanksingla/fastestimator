# Copyright 2019 The FastEstimator Authors. All Rights Reserved.
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
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Iterable, Tuple, TypeVar, Union

import numpy as np
import tensorflow as tf
import torch

from fastestimator.backend._concat import concat
from fastestimator.backend._reduce_max import reduce_max
from fastestimator.backend._reduce_min import reduce_min
from fastestimator.backend._squeeze import squeeze
from fastestimator.trace.trace import Trace
from fastestimator.util.data import Data
from fastestimator.util.img_data import ImgData
from fastestimator.util.traceability_util import traceable
from fastestimator.util.util import to_number
from fastestimator.util.base_util import to_list
from fastestimator.xai.saliency import SaliencyNet

Model = TypeVar('Model', tf.keras.Model, torch.nn.Module)


@traceable()
class Saliency(Trace):
    """A Trace which computes saliency maps for a given model throughout training.

    Args:
        model: A model compiled with fe.build to be analyzed.
        model_inputs: Keys for the input values for the model.
        model_outputs: Keys for the output values from a model.
        class_key: The key of the true labels corresponding to the model inputs (not required).
        label_mapping: {class_string: model_output_value}.
        outputs: The name of the output which will be generated by this trace.
        samples: How many datapoints to collect in order to perform visualization, or {model_input_key: model_input}.
        mode: What mode(s) to execute this Trace in. For example, "train", "eval", "test", or "infer". To execute
            regardless of mode, pass None. To execute in all modes except for a particular one, you can pass an argument
            like "!infer" or "!train".
        ds_id: What dataset id(s) to execute this Trace in. To execute regardless of ds_id, pass None. To execute in all
            ds_ids except for a particular one, you can pass an argument like "!ds1".
        smoothing: How many rounds of smoothing should be applied to the saliency mask (0 to disable).
        integrating: How many rounds of integration should be applied to the saliency mask (0 to disable). A tuple may
            be used to indicate (# integration, # smoothing) if a different amount of smoothing is desired than was
            provided by the smoothing variable (useful if you want to compare techniques / save on computation time).
    """
    samples: Dict[str, Union[None, int, Dict[str, Any]]]  # {mode: val}
    n_found: Dict[str, int]  # {mode: val}
    n_required: Dict[str, int]  # {mode: val}

    def __init__(self,
                 model: Model,
                 model_inputs: Union[str, Sequence[str]],
                 model_outputs: Union[str, Sequence[str]],
                 class_key: Optional[str] = None,
                 label_mapping: Optional[Dict[str, Any]] = None,
                 outputs: Union[str, List[str]] = "saliency",
                 samples: Union[None, int, Dict[str, Any]] = None,
                 mode: Union[None, str, Iterable[str]] = ("eval", "test"),
                 ds_id: Union[None, str, Iterable[str]] = None,
                 smoothing: int = 25,
                 integrating: Union[int, Tuple[int, int]] = (100, 6)) -> None:
        # Model outputs are required due to inability to statically determine the number of outputs from a pytorch model
        self.class_key = class_key
        self.model_outputs = to_list(model_outputs)
        super().__init__(inputs=to_list(self.class_key) + to_list(model_inputs), outputs=outputs, mode=mode,
                         ds_id=ds_id)
        self.smoothing = smoothing
        self.integrating = integrating
        self.samples = {}
        self.n_found = {}
        self.n_required = {}
        # TODO - handle non-hashable labels
        self.label_mapping = {val: key for key, val in label_mapping.items()} if label_mapping else None
        for mode in mode or ("train", "eval", "test"):
            self.samples[mode] = samples
            if isinstance(samples, int):
                self.samples[mode] = None
                self.n_found[mode] = 0
                self.n_required[mode] = samples
            else:
                self.n_found[mode] = 0
                self.n_required[mode] = 0
            if self.samples[mode] is None:
                self.samples[mode] = defaultdict(list)
        self.salnet = SaliencyNet(model=model, model_inputs=model_inputs, model_outputs=model_outputs, outputs=outputs)

    def on_batch_end(self, data: Data) -> None:
        mode = self.system.mode
        if not self.samples[mode] or self.n_found[mode] < self.n_required[mode]:
            n_samples = 0
            for key in self.inputs:
                self.samples[mode][key].append(data[key])
                n_samples = len(data[key])
            self.n_found[mode] += n_samples

    def on_epoch_end(self, data: Data) -> None:
        mode = self.system.mode
        if self.n_found[mode] > 0:
            if self.n_required[mode] > 0:
                # We are keeping a user-specified number of samples
                self.samples[mode] = {
                    key: concat(val)[:self.n_required[mode]]
                    for key, val in self.samples[mode].items()
                }
            else:
                # We are keeping one batch of data
                self.samples[mode] = {key: val[0] for key, val in self.samples[mode].items()}
            # even if you haven't found n_required samples, you're at end of epoch so no point trying to collect more
            self.n_found[mode] = 0
            self.n_required[mode] = 0

        masks = self.salnet.get_masks(self.samples[mode])
        smoothed, integrated, smint = {}, {}, {}
        if self.smoothing:
            smoothed = self.salnet.get_smoothed_masks(self.samples[mode], nsamples=self.smoothing)
        if self.integrating:
            if isinstance(self.integrating, Tuple):
                n_integration, n_smoothing = self.integrating
            else:
                n_integration = self.integrating
                n_smoothing = self.smoothing
            integrated = self.salnet.get_integrated_masks(self.samples[mode], nsamples=n_integration)
            if n_smoothing:
                smint = self.salnet.get_smoothed_masks(self.samples[mode],
                                                       nsamples=n_smoothing,
                                                       nintegration=n_integration)

        # Arrange the outputs
        args = {}
        if self.class_key:
            classes = self.samples[mode][self.class_key]
            if self.label_mapping:
                classes = np.array([self.label_mapping[clazz] for clazz in to_number(squeeze(classes))])
            args[self.class_key] = classes
        for key in self.model_outputs:
            classes = masks[key]
            if self.label_mapping:
                classes = np.array([self.label_mapping[clazz] for clazz in to_number(squeeze(classes))])
            args[key] = classes
        sal = smint or integrated or smoothed or masks
        for key, val in self.samples[mode].items():
            if key is not self.class_key:
                args[key] = val
                # Create a linear combination of the original image, the saliency mask, and the product of the two in
                # order to highlight regions of importance
                min_val = reduce_min(val)
                diff = reduce_max(val) - min_val
                for outkey in self.outputs:
                    args["{} {}".format(key, outkey)] = (0.3 * (sal[outkey] * (val - min_val) + min_val) + 0.3 * val +
                                                         0.4 * sal[outkey] * diff + min_val)
        for key in self.outputs:
            args[key] = masks[key]
            if smoothed:
                args["Smoothed {}".format(key)] = smoothed[key]
            if integrated:
                args["Integrated {}".format(key)] = integrated[key]
            if smint:
                args["SmInt {}".format(key)] = smint[key]
        result = ImgData(colormap="inferno", **args)

        data.write_without_log(self.outputs[0], result)
