# Q3232: EVM ENearProxy mint legacy proof can be replayed in modern context

## Question
Can an unprivileged attacker submit a valid legacy proof through `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` and make `evm/src/eNear/contracts/ENearProxy.sol::mint` accept it after the bridge state moved on because of fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR, violating `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Look for adapters that validate one older proof format but still affect live bridge state.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt stale-proof replay and assert that current bridge state or replay guards reject it once the event was consumed.
