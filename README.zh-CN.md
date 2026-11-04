# data-refinery 数据提炼炉

[English](README.md) · **中文**

把分散的公开报告，提炼成一张每个数字都有出处、可以排序的表。

你先提一个问题，比如"欧盟哪些国家的可再生能源转型最快"，然后把证据整理进一份 YAML 文件：每个来源对每个对象怎么说（↑ 或 ↓），再加上从开放数据接口拿到的数值指标。一个小脚本负责打分、排序，输出 CSV 或网页。

```
研究问题 → evidence.yaml ─┬─ signals 信号（某来源说 ↑ / ↓，带链接和可信度）
                          └─ metrics 指标（手填，或用 --live 从开放接口获取）
                                │
                                ▼
                     得分 = ↑ 条数 − ↓ 条数  →  排好序的 CSV / 网页
```

这个仓库同时也是一份三步教程，教你怎么给数据流程加上"AI 大脑"：

| 步骤 | 谁来决定 | 进度 |
|---|---|---|
| 1. 脚本 | 你手写证据表，代码负责排序 | ✅ 已完成 |
| 2. 工作流 | 步骤固定；Claude 针对一个问题读一份报告，提出信号，由你审核 | ✅ 本版本 |
| 3. Agent | Claude 自己决定去哪些来源搜、读什么（Claude Code 子 agent 或 Claude Agent SDK 两种写法） | 计划中 |

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m refinery check examples/renewables-eu/evidence.yaml          # 只校验
python -m refinery build examples/renewables-eu/evidence.yaml          # → out/renewables-eu_<日期>.csv
python -m refinery build examples/renewables-eu/evidence.yaml --html   # → out/renewables-eu_<日期>.html
python -m refinery build examples/renewables-eu/evidence.yaml --live   # 先从 Eurostat 刷新指标
python -m refinery build examples/job-shortage-de/evidence.yaml --sort salary_year

python tests/test_offline.py                                           # 离线测试
```

CSV 旁边会附一份 `.columns.csv`，解释每一列的含义。网页在电脑上显示为表格，平板和手机上显示为卡片；列说明和数据来源默认折叠。

## 让 Claude 读报告（第 2 步）

```
报告（--url 或 --file）→ extract → evidence.proposed.yaml → 你挑出有错的 → merge → evidence.yaml → build
```

```bash
# 1. Claude 提出信号，写进草稿。证据表不会被改动。
python -m refinery extract examples/job-shortage-de/evidence.yaml --url https://example.org/report.pdf

# 2. 打开 evidence.proposed.yaml，把有错的条目改成 decision: reject。

# 3. 其余条目写进证据表，然后照常出报告。
python -m refinery merge examples/job-shortage-de/evidence.yaml --accept-rest
python -m refinery build examples/job-shortage-de/evidence.yaml --html
```

如果是新问题，写一个还不存在的证据表路径，`merge` 时会自动创建：

```bash
python -m refinery extract my-question/evidence.yaml --question "哪些技工薪资最高？" --lang zh \
  --file report.pdf --source-url https://example.org/report.pdf
```

**需要** 已登录的 [Claude Code](https://code.claude.com/docs) 2.1.280 或更新版本。`extract` 调用的是 `claude` 命令，有订阅就能用，不需要 API key；它会占用套餐额度，长报告占得比较多。默认模型是 `claude-opus-5-5`：临时切换用 `--model`，长期修改就改 `refinery/extract.py` 里的 `DEFAULT_MODEL`。

**怎么防止出错**

- Claude 运行时没有任何工具，报告里就算藏了指令，最多也只能让草稿里出现错误内容。
- `quote_check`：程序到报告正文里查每条引用（`found` 查到、`not_found` 没查到、`too_short` 太短）。
- `claim_numbers_missing`：转述里有、引用里没有的数字。
- `--accept-rest` 不会接受被标记的条目。`not_found` 的引用要你核对原文后，自己写 `decision: accept_unverified`。
- 引用是真的，不代表其他内容都对，**一定要看方向和对象**。第一次试运行时，所有引用都是真的，但有一条讲工程师的内容被挂到了技工对象上。

同一份报告换个问题再提炼时不用重新下载：正文缓存在 `.cache/reports/`，不进仓库；要重新下载就加 `--refresh`。再次运行 `extract` 会追加进草稿，不影响你填好的 `decision`。不加 `--accept-rest` 时，`merge` 只写入你标为 `accept` 的条目，`pending` 的留在草稿里。

## 示例

| 文件夹 | 问题 | 语言 |
|---|---|---|
| `examples/renewables-eu` | 欧盟哪些国家的可再生能源转型最快？指标来自 Eurostat 接口 | 英文 |
| `examples/job-shortage-de` | 德国哪些职业最缺人，薪资水平如何？ | 中文 |

## 证据表格式

见 [英文说明](README.md#evidence-file) 里的完整示例。几个要点：

- `lang`：报告界面语言，`en` 或 `zh`
- `default_sort`：默认排序，`score` 或任意指标 id
- `trend_labels`：↑ / ↓ 显示成什么词，比如 `{up: 紧缺, down: 降温}`
- 每个来源都要标 `tier` 可信度：`official` 官方统计、部委 · `research` 研究机构、智库 · `industry` 企业或平台报告 · `media` 新闻报道 · `secondary` 二手转载。尽量用原始来源；只找到转载时，如实标成 `secondary`
- `claim` 是你的转述，原文可以另写在 `quote` 里
- `check` 会一次列出所有问题：来源没登记、可信度写错、指标不是数字……

### 可选字段

| 位置 | 字段 | 含义 |
|---|---|---|
| 对象 | `subtitle`、`tags` | 原文名称；自由标签 |
| 对象 | `keys` | 给接口用的代码，例如 `{eurostat: DK}` |
| 对象的指标值 | `{value, url, note}` | 可以只写数字，也可以带上链接和备注，备注会显示在报告里 |
| 信号 | `trend: mixed` | 显示，但不计分 |
| 信号 | `quote` | 原文句子，和你的 `claim` 转述并列 |
| 指标 | `better: lower` | 按这个指标排序时，越小越靠前 |
| 指标 | `show: false` | 表格里不显示（仍可用于 `derive` 和 `--sort`） |
| 指标 | `description` | 列说明里用的较长解释 |

指标值的来源优先级：证据表里手填 → `--live` 快照 → `derive` 计算。`--live` 会覆盖 `evidence.metrics.json`；只要有一个请求失败，就保留旧快照不动。

## 基本规则

- **读什么来源由你决定，是否符合条款也由你确认。** `extract` 会读取你指定的报告。网站拒绝访问时，命令只报错并停止，你可以自己下载后用 `--file` 传入。`--live` 的指标只从条款允许程序访问的开放接口获取（例如 Eurostat，CC BY 4.0）。
- **不收集个人信息。** 只给国家、行业、产品、职业排名，不给人排名。
- **人来决定。** 在第 2、3 步里，AI 只负责提议；你运行 `merge` 之前，任何内容都不会进入证据表。用 `--accept-rest` 时，你没有标为错误的条目都算接受。
- 得分只数每条信号站哪边。各家口径不同，彼此的数字不拿来直接比较。每条信号都算一次，不做去重，所以同一份报告或同一机构的多条信号都会计分。

## 目录结构

```
refinery/
  evidence.py      读取、校验、打分、排序
  fetch/           开放数据接口（eurostat.py）和快照写入
  output.py        CSV + 列说明、网页
  report.py        把报告（链接或文件）读成纯文本
  extract.py       调用 Claude、核对引用、写草稿
  merge.py         把接受的条目写进证据表
examples/<名称>/
  evidence.yaml           证据表
  evidence.metrics.json   --live 写的快照（提交进仓库，没联网也能出报告）
  evidence.proposed.yaml  等待审核的草稿（不进仓库）
tests/test_offline.py
```

## 许可证

[MIT](LICENSE)。示例数据的权利归各发布方所有，使用条款见各来源链接。
