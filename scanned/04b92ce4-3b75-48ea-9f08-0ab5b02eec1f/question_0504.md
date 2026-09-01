# Q0504: `is_compatible` and encoding/decoding round trips

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port supply a value whose encode/decode round trip through `is_compatible` in `core/src/compatibility.rs` is not the identity (endianness of an outpoint vout, hex/borsh/serde forms, truncation), so two components of the bridge disagree about the same protocol object?

## Target
- File/function: `core/src/compatibility.rs` -> `is_compatible` (This module contains the logic for checking compatibility between actors in the system)
- Entrypoint: a request or on-chain value that exercises the encoding -> `is_compatible`
- Attacker controls: the encoded bytes submitted; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: make two bridge components disagree about one object
- Invariant to test: encode-then-decode through `is_compatible` is the identity on the whole domain
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: property-test the round trip for `is_compatible` over adversarial values
