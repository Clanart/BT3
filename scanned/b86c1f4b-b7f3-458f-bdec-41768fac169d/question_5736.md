# Q5736: `op_return_txout` and unvalidated protobuf-to-domain conversion

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator send a `DepositParams`/request field that survives conversion into the type `op_return_txout` in `core/src/builder/transaction/mod.rs` consumes - an address parsed with `assume_checked` for the wrong network, an out-of-range index, a non-canonical `XOnlyPublicKey`, an oversized Winternitz key - and reach signing with a value the rest of the protocol assumes is already validated?

## Target
- File/function: `core/src/builder/transaction/mod.rs` -> `op_return_txout` (This module provides the core logic for constructing, handling, and signing the various Bitcoin transactions)
- Entrypoint: aggregator gRPC request -> `rpc::parser` -> `op_return_txout`
- Attacker controls: every byte of the protobuf request; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: smuggle an unvalidated value into transaction construction or signing
- Invariant to test: every value reaching `op_return_txout` satisfies the invariants the parser is assumed to have enforced
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: fuzz the parser for `op_return_txout`'s inputs and assert malformed values are rejected before signing
