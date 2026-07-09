# Q3288: Solana used-nonce rent compensation bitmap slot boundary corrupts replay protection

## Question
Can an unprivileged attacker use `public inbound finalize flows` with boundary nonce values so that `solana/programs/bridge_token_factory/src/state/used_nonces.rs compensation path` aliases or mis-marks neighboring Starknet nonces because of charges or refunds the authority reserve depending on how sparse the used-nonce space is at the moment a new nonce is finalized, violating `reserve compensation must not let an attacker extract rent-lamports while still keeping replay protection intact for the same nonce range`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/used_nonces.rs compensation path`
- Entrypoint: `public inbound finalize flows`
- Attacker controls: max observed nonce, nonce gaps, current reserve lamports, and payer funding
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme.
- Invariant to test: reserve compensation must not let an attacker extract rent-lamports while still keeping replay protection intact for the same nonce range
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit.
