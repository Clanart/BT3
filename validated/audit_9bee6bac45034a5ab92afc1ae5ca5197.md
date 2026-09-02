### Title
`deposit_constant`/`journal_hash` never commit to the payout transaction's output value distribution, letting an operator pre-generate one bridge-circuit proof valid for many different payout amounts - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
`deposit_constant()` in `circuits-lib/src/bridge_circuit/mod.rs` hashes only `move_txid`, a digest of watchtower pubkeys, `operator_xonlypk`, `watchtower_challenge_connector_start_idx`, `round_txid`, `kickoff_round_vout`, and `genesis_state_hash`. `journal_hash()` additionally folds in `payout_tx_blockhash`, `latest_blockhash`, and `challenge_sending_watchtowers`. Neither function, nor any check in `bridge_circuit()`, binds the committed output to the payout transaction's txid or to the amounts/outputs actually paid to the withdrawer.

### Finding Description
Binding claimed by the protocol: `deposit_constant`/`journal_hash` committed == a value uniquely tied to *one* specific payout (a specific amount paid to the withdrawer). Actual binding realized by the code: `deposit_constant` == `SHA256(move_txid || watchtower_pubkeys_digest || operator_xonlypk || watchtower_challenge_connector_start_idx || round_txid || kickoff_round_vout || genesis_state_hash)` [1](#0-0) , and `journal_hash` further mixes in only `payout_tx_blockhash`, `latest_blockhash`, and `challenge_sending_watchtowers` [2](#0-1) . None of these fields depend on the payout transaction's txid, its output count, or the value of any output paid to the withdrawer.

Within `bridge_circuit()`, the only cross-checks tying the payout transaction to the deposit are: (1) SPV inclusion of `input.payout_spv.transaction` in the claimed chain [3](#0-2) , and (2) that the **input** at `payout_input_index` of that transaction spends the exact `(txid, vout)` recorded by the storage proof for the withdrawal outpoint [4](#0-3) . There is no assertion anywhere in this function that inspects `input.payout_spv.transaction.output` to confirm any specific output amount was actually delivered to the withdrawer's script. As long as an operator constructs several payout transactions that (a) spend the same previous-output at the same input index, (b) carry an identical first OP_RETURN output (same `operator_xonlypk`), and (c) are built against the same `kickoff_tx`/`round_txid`/`kickoff_round_vout`/watchtower set, all of them yield the identical `deposit_constant`, and, once mined in the same block with the same watchtower challenge bitmap, the identical `journal_hash`. The circuit's zk output is therefore fungible with respect to how the outputs of the payout transaction actually distribute value.

None of the existing guards close this gap: `Verifier::is_deposit_valid`, `verify_storage_proofs`, and `SPV::verify` only check that the referenced previous-output (the withdrawal outpoint) is spent by the designated input; they never inspect or hash the payout transaction's own outputs. `total_work_and_watchtower_flags`/watchtower-challenge verification is orthogonal (it verifies watchtower disproof attempts, not payout content). No database uniqueness constraint or presigned-transaction-graph rule constrains the *outputs* of the freely-broadcast payout transaction, since the payout transaction is not part of the N-of-N presigned graph.

### Impact Explanation
Because the proof (`journal_hash`) is agnostic to the payout transaction's value distribution, an operator can, after generating (or having generated) a valid proof for a "template" payout transaction, choose to broadcast a variant that pays the withdrawer less than owed (or nothing) while retaining the same proof validity conditions with respect to `deposit_constant`. This risks "an operator reimbursed for a payout it never funded" — the Critical impact category explicitly listed in scope — because reimbursement logic that trusts the circuit's committed `deposit_constant`/`journal_hash` as proof that a specific, correctly-funded payout occurred cannot actually distinguish a full payout from a short-paid or empty one from the committed hash alone. This is repeatable per-deposit/per-operator since the derivation is purely a function of metadata that is independent of the amount field.

### Likelihood Explanation
Exploitability depends on whether any component outside the bridge circuit (e.g., a Bitcoin script condition on the previous-output being spent, or a downstream on-chain check against the payout transaction's outputs) enforces the payout amount. I was not able to fully confirm within the available context whether such an external enforcement exists (e.g., in `core/src/verifier.rs`'s use of `deposit_constant`, `core/src/builder/transaction/creator.rs`, or the Citrea bridge contract's withdrawal bookkeeping in `core/src/citrea.rs`) that would independently constrain the payout transaction's output amounts before reimbursement is granted. Given the size/index limitations that truncated my reading of `circuits-lib/src/bridge_circuit/mod.rs` and prevented full review of `core/src/verifier.rs` and `core/src/builder/transaction/creator.rs`, I could not verify with certainty whether an amount check exists in the operator/verifier flow that consumes this proof and rejects mismatched payouts before crediting reimbursement.

### Recommendation
Include a binding commitment to the payout transaction's economically relevant outputs (e.g., the withdrawer's output script and value, or the full payout txid) inside `deposit_constant` or `journal_hash`, so that the zk proof is only valid for the exact payout transaction that was actually broadcast and funded, not for any transaction sharing the same input/OP_RETURN metadata.

### Proof of Concept
Not fully demonstrable from the indexed context alone due to uncertainty about downstream amount-verification logic (see Likelihood Explanation); a `cargo test` in `circuits-lib/src/bridge_circuit` computing `deposit_constant(...)` for two `CircuitTransaction`s that share `operator_xonlypk`, `round_txid`, `kickoff_round_vout`, and `watchtower_pubkeys` but differ in their non-OP_RETURN output values would be needed, asserting `deposit_constant_a == deposit_constant_b`, to conclusively confirm exploitability end-to-end together with verification of whether `core/src/verifier.rs` or the Citrea contract logic independently rejects the under-funded variant.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L162-169)
```rust
    let mmr = input.hcp.chain_state.block_hashes_mmr.clone();

    if !input.payout_spv.verify(mmr) {
        panic!(
            "Invalid SPV proof for txid: {}",
            input.payout_spv.transaction.compute_txid()
        );
    }
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L186-204)
```rust
    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L634-663)
```rust
pub fn deposit_constant(
    operator_xonlypk: [u8; 32],
    watchtower_challenge_connector_start_idx: u32,
    watchtower_pubkeys: &[[u8; 32]],
    move_txid: [u8; 32],
    round_txid: [u8; 32],
    kickoff_round_vout: u32,
    genesis_state_hash: [u8; 32],
) -> DepositConstant {
    // pubkeys are 32 bytes long
    let pubkey_concat = watchtower_pubkeys
        .iter()
        .flat_map(|pubkey| pubkey.to_vec())
        .collect::<Vec<u8>>();

    let watchtower_pubkeys_digest: [u8; 32] = Sha256::digest(&pubkey_concat).into();

    let pre_deposit_constant = [
        &move_txid,
        &watchtower_pubkeys_digest,
        &operator_xonlypk,
        &watchtower_challenge_connector_start_idx.to_be_bytes()[..],
        &round_txid,
        &kickoff_round_vout.to_be_bytes()[..],
        &genesis_state_hash,
    ]
    .concat();

    DepositConstant(Sha256::digest(&pre_deposit_constant).into())
}
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L665-684)
```rust
pub fn journal_hash(
    payout_tx_blockhash: PayoutTxBlockhash,
    latest_blockhash: LatestBlockhash,
    challenge_sending_watchtowers: ChallengeSendingWatchtowers,
    deposit_constant: DepositConstant,
) -> blake3::Hash {
    let concatenated_data = [
        payout_tx_blockhash.0,
        latest_blockhash.0,
        challenge_sending_watchtowers.0,
    ]
    .concat();

    let binding = blake3::hash(&concatenated_data);
    let hash_bytes = binding.as_bytes();

    let concat_journal = [deposit_constant.0, *hash_bytes].concat();

    blake3::hash(&concat_journal)
}
```
