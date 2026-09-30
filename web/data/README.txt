生产数据目录（服务器上为 /home/linux/bingjing/web/data/）

部署规则：
- 同步脚本只上传 app 代码，不上传、不覆盖 studio.db 与 projects/
- 部署后只执行 migrate_schema_only()（建表、ADD COLUMN）
- 剧本、分镜图、Drama10、任务记录等业务数据仅在制作台内写入，不由部署修改

本地可保留 ep01.json 供首次 seed 使用；不会自动推到服务器。
