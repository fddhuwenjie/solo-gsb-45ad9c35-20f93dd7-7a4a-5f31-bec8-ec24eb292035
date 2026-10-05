# 考古地层关系推断台

基于 **Python Flask + SQLite + 原生 HTML/CSS/JavaScript** 的单容器应用。
考古人员录入发掘区内的地层、遗迹单位以及它们之间的 **早于 / 切割 / 填充**
关系，系统维护一张带关系类型的有向偏序图（Harris 矩阵的时间语义），
自动完成：

- **约束校验**：拒绝跨发掘区引用、自环、重复关系；
- **矛盾检测**：每次提交做有向环检测，并给出经过新边的**最短矛盾链**；
- **层位推断**：**按发掘区分别计算**每个对象的最早 / 最晚层位
  （ASAP/ALAP，高度只取本区最长链，跨区长链不串扰），
  层位相等即位置被完全锁定；
- **可互换对象组**：在**同一发掘区内**找出严格前驱、后继集合完全
  相同的不可比对象（分属不同区的孤立对象不会成组）；
- **双视图举证**：关系矩阵（直接关系 + 间接可达底纹）与可点击的
  分层 SVG 关系图，可高亮任意两对象间的最短推断链；
- **增量重算**：增删边后只合并/失效受影响节点的缓存，
  API 返回受影响对象并在图中金色闪烁高亮；
- **版本管理**：每次成功提交自动保存完整快照，支持一键回滚，
  所有被拒绝的提交也会进入事件日志。

## 一条命令启动

```bash
docker compose up -d --build
```

然后浏览器访问： <http://localhost:8000>

数据保存在命名卷 `strata-data`（容器内 `/data/strata.db`），
停止/重建容器不丢失。停止与清理：

```bash
docker compose down          # 停止（保留数据）
docker compose down -v       # 停止并删除数据卷
```

> 首次启动会自动建库并写入一个演示探方（T1：L4–L1 自然堆积、
> 灰坑 H1 及其填土 F1、两个关系不明的柱洞 H2/H3）和一个用于验证
> 跨区拦截的 T2 探方（孤立地层 G1），可直接体验环检测与可互换组。

## 多发掘区复核步骤

层位窗口与可互换组都**按所在发掘区的偏序分别计算**：T1 的长链
不会影响 T2，跨区的孤立对象也不会被并入同一可互换组。首次启动
（或回滚到“初始演示数据”版本）后，可按下面步骤复核：

1. **首次 `GET /api/state`**：T2 唯一对象 **G1 的最早/最晚层位均为 0**
   （本区高度为 0），而不是 T1 长链的 5；可互换组只有 T1 区内的
   `H2 · H3 · L1`，G1 不与任何 T1 对象成组。

   ```bash
   curl -s localhost:8000/api/state | python3 -m json.tool \
     | grep -A6 '"code": "G1"'
   ```

2. **本区增边只影响本区**：新建 T2 对象 G2，再录入 `G1 早于 G2`，
   T2 高度由 0 变 1（G1=0、G2=1），接口 `affected` 只含 T2 对象；
   T1 所有对象的 `earliest/latest` 与 T1=5 的高度完全不变。
   在“层位推断”页可见 T2 窗口条形按本区 1 层缩放，T1 表格不变。

3. **本区最长链回缩，本区其他分支同步更新**：删除 T1 的 `F1 早于 L2`
   后，T1 高度由 5 降为 3，H1/F1/H2/H3 等分支的最晚层位一并回缩；
   **T2 的 G1 仍为 0/0**（别区推断不变）。

4. **跨区孤立对象不成组**：再建一个 T3 探方并录入孤立对象 X1，
   它不会与 T2 的 G1/G2 出现在同一可互换组中；矩阵的“标出可互换组”
   底纹也只覆盖同区格子。

5. **回滚后一致**：在“版本 / 日志”页回滚到 `R1 初始演示数据`
   （或 `POST /api/versions/1/restore`），页面层位表、可互换组与
   再次请求 `GET /api/state` 的结果一致：T1 高度恢复 5，G1 回到 0/0。

跨区关系、自环、重复关系与有向环在录入时仍按原规则拒绝（409），
所有成功提交与拒绝事件照常写入版本与日志。

## 删边后旧推断残留的复核步骤

删除一条关系时，只有当该对对象之间**再无其他类型关系**时时间边才
真正消失；引擎按删边前的真实祖先/后代闭包精确失效缓存，因此无论
祖先侧还是后代侧的缓存在删前是否建立过（包括服务重启、回滚后缓存
为空，或此前只查过某一侧），删边后的可达性、可互换组、本区层位都
与当前关系一致；随后重新加边也按新图计算，不会复活旧可达关系。

下面用一个独立发掘区 T9 做端到端复核（先 `docker compose up -d --build`，
访问 <http://localhost:8000>）：

```bash
BASE=http://localhost:8000
# 1) 建区与对象 x、y、u、v
A=$(curl -s -XPOST $BASE/api/areas -H 'Content-Type: application/json' \
      -d '{"code":"T9","name":"删边复核区"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
mk() { curl -s -XPOST $BASE/api/units -H 'Content-Type: application/json' \
      -d "{\"area_id\":$A,\"code\":\"$1\",\"kind\":\"layer\"}" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])'; }
X=$(mk X); Y=$(mk Y); U=$(mk U); V=$(mk V)
# 2) 录入 x→u、y→u、u→v
add() { curl -s -XPOST $BASE/api/relations -H 'Content-Type: application/json' \
      -d "{\"type\":\"earlier\",\"subject\":$1,\"object\":$2}"; }
add $X $U >/dev/null; add $Y $U >/dev/null
RUV=$(add $U $V | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
# 3) 删除 u→v
curl -s -XDELETE $BASE/api/relations/$RUV > /tmp/del.json
python3 - <<'EOF'
import json
d = json.load(open('/tmp/del.json'))
codes = {n['id']: n['code'] for n in d['nodes']}
aff = {codes[i] for i in d['affected']}
assert {'X','Y','U','V'} <= aff, aff                 # 受影响对象含全部四者
groups = [{codes[m] for m in g['members']} for g in d['groups']]
assert {'X','Y'} in groups, groups                    # x、y 归为同组
lay = {n['code']: (n['earliest'], n['latest']) for n in d['nodes']
       if n['code'] in ('X','Y','U','V')}
assert lay == {'X': (0,0), 'Y': (0,0), 'U': (1,1), 'V': (0,0)}, lay
print('删除 u→v 后：affected=%s；可互换组含 {X,Y}；层位 %s' % (sorted(aff), lay))
EOF
# 4) x 不再可达 v，但仍可达 u
curl -s "$BASE/api/basis?from=$X&to=$V" | grep -o '"reachable": false'
curl -s "$BASE/api/basis?from=$X&to=$U" | grep -o '"reachable": true'
# 5) 重新录入 u→v：x→v 按新图重新成立；再删后仍不成立
NEW=$(add $U $V | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -s "$BASE/api/basis?from=$X&to=$V" | grep -o '"reachable": true'
curl -s -XDELETE $BASE/api/relations/$NEW >/dev/null
curl -s "$BASE/api/basis?from=$X&to=$V" | grep -o '"reachable": false'
```

同一对对象若还有另一种类型关系，删除其中一条不改变时间边：在 u、v
上再录一条“V 填充 U”（时间方向同为 u→v）后删掉“U 早于 V”，
`affected` 为空、`u→v` 仍可达；再删掉填充关系，时间边才消失。

接口契约保持不变：`DELETE /api/relations/<id>` 成功仍返回 **200**，
响应仍是 `{"ok": true, ...全量 state}`（含 `affected` 节点 id 列表），
不存在的 id 返回 **404**；每次成功删除仍写入一条新版本与事件日志。

引擎层面的缓存一致性可用零依赖的差分测试复核（覆盖“只建过祖先侧 /
只建过后代侧 / 两侧都建过 / 重启后缓存为空”四种删边前状态、多类型
并行关系，以及 180 轮多区随机增删与“从零全量重算”参照结果的逐条
比对）：

```bash
python3 tests/test_graph_cache.py
```

## 不使用 Docker 的本地运行

```bash
pip install -r requirements.txt
export DATABASE_PATH=./strata.db      # 可选，默认 /data/strata.db
python app/app.py                      # 监听 0.0.0.0:8000
```

## 关系语义与时间方向

图中每条边都按**考古时间方向（早 → 晚）**参与偏序计算，
但矩阵/图上保留原始关系类型标注：

| 关系 | 含义 | 时间边（早 → 晚） |
|---|---|---|
| `earlier` 早于 | A 的形成早于 B | `A → B` |
| `cuts` 切割 | A（遗迹）打破 B（地层/遗迹） | `B → A` |
| `fills` 填充 | A 是 B 的填土/填充物 | `B → A` |

同一对单位之间可以并存多种关系类型（如“切割”与“早于”），
它们共享同一条时间边，但在界面上分别列出、可分别删除。

## 界面说明

- **关系图**：按最早层位分层（最早在底，符合地层直觉），
  绿/红/蓝箭头分别表示早于/切割/填充。滚轮缩放、拖拽平移；
  点击节点高亮其全部前因后果并在侧栏查看/删除关系；
  提交后边侧栏会列出“本次增量重算”的对象。
- **关系矩阵**：行是较早端、列是较晚端。对角格（自环）与
  跨区格直接禁用并做底纹标记；点击空格可选择要添加的关系类型，
  点击已有关系可删除，点间接格可在图中高亮其推断链。
- **层位推断**：按发掘区分别缩放的层位窗口条形图、最早/最晚层位与
  浮动量（摘要显示“T1 n 层 · T2 m 层”）；下方列出同区的可互换对象组，
  并提供任意两对象的最短推断链查询。
- **版本 / 日志**：版本快照列表可回滚（回滚本身也生成新版本，不丢数据）；
  事件日志区分“通过 / 拒绝”并记录矛盾链等原因。

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | 全量推断状态（区域/对象/关系/层位/按区的可互换组/受影响对象）；节点含 `area_height`（本区高度），顶层另附 `area_heights` 各区高度映射 |
| POST | `/api/areas` | 新建发掘区 `{code,name}` |
| POST | `/api/units` | 新建对象 `{area_id,code,kind,name}` |
| DELETE | `/api/units/<id>` | 删除对象及其全部关系 |
| POST | `/api/relations` | 提交关系 `{type,subject,object,note}`；冲突返回 409 |
| DELETE | `/api/relations/<id>` | 删除关系（增量重算） |
| GET | `/api/relations/<id>/basis` | 去掉该边后的最短间接推断链 |
| GET | `/api/versions` / `/api/versions/<id>` | 版本列表 / 快照详情 |
| POST | `/api/versions/<id>/restore` | 回滚 |
| GET | `/api/events` | 操作与拒绝日志 |

409 响应的 `code` 取值：`self_loop`（自环）、`cross_area`（跨发掘区）、
`duplicate`（重复关系）、`cycle`（有向环，附 `cycle` 节点序列与
`chain` 最短矛盾链文字说明）。

示例：

```bash
curl -sX POST localhost:8000/api/relations \
  -H 'Content-Type: application/json' \
  -d '{"type":"cuts","subject":5,"object":3}'
```

## 实现要点

- `app/engine/graph.py`：偏序图引擎。邻接表 + 反向邻接表；
  祖先/后代闭包记忆化 DFS；加边时先在旧图上强制展开
  `Anc(u) × Desc(v)` 锚点闭包再精确合并（缓存未建立不等于闭包只有
  自身——重载后首次操作或上次删边只失效过一侧时，另一批闭包可能
  缺失），删边时在**删边前**取两个锚点的真实闭包，时间边真正消失
  （该对对象已无任何类型关系）才按真实闭包精确失效缓存条目，
  缓存缺失按 `{自身}` 兜底会漏掉 x、y 一类间接祖先，正是“删边后
  仍沿用旧可达推断”的根因；**层位（最长链）按发掘区分别缓存**：
  每区维护独立高度，增删边只失效所在区的 `earliest/latest/tail/高度`，
  其他区的推断缓存原样保留；可互换组的等价键包含 `area_id`，
  跨区对象不成组。环检测使用 BFS 求“晚端 → 早端”的最短路径，
  即为最短矛盾链。`tests/test_graph_cache.py` 以零依赖的回归断言与
  180 轮多区随机差分测试（任意缓存预热、随机重载、加边/删边/删对象，
  与全量重建参照逐条比对闭包、层位、可互换组及别区不受影响）。
- `app/storage.py`：SQLite 建表与 CRUD（`areas / units / relations /
  versions / events`），版本保存完整 JSON 快照。
- `app/app.py`：Flask 路由、校验顺序（存在性 → 自环 → 跨区 → 重复 →
  环）、引擎与数据库的同步、快照回滚。
- `app/static/`：零依赖原生前端，SVG 手绘关系图。

## 目录结构

```
.
├── Dockerfile
├── docker-compose.yaml
├── requirements.txt
├── README.md
├── app
│   ├── app.py              # Flask 路由与业务编排
│   ├── storage.py          # SQLite 持久层
│   ├── engine/
│   │   └── graph.py        # 带关系类型的偏序图引擎（环检测/层位/增量）
│   └── static/
│       ├── index.html
│       ├── style.css
│       └── app.js          # 矩阵 + SVG 关系图 + 版本/日志
└── tests
    └── test_graph_cache.py # 删边缓存一致性回归 + 多区随机差分测试
```
