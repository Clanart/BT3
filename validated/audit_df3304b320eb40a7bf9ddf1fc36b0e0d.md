### Title
Bridge circuit accepts a tie in cumulative work instead of requiring the operator's chain to strictly outweigh a watchtower's challenge - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
The bridge circuit's proof-of-work comparison, which is supposed to prove the operator followed the canonical (heaviest) chain, uses a non-strict `<` comparison instead of the documented strict "greater than" requirement, letting an operator whose claimed chain merely *ties* a watchtower-proven competing chain still produce a valid Groth16 proof of innocence.

### Finding Description
The doc comment for `bridge_circuit` explicitly states the intended rule:
"Asserts that the Operator's `total_work` from their HCP is greater than the `max_total_work` from the Watchtowers" and "If `max_total_work` given by watchtowers is greater than `hcp.chain_state.total_work`" it panics. [1](#0-0) 

The actual implementation only panics on strict inequality in the other direction, meaning equal work is treated as acceptable: [2](#0-1) 

```rust
let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
    .try_into()
    .expect("Cannot fail");

// If total work is less than the max total work of watchtowers, panic
if total_work < max_total_work {
    panic!(
        "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
    );
}
```

This mirrors the SEDA-protocol bug class: a threshold comparison meant to be strict (`>`) is implemented as its non-strict complement (`>=`/allowing equality), causing the wrong side of a binary outcome (accepted vs rejected) to be selected exactly at the boundary.

In the bridge circuit's context, `total_work` (the operator's own recursively-proven `hcp.chain_state.total_work`) and `max_total_work` (the highest cumulative work proven by a valid watchtower Work-Only Proof, selected in `total_work_and_watchtower_flags`) are compared to decide whether the operator followed the canonical Bitcoin chain when producing their payout SPV proof. [3](#0-2) 

Bitcoin's per-block work is a pure function of the target/bits at that height, not of the actual block hash chosen; therefore any two same-length chains built within an unchanged difficulty period accumulate identical cumulative work regardless of which blocks were selected. This means an operator can construct a chain that is not the canonical one that watchtowers observed, yet has cumulative work exactly equal to (rather than less than) the watchtower-proven chain. Under the current `<` check, this tie does not panic, so the circuit proceeds to accept the operator's SPV proof of the payout transaction, generate `deposit_constant`/`journal_hash`, and commit a valid Groth16 proof — exactly the "false circuit claim proved" scenario the watchtower/disprove mechanism (documented in `docs/bridge-circuit.md`) is meant to prevent. [4](#0-3) 

### Impact Explanation
If the operator can obtain a tie in cumulative work against a watchtower's proven alternative chain, the operator can generate a valid, on-chain-verifiable Groth16 proof for a payout SPV rooted in a non-canonical (or at least non-strictly-dominant) chain state. Since the whole point of the watchtower challenge/work comparison is to force the operator to prove they followed the heaviest chain, a tie being accepted breaks the intended custody binding: "an operator's claimed chain state is proved valid" should equal "operator's chain is strictly heavier than any proven competing chain," but the code allows equality. This falls under the Critical impact category "a false circuit claim proved," and downstream could let an operator be reimbursed via `reimburse_tx`/kickoff flow for a payout whose validity should have been successfully disputed by a watchtower. [5](#0-4) 

### Likelihood Explanation
Exploitability depends on being able to construct/observe a genuine tie in cumulative work, which — unlike arbitrary floating point ties — is a real, reachable condition in Bitcoin's PoW model: work per block depends solely on `bits` (target), and `calculate_work` derives work purely from the target bytes, not the hash. [6](#0-5) 
Two chains of equal length that do not cross a difficulty retarget boundary (`BLOCKS_PER_EPOCH`) will therefore have identical cumulative work irrespective of which specific blocks (hashes) were included, making a tie a concretely constructible scenario rather than a purely theoretical one, especially over short intervals within one difficulty epoch (2016 blocks on mainnet, or fixed difficulty periods on regtest/testnet4 as also handled specially in this codebase). [7](#0-6) 

### Recommendation
Change the comparison to enforce the documented strict inequality, rejecting ties:
```rust
if total_work <= max_total_work {
    panic!(
        "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
    );
}
```
This ensures the operator's chain must have strictly more work than any proven watchtower challenge, matching the documented intent and eliminating the tie-acceptance edge case.

### Proof of Concept
Not directly executable from static analysis (would require constructing two chains with identical `bits` over an equal number of blocks — one submitted as the operator's HCP, the other as a watchtower Work-Only Proof — and observing that `bridge_circuit` does not panic despite the two `total_work` values being equal). The root-cause code path is demonstrated above at `circuits-lib/src/bridge_circuit/mod.rs:151-160`, and the requisite equal-work property of Bitcoin's difficulty model is demonstrated at `circuits-lib/src/header_chain/mod.rs:706-726`.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L115-132)
```rust
/// 4. Computes maximum total work and watchtower challenge flags using `total_work_and_watchtower_flags`.
/// 5. Validates that the computed `max_total_work` does not exceed the `total_work` in `hcp.chain_state`.
/// 6. Fetches the MMR (Merkle Mountain Range) for block hashes from `hcp.chain_state`.
/// 7. Verifies the SPV proof (`payout_spv`) using the fetched MMR.
/// 8. Verifies the light client proof using `lc_proof_verifier`.
/// 9. Ensures the L1 block hash from the light client proof matches the payout transaction's block hash.
/// 10. Checks storage proofs for deposit and withdrawal transaction indices using `verify_storage_proofs`.
/// 11. Converts the verified withdrawal outpoint into a Bitcoin transaction ID.
/// 12. Ensures the withdrawal transaction ID matches the input reference in `payout_spv.transaction`.
/// 13. Computes the `deposit_constant` using the first OP_RETURN output of the payout transaction.
/// 14. Extracts and truncates the latest block hash and the payout transaction’s block hash.
/// 15. Computes a Blake3 hash over concatenated block hash and watchtower flags.
/// 16. Generates a final journal hash using Blake3 over concatenated data and commits it.
///
/// # Panics
///
/// - If the method ID in `hcp` does not match `HEADER_CHAIN_METHOD_ID`.
/// - If `max_total_work` given by watchtowers is greater than `hcp.chain_state.total_work`.
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L151-160)
```rust
    let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
        .try_into()
        .expect("Cannot fail");

    // If total work is less than the max total work of watchtowers, panic
    if total_work < max_total_work {
        panic!(
            "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
        );
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

**File:** docs/bridge-circuit.md (L61-65)
```markdown
* **Watchtower Challenge Processing**: In the circuit, the Operator processes and validates challenges from watchtowers, who monitor operator behavior and provide their own Work Only Proof (WOP) as a Groth16 proof.
    This verification is done as follows:
    For each Watchtower, the signature that is for spending the connector UTXO for the challenge-sending transaction is verified. If the signature is verified, the corresponding bit flag to that Watchtower will be set to 1.
    Then the `Work`s provided by the Watchtowers are sorted in a descending order. Then, until the first Groth16 proof is verified, they are looped. This way, the Operator obtains the maximum valid amount of Work
    provided by the Watchtowers. The Operator must provide a HCP with more work compared to the WOP with maximum Work. This is necessary, since the canonical Bitcoin blockchain is determined by the total Work done. If the Operator fails to do so, this automatically means that the Operator did not follow the canonical chain; therefore, is already malicious.
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L318-345)
```rust
/// Creates a [`TxHandler`] for the `reimburse_tx`.
///
/// This transaction is sent by the operator if no challenge was sent, or a challenge was sent but no disprove was sent, to reimburse the operator for their payout.
///
/// # Inputs
/// 1. MoveToVaultTx: Utxo containing the deposit
/// 2. KickoffTx: Reimburse connector utxo in the kickoff
/// 3. RoundTx: Reimburse connector utxo in the round (for the given kickoff index)
///
/// # Outputs
/// 1. Reimbursement output to the operator
/// 2. Anchor output for CPFP
///
/// # Arguments
/// * `move_txhandler` - The move-to-vault transaction handler for the deposit.
/// * `round_txhandler` - The round transaction handler for the round.
/// * `kickoff_txhandler` - The kickoff transaction handler for the kickoff.
/// * `kickoff_idx` - The kickoff index of the operator's kickoff.
/// * `paramset` - Protocol parameter set.
/// * `operator_reimbursement_address` - The address to reimburse the operator.
///
/// # Returns
/// A [`TxHandler`] for the reimburse transaction, or a [`BridgeError`] if construction fails.
pub fn create_reimburse_txhandler(
    move_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
    kickoff_txhandler: &TxHandler,
    kickoff_idx: usize,
```

**File:** circuits-lib/src/header_chain/mod.rs (L421-455)
```rust
        for block_header in block_headers {
            self.block_height = self.block_height.wrapping_add(1);

            let (target_to_use, expected_bits, work_to_add) = if IS_TESTNET4 {
                if block_header.time > last_block_time + 1200 {
                    // If the block is an epoch block, then it still has to have the real target.
                    if self.block_height % BLOCKS_PER_EPOCH == 0 {
                        (
                            current_target_bytes,
                            self.current_target_bits,
                            calculate_work(&current_target_bytes),
                        )
                    }
                    // Otherwise, if the timestamp is more than 20 minutes ahead of the last block, the block is allowed to use the maximum target.
                    else {
                        (
                            NETWORK_CONSTANTS.max_target_bytes,
                            NETWORK_CONSTANTS.max_bits,
                            MINIMUM_WORK_TESTNET,
                        )
                    }
                } else {
                    (
                        current_target_bytes,
                        self.current_target_bits,
                        calculate_work(&current_target_bytes),
                    )
                }
            } else {
                (
                    current_target_bytes,
                    self.current_target_bits,
                    calculate_work(&current_target_bytes),
                )
            };
```

**File:** circuits-lib/src/header_chain/mod.rs (L706-726)
```rust
fn calculate_work(target: &[u8; 32]) -> U256 {
    // We should never have a target/work of zero so this doesn't matter
    // that much but we define the inverse of 0 as max.
    let target = U256::from_be_slice(target);
    if target == U256::ZERO {
        return U256::MAX;
    }
    // We define the inverse of 1 as max.
    if target == U256::ONE {
        return U256::MAX;
    }
    // We define the inverse of max as 1.
    if target == U256::MAX {
        return U256::ONE;
    }

    let comp = !target;

    let ret = comp.wrapping_div(&target.wrapping_add(&U256::ONE));
    ret.wrapping_add(&U256::ONE)
}
```
