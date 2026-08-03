# Q1719: Authority Set Misbinding After Partial State Change

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages)` with attacker-controlled consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights and replaying the same public flow after one part of storage changed and another part did not, and make `lib_logic` accept a proof under the wrong authority set or signer set so `the accepted authority set` becomes inconsistent with `the authority set that actually finalized the proven block`, breaking the invariant that a consensus update must advance only when the exact current or next authority set for that block authenticated it and leading to Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement?

## Target
- File/function: modules/ismp/clients/casper-ffg/src/lib.rs::lib_logic
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages)
- Attacker controls: consensus proof bytes, authority indices, header batches, state-machine identifiers, and claimed heights
- Exploit idea: Accept a proof under the wrong authority set or signer set. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: a consensus update must advance only when the exact current or next authority set for that block authenticated it
- Expected Immunefi impact: Critical: false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement.
- Fast validation: Mutate validator-set identifiers, signer membership, or signer indices while keeping the rest of the proof well formed, and assert consensus state, epoch data, and stored commitments do not advance. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
