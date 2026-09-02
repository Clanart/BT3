### Title
Payout transaction's OP_RETURN operator attribution is not bound to the entity that actually funds the payout, allowing reimbursement credit to be misattributed - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The `payout_tx` that fronts a Citrea withdrawal contains an OP_RETURN output that records which operator's x-only public key is credited as having "fronted" the peg-out. This value is later read back off-chain-of-Bitcoin and used to determine who is entitled to reimbursement from the vault. Nothing in the protocol cryptographically ties this OP_RETURN pubkey to the party who actually supplied the Bitcoin that pays the user, breaking the binding `payout_payer_operator_xonly_pk (credited) == operator whose funds paid the withdrawal (fronted)`.

### Finding Description
`create_payout_txhandler` builds the payout transaction with three outputs: the user's payout, an anchor, and an OP_RETURN containing `operator_xonly_pk`: [1](#0-0) 

Only the single withdrawal input and the output at the same index are covered by the user's `SinglePlusAnyoneCanPay` Schnorr signature (`SpendPath::KeySpend` with `set_p2tr_key_spend_witness`). The remaining outputs (anchor, OP_RETURN) and any additional funding inputs are outside the scope of that signature — `SIGHASH_SINGLE | ANYONECANPAY` explicitly permits arbitrary extra inputs/outputs to be appended by whoever assembles the final transaction.

The operator's own code path funds the transaction using its bitcoind wallet via `fund_raw_transaction`/`sign_raw_transaction_with_wallet`, adding the operator's own inputs and a change output: [2](#0-1) 

The `operator_xonly_pk` embedded in the OP_RETURN is simply whatever value the constructor of the transaction chose to push before funding/signing — nothing forces it to equal the public key that controls the wallet actually supplying the funding inputs.

Downstream, the verifier's block-sync logic trusts this OP_RETURN value blindly to decide "who paid": [3](#0-2) 

which is persisted via `update_payout_txs_and_payer_operator_xonly_pk` and later used to gate reimbursement eligibility in `is_kickoff_malicious` (a kickoff is treated as malicious/rejected if its `operator_xonly_pk` doesn't match the OP_RETURN value recorded for that withdrawal): [4](#0-3) 

and in `PayoutCheckerTask`, which drives an operator's own automated reimbursement flow purely off this DB-recorded pubkey: [5](#0-4) 

Because the withdrawal's user-side signature (`in_signature`, `in_outpoint`, `output_script_pubkey`, `output_amount`) is not secret — it must be shared with any operator that wants to front the withdrawal, and becomes fully public the moment any payout attempt appears in the Bitcoin mempool before confirmation — a party other than the intended/legitimate funder can reuse that exact signed input/output pair to assemble a rival payout transaction with a different (attacker-chosen) OP_RETURN pubkey and get it mined first. This is the direct analog of the WithdrawPeriphery issue: the credential that authorizes a value-moving action (the withdrawal signature) is not bound to the party that ends up being credited/attributed for that action, so a third party can insert themselves into (or misattribute) a transaction whose authorization was intended for someone else.

### Impact Explanation
Two concrete outcomes fall inside the Critical impact bucket:
- If the forged OP_RETURN names a real, currently-registered operator who did not actually fund this payout, that operator becomes the only one whose kickoff will be accepted as non-malicious for this deposit (`is_kickoff_malicious`), letting them later claim the `move_to_vault` UTXO via `create_reimburse_txhandler` reimbursement flow for a payout they never funded — "operator reimbursed for a payout it never funded".
- If the forged OP_RETURN instead contains an arbitrary 32-byte value that does not correspond to any real registered operator (nothing validates the pushed bytes against the operator registry at parse time — `XOnlyPublicKey::from_slice` only checks curve validity), no operator can ever produce a matching kickoff, so `is_kickoff_malicious` will reject every legitimate operator's kickoff for that deposit — the move-to-vault UTXO for that deposit becomes permanently un-reimbursable ("honest operator permanently unable to be reimbursed" / vault funds effectively frozen from the operator-reimbursement path).

### Likelihood Explanation
Exploitation requires only observing an unconfirmed/pending payout attempt (or otherwise obtaining the withdrawal's signed parameters, which must be disclosed to operators/aggregator to solicit a front) and racing a replacement transaction with a higher fee before the legitimate transaction confirms — a standard, low-cost Bitcoin mempool race requiring no special protocol role, key, or trusted position, only awareness of a pending withdrawal and normal fee bidding.

### Recommendation
Cryptographically bind the OP_RETURN operator attribution to the actual funder of the payout, e.g. by requiring the operator to also sign (with their registered protocol key) a commitment over the full finalized transaction (including OP_RETURN) before it is accepted as valid for reimbursement purposes, or by deriving the credited operator identity from the source of the funding inputs (verified in `update_finalized_payouts`) rather than trusting unauthenticated OP_RETURN pushdata.

### Proof of Concept
1. User calls Citrea's withdrawal contract, producing publicly-visible withdrawal parameters (`withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, `output_amount`) and shares/leaks them (or they appear in the Bitcoin mempool once any operator attempts to front the payout), see the params structure at: [6](#0-5) 
2. Attacker builds their own Bitcoin transaction spending the same `input_outpoint` with the same `in_signature` at input index 0 and the same `output_script_pubkey`/`output_amount` at output index 0 (satisfying `SinglePlusAnyoneCanPay`), but appends their own funding inputs plus an OP_RETURN output naming an arbitrary or a targeted real operator's x-only public key, mirroring the structure built in `create_payout_txhandler`: [1](#0-0) 
3. Attacker broadcasts with a competitive fee so it confirms before/instead of the legitimate operator's transaction.
4. `update_finalized_payouts` records the attacker's forged OP_RETURN pubkey as the "payer" for this withdrawal: [7](#0-6) 
5. Subsequent reimbursement/kickoff validation (`is_kickoff_malicious`) now only accepts kickoffs from that forged pubkey, letting a falsely-named real operator claim reimbursement they never earned, or permanently blocking reimbursement if the pubkey is bogus.

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

**File:** core/src/operator.rs (L639-691)
```rust
        let fee_rate = self
            .rpc
            .get_fee_rate_kvb(
                self.config.protocol_paramset.network,
                &self.config.mempool_api_host,
                &self.config.mempool_api_endpoint,
                self.config.tx_sender_limits.mempool_fee_rate_multiplier,
                self.config.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.config.tx_sender_limits.fee_rate_hard_cap,
            )
            .await?;

        // send payout tx using RBF
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;

        let signed_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_tx, None, None)
            .await
            .wrap_err("Failed to sign withdrawal transaction")?
            .hex;

        let signed_tx: Transaction = bitcoin::consensus::deserialize(&signed_tx)
            .wrap_err("Failed to deserialize signed withdrawal transaction")?;

        self.rpc
            .send_raw_transaction(&signed_tx)
            .await
            .wrap_err("Failed to send withdrawal transaction")?;

        Ok(signed_tx)
```

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2312-2350)
```rust
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/task/payout_checker.rs (L41-47)
```rust
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;
```

**File:** core/src/rpc/clementine.rs (L238-258)
```rust
#[derive(Clone, PartialEq, ::prost::Message)]
pub struct WithdrawParams {
    /// The ID of the withdrawal in Citrea
    #[prost(uint32, tag = "1")]
    pub withdrawal_id: u32,
    /// User's \[`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`\]
    /// signature
    #[prost(bytes = "vec", tag = "2")]
    pub input_signature: ::prost::alloc::vec::Vec<u8>,
    /// User's UTXO to claim the deposit
    #[prost(message, optional, tag = "3")]
    pub input_outpoint: ::core::option::Option<Outpoint>,
    /// The withdrawal output's script_pubkey (user's signature is only valid for
    /// this pubkey)
    #[prost(bytes = "vec", tag = "4")]
    pub output_script_pubkey: ::prost::alloc::vec::Vec<u8>,
    /// The withdrawal output's amount (user's signature is only valid for this
    /// amount)
    #[prost(uint64, tag = "5")]
    pub output_amount: u64,
}
```
