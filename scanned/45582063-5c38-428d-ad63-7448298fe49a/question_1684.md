# Q1684: NEAR send_tokens helper resume-path replay or duplication through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reached from public finalize and fast paths` with control over token id, recipient account, amount, optional message, current gas budget, and whether the token is wNEAR, deployed, or external and desynchronize `near/omni-bridge/src/lib.rs::send_tokens` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `resume-path replay or duplication` attack class because chooses between wNEAR unwrap, bridge-token mint, `ft_transfer`, and `ft_transfer_call` depending on token type and message presence, violating `asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens`
- Entrypoint: `internal helper reached from public finalize and fast paths`
- Attacker controls: token id, recipient account, amount, optional message, current gas budget, and whether the token is wNEAR, deployed, or external
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::send_tokens` and the adjacent mint, burn, or custody accounting after every branch.
