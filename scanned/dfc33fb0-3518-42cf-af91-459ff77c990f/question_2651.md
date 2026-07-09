# Q2651: EVM ENearProxy mint legacy receipt or origin counter progression is forgeable

## Question
Can an unprivileged attacker use `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` so that `evm/src/eNear/contracts/ENearProxy.sol::mint` advances or assumes a legacy receipt/origin counter in a way that authorizes multiple settlements for one event, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Target synthetic receipt ids and legacy bridges that increment counters locally before proving uniqueness elsewhere.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay or reorder legacy settlements and assert that no synthetic or local counter can replace canonical event uniqueness.
