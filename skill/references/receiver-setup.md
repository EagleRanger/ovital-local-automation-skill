# 接收方配置与代码入口

## 环境

普通离线几何模块使用 Python 与公开库；原生消息适配器当前要求 Windows、64位Python及已经运行的奥维。源工程已验证的计算环境是 CPython 3.14；本次不宣称所有Python/奥维版本均已测试。

在本包 `skill/` 下建立独立环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

软件本体由用户正常安装并自行登录。原生接口的账户权限、启用状态、进程位数和权限等级需按本机核实；不修改账号、不借安装任务写入真实收藏夹。缓存地图导出当前受官方 SVIP 权限限制。无 ArcGIS/ArcPy 依赖。

## 模块调用

模块位于 `scripts/ovital/`，从 `skill/scripts/` 作为当前目录调用，或把此目录加入当前Python进程的模块路径。不要把作者绝对路径写进接收方配置。

| 模块 | 用途 |
| --- | --- |
| `ovital.protocol` | 官方消息对象头、Track/Shape/Group结构、编解码与语义签名 |
| `ovital.session.WinMessageTransport` | 隐藏回调窗口、绑定目标进程、消息/回调时限和默认只读传输 |
| `ovital.favorites.Favorites` | 目录树、快照、对象绑定、范围受限CRUD和备份 |
| `ovital.favorites.save_and_verify` | 动态解析后台保存动作、指定数据库变化和独立会话读回 |
| `ovital.geometry` | 坐标转换、局部米制计算、封面、外扩、叠加、差集、去重、计量 |
| `ovital.kml` | KML/KMZ解析与导出，保留多部件/孔洞与目录语义 |
| `ovital.imagery` | 当前地图信息、缓存范围导出、坐标叠图与沿线展开 |

离线计算示例（纯合成，不连接奥维）：

```python
from shapely.geometry import LineString
from ovital.geometry import close_road_pair, buffer_m, measure

left = LineString([(0, 0), (100, 0)])
right = LineString([(0, 4), (100, 4)])
road, checks = close_road_pair(left, right, crs="EPSG:32650", max_cap_distance=5)
expanded = buffer_m(road, 3, crs="EPSG:32650")
print(measure(expanded, crs="EPSG:32650"))
```

这里的坐标、宽度、投影和偏移仅为合成样例，不是设计默认值。真实数据先确认 CRS 与物理基准，再选投影和参数。

KML/KMZ保留支持的几何、目录和本地Style/StyleMap引用闭包（含normal/highlight）；未知几何（如Model）、混合未知成员、样式断链/循环/冲突均明确拒绝，不静默略过后输出残缺结果。KML输出采用其经纬度约定，不能直接写投影平面坐标。

原生调用先读 [后台操作边界](native-workflow.md)。默认 `WinMessageTransport(allow_write=False)`；只在当前用户明确授权目标后，才启用写传输并给 `Favorites` 配置明确的 `allowed_ids`、`allowed_parents`、`backup_dir`。不要为了“装好验证”创建试验收藏夹。

## CLI

在 `skill/` 目录执行（前两条不连接奥维）：

```powershell
python scripts/ovital_tool.py --help
python scripts/ovital_tool.py kml-info 'D:/自己的输入/input.kmz'
python scripts/ovital_tool.py calculate plan.json --output result.json
```

可先执行 `python scripts/ovital_tool.py calculate examples/synthetic-road.json --output outputs/synthetic-road-result.json`：只是生成100×4的合成矩形及计量，不连接奥维、不使用真实工程。再次执行须换新输出位置，不能覆盖或把已有结果当作本次新成功。

`calculate` 的 JSON 计划使用 `operation`、`crs` 与 GeoJSON `geometries`；支持 `buffer`、`overlay`、`road-pair`、`corridor`、`extend`、`reproject`、`replace-covered`，对应函数必需参数以脚本实现及包内合成测试为准。输出已存在时拒绝覆盖。

连接真实奥维必须显式选择原生入口：

```powershell
python scripts/ovital_tool.py native plan.json --receipt new-receipt.json --allow-live
```

计划的 `action` 可以是 `snapshot`、`map-info`、`export-cache`、`mutate` 或 `save`；后两项还须 `--allow-write`，并提供本次授权的ID/父级范围和备份位置。`--allow-live/--allow-write` 是工具防误触门槛，不代替用户授权。已有回执禁止重新运行，先核部分成功和现场；不要删回执来强行重跑。

## 验证与迁移

从 `skill/` 执行 `python -m unittest discover -s tests -v` 和CLI帮助，再在接收方授权范围内检查版本/只读快照。当前发布测试不启动奥维，不取用户现场数据，不访问真实收藏夹，也不宣称后台保存已经在新机实测。

若缺权限、API版本/结构不符、参考图源缺片或无匹配保存动作，报告具体缺口并保留现场；不要切换前台坐标点击、放宽校验或沿用历史句柄。接收方可在对应接口做局部适配，核心几何逻辑不必重写。
