# Q2565: Solana used-nonce rent compensation storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `public inbound finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `solana/programs/bridge_token_factory/src/state/used_nonces.rs compensation path` violate `reserve compensation must not let an attacker extract rent-lamports while still keeping replay protection intact for the same nonce range` in the `storage withdrawal escapes live liabilities` attack class because charges or refunds the authority reserve depending on how sparse the used-nonce space is at the moment a new nonce is finalized becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/used_nonces.rs compensation path`
- Entrypoint: `public inbound finalize flows`
- Attacker controls: max observed nonce, nonce gaps, current reserve lamports, and payer funding
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: reserve compensation must not let an attacker extract rent-lamports while still keeping replay protection intact for the same nonce range
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
