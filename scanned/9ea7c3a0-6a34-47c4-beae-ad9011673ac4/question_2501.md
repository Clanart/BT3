# Q2501: EVM ENearProxy mint shared proof response reused across entrypoints at boundary values

## Question
Can an unprivileged attacker trigger `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/eNear/contracts/ENearProxy.sol::mint` violate `legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context` in the `shared proof response reused across entrypoints` attack class because fabricates proof bytes around `currentReceiptId`, increments the stored receipt id, and calls `eNear.finaliseNearToEthTransfer` to mint legacy eNEAR becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::mint`
- Entrypoint: `public bridge-side mint path behind `MINTER_ROLE` but reachable via live bridge flows`
- Attacker controls: recipient address, amount, current receipt id, and fake-proof bytes assembled from contract state
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: legacy receipt-id progression must not let one bridge-side action mint multiple times or mint against a stale/forged Near receipt context
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
