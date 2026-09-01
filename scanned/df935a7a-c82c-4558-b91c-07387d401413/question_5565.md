# Q5565: `to_secp_kp` and the security-council multisig script

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator supply a `SecurityCouncil` string (via `FromStr`, duplicated keys, threshold equal to zero, or a key ordering that changes the script) that `to_secp_kp` in `core/src/musig2.rs` builds into the vault's alternative spend path, producing a vault spendable by a script weaker than the configured m-of-n?

## Target
- File/function: `core/src/musig2.rs` -> `to_secp_kp` (Helper functions for the MuSig2 signature scheme)
- Entrypoint: aggregator `NewDeposit` `security_council` field -> `Multisig::from_security_council` -> `to_secp_kp`
- Attacker controls: the `SecurityCouncil` encoding submitted with the deposit; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: weaken the vault's secondary spend path to something the attacker can satisfy
- Invariant to test: the multisig script built into a vault == the configured `security_council` threshold and key set
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: parse adversarial `SecurityCouncil` strings and assert the built script matches the config exactly
