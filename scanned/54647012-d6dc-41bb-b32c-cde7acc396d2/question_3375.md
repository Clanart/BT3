# Q3375: Solana finalize_transfer_sol shared Wormhole nonce can be replayed or gap-filled via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana `finalize_transfer_sol` instruction` and then replay or reorder another proof-consuming public entrypoint so that `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol` ends up accepting two inconsistent interpretations of the same economic event specifically around `shared Wormhole nonce can be replayed or gap-filled` under verifies the NEAR-derived signature for a SOL transfer, uses nonce tracking, and processes native-SOL finalization, violating `native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol`
- Entrypoint: `public Solana `finalize_transfer_sol` instruction`
- Attacker controls: signed payload bytes, destination nonce, recipient account, payer funding, and SOL-specific account layout
- Exploit idea: Target contracts that reuse one monotonic Wormhole nonce across deploy, init, metadata, and finalize messages. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Interleave message classes and failures and assert that nonce progression remains globally unique and monotonic for emitted messages. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
