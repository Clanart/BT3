# Q1282: Starknet ETH-signature domain proof kind or event class confusion at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet deploy/finalize entrypoints` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` violate `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants` in the `proof kind or event class confusion` attack class because recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
