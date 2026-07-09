# Q2083: Starknet ETH-signature domain parser boundary or offset manipulation

## Question
Can an unprivileged attacker craft proof bytes for `public Starknet deploy/finalize entrypoints` that make `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` shift field boundaries, truncate payloads, or reinterpret trailing bytes because of recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator, violating `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields.
