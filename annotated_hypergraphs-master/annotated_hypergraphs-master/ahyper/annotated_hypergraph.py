from .utils import *

from collections import Counter, defaultdict
from itertools import permutations
from copy import deepcopy
from random import shuffle

import numpy as np
import pandas as pd
import networkx as nx


class AnnotatedHypergraph(object):
    def __init__(self, IL, roles, **kwargs):
        """
        Annotated Hypergraphs.

        For construction use the class methods:
            .from_records()
            .from_incidence()
        """

        self.IL = IL
        self.roles = roles

        for key, val in kwargs.items():
            setattr(self, key, val)

        self.set_states()
        self.sort(by="eid")

    @classmethod
    def from_records(cls, records, roles):
        """
        Construct an annotated hypergraph from records.

        Input records should be of the form
            {'role_1':[node1, node 2],
            'role_2':[node 3],
            }

        Input records can also include extra keys that will be stored
        as metadata for each edge.

        Input:
            records (list): A list of records (JSON-like)
            roles (list): A list of role labels

        Output:
            AnnotatedHypergraph
        """

        # Assign edge ids if not already present
        if records[0].get("eid") is None:
            for i in range(len(records)):
                records[i]["eid"] = i

        IL = incidence_list_from_records(records, roles)

        return cls(IL, roles)

    @classmethod
    def from_incidence(
        cls, dataset, root="./data/", relabel_roles=False, add_metadata=False
    ):
        """
        Construct an annotated hypergraph from saved data.

        Saved data should be of the form
            root/dataset/incidence.csv
            root/dataset/edges.csv
            root/dataset/roles.csv

        See examples in Github.

        Input:
            dataset (str): The folder name where the data is stored.
            root (str): The file path to the dataset folder.
            relabel_roles (bool): If True, roles are relabelled from integers to labels (default False).
            add_metadata (bool): If True, edge metadata is added to the incidence list (default False).

        Output:
            AnnotatedHypergraph
        """

        incidence = pd.read_csv(root + dataset + "/incidence.csv")
        edges = pd.read_csv(root + dataset + "/edges.csv", index_col=0)
        incidence.columns = ["nid", "eid", "role"]
        role_map = pd.read_csv(
            root + dataset + "/roles.csv", index_col=0, header=None, squeeze=True
        )

        if relabel_roles:
            incidence["role"] = incidence.role.apply(lambda x: role_map[x])
            roles = list(role_map.values)
        else:
            roles = list(range(len(role_map)))

        if add_metadata:
            metamapper = {
                ix: d for ix, d in zip(edges.index, edges.to_dict(orient="records"))
            }
            incidence["meta"] = incidence.eid.apply(lambda x: metamapper[x])
        else:
            incidence["meta"] = None

        return cls(
            [NodeEdgeIncidence(**row) for ix, row in incidence.iterrows()],
            roles,
            role_map=role_map,
        )

    def set_states(self):
        """Sets the internal states from incidence list."""
        self.node_list = np.unique([e.nid for e in self.IL])
        self.edge_list = np.unique([e.eid for e in self.IL])
        self.n = len(self.node_list)
        self.m = len(self.edge_list)
        self.R = None
        self.sort_key = None

    def get_node_list(self):
        """Returns the node list."""
        return self.node_list

    def get_edge_list(self):
        """Returns the edge list."""
        return self.edge_list

    def _MCMC_no_role(self, avoid_degeneracy=True, n_steps=1, verbose=False):
        """ Helper function for systematic methods. """
        return self.MCMC(
            n_steps=n_steps,
            avoid_degeneracy=avoid_degeneracy,
            verbose=verbose,
            role_labels=False,
        )

    def get_IL(self):
        """Returns the sorted incidence list."""
        return sorted(self.IL, key=lambda x: x.eid, reverse=True)

    def get_records(self):
        """Returns records representing the annotated hypergraph. """
        return records_from_incidence_list(self.IL, role_fields=self.roles)

    def sort(self, by="eid", reverse=False):
        """
        Sorts the incidence list.

        Keeps a track of what key the incidence list is sorted by
        and does not attempt to sort if already sorted.

        Input:
            by (str,list): Choice from ['nid','eid','role'], or a subset of. 
            reverse (bool): If True, reverse the sorting.
        """
        if isinstance(by, str):
            by = [by]

        sort_key = "_".join(by)

        if self.sort_key == sort_key:
            return None
        else:
            self.IL.sort(key=lambda x: [getattr(x, b) for b in by], reverse=reverse)

    def MCMC(self, n_steps=1, avoid_degeneracy=True, **kwargs):
        """
        Performs iterations of the MCMC algorithm to perform node swaps in the
        annotated hypergraph.

        Input:
            n_steps (int): The number of Monte Carlo steps (swaps) to take.
            avoid_degeneracy (bool): If True, prevents an edge becoming degenerate,
                                     i.e., containing the same node in multiple roles.
            kwargs: Includes 'verbose' which prints out progress
                    and 'role_labels' which, if True, preserves
                    node role labels when switching.
        """

        if avoid_degeneracy:
            alg = self._degeneracy_avoiding_MCMC
        else:
            alg = self._stub_labeled_MCMC

        alg(n_steps, **kwargs)

    def _stub_labeled_MCMC(self, n_steps=1):
        """
        Can create degeneracies, probably deprecated
        """

        self.sort(by="role")
        by_role = [list(v) for role, v in groupby(self.IL, lambda x: x.role)]

        # distribute steps over the role partition, using coupon-collector heuristic
        N = np.array([len(l) for l in by_role])
        steps = ((N * np.log(N)) / (N * np.log(N).sum()) * n_steps).astype(int)

        for i in range(len(self.roles)):
            for k in range(steps[i]):
                swap_step(by_role[i])

        self.IL = [e for role in by_role for e in role]

    def _degeneracy_avoiding_MCMC(self, n_steps=1, verbose=False, role_labels=True):
        """
        Avoids creating edges in which the same node appears multiple times. 
        Some properties need checking, but should be equivalent to stub-matching conditioned on nondegeneracy. 
        """

        # TODO: easier to work on a transformed data structure in which incidences are grouped into hyper edges
        # TODO: sorting will hit runtime quite a bit - easy first step in optimisation.

        self.sort(by=["eid", "role"])
        grouped = groupby(self.IL, lambda e: (e.eid))
        edges = {k: list(v) for k, v in grouped}

        k_rejected = 0
        N = 0

        while N < n_steps:

            # select two random hyperedges
            i, j = np.random.randint(0, self.m, 2)
            E0, E1 = edges[i], edges[j]

            # select a random node-edge incidence from each
            k = np.random.randint(len(E0))
            l = np.random.randint(len(E1))

            # if the two node-edge incidences have different roles, then try again
            if role_labels and (E0[k].role != E1[l].role):
                k_rejected += 1
                continue

            # Construct the proposal swap
            E0_prop = E0.copy()
            E0_prop[k] = NodeEdgeIncidence(E1[l].nid, E0[k].role, E0[k].eid, E0[k].meta)

            E1_prop = E1.copy()
            E1_prop[l] = NodeEdgeIncidence(E0[k].nid, E1[l].role, E1[l].eid, E1[l].meta)

            # if either of the edges would become degenerate, reject the proposal
            if check_degenerate(E0_prop) or check_degenerate(E1_prop):
                k_rejected += 1

            # otherwise, accept the proposal
            else:
                edges[i] = E0_prop
                edges[j] = E1_prop
                N += 1

        # update self.IL
        self.IL = [e for E in edges for e in edges[E]]

        if verbose:
            print(
                str(n_steps) + " steps taken, " + str(k_rejected) + " steps rejected."
            )

    def node_degrees(self, by_role=False, as_matrix=False):
        """
        Calculates the node degrees.

        Input:
            by_role (bool): If True, returns node degrees grouped by role (default False).
        
        Output:
            node_degrees (dict): The node degrees.
        """

        self.sort(by="role")

        if by_role:
            br = {role: list(v) for role, v in groupby(self.IL, lambda x: x.role)}
            DT = {role: Counter([e.nid for e in br[role]]) for role in self.roles}
            D = {k: {role: DT[role][k] for role in self.roles} for k in self.node_list}

            if as_matrix:
                M = np.zeros((self.n, len(self.roles)))
                ix_map = {ix: role for ix, role in enumerate(self.roles)}
                for i in range(self.n):
                    for j in ix_map:
                        M[i][j] = D[self.node_list[i]][ix_map[j]]
                return M
            return D

        else:
            V = [e.nid for e in self.IL]
            return dict(Counter(V))

    def edge_dimensions(self, by_role=False, as_matrix=False):
        """
        Calculates the edge dimensions.

        Input:
            by_role (bool): If True, returns edge dimensions grouped by role (default False).

        Output:
            edge_dimensions (dict): The edge dimensions.
        """
        self.sort(by="role")
        if by_role:
            br = {role: list(v) for role, v in groupby(self.IL, lambda x: x.role)}
            DT = {role: Counter([e.eid for e in br[role]]) for role in self.roles}
            D = {k: {role: DT[role][k] for role in self.roles} for k in self.edge_list}

            if as_matrix:
                M = np.zeros((self.m, len(self.roles)))
                ix_map = {ix: role for ix, role in enumerate(self.roles)}
                for i in range(self.m):
                    for j in ix_map:
                        M[i][j] = D[self.edge_list[i]][ix_map[j]]
                return M

            return D

        else:
            E = [e.eid for e in self.IL]
            return dict(Counter(E))

    def assign_role_interaction_matrix(self, R=None):
        """
        Assigns a role-interaction matrix to the annotated hypergraph.

        The R_ij defines the weight which a node in role i interacts with a node in role j
        when they belong to the same edge.

        Input:
            R (np.array): An array with role interaction weights. Must be of the same length
                          and in the same order as AnnotatedHypergraph.roles.

        TODO: Extend this to allow edge-dependent role-interaction matrices.
        """

        num_roles = len(self.roles)
        if R is not None:
            assert R.shape[0] == num_roles
            assert R.shape[1] == num_roles
            self.R = R
        else:
            self.R = np.ones(shape=(num_roles, num_roles))

    def to_weighted_projection(
        self, use_networkx=False, use_graphtool=False, as_matrix=False
    ):
        """
        Projects an annotated hypergraph to a weighted, directed graph.

        If role-interaction matrix has not been defined (through
        self.assign_role_interaction_matrix) then all interactions will be assigned a
        weight of one.

        Input:
            use_networkx (bool): If True, returns a networkx DiGraph object.
        
        Output:
            weighted_edges (dict): A dictionary containing all source nodes as keys.
                                   The values are dictionaries of targets which in turn
                                   contain weights of interaction.
        """
        if sum([use_networkx, use_graphtool, as_matrix]) > 1:
            raise Exception("Only one output format must be specified.")

        weighted_edges = defaultdict(lambda: defaultdict(lambda: 0.0))
        role_map = {role: ix for ix, role in enumerate(self.roles)}

        # Default behaviour if R has not been defined.
        if self.R is None:
            self.assign_role_interaction_matrix()

        for eid, edge in groupby(self.get_IL(), lambda x: x.eid):
            edge = list(edge)
            for a, b in permutations(edge, 2):
                weighted_edges[a.nid][b.nid] += self.R[
                    role_map[a.role], role_map[b.role]
                ]

        if as_matrix:
            M = np.zeros((self.n, self.n))
            for i in range(self.n):
                for j in range(self.n):
                    M[i, j] = weighted_edges[self.node_list[i]][self.node_list[j]]
            return M

        if use_networkx:
            weighted_edges = {
                source: {target: {"weight": val} for target, val in values.items()}
                for source, values in weighted_edges.items()
            }
            G = nx.DiGraph(weighted_edges)
            return G

        if use_graphtool:
            try:
                import graph_tool as gt
            except ImportError as e:
                print("Graph-tool not installed")
                return None

            G = gt.Graph(directed=True)
            weights = G.new_edge_property("float")
            node_labels = G.new_vertex_property("int")

            vertices = {}

            for source, edges in weighted_edges.items():
                for target, weight in edges.items():

                    s = vertices.get(source)
                    if s is None:
                        vertices[source] = G.add_vertex()
                        s = vertices[source]
                    t = vertices.get(target)
                    if t is None:
                        vertices[target] = G.add_vertex()
                        t = vertices[target]

                    e = G.add_edge(s, t)
                    weights[e] = weight

            for node, v in vertices.items():
                node_labels[v] = node

            G.edge_properties["weights"] = weights
            G.vertex_properties["node_labels"] = node_labels
            return G

        return weighted_edges

    def to_bipartite_graph(self, use_networkx=True):
        """
        Constructs a bipartitate representation of the annotated hypergraph.

        Both nodes and edges occur as vertices and are linked according to incidence.

        Input:
            use_networkx (bool): If True, returns a NetworkX graph object (default True).

        Output:
            G (nx.Graph): The bipartite graph.
        """

        ebunch = [(e.nid, -e.eid - 1, {"role": e.role}) for e in self.get_IL()]

        if use_networkx:
            G = nx.Graph()
            G.add_edges_from(ebunch)
            return G
        else:
            raise NotImplementedError("Currently only supporting NetworkX")

    def count_degeneracies(self):
        """Return the number of edges in which the same node appears multiple times"""

        self.sort(by="eid")
        by_edges = [list(v) for eid, v in groupby(self.IL, lambda x: x.eid)]

        return sum([check_degenerate(E) for E in by_edges])

    def remove_degeneracies(self, precedence):
        """
        Removes entries from self.IL in order of precedence until each node appears only once in each edge.

        Roles with higher precedence are retained.  
        May be overaggressive in  node removal -- further tests necessary 
        
        Input:
            precedence (dict): A dictionary of the form {role : p}. Lower p -> higher precedence.
        """

        self.IL.sort(key=lambda x: (x.eid, x.nid, precedence[x.role]))
        self.sort_key = "custom"

        grouped = [list(v) for eid, v in groupby(self.IL, lambda x: x.eid)]

        IL_ = []

        for E in grouped:
            E_ = []
            for e in E:
                if e.nid not in [e.nid for e in E_]:
                    E_.append(e)
            IL_.append(E_)

        IL_ = [e for E in IL_ for e in E]

        n_removed = len(self.IL) - len(IL_)
        print("Removed " + str(n_removed) + " node-edge incidences")
        self.IL = IL_
        self.IL.sort(key=lambda x: x.role)
        self.relabel()

    def remove_singletons(self):
        """
        Removes entries from self.IL if the corresponding edge contains only one node. 
        """

        D = self.edge_dimensions()
        to_remove = []
        for e in self.IL:
            if D[e.eid] == 1:
                to_remove.append(e)
        k_removed = len(to_remove)
        for e in to_remove:
            self.IL.remove(e)
        self.relabel()
        self.set_states()
        print("Removed " + str(k_removed) + " singletons.")

    def relabel(self):
        """
        TODO: Why does relabel_by_field return a value?
        """

        def relabel_by_field(D, field):

            D.sort(key=lambda x: x[field])
            j = 0
            old = 0
            for e in D:
                if e[field] != old:
                    old = e[field]
                    j += 1
                e[field] = j
            return D

        D = relabel_by_field(self.IL, "eid")
        D = relabel_by_field(self.IL, "nid")

        self.set_states()

    def stub_matching(self):
        """
        Return a randomized version of self constructed according to the naive stub-matching algorithm. 
        Preserves node-role and edge-role matrices, but generally introduces degeneracies. 
        """

        dims = self.edge_dimensions(by_role=True)

        stubs = [(e.nid, e.role) for e in self.IL]
        shuffle(stubs)
        stubs.sort(key=lambda e: e[1])
        stubs = {r: list(s) for r, s in groupby(stubs, key=lambda e: e[1])}

        a = deepcopy(self)
        IL_ = []

        for e in dims:
            for r in self.roles:
                for i in range(dims[e][r]):
                    to_add = stubs[r].pop(0)
                    e_ = NodeEdgeIncidence(
                        nid=to_add[0], role=to_add[1], eid=e, meta=None
                    )
                    IL_.append(e_)
        a.IL = IL_
        a.set_states()
        return a

    def bipartite_graph(self):
        """
        return an nx.Graph() in which both nodes and edges occur as nodes, and are linked according to incidence. 
        """
        ebunch = [(e.nid, -e.eid - 1, {"role": e.role}) for e in self.get_IL()]
        G = nx.Graph()
        G.add_edges_from(ebunch)
        return G

    def null_expectation_matrix(self):
        """
        return the second term in the dyadic modularity described in the draft text.
        """
        if self.R is None:
            self.assign_role_interaction_matrix()

        K = self.edge_dimensions(by_role=True, as_matrix=True)
        D = self.node_degrees(by_role=True, as_matrix=True)

        D_ = D.sum(axis=0)

        Gamma = np.dot(K.T, K) / np.outer(D_, D_)

        M = np.zeros((self.n, self.n))

        for x in range(len(self.roles)):
            for y in range(len(self.roles)):
                M += np.outer(D[:, x], D[:, y]) * Gamma[x, y] * self.R[x, y]

        return M

    def modularity_matrix(self, symmetrize=False):

        B = self.to_weighted_projection(as_matrix=True) - self.null_expectation_matrix()
        if symmetrize:
            B = (B.T + B) / 2
        return B


def bipartite_edge_swap(e0, e1):
    """
    Creates two new swapped edges by permuting the node ids.

    Used under the assumption that e0 and e1 are members of node-edge incidence 
    list with same role, although not explicitly checked.
    """

    f0 = NodeEdgeIncidence(e0.nid, e1.role, e1.eid, e1.meta)
    f1 = NodeEdgeIncidence(e1.nid, e0.role, e0.eid, e0.meta)

    return (f1, f0)


def swap_step(il):
    """
    Swap two node-edge incidence entries in the node-edge incidence list.

    Technical node: entries are replaced by new copies of the data.
    """
    n = len(il)
    i, j = np.random.randint(0, n, 2)
    while il[i].eid == il[j].eid:
        i, j = np.random.randint(0, n, 2)
    il[i], il[j] = bipartite_edge_swap(il[i], il[j])


def check_degenerate(E):
    """E is a set of node-edge incidences corresponding to a single edge"""
    E_distinct = set([e.nid for e in E])
    return len(E_distinct) != len(E)
