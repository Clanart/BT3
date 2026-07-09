# Q3266: Starknet ETH-signature domain optional-field encoding ambiguity

## Question
Can an unprivileged attacker exploit empty-versus-present optional fields in proofs reaching `public Starknet deploy/finalize entrypoints` so that `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` authenticates one payload but downstream logic interprets another because of recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator, violating `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior.
