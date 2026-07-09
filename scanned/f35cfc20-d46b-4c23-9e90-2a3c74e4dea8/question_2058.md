# Q2058: Solana UsedNonces::use_nonce shared proof response reused across entrypoints

## Question
Can an unprivileged attacker obtain a valid verifier result for one public flow and reuse it in `public nonce-tracking path through `finalize_transfer` instructions` because `solana/programs/bridge_token_factory/src/state/used_nonces.rs::use_nonce` trusts the same response envelope under a different meaning, violating `nonce tracking must reject every replay while keeping reserve accounting exact, even under gaps, huge nonces, or interleaved PDA initialization`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/used_nonces.rs::use_nonce`
- Entrypoint: `public nonce-tracking path through `finalize_transfer` instructions`
- Attacker controls: destination nonce, used-nonces PDA slot, payer funding, authority PDA lamports, and nonce gaps
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type.
- Invariant to test: nonce tracking must reject every replay while keeping reserve accounting exact, even under gaps, huge nonces, or interleaved PDA initialization
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics.
