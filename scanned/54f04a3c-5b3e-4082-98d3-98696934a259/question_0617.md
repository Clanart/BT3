# Q617: Starknet ETH-signature domain state update before full validation at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet deploy/finalize entrypoints` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` violate `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants` in the `state update before full validation` attack class because recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
