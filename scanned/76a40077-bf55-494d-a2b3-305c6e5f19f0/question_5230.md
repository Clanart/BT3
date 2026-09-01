# Q5230: `new_mid_state` and header-chain continuity rules

## Question
Can a prover feed `new_mid_state` in `circuits-lib/src/bridge_circuit/merkle_tree.rs` a header sequence that satisfies its continuity, difficulty or timestamp checks while not being a real Bitcoin chain - a retarget boundary off by one, a timestamp median edge case, a `bits` value the circuit accepts but Bitcoin would not - so a cheap fabricated chain is accepted and used to prove inclusion?

## Target
- File/function: `circuits-lib/src/bridge_circuit/merkle_tree.rs` -> `new_mid_state` (This module implements a Bitcoin Merkle tree structure, which is used to verify the integrity of transactions in a block)
- Entrypoint: a fabricated header chain proof -> `new_mid_state`
- Attacker controls: the header sequence, its timestamps, bits and retarget boundaries; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: prove inclusion in a chain that consensus would reject
- Invariant to test: a header sequence accepted by `new_mid_state` == a sequence Bitcoin consensus would accept
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: feed boundary retarget/timestamp sequences and assert parity with consensus rules
