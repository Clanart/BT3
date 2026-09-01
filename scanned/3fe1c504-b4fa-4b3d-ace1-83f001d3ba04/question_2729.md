# Q2729: `get_params` and Winternitz public-key binding

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator cause `get_params` in `core/src/actor.rs` to accept or derive Winternitz public keys (`generate_kickoff_winternitz_pubkeys`, `generate_assert_winternitz_pubkeys`, `WinternitzDerivationPath`) whose derivation path is not uniquely bound to (deposit, round, kickoff), so one committed value can be replayed into a different kickoff and satisfy a check it should fail?

## Target
- File/function: `core/src/actor.rs` -> `get_params`
- Entrypoint: aggregator `NewDeposit` / `SetOperatorKeys` path -> `get_params`
- Attacker controls: deposit-time indices that enter the derivation path; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: replay a Winternitz commitment across contexts
- Invariant to test: a Winternitz public key == a unique (deposit, round index, kickoff index) triple
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: assert derivation paths collide for no two distinct index triples
