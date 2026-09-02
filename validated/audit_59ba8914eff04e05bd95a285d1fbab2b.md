## Title
Unauthenticated OP_RETURN operator-attribution output in `payout_tx` lets any party rewrite who gets credited as the fronting operator - ([File: core/src/builder/transaction/operator_reimburse.rs])

## Summary
The bridge attributes a payout (and, downstream, reimbursement eligibility) to whichever operator's x-only pubkey is embedded in the payout transaction's OP_RETURN output. That output is not covered by the user's authorization signature because the payout transaction's sole input is signed with `SinglePlusAnyoneCanPay`, which only commits to input 0 and its "matching" output (index 0, the user's payout output). This mirrors the Yeti Finance bug class: a value/attribution field (`lastBuyBackPrice` in the analog; the operator xonly pubkey here) is derived from transaction data that isn't actually bound/verified against the real party who performed the qualifying action (the router path amounts in the analog; the operator who broadcast/fronted the payout here).

## Finding Description
`create_payout_txhandler` builds the payout transaction with three outputs: (1) user payout, (2) anchor, (3) OP_RETURN containing `operator_xonly_pk` [1](#0-0) . The witness for the single input is produced via `set_p2tr_key_spend_witness(&user_sig, 0)`, and in `Operator::withdraw` the user's signature is verified with `in_signature.sighash_type` explicitly expected to be `SinglePlusAnyoneCanPay` [2](#0-1) .

Because `SIGHASH_SINGLE | ANYONECANPAY` only commits to the single input being spent and the correspondingly-indexed output (output 0), the OP_RETURN output (index 2) carrying `operator_xonly_pk` is completely unauthenticated by the user's signature. Downstream, this OP_RETURN is the sole source of truth for "which operator fronted this payout":

- `verifier.rs::update_finalized_payouts` extracts `operator_xonly_pk` straight from the first OP_RETURN output of the on-chain payout tx and persists it as the payer of record: [3](#0-2) .
- `verifier.rs::is_kickoff_malicious` later compares the kickoff's claimed operator against this same stored `operator_xonly_pk` to decide whether a kickoff (and its reimbursement claim) is legitimate: [4](#0-3) .
- The bridge circuit (and its host equivalent) also derive `operator_xonlypk` directly from `get_first_op_return_output` of the payout tx and bake it into `deposit_constant`, which gates the zk proof that authorizes reimbursement on L1/L2: [5](#0-4) [6](#0-5) .

Because the OP_RETURN is unsigned/unauthenticated, any party who obtains the user's signed input (e.g., by observing the payout transaction template or the raw signature/outpoint before it confirms) can construct a competing transaction that reuses the same signed input+output-0 pair but substitutes a different OP_RETURN output naming a different operator's xonly pubkey. If that competing transaction is the one that actually confirms on-chain (e.g., via fee-bumping/replacement race), the attribution stored by verifiers and the one baked into the ZK proof will point to an operator who did not actually construct/fund that specific payout broadcast, while the identity that the honest operator intended to have recorded is displaced.

This directly parallels the audited bug: `lastBuyBackPrice = amounts[0]/amounts[1]` trusted router-provided values without validating that those values corresponded to the actual claimed asset pair/path; here, the protocol trusts an arbitrary, unauthenticated transaction field (`operator_xonly_pk` in the OP_RETURN) as ground truth for "who fronted this withdrawal" without any binding to the signature that actually authorizes the withdrawal payment.

## Impact Explanation
This breaks the binding "the operator credited versus the party that paid" — a Critical-severity impact category per the given rules ("an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed"). If a malicious actor can get their own OP_RETURN-tagged payout transaction confirmed instead of the honest operator's version (both spending the same signed input), the honest operator who actually intends to front the withdrawal can be denied correct attribution, while another party's xonly pubkey ends up as the on-chain/zk-proof-committed payer — potentially enabling that other party to pursue the reimbursement/kickoff flow for a payout it did not actually construct as claimed.

## Likelihood Explanation
Exploitation requires the attacker to obtain the signed input (`in_signature`/outpoint) before the honest operator's specific broadcast confirms, and to win a replacement/first-confirmation race on Bitcoin — this is feasible in mempool-monitoring conditions but is probabilistic and time-sensitive (RBF/replacement dependent), not a guaranteed unauthenticated call. I could not fully verify within the available context whether additional protocol-level checks elsewhere (e.g., in the tx_sender or watchtower challenge flow) independently re-derive or cross-check the operator attribution against the actual broadcaster in a way that would neutralize this before reimbursement is finalized; this remains an open question given tool-call limits reached during this investigation.

## Recommendation
Bind the operator attribution to something the user's authorization actually commits to, e.g., include the operator's xonly pubkey inside the signed message/sighash of the payout output (use `SIGHASH_ALL` or otherwise commit the OP_RETURN output within the signed scope), or have the aggregator/verifier require an operator-specific verification signature over the *entire* payout transaction (including OP_RETURN) rather than trusting an unauthenticated output. Additionally, cross-check `operator_xonly_pk` extracted from OP_RETURN against the operator's own broadcast records / the key that actually funded/relayed the transaction before persisting or using it for kickoff legitimacy or deposit-constant computation.

## Proof of Concept
1. Honest operator `O1` calls `withdraw()` with `withdrawal_index`, `in_signature` (SinglePlusAnyoneCanPay over input 0/output 0), `in_outpoint`, `out_script_pubkey`, `out_amount`. `Operator::withdraw` builds `payout_txhandler` via `create_payout_txhandler(..., self.signer.xonly_public_key=O1, in_signature, ...)` [7](#0-6) .
2. Before `O1`'s transaction confirms, attacker `O2` observes `in_signature`/`in_outpoint`/`out_script_pubkey`/`out_amount` (e.g., via mempool or RPC broadcast visibility) and independently calls `create_payout_txhandler` with the same `input_utxo`/`output_txout`/`user_sig` but `operator_xonly_pk = O2`. The witness `set_p2tr_key_spend_witness(&user_sig, 0)` remains valid since the signature only commits to input 0/output 0 [1](#0-0) .
3. `O2` broadcasts this alternate transaction with a higher fee (RBF) so it replaces `O1`'s version and confirms.
4. On confirmation, `verifier.rs::update_finalized_payouts` reads the OP_RETURN of the confirmed transaction and records `O2` as the payer operator for that withdrawal [3](#0-2) ; the ZK bridge circuit will likewise bind `deposit_constant` to `O2`'s pubkey [5](#0-4) , even though `O1` was the operator who obtained and intended to use the withdrawal authorization.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-436)
```rust
pub fn create_payout_txhandler(
    input_utxo: UTXO,
    output_txout: TxOut,
    operator_xonly_pk: XOnlyPublicKey,
    user_sig: taproot::Signature,
    _network: bitcoin::Network,
) -> Result<TxHandler<Signed>, BridgeError> {
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(anchor_output(
            NON_EPHEMERAL_ANCHOR_AMOUNT,
        )))
        .add_output(UnspentTxOut::from_partial(op_return_txout))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    txhandler.promote()
}
```

**File:** core/src/operator.rs (L614-637)
```rust
        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/verifier.rs (L1857-1890)
```rust
    /// Checks if the operator who sent the kickoff matches the payout data saved in our db
    /// Payout data in db is updated during citrea sync.
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2311-2343)
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

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }

            tracing::info!(
                "A new payout tx detected for withdrawal {}, payout txid: {:?}, operator xonly pk: {:?}",
                idx,
                payout_txid,
                operator_xonly_pk
            );

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-229)
```rust
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
```

**File:** bridge-circuit-host/src/structs.rs (L482-516)
```rust
fn host_deposit_constant(
    input: &BridgeCircuitInput,
) -> Result<DepositConstant, BridgeCircuitHostParamsError> {
    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .ok_or(BridgeCircuitHostParamsError::InvalidOperatorPubkey)?;

    let deposit_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&input.sp.storage_proof_deposit_txid).map_err(|e| {
            BridgeCircuitHostParamsError::StorageProofDeserializationError(e.to_string())
        })?;

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .ok_or(BridgeCircuitHostParamsError::InvalidOperatorPubkey)?
        .try_into()
        .map_err(|_| BridgeCircuitHostParamsError::InvalidOperatorPubkey)?;

    let deposit_value_bytes: [u8; 32] = deposit_storage_proof.value.to_be_bytes::<32>();

    Ok(deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        deposit_value_bytes,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    ))
}
```
