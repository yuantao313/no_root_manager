# NRM · No Root Manager

NRM 为中小团队提供服务器账号申请、审批和 SSH 开通闭环，替代共享 root 凭据和口头授权。

## 产品定位

- 普通用户提交账号、接管或权限组申请。
- 普通管理员只审批已绑定服务器的普通申请。
- 超级管理员维护服务器、凭据和系统配置，并审批 sudo/docker 等 root 等价权限。
- 系统通过已核验主机指纹的 SSH 连接执行机器操作，并记录结果。

NRM 不是监控平台、CMDB、批量配置中心或堡垒机，也不包含 NPU/GPU、资源配额、自动到期撤权和移动端能力。

## 文档导航

- [快速开始](quickstart.md)
- [使用指南](usage/index.md)
- [部署指南](deploy.md)
- [架构设计](architecture.md)
- [运维手册](operations.md)
