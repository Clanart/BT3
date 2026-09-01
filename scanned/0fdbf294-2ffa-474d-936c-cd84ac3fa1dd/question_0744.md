# Q0744: `try_join_all_combine_errors` and encoding/decoding round trips

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port supply a value whose encode/decode round trip through `try_join_all_combine_errors` in `core/src/utils.rs` is not the identity (endianness of an outpoint vout, hex/borsh/serde forms, truncation), so two components of the bridge disagree about the same protocol object?

## Target
- File/function: `core/src/utils.rs` -> `try_join_all_combine_errors`
- Entrypoint: a request or on-chain value that exercises the encoding -> `try_join_all_combine_errors`
- Attacker controls: the encoded bytes submitted; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: make two bridge components disagree about one object
- Invariant to test: encode-then-decode through `try_join_all_combine_errors` is the identity on the whole domain
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: property-test the round trip for `try_join_all_combine_errors` over adversarial values
