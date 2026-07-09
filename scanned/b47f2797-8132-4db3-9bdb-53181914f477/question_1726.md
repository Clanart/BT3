# Q1726: EVM ENearProxy mint stale or reordered proof acceptance through cross-module drift

## Question
Can an unprivileged attacker use `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` with control over recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::mint` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `stale or reordered proof acceptance` attack class because fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::mint` and the adjacent replay-protection bookkeeping after every branch.
