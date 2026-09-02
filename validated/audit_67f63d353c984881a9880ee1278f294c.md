### Title
Unauthenticated OP_RETURN operator attribution in `update_finalized_payouts` lets an attacker credit a payout to an operator who never funded it - ([File: core/src/verifier.rs])

### Summary
`update_finalized_payouts` derives the `operator_xonly_pk` credited for a withdrawal purely by parsing the OP_RETURN bytes of whatever transaction spends the withdrawal UTXO, with no check that the OP_RETURN key belongs to the party that actually supplied the transaction's inputs. Since anyone can craft and broadcast a payout-shaped transaction spending the (attacker-chosen) withdrawal UTXO with arbitrary OP_RETURN bytes, an attacker can attribute a self-funded payout to a victim operator, whose `PayoutCheckerTask` will then pick it up via `get_first_unhandled_payout_by_operator_xonly_pk` and call `handle_finalized_payout` on the victim's behalf.

### Finding Description
The claimed binding is: `operator_xonly_pk` recorded for withdrawal `idx` == the xonly_pk of the party whose funds actually paid that withdrawal. Tracing `update_finalized_payouts`: [1](#0-0) 

`operator_xonly_pk` is computed solely by `get_first_op_return_output` + `parse_op_return_data` + `XOnlyPublicKey::from_slice` on the located payout transaction's OP_RETURN output — there is no signature check, no check of the transaction's inputs' scriptSig/witness, and no comparison against who controls the spending keys. The value is then written directly into the DB via `update_payout_txs_and_payer_operator_xonly_pk`: [2](#0-1) 

The victim operator's own background task later blindly trusts this DB column: [3](#0-2) 

and if a row is found, unconditionally proceeds to `handle_finalized_payout`, `fetch_validate_and_store_lcp`, `end_round`, and `mark_payout_handled` — i.e., the victim operator begins the on-chain kickoff/reimbursement process for a payout it never funded: [4](#0-3) 

Since the withdrawal UTXO is attacker-chosen (attacker calls `withdraw` on the Citrea bridge and picks the withdrawal UTXO bytes per the threat model), and the payout transaction is an ordinary Bitcoin transaction outside the presigned N-of-N transaction graph, the attacker fully controls both its inputs (their own funds) and its OP_RETURN payload (victim operator's `XOnlyPublicKey` bytes). None of the referenced guards (`is_deposit_valid`, `is_profitable`, `SECP.verify_schnorr`, `only_aggregator_and_self`, `is_kickoff_malicious`, `verify_storage_proofs`, `SPV::verify`, `lc_proof_verifier`) run at this point in `update_finalized_payouts` to bind the OP_RETURN identity to the actual funder — the code path is fully unauthenticated with respect to that binding.

### Impact Explanation
The victim operator is induced to attempt `handle_finalized_payout`/kickoff for a payout it never funded, matching the Critical category "an operator reimbursed for a payout it never funded" (or made to attempt kickoff/reimbursement). This is repeatable per withdrawal and across any operator whose `XOnlyPublicKey` bytes are public knowledge (operator keys are public protocol parameters), so the blast radius spans every operator and every withdrawal processed by `update_finalized_payouts`. The immediate on-chain consequence is the victim operator spending its own collateral/fees initiating a kickoff it cannot legitimately justify, risking that the kickoff is later found unjustified/malicious and the operator's collateral penalized.

### Likelihood Explanation
No privileged role is required: the attacker only needs to fund a normal Bitcoin transaction spending the withdrawal UTXO it created via `withdraw`, with an OP_RETURN it can set to arbitrary bytes, and pay for that transaction's on-chain fees. This is directly within the stated unprivileged attacker capability set (choose withdrawal UTXO bytes, script/witness/OP_RETURN content, broadcast transactions). The attack is cheap (cost = payout amount + fee, which the attacker recovers no differently than a legitimate front-runner would) and fully repeatable for every future withdrawal.

### Recommendation
Bind the credited `operator_xonly_pk` to cryptographic proof that the payout transaction's inputs are actually controlled/authorized by that operator (e.g., require the payout transaction's spending witness to be validated against the operator's committed pubkey, or require an operator-signed commitment covering the specific withdrawal `idx`/UTXO before crediting), rather than trusting self-declared OP_RETURN bytes alone.

### Proof of Concept
```
cargo test finalized_payout_op_return_spoofing_attributes_to_wrong_operator (core/src/verifier.rs / core/src/task/payout_checker.rs)
```
1. Set up two operators, `attacker_funder` (unprivileged, no operator key) and `victim_operator` (a real registered operator).
2. Create a deposit/withdrawal so a withdrawal UTXO exists for idx `i`.
3. Attacker broadcasts a payout-shaped transaction: inputs from attacker's own wallet, spends the withdrawal UTXO, OP_RETURN = `victim_operator.signer.xonly_public_key` bytes.
4. Mine the block; run the verifier's block-sync path that calls `update_finalized_payouts`.
5. Assert: DB row for idx `i` has `operator_xonly_pk == victim_operator.signer.xonly_public_key` (LHS of binding), even though victim never signed/funded any input (RHS is attacker's key) — binding violated.
6. Run `victim_operator`'s `PayoutCheckerTask::run_once`; assert it returns `Ok(true)` and calls `handle_finalized_payout` for a payout it never funded.
7. Assert the resulting kickoff transaction is later flagged by `is_kickoff_malicious` (or equivalent disprove check), demonstrating the victim operator's collateral is put at risk for a payout it never made.

### Citations

**File:** core/src/verifier.rs (L2311-2321)
```rust
            let payout_tx = &block.txdata[*payout_tx_idx];
            // Find the first output that contains OP_RETURN
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());
```

**File:** core/src/verifier.rs (L2337-2350)
```rust
            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/task/payout_checker.rs (L39-47)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;
```

**File:** core/src/task/payout_checker.rs (L70-106)
```rust
        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;
```
