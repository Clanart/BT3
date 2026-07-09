# Q1472: Solana used-nonce rent compensation storage quote underestimates live state

## Question
Can an unprivileged attacker reach `public inbound finalize flows` and make `solana/programs/bridge_token_factory/src/state/used_nonces.rs compensation path` reserve less storage than the live bridge state actually consumes because of charges or refunds the authority reserve depending on how sparse the used-nonce space is at the moment a new nonce is finalized, violating `reserve compensation must not let an attacker extract rent-lamports while still keeping replay protection intact for the same nonce range`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/used_nonces.rs compensation path`
- Entrypoint: `public inbound finalize flows`
- Attacker controls: max observed nonce, nonce gaps, current reserve lamports, and payer funding
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: reserve compensation must not let an attacker extract rent-lamports while still keeping replay protection intact for the same nonce range
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
