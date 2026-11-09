"""会话分叉纯逻辑单测：继承前缀截取、删除时保留哪些轮次、新加入模型接哪个父节点。

    .venv/bin/python -m pytest tests/test_conversation_tree.py
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.conversation_tree import (  # noqa: E402
    ForkLink,
    inherited_until,
    pick_fallback_parent,
    protected_batch_ids,
    take_until,
)


def _ids(count: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(count)]


def test_take_until_keeps_prefix_including_fork_round():
    batches = [SimpleNamespace(id=batch_id) for batch_id in _ids(3)]
    assert take_until(batches, None) == batches
    assert take_until(batches, batches[1].id) == batches[:2]
    assert take_until(batches, uuid.uuid4()) == []


def test_inherited_until_walks_through_intermediate_forks():
    root, fork, fork_of_fork, other = _ids(4)
    root_round_1, fork_round_1 = _ids(2)
    links = {
        fork: ForkLink(parent_id=root, until_batch_id=root_round_1),
        fork_of_fork: ForkLink(parent_id=fork, until_batch_id=fork_round_1),
    }
    assert inherited_until(fork, root, links) == root_round_1
    assert inherited_until(fork_of_fork, fork, links) == fork_round_1
    # 分叉的分叉也继承了根会话那段前缀。
    assert inherited_until(fork_of_fork, root, links) == root_round_1
    assert inherited_until(other, root, links) is None
    assert inherited_until(root, fork, links) is None


def test_protected_batches_take_longest_inherited_prefix():
    own = _ids(4)
    assert protected_batch_ids(own, []) == set()
    assert protected_batch_ids(own, [own[0], own[2]]) == set(own[:3])
    assert protected_batch_ids(own, [uuid.uuid4()]) == set()


def test_fallback_parent_prefers_first_ready_node_in_model_order():
    failed_a = SimpleNamespace(id=uuid.uuid4(), model_key="a", status="failed")
    ready_b = SimpleNamespace(id=uuid.uuid4(), model_key="b", status="ready")
    ready_c = SimpleNamespace(id=uuid.uuid4(), model_key="c", status="ready")
    assert pick_fallback_parent([ready_c, failed_a, ready_b], ["a", "b", "c"]) == ready_b.id
    assert pick_fallback_parent([failed_a], ["a"]) == failed_a.id
    assert pick_fallback_parent([], ["a"]) is None
