# Nonebot Plugin Suda Electricity

[![AGPL-3.0 license](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](https://github.com/hlfzsi/nonebot-plugin-suda-electricity/blob/main/LICENSE)

一个适用于 [Nonebot2](https://github.com/nonebot/nonebot2) 的苏州大学宿舍电费查询插件。

## 功能介绍

- **电费查询**：随时查询宿舍电费余额。
- **用户绑定**：绑定学号和密码，免去重复输入的麻烦。
- **低余额提醒**：可自定义电费余额阈值，当低于该值时，机器人会自动发送提醒。
- **多账号支持**：支持多个用户绑定不同的宿舍。
- **灵活订阅**：可以将提醒消息订阅到私聊或群聊。

## 安装说明

通过 `pip` 或 `nb-cli` 安装本插件：

```bash
pip install nonebot-plugin-suda-electricity
```

或者

```bash
nb-cli plugin install nonebot_plugin_suda_electricity
```

## 使用方法


| 命令                      | 别名      | 功能                       | 示例                                   |
| :------------------------ | :-------- | :------------------------- | :------------------------------------- |
| `/sd login <学号> <密码>` |           | 绑定您的学号和密码         | `/sd login your_account your_password` |
| `/sd check`               |           | 查询已绑定宿舍的电费       | `/sd check`                            |
| `/sd subscribe`           | `/sd sub` | 将低电量提醒订阅到当前聊天 | `/sd subscribe`                        |
| `/sd threshold <金额>`    |           | 设置电费提醒阈值           | `/sd threshold 20`                     |
| `/sd logout`              |           | 解除当前用户的绑定         | `/sd logout`                           |

**注意**：`/sd login` 命令建议在与机器人私聊时使用，以避免密码泄露。

## 配置项

本插件支持以下配置项：

| 配置项 | 是否必填 | 默认值 | 说明 |
| :-- | :-- | :-- | :-- |
| `suda_secret_key` | 是 | 无 | 用于加密本地存储的敏感信息，请使用高强度且仅自己掌握的字符串。 |
| `database_url` | 否 | 本地 SQLite 文件 | 数据库连接地址。 |
| `scheduler_interval_hours` | 否 | `8` | 每个宿舍的固定检查间隔（小时）。 |
| `scheduler_tick_seconds` | 否 | `60` | 调度器扫描到期任务的周期（秒）。 |
| `scheduler_due_limit` | 否 | `10` | 单次扫描最多处理的到期宿舍数量。 |

示例（`.env`）：

```env
SUDA_SECRET_KEY=change-this-to-a-strong-random-secret
```

## 重要声明

### 部署环境

苏州大学统一认证服务**强制要求在校园网环境下访问**。因此，您需要将机器人部署在校内服务器上，或者使用 VPN 等方式接入校园网环境，否则插件将无法正常工作。

### 账密存储

由于统一认证 `code` 的有效性周期尚不明确，本插件必须使用账密进行登录，且请求过程中仍需按学校认证要求提交明文账密。插件目前已对本地存储的敏感信息进行加密处理，用于降低**仅数据库文件单独泄漏**时的直接暴露风险。

请注意：该加密并非“绝对安全”。如果数据库、密钥和盐等关键材料同时泄漏，则该保护将失效。请务必做好主机、部署目录与数据库访问控制，避免未授权访问。

### 免责声明

本项目仅作为编程学习和技术研究目的使用。本项目承诺永不滥用账密。但开发者不对使用本插件可能造成的任何后果承担责任，包括但不限于账号信息泄露。对于基于本插件或受本插件启发的二次开发，本项目开发者概不负责。

本项目仅供学习交流，无意对学校系统造成压力。若校方认为不妥，请通过 [ hlfzsi@outlook.com / Issue ] 联系，我们将立即停止维护或调整访问策略。

### 开源协议

本项目采用 [AGPL-3.0](https://github.com/hlfzsi/nonebot-plugin-suda-electricity/blob/main/LICENSE) 开源协议。
