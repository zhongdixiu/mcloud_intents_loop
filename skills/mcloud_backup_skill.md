---
id: mcloud_backup_skill
name: 存储与备份管理
description: 承接相册、应用、手机、微信和通讯录备份及云换机；不承接单文件上传、回收站恢复或普通备份咨询。
version: '1.0'
scope:
- 相册备份
- 应用备份
- 手机备份
- 云换机
- 微信备份
- 通讯录备份
out_of_scope:
- 上传单个文件
- 恢复回收站文件
- 备份知识咨询
aliases:
- 备份工具
- 手机备份
status: active
---

# 存储与备份管理 Skill

## Skill Scope

本 Skill 负责：

- 相册备份；
- 应用备份；
- 手机备份；
- 云换机；
- 微信备份；
- 通讯录备份；

本 Skill 不负责：

- 上传单个文件；
- 恢复回收站文件；
- 备份知识咨询；

## Intent Routing Principles

1. 根据用户当前主动作选择 Intent，参数缺失不影响 Route 判断。
2. 专用 Intent 优先于通用入口；同 code 的不同 Intent 仍按完整 Route Key 区分。
3. 当前 Skill 不支持请求时允许返回空候选，不强制选择相近 Intent。
4. 二级阶段保留现有参数输出兼容，但不得因参数不全降低正确 Route 的优先级。

现有业务规则：

无额外专用规则。

## Intent Contrast Rules

### 云盘备份 vs 文件上传（跨 Skill）

- 对相册、通讯录、微信文件等数据类别进行持续或批量备份时选择本 Skill。
- 上传某个已有文件时选择文件管理中的“文件上传”。

## Tools Schema

```json
{
  "相册备份": {
    "code": "021",
    "desc": "将手机相册中的照片和视频备份至云存储",
    "params": {}
  },
  "应用备份": {
    "code": "021",
    "desc": "备份手机应用程序及数据至云端",
    "params": {}
  },
  "手机备份": {
    "code": "021",
    "desc": "完整备份手机系统数据和应用配置",
    "params": {}
  },
  "云换机": {
    "code": "021",
    "desc": "通过云端数据迁移实现新旧手机一键换机",
    "params": {}
  },
  "微信备份": {
    "code": "021",
    "desc": "备份微信聊天记录及文件至云端",
    "params": {}
  },
  "通讯录备份": {
    "code": "021",
    "desc": "备份手机联系人信息至云端存储",
    "params": {}
  }
}
```

## Intent-Specific Rules

- 每个 Intent 的适用范围以 Tools Schema 的 `desc` 和上述对比规则为准。
- 只抽取用户当前输入或上下文中明确存在的参数，不猜测实体或真实资源句柄。
- 参数缺失不改变已经确定的 Intent；同 code Intent 必须根据名称语义区分。

## Positive Examples

- “备份手机通讯录” → 通讯录备份
- “开启相册自动备份” → 相册备份

## Negative Examples

- “上传这张照片” → 文件管理
- “怎么备份手机” → 普通问答

## Execution Instructions

- 最终 Route 确定后，按照该 Intent 的参数 Schema 做类型、数组元素和枚举校验。
- 未明确提供的可选参数不阻塞路由；不得伪造文件、图片、邮件等业务句柄。
- 涉及删除、覆盖、外发或权限变更时，由执行阶段完成对象确认和风险确认。
