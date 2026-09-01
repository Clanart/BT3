# Q5976: `are_all_watchtowers_unique` and unvalidated protobuf-to-domain conversion

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator send a `DepositParams`/request field that survives conversion into the type `are_all_watchtowers_unique` in `core/src/deposit.rs` consumes - an address parsed with `assume_checked` for the wrong network, an out-of-range index, a non-canonical `XOnlyPublicKey`, an oversized Winternitz key - and reach signing with a value the rest of the protocol assumes is already validated?

## Target
- File/function: `core/src/deposit.rs` -> `are_all_watchtowers_unique` (This module defines the data structures related to Citrea deposits in the Clementine bridge)
- Entrypoint: aggregator gRPC request -> `rpc::parser` -> `are_all_watchtowers_unique`
- Attacker controls: every byte of the protobuf request; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: smuggle an unvalidated value into transaction construction or signing
- Invariant to test: every value reaching `are_all_watchtowers_unique` satisfies the invariants the parser is assumed to have enforced
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: fuzz the parser for `are_all_watchtowers_unique`'s inputs and assert malformed values are rejected before signing
