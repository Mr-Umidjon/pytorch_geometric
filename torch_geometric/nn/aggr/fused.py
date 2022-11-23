from typing import Any, List, Optional, Tuple, Union

import torch
from torch import Tensor

from torch_geometric.nn import (
    Aggregation,
    MaxAggregation,
    MeanAggregation,
    MinAggregation,
    MulAggregation,
    StdAggregation,
    SumAggregation,
    VarAggregation,
)
from torch_geometric.nn.resolver import aggregation_resolver


class FusedAggregation(Aggregation):
    r"""Helper class to fuse computation of multiple aggregations together.
    Used internally in :class:`~torch_geometric.nn.aggr.MultiAggregation` to
    speed-up computation.

    Args:
        aggrs (list): The list of aggregation schemes to use.
    """
    # We can fuse all aggregations together that rely on `scatter` directives.
    FUSABLE_AGGRS = {
        SumAggregation,
        MeanAggregation,
        MinAggregation,
        MaxAggregation,
        MulAggregation,
        VarAggregation,
        StdAggregation,
    }

    # All aggregations that rely on computing the degree of indices.
    DEGREE_BASED_AGGRS = {
        MeanAggregation,
        VarAggregation,
        StdAggregation,
    }

    # All aggregations that require manual masking for invalid rows:
    MASK_REQUIRED_AGGRS = {
        MinAggregation,
        MaxAggregation,
        MulAggregation,
    }

    # Map aggregations to `reduce` options in `scatter` directives.
    REDUCE = {
        SumAggregation: 'sum',
        MeanAggregation: 'sum',
        MinAggregation: 'amin',
        MaxAggregation: 'amax',
        MulAggregation: 'prod',
        VarAggregation: 'pow_sum',
        StdAggregation: 'pow_sum',
    }

    def __init__(self, aggrs: List[Union[Aggregation, str]]):
        super().__init__()

        if not isinstance(aggrs, (list, tuple)):
            raise ValueError(f"'aggrs' of '{self.__class__.__name__}' should "
                             f"be a list or tuple (got '{type(aggrs)}').")

        if len(aggrs) == 0:
            raise ValueError(f"'aggrs' of '{self.__class__.__name__}' should "
                             f"not be empty.")

        aggrs = [aggregation_resolver(aggr) for aggr in aggrs]
        self.aggr_cls = [aggr.__class__ for aggr in aggrs]
        self.aggr_index = {cls: i for i, cls in enumerate(self.aggr_cls)}

        for cls in self.aggr_cls:
            if cls not in self.FUSABLE_AGGRS:
                raise ValueError(f"Received aggregation '{cls.__name__}' in "
                                 f"'{self.__class__.__name__}' which is not "
                                 f"fusable")

        # Check whether we need to compute degree information:
        self.need_degree = False
        for cls in self.aggr_cls:
            if cls in self.DEGREE_BASED_AGGRS:
                self.need_degree = True

        # Check whether we need to compute mask information:
        self.requires_mask = False
        for cls in self.aggr_cls:
            if cls in self.MASK_REQUIRED_AGGRS:
                self.requires_mask = True

        # Determine which reduction to use for each aggregator:
        # An entry of `None` means that this operator re-uses intermediate
        # outputs from other aggregators.
        self.reduce_ops: List[Optional[str]] = []
        # Determine which `(Aggregator, index)` to use as intermediate output:
        self.lookup_ops: List[Optional[Tuple[Any, int]]] = []

        for cls in self.aggr_cls:
            if cls == MeanAggregation:
                # Directly use output of `SumAggregation`:
                if SumAggregation in self.aggr_index:
                    self.reduce_ops.append(None)
                    self.lookup_ops.append(
                        (SumAggregation, self.aggr_index[SumAggregation]))
                else:
                    self.reduce_ops.append(self.REDUCE[cls])
                    self.lookup_ops.append(None)

            elif cls == VarAggregation:
                if MeanAggregation in self.aggr_index:
                    self.reduce_ops.append(self.REDUCE[cls])
                    self.lookup_ops.append(
                        (MeanAggregation, self.aggr_index[MeanAggregation]))
                elif SumAggregation in self.aggr_index:
                    self.reduce_ops.append(self.REDUCE[cls])
                    self.lookup_ops.append(
                        (SumAggregation, self.aggr_index[SumAggregation]))
                else:
                    self.reduce_ops.append(self.REDUCE[cls])
                    self.lookup_ops.append(None)

            elif cls == StdAggregation:
                # Directly use output of `VarAggregation`:
                if VarAggregation in self.aggr_index:
                    self.reduce_ops.append(None)
                    self.lookup_ops.append(
                        (VarAggregation, self.aggr_index[VarAggregation]))
                elif MeanAggregation in self.aggr_index:
                    self.reduce_ops.append(self.REDUCE[cls])
                    self.lookup_ops.append(
                        (MeanAggregation, self.aggr_index[MeanAggregation]))
                elif SumAggregation in self.aggr_index:
                    self.reduce_ops.append(self.REDUCE[cls])
                    self.lookup_ops.append(
                        (SumAggregation, self.aggr_index[SumAggregation]))
                else:
                    self.reduce_ops.append(self.REDUCE[cls])
                    self.lookup_ops.append(None)

            else:
                self.reduce_ops.append(self.REDUCE[cls])
                self.lookup_ops.append(None)

    def forward(self, x: Tensor, index: Optional[Tensor] = None,
                ptr: Optional[Tensor] = None, dim_size: Optional[int] = None,
                dim: int = -2) -> Tensor:

        # Assert two-dimensional input for now to simplify computation:
        # TODO refactor this to support any dimension.
        self.assert_index_present(index)
        self.assert_two_dimensional_input(x, dim)

        if self.need_degree:
            count = x.new_zeros(dim_size)
            count.scatter_add_(0, index, x.new_ones(x.size(0)))
            if self.requires_mask:
                mask = count == 0
            count = count.clamp_(min=1).view(-1, 1)

        elif self.requires_mask:  # Mask to set non-existing indicses to zero:
            mask = x.new_ones(dim_size, dtype=torch.bool)
            mask[index] = False

        num_feats = x.size(-1)
        index = index.view(-1, 1).expand(-1, num_feats)

        #######################################################################

        outs: List[Optional[Tensor]] = []

        # Iterate over all reduction ops to compute first results:
        for i, reduce in enumerate(self.reduce_ops):
            if reduce is None:
                outs.append(None)
                continue

            src = x * x if reduce == 'pow_sum' else x
            reduce = 'sum' if reduce == 'pow_sum' else reduce

            fill_value = 0.0
            if reduce == 'amin':
                fill_value = float('inf')
            elif reduce == 'amax':
                fill_value = float('-inf')
            elif reduce == 'prod':
                fill_value = 1.0

            # `include_self=True` + manual masking leads to faster runtime:
            out = x.new_full((dim_size, num_feats), fill_value)
            out.scatter_reduce_(0, index, src, reduce, include_self=True)
            if fill_value != 0.0:
                out = out.masked_fill(mask.view(-1, 1), 0.0)
            outs.append(out)

        #######################################################################

        # Compute `MeanAggregation` first to be able to re-use it:
        i = self.aggr_index.get(MeanAggregation)
        if i is not None:
            if self.lookup_ops[i] is None:
                sum_ = outs[i]
            else:
                tmp_aggr, j = self.lookup_ops[i]
                assert tmp_aggr == SumAggregation
                sum_ = outs[j]

            outs[i] = sum_ / count

        # Compute `VarAggregation` second to be able to re-use it:
        i = self.aggr_index.get(VarAggregation)
        if i is not None:
            if self.lookup_ops[i] is None:
                mean = x.new_zeros(dim_size, num_feats)
                mean.scatter_reduce_(0, index, x, 'sum', include_self=True)
                mean = mean / count
            else:
                tmp_aggr, j = self.lookup_ops[i]
                if tmp_aggr == SumAggregation:
                    mean = outs[j] / count
                elif tmp_aggr == MeanAggregation:
                    mean = outs[j]
                else:
                    raise NotImplementedError

            pow_sum = outs[i]
            outs[i] = (pow_sum / count) - (mean * mean)

        # Compute `StdAggregation` last:
        i = self.aggr_index.get(StdAggregation)
        if i is not None:
            var = None
            if self.lookup_ops[i] is None:
                pow_sum = outs[i]
                mean = x.new_zeros(dim_size, num_feats)
                mean.scatter_reduce_(0, index, x, 'sum', include_self=True)
                mean = mean / count
            else:
                tmp_aggr, j = self.lookup_ops[i]
                if tmp_aggr == VarAggregation:
                    var = outs[j]
                elif tmp_aggr == SumAggregation:
                    pow_sum = outs[i]
                    mean = outs[j] / count
                elif tmp_aggr == MeanAggregation:
                    pow_sum = outs[i]
                    mean = outs[j]
                else:
                    raise NotImplementedError

            if var is None:
                var = (pow_sum / count) - (mean * mean)

            outs[i] = (var.relu() + 1e-5).sqrt()

        #######################################################################

        out = torch.cat(outs, dim=-1)

        return out
