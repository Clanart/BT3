# Q1768: `fee_vb` and handling of secret or pre-reveal material

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port obtain, or cause the early disclosure of, material `fee_vb` in `crates/clementine-primitives/src/lib.rs` handles (a decrypted key, a Winternitz preimage, a challenge-ack preimage) through a path reachable without any privileged role, and use it to spend a bridge UTXO?

## Target
- File/function: `crates/clementine-primitives/src/lib.rs` -> `fee_vb` (Primitive types shared across clementine crates)
- Entrypoint: an unauthenticated request or an on-chain observation -> `fee_vb`
- Attacker controls: the request parameters or the on-chain data that triggers disclosure; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: obtain material that unlocks a bridge UTXO
- Invariant to test: material `fee_vb` handles is never observable to a non-participant before its intended reveal
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: exercise the disclosure path unauthenticated and assert nothing usable is returned
