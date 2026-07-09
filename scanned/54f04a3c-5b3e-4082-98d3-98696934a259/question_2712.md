# Q2712: Solana used-nonce rent compensation rent compensation can leak reserve funds

## Question
Can an unprivileged attacker exploit `public inbound finalize flows` so that `solana/programs/bridge_token_factory/src/state/used_nonces.rs compensation path` overpays or refunds reserve lamports/NEAR while still keeping the same replay-protection or storage state because of charges or refunds the authority reserve depending on how sparse the used-nonce space is at the moment a new nonce is finalized, violating `reserve compensation must not let an attacker extract rent-lamports while still keeping replay protection intact for the same nonce range`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/used_nonces.rs compensation path`
- Entrypoint: `public inbound finalize flows`
- Attacker controls: max observed nonce, nonce gaps, current reserve lamports, and payer funding
- Exploit idea: Target reserve-compensation logic keyed by highest nonce or account initialization.
- Invariant to test: reserve compensation must not let an attacker extract rent-lamports while still keeping replay protection intact for the same nonce range
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Drive sparse high-nonce patterns and assert that reserve accounting changes exactly match the actual storage objects created.
