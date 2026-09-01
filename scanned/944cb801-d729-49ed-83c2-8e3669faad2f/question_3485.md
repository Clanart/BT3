# Q3485: `host_journal_hash` panics where the protocol needs a defined outcome

## Question
Can an attacker supply on-chain data that forces `host_journal_hash` in `bridge-circuit-host/src/structs.rs` to panic for every possible honest prover input (malformed witness, oversized field, unparsable structure placed on chain), so no valid proof can ever be produced for a settlement that is otherwise legitimate and the corresponding vault becomes permanently unspendable?

## Target
- File/function: `bridge-circuit-host/src/structs.rs` -> `host_journal_hash`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees that becomes circuit input -> `host_journal_hash`
- Attacker controls: the shape of the on-chain data the circuit is obliged to ingest; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: make an honest, legitimate settlement unprovable forever
- Invariant to test: for every reachable on-chain state, some honest prover input makes `host_journal_hash` terminate without panicking
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: place the adversarial data on a regtest chain and assert a proof can still be produced
