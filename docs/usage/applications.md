# 用户申请

## 申请流程

1. 访问 `/applications/new/`（**无需登录**）
2. 填写身份信息：
   - **姓名**：必填
   - **用户名**：必填，可调用用户名建议接口自动生成候选（见下文）
   - **邮箱**：必填，用于接收开通密码与审批结果
   - **工号**：可选
3. 选择申请类型、目标服务器、使用截止时间
4. 可选勾选：
   - **申请 root/sudo 权限**：当天有效，次日自动失效（需重新申请）
   - **迁移已有目录**：将 `/home/old/username` 迁移到 `/home/username`
5. 提交后：管理员收到通知 → 审批 → 系统自动开通账号

## 用户名建议接口

`GET /accounts/api/username-suggestions/?name=张三丰`

根据姓名生成候选用户名（支持复姓与多音字排列组合）：

```json
{
  "name": "诸葛孔明",
  "is_compound_surname": true,
  "single_surname": ["zhugekongming", "gekongmingzhu", "zhugkm", "gkmzhu"],
  "compound_surname": ["zhugekongming", "kongmingzhuge", "zhugekm", "kmzhuge"],
  "suggestions": ["zhugekongming", "gekongmingzhu", "..."]
}
```

## 审批（管理员）

- 申请列表：`/applications/list/`（仅管理员可见）
- 详情页可查看申请人信息、目标服务器、使用截止时间、sudo 审计记录
- 操作：通过 / 驳回，可填审批意见
- **自动审批**：服务器开启了"自动审批"后，针对该服务器的申请提交即自动通过并开通，无需人工干预

## 开通结果

审批通过后自动执行：

1. 目标机器创建普通用户（`useradd`，默认加入 `nrm_managed` 组）
2. 生成 16 位随机密码并邮件发送
3. 强制首次登录修改密码（`chage -d 0`）
4. 写入资源限制（limits.d）
5. 匿名申请者自动获得系统登录账户（密码即首次开通密码）
