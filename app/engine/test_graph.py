# -*- coding: utf-8 -*-
"""偏序图引擎测试：删边/加边后的缓存一致性。

重点覆盖：无论祖先/后代闭包缓存此前建立了哪一侧（都没建、只建
后代、只建祖先、全建），删除关系后可达性、可互换组与本区层位都必须
与当前关系一致；随后再加边也不得复活旧可达关系。末尾为随机差分
测试：增量引擎在任意缓存状态下与全量重建结果逐项一致。

运行方式（任选其一）：

    python app/engine/test_graph.py
    cd app && python -m unittest engine.test_graph -v
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.graph import Poset  # noqa: E402

REL_TYPES = ("earlier", "cuts", "fills")


def build_xyuv(prewarm):
    """构造 x->u、y->u、u->v（同区），并按 prewarm 预热缓存。

    prewarm: "none" 不查任何闭包 / "desc" 只查 x 的后代 /
             "anc" 只查 v 的祖先 / "full" 全部查一遍。
    返回 (poset, ids, rid_uv)。
    """
    p = Poset()
    ids = {c: p.add_node(c, area_id="A") for c in ("x", "y", "u", "v")}
    x, y, u, v = (ids[c] for c in "xyuv")
    p.add_rel("earlier", x, u)
    p.add_rel("earlier", y, u)
    rid_uv, _ = p.add_rel("earlier", u, v)
    p._reset_caches()  # 模拟服务重启/回滚后的冷缓存，再按需预热
    if prewarm == "desc":
        p.descendants(x)
    elif prewarm == "anc":
        p.ancestors(v)
    elif prewarm == "full":
        for n in ids.values():
            p.descendants(n)
            p.ancestors(n)
    return p, ids, rid_uv


def fresh_clone(p):
    """按当前节点与关系全量重建一个全新引擎（参照系）。"""
    q = Poset()
    for nid, n in p.nodes.items():
        q.add_node(n["code"], n["kind"], n["name"], n["area_id"],
                   node_id=nid)
    for rid, r in p.rels.items():
        q.rels[rid] = dict(r)
        q._put_rel_in_adj(rid)
    q._reset_caches()
    return q


def assert_same_inference(tc, p, q):
    """两个引擎的全部推断结果逐项一致。"""
    tc.assertEqual(set(p.nodes), set(q.nodes))
    for nid in p.nodes:
        tc.assertEqual(p.descendants(nid), q.descendants(nid),
                       f"descendants({nid})")
        tc.assertEqual(p.ancestors(nid), q.ancestors(nid),
                       f"ancestors({nid})")
        tc.assertEqual(p.earliest(nid), q.earliest(nid),
                       f"earliest({nid})")
        tc.assertEqual(p.latest(nid), q.latest(nid),
                       f"latest({nid})")
    tc.assertEqual(p.area_heights(), q.area_heights())
    tc.assertEqual(p.interchangeable_groups(), q.interchangeable_groups())


class DeleteConsistencyTest(unittest.TestCase):
    """删边后在各种缓存预热状态下的推断一致性（任务书场景）。"""

    def test_delete_uv_any_cache_state(self):
        for prewarm in ("none", "desc", "anc", "full"):
            with self.subTest(prewarm=prewarm):
                p, ids, rid_uv = build_xyuv(prewarm)
                x, y, u, v = (ids[c] for c in "xyuv")

                affected = p.remove_rel(rid_uv)

                # 受影响对象必须覆盖 x、y、u、v
                self.assertEqual({x, y, u, v}, set(affected))
                # x 的后代只含 x、u（不再把 v 列为后代）
                self.assertEqual({x, u}, p.descendants(x))
                self.assertEqual({y, u}, p.descendants(y))
                # x、y 归为同一可互换组
                groups = [set(g["members"])
                          for g in p.interchangeable_groups()]
                self.assertIn({x, y}, groups)
                # 全量推断与重建参照系一致（含本区层位）
                assert_same_inference(self, p, fresh_clone(p))

                # 再加回 u->v：恢复当前可达，且不复活任何旧推断
                _, affected2 = p.add_rel("earlier", u, v)
                self.assertEqual({x, u, v}, p.descendants(x))
                self.assertEqual({x, y, u, v}, set(affected2))
                assert_same_inference(self, p, fresh_clone(p))

                # 再删一次，结果依旧
                rid_uv2 = next(rid for rid, r in p.rels.items()
                               if r["s"] == u and r["o"] == v)
                p.remove_rel(rid_uv2)
                self.assertEqual({x, u}, p.descendants(x))
                assert_same_inference(self, p, fresh_clone(p))

    def test_delete_one_of_two_types_keeps_time_edge(self):
        """同一对对象有两种关系时，删一条不改变时间边与缓存。"""
        p, ids, _ = build_xyuv("full")
        x, u = ids["x"], ids["u"]
        # 再补一种类型：fills(u, x) 的时间边仍是 x -> u
        rid2, affected = p.add_rel("fills", u, x)
        self.assertEqual(set(), affected)  # 时间边已存在，无结构变化
        before = p.descendants(x)
        affected = p.remove_rel(rid2)
        self.assertEqual(set(), affected)      # 时间边未变，无受影响对象
        self.assertEqual(before, p.descendants(x))
        self.assertEqual({x, u, ids["v"]}, p.descendants(x))
        assert_same_inference(self, p, fresh_clone(p))

    def test_delete_does_not_touch_other_area(self):
        """删边只影响本区，别区推断（含缓存）原样保留。"""
        p, ids, rid_uv = build_xyuv("full")
        g1 = p.add_node("G1", area_id="B")
        g2 = p.add_node("G2", area_id="B")
        p.add_rel("earlier", g1, g2)
        snap = {n: (p.descendants(n), p.ancestors(n),
                    p.earliest(n), p.latest(n)) for n in (g1, g2)}
        height_b = p.area_height("B")
        p.remove_rel(rid_uv)
        for n in (g1, g2):
            self.assertEqual(snap[n], (p.descendants(n), p.ancestors(n),
                                       p.earliest(n), p.latest(n)))
        self.assertEqual(height_b, p.area_height("B"))
        assert_same_inference(self, p, fresh_clone(p))


class DifferentialTest(unittest.TestCase):
    """随机差分：增量维护与全量重建逐项一致。

    每步随机执行加边/删边/删点/加点，并在操作之间随机查询部分节点
    的闭包与层位（制造任意“只建了一侧”的缓存状态）；每步之后与
    全量重建的参照系对比全部推断结果，并校验 affected 覆盖了
    可达性实际发生变化的节点。
    """

    def test_random_ops_match_rebuild(self):
        rng = random.Random(20261005)
        rounds = 1000
        for it in range(rounds):
            p = Poset()
            areas = ["A", "B", None]
            n_nodes = rng.randint(2, 6)
            node_ids = [p.add_node(f"N{i}", area_id=rng.choice(areas))
                        for i in range(n_nodes)]
            for step in range(rng.randint(1, 25)):
                op = rng.random()
                if op < 0.45:
                    # 加关系（同区：跨区关系在录入层被拒绝；
                    # 跳过自环/重复/成环）
                    s, o = rng.sample(node_ids, 2)
                    if p.nodes[s]["area_id"] != p.nodes[o]["area_id"]:
                        continue
                    rtype = rng.choice(REL_TYPES)
                    dup = any(r["s"] == s and r["o"] == o
                              and r["type"] == rtype
                              for r in p.rels.values())
                    if dup or p.check_cycle(rtype, s, o) is not None:
                        continue
                    before = {n: p.descendants(n) for n in node_ids}
                    _, affected = p.add_rel(rtype, s, o)
                    changed = {n for n in node_ids
                               if p.descendants(n) != before[n]}
                    self.assertLessEqual(changed, set(affected))
                elif op < 0.75 and p.rels:
                    # 删关系
                    rid = rng.choice(list(p.rels))
                    before = {n: p.descendants(n) for n in node_ids}
                    affected = p.remove_rel(rid)
                    changed = {n for n in node_ids
                               if p.descendants(n) != before[n]}
                    self.assertLessEqual(changed, set(affected))
                elif op < 0.85 and len(node_ids) > 2:
                    # 删对象（连同其关系）
                    victim = rng.choice(node_ids)
                    node_ids.remove(victim)
                    p.remove_node(victim)
                else:
                    # 加对象
                    nid = p.add_node(f"N{len(node_ids)}s{step}",
                                     area_id=rng.choice(areas))
                    node_ids.append(nid)
                # 随机查询一部分节点，制造部分缓存状态
                for n in rng.sample(node_ids, rng.randint(0, len(node_ids))):
                    rng.choice((p.descendants, p.ancestors,
                                p.earliest, p.latest))(n)
                if rng.random() < 0.3:
                    p.interchangeable_groups()
                # 与全量重建逐项一致
                assert_same_inference(self, p, fresh_clone(p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
