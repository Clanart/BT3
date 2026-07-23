# Q1646: Exploit witness/annex edge cases in verify_watchtower_challenges

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `verify_watchtower_challenges` verifies a different Bitcoin statement than later settlement relies on, corrupting the SPV inclusion result for the payout transaction and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/bridge_circuit/mod.rs::verify_watchtower_challenges
- Entrypoint: broadcast a crafted Bitcoin kickoff/challenge/assert/disprove transaction that later reaches sync and verifier logic
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
