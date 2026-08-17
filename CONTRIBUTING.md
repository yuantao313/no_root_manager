# 贡献指南

## 工作流程：Issue → PR → CI → 合入

所有功能/修复必须走以下流程，**禁止直接 push 到 main**：

```
1. 创建 Issue（描述需求/缺陷，含验收标准）
      ↓
2. 从 main 拉取功能分支：git checkout -b feat/<描述>（或 fix/<描述>）
      ↓
3. 实现改动，本地验证：
      uv run pytest          # 测试必须全部通过
      uv run ruff check .    # 静态检查零告警
      uv run ruff format --check .
      uv run python manage.py check
      uv run python manage.py makemigrations --check --dry-run  # 迁移一致
      ↓
4. 推送分支并创建 PR（标题/描述中关联 Issue，如 "Closes #123"）
      ↓
5. CI（GitHub Actions）在 PR 上自动运行，必须全部通过
      ↓
6. 管理员审查后 squash 合入（保持 main 历史干净：一个 PR 一条提交）
```

## 分支命名约定

| 类型 | 前缀 | 示例 |
|------|------|------|
| 新功能 | `feat/` | `feat/user-equal-login` |
| 缺陷修复 | `fix/` | `fix/email-backend` |
| 文档 | `docs/` | `docs/quickstart` |
| 重构/清理 | `chore/` | `chore/remove-dead-code` |

## 提交信息规范

- 首行用 Conventional Commits：`type: 简短描述`（type: feat/fix/docs/chore/refactor）
- 正文说明改动内容与动机
- 测试类改动标注测试结果

## 代码规范

- 新增代码需配套 pytest 用例（关键路径必须覆盖）
- 不引入未使用的导入/变量（ruff 零告警）
- 敏感信息（密钥/密码）严禁硬编码与入库，使用环境变量
- 保持"不过度设计"：不做需求外的扩展
- 验证必须使用 pytest 隔离数据库和 mock SSH/邮件，禁止写入开发数据库
