# 双修订高程差值带

两次测绘使用同一平面坐标系。归档员从已登记的两份修订生成一份变化带，合作测绘组收到包后可以复核分量边界、面积和各次测绘的原面贡献。

## 加工接口

在 `terrain.archive.api` 提供 `build_change_band(catalog, request, old_revision_id, new_revision_id, lo, hi)`。请求、修订标识和精确有理输入沿用 README 中的口径。`lo < hi`。网格的顶点和面身份由各自的修订解释，双方标识及剖分可不同。同修订和空网格合法。

只考虑两份网格面域的交集。在共同覆盖内，差值等于该点的新场高程减旧场高程。只取 `lo <= 差值 < hi`。共同域仅在点或线上接触时没有正面积贡献。对正面积带内区域，共边的相对内部须有正长度且属于该区间才给出连接。下界等值边可连接，整段处在上界的边及点接触不连接。面积环表示各分量的二维闭包，分量内若可绕开零宽裂缝到达另一侧，面积环仍表示整个面载体。外环逆时针，洞环顺时针。允许保留共线点，环点序列不重复首点。分量及洞顺序和环起点自由。数值计算使用 `Fraction`，坐标、高程差和面积的单位沿用原档案。

返回可由 `Catalog.load_product` 和 `read_saved_product` 读取的成果。Python 调用约定如下。

| 属性 | 含义 |
| --- | --- |
| `product.product_id` | 请求身份 |
| `product.payload.kind` | `change_band` |
| `product.payload.revision_ids` | `(old_revision_id, new_revision_id)` |
| `product.payload.levels` | 精确的 `(lo, hi)` |
| `product.payload.result.components` | 分量元组，空结果使用 `()` |
| `product.payload.result.area` | 全部组件的精确面积和 |
| `component.outer.points`、`component.holes` | 外环有理数点元组，以及带 `.points` 的洞环元组 |
| `component.area` | 该分量的精确面积 |
| `component.contributions` | `(旧面ID, 新面ID, Fraction面积)` 三元组的元组，按前两项排序，同一对在同一分量内只出现一次 |

逐对面积来自该分量中实际由两面共同覆盖的带内区域。每一笔贡献为正，逐对面积和等于该分量面积。请求号与 `build_bands`、`build_contours` 共用。有序双修订和规范化区间构成新成果的绑定。已存在的同绑定请求读取保存成果，换序、改界或改种类均触发 `ProductBindingConflict`。按原异常语义拒绝非法参数（`ValueError`）、缺失源修订（`UnknownRevision`）和写入失败（`CatalogError`）。失败不留新请求或部分成果。更正测绘、关闭重开、修改调用者持有的数据都不改变保存成果。

## 固定交付

`read_export(catalog, product_ids, destination=None)` 的差值路径接收单个差值成果的 ID 或仅含该 ID 的序列。差值与其他成果混装须拒绝。视图的 `revisions` 是按旧、新排列的两个 `MeshRevision`，`products` 是单元素成果元组。读取和导出消费已保存的结果，沿原文件发布语义交付。V1 视图、原混组规则、旧数据库及 V1 包保持兼容。

差值包使用版本 2，与原包同样为单个 JSON 文档。顶层是 `version`、`meshes`、`products`、`manifest`。`meshes` 是依旧、新排列的两个原网格快照，快照编码与 V1 的 `mesh` 相同。同修订也占两个位置。`products` 含一个 `id`、`binding`、`geometry`、`sources` 组成的对象。`binding` 为 `{"kind":"change_band","revisions":[旧修订ID,新修订ID],"levels":[下界,上界]}`。`geometry` 的每个分量为 `[外环点数组, 洞环点数组的列表, 面积]`。`sources` 与 `geometry` 按分量一一对应，每个分量存其 `[旧面ID, 新面ID, 面积]` 行。坐标、界值和面积均沿用 V1 的 `numerator/denominator` 字符串编码。`manifest` 包含与绑定相同的 `revisions` 和 `entries`。唯一条目按 V1 的 `[成果ID, kind, levels, 摘要]` 编码，摘要是对 `terrain.archive.codec.dumps` 生成的成果对象 ASCII 字节求 SHA256。SQLite 内部布局自行选择。

沿 `load_package`、`replay_package` 及 `audit` 核账。包中的双快照都须通过结构和修订绑定验证。对结构有效的包，业务核账按两份快照检查保存的实际边界、分量划分和逐对面积。即使摘要、总面积和面名有效，错归给实际未贡献的原面对也须得到失败核账报告。保留 `report.ok`、`report.issues` 和 `report.band_areas`，其中 `band_areas` 按成果身份给出差值带面积。离线核账无需原数据库或重跑入仓请求，允许在核账阶段重新推导预期几何。`python3 -B -m terrain.delivery.replay 包路径` 沿用核账结果和失败非零退出，差值包在输出中显示有序 `revisions`。

## 手算样例与运行

`change_band_cases.py` 给出单位方形的 AC、BD 两次剖分。旧面全零，新网仅 A 高程为 1。在新面 ABD 内差值为 `1-x-y`，另一面为零。`[1/2,2)` 只有一片，外环三个拐点为 `(0,0)`、`(1/2,0)`、`(0,1/2)`。旧对角线切开这片，两个与 ABD 配对的贡献各为 `1/16`，总面积 `1/8`。`[0,1/2)` 面积 `7/8`，与 ABD 配对的两项各 `3/16`，与另一新面配对的两项各 `1/4`。另一例沿 AC 的差值为零，B 和 D 的差值为负一，`[-2,0)` 在 AC 两侧各得面积 `1/2` 的独立分量。夹具还包含同修订、点接触及空共同域。

从根目录运行 `python3 -B change_band_cases.py` 可检查夹具和 V1 行为。功能实现后，`python3 -B change_band_cases.py --exercise` 调用公开入口并演示重开、保存成果、离库读取及同总量的错来源核账。发布失败验证可参考 `tests/test_archive.py` 的 SQLite 写入失败注入，在实现的实际保存接点核对请求与成果都未留下。简述加工、保存与离线核账之间的交接不变量，补独立业务核验。更新现有 `.pointwise-runtime.json` 的 `verify` 和 `demo` 命令数组，核验包含原回归和新链，演示实际换剖分成果的离库交付。沿用现有 `version`、`kind`、`setup` 约定。
