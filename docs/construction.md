# 完整流程的原项目文档

完整项目源码与所需子模块源码已包含在仓库内。构建流程和参数以原项目文档为准：

- [Belta 项目说明](../belta/PROJECT.md)：仓库发现、候选版本对、环境构建和旧实现回插任务。
- [Belta 设计与实现约定](../belta/DESIGN.md)：各阶段命令、配置、数据与验证口径。
- [FeatureFactory 项目说明](../FeatureFactory/README.md)：环境构建及 SDK 集成。
- [Harbor Belta adapter](../harbor/adapters/belta/README.md)：导出、Oracle、Agent 评测和消融。

当前这批评测直接使用已经冻结的 Full200、Lite40 及消融数据和镜像。
运行方式见 [原 Harbor 运行命令](evaluation.md)。
