# Q2539: Starknet ETH-signature domain parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet deploy/finalize entrypoints` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` violate `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants` in the `parser boundary or offset manipulation` attack class because recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
