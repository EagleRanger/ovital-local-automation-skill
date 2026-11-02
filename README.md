# 奥维地图后台自动化 Skill

通过奥维互动地图的官方 Windows 消息接口，在不接管鼠标键盘的情况下处理本地收藏夹与 GIS 几何。附带实际 Python 执行代码，不是只列功能的提示词，也不依赖作者电脑上的私人项目。

当前版本：**v1.0.0**。

[下载 Skill ZIP](https://github.com/EagleRanger/ovital-local-automation-skill/releases/latest) · [AI 使用入口](skill/SKILL.md) · [配置与调用](skill/references/receiver-setup.md) · [GIS 自动化分区](https://github.com/EagleRanger/windows-app-automation-skills#gis-自动化)

## 能做什么

- 奥维后台会话连接，读取完整目录、对象、父路径、轨迹/图形几何及样式；在明确授权范围内创建、修改、移动与分类。
- 解析、导出 KML/KMZ，保持坐标和孔洞；处理线面转换、成对道路边界封面、米制外扩、合并、裁切、差集和工程量核验。
- 获取当前地图参数与指定范围的已有缓存影像，辅助坐标叠图；不默认移动前台地图或发起地图下载。
- 保留源对象，按明确 ID 和覆盖范围进行局部更新；分开核 API 回执、保存和独立读回。

普通几何计算使用 Shapely/pyproj，不要求 ArcGIS Pro、ArcPy 或其许可。ArcGIS 的软件控制由 [独立 ArcGIS 项目](https://github.com/EagleRanger/arcgis-pro-local-automation-skill) 负责，本项目不重复安装它。

## 迁移给其他 AI

下载 ZIP，交给有本地文件/命令能力的 AI，先读 `skill/SKILL.md`，安装 `skill/requirements.txt` 所列公开依赖，再按本机奥维版本及权限对接。安装时只核环境与离线样例，不对真实收藏夹进行测试写入。用户自己安装软件并登录。

直接将包内 `skill/` 目录作为独立 Skill 安装；无需把 ArcGIS Skill 嵌进其中。依赖不捆绑，真实 KML、卫星图、收藏夹数据库、账号及作者工程状态不随包迁移。

## 已排过的坑

先区分真实设施边缘与另行外扩距离；左右道路边线不是重复线；碎线不能首尾强闭合；部分新版不能覆盖全部旧稿；地图配准坐标不等于地面精确米；合并净面积与分项毛量须区分。更多适用条件见 [几何处理与经验](skill/references/geometry-lessons.md)。

## 验证边界

本次以合成数据验证几何、KML往返、协议结构、参数和受控写入边界。源项目有奥维后台读写、保存和独立读回的使用记录；这不等于本发布包在所有奥维版本/新电脑实写通过。官方地图数据接口当前标明需 SVIP；实际权限以软件和官方说明为准。部分保存入口依赖当前版本的无障碍实现，必须单独核验，不以普通配置保存代替收藏夹持久化。

## 来源与支持

接口依据 [奥维官方第三方协作接口](https://www.ovital.com/openapi/)，Windows 消息后台适配及工作流由既有项目代码整理；几何库各自按上游许可使用。详见 [NOTICE.md](NOTICE.md)。需求与反馈：EagleRanger；代码整理、迁移与验证协助：OpenAI Codex。

如实际下载、使用或借鉴后对开发有帮助，欢迎自愿给本项目 Star，方便更多同类需求找到它。
