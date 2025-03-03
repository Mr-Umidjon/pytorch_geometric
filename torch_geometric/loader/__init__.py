from torch_geometric.deprecation import deprecated

from .dataloader import DataLoader
from .neighbor_loader import NeighborLoader
from .link_neighbor_loader import LinkNeighborLoader
from .hgt_loader import HGTLoader
from .cluster import ClusterData, ClusterLoader
from .graph_saint import (GraphSAINTSampler, GraphSAINTNodeSampler,
                          GraphSAINTEdgeSampler, GraphSAINTRandomWalkSampler)
from .shadow import ShaDowKHopSampler
from .random_node_loader import RandomNodeLoader
from .data_list_loader import DataListLoader
from .dense_data_loader import DenseDataLoader
from .temporal_dataloader import TemporalDataLoader
from .neighbor_sampler import NeighborSampler
from .imbalanced_sampler import ImbalancedSampler
from .dynamic_batch_sampler import DynamicBatchSampler
from .node_loader import NodeLoader
from .link_loader import LinkLoader

__all__ = classes = [
    'DataLoader',
    'NodeLoader',
    'LinkLoader',
    'NeighborLoader',
    'LinkNeighborLoader',
    'HGTLoader',
    'ClusterData',
    'ClusterLoader',
    'GraphSAINTSampler',
    'GraphSAINTNodeSampler',
    'GraphSAINTEdgeSampler',
    'GraphSAINTRandomWalkSampler',
    'ShaDowKHopSampler',
    'RandomNodeLoader',
    'DataListLoader',
    'DenseDataLoader',
    'TemporalDataLoader',
    'NeighborSampler',
    'ImbalancedSampler',
    'DynamicBatchSampler',
]

RandomNodeSampler = deprecated(
    details="use 'loader.RandomNodeLoader' instead",
    func_name='loader.RandomNodeSampler',
)(RandomNodeLoader)
