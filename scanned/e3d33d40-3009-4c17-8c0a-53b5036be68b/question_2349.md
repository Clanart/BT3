# Q2349: EVM ENearProxy mint shared proof response reused across entrypoints through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` with control over recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::mint` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `shared proof response reused across entrypoints` attack class because fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::mint` and the adjacent replay-protection bookkeeping after every branch.
