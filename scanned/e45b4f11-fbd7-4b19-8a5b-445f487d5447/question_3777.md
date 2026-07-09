# Q3777: Solana finalize_transfer_sol guardian-approved bytes map to wrong authenticated emitter

## Question
Can an unprivileged attacker submit a valid Wormhole proof through `public Solana `finalize_transfer_sol` instruction` that `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol` associates with the wrong emitter or factory because of verifies the NEAR-derived signature for a SOL transfer, uses nonce tracking, and processes native-SOL finalization, violating `native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol`
- Entrypoint: `public Solana `finalize_transfer_sol` instruction`
- Attacker controls: signed payload bytes, destination nonce, recipient account, payer funding, and SOL-specific account layout
- Exploit idea: Probe derivations that use token-address chain or local mapping state to infer emitter identity.
- Invariant to test: native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Craft mismatched emitter/token-chain payloads and assert that every accepted proof binds to the exact contract instance that published it.
