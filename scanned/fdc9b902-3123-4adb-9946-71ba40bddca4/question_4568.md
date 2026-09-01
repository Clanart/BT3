# Q4568: `send_disprove` and irrevocable commitments

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees make `send_disprove` in `core/src/states/kickoff.rs` publish an irrevocable commitment (a WOTS-committed value, a one-time connector spend) derived from data the attacker can still change, so the commitment becomes false and the honest path is lost?

## Target
- File/function: `core/src/states/kickoff.rs` -> `send_disprove`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `send_disprove`
- Attacker controls: the data the commitment is derived from and its replaceability; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: poison an irrevocable commitment before it is safe to make
- Invariant to test: any value committed irrevocably is derived only from data that can no longer change
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: change the underlying data after the commitment and assert the honest path still closes
