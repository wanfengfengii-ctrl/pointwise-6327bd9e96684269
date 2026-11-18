# 青岚地形成果档案

青岚地形资料组接纳外业三角网，固定用于当天复测的修订，生成等高线和分级高程带，再向合作测绘组交付可离线检查的成果。质控员可以在任何固定修订上查高程，并核对保存成果的实际几何、面积和来源。

## 运行

需要 Python 3.11 或以上版本，仅用标准库。在项目根目录执行。

```sh
python3 -B -m unittest discover -s tests -t . -v
python3 -B -m terrain.archive.api --sample samples/day_shift.json
```

样例默认在临时目录完成接纳、查询、加工、核账、导出及离线重放。给样例命令追加 `--directory 目录` 可保留 `terrain.sqlite` 和 `delivery.json`，再用 `python3 -B -m terrain.delivery.replay 目录/delivery.json` 独立核账。

## 使用接口

```python
from terrain.archive.api import build_bands, build_contours
from terrain.archive.catalog import Catalog
from terrain.delivery.audit import audit
from terrain.delivery.read import read_export
from terrain.field.query import sample
from terrain.mesh.revisions import put_mesh

with Catalog('terrain.sqlite') as catalog:
    revision = put_mesh(catalog.meshes, 'survey-1',
                        [('a', 0, 0, 1), ('b', 1, 0, 1), ('c', 0, 1, 1)],
                        [('face-1', 'a', 'b', 'c')])
    point = sample(catalog.meshes, revision.revision_id, '1/4', '1/4')
    contour = build_contours(catalog, 'line-1', revision.revision_id, [1])
    band = build_bands(catalog, 'band-1', revision.revision_id, 1, 2)
    view = read_export(catalog, [contour.product_id, band.product_id])
    report = audit(view)
    if report.ok:
        read_export(catalog, ['line-1', 'band-1'], 'delivery.json')
```

`put_mesh` 的顶点行是 `(id,x,y,h)`，面行是 `(id,v0,v1,v2)`。标识必须是内容非空、无首尾空白的可打印字符串。数值接受整数、`Fraction` 或形如 `-3/7` 的有理数字符串。布尔、浮点、空值、退化面、重叠以及不共形接合均拒绝。公共边或公共顶点的原身份必须一致。坐标、高程及面积单位分别为米、米和平方米。空网格是合法输入。`sample` 返回 `inside` 或 `outside`，命中时携带精确高程、全部命中面及面内位置分类。

`build_contours` 接收单个阈值或有限阈值序列，按规范值去重和排序。每个结果有精确等值身份、带来源的无向弧及其端点关联，孤立接触记录在节点中。`build_bands` 接收 `lo < hi`，返回半开区间 `[lo,hi)` 的分量、外环、孔洞和面积账。外环逆时针，孔洞顺时针。每个逐面单元以闭环表示几何，并记录顶点是否属于区间。单元边的开段在两端均被排除时排除。零宽上界缝由这些包含标记保存，区域面积环只表示各分量的面载体。仅点接触不合并开内部。

加工请求身份同时作为成果身份。同请求同绑定返回保存数据，内容或绑定变更须使用新的请求身份。一次 `build_*` 调用原子保存一个完整成果。`read_export` 接受一个成果身份或同修订的一组成果身份，返回固定交付视图，给定目的路径时同时发布自足包。`audit` 返回带业务偏差的报告，不写回档案。业务输入和身份错误抛出明确异常，SQLite 发布失败整体回滚，导出失败保留已有目的文件。重开目录和源数据更正均不会覆盖旧修订或成果。
