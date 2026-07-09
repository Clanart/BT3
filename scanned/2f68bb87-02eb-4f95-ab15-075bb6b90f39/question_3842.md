# Q3842: NEAR message publication and later proof consumption coupling shared proof response reused across entrypoints

## Question
Can an unprivileged attacker obtain a valid verifier result for one public flow and reuse it in `public init/finalize/deploy/log flows across every chain adapter` because `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo` trusts the same response envelope under a different meaning, violating `publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event`?

## Target
- File/function: `README-supported Wormhole, EVM light-client, and MPC-backed flows across repo`
- Entrypoint: `public init/finalize/deploy/log flows across every chain adapter`
- Attacker controls: proof/message bytes, chain selection, and any supported chain among Ethereum, Base, Arbitrum, Polygon, BNB, Solana, Starknet, Bitcoin, and Zcash
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type.
- Invariant to test: publication and consumption must remain one-to-one across all supported chains so a valid event on one domain cannot be replayed or misparsed as another domain’s event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics.
