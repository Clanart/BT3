# Q4498: `handle_event` and the header chain the prover advances

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees feed `handle_event` in `core/src/states/event.rs` headers or proofs that advance the tracked chain onto a branch that is not the one bridge settlements are proved against (a low-work branch, a gap, a repeated header), so a later proof cannot be produced or proves the wrong branch?

## Target
- File/function: `core/src/states/event.rs` -> `handle_event`
- Entrypoint: Bitcoin headers observable by the entity, shaped by an unprivileged party paying only mining fees -> `handle_event`
- Attacker controls: which branch the attacker's transactions and blocks extend; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: steer the tracked chain away from the settlement branch
- Invariant to test: the tracked chain == the highest-work chain containing the bridge's confirmed transactions
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: feed competing branches and assert the tracked chain follows the highest work
