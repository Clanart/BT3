# Q2760: Cross-wire presigning material in upsert_move_to_vault_txid_from_citrea_deposit

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline to make `upsert_move_to_vault_txid_from_citrea_deposit` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the set of verifier, operator, or watchtower keys that get associated with the deposit context, so the verifier nonce session that a final signature is supposed to consume is authorized under the wrong context and the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle breaks, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::upsert_move_to_vault_txid_from_citrea_deposit
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
