# Q2724: NEAR factory map mutation assumptions same remote asset deployable via multiple proof paths

## Question
Can an unprivileged attacker use `public proof-consuming flows after a valid source-chain event exists` to deploy or bind the same remote asset through a second path because `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks` authenticates uses a single configured factory per chain to authenticate proof-derived events across init, finalize, deploy, metadata, and fee claim flows differently than another deploy path, violating `source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::factories usage in all proof callbacks`
- Entrypoint: `public proof-consuming flows after a valid source-chain event exists`
- Attacker controls: chain kind, emitter address, and any state race across token deployment and finalization
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths.
- Invariant to test: source authentication must never accept a valid event from the wrong contract instance, retired factory, or mismatched chain domain
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation.
