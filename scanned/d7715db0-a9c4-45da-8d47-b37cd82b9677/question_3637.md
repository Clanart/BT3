# Q3637: EVM ENearProxy mint legacy proof can be replayed in modern context at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/eNear/contracts/ENearProxy.sol::mint` violate `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context` in the `legacy proof can be replayed in modern context` attack class because fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Look for adapters that validate one older proof format but still affect live bridge state. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt stale-proof replay and assert that current bridge state or replay guards reject it once the event was consumed. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
