# Q4321: `send_operator_asserts_if_ready` and one-time connector outputs

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees cause a connector output tracked by `send_operator_asserts_if_ready` in `core/src/states/kickoff.rs` to be consumed, burned or marked used without the corresponding protocol stage completing, so the deposit it belongs to loses its path to settlement?

## Target
- File/function: `core/src/states/kickoff.rs` -> `send_operator_asserts_if_ready`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `send_operator_asserts_if_ready`
- Attacker controls: the spend of the connector and its timing; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: burn the connector a legitimate settlement needed
- Invariant to test: a connector is marked used exactly when the stage it authorises actually completed
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: spend/observe the connector adversarially and assert bookkeeping matches the chain
