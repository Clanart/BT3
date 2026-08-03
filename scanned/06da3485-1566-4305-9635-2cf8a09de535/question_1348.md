# Q1348: Challenge-Period Bypass With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `account_id_from_public_key` make pre-finality state usable for message execution, timeout settlement, or withdrawal attribution so `the state commitment treated as challenge-period safe` becomes inconsistent with `the oldest commitment whose challenge or finality delay has truly elapsed`, breaking the invariant that message execution, timeout handling, and reward attribution must only consume commitments after the configured safety delay and leading to High: duplicate settlement, duplicate claim, or double execution of a one-time flow?

## Target
- File/function: modules/consensus/tendermint/primitives/src/address.rs::account_id_from_public_key
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights
- Exploit idea: Make pre-finality state usable for message execution, timeout settlement, or withdrawal attribution. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: message execution, timeout handling, and reward attribution must only consume commitments after the configured safety delay
- Expected Immunefi impact: High: duplicate settlement, duplicate claim, or double execution of a one-time flow.
- Fast validation: Set up a recently updated commitment, then try to consume it immediately through the public handler path and assert all settlement paths reject. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
