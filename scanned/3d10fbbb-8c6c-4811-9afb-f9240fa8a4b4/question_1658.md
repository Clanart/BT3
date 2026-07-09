# Q1658: NEAR message publication and later proof consumption coupling one inbound event spawns multiple outbound obligations via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public init/finalize/deploy/log flows across every chain adapter` and then replay or reorder later bind, deploy, or metadata-consumption step so that `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo` ends up accepting two inconsistent interpretations of the same economic event specifically around `one inbound event spawns multiple outbound obligations` under the bridge publishes source events on one chain and later consumes proofs or signatures of those events on another chain using chain-specific adapters, violating `publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event`?

## Target
- File/function: `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo`
- Entrypoint: `public init/finalize/deploy/log flows across every chain adapter`
- Attacker controls: proof/message bytes, chain selection, and any supported chain among Ethereum, Base, Arbitrum, Polygon, BNB, Solana, Starknet, Bitcoin, and Zcash
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
