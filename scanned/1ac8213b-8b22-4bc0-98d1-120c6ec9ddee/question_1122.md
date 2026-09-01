# Q1122: `host_journal_hash` and storage-slot derivation

## Question
Can a prover choose an index or key so that `host_journal_hash` in `bridge-circuit-host/src/structs.rs` derives a storage slot that collides with, or is adjacent to, a slot holding different contract data (via `index*2` arithmetic, slot-base addition, or a key equality check performed on only part of the key), so a proof reads an attacker-chosen value as if it were the withdrawal or deposit record?

## Target
- File/function: `bridge-circuit-host/src/structs.rs` -> `host_journal_hash`
- Entrypoint: a storage proof supplied to the circuit -> `host_journal_hash`
- Attacker controls: the claimed index and the serialized storage proof; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: prove a bridge record whose bytes actually came from an unrelated slot
- Invariant to test: the slot proved == the canonical slot for (mapping, index), with no arithmetic wrap or collision
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: assert slot derivation is injective and rejects wrapping indices
