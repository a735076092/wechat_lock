# 微信空闲锁定器 (WeChat Idle Locker)

一个 Windows 桌面小工具：微信长时间无人操作时自动锁定，防止他人偷看聊天记录。

## 功能特性

- **空闲自动锁定**：微信超过设定时间（默认 5 分钟，可设 1~120 分钟）没有操作，自动锁定
- **全局快捷键**：`Ctrl + L` 随时手动立即锁定
- **密码解锁**：锁定后微信窗口隐藏，弹出密码锁屏窗，输入正确密码才能恢复
- **防绕过**：
  - 锁定期间微信窗口持续隐藏，无法通过托盘图标 / 任务栏打开
  - 双击托盘微信图标时，自动弹出锁屏窗要求输入密码（含最小化自动还原 + 任务栏闪烁提醒）
  - 锁屏窗不可关闭、不可跳过
- **托盘常驻**：最小化到系统托盘后台运行，不占任务栏
- **窗口体验**：锁屏窗为普通窗口，可拖动、可最小化
- **自动解锁兜底**：微信进程完全退出后锁屏自动解除，不会卡死

## 使用方法

1. 双击运行 `微信空闲锁定器.exe`，桌面右下角出现锁形托盘图标
2. 双击托盘图标打开设置，修改空闲时间 / 解锁密码（默认密码 `123456`）
3. 平时无需干预，程序自动监测微信空闲状态

## 环境要求

- Windows 10 / 11
- 微信 3.x（WeChat.exe）或微信 4.x（Weixin.exe）
- Python 3.8+（仅源码运行 / 自行打包时需要）

## 从源码运行

```bash
pip install pywin32 pystray pillow
python wechat_lock.py
```

## 自行打包 exe

```bash
pip install pyinstaller
python make_icon.py                # 生成 icon.ico（首次）
pyinstaller --onefile --noconsole --name 微信空闲锁定器 --icon icon.ico wechat_lock.py
```

## 工作原理

| 模块 | 说明 |
|------|------|
| 空闲检测 | `GetLastInputInfo` 获取系统最后输入时间，微信前台且有输入才刷新活跃时间 |
| 窗口定位 | `EnumWindows` + `QueryFullProcessImageNameW` 按进程名定位微信主窗口（兼容隐藏态） |
| 进程兜底 | `CreateToolhelp32Snapshot` 进程快照检测微信运行状态（微信 4.0 关托盘会销毁窗口） |
| 锁定/解锁 | `ShowWindow(SW_HIDE/SW_SHOW)` 隐藏与恢复微信窗口 |
| 全局热键 | `RegisterHotKey` 注册 Ctrl+L，独立线程消息循环 |
| 线程安全 | 后台监控线程通过 UI 事件队列调度 tkinter 操作，避免跨线程崩溃 |

## 项目结构

```
WeChatLock/
├── wechat_lock.py       # 主程序（单文件实现）
├── make_icon.py         # 程序图标生成脚本
├── icon.ico             # 程序图标
├── smoke_test.py        # 冒烟测试（窗口检测/热键/配置）
├── integration_test.py  # 集成测试（锁定-解锁完整链路）
└── .gitignore
```

## 注意事项

- 程序需常驻后台运行才有效；彻底退出请右键托盘图标 → 退出
- 本工具仅隐藏微信窗口，不读写、不修改微信任何数据文件
- 配置文件 `wechat_lock_config.json` 生成在 exe 同目录

## License

MIT
