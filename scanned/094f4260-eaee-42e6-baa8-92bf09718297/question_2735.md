# Q2735: NEAR message publication and later proof consumption coupling emitter or factory binding mismatch

## Question
Can an unprivileged attacker submit a structurally valid proof to `public init/finalize/deploy/log flows across every chain adapter` whose payload points to one source chain while `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo` authenticates another because of the bridge publishes source events on one chain and later consumes proofs or signatures of those events on another chain using chain-specific adapters, violating `publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event`?

## Target
- File/function: `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo`
- Entrypoint: `public init/finalize/deploy/log flows across every chain adapter`
- Attacker controls: proof/message bytes, chain selection, and any supported chain among Ethereum, Base, Arbitrum, Polygon, BNB, Solana, Starknet, Bitcoin, and Zcash
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps.
- Invariant to test: publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees.
