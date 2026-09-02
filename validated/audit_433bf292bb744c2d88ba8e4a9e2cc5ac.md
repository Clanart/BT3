### Title
Bridge circuit never commits to the payout transaction's output value/script, letting an attacker forge a "fronted payout" for any operator's public key - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
`bridge_circuit` (and `verify_storage_proofs`) only bind the *previous_output identity* (txid/vout) of the withdrawal input, never the value or script of the payout output that is supposed to pay the withdrawer. Since the withdrawal UTXO's private key is controlled by the withdrawing user (an unprivileged attacker), and the OP_RETURN that attributes the payout to an operator carries no signature at all, an attacker can sign an arbitrary replacement transaction — spending the same registered withdrawal outpoint, paying whatever value/script they like, and embedding any operator's real x-only pubkey in the OP_RETURN — and the whole downstream pipeline (`verify_storage_proofs` → `bridge_circuit` → verifier's `update_finalized_payouts`/`is_kickoff_malicious` → operator's `PayoutCheckerTask`) will treat it as a legitimate, correctly-funded payout by that operator.

### Finding Description
The binding that should hold is:
`payout_spv.transaction` == the transaction the operator itself broadcast via `create_payout_txhandler`, which pays `output_txout.value`/`output_txout.script_pubkey` exactly as the withdrawer requested in `WithdrawParams`.

What is actually checked in `bridge_circuit` (`circuits-lib/src/bridge_circuit/mod.rs:182-229`):
- `verify_storage_proofs` (`circuits-lib/src/bridge_circuit/storage_proof.rs:44-133`) only returns the withdrawal outpoint's `txid`/`vout` and `move_txid` from Citrea storage — no value/script commitment exists in that contract slot at all.
- `bridge_circuit` asserts `user_wd_txid`/`vout` equal `payout_spv.transaction.input[payout_input_index].previous_output` (`mod.rs:190-204`) — identity of the *spent* outpoint only. [1](#0-0) 
- `deposit_constant` is computed from `move_txid`, watchtower pubkeys digest, `operator_xonlypk` (read straight out of the payout transaction's own OP_RETURN, unsigned data), `round_txid`, `kickoff_round_vout`, `genesis_state_hash` — **no output value, no output script**. [2](#0-1) [3](#0-2) 

The `create_payout_txhandler` used by a legitimate operator signs the withdrawal input with the withdrawer's own key using `SinglePlusAnyoneCanPay` — this only proves that *whoever holds the withdrawal UTXO's private key* (the withdrawer, not the operator) approved that specific output at index 0. [4](#0-3) 

Because the withdrawer holds that key, they can sign an entirely different transaction spending the *same* registered withdrawal outpoint, with any output value/script they want (e.g., paying themselves with their own separately-funded inputs, thanks to `ANYONECANPAY`), and append an OP_RETURN naming ANY operator's real x-only public key — OP_RETURN outputs require no signature.

Downstream, `update_finalized_payouts` blindly trusts this OP_RETURN to attribute the payout to that operator in the DB: [5](#0-4) 

`PayoutCheckerTask` for that operator automatically picks up "its" unhandled payout purely from this DB attribution and drives the full kickoff/reimbursement flow: [6](#0-5) 

`is_kickoff_malicious` only checks that the attributed operator pubkey matches the kickoff's operator and that the committed blockhash matches — both of which trivially hold for the forged transaction: [7](#0-6) 

None of `verify_storage_proofs`, `bridge_circuit`'s asserts, `is_kickoff_malicious`, or `send_asserts`'s operator-matching check re-derive or re-verify that the mined payout transaction's output actually delivered the withdrawer's requested value/script. The `withdraw()`/`optimistic_payout` RPC-time profitability and script checks (`core/src/operator.rs:502-627`, `core/src/verifier.rs:1566-1660`) are purely advisory pre-broadcast checks on a transaction the operator *intends* to send; they are never re-checked against whatever transaction ends up confirmed on-chain.

### Impact Explanation
An unprivileged attacker (the withdrawer) can construct and mine a transaction that spends their own registered withdrawal outpoint with a value/script of their choosing (funded entirely by their own additional inputs, not the operator's), while embedding a real, honest operator's x-only public key in the OP_RETURN. This satisfies every identity check in `bridge_circuit`, causing that operator's automation to believe it fronted the withdrawal and to drive a genuine kickoff/reimbursement — releasing BTC from the round/reimbursement chain to that operator for a payout it never funded. This matches the Critical category "an operator reimbursed for a payout it never funded" / "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal." The attack is repeatable per withdrawal and works against any operator whose x-only pubkey is public (all registered operators), so the blast radius spans every deposit/withdrawal and every operator.

### Likelihood Explanation
No special preconditions beyond controlling one's own withdrawal UTXO key (which every withdrawer has by design) and knowing a target operator's public x-only key (public information). No RBF race against a confirmed transaction is even required — the attacker can build the entire forged payout from scratch, before any operator ever calls `withdraw()`. Cost is only normal Bitcoin transaction fees. This is fully reachable by the unprivileged attacker persona defined in the rules, requires no verifier/operator/aggregator collusion, no key compromise, and no majority hashrate.

### Recommendation
Bind the payout transaction's actual output (value and script_pubkey) into `deposit_constant`/`journal_hash` in `circuits-lib/src/bridge_circuit/mod.rs`, and cross-check it against the withdrawal amount/script that Citrea's bridge contract records for that specific withdrawal (already tracked as `output_amount`/`output_script_pubkey` in `WithdrawParams`, but never propagated into the storage proof or the circuit). Additionally, require that the operator's own signature (not just an unsigned OP_RETURN) authorizes the OP_RETURN "I fronted this" attribution, e.g. by having the operator's funding input(s) or a signed commitment included in the sighash validated by the circuit.

### Proof of Concept
```
cargo test in circuits-lib crate:
1. Build a BridgeCircuitInput where:
   - storage proof commits withdrawal outpoint (txid_W, vout_W) and move_txid M (as in existing test_data/storage_proof.bin).
   - payout_spv.transaction spends (txid_W, vout_W) at payout_input_index, with output[0] = TxOut { value: 0, script_pubkey: attacker_script }, funded via an attacker-only additional input, and output containing OP_RETURN(honest_operator_xonlypk).
2. Assert bridge_circuit(...) does NOT panic and commits a journal_hash, i.e. it accepts a transaction whose output value (0) mismatches what should have been paid to the withdrawer.
3. Compare against a second variant where output[0].value equals the real bridge_amount, showing deposit_constant/journal_hash is IDENTICAL modulo move_txid/operator pubkey/round data — i.e., proving the circuit's committed output never depends on the payout's value/script.
```

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-204)
```rust
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

**File:** core/src/verifier.rs (L1857-1915)
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

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
    }
```

**File:** core/src/verifier.rs (L2311-2342)
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
```

**File:** core/src/task/payout_checker.rs (L39-79)
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

        if unhandled_payout.is_none() {
            return Ok(false);
        }

        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");

        tracing::info!(
            "Unhandled payout found for withdrawal {}, move_txid: {}",
            citrea_idx,
            move_to_vault_txid
        );

        let deposit_data = self
            .db
            .get_deposit_data_with_move_tx(Some(&mut dbtx), move_to_vault_txid)
            .await?;
        if deposit_data.is_none() {
            return Err(eyre::eyre!("Fronted withdrawal for move tx {move_to_vault_txid} found, but the signatures for the deposit are not found in the db.").into());
        }

        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;
```
