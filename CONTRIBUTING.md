# 参与贡献

感谢你愿意改进这个项目。

提交代码前，请先创建 Issue 描述问题或改动目标。修复小错误可以直接提交 Pull Request。

本地检查：

```powershell
.\install.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

提交时请做到：

- 不提交 `.venv`、数据库、日志或真实员工人脸
- 新功能同时补充测试和文档
- 不把识别距离描述成准确率或百分比
- 涉及数据库结构时提供向前迁移和升级前备份
- 涉及安全能力时明确说明能防什么、不能防什么
