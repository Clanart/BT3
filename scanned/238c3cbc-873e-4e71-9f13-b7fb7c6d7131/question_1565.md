# Q1565: EVM ENearProxy mint stale or reordered proof acceptance via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` and then replay or reorder another proof-consuming public entrypoint so that `evm/src/eNear/contracts/ENearProxy.sol::mint` ends up accepting two inconsistent interpretations of the same economic event specifically around `stale or reordered proof acceptance` under fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
