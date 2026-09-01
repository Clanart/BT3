# Q3317: `get_subroot` and the total-work comparison

## Question
Can a prover manipulate the work values compared in `get_subroot` in `circuits-lib/src/header_chain/mmr_native.rs` - truncation to the low 128 bits, byte-order of the comparison, an empty or filtered challenge set - so a chain with less accumulated work is accepted as canonical and a payout on a minority branch is proved?

## Target
- File/function: `circuits-lib/src/header_chain/mmr_native.rs` -> `get_subroot` (Full-featured MMR implementation for native (non-zkVM) environments)
- Entrypoint: a Groth16 proof over a low-work chain -> `get_subroot`
- Attacker controls: the header chain fed into the proof and the challenge set presented alongside it; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: prove a settlement on a branch that is not the canonical chain
- Invariant to test: the work value the circuit treats as the operator's == the full accumulated work of the chain containing the proved block, compared without truncation loss
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: feed boundary work values to `get_subroot` and assert the comparison is total and untruncated
