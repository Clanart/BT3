# Q1925: Confuse replacement linkage in internal_create_watchtower_challenge

## Question
Can an unprivileged attacker shape the `old_move_txid` in `ReplacementDeposit` so `internal_create_watchtower_challenge` confuses replacement and non-replacement contexts, causing the emergency-stop transaction that should protect the same deposit to inherit the wrong history and violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/verifier.rs::internal_create_watchtower_challenge
- Entrypoint: auth-bypass attempt into gRPC `ClementineVerifier.InternalCreateWatchtowerChallenge`
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
