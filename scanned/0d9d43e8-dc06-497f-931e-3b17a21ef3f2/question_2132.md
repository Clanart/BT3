# Q2132: NEAR message publication and later proof consumption coupling missing chain or contract domain separation

## Question
Can an unprivileged attacker reuse a valid proof or signature from one chain, contract, or message domain in `public init/finalize/deploy/log flows across every chain adapter` because `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo` relies on the bridge publishes source events on one chain and later consumes proofs or signatures of those events on another chain using chain-specific adapters more narrowly than the true trust domain, violating `publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event`?

## Target
- File/function: `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo`
- Entrypoint: `public init/finalize/deploy/log flows across every chain adapter`
- Attacker controls: proof/message bytes, chain selection, and any supported chain among Ethereum, Base, Arbitrum, Polygon, BNB, Solana, Starknet, Bitcoin, and Zcash
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field.
- Invariant to test: publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance.
