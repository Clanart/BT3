## Title
Unauthenticated OP_RETURN operator tag in the `Payout` transaction lets an attacker reassign reimbursement credit to any registered operator - (File: core/src/task/payout_checker.rs, core/src/verifier.rs, core/src/builder/transaction/operator_reimburse.rs)

### Summary
The `Payout` transaction's OP_RETURN output (which names the operator entitled to reimbursement) is not covered by the user's `SinglePlusAnyoneCanPay` signature, so anyone observing a broadcast (but unconfirmed) payout transaction can mutate the OP_RETURN to name a different operator and get that variant confirmed instead. The verifier's chain sync then attributes the withdrawal to the attacker-named operator purely from this unauthenticated OP_RETURN, and that operator's own `PayoutCheckerTask` will pick it up and claim reimbursement for a payout it never made.

### Finding Description
The binding claimed by the protocol is: `payout_payer_operator_xonly_pk (DB) == operator that actually broadcast/funded the withdrawal's payout_tx`. This binding is broken because the OP_RETURN carrying the operator's xonly pubkey is not committed by the user's signature.

The user signs the payout input with `TapSighashType::SinglePlusAnyoneCanPay`, enforced in `core/src/rpc/parser/operator.rs:182` (`parse_withdrawal_sig_params`). This sighash type commits only to output index 0 (the user payout) and the single signed input; it explicitly does not cover output 1 (anchor) or output 2 (the OP_RETURN with the operator's xonly pk), as built in `create_payout_txhandler` [1](#0-0) . `calculate_script_spend_sighash`/related sighash logic confirms `Prevouts::One` is used and outputs beyond index 0 are unconstrained for `*PlusAnyoneCanPay` types [2](#0-1) .

Exploit flow:
1. Operator A (honestly funded, real operator) broadcasts a `Payout` tx spending withdrawal input0 → output0 (user payout) → OP_RETURN(A's xonly pk), signed with `SinglePlusAnyoneCanPay`.
2. Before it confirms, the attacker (any party who can see the mempool tx) copies input0+output0+the same signature, replaces the OP_RETURN payload with B's xonly pk, optionally adds their own fee-bump input (permitted because ANYONECANPAY only commits the signed input), and rebroadcasts with higher fee/RBF so it confirms first.
3. Verifier chain sync (`update_finalized_payouts`) parses the OP_RETURN of whichever payout tx actually confirmed and stores it verbatim as `payout_payer_operator_xonly_pk` with no authentication of who created it: [3](#0-2) .
4. Operator B's own `PayoutCheckerTask::run_once` queries `get_first_unhandled_payout_by_operator_xonly_pk(B's own xonly_pk)` [4](#0-3)  and finds this withdrawal because the DB (poisoned by the attacker's OP_RETURN) now names B.
5. It calls `Operator::handle_finalized_payout`, which fetches an unused kickoff connector and proceeds to build/sign/broadcast Kickoff and, eventually, Reimburse using B's own valid pre-signed N-of-N kickoff/reimburse graph for that deposit [5](#0-4) .
6. `Verifier::is_kickoff_malicious` only checks that `operator_xonly_pk` (from the same poisoned DB column) equals `kickoff_data.operator_xonly_pk`, and that the committed payout blockhash matches — it does not verify who actually fronted the withdrawal [6](#0-5) . `validate_payer_is_operator` likewise only compares the DB's (attacker-controlled) `payer_xonly_pk` against `self.signer.xonly_public_key`, which trivially matches for B since B is the named operator [7](#0-6) .

No existing guard authenticates the OP_RETURN payload against the broadcaster's identity; the only signature present (the user's) deliberately excludes it via the mandated `SinglePlusAnyoneCanPay` sighash flag.

### Impact Explanation
Operator B receives a real bridge-amount Reimburse output from the move-to-vault UTXO for a withdrawal it never funded, while operator A — who genuinely paid the user out of pocket — can never claim reimbursement for that same `idx` (the `withdrawals` row's `is_payout_handled`/`payout_payer_operator_xonly_pk` is now permanently attributed to B). This is repeatable for every withdrawal where the attacker can win the block/fee race against the honest operator's broadcast, and works against any two registered operators. This matches the Critical category: "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
The attacker needs no privileged role — only mempool visibility of operator A's broadcast (but unconfirmed) `Payout` transaction and the ability to pay Bitcoin fees to win an RBF/fee race, both of which are explicitly in-scope attacker capabilities. No control of hashrate is required to "mine first" in a meaningful sense — a simple higher-fee rebroadcast of the same input suffices since `SinglePlusAnyoneCanPay` also permits adding attacker-controlled fee inputs. This is feasible on every withdrawal cycle and does not require compromising any key, verifier, or Citrea component.

### Recommendation
Bind the OP_RETURN operator tag to the actual broadcaster cryptographically — e.g., require the user's payout signature to use `AllPlusAnyoneCanPay` (or otherwise cover all outputs) so the OP_RETURN cannot be swapped post-signature, or have the operator supply a separate signature/commitment over the OP_RETURN payload that verifiers validate before trusting `payout_payer_operator_xonly_pk`.

### Proof of Concept
```
cargo test payout_operator_attribution_hijack -- --nocapture
```
Plan:
1. Register operators A and B on a shared deposit (both have valid N-of-N kickoff/reimburse graphs for the deposit, as in existing e2e setup in `core/src/test/deposit_and_withdraw_e2e.rs`).
2. Have operator A call `withdraw`/`internal_withdraw` to construct and broadcast its `Payout` tx (input0+output0, OP_RETURN=A) but do not mine it yet.
3. As "attacker," take A's broadcast raw tx, decode it, replace the OP_RETURN output's push data with B's xonly pk, add an extra fee input if needed, and rebroadcast/RBF so this variant is the one mined.
4. Assert `verifier.db.get_payout_info_from_move_txid` returns `payout_payer_operator_xonly_pk == B`, not A (binding broken: expected `A == payer`, actual `payer == B`).
5. Poll until `PayoutCheckerTask` for operator B's node reports `get_handled_payout_kickoff_txid` non-None for this payout, confirming B autonomously created a kickoff for a withdrawal it never funded.
6. Continue the round to Reimburse confirmation and assert the Reimburse tx output pays operator B's `reimburse_addr`, spending the move-to-vault UTXO, while operator A's `get_first_unhandled_payout_by_operator_xonly_pk(A_pk)` never returns this `idx`.

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

**File:** core/src/builder/transaction/txhandler.rs (L315-322)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };
```

**File:** core/src/verifier.rs (L1857-1914)
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
```

**File:** core/src/verifier.rs (L2312-2343)
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

**File:** core/src/operator.rs (L839-885)
```rust
    pub async fn handle_finalized_payout<'a>(
        &'a self,
        dbtx: DatabaseTransaction<'a>,
        deposit_outpoint: OutPoint,
        payout_tx_blockhash: BlockHash,
    ) -> Result<bitcoin::Txid, BridgeError> {
        let (deposit_id, deposit_data) = self
            .db
            .get_deposit_data(Some(dbtx), deposit_outpoint)
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        // get unused kickoff connector
        let (round_idx, kickoff_idx) = self
            .db
            .get_unused_and_signed_kickoff_connector(
                Some(dbtx),
                deposit_id,
                self.signer.xonly_public_key,
            )
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }

```

**File:** core/src/operator.rs (L1686-1729)
```rust
    /// For a deposit_id checks that the payer for that deposit is the operator, and the payout blockhash and kickoff txid are set.
    async fn validate_payer_is_operator(
        &self,
        dbtx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(BlockHash, Txid), BridgeError> {
        let (payer_xonly_pk, payout_blockhash, kickoff_txid) = self
            .db
            .get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(dbtx, deposit_id)
            .await?;

        tracing::info!(
            "Payer xonly pk and kickoff txid found for the requested deposit, payer xonly pk: {:?}, kickoff txid: {:?}",
            payer_xonly_pk,
            kickoff_txid
        );

        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
        let (payout_blockhash, kickoff_txid) = match (
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid,
        ) {
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
                (payout_blockhash, kickoff_txid)
            }
            _ => {
                return Err(eyre::eyre!(
                    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
                    payout_blockhash,
                    kickoff_txid
                )
                .into());
            }
        };
```
