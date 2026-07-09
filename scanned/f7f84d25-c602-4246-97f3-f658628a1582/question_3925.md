# Q3925: NEAR EVM eNear interface path legacy withdrawal shortcut aliases a normal transfer via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `legacy/public eNEAR mint/burn/finalize flows` and then replay or reorder another proof-consuming public entrypoint so that `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` ends up accepting two inconsistent interpretations of the same economic event specifically around `legacy withdrawal shortcut aliases a normal transfer` under legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Target memo-based or self-transfer-based legacy shortcuts in token wrappers. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise equivalent economic transfers with and without legacy markers and assert that only the intended path creates a bridge event. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
