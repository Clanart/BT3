# Q1712: `anchor_amount` and encoding/decoding round trips

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port supply a value whose encode/decode round trip through `anchor_amount` in `crates/clementine-config/src/protocol.rs` is not the identity (endianness of an outpoint vout, hex/borsh/serde forms, truncation), so two components of the bridge disagree about the same protocol object?

## Target
- File/function: `crates/clementine-config/src/protocol.rs` -> `anchor_amount` (Protocol parameters for the clementine bridge)
- Entrypoint: a request or on-chain value that exercises the encoding -> `anchor_amount`
- Attacker controls: the encoded bytes submitted; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: make two bridge components disagree about one object
- Invariant to test: encode-then-decode through `anchor_amount` is the identity on the whole domain
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: property-test the round trip for `anchor_amount` over adversarial values
