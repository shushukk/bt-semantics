# AMD AUP Learning Cloud 预演指南

> 目标：在 AUP Learning Cloud 上完整跑一遍本项目（"云端预演"），并把云端大规模实验的数据
> 收集成报告素材。全程约 30~40 分钟（含环境安装）。

---

## 1. 平台是什么、为什么本项目适合它

[AUP Learning Cloud](https://amdresearch.github.io/aup-learning-cloud/) 是 AMD Research 开源的
教学云（MIT 许可）：底层是 **Ryzen AI APU**（如 Ryzen AI Max+ 395，Zen 5 16 核 + 大内存 + iGPU/NPU），
上层是 **JupyterHub / Code Server**，浏览器登录（GitHub SSO）即可获得一个开发环境。

本项目的工作负载是 **CPU + 内存受限**的（状态空间探索和 SMT 求解都是纯 CPU 计算），不需要 GPU/NPU。
这一点要写进"算力需求设计"里，因为它是判断算力类型的一部分：

* 当前瓶颈：**单核解释速度 + 峰值内存**（状态数 × 每状态开销）；
* 未来瓶颈：**并行度**（状态空间按子树切块后多核/多机并行 BFS）与**总内存**（10⁸ 量级转移边）；
* GPU/NPU 对本负载无收益（没有规则的数据并行结构）——明确"不需要什么"同样是算力设计的得分点。

## 2. 准备（本地一次性完成）

```bash
cd bt-semantics
git init && git add -A && git commit -m "bt-semantics: 行为树形式语义与 SMT 验证"
# 在 GitHub 建一个空仓库后：
git remote add origin https://github.com/<你的用户名>/bt-semantics.git
git push -u origin main        # 这一步同时满足"开源链接提档"
```

## 3. 云端预演步骤（登录 tpe.aupcloud.io 后）

1. **登录并选择环境**：GitHub SSO 登录 → Spawn 页选 CPU 镜像（`default` 或 `code-cpu` 即可，无需占用 GPU 配额）。
2. **拉代码**：打开 Terminal（JupyterLab 的 Terminal 或 Code Server）：
   ```bash
   git clone https://github.com/<你的用户名>/bt-semantics.git
   cd bt-semantics
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **回归测试**（证明云端结果与本地一致）：
   ```bash
   python -m pytest -q          # 预期：41 passed
   ```
4. **Demo 预演**（每个都截图）：
   ```bash
   python -m btfsv show    examples/guard.json
   python -m btfsv verify  examples/buggy.json      # 反例 + 回放复核
   python -m btfsv verify  examples/guard.json      # 有界通过
   ```
5. **云端大规模实验**（关键：把本地撞墙的规模推过去）：
   ```bash
   # 先看机器规格，写进报告
   lscpu | head -12; free -h
   # 大规模扫描：状态上限从本地的 10 万提到 50 万
   python scripts/bench_scale.py --widths 1,2,3,4,5,6,7,8 --durations 2,3,4 \
          --state-limit 500000 --out-dir build_cloud
   ```
   本地（MacBook，约 180 MB 峰值）在 w=6 就撞了 15 万条边的上限；云端 32 GB 级内存
   可以把扫描推到 w=7~8（|S| ≈ 数千，|δ| ≈ 数十万），并测出真实峰值内存曲线。
6. **收集证据**：Jupyter 文件浏览器里下载 `build_cloud/`（bench.csv / bench.md / bench.html），
   截图保存：pytest 全绿、两份 verify 输出、bench 控制台表格、`lscpu`/`free -h`。

## 4. 报告"云端预演"一节的写作模板

> 本节在 AMD AUP Learning Cloud（Ryzen AI Max+ 395，Zen 5 16 核，\_\_ GB 内存）上完成。
> 在 500,000 状态预算下扫描 8 组树形，实测峰值内存由 \_\_ MB 增长至 \_\_ MB
> （`bench_cloud.csv` 第 \_\_ 行），与理论模型 |δ| ≈ Π dᵢ × 2^w 的预测一致（误差 \_\_%）。
> 据此外推：w=10 时 |δ| ≈ 1.7×10⁸，按每条转移约 150 B 估算需 25 GB 级内存与 \_\_ h 单核机时，
> 单机不可行，故提出"按顶层子树分片 + 多机并行 BFS"的分布式方案，预估需要 \_\_ 节点 × \_\_ GB。

对应的理论估算公式（已有实测标定）：

```
|S|  ≈ Π dᵢ          （并行分支动作进度的组合数）
|δ|  = |S| × 2^w     （环境全称量化）
内存 ≈ |δ| × ~150 B   （每条转移的对象开销，可用云端实测标定）
机时 ≈ |δ| × 单步微秒数（本地实测 ~1 µs/转移）
```

## 5. 符合性自查表（B 自命题型）

| 要求 | 现状 | 证据位置 |
|---|---|---|
| 针对本人方向（编译/程序语言/形式化） | ✅ SOS 语义 + 属性文法类比 + 编译器式流水线 | `btfsv/sos.py`、`docs/REPORT.md` §1 |
| proposal 规划 | ✅ 章节映射 + 算力规划模板 + 答辩问答 | `docs/REPORT.md` |
| 小型 demo | ✅ 可运行、41 个测试全绿 | `btfsv/`、`tests/` |
| **云端平台预演** | ⬜ 待做（按本文档 §3 执行） | `build_cloud/` + 截图 |
| **未来的算力需求设计** | ✅ 公式 + 实测标定 + 外推；⬜ 云端数据回填 | `scripts/bench_scale.py`、本文档 §4 |
| 开源链接（提档） | ⬜ 待推 GitHub | §2 |

## 6. 常见问题

* **平台里 pip 装不上？** 优先 `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`；
  平台镜像若自带 z3-solver 则直接跳过安装。
* **Jupyter 里怎么跑命令行？** JupyterLab → File → New → Terminal；或用 Code Server 的内置终端。
* **notebook 演示**：仓库根目录有 `cloud_demo.ipynb`，在 JupyterLab 里直接打开、从头到尾
  Run All 即可完成 §3 的第 3~5 步（并内联绘制状态爆炸曲线），是"预演"最省事的形式。
* **要不要用 GPU 镜像？** 不需要。刻意选 CPU 镜像并在报告里说明理由（见 §1），体现的是算力
  设计能力而不是资源浪费。
