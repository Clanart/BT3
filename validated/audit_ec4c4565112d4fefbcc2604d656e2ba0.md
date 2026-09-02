### Title
Withdrawer-chosen `out_script_pubkey` can be crafted as an OP_RETURN, causing `get_first_op_return_output` to pick the wrong output and permanently mark an honest operator's kickoff as malicious - ([File: circuits-lib/src/bridge_circuit/mod.rs], [File: core/src/builder/transaction/operator_reimburse.rs], [File: core/src/operator.rs], [File: core/src/verifier.rs])

### Summary
`Operator::withdraw` and `Aggregator::withdraw` accept an attacker-chosen `out_script_pubkey` with no restriction on script type, unlike the optimistic-payout path which explicitly whitelists standard script types. If the withdrawer sets `out_script_pubkey` to an `OP_RETURN` script, `create_payout_txhandler` places it at output index 0, before the operator's real identity `OP_RETURN` at output index 2, so every consumer of `get_first_op_return_output` (the bridge circuit, and `Verifier::is_kickoff_malicious`/`update_finalized_payouts` in `core/src/verifier.rs`) reads the attacker's bytes instead of the honest operator's xonly pubkey.

### Finding Description
The binding being broken is the equality checked in `Verifier::is_kickoff_malicious`:

`operator_xonly_pk` (parsed from the payout tx's *first* OP_RETURN output) `== kickoff_data.operator_xonly_pk` (the real signer of the kickoff tx). [1](#0-0) 

Before the attack this holds because `create_payout_txhandler` places exactly one `OP_RETURN` (containing the true `operator_xonly_pk`) at output index 2: [2](#0-1) 

`Operator::withdraw` builds `output_txout` directly from the unvalidated caller-supplied `out_script_pubkey`/`out_amount`, with no check on script type (only value/profitability is checked): [3](#0-2) 

`Aggregator::withdraw` (the entry point reachable by an unprivileged withdrawer through the aggregator's public gRPC) forwards the same unvalidated params to every operator, without the standard-script-pubkey whitelist that the sibling `optimistic_payout` RPC applies: [4](#0-3) [5](#0-4) 

If the attacker sets `out_script_pubkey` to an `OP_RETURN` script carrying 32 attacker-chosen bytes, the resulting payout tx has two `OP_RETURN` outputs: index 0 (attacker's) and index 2 (operator's real key). `get_first_op_return_output` unconditionally returns the *first* match: [6](#0-5) 

This same function is used identically in the off-chain payout-sync path that populates the DB column consumed by `is_kickoff_malicious`: [7](#0-6) 

and inside the ZK bridge circuit itself to compute `deposit_constant`/`journal_hash`: [8](#0-7) 

Because output 0 (attacker-controlled) now wins over output 2 (operator's real key), both the DB-tracked payer identity and the circuit's `deposit_constant` are computed from attacker bytes instead of the honest operator's actual xonly pubkey. `is_kickoff_malicious` then finds `operator_xonly_pk != kickoff_data.operator_xonly_pk` (or `None` if the 32 bytes don't decode to a valid xonly pubkey) and returns `true`, i.e. it treats a completely honest operator kickoff as malicious.

None of the existing guards catch this: `Operator::is_profitable` only checks value amounts, `SECP.verify_schnorr` only authenticates that the withdrawer signed *this exact* output (which is precisely the attacker-chosen OP_RETURN, so the signature is valid), and the standard-script-pubkey whitelist that exists in `optimistic_payout` is absent from the normal `withdraw`/`internal_withdraw` path.

### Impact Explanation
An honest operator who fronts a legitimate withdrawal for a user who supplied a malicious `out_script_pubkey` will have its own kickoff misclassified as malicious by verifiers (`is_kickoff_malicious` returns `true`), which drives the honest operator into the disprove/challenge path instead of normal reimbursement. This matches the Critical category "an honest operator permanently unable to be reimbursed." The attack is repeatable per withdrawal/operator: any unprivileged withdrawer can grief any operator that processes their withdrawal request by supplying an OP_RETURN destination script, at the cost of burning their own advertised payout value (since OP_RETURN is unspendable) — a cheap griefing primitive with no need for privileged access, verifier collusion, or hashrate.

### Likelihood Explanation
No special preconditions beyond being a normal withdrawer: register a withdrawal on Citrea, then call `Aggregator::withdraw`/`Operator::withdraw` with `out_script_pubkey` set to a valid `OP_RETURN` push script and a matching `SinglePlusAnyoneCanPay` Schnorr signature over the payout tx sighash (which the attacker fully controls since it is their own withdrawal input). Cost is limited to the withdrawal amount itself (which is burned) plus fees; no BTC hashrate, TLS interception or key compromise is required. This is fully reproducible offline against `create_payout_txhandler` and `get_first_op_return_output`/`is_kickoff_malicious` without mainnet or a live Citrea node.

### Recommendation
Reject `out_script_pubkey` values that are `OP_RETURN` (or otherwise non-standard) in `Operator::withdraw`/`internal_withdraw` and in `Aggregator::withdraw`, mirroring the whitelist already enforced in `optimistic_payout` (`is_p2tr`/`is_p2pkh`/`is_p2sh`/`is_p2wpkh`/`is_p2wsh`). Additionally, harden `get_first_op_return_output` (and its downstream consumers `deposit_constant`, `is_kickoff_malicious`, `update_finalized_payouts`) to only trust the OP_RETURN at the protocol-defined fixed output index (index 2 for payout txs) rather than the first OP_RETURN found by scanning, so that additional attacker-inserted OP_RETURN outputs cannot shadow the canonical one.

### Proof of Concept
```rust
// core/src/builder/transaction/operator_reimburse.rs / circuits-lib/src/bridge_circuit/mod.rs
#[test]
fn test_attacker_op_return_shadows_operator_identity() {
    // 1. Build a payout tx via create_payout_txhandler where out_script_pubkey
    //    (output 0) is itself an OP_RETURN with 32 attacker-chosen bytes.
    let attacker_bytes = [0x41u8; 32];
    let attacker_op_return = op_return_txout(PushBytesBuf::from(attacker_bytes));
    // output_txout normally supplied by attacker via `withdraw` RPC
    let output_txout = TxOut {
        value: Amount::from_sat(100_000),
        script_pubkey: attacker_op_return.script_pubkey.clone(),
    };

    let real_operator_xonly_pk = /* honest operator's real key */;
    let txhandler = create_payout_txhandler(
        input_utxo, output_txout, real_operator_xonly_pk, user_sig, network,
    ).unwrap();
    let tx = txhandler.get_cached_tx();

    // Output 0 is attacker's OP_RETURN, output 2 is the honest operator's real OP_RETURN.
    assert!(tx.output[0].script_pubkey.is_op_return());
    assert!(tx.output[2].script_pubkey.is_op_return());

    // 2. get_first_op_return_output picks output 0, not output 2.
    let first = get_first_op_return_output(&CircuitTransaction::from(tx.clone())).unwrap();
    assert_eq!(first.script_pubkey, attacker_op_return.script_pubkey);

    // 3. deposit_constant computed from attacker bytes != deposit_constant from real key.
    let bad_pk: [u8; 32] = parse_op_return_data(&first.script_pubkey).unwrap().try_into().unwrap();
    let dc_bad = deposit_constant(bad_pk, /* ... */);
    let dc_good = deposit_constant(real_operator_xonly_pk.serialize(), /* same other args */);
    assert_ne!(dc_bad, dc_good, "operator identity binding was broken by attacker-controlled OP_RETURN");

    // 4. Equivalent effect on is_kickoff_malicious's binding: bad_pk != kickoff signer's real xonly pk
    assert_ne!(bad_pk, real_operator_xonly_pk.serialize());
}
```

### Citations

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

**File:** core/src/verifier.rs (L2312-2321)
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
```

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

**File:** core/src/operator.rs (L560-626)
```rust
    pub async fn withdraw(
        &self,
        withdrawal_index: u32,
        in_signature: taproot::Signature,
        in_outpoint: OutPoint,
        out_script_pubkey: ScriptBuf,
        out_amount: Amount,
    ) -> Result<Transaction, BridgeError> {
        tracing::info!(
            "Withdrawing with index: {}, in_signature: {:?}, in_outpoint: {:?}, out_script_pubkey: {}, out_amount: {}",
            withdrawal_index,
            in_signature,
            in_outpoint,
            out_script_pubkey,
            out_amount
        );

        // Prepare input and output of the payout transaction.
        let input_prevout = self.rpc.get_txout_from_outpoint(&in_outpoint).await?;
        let input_utxo = UTXO {
            outpoint: in_outpoint,
            txout: input_prevout,
        };
        let output_txout = TxOut {
            value: out_amount,
            script_pubkey: out_script_pubkey,
        };

        // Check Citrea for the withdrawal state.
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }

        let operator_withdrawal_fee_sats =
            self.config
                .operator_withdrawal_fee_sats
                .ok_or(BridgeError::ConfigError(
                    "Operator withdrawal fee sats is not specified in configuration file"
                        .to_string(),
                ))?;
        if !Self::is_profitable(
            input_utxo.txout.value,
            output_txout.value,
            self.config.protocol_paramset().bridge_amount,
            operator_withdrawal_fee_sats,
        ) {
            return Err(eyre::eyre!("Not enough fee for operator").into());
        }

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
```

**File:** core/src/rpc/aggregator.rs (L1044-1054)
```rust
        // check for some standard script pubkeys
        if !(output_script_pubkey.is_p2tr()
            || output_script_pubkey.is_p2pkh()
            || output_script_pubkey.is_p2sh()
            || output_script_pubkey.is_p2wpkh()
            || output_script_pubkey.is_p2wsh())
        {
            return Err(Status::invalid_argument(format!(
                "Output script pubkey is not a valid script pubkey: {output_script_pubkey}, must be p2tr, p2pkh, p2sh, p2wpkh, or p2wsh"
            )));
        }
```

**File:** core/src/rpc/aggregator.rs (L1812-1855)
```rust
    async fn withdraw(
        &self,
        request: Request<AggregatorWithdrawalInput>,
    ) -> Result<Response<AggregatorWithdrawResponse>, Status> {
        tracing::warn!("Withdraw rpc called");
        let request = request.into_inner();
        let (withdraw_params_with_sig, operator_xonly_pks) = (
            request.withdrawal.ok_or(Status::invalid_argument(
                "withdrawalParamsWithSig is missing",
            ))?,
            request.operator_xonly_pks,
        );
        // check compatibility with operators only
        self.check_compatibility_with_actors(CompatibilityCheckScope::OperatorsOnly)
            .await?;

        let withdraw_params = withdraw_params_with_sig
            .clone()
            .withdrawal
            .ok_or(Status::invalid_argument("withdrawalParams is missing"))?;

        // convert rpc xonly pks to bitcoin xonly pks
        let operator_xonly_pks_from_rpc: Vec<XOnlyPublicKey> = operator_xonly_pks
            .into_iter()
            .map(|xonly_pk| {
                xonly_pk.try_into().map_err(|e| {
                    Status::invalid_argument(format!("Failed to convert xonly public key: {e}"))
                })
            })
            .collect::<Result<Vec<_>, Status>>()?;

        tracing::info!(
            "Parsed withdraw rpc params, withdrawal params: {:?}, operator xonly pks: {:?}",
            withdraw_params,
            operator_xonly_pks_from_rpc
                .iter()
                .map(|pk| pk.to_string())
                .collect::<Vec<_>>()
        );

        // parse_withdrawal_sig_params is called to check if the inputs can be parsed correctly
        // and check if input sighash type is SinglePlusAnyoneCanPay
        let (withdrawal_id, _, _, _, _) =
            parser::operator::parse_withdrawal_sig_params(withdraw_params)?;
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L686-692)
```rust
/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```
