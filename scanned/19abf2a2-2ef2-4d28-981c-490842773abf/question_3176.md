# Q3176: NEAR message publication and later proof consumption coupling emitter or factory binding mismatch at boundary values

## Question
Can an unprivileged attacker trigger `public init/finalize/deploy/log flows across every chain adapter` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo` violate `publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event` in the `emitter or factory binding mismatch` attack class because the bridge publishes source events on one chain and later consumes proofs or signatures of those events on another chain using chain-specific adapters becomes fragile at those edges?

## Target
- File/function: `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo`
- Entrypoint: `public init/finalize/deploy/log flows across every chain adapter`
- Attacker controls: proof/message bytes, chain selection, and any supported chain among Ethereum, Base, Arbitrum, Polygon, BNB, Solana, Starknet, Bitcoin, and Zcash
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
