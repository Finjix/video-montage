# 语义分析与帧计划

本模块负责源素材 ASR、候选语句与原生帧证据、独立边界审核、全批量语义规划、帧计划门禁和便携帧渲染。生产任务从 `video-montage` 技能和执行器进入，不直接把模块脚本当作交付入口。

现行规则见 [语义合同](references/semantic-contract.md)；任务流程见 [技能工作流](../../skill/video-montage/semantic-workflow.md)。任务证据保存在任务目录，来源 SHA-256、帧区间、审核与成片回执必须保持可追溯。
