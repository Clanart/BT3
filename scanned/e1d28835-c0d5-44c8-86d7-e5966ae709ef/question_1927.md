# Q1927: Starknet ETH-signature domain missing chain or contract domain separation at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet deploy/finalize entrypoints` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` violate `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants` in the `missing chain or contract domain separation` attack class because recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
