# Q2687: Starknet ETH-signature domain signature malleability or alternate recovery

## Question
Can an unprivileged attacker submit alternate signature encodings through `public Starknet deploy/finalize entrypoints` that `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` treats as authorizing the same or a different bridge action because of recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator, violating `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Target `v/r/s` normalization, ECDSA recovery semantics, and Ethereum-style signature handling on non-Ethereum chains.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Try low-s/high-s and alternate-`v` forms and assert that recovery either rejects them or yields one unique signer and one unique message.
