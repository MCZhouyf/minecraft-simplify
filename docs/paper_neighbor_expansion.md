# 论文措辞：签名诱导的邻域扩展（放入 §4.x 提案器小节）

## 中文版（正文）

**签名诱导的邻域扩展（Signature-induced neighbor expansion）。** 提案器产生的候选门控
直接来自 LLM 的失败诊断，而 LLM 的诊断往往反映其预训练先验给出的*默认*取值；当环境机制
发生漂移时，真实门控的取值可能与该默认值在某个有序结构上仅相差一档（例如采矿工具档位
由"铁"漂移为"钻石"）。我们不假设 LLM 能直接猜中漂移后的精确取值；相反，对于签名 Σ 中
声明为**有序枚举类型**（ordered categorical，记于 Σ 的 ordered_domains）的属性，提案器
沿该类型在签名中声明的序，确定性地追加 LLM 所提取值的直接前驱与后继作为兄弟候选，再由
干预验证与双池后验从中选出受 do-evidence 支持者。该操作只读取签名的类型声明，**不含任何
环境特定常量或阈值**，因而在不同环境（Σ_MC、Σ_ALF）间零改动迁移；在 Σ_MC 中唯一的有序
枚举为工具档位阶梯。连续数值阈值不作枚举式扩展——其取值偏差由后验的 δ-间隔判定吸收，
故无需引入任何人工步长。由于每个有序域的邻域规模为 O(1)，假设空间 H 仍保持有限，
定理 2 的保证不受影响。

## 英文版（备 rebuttal / 投稿）

**Signature-induced neighbor expansion.** Candidate gates emitted by the proposer
come from the LLM's failure diagnosis, which typically reflects the *default*
value given by its pretraining prior; under a drifted mechanism the true gate
value may differ from that default by a single step along some ordered structure
(e.g. the mining tool tier drifting from *iron* to *diamond*). Rather than
assuming the LLM guesses the drifted value, for any attribute whose signature
type is an **ordered categorical** (declared in Σ's `ordered_domains`) the
proposer deterministically appends the immediate predecessor and successor of the
proposed value along the order declared in the signature, and lets intervention
verification with the dual-pool posterior select the do-evidence-supported one.
This operation reads only the signature's type declarations and contains **no
environment-specific constants or thresholds**, so it transfers across
environments (Σ_MC, Σ_ALF) with no proposer changes; in Σ_MC the only ordered
categorical is the tool-tier ladder. Continuous numeric thresholds are *not*
enumerated — a small threshold error is absorbed by the posterior's δ-margin
decision — so no hand-chosen step size is introduced. Each ordered domain has an
O(1) neighborhood, so the hypothesis space H stays finite and the Theorem-2
guarantee is unaffected.

## 预备的 rebuttal 应答（若审稿人问"是否领域定制"）

Q: The neighbor expansion seems to rely on hand-specified Minecraft orderings,
   which undermines the generality claim.
A: The ordering is not specified in the proposer; it is part of the evaluation
   signature Σ (the `ordered_domains` declaration), exactly as the predicate
   vocabulary and dimension whitelist are. The proposer code contains no
   environment constants and is shared verbatim across Σ_MC and Σ_ALF; we
   demonstrate this transfer in §[ALF-Drift] / Table 10, where the same operator
   applies to Σ_ALF's own ordered domain with zero code changes. Numeric
   thresholds use no step size at all — their error is handled by the posterior's
   δ-margin — so the only structure used is the ordered-enum declaration that any
   typed signature naturally carries.
