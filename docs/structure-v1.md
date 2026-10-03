# SLBH 结构数据格式 v1（slbh.structure 1.1）

施工图翻模的唯一输出格式。Blender 建模、Revit 导入、管综工具箱（lwp）、施工计划关联（schedule-link）都只读这一份，
不再各自从中间文件推断。生产者是翻模线（本仓库）；下游拿到什么就用什么，不自己猜"是否推定"。

文件：`projects/<id>/data/structure_<范围>.v1.json`，UTF-8。校验：`python tools/struct/slbh_struct.py validate <file>`。

生产：`python tools/struct/build_floor.py --project <id> --floor L1`，按 `projects/<id>/struct_config.json` 的楼层配置
（输入图纸、标号、人工确认项 not_holes / not_slab）推出一层（本层墙柱 + 上一层梁板）的全部结构事实。
所有推导（板分区、折板、梁顶随板顶并分段、墙顶到梁底、柱顶随梁板、补梁、扣窄条、梁端伸到支座）都在这里做，
下游只照做，不再各自推断。

| 版本 | 变化 |
|---|---|
| 1.0.0 | 首版（罗湖一层迁移） |
| 1.1.0 | 合流（2026-10-03）：板加 `cuts`、`slope`、`pieces`，墙加 `top_profile`；都是可选字段，1.0 的读法仍成立（读 1.0 的下游不认识新字段时，结果偏保守：被扣区域当成本板、斜板按低端） |

## 1. 约定

| 项 | 规定 |
|---|---|
| 单位 | mm；角度为度 |
| 平面坐标 | 项目原点（`origin`），罗湖 = 1-1 × 1-A 轴交点；不旋转 |
| 标高 | 构件写"所属标高 + 偏移"；同时给出算好的绝对值 `z`，两者必须一致（校验器检查） |
| 身份 | `key` = 规范键；`uuid` = UUID5(SLBH 命名空间, key)。下游只认 `uuid`，不自己生成 |
| 推定 | 生产者在每个构件上写明哪些字段是推定的（`basis.inferred_fields`），`basis.inferred` 由它推出 |
| 版本 | `version` 用 语义化版本；下游只接受主版本号 1 |

## 2. 文件结构

```json
{
  "schema": "slbh.structure", "version": "1.0.0",
  "project": {"id": "luohu", "name": "罗湖美术馆", "building": "1栋"},
  "scope": "地上一层（1F 墙柱 + 2F 梁板）",
  "tier": "bid",
  "units": "mm",
  "origin": {"desc": "1-1 × 1-A 轴交点", "grids": ["1-1", "1-A"]},
  "levels": [{"name": "1F", "elevation": -60, "source": "S-levels"}],
  "sources": [{"id": "GS-1-201", "title": "一层墙柱定位平面图", "crop": "1栋_1F_墙柱定位_1-1x1-A", "role": "vertical"}],
  "grids": [{"name": "1-1", "a": [x, y], "b": [x, y]}],
  "members": [ ... ],
  "issues":  [ ... ],
  "provenance": {"generator": "tools/struct/convert_luohu_v1.py", "inputs": ["..."], "created": "..."}
}
```

- `tier`：`bid`（投标级：缺标注按规则补齐，标推定）或 `construction`（施工级：不自动补，全部列问题）。
- `levels`：按标高升序；`name` 唯一。
- `sources[].id` 是图号；构件和问题用它引用图纸。

## 3. 构件（members）

公共字段：

| 字段 | 说明 |
|---|---|
| `uuid`, `key` | 身份。key = `{单体}\|{楼层}\|{类别}\|{编号}\|{x/100},{y/100}[\|{跨号}]`，坐标取构件中心（梁取跨中），0.1 m 取整 |
| `type` | `column` / `wall` / `beam` / `slab`（以后加 `foundation`、`opening`、`stair`） |
| `mark` | 图纸编号，梁含跨数，如 `KL17(4A)`；没有编号时按规则给并在 `inferred_fields` 里写 `mark` |
| `level` | 所属楼层（柱墙 = 底部所在层，梁板 = 所在层） |
| `grade` | 混凝土强度等级，如 `C40`、`C35P8` |
| `geom` | 按类别见下 |
| `basis` | `{"source": 图号, "rule": "图纸" 或推定规则的文字, "inferred_fields": [...], "inferred": bool}` |
| `status` | `ok` 或 `review`（标黄，需人工看）；`review` 时 `issues` 里必须有引用它的条目 |
| `extra` | 类别特有的附加信息，下游可忽略 |

`inferred` 的定义：`inferred_fields` 里只要有一个**尺寸或存在性**字段（`b`、`h`、`d`、`thickness`、`top`、`bottom`、`outline`、`position`、`existence`）就为 true。
只推定了编号（`mark`）、分跨（`span`）这类不影响几何的字段时为 false，但仍写在 `inferred_fields` 里。

各类别的 `geom`：

| 类别 | 字段 |
|---|---|
| column | `shape`: `rect` / `round` / `poly`（异形，只有轮廓）；`center` [x,y]；`rect` 有 `b`、`h`、`rot`（度，b 方向相对 X 轴）；`round` 有 `d`；都带原始轮廓 `outline`；`base` / `top` = `{"level", "offset"}`；`z` = [底, 顶] |
| wall | `shape`: `straight`（单段直墙）/ `poly`（L 形等，只有轮廓）；`straight` 有 `a`、`b`（核心层中心线端点）、`thickness`；都带原始轮廓 `outline`；`base` / `top` 同柱；`z` |
| beam | `a`、`b`（梁中心线，按跨）、`b_w`（宽）、`h`；`top` = `{"level", "offset"}`；`z` = [梁底, 梁顶] |
| slab | `outline`（外轮廓）、`holes`（洞口，可空）、`thickness`；`top` = `{"level", "offset"}`；`z` = [板底, 板顶]。1.1 可选：`cuts`、`slope`、`pieces`（见下） |

1.1 新增的可选字段：

| 字段 | 说明 |
|---|---|
| slab `cuts` | 从本板扣掉的区域（别的板占的降板分区、折板区、压在构件上的整板窄条等）。可越出外轮廓、可相互重叠。板的实际范围 = `outline − holes − cuts` |
| slab `slope` | 斜板：`{"tail": [x,y], "head": [x,y], "rise": mm, "slope": 比值}`。板顶在 tail 处 = `top`（低端），沿 tail→head 线性升高，到 head 处高 `rise`；`z` 按低端写 |
| slab `pieces` | `outline − holes − cuts` 拆成的若干块 `[{"outline", "holes"}]`，给不做布尔运算的下游用；近似（10 mm 栅格，拐角吸回原始顶点，斜边偏差 ≤ 10 mm），面积 < 0.5 m² 或平均宽 < 100 mm 的碎块不列。只在有 `cuts` 的板上给 |
| wall `top_profile` | 墙顶沿墙长有起伏时（局部到梁底、随折板），折线 `[[x, y, z绝对], …]`；`top` / `z` 取最高处 |
| 构件 `extra` | 折板面 `extra.fold` = `{"grp", "i"}`、`extra.code` = `ZB`、`extra.name` = `预应力折板`；分区板 `extra.note`（如“卫生间”）；补的梁 `extra.supplement` = true、分段梁 `extra.piece_of` = 原梁 key |

注：梁宽用 `b_w`，避免和端点 `b` 重名。柱里的型钢（钢骨）不单列构件，写在柱的 `extra.steel_core`（轮廓列表）。
下游做不了的形状（如 Revit 导入器暂不建 L 形墙、异形柱）由下游自己跳过并记日志；这不是图纸问题，不进 `issues`。

## 4. 问题（issues）

图纸问题清单直接从这里生成。

| 字段 | 说明 |
|---|---|
| `id` | 文件内唯一，如 `I-001` |
| `kind` | `missing_annotation` 无集中标注 / `span_mismatch` 跨数对不上 / `annotation_without_geometry` 有标注无图形 / `annotation_outside` 标注在图框外 / `not_modeled` 识别了但没建 / `existence_unconfirmed` 是否存在待确认 / `zoning_missing` 未分区（如板厚、板顶） / `conflict` 图纸之间或图内矛盾 |
| `members` | 引用的构件 uuid（可空） |
| `source` | 图号 |
| `at` | [x, y]（可空）；`grid_ref` 轴网位置描述 |
| `text` | 现象 |
| `ask` | 建议向设计提的问题（可空） |
| `impact` | 影响：`geometry` / `quantity` / `headroom` / `naming` |

## 5. 校验（`slbh_struct.py validate`）

1. JSON Schema（`structure_v1.schema.json`）：字段、类型、枚举。
2. 语义：uuid 唯一且等于 UUID5(key)；引用的标高、图号存在；`z` 与"标高 + 偏移"一致（±1 mm）；
   `inferred` 与 `inferred_fields` 一致；`status = review` 的构件都有问题引用；问题引用的 uuid 都存在；
   洞口在板外轮廓内；梁、墙长度 > 0。

## 6. 下游怎么读 1.1

- Revit（`tools/revit/build_revit_plan.py`）：只读 v1.1，自己做类型名、族、板扣构件外框（mcuts）、指纹；`shape = poly` 的墙柱跳过并记录。
- 管综 lwp（`lwp struct`）：`cuts` 当洞口（判断点在板内 = 在外轮廓内且不在任何洞口 / cuts 内），斜板的包围盒到高端，净高按低端（偏保守）。
- schedule-link：有 `pieces` 的板按块建构件（多块时 key = 板 key + `|块n`，父键 = 板），斜板高到高端。
- Blender（`projects/luohu/scripts/build_structure_v2.py`）：直接读 v1（不再转旧格式），板用 `pieces`，斜板按坡度抬高，带 `top_profile` 的墙按立面轮廓建。

## 7. 迁移

- （已停用，2026-10-03 起由 `build_floor.py` 直接生成）罗湖一层：`tools/struct/convert_luohu_v1.py` 把 `structure_1F_v2.json` + `beams_2F_spans.json` + Revit 计划（墙柱编号、截面、轴网名）合成 `structure_1F.v1.json`。只用于迁移；新楼层由翻模直接写 v1。
- 身份：统一用 SLBH 命名空间 `6f1b2c3d-5e4f-4a1b-9c8d-7e6f5a4b3c2d`。梁原来用另一个命名空间，迁移后梁的 uuid 会变；Revit 计划生成器改读 v1 时（v0.4 验证完成后）重新导入一次。
- 下游兼容旧格式一个版本，罗湖转换完成后删除。
