# CAXA 高级搜索抓包与 Part→Product 映射

## 捕获

- 时间：2026-11-26 09:39:22–09:40:49
- ETL：`work/caxa_product_search_capture_20261126_run3.etl`
- pcapng：`work/caxa_product_search_capture_20261126_run3.pcapng`
- 双向重组：`work/caxa_product_search_capture_20261126_run3_frames.jsonl`
- 筛选：TCP `192.168.1.28` 全部端口

高级搜索没有走 EAP `8900`，而是由客户端直连 SQL Server `1433`。

## 捕获到的核心 SQL

```sql
SELECT DISTINCT a.*
FROM T_Product a, T_Part b_1, T_ProductPartLink c_1
WHERE
    a.OBJ_ID = c_1.ROLEA_ID
    AND a.CLASS_ID = c_1.ROLEA_CLASS_ID
    AND c_1.Recycle_ID = 0
    AND c_1.Qualification_ID = 0
    AND a.Recycle_ID = 0
    AND b_1.OBJ_ID = c_1.ROLEB_ID
    AND b_1.CLASS_ID = c_1.ROLEB_CLASS_ID
    AND b_1.Recycle_ID = 0
    AND b_1.IdentityNo LIKE @P1
    AND a.OBJ_ID <> 0;
```

参数分别为：

- `%03.HT.HG8-100AD%`
- `%03.HT.HG025-350W%`

## SDK 可复用路径

`ProductPartLink(1036)` 元数据：

- 显示名：产品总装件关联
- Product 角色：`ProdWithTopAssembly`（包含总装的产品）
- Part 角色：`TopAssemblyInProd`（产品所属总装）

helper 已通过 CAXA `ObjectStore.NewQuery()` 构造：

1. 查询类：`Product`
2. 起始对象：`ProductClassification("HT合同特殊")`
3. 查询条件：Part `IdentityNo` 包含整机图号
4. 关系角色使用上述 A/B role
5. 返回 Product 的真实 object ID、图号、名称

`product_folder_paths` 在一次 helper 会话中只遍历一次 ProductFolder 树，
缓存所有 Product 到实际目录路径的映射。

## 现场结果

| 整机 Part 图号 | Product ID | Product 图号 | 实际目录 |
|---|---:|---|---|
| `03.HT.HG8-100AD` | 1435 | `03.HT.HG8-100AD` | `HT合同特殊` |
| `03.HT.HG025-350W` | 1520 | `03.HT.HG025-350W` | `HT合同特殊` |
| `03.HT.HGB05-05025B` | 687 | `03.HT.HGB05-05025` | `HT合同特殊/HGB系列` |
| `03.HX21.E20H-03L` | 1377 | `03.HX21.E20H-01` | `HX系列` |

集成查询四个整机约 43 秒，全部命中，且主路径不再加载 1722 个 Product
的旧全量索引。
