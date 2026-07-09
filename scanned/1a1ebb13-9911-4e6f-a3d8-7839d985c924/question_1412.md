# Q1412: Solana finalize_transfer_sol emitter or factory binding mismatch

## Question
Can an unprivileged attacker submit a structurally valid proof to `public Solana `finalize_transfer_sol` instruction` whose payload points to one source chain while `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol` authenticates another because of verifies the NEAR-derived signature for a SOL transfer, uses nonce tracking, and processes native-SOL finalization, violating `native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol`
- Entrypoint: `public Solana `finalize_transfer_sol` instruction`
- Attacker controls: signed payload bytes, destination nonce, recipient account, payer funding, and SOL-specific account layout
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps.
- Invariant to test: native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees.
