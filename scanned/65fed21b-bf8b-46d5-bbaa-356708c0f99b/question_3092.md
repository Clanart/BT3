# Q3092: EVM ENearProxy mint legacy receipt or origin counter progression is forgeable at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/eNear/contracts/ENearProxy.sol::mint` violate `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context` in the `legacy receipt or origin counter progression is forgeable` attack class because fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Target synthetic receipt ids and legacy bridges that increment counters locally before proving uniqueness elsewhere. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay or reorder legacy settlements and assert that no synthetic or local counter can replace canonical event uniqueness. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
