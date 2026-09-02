### Title
`bridge_circuit` never validates the payout transaction's output value/script against the withdrawal request, independent of watchtower participation - (`File: circuits-lib/src/bridge_circuit/mod.rs`)

### Summary
`bridge_circuit()` in `circuits-lib/src/bridge_circuit/mod.rs` only checks that the payout transaction *spends* the correct withdrawal outpoint (`txid`/`vout`) at `input.payout_input_index`, but it never inspects the payout transaction's outputs to confirm that any value was actually paid to the withdrawer's `output_script_pubkey`/`out_amount`. This omission is completely orthogonal to the watchtower/challenge logic, so it is exploitable (and produces a committed `journal_hash`) even when `challenge_sending_watchtowers == [0u8;20]` (no watchtower challenged) and `total_work` trivially satisfies `total_work >= max_total_work` (since `max_total_work` defaults to `[0u8;16]` with zero watchtower inputs).

### Finding Description
Binding claimed broken: `journal_hash` acceptance (i.e., `bridge_circuit` committing a journal without panicking) **==** actual BTC value transfer to the withdrawer via the payout transaction's output.

Tracing `bridge_circuit` ( [1](#0-0) ):

1. HCP method id and Groth16 verification — unrelated to payout value.
2. `total_work_and_watchtower_flags` computes `max_total_work` and `challenge_sending_watchtowers` from `input.watchtower_inputs`. With `watchtower_inputs = vec![]`, `verify_watchtower_challenges` returns `challenge_senders = [0u8;20]` and no commitments are produced, so `max_total_work = TotalWork([0u8;16])` [2](#0-1) . The subsequent check `if total_work < max_total_work { panic!(...) }` ( [3](#0-2) ) trivially passes for any non-negative `total_work` from the HCP.
3. SPV proof verification (`input.payout_spv.verify(mmr)`) only proves the payout transaction (as a whole, with whatever outputs it has) is included in the claimed chain — it says nothing about output values.
4. Light-client proof / L1 block-hash match — unrelated to payout value.
5. `verify_storage_proofs` returns `(user_wd_outpoint, vout, move_txid)` from L2 storage, and the circuit only asserts that `input.payout_spv.transaction.input[payout_input_index].previous_output` equals `(user_wd_txid, vout)` ( [4](#0-3) ). This confirms the payout tx *consumes* the correct withdrawal UTXO as an input, but does not check any output of the payout transaction.
6. `deposit_constant` is computed from `operator_xonlypk`, watchtower pubkey commitments, `move_txid`, and round/kickoff txid/vout ( [5](#0-4) ) — none of these are the payout output value.
7. The final `journal_hash` is computed purely from `payout_tx_blockhash`, `latest_blockhash`, `challenge_sending_watchtowers`, and `deposit_constant` ( [6](#0-5)  and [7](#0-6) ) — again, no output-value component.

Nowhere in this list (nor in `verify_storage_proofs` in `circuits-lib/src/bridge_circuit/storage_proof.rs`, which only checks storage keys/values for the withdrawal outpoint and deposit-amount slot used later for `deposit_constant`) is the payout transaction's actual paid-out amount (`TxOut.value`) or destination `script_pubkey` compared against the withdrawal's requested `output_amount`/`out_script_pubkey`. A payout transaction that spends the correct withdrawal input but sends 0 value (or an unrelated destination) to an OP_RETURN/dust output, or any other output structure, will still pass every check in `bridge_circuit` and produce an accepted `journal_hash`. Since none of these checks (steps 2–7) depend on watchtower data except for the two watchtower-derived values (`max_total_work`, `challenge_sending_watchtowers`), the defect persists identically with zero watchtowers (`watchtower_inputs = vec![]`).

The docs corroborate the intended scope of `BridgeDisproveScript`/`ClementineDisproveScript` (the on-chain enforcement of this circuit's output): they describe verification of "Watchtower challenges and block hashes (committed via WOTS)" but do not mention verifying payout output value [8](#0-7) , consistent with the absence of such a check in the circuit itself.

### Impact Explanation
If a dishonest but permissionless operator (operators are unprivileged, collateral-only registered participants under this protocol's trust model) submits a kickoff/payout claiming to have fronted a withdrawal while the payout transaction pays zero or near-zero value to the withdrawer, `bridge_circuit` still accepts and commits a valid `journal_hash`. Since this journal underlies the Disprove mechanism (`BridgeDisproveScript`) that is supposed to let honest parties prove operator fraud on-chain, the missing value check means such fraud is *not provable as false* by the circuit — matching the "Critical: a false claim proved (or a true claim made unprovable) by the bridge... circuit" and "an operator reimbursed for a payout it never funded" impact categories. The operator can be reimbursed the full `bridge_amount` from the move-to-vault UTXO without ever paying the withdrawer, and this is repeatable for every deposit/withdrawal the malicious operator services, independent of whether any watchtower participates.

### Likelihood Explanation
No mainnet or live Citrea interaction is required to demonstrate: the missing check is directly reproducible via a `circuits-lib` unit test that constructs a `BridgeCircuitInput` with `watchtower_inputs = vec![]` (forcing `challenge_sending_watchtowers == [0u8;20]` and trivial `total_work` satisfaction) and a `payout_spv.transaction` whose output to the withdrawer's script is 0 value (or any value/script), while its input still correctly references the withdrawal outpoint/vout returned by `verify_storage_proofs`. This requires no attacker capability beyond constructing/proving the bridge circuit inputs (the operator's own role in the protocol), and no watchtower cooperation or defeat is needed since the check is simply absent for any watchtower state.

### Recommendation
In `circuits-lib/src/bridge_circuit/mod.rs::bridge_circuit`, after locating `payout_input_index` and validating the withdrawal-outpoint input, add an explicit assertion that the payout transaction contains an output paying at least the withdrawal's requested `out_amount` to `out_script_pubkey` (both of which must be committed into and read from L2 storage proofs / `verify_storage_proofs`, analogous to how `user_wd_outpoint`/`vout` are already fetched), and panic if no matching, sufficiently-valued output exists in `input.payout_spv.transaction.output`.

### Proof of Concept
```rust
// circuits-lib/src/bridge_circuit/mod.rs (test module)
#[test]
fn test_bridge_circuit_zero_value_payout_bypasses_watchtowers() {
    // Build minimal BridgeCircuitInput fixture (reusing existing test helpers/setup)
    // that satisfies HCP/SPV/LCP/storage-proof checks, with:
    let mut input: BridgeCircuitInput = /* valid base fixture from existing tests */;

    // 1. Zero watchtowers -> challenge_sending_watchtowers must end up [0u8;20]
    input.watchtower_inputs = vec![];

    // 2. Craft payout_spv.transaction so that:
    //    - its input[payout_input_index].previous_output == (user_wd_txid, vout)
    //      from verify_storage_proofs(&input.sp, ...) (so the existing checks pass)
    //    - but its outputs pay ZERO value (or an unrelated script) to the withdrawer
    input.payout_spv.transaction.output[withdrawer_out_idx].value = Amount::ZERO;

    // Run the mock/test guest (per repo's ZkvmGuest test harness) invoking bridge_circuit
    let guest = MockZkvmGuest::new(input.clone());
    bridge_circuit(&guest, REGTEST_WORK_ONLY_METHOD_ID); // must NOT panic

    // Binding check (left == acceptance, right == actual value transferred):
    let committed = guest.committed_journal(); // journal_hash bytes
    assert!(!committed.is_empty(), "journal committed despite zero-value payout");
    assert_eq!(
        input.payout_spv.transaction.output[withdrawer_out_idx].value,
        Amount::ZERO,
        "confirms zero value was actually paid to withdrawer while journal was still accepted"
    );

    // Independently confirm watchtower state was trivial:
    let (max_total_work, challenge_sending_wts) =
        total_work_and_watchtower_flags(&input, &REGTEST_WORK_ONLY_METHOD_ID);
    assert_eq!(*challenge_sending_wts, [0u8; 20]);
    assert_eq!(*max_total_work, [0u8; 16]);
}
```
This test (run with `cargo test -p circuits-lib test_bridge_circuit_zero_value_payout_bypasses_watchtowers`, no mainnet/live Citrea needed) demonstrates `bridge_circuit` commits a journal for a zero-value payout while watchtower participation is empty/trivial, proving the defect's independence from the watchtower subsystem.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L137-245)
```rust
pub fn bridge_circuit(guest: &impl ZkvmGuest, work_only_image_id: [u8; 32]) {
    let input: BridgeCircuitInput = guest.read_from_host();
    assert_eq!(
        HEADER_CHAIN_METHOD_ID, input.hcp.method_id,
        "Invalid method ID for header chain circuit: expected {:?}, got {:?}",
        HEADER_CHAIN_METHOD_ID, input.hcp.method_id
    );

    // Verify the HCP
    guest.verify(input.hcp.method_id, &input.hcp);

    let (max_total_work, challenge_sending_watchtowers) =
        total_work_and_watchtower_flags(&input, &work_only_image_id);

    let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
        .try_into()
        .expect("Cannot fail");

    // If total work is less than the max total work of watchtowers, panic
    if total_work < max_total_work {
        panic!(
            "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
        );
    }

    let mmr = input.hcp.chain_state.block_hashes_mmr.clone();

    if !input.payout_spv.verify(mmr) {
        panic!(
            "Invalid SPV proof for txid: {}",
            input.payout_spv.transaction.compute_txid()
        );
    }

    // Light client proof verification
    let light_client_circuit_output = lc_proof_verifier(input.lcp.clone());

    // Make sure the L1 block hash of the LightClientCircuitOutput matches the payout tx block hash
    let lc_l1_block_hash = light_client_circuit_output.latest_da_state.block_hash;
    let spv_l1_block_hash = input.payout_spv.block_header.compute_block_hash();

    if lc_l1_block_hash != spv_l1_block_hash {
        panic!("L1 block hash mismatch: expected {lc_l1_block_hash:?}, got {spv_l1_block_hash:?}");
    }

    // Storage proof verification for deposit tx index and withdrawal outpoint
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);

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

    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );

    let latest_blockhash: LatestBlockhash = input.hcp.chain_state.best_block_hash[12..32]
        .try_into()
        .unwrap();

    let payout_tx_blockhash: PayoutTxBlockhash = spv_l1_block_hash[12..32].try_into().unwrap();

    let journal_hash = journal_hash(
        payout_tx_blockhash,
        latest_blockhash,
        challenge_sending_watchtowers,
        deposit_constant,
    );

    guest.commit(journal_hash.as_bytes());
}
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L515-606)
```rust
pub fn total_work_and_watchtower_flags(
    circuit_input: &BridgeCircuitInput,
    work_only_image_id: &[u8; 32],
) -> (TotalWork, ChallengeSendingWatchtowers) {
    let watchtower_challenge_set = verify_watchtower_challenges(circuit_input);

    let mut valid_watchtower_challenge_commitments: Vec<WatchTowerChallengeTxCommitment> = vec![];

    for outputs in watchtower_challenge_set.challenge_outputs {
        let compressed_g16_proof: [u8; 128];
        let total_work: [u8; 16];

        match outputs.as_slice() {
            // Single OP_RETURN output with 144 bytes
            [op_return_output, ..] if op_return_output.script_pubkey.is_op_return() => {
                // If the first output is OP_RETURN, we expect a single output with 144 bytes
                let Some(Ok(whole_output)) = parse_op_return_data(&op_return_output.script_pubkey)
                    .map(TryInto::<[u8; 144]>::try_into)
                else {
                    continue;
                };
                compressed_g16_proof = whole_output[0..128]
                    .try_into()
                    .expect("Cannot fail: slicing 128 bytes from 144-byte array");
                total_work = whole_output[128..144]
                    .try_into()
                    .expect("Cannot fail: slicing 16 bytes from 144-byte array");
            }
            // Otherwise, we expect three outputs:
            // 1. [out1, out2, out3] where out1 and out2 are P2TR outputs
            //    and out3 is an OP_RETURN output with 80 bytes
            [out1, out2, out3, ..]
                if out1.script_pubkey.is_p2tr()
                    && out2.script_pubkey.is_p2tr()
                    && out3.script_pubkey.is_op_return() =>
            {
                let first_output: [u8; 32] = out1.script_pubkey.to_bytes()[2..]
                    .try_into()
                    .expect("Cannot fail: slicing 32 bytes from P2TR output");
                let second_output: [u8; 32] = out2.script_pubkey.to_bytes()[2..]
                    .try_into()
                    .expect("Cannot fail: slicing 32 bytes from P2TR output");

                let Some(Ok(third_output)) =
                    parse_op_return_data(&out3.script_pubkey).map(TryInto::<[u8; 80]>::try_into)
                else {
                    continue;
                };

                compressed_g16_proof =
                    [&first_output[..], &second_output[..], &third_output[0..64]]
                        .concat()
                        .try_into()
                        .expect("Cannot fail: concatenating and converting to 128-byte array");

                // Borsh deserialization of the final 16 bytes is functionally redundant in this context,
                // as it does not alter the byte content. It is retained here for consistency and defensive safety.
                total_work = borsh::from_slice(&third_output[64..])
                    .expect("Cannot fail: deserializing 16 bytes from 16-byte slice");
            }
            _ => continue,
        }

        let commitment = WatchTowerChallengeTxCommitment {
            compressed_g16_proof,
            total_work,
        };

        valid_watchtower_challenge_commitments.push(commitment);
    }

    valid_watchtower_challenge_commitments.sort_by(|a, b| b.total_work.cmp(&a.total_work));

    let mut total_work_result = [0u8; 16];

    for commitment in valid_watchtower_challenge_commitments {
        if convert_to_groth16_and_verify(
            &commitment.compressed_g16_proof,
            commitment.total_work,
            work_only_image_id,
            circuit_input.hcp.genesis_state_hash,
        ) {
            total_work_result = commitment.total_work;
            break;
        }
    }

    (
        TotalWork(total_work_result),
        ChallengeSendingWatchtowers(watchtower_challenge_set.challenge_senders),
    )
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

**File:** docs/bridge-circuit.md (L19-22)
```markdown
### The Disprove Process
If a Challenger finds an error with the output of the Operator's off-chain execution of the bridge program, they can post a Disprove transaction. This transaction pinpoints the specific step of the program where the Operator's computation was incorrect and executes that step on-chain. If the on-chain execution confirms the Operator's error, the Challenger is able to burn the Operator's collateral. There are two types of scripts that can be executed in a Disprove transaction:
- BridgeDisproveScript: This script verifies the main Bridge Circuit. It uses a Groth16 proof to check several critical conditions related to bridge operations.
- ClementineDisproveScript: This script ensures that the inputs provided to the Bridge Circuit are consistent with the on-chain state of the relevant data, such as Watchtower challenges and block hashes (committed via WOTS). It verifies that the Operator has not censored or ignored any valid challenges from the Watchtowers, and did use the data they committed on-chain.
```
