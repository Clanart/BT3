# Q1114: NEAR EVM eNear interface path legacy or migration path aliasing through cross-module drift

## Question
Can an unprivileged attacker use `legacy/public eNEAR mint/burn/finalize flows` with control over proof bytes, receipt ids, token address, amount, and pause state and desynchronize `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `legacy or migration path aliasing` attack class because legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Use memo-triggered legacy paths, migrated-token aliases, or old/new token relationships to create a second valid outbound interpretation of the same balance change. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise both the modern and legacy branches with equivalent economic inputs and assert that only one bridge claim can arise from one unit of consumed value. Also assert cross-module consistency between `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` and the adjacent mint, burn, or custody accounting after every branch.
