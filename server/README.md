# 服务器部署说明

采集器运行在 Vultr 东京服务器上，与量化交易程序隔离：

- 独立的 Linux 用户 `collector`：无 sudo、不在 docker 组，读不到交易程序的文件
- 代码位于 `/home/collector/tokenized-stock-data`
- 用只对本仓库有写权限的 GitHub 部署密钥推送数据（`~/.ssh/github_deploy`）
- `collector` 用户的 crontab：

  ```
  */10 * * * * flock -n /tmp/tokenprice-collector.lock /home/collector/tokenized-stock-data/server/run.sh >> /home/collector/collect.log 2>&1
  ```

常用命令（以 root 登录后）：

```bash
tail -n 30 /home/collector/collect.log      # 看最近的采集日志
crontab -u collector -l                     # 查看定时任务
crontab -u collector -r                     # 停止采集（删除定时任务）
```
