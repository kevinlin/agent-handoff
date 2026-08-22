<sub>🌐 <b>中文</b> · <a href="README.en.md">English</a></sub>

<div align="center">

# 搭子.skill (Partner)

> 我的 Claude Code 和 Codex 天下第一好。

[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-partner--skill-blueviolet)](SKILL.md)
[![Version: 3.0.0](https://img.shields.io/badge/version-3.0.0-ef6f4f)](CHANGELOG.md)
[![GitHub stars](https://img.shields.io/github/stars/LearnPrompt/partner-skill?style=flat-square&color=f5c542)](https://github.com/LearnPrompt/partner-skill/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Claude Code 负责规划、拆分和验收，Codex 在自己的订阅上把活干完。省的是 Claude API 额度，守住的是质量门。**

[30 秒装上](#30-秒装上) · [Showcase](#showcase) · [一句话用起来](#一句话用起来) · [成本压力模型](#成本压力模型) · [它解决什么](#它解决什么) · [安全边界](#安全边界) · [验证](#验证)

</div>

---

## 30 秒装上

一行 `npx` 装好：

```bash
npx skills add LearnPrompt/partner-skill -g
```

也可以直接把这个仓库链接发给你的 Agent：

```text
请安装搭子.skill：https://github.com/LearnPrompt/partner-skill
```

本地开发或手动安装：

```bash
git clone https://github.com/LearnPrompt/partner-skill.git
cd partner-skill
bash install.sh
```

装完第一次用之前，说一句「搭子，配置」：搭子会打开只监听 `127.0.0.1` 的本地单页 UI。均衡/质量/成本三个起点和三个身份的具体 CLI/模型/effort 都在一页完成；页面先展示精确 diff，确认后才落盘。小白流程固定使用当前项目、本机 Git 忽略、安装后自动检查等安全默认值，不再追问高级选项。Codex 模型和每个模型支持的 effort 来自本机 CLI `model/list`，Claude 别名与 effort 来自 `claude --help`，绝不瞎猜。

<div align="center">
<p><strong>配置演示：切换工作模式、CLI、模型和推理强度</strong></p>
<a href="assets/config-switch-demo.mp4">
<img src="assets/config-switch-demo.gif" alt="Partner 配置页面演示：切换工作模式与三个搭子角色的 CLI、模型和推理强度" width="720" />
</a>
<p><a href="assets/config-switch-demo.mp4">打开 7 秒完整 MP4</a></p>
</div>

## Showcase

**真实成本小票（v2.0.1 存档）**

<div align="center">
<a href="examples/v2.0.1-conversation-cost-receipt.html">
<img src="assets/v2.0.1-conversation-cost-receipt.png" alt="Partner v2.0.1 对话消耗网页截图：角色运行映射、可核验成本、任务、推理强度和交付结果" width="720" />
</a>
<p><sub>真实网页截图：切换角色查看实际模型、effort、任务、成本与交付证据。</sub></p>
</div>

这是 v2.0.1 时期 bounded planner 的真实故障链存档。那个组件已在 3.0.0 移除，这里保留它，是因为它记录了一件仍然成立的事：真实成本可以逐次核验，失败的那一次没有静默换模，也没有把半截结果当计划。

| 实际阶段 | 观测结果 | Claude CLI 返回成本 | Partner 怎么处理 |
|---|---|---:|---|
| v2.0.0 仓库规划 | 登录成功，派生 3 个子 Agent 后 stream idle；花费已发生但没有计划 | `$6.57` | 暴露旧流程无边界 |
| v2.0.1 fresh bounded attempt | 180 秒没有有效事件，`idle_timeout`；没有生成 plan | `unknown`（CLI 未返回最终成本） | 杀掉整个进程组，保留 metadata/checkpoint/recovery |
| 同 session resume | exact `claude-fable-5` / `xhigh`，返回有效八段计划 | `$0.382695` | 证明失败链可恢复，没有换模型 |
| 最终 fresh candidate | exact model/session、return code 0、packet/runner hash 一致 | `$0.45282` | 作为最终 Judge 与 PR 证据 |

这里的美元数是 Claude CLI 在对应真实 planning run 中返回的成本，不是整套工作流的 token 节省率；失败尝试没有 final result 时就诚实写 `unknown`。每个身份实际执行的任务、模型、effort 和逐次成本分别见 [v2.0.0 失败基线小票](examples/v2.0.0-conversation-cost-receipt.md)和 [v2.0.1 完整对话消耗小票](examples/v2.0.1-conversation-cost-receipt.md)（两张都是 schema v2 存档）。运行边界见 [`docs/releases/v2.0.1.md`](docs/releases/v2.0.1.md) 和 [`docs/showcase-cost-model.md`](docs/showcase-cost-model.md)。

## 一句话用起来

```text
搭子，这个需求你先规划拆分；机械的部分分工给 codex 后台跑，
你盯着进度，做完你自己把 diff 全量看一遍再收，
最后给我 Partner Session Receipt。
```

更短一点：

```text
搭子，分工给 codex 后台跑，做完你全量验收。
```

第一次用先配置：

```text
搭子，配置
```

也可以从仓库直接打开：

```bash
bash install.sh --configure --repo /path/to/project
```

## 成本压力模型

Partner 的省钱逻辑不是“少用 Claude”，而是**别用 Claude 的电表干机械活**。最贵的浪费是让 Claude 逐个文件做批量迁移、写模板测试、跑大范围只读扫描——这些活换到 Codex 订阅上，判断质量一点没变。

当前 README 用的是 showcase workload model，不是 API billing telemetry。没有可靠数据支撑时，我们不声称能省多少 token。这张表由 `scripts/showcase-cost-ledger.py` 生成，源数据在 `examples/showcase-cost-ledger.json`。

| 没有 Partner | 有 Partner |
|---|---|
| 机械改动也走 Claude API 电表 | 机械改动落在 Codex 订阅上 |
| 「分工了」只是口头说法 | 每个任务有 jobId、meta 里记着真实 model/effort |
| “省 token”说不清楚 | receipt 写明 `codex_jobs` 和 `roles_used` |

三种模式可以这样看：

| 模式 | Codex 承担 | Claude Code 承担 | Claude 压力 | 适合场景 |
|---|---:|---:|---:|---|
| 纯 Codex | 100% 实现与检查 | 0% | 0.0x，但没有独立验收视角 | 低风险、可自行验证 |
| 搭子 Partner | 约 70% 实现、检查、修复 | 约 30% 规划、拆分、全量验收 | 0.3x | 任务多、机械量大、需要省 Claude API 成本 |
| 纯 Claude Code | 0% | 100% 全流程 | 1.0x，机械改动也由 Claude 承担 | 很短任务，或用户明确要 Claude 全包 |

标准收尾小票：

```text
[Partner session receipt]
phase: final fix
claude_session: 9836fe7e-4aca-47a6-83b5-69086b8db275
codex_jobs: 2
checks: bash scripts/check-skill-repo.sh .; jq schema check; git diff --check
anomalies: none
scope: project
config_source: project
roles_used: [{"role":"fast_worker","host":"codex","model":"gpt-fast","effort":"high","verified":true}]
receipt_schema_version: 3
```

没有可靠 telemetry 时，Partner 只报告能验证的事实：哪些活跑在 Codex 订阅上、几个 job 几轮返工、全量 diff 是否对着验收标准看过、检查是否通过。

## 它解决什么

你可能已经在 Codex 和 Claude Code 之间来回切了。真正麻烦的不是“它们能不能协作”，而是协作经常散掉：

- Claude Code 适合规划、判断和验收，但让它包办所有机械改动很贵。
- Codex 适合长上下文实现、跑检查、批量迁移，但缺一个独立验收的视角。
- 「分工给 codex」经常只是口头说法：没有 job 状态、没有真实 model/effort 记录、也没人回来对着验收标准看 diff。
- 用户只听到“我分工了”，但看不到到底省在哪、质量有没有掉。

Partner 把这件事变成固定协议：

```text
Claude Code (driver):
  plan -> split (adversarial gate) -> delegate -> monitor loop -> full review -> receipt

Codex (background jobs):
  implement -> report -> bounded fix rounds on the same session
```

下沉执行有三条通道，按「电表落在订阅上」优先排序：搭子后台作业（`delegate-codex.sh`，走 Codex 订阅，可 loop 监控 + resume 返工）> 一次性 Codex subagent（卡住时补刀）> 更便宜的 Claude subagent（仍走 Claude API 计量，不省额度）。质量关键步即使贵也留 Claude。

分工方案先过对抗式审查，三个问题必须书面回答：这条活是不是真的不需要贵的那一档、拆开之后的集成成本会不会把省下的吃掉、每一行的身份是否配得上它的风险。过不了的行就改身份、并回邻居、或者留在 Claude 手里。

角色的模型/推理强度只有一份事实源——`.partner/config.toml`（项目或全局），不会散落进 prompt 或文档里到处抄。

## 触发方式

```text
搭子
搭子，帮我规划一下这个任务。
搭子，分工给 codex 后台跑，做完你全量验收。
搭子，这个重构拆给 codex 一部分，拆之前先审一下分工靠不靠谱。
搭子，codex 的后台任务跑完了，验收一下然后给我回执。
搭子，恢复上次任务，接着做。
搭子，配置
搭子，试跑
搭子，走完整协议，给我一个 PR 交付。
这个结论有争议，找仲裁者盲解一遍再定。
```

## 它会交付什么

- 清晰分工：Claude Code 负责规划、拆分、集成和全量验收；Codex 负责实现、跑检查、批量改动和返工。
- 对抗式分工门：每一行都要过三个问题才允许下沉，过不了就改身份或留在 Claude 手里。
- 持久化后台作业：`scripts/delegate-codex.sh` 把 `codex exec --json` 包成可查状态、可 resume、可取消的作业，状态落在 `<repo>/.partner/jobs/`。
- 全量 review 门：对着 `.partner/goal.md` 里的验收标准看完整 diff，不是抽样，也不是信 Codex 自己的总结；每个任务最多两轮返工，还不行就收回 Claude 自己做。
- Session Receipt：把 `codex_jobs`、检查、异常和 `roles_used` 写清楚，可用 `scripts/validate-receipt.py` 机器校验。
- 并发安全的目标文件：`scripts/goal-sync.py` 用 sha256 比对读写 `.partner/goal.md`，monitor loop 和主线程不会静默覆盖彼此。
- 盲解仲裁：有争议的判断同时发给 `deep_reasoner` 和 `arbiter`，两边互不知情，分歧由 driver 裁决并记进 receipt。
- Darwin-style 验证门：一次只改一个协作维度，过检查才保留。
- 首次配置向导（`搭子，配置`）：均衡/质量/成本预设可继续逐角色调整，`.partner/config.toml` 是单一事实源；小白流程使用安全默认值，预览 diff 再落盘，绝不覆盖已有 agent 文件；模型和 effort 从两个 CLI 的真实能力列表读取，安装后自动启动无工具的新 Claude session 和 Codex dry-run 做验证。
- Partner Session Receipt v3：`scope`/`config_source`/`roles_used` 证明这次跑的到底是哪个模型、哪个 effort，而不只是"分工了"。
- 可选的完整协议（`references/goal-to-pr.md`）：Plan→Goal→PR→Verification，一路跑到 merge-ready + preview verified 为止；merge、上生产、打 tag、force-push、删除、破坏性迁移、对外发布，每一个都要单独一句祈使句授权。

## 文件结构

```text
SKILL.md                         Runtime instructions for Claude Code
README.md                        中文入口
README.en.md                     English entrypoint
install.sh                       Local installer for ~/.claude/skills/partner-skill
test-prompts.json                Trigger and behavior regression prompts
docs/showcase-cost-model.md      Showcase 成本压力模型与真实 token 记录字段
docs/receipt-schema.json         Partner Session Receipt 的 JSON schema (partner.receipt.v3)
docs/config-schema.md            Partner 配置 schema v2：身份矩阵、优先级链、并发语义、TOML 子集边界
examples/session-receipt.md      Receipt 样例（schema v2 存档）
examples/v2.0.0-conversation-cost-receipt.md
                                  2.0.0 失败基线的身份、模型、effort 与成本小票
examples/v2.0.1-conversation-cost-receipt.md
                                  本轮三个身份的真实任务、模型、effort 与成本小票
examples/showcase-cost-ledger.json
                                  三种模式的成本压力 ledger
references/handoff-template.md   派活给 Codex 的委派包 + 给用户的 Goal Packet 模板
references/darwin-ratchet.md     Validation-gated improvement rules
references/claude-driven.md      Claude 主驾的五阶段流程（含对抗式分工门与盲解仲裁）
references/setup.md              「搭子，配置」首次配置向导：身份矩阵（三身份跨家搭配）+ 第二宿主增量接入
references/tryout.md             「搭子，试跑」身份试跑：三身份各跑一个微任务，出对照报告证明模型真生效
references/goal-to-pr.md         完整协议(可选)：Plan→Goal→PR→Verification、hard stop 清单、祈使句授权法
references/goal-template.md      .partner/goal.md 目标文件模板（任务表 + checkpoint 规则）
references/fable5-principles.md  前沿模型提示词共同准则（why-forward、effort、checkpoint、resume）
references/memory-protocol.md    收尾记忆协议（claude-mem / mem0 / auto-memory / rollout）
scripts/showcase-cost-ledger.py  Rebuilds the showcase cost-pressure ledger
scripts/check-readme-parity.py   检查中英文 README 章节和关键证据是否对齐
scripts/check-skill-repo.sh      Publish readiness smoke check
scripts/make-receipt.py          生成并预校验 receipt，可存入 .partner/
scripts/validate-receipt.py      校验 Partner Session Receipt 的字段与取值
scripts/run-test-prompts.py      行为回归 prompt 的静态检查与实验性 live 模式
scripts/delegate-codex.sh        Codex 后台任务原语：submit / status / result / resume / cancel
scripts/partner-config.py        配置引擎：TOML 子集解析、确定性写回、锁与原子写（schema v2）
scripts/partner_runtime.py       Claude 子进程共享环境边界，避免宿主变量污染一方 OAuth
scripts/partner-setup.py         向导落盘引擎：--preview/--apply/--rollback/--smoke/--status/--interactive
scripts/partner-setup-ui.py      localhost 单页配置 UI：完整模型矩阵、精确预览、确认写入
scripts/goal-sync.py             .partner/goal.md 哈希校验读写：并发写入不静默丢更新，冲突即 abort
tests/test_partner_config.py     配置引擎单元测试（round-trip / 锁 / 优先级链）
tests/test_partner_setup.py      向导引擎单元测试（幂等 / 防覆盖 / managed block / 回滚）
tests/test_partner_setup_ui.py   本地 UI 状态、预览绑定与写入门单元测试
tests/test_delegate_role.py      --role 注入与覆盖链单元测试
tests/test_goal_sync.py          goal.md 并发写入单元测试（哈希不符即拒绝，证明无静默丢更新）
```

## 安全边界

- 后台作业默认读写沙箱按你自己的 codex 配置；只读扫描/审查类任务用 `--read-only`。
- 分工不等于放权：架构、拆分决策、跨任务集成、安全与正确性关键路径、最终验收都留在 Claude 手里。
- 不接受没读过的 diff；不因为 Codex 说"做完了"就标记完成。
- 不改 repo visibility、不打 tag、不发 registry、不公告，除非用户单独明确授权。
- 不用 `git reset --hard` 当默认回刀方案；优先用可审计 diff 或 revert。
- `.partner/config.toml` 默认不进 git（写进 `.git/info/exclude`，不动你的 `.gitignore`）；Codex 侧模型名绝不臆造，探测不到就报错等你给。
- Managed routing block（写进 CLAUDE.md/AGENTS.md 的常驻路由段）默认关闭，标记损坏的五种情形一律拒绝并解释，绝不猜测修复。
- 走完整协议（Plan→Goal→PR→Verification）也一样：merge、上生产、打 tag、force-push、删除、破坏性迁移、对外发布，各自需要独立的一句祈使句，不会因为前面一句「继续」就顺带做掉。

## 验证

```bash
bash scripts/check-skill-repo.sh .
python3 scripts/check-readme-parity.py
python3 -m unittest discover tests
jq -r '.[].id' test-prompts.json
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py
```

以上检查也会在每次 push 和 pull request 时由 GitHub Actions 自动运行（`.github/workflows/checks.yml`）。

## License

MIT

---

<div align="center">

**更多好用 Skill · More Skills** → [learnprompt.pro/skills](https://learnprompt.pro/skills/)

[鲁班·Skill打磨](https://github.com/LearnPrompt/luban-skill) · [庖丁·博主蒸馏](https://github.com/LearnPrompt/paoding-skill) · [蔡伦·对话造纸](https://github.com/LearnPrompt/cailun-skill) · [阿福·LLM Todo](https://github.com/LearnPrompt/afu-llm-todo) · [愚公·Loop工程](https://github.com/LearnPrompt/loop-engineering) · [搭子·结对开发](https://github.com/LearnPrompt/partner-skill) · [AI雷达·零API资讯](https://github.com/LearnPrompt/ai-news-radar)

[淘金小镇·ClawHub日榜](https://github.com/LearnPrompt/skillrush-town) · [Irasutoya·正文配图](https://github.com/LearnPrompt/carl-irasutoya-illustrations) · [Humanize PPT·演讲系统](https://github.com/LearnPrompt/humanize-ppt) · [CC Harness·六件套](https://github.com/LearnPrompt/cc-harness-skills) · [微信读书教练](https://github.com/LearnPrompt/carl-weread) · [X Article发布](https://github.com/LearnPrompt/x-article-publisher-skill)

<sub>**[LearnPrompt](https://github.com/LearnPrompt) 出品** · 公众号「卡尔的AI沃茨」 · [X @aiwarts](https://x.com/aiwarts)</sub>

<sub>致谢：`references/goal-to-pr.md` 的 done_when / anti-Goodhart / 祈使句授权法词汇，参考了 [愚公·Loop工程](https://github.com/LearnPrompt/loop-engineering) 的 goal-forging.md 与 guardrails.md（只借词汇与不变量，不搬 YAML 格式与仪式）。</sub>

</div>
