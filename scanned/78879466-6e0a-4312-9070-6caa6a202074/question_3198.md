# Q3198: NEAR finish_withdraw_v2 origin nonce fork across chained flows

## Question
Can an unprivileged attacker make `near/omni-bridge/src/lib.rs::finish_withdraw_v2` allocate origin nonces in a sequence that forks across normal, legacy, or chained bridge legs reachable from `legacy/public withdrawal completion path on migrated tokens`, violating `legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::finish_withdraw_v2`
- Entrypoint: `legacy/public withdrawal completion path on migrated tokens`
- Attacker controls: sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address
- Exploit idea: Look for one public flow that allocates fresh origin nonces while another flow derives new bridge obligations from an existing transfer id.
- Invariant to test: legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive chained and legacy outbound flows concurrently and assert that no two emitted obligations share or ambiguously derive origin-nonce lineage.
