# -*- coding: utf-8 -*-
"""删边/加边后增量缓存与全量推断一致的回归与差分测试。

不依赖第三方库，直接运行：

    python3 tests/test_graph_cache.py

覆盖三类问题：

1. 报告场景：同区 x→u、y→u、u→v，删除 u→v 后，无论祖先/后代缓存
   此前建立了哪一侧（含服务重启后首次操作），可达性、可互换组、本区
   层位都必须与当前关系一致，受影响对象含 x、y、u、v；删后再加边
   不得复活/丢失可达关系。
2. 同一对对象存在多种关系类型时，删除其中一条不改变时间边。
3. 随机差分：多区、任意缓存预热、随机重载、加边/删边/删对象序列，
   逐步与“从零全量重算”的参照引擎比对闭包、层位、可互换组，
   并校验别区推断不被波及。
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from engine.graph import Poset  # noqa: E402

TYPES = ("earlier", "cuts", "fills")


# ---------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------
def fresh_four(area="A", with_w=False):
    """直接装邻接表 + 清空缓存，模拟 app.reload_engine 的结果。"""
    p = Poset()
    ids = {}
    for c in (("x", "y", "u", "v", "w") if with_w else
              ("x", "y", "u", "v")):
        ids[c] = p.add_node(c, area_id=area)
    rels = [("x", "u", "earlier"), ("y", "u", "earlier"),
            ("u", "v", "earlier")]
    next_id = 1
    for s, o, t in rels:
        rid = str(next_id)
        next_id += 1
        p.rels[rid] = {"id": rid, "s": ids[s], "o": ids[o], "type": t}
        p._put_rel_in_adj(rid)
    p._reset_caches()
    p._next_rel_id = next_id
    return p, ids


def groups_by_code(p):
    return sorted(
        sorted(p.nodes[m]["code"] for m in g["members"])
        for g in p.interchangeable_groups())


# ---------------------------------------------------------------------
# 1. 报告场景：删 u→v（四种缓存建立状态）
# ---------------------------------------------------------------------
def check_deletion_after_cache_state(warm):
    p, ids = fresh_four()
    warm(p, ids)
    rid_uv = next(rid for rid, r in p.rels.items()
                  if r["s"] == ids["u"] and r["o"] == ids["v"])

    affected = p.remove_rel(rid_uv)

    assert p.descendants(ids["x"]) == {ids["x"], ids["u"]}
    assert p.descendants(ids["y"]) == {ids["y"], ids["u"]}
    assert p.descendants(ids["u"]) == {ids["u"]}
    assert p.ancestors(ids["v"]) == {ids["v"]}
    assert groups_by_code(p) == [["x", "y"]]
    # 层位与当前关系一致：x、y、u 为第 0 层，v 不再处于链上
    assert p.earliest(ids["x"]) == 0
    assert p.earliest(ids["y"]) == 0
    assert p.earliest(ids["u"]) == 1
    assert p.earliest(ids["v"]) == 0
    assert p.area_height("A") == 1
    # 受影响对象必须包含 x、y、u、v
    assert {ids[k] for k in ("x", "y", "u", "v")} <= affected

    # 删后再加边：可达关系按新图重新成立；再删仍正确（不复活旧推断）
    new_rid, _ = p.add_rel("earlier", ids["u"], ids["v"])
    assert p.descendants(ids["x"]) == {ids["x"], ids["u"], ids["v"]}
    assert p.area_height("A") == 2
    p.remove_rel(new_rid)
    assert p.descendants(ids["x"]) == {ids["x"], ids["u"]}
    assert p.ancestors(ids["v"]) == {ids["v"]}
    assert p.area_height("A") == 1


def test_report_scenario_all_cache_sides():
    check_deletion_after_cache_state(lambda p, i: None)  # 两侧都未建
    check_deletion_after_cache_state(                     # 只查过后代
        lambda p, i: p.descendants(i["x"]))
    check_deletion_after_cache_state(                     # 只查过祖先
        lambda p, i: p.ancestors(i["v"]))
    check_deletion_after_cache_state(                     # 两侧都建过
        lambda p, i: (p.descendants(i["x"]), p.ancestors(i["v"])))


def test_deletion_then_unrelated_add_does_not_revive():
    """删 u→v 后加一条 y→v，v 不得复活为 x 的后代。"""
    p, ids = fresh_four()
    p.descendants(ids["x"])
    rid_uv = next(rid for rid, r in p.rels.items()
                  if r["s"] == ids["u"] and r["o"] == ids["v"])
    p.remove_rel(rid_uv)
    p.add_rel("earlier", ids["y"], ids["v"])
    assert p.descendants(ids["x"]) == {ids["x"], ids["u"]}
    assert p.descendants(ids["y"]) == {ids["y"], ids["u"], ids["v"]}
    # u→v 已删，u 不再是 v 的祖先
    assert p.ancestors(ids["v"]) == {ids["v"], ids["y"]}


# ---------------------------------------------------------------------
# 2. 同对多类型关系
# ---------------------------------------------------------------------
def test_parallel_types_share_time_edge():
    p, ids = fresh_four()
    # v fills u 的时间方向也是 u→v，与已有的 u 早于 v 并行
    rid2, affected = p.add_rel("fills", ids["v"], ids["u"])
    assert affected == set()
    rid_e = next(rid for rid, r in p.rels.items()
                 if (r["s"], r["o"], r["type"]) ==
                 (ids["u"], ids["v"], "earlier"))
    # 删除 earlier：fills 仍在，时间边不变，无对象受影响
    assert p.remove_rel(rid_e) == set()
    assert p.descendants(ids["x"]) == {ids["x"], ids["u"], ids["v"]}
    # 再删 fills：时间边才真正消失
    assert p.remove_rel(rid2) == {ids[k] for k in ("x", "y", "u", "v")}
    assert p.descendants(ids["x"]) == {ids["x"], ids["u"]}


# ---------------------------------------------------------------------
# 3. 随机差分
# ---------------------------------------------------------------------
def reference(units, rels):
    """按 (id, area) 与当前关系从零建参照引擎。"""
    q = Poset()
    for uid, code, area in units:
        q.add_node(code, area_id=area, node_id=uid)
    for rid, s, o, t in rels:
        q.rels[str(rid)] = {"id": str(rid), "s": str(s),
                            "o": str(o), "type": t}
        q._put_rel_in_adj(str(rid))
    q._reset_caches()
    area_of = {str(u): a for u, _, a in units}
    out = {}
    for uid, _, _ in units:
        n = str(uid)
        out[n] = (frozenset(q.descendants(n)),
                  frozenset(q.ancestors(n)),
                  q.earliest(n), q.latest(n),
                  q.area_height(area_of[n]))
    out["groups"] = sorted(tuple(sorted(g["members"]))
                           for g in q.interchangeable_groups())
    return out


def _warm(p, units, rng):
    for _ in range(rng.randint(0, 4)):
        n = str(rng.choice(units)[0])
        (p.descendants if rng.random() < 0.5 else p.ancestors)(n)
        if rng.random() < 0.3:
            p.earliest(n)
        if rng.random() < 0.3:
            p.latest(n)
    p.interchangeable_groups()


def _reload(units, rels, next_rid):
    q = Poset()
    for uid, code, area in units:
        q.add_node(code, area_id=area, node_id=uid)
    for rid, s, o, t in rels:
        q.rels[str(rid)] = {"id": str(rid), "s": s, "o": o, "type": t}
        q._put_rel_in_adj(str(rid))
    q._reset_caches()
    q._next_rel_id = next_rid
    return q


def _assert_match(p, units, rels, tag):
    ref = reference(units, rels)
    area_of = {str(u): a for u, _, a in units}
    for uid, _, _ in units:
        n = str(uid)
        got = (frozenset(p.descendants(n)), frozenset(p.ancestors(n)),
               p.earliest(n), p.latest(n), p.area_height(area_of[n]))
        assert got == ref[n], f"{tag}: {n} {got} != {ref[n]}"
    got_groups = sorted(tuple(sorted(g["members"]))
                        for g in p.interchangeable_groups())
    assert got_groups == ref["groups"], f"{tag}: groups 不一致"


def fuzz_round(rng, n_nodes, multi_area, steps):
    units = []
    for i in range(n_nodes):
        area = ("A" if i < n_nodes // 2 else "B") if multi_area else "A"
        units.append((i + 1, f"n{i}", area))
    p = Poset()
    for uid, code, area in units:
        p.add_node(code, area_id=area, node_id=uid)
    rels = []
    next_rid = 1

    for step in range(steps):
        _warm(p, units, rng)
        if rng.random() < 0.12:  # 模拟重启/回滚后从库重载
            p = _reload(units, rels, next_rid)

        if rels and rng.random() < 0.35:
            idx = rng.randrange(len(rels))
            rid, s, o, t = rels[idx]
            if multi_area:
                area = next(a for u, _, a in units if str(u) == s)
                other = [str(u) for u, _, a in units
                         if a != area]
                before = {n: (p.earliest(n), p.latest(n)) for n in other}
            p.remove_rel(str(rid))
            del rels[idx]
            if multi_area:
                after = {n: (p.earliest(n), p.latest(n)) for n in other}
                assert before == after, "别区层位被增删边波及"
        elif multi_area and rng.random() < 0.04:
            used = {x for _, s, o, _ in rels for x in (s, o)}
            free = [u for u, _, _ in units if str(u) not in used]
            if free:
                victim = rng.choice(free)
                p.remove_node(str(victim))
                units = [u for u in units if u[0] != victim]
        else:
            area = rng.choice(sorted({a for _, _, a in units}))
            members = [str(u) for u, _, a in units if a == area]
            cands = []
            for s in members:
                for o in members:
                    if s == o:
                        continue
                    for t in TYPES:
                        if any(rs == s and ro == o and rt == t
                               for _, rs, ro, rt in rels):
                            continue
                        early, late = Poset.endpoints(t, s, o)
                        if p.shortest_path(late, early) is not None:
                            continue
                        cands.append((s, o, t))
            if cands:
                s, o, t = rng.choice(cands)
                p.add_rel(t, s, o, rel_id=next_rid)
                rels.append((next_rid, s, o, t))
                next_rid += 1

        _assert_match(p, units, rels,
                      f"multi={multi_area} step={step}")


def test_random_differential():
    rng = random.Random(20261005)
    for rnd in range(120):
        fuzz_round(rng, n_nodes=7, multi_area=False, steps=50)
    for rnd in range(60):
        fuzz_round(rng, n_nodes=10, multi_area=True, steps=60)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    if failures:
        print(f"\n{failures} 个测试失败")
        sys.exit(1)
    print("\n全部通过")
