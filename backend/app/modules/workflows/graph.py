"""Workflow graph validation.

Pydantic (schemas.py) validates the JSON *shape*. This module validates
the *rules*: unique ids, DAG, referential integrity, node-type-specific
requirements. Some rules cross nodes (unique ids, DAG) so they can't be
expressed as Pydantic field validators.

The validator returns a list of errors. Empty list = valid. Multiple
errors returned rather than raising on the first, so a user fixing a
graph sees all issues at once.

Agent-id and tool-key visibility checks are done in service.py, not here.
They require DB access; this module is pure.
"""
from __future__ import annotations

from collections import defaultdict

from app.modules.workflows.schemas import Node, NodeType, WorkflowGraph


def validate_graph(graph: WorkflowGraph) -> list[str]:
    """Validate the graph. Returns list of errors. Empty = valid."""
    errors: list[str] = []

    nodes_by_id: dict[str, Node] = {}
    for node in graph.nodes:
        if node.id in nodes_by_id:
            errors.append(f"duplicate node id: {node.id!r}")
        else:
            nodes_by_id[node.id] = node

    # 1. Exactly one trigger node, matching entry_node_id
    trigger_nodes = [n for n in graph.nodes if n.type == NodeType.TRIGGER]
    if len(trigger_nodes) == 0:
        errors.append("graph must have exactly one trigger node, found 0")
    elif len(trigger_nodes) > 1:
        errors.append(
            f"graph must have exactly one trigger node, found {len(trigger_nodes)}: "
            f"{[n.id for n in trigger_nodes]}"
        )
    elif trigger_nodes[0].id != graph.entry_node_id:
        errors.append(
            f"entry_node_id={graph.entry_node_id!r} does not match "
            f"trigger node id={trigger_nodes[0].id!r}"
        )

    # 2. Edges reference existing nodes
    for src, dst in graph.edges:
        if src not in nodes_by_id:
            errors.append(f"edge source not found: {src!r}")
        if dst not in nodes_by_id:
            errors.append(f"edge target not found: {dst!r}")

    # 3. Node outgoing references (next, branches[].next) target existing nodes
    for node in graph.nodes:
        if node.next is not None and node.next not in nodes_by_id:
            errors.append(f"node {node.id!r} next={node.next!r} not found")
        if node.branches:
            for i, branch in enumerate(node.branches):
                if branch.next not in nodes_by_id:
                    errors.append(
                        f"node {node.id!r} branch[{i}].next={branch.next!r} not found"
                    )

    # 4. Node-type-specific requirements
    for node in graph.nodes:
        if node.type == NodeType.TERMINATE:
            if node.next is not None:
                errors.append(f"terminate node {node.id!r} must not have 'next'")
            if not node.config or not node.config.reason:
                errors.append(f"terminate node {node.id!r} requires config.reason")
        else:
            if node.type == NodeType.CONDITION:
                if not node.branches or len(node.branches) < 2:
                    errors.append(
                        f"condition node {node.id!r} requires 2+ branches, "
                        f"found {len(node.branches or [])}"
                    )
                if node.next is not None:
                    errors.append(
                        f"condition node {node.id!r} must use branches, not 'next'"
                    )
            else:
                if node.next is None:
                    errors.append(f"{node.type.value} node {node.id!r} requires 'next'")
                if node.branches:
                    errors.append(
                        f"{node.type.value} node {node.id!r} must not have branches"
                    )

        if node.type == NodeType.AGENT:
            if not node.config or not node.config.agent_id:
                errors.append(f"agent node {node.id!r} requires config.agent_id")
        if node.type == NodeType.TOOL:
            if not node.config or not node.config.tool_key:
                errors.append(f"tool node {node.id!r} requires config.tool_key")

    # 5. DAG check via topological sort (Kahn's algorithm)
    # Build adjacency from both edges AND node.next/branches references
    adj: dict[str, set[str]] = defaultdict(set)
    indeg: dict[str, int] = {n.id: 0 for n in graph.nodes}

    def _add_edge(src: str, dst: str) -> None:
        if src in nodes_by_id and dst in nodes_by_id and dst not in adj[src]:
            adj[src].add(dst)
            indeg[dst] += 1

    for src, dst in graph.edges:
        _add_edge(src, dst)
    for node in graph.nodes:
        if node.next:
            _add_edge(node.id, node.next)
        if node.branches:
            for branch in node.branches:
                _add_edge(node.id, branch.next)

    queue = [nid for nid, d in indeg.items() if d == 0]
    visited = 0
    while queue:
        cur = queue.pop(0)
        visited += 1
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)

    if visited != len(nodes_by_id):
        errors.append(
            f"graph has a cycle: {len(nodes_by_id) - visited} node(s) unreachable "
            f"via topological sort"
        )

    # 6. Reachability from entry node (only if entry is valid)
    if trigger_nodes and trigger_nodes[0].id in nodes_by_id:
        reachable: set[str] = set()
        stack = [graph.entry_node_id]
        while stack:
            cur = stack.pop()
            if cur in reachable or cur not in nodes_by_id:
                continue
            reachable.add(cur)
            for nxt in adj[cur]:
                stack.append(nxt)
        unreachable = set(nodes_by_id.keys()) - reachable
        if unreachable:
            errors.append(
                f"nodes unreachable from entry: {sorted(unreachable)}"
            )

    return errors


def is_valid(graph: WorkflowGraph) -> bool:
    """Convenience: True if graph has no validation errors."""
    return not validate_graph(graph)


__all__ = ["validate_graph", "is_valid"]
