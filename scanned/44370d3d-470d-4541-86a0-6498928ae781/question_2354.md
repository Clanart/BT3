# Q2354: `unwrap_all_prevouts` and the set of challenges the circuit is obliged to honour

## Question
Can an attacker place, omit or reshape on-chain data so that the challenge/flag set `unwrap_all_prevouts` in `circuits-lib/src/bridge_circuit/mod.rs` derives differs from the set that actually exists on chain - a challenge that is ignored, or a flag set for one that was never sent - so the proof's view of the dispute diverges from Bitcoin's?

## Target
- File/function: `circuits-lib/src/bridge_circuit/mod.rs` -> `unwrap_all_prevouts` (This module implements the Bridge Circuit for Clementine protocol)
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `unwrap_all_prevouts`
- Attacker controls: the transactions placed on chain that the circuit must enumerate; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: make the circuit's dispute view diverge from the chain's
- Invariant to test: the challenge flags the circuit commits == the challenges actually confirmed on chain
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: assert the derived flag set equals the on-chain set for adversarial placements
