# Copyright 2023 The FastEstimator Authors. All Rights Reserved.
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

class CombinedDataset:
    def __init__(self, datasets: list) -> None:
        # Potential checks
            # 1 check if datasets is empty or not
            # 2 check if dataset is a type of pytorch dataset
        if len(datasets) < 2:
            raise AssertionError("Please provide atleast 2 datasets.")
        self.datasets = datasets
        # match the keys of 0th index item of all the datasets
        keys = datasets[0][0].keys()
        for ds in datasets[1:]:
            if ds[0].keys() != keys:
                raise KeyError("All datasets should have same keys.")


    def __len__(self):
        return sum([len(ds) for ds in self.datasets])

    def __getitem__(self, idx):
        start = 0 
        end = 0
        for ds in self.datasets:
            end += len(ds)
            if idx >= start and idx< end:
                return ds[idx-start]
            start += end
