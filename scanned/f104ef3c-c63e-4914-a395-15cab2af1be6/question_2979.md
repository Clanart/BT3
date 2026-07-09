# Q2979: NEAR EVM eNear interface path shared proof response reused across entrypoints through cross-module drift

## Question
Can an unprivileged attacker use `legacy/public eNEAR mint/burn/finalize flows` with control over proof bytes, receipt ids, token address, amount, and pause state and desynchronize `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `shared proof response reused across entrypoints` attack class because legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Also assert cross-module consistency between `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` and the adjacent mint, burn, or custody accounting after every branch.
