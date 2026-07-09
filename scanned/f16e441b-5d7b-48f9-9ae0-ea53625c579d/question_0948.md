# Q948: NEAR EVM eNear interface path legacy or migration path aliasing via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `legacy/public eNEAR mint/burn/finalize flows` and then replay or reorder another proof-consuming public entrypoint so that `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` ends up accepting two inconsistent interpretations of the same economic event specifically around `legacy or migration path aliasing` under legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Use memo-triggered legacy paths, migrated-token aliases, or old/new token relationships to create a second valid outbound interpretation of the same balance change. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise both the modern and legacy branches with equivalent economic inputs and assert that only one bridge claim can arise from one unit of consumed value. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
