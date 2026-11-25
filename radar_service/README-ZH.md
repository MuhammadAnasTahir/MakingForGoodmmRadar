# RadarSense 完整恢复版

## 恢复与启动
1. 将压缩包解压到 D:\download，确保得到 D:\download\radar_service\service.py 和同层的 config.json、web 文件夹。不要从 ZIP 内运行。
2. 已烧录的固件仍在雷达上，不会因电脑文件夹丢失而消失。关闭 Visualizer、UniFlash 和旧后台；S3 OFF，必要时复位。
3. 双击 Start-Radar.cmd。它使用原 Visualizer 的 Python 3.9 虚拟环境，不执行受限制的 PowerShell 脚本。
4. 双击 Open-Website.cmd，或浏览器访问 http://127.0.0.1:8765/。启动窗口保持打开，Ctrl+C 停止。
5. 确认在线、帧号增加，现场有人时查看位置。若端口被占用，先关闭旧后台。

## 配置
config.json 默认 Toolbox 位于 D:/download/radar_toolbox_4_30_00_06，CLI COM3、DATA COM4、3D People Tracking 和 AOP_6m_default.cfg。不同电脑应在设备管理器确认 Enhanced=CLI、Standard=DATA。
雷达物理安装使用上次已校正的方向、1.6 米高、下倾 15 度。启动器打印 CFG 的 sensorPosition；必须核对为 sensorPosition 1.6 0 15。它不会擅自改动 Toolbox 文件。
config.json 的 expected_sensor_position [1.6, 0, 15]（高度、水平偏转、下倾角）由启动器与 CFG 比对，不一致时打印中文警告。
若 Toolbox 已移动，改 config.json 的 toolbox_root 和 cfg_path，并编辑各 .cmd 的 Python 路径；Windows JSON 路径请使用正斜杠 /。

## Python 环境丢失时
本包包含网站与后台源码，但不包含 Python、TI Toolbox、固件或巨大虚拟环境。优先使用原来仍存在的 Visualizer .venv。
如果该环境已丢失，使用已安装的 64 位 Python 3.9，在本文件夹打开 PowerShell，执行：

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe bootstrap.py

需要联网下载依赖。C 盘空间不足时，先在 D 盘创建 pip-temp，再将当前会话 TEMP 和 TMP 指向它。Python 路径变化后可直接用完整路径运行 bootstrap.py。
TI parser 来自已解压 Toolbox/common，不能删掉该目录。若导入还报告缺少模块，请保留完整错误输出再补装，不要改为假的演示数据。

## 功能
- 真实串口采集、TI parser、网页实时目标与真实点云。
- 夜间深色页面；等比例相对 XY 网格，默认 X -2～2、Y 0～4 米。无具体房间标定，不显示为精确房间布局。
- 右上角“中文 | EN”切换网页语言，选择保存在浏览器；也可直接打开 http://127.0.0.1:8765/?lang=en 。所有文字在 web/i18n.js，修改措辞时中英文两边都要改（tests/test_frontend_i18n.cjs 会检查）。
- 外观与显示范围可在网页设置，浏览器本地保存；颜色和字体在 web/theme.css，样式在 web/styles.css，源文件可直接编辑，刷新即可，不需要构建。
- 区域 ROI 和排除矩形保存到 filters.json。默认关闭，避免原 Y≤2 米屏蔽 Y≈2.1 米处的躺下目标。显示范围不等于后台过滤范围。
- 保留旧运动过滤：30 秒未偏离初始位置 0.25 米的目标会隐藏；不是人/家具识别，静坐者也可能被隐藏。config.json 的 motion_filter.enabled 可关闭。
- 跌倒规则（config.json/fall）：“疑似跌倒”需同时满足 ①下降 ≥ min_drop_m(0.6 米) 且低位 ≤ max_low_height_m(0.7 米)；②10%→90% 下降用时 ≤ fall_max_duration_s(1.2 秒) 且峰值下降速度 ≥ fall_min_speed_mps(1.0 米/秒，按 0.2 秒区间计算)；③confirm_seconds(1 秒) 后仍在低位；④不在 safe_zones 安全区（如床，格式 [[xmin,xmax,ymin,ymax]]）。
- 慢慢躺下记为灰色“主动躺下 · 已排除”（不报警、不响铃）；1 秒内站起记为“下蹲/弯腰 · 已排除”；慢慢滑倒后在安全区外低位 floor_alert_s(30 秒) 报“长时间低位”。报警记录带 kind 字段，旧数据库自动迁移，旧记录视为 fall。冷却 30 秒保持不变。
- 同一低位事件只报警一次；恢复到参考高度 85% 并稳定 2 秒才重新允许预警。阈值在 config.json/fall 中。
- SQLite 报警存档、人工确认已查看、Windows 本地提示音、断线重试、过期数据提示。
- Record-60s.cmd：后台保持运行，另开记录窗口，输入动作标签，保存 60 秒状态与前后事件快照，自动压缩到 recordings。
- Backup.cmd：将代码、设置、记录和 SQLite 一致性备份保存为上一级目录中的 ZIP。请将备份另存一处。

## 边界
躺下/跌倒区分基于高度下降幅度、速度和时长的启发式规则，只用合成数据测试过，必须在现场用真人动作验证和调参；在安全区外的地上躺超过 30 秒会报“长时间低位”。不能承诺排除所有家具反射。高度不是头部高度，目标 ID 不是身份。ID 更换、服务重启或重连可能中断单次事件锁定；不保证跨 ID 去重。心率、呼吸和姿态分类未接入。
本包没有丢失目录中的历史数据库、日志或自定义外观；这些内容无法凭代码重建。原数据如找回，可单独恢复，但不要覆盖新代码。
本机服务仅监听 127.0.0.1。需要其他电脑访问时应另做网络接入配置。

## 测试
包内 tests 可通过 python -m unittest discover -s tests -v、node tests/test_frontend.cjs、node tests/test_frontend_kinds.cjs 和 node tests/test_frontend_i18n.cjs 执行。离线测试通过不等于新包已在实验室雷达上完成验证，安装后仍需检查串口和现场动作。
