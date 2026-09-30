# lwp 管综工具箱

Python 3.11+，依赖由 uv 管理。当前实现 R0（导入、体检、碰撞 / 净距 / 净高检测、只读三维预览）和 R1 v0（直走廊单断面分层求解，见 docs/r1-v0-results.md）。

## 安装与运行

```bash
cd toolbox
uv sync
uv run lwp check path/to/项目_视图_管综.slbh            # 默认规则：小区地下室
uv run lwp check path/to/xxx.slbh --rules my_rules.yaml --out out_dir
uv run lwp view path/to/xxx.slbh                       # 只生成三维预览（含碰撞检测）
uv run lwp check 调整后.slbh --compare 调整前.slbh       # 预览中加入调整前后对比
uv run lwp corridors 调整前.slbh                        # 列出直走廊候选
uv run lwp solve 调整前.slbh --corridor C --gold 调整后.slbh --out solve_C   # 分层排布三方案 + 对比
uv run pytest                                          # 单元测试
LWP_SAMPLE=path/to/xxx.slbh uv run pytest              # 加上真实导出包的端到端测试
```

输出目录（默认 `<包名>.lwp/`）：

| 文件 | 内容 |
| --- | --- |
| report.md | 体检与检测报告（摘要、系统识别、保温、碰撞、净距、穿墙、净高、模型质量） |
| conflicts.json | 全部碰撞、净距不足、穿墙明细，含轴网定位 |
| headroom.json | 全部水平管段的净高 |
| view.html | 三维预览：单文件、离线可用，浏览器直接打开 |

## 三维预览

- 着色模式：系统 / 底部净高（构件底距楼面的色带）/ 调整对比（上移、下移、水平移动、新增、未变）。
- 调整对比：同一 UniqueId 直接比较；调整时被打断或重画的管线按位置匹配（同系统、平行、平面距离 ≤ 150 mm）。
  可叠加显示参照模型（半透明），已删除或替换的构件为红色。
- 净高分区图：0.5 m 网格，取构件（水平管段、管件、附件、风口）或梁底距楼面的最小值，按要求值分 5 档；
  点击分区图显示数值和控制构件；标出最低点，可一键定位。“只看分区图”隐藏全部构件。
- 漫游：人眼高度 1.6 m，W/S/A/D 移动，Q/E 升降，拖动转向，滚轮调速，Shift 加速，Esc 退出；十字准星处单击选择构件。
- 按系统开关图层；梁、柱半透明，墙更淡。
- 顶部剖切（按楼层以上高度）和竖向剖切（沿 X / Y，可翻转）。
- 右侧冲突列表按类型筛选（硬碰撞、净距不足、未连接接驳、同系统重叠、穿墙），单击定位并高亮两个构件（A 黄色、B 洋红），显示距离和要求。
- 单击构件显示系统、族类型、保温来源、底 / 顶标高、净高、几何精度。
- 左键旋转、右键平移、滚轮缩放、双击设旋转中心。
- 圆管、矩形风管、桥架按参数显示；管件、梁、柱、墙显示为拟合包围盒（近似外形）。
- 使用 three.js 0.160.1（MIT，`src/lwp/viewer/LICENSE-three.txt`），内联在 HTML 中。

## 规则

所有工程数值在规则文件中，默认 `src/lwp/rules/residential_basement.yaml`：系统分类、默认保温厚度、净距矩阵、净高要求、障碍物类别。
`confirmed: false` 的条目是推定值，报告会单独列出。项目规则文件复制默认文件修改即可。

## 计算口径

- 单位毫米，坐标为 Revit 内部坐标（宿主模型）。
- 圆管按胶囊体、矩形风管和桥架按有向长方体（截面方向取自连接件坐标系），均含保温，距离为精确值。
- 管件、附件、梁、柱、墙按网格拟合的有向包围盒，报告中“精确”列标为否。
- 连接关系 3 跳以内的构件之间不检测（管段与管件、管件两侧管段、变径加弯头等组合）；支吊架不参与检测。
- 同一系统内的重叠不计入硬碰撞：端口插入另一构件而未连接的记为“未连接接驳”，其余记为“同系统重叠”，列入模型质量。
- 只统计剖面框范围内的结果；构件对逐对计数（Navisworks 口径），碰撞点把 1 m 内的构件对合并。
- 净高只统计水平管段，地面取下方最近的主模型标高。

## 模块

| 模块 | 职责 |
| --- | --- |
| rules.py | 规则文件加载与校验 |
| package.py | 读取导出包，换算为毫米，建立构件实体 |
| classify.py | 系统分类 |
| geometry.py | 胶囊体、有向长方体、距离计算 |
| detect.py | 碰撞、净距、穿墙、净高 |
| health.py | 模型体检 |
| grids.py | 轴网定位 |
| report.py | 报告输出 |
| headroom_map.py | 净高分区图（网格化最低遮挡） |
| corridor.py | 走廊定义与候选 |
| section.py | 走廊断面：管线归并、梁柱墙限制、横穿、出线方向 |
| solver.py | CP-SAT 分层求解、三方案、无解诊断 |
| plan.py | 方案应用、方案 JSON、走廊评估 |
| section_svg.py | 断面图 |
| solve_cmd.py | corridors / solve 命令与结果页 |
| viewer/ | 三维预览（数据生成 + HTML 模板 + three.js） |
| cli.py | 命令行 |
