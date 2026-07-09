# Q3671: Starknet ETH-signature domain optional-field encoding ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet deploy/finalize entrypoints` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` violate `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants` in the `optional-field encoding ambiguity` attack class because recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
