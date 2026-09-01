# Q0088: `default_tx_sender_limits` and handling of secret or pre-reveal material

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port obtain, or cause the early disclosure of, material `default_tx_sender_limits` in `core/src/config/mod.rs` handles (a decrypted key, a Winternitz preimage, a challenge-ack preimage) through a path reachable without any privileged role, and use it to spend a bridge UTXO?

## Target
- File/function: `core/src/config/mod.rs` -> `default_tx_sender_limits` (This module defines configuration options)
- Entrypoint: an unauthenticated request or an on-chain observation -> `default_tx_sender_limits`
- Attacker controls: the request parameters or the on-chain data that triggers disclosure; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: obtain material that unlocks a bridge UTXO
- Invariant to test: material `default_tx_sender_limits` handles is never observable to a non-participant before its intended reveal
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: exercise the disclosure path unauthenticated and assert nothing usable is returned
