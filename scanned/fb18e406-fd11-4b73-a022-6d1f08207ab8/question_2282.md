# Q2282: `deposit_constant` and the total-work comparison

## Question
Can a prover manipulate the work values compared in `deposit_constant` in `circuits-lib/src/bridge_circuit/mod.rs` - truncation to the low 128 bits, byte-order of the comparison, an empty or filtered challenge set - so a chain with less accumulated work is accepted as canonical and a payout on a minority branch is proved?

## Target
- File/function: `circuits-lib/src/bridge_circuit/mod.rs` -> `deposit_constant` (This module implements the Bridge Circuit for Clementine protocol)
- Entrypoint: a Groth16 proof over a low-work chain -> `deposit_constant`
- Attacker controls: the header chain fed into the proof and the challenge set presented alongside it; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: prove a settlement on a branch that is not the canonical chain
- Invariant to test: the work value the circuit treats as the operator's == the full accumulated work of the chain containing the proved block, compared without truncation loss
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: feed boundary work values to `deposit_constant` and assert the comparison is total and untruncated
