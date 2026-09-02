### Title
Attacker-supplied `out_script_pubkey` in a `Withdraw` request can itself be an OP_RETURN output placed before the operator's genuine `operator_xonly_pk` OP_RETURN, corrupting `deposit_constant` attribution - (File: circuits-lib/src/bridge_circuit/mod.rs)

### Summary
`get_first_op_return_output` (circuits-lib/src/bridge_circuit/mod.rs:688-692) blindly returns the *first* OP_RETURN output of the payout transaction and feeds it into `parse_op_return_data`/`deposit_constant` as the operator's x-only pubkey. `create_payout_txhandler` (core/src/builder/transaction/operator_reimburse.rs:407-436) always places the user-controlled payout output (index 0) **before** the operator's own OP_RETURN (index 2), and `Operator::withdraw` (core/src/operator.rs:560-692) never validates that the caller-supplied `out_script_pubkey` is a spendable, non-OP_RETURN script. If a caller of the operator's `Withdraw`/`InternalWithdraw` gRPC supplies an OP_RETURN as `out_script_pubkey`, that decoy output becomes output[0] and is picked up by `get_first_op_return_output` instead of the operator's real attribution output at index 2.

### Finding Description
The binding claimed to hold is:
`operator_xonlypk` extracted by `get_first_op_return_output(payout_spv.transaction)` + `parse_op_return_data` == the x-only public key of the operator that actually funded (fronted) the payout.

Trace:
- `create_payout_txhandler` (core/src/builder/transaction/operator_reimburse.rs:407-436) builds the payout tx with a fixed output order: `[output_txout (user payout, attacker-supplied script/amount), anchor_output, op_return_txout(operator_xonly_pk.serialize())]`.
- `Operator::withdraw` (core/src/operator.rs:560-692) takes `out_script_pubkey`/`out_amount` directly from the RPC caller and puts them into `output_txout` with **no validation of the script type** (it only validates the *input* UTXO's taproot pubkey at line 614-618, and calls `is_profitable` which only checks amounts, lines 502-537). Nothing rejects `out_script_pubkey` being `OP_RETURN <32 arbitrary bytes>`.
- `bridge_circuit` (circuits-lib/src/bridge_circuit/mod.rs:206-219) and the host helper `host_deposit_constant` (bridge-circuit-host/src/structs.rs:485-503), as well as `update_finalized_payouts` used by verifiers (core/src/verifier.rs:2312-2321), all call `get_first_op_return_output`, which does `tx.output.iter().find(|out| out.script_pubkey.is_op_return())` (mod.rs:688-692) — i.e., first match wins, with no check that there is only one OP_RETURN output or that it's at a fixed/expected index.
- If output[0] is attacker-crafted OP_RETURN data, `parse_op_return_data` extracts the attacker's 32 bytes as `operator_xonlypk`, which is then baked into `deposit_constant` (mod.rs:634-663) and ultimately `journal_hash` (mod.rs:665-684, committed at line 244).
- Independently, `Verifier::is_kickoff_malicious` (core/src/verifier.rs:1857-1915) reads the operator xonly pk from the DB (populated by the same corrupted `get_first_op_return_output`/`parse_op_return_data` logic in `update_finalized_payouts`, lines 2312-2321) and compares it against `kickoff_data.operator_xonly_pk`; a mismatch (line 1887-1890) causes the verifier to flag the kickoff as malicious, even though the real operator genuinely fronted the payout.

Existing guards checked and why they don't prevent this:
- `Verifier::is_deposit_valid`, `SPV::verify`, `verify_storage_proofs`, and `lc_proof_verifier` validate the payout transaction's inclusion, block, and withdrawal-UTXO linkage, but none of them constrain the *number or position* of OP_RETURN outputs in the payout transaction.
- `is_kickoff_malicious` compares `operator_xonly_pk` from the DB (itself already corrupted by the same first-match bug) against `kickoff_data.operator_xonly_pk` — this is exactly the mechanism through which the corruption becomes a false "malicious kickoff" verdict against an honest operator.
- Neither `create_payout_txhandler` nor `Operator::withdraw` enforces that the user-facing payout output is a standard, non-OP_RETURN script pubkey. (By contrast, the optimistic-payout path in `core/src/rpc/aggregator.rs:1044-1054` *does* explicitly check the output script pubkey is one of `p2tr`/`p2pkh`/`p2sh`/`p2wpkh`/`p2wsh` — this check is conspicuously absent from the standard `Operator::withdraw`/`create_payout_txhandler` path.)

Reachability caveat: `Operator::withdraw`'s gRPC entry point (`ClementineOperator/Withdraw`) is gated by the `only_aggregator_and_self` mTLS interceptor (core/src/rpc/interceptors.rs:36-77), so a caller without the aggregator's or the operator's own TLS certificate cannot invoke it directly. The attacker's only available entry point per the threat model is the aggregator's public gRPC, which forwards withdrawal parameters to operators over mTLS. I was unable to fully confirm within this investigation whether the aggregator's standard `Withdraw` relay path re-validates `output_script_pubkey`'s script type the same way the optimistic-payout path does before forwarding to `Operator::withdraw`; this is the one open question limiting a fully confirmed end-to-end unprivileged exploit.

### Impact Explanation
If exploited, this corrupts `operator_xonlypk` used in `deposit_constant`/`journal_hash`, which is the core cryptographic attribution binding a payout to the operator who fronted it. Downstream, `is_kickoff_malicious` (core/src/verifier.rs) would see a bogus/attacker-chosen "operator xonly pk" that does not match `kickoff_data.operator_xonly_pk`, causing verifiers to treat an honest operator's kickoff as malicious — matching the Critical category "an honest operator permanently unable to be reimbursed" / "a true claim made unprovable by the bridge... circuit." The blast radius is scoped to the specific withdrawal/deposit whose payout tx was tampered with; it does not by itself move bridge BTC to the attacker, but it can burn an honest operator's collateral or reimbursement eligibility for that deposit.

### Likelihood Explanation
Exploiting this requires the attacker (or a party under attacker influence) to control the `out_script_pubkey`/`out_amount` fields of a `WithdrawParams`/`WithdrawParamsWithSig` request that ultimately reaches `Operator::withdraw`. Direct invocation of the operator's `Withdraw` RPC is blocked by mTLS (`only_aggregator_and_self`), so the realistic path is via the aggregator's public relay, which I could not conclusively confirm lacks equivalent script-type validation on this code path (unlike the optimistic-payout path, which does validate). Given this open verification gap, likelihood is uncertain but plausible given the root-cause absence of validation in `Operator::withdraw`/`create_payout_txhandler` itself.

### Recommendation
- In `Operator::withdraw` (core/src/operator.rs) and/or `create_payout_txhandler` (core/src/builder/transaction/operator_reimburse.rs), reject `out_script_pubkey` if it is an OP_RETURN script (mirror the standard-script-pubkey check already present in `core/src/rpc/aggregator.rs:1044-1054`).
- Harden `get_first_op_return_output`/the bridge circuit logic to require the operator's OP_RETURN output to be at a fixed, protocol-defined index (e.g., always the last output) rather than "first OP_RETURN found," and/or assert there is exactly one OP_RETURN output in the payout transaction.
- Apply the same fixed-index/uniqueness check in `update_finalized_payouts` (core/src/verifier.rs) so the DB-cached `operator_xonly_pk` used by `is_kickoff_malicious` cannot be poisoned the same way.

### Proof of Concept
```rust
// In circuits-lib/src/bridge_circuit/mod.rs test module
#[test]
fn test_get_first_op_return_picks_decoy_before_operator_op_return() {
    // Build a payout-like tx with two OP_RETURN outputs:
    // output[0]: attacker decoy OP_RETURN with 32 arbitrary bytes
    // output[1]: anchor output (non-OP_RETURN)
    // output[2]: honest operator's OP_RETURN with real 32-byte x-only pubkey
    let decoy_bytes = [0xAAu8; 32];
    let honest_operator_pk = [0xBBu8; 32];

    let decoy_out = TxOut {
        value: Amount::from_sat(0),
        script_pubkey: op_return_script(&decoy_bytes), // helper building OP_RETURN <32 bytes>
    };
    let anchor_out = TxOut { value: Amount::from_sat(240), script_pubkey: anchor_script() };
    let honest_out = TxOut {
        value: Amount::from_sat(0),
        script_pubkey: op_return_script(&honest_operator_pk),
    };

    let tx = Transaction {
        version: bitcoin::transaction::Version::TWO,
        lock_time: bitcoin::absolute::LockTime::ZERO,
        input: vec![/* ... */],
        output: vec![decoy_out, anchor_out, honest_out],
    };
    let circuit_tx = CircuitTransaction::from(tx);

    let first = get_first_op_return_output(&circuit_tx).expect("has OP_RETURN");
    let extracted: [u8; 32] = parse_op_return_data(&first.script_pubkey)
        .unwrap().try_into().unwrap();

    // Bug: extracted equals attacker's decoy, not the honest operator's key.
    assert_eq!(extracted, decoy_bytes);
    assert_ne!(extracted, honest_operator_pk);

    // Compute deposit_constant with the corrupted key vs. the honest key and show divergence.
    let dc_corrupted = deposit_constant(extracted, 0, &[], [0u8; 32], [0u8; 32], 0, [0u8; 32]);
    let dc_honest = deposit_constant(honest_operator_pk, 0, &[], [0u8; 32], [0u8; 32], 0, [0u8; 32]);
    assert_ne!(dc_corrupted.0, dc_honest.0);
}
```
This test (constructing the tx purely in-process, no mainnet/Citrea needed) demonstrates the `get_first_op_return_output`/`parse_op_return_data`/`deposit_constant` chain picks the decoy output over the honest operator's, breaking the `operator_xonlypk == actual funding operator` binding at the circuit level. A full end-to-end PoC would additionally need to show `Operator::withdraw` accepts an OP_RETURN `out_script_pubkey` unchecked (core/src/operator.rs:560-692) and that this reaches the operator via the aggregator's public relay — the latter step is not fully confirmed in this investigation. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-219)
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

**File:** core/src/operator.rs (L560-627)
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

**File:** core/src/verifier.rs (L1875-1890)
```rust
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

**File:** core/src/rpc/interceptors.rs (L36-77)
```rust
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
}
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
