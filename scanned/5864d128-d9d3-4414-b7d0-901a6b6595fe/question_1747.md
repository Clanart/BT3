# Q1747: Stale Height Replay Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `verify_consensus` treat stale or already-consumed proof material as fresh and overwrite newer state so `the stored latest height or consensus state` becomes inconsistent with `the newest previously accepted height and state`, breaking the invariant that consensus state and stored commitments must move strictly forward and must never roll back to an older authenticated height and leading to Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement?

## Target
- File/function: modules/ismp/clients/grandpa/src/consensus.rs::verify_consensus
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights
- Exploit idea: Treat stale or already-consumed proof material as fresh and overwrite newer state. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: consensus state and stored commitments must move strictly forward and must never roll back to an older authenticated height
- Expected Immunefi impact: Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement.
- Fast validation: Accept one update first, then replay older or equal-height material with one field changed and assert latest height, consensus bytes, and commitments remain unchanged. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
