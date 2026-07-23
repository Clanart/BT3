# Q3564: Drive state split inside sighash

## Question
Can an unprivileged attacker use broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation with crafted Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `sighash` updates one canonical value while another subsystem retains the older one for the same event, corrupting the L1 block hash carried from the light-client proof into bridge validation and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/bridge_circuit/mod.rs::sighash
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: update one canonical value while another subsystem keeps the old one for the same event via Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
