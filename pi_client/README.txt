DripLink 树莓派定时拍照上传（Python 3.9+，仅标准库）
====================================================

范围与验收状态
--------------
本目录只负责 JPEG 拍照、落盘排队、鉴权上传，不加载或改动核心识别模型。
树莓派当前未连接：只运行了 Windows 上的离线 mock 单元测试，不能宣称
相机、树莓派硬件、真实网络或 systemd 已实测。本次没有安装/启动服务、
没有真实拍照、没有向服务器发送请求。以下部署命令由操作者日后在 Pi 执行。

1. 服务端和设备身份
-------------------
服务端接口：POST http://SERVER:5001/api/device/upload（鉴权端口 5001）。
multipart/form-data 字段：
  file         JPEG 文件（Content-Type: image/jpeg），不接受 PNG 等其他格式
  meter_id     该实体水表的标识；每只实体水表设置自己的 meter_id
  captured_at  RFC3339 UTC 时间，带 +00:00 offset，例如 2026-11-14T12:00:00+00:00
  upload_id    UUID，同一排队照片及其所有重试保持同一个 ID
请求头：Authorization: Bearer <DRIPLINK_DEVICE_TOKEN>。

Token 不是这里在网页上生成的。本项目后端 webapp/run.py 启动时会读取或
创建 webapp/instance/device_token.txt；请在服务端本机安全读取该文件取值，
再在树莓派本机配置环境文件。若该文件尚不存在，先由后端管理员正常启动
webapp/run.py 并确认配置；不要在客户端另造一个不匹配的 Token。不要把
真实 Token 上传到聊天、截图、工单、Git 或日志。客户端不提供 Token 命令行参数。
如后端使用部署环境覆盖 Token，以后端实际配置为准。

局域网调试明确允许 http://SERVER:5001，但 HTTP 不加密 Token 和照片。
生产环境使用 HTTPS 或受控的 WireGuard 隧道，并限制端口访问；不要直接
把明文 5001 暴露互联网。HTTPS 使用 Python 默认 CA 证书校验，不绕过 TLS。
客户端拒绝 URL 内嵌用户名/密码和查询串，不跟随重定向，防止转发凭据。

2. 树莓派准备和一次测试
----------------------
将项目放在 /home/pi/DripLink-main，已有 Python 3 即可，无需 pip 依赖。
当前 service 模板固定 User=pi；如果 Pi 用户名或路径不同，请部署前修改
服务副本中的 User、WorkingDirectory 和 ExecStart。
相机使用 shutil.which 优先选择新 Pi OS 的 rpicam-still，其次旧系统的
libcamera-still；执行 argv，无 shell，超时 45 秒：
  rpicam-still --nopreview --timeout 2000 --output <outbox-entry>/image.jpg
程序捕获相机输出但不打印，避免泄露环境；错误时检查相机安装、连接和权限。

用 sudoedit /etc/driplink-camera.env 在 Pi 本机写入（替换占位符）：
  DRIPLINK_SERVER_URL=http://SERVER:5001
  DRIPLINK_METER_ID=meter-001
  DRIPLINK_DEVICE_TOKEN=在本机填入服务端Token
保护该文件：
  sudo chown root:root /etc/driplink-camera.env
  sudo chmod 600 /etc/driplink-camera.env
环境文件不放项目内，不提交版本控制。文件内容是 KEY=value，不带 export。
不要用含真实 Token 的 echo/命令参数赋值，避免进 shell 历史。

首次可先用已有 JPEG 测试，不触发相机。交互式 shell 须另外从受保护的
配置安全加载上述环境，或隐藏输入 Token：
  export DRIPLINK_SERVER_URL=http://SERVER:5001
  export DRIPLINK_METER_ID=meter-001
  read -r -s -p 'Device token: ' DRIPLINK_DEVICE_TOKEN; printf '\n'
  export DRIPLINK_DEVICE_TOKEN
  python3 -B /home/pi/DripLink-main/pi_client/uploader.py --once --image /path/to/test.jpg
  unset DRIPLINK_DEVICE_TOKEN
不要使用 env（无参数）、set -x 或打印环境的诊断命令。
--image 复制照片到队列，不移动原文件，也不从 EXIF 推断时间；测试照片
captured_at 记录入队时的 UTC 时间。真实拍摄记录拍摄完成时间，系统时钟
应先校准。JPEG 做起止标记与大小检查，不声称完成了图像解码验证。

不带 --image 时才拍照；--once 是默认行为，--interval 与 --once 互斥：
  python3 -B pi_client/uploader.py --once
  python3 -B pi_client/uploader.py --interval 3600
--interval 最低 60 秒，表示每轮完成后等待的秒数，不是精确钟点调度。
Ctrl+C 返回 130 干净退出，已完成落盘的队列保留。推荐使用下面的 timer，
不要同时运行多个客户端进程或同时启动 timer 和 --interval。

3. 安装 systemd（仅提供命令，本次没有执行）
-----------------------------------------
在树莓派上检查模板与日历规则：
  sudo systemd-analyze verify pi_client/driplink-camera.service pi_client/driplink-camera.timer
  systemd-analyze calendar hourly
安装并启用：
  sudo install -m 644 pi_client/driplink-camera.service /etc/systemd/system/
  sudo install -m 644 pi_client/driplink-camera.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now driplink-camera.timer
查看/停止：
  systemctl list-timers driplink-camera.timer
  systemctl status driplink-camera.service
  journalctl -u driplink-camera.service
  sudo systemctl disable --now driplink-camera.timer

service 使用指定的 pi 用户、目录、EnvironmentFile 和 --once 命令。
timer 设置 OnBootSec=2min、OnCalendar=hourly、RandomizedDelaySec=30、
Persistent=true。根据 systemd.timer 官方规则，Persistent 只对 OnCalendar
生效，因此不用 OnUnitActiveSec=1h 假装持久调度。关机错过的日历触发恢复
后补触发一次，不逐小时补拍历史照片；补触发可能早于开机两分钟，并有随机
延迟。小时调度为整点，不是首次激活后固定 3600 秒；还受 systemd 默认
AccuracySec 的合并窗口影响。规则参考：
https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html
此处只核对了官方规则，Windows 环境未实际运行 systemd-analyze。

4. 持久队列、失败处理和人工恢复
------------------------------
默认 outbox：~/.local/share/driplink/outbox（以运行用户 home 为准，User=pi
时通常是 /home/pi/.local/share/driplink/outbox）。可用 --outbox 指定其他目录。
每条是 UUID 目录，包含 image.jpg、metadata.json；元数据只含版本、UUID、
meter_id、UTC captured_at、队列状态/HTTP 错误码或 local_corrupt 标记，不含 Token 或服务器响应。
元数据先写临时文件并 fsync 后原子替换；掉电留下的残缺目录不自动删除，
需人工排查，且计入容量。照片是敏感数据，请保护磁盘/备份和访问权限。

每轮先按时间重传旧队列，再拍摄/入队新照片。网络不可达、超时、5xx、
408、429 单条最多尝试 3 次（含首次，退避 1、2 秒），每次 HTTP 超时 45 秒。
旧队列耗尽重试仍失败时，本轮新照片仅入队，下轮再按先旧后新重传。
不要删除元数据后重新提交同一图片，这会改变 upload_id，破坏幂等去重。

其他 4xx（尤其 400/401/403/404/413/422）视为永久错误：标为 blocked，
保留 JPEG 和元数据供人工检查，以后不再自动重试。401/403 会暂停后续
拍摄/新入队，防止凭据失效时无限积压；修正 Token 后仍需人工恢复该条
状态。3xx 不跟随，同样 blocked。错误日志只输出固定说明及 HTTP 码，
不会输出 Token、请求头、异常详情或服务器原始响应。
缺失照片、损坏 JPEG 或超限旧照片逐条标为 blocked，error=local_corrupt，
保留现有文件并继续正常队列和新拍摄；若目录不可写无法保存标记，也只输出
固定提示并继续，不让单条损坏记录卡住后续上传。人工修复后方可重新入队。

恢复步骤（先停 timer/循环进程）：核查 meter_id/端口/鉴权/文件格式，备份
该条；在 metadata.json 中仅把 state 从 blocked 改为 queued，移除可选
http_status，保持 upload_id、captured_at、meter_id 和照片不变，再运行
一次 --once（将先补传旧队列，随后拍摄一张，或用 --image 使用测试照片）。
若旧队列中的实体归属有误，不要擅自改 meter_id；先人工确认处置。
确定不再需要的损坏/拒收队列可由操作者手动删除对应整个 UUID 目录。

最多保留 100 条目录（含 blocked/残缺），达到上限拒绝新拍摄而不静默
丢弃旧记录。每张入队 JPEG 上限 9 MiB，为网站请求总上限 10 MiB 留出
multipart 余量；100 张最多约 900 MiB，另有少量元数据和临时拍摄空间。
这不是磁盘配额管理，仍应监控剩余空间。

仅 HTTP 2xx 且返回有效 JSON 对象、有效 record_id 或 record.id，以及
status=pending/confirmed/sample 之一时，才打印记录 ID/status 并删除队列。
ID 须为正整数或非空、无控制字符且至多 200 字符的字符串，不接受布尔值。
嵌套 record.status 也可接受。HTML、非对象 JSON、缺 ID/状态或非法状态
均不算成功回执，按原 upload_id 最多三次退避重试后保留队列，等待下次补传；
不会把正文或 Token 打印到日志。回执最多读取 64 KiB 加一个溢出检测字节。
返回 status=pending 表示等待人工审核，不表示已审核、自动计费或结算。
旧版八点 LabelMe 标注与新版角点格式不同；客户端不做标注转换或识别。

5. 离线测试
-----------
项目根目录用现有 Python 执行，不装新环境、不写 .pyc：
  python -B -m unittest discover -s tests -p test_pi_uploader.py -v
测试 mock 相机、HTTP、重试等待；所有数据写入 TemporaryDirectory，绝不
真实发网或拍照。覆盖环境校验、相机 argv/超时、JPEG、UUID/UTC/落盘无
Token、multipart/鉴权、pending、同 ID 重试、重传顺序、4xx/5xx 分类、
401 停拍、100 条上限、日志反射 Token 脱敏、Ctrl+C 和拒绝重定向；另覆盖
超过 9 MiB 拒绝入队、HTTP 200 HTML、缺失/非法 ID 或状态时重试并保留队列。
