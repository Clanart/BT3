### Title
Unauthenticated OP_RETURN operator attribution in `update_finalized_payouts` lets anyone credit a payout to an operator that never funded it - (File: `core/src/verifier.rs`)

### Summary
`Verifier::update_finalized_payouts` attributes a withdrawal's payout to whichever `XOnlyPublicKey` happens to be encoded in the first OP_RETURN output of the transaction that spends the withdrawal UTXO, with no check that the named operator actually constructed, signed, or funded that transaction. Because the withdrawal UTXO's only spending authorization is a `SIGHASH_SINGLE|ANYONECANPAY` signature from the withdrawer, any unprivileged party can add its own funding inputs and an arbitrary OP_RETURN (including a real operator's pubkey) to a valid spend of that UTXO, hijacking the attribution.

### Finding Description
The binding the protocol needs is: `payout_payer_operator_xonly_pk[idx] == identity of the party whose BTC funded the output paid to the withdrawer for idx`.

`update_finalized_payouts` derives `operator_xonly_pk` purely from parsing the OP_RETURN of whatever transaction is found to spend `withdrawal_utxo_txid/vout` in the finalized block: [1](#0-0) 

The withdrawal UTXO's spend is authorized only by the withdrawer's `SinglePlusAnyoneCanPay` signature, verified in `Aggregator::optimistic_payout`: [2](#0-1) 

`ANYONECANPAY` means the signature covers only the single signed input/output pair; any party who learns `(input_outpoint, input_signature, output_script_pubkey, output_amount)` — which are exactly the fields carried in the public `WithdrawParams` used by both `Aggregator::withdraw` and `Aggregator::optimistic_payout` gRPC calls — can build their own transaction that reuses that input, adds its own funding inputs, and appends any OP_RETURN payload, including a real operator's 32-byte xonly pubkey it does not control.

Since a UTXO can only be spent once on Bitcoin, whichever transaction confirms first becomes "the" payout recorded by `get_payout_txs_for_withdrawal_utxos`: [3](#0-2) 

`PayoutCheckerTask::run_once` then blindly trusts this DB attribution, looking up the first unhandled payout matching the operator's *own* key and automatically starting the kickoff/reimbursement flow for it: [4](#0-3) [5](#0-4) 

The only cross-check against forged attribution, `Verifier::is_kickoff_malicious`, verifies that the OP_RETURN pubkey matches the kickoff's declared operator and that a committed blockhash matches — it never verifies that the named operator actually constructed, signed, or funded the payout transaction itself: [6](#0-5) 

Exploit flow: the attacker observes a withdrawal's public parameters (via Citrea's `withdraw` event or the aggregator's public `withdraw`/`optimistic_payout` gRPC calls), then races a self-funded transaction spending the withdrawal UTXO to the designated output, appending a legitimate operator O's xonly pubkey as OP_RETURN, and gets it mined before O's own payout or the optimistic payout confirms. `update_finalized_payouts` records `(idx, txid, Some(O), block_hash)`. O's own `PayoutCheckerTask` (running automatically, with no manual approval per-withdrawal) then treats this as its own unhandled payout, drives the kickoff to completion, and ultimately claims the `Reimburse` output for a withdrawal it never funded.

### Impact Explanation
This breaks the fundamental attribution invariant that reimbursement credit tracks who actually fronted a withdrawal. An unprivileged attacker can unilaterally assign credit for a bridge payout to any operator's public key (which is public knowledge) without that operator's consent or participation, causing the depositor's escrowed `bridge_amount` to be released via the presigned kickoff/round/Reimburse chain to an operator that never disbursed funds. This is repeatable per withdrawal and per operator (any operator's known xonly_pk can be targeted), and directly matches the pre-approved Critical category "an operator reimbursed for a payout it never funded."

A closely related variant (embedding an invalid/no-owner pubkey, or a value that fails `XOnlyPublicKey::from_slice`) causes `operator_xonly_pk` to be recorded as `NULL`; `is_kickoff_malicious` then treats *any* subsequent operator kickoff attempt for that deposit as malicious, permanently blocking reimbursement and freezing the corresponding move-to-vault funds — also a listed Critical category.

### Likelihood Explanation
No privileged access is required: the attacker only needs to observe a withdrawal's public parameters (obtainable from Citrea's public bridge contract or the aggregator's public gRPC surface) and pay Bitcoin fees plus the withdrawal output amount to win the race against the legitimate payer. This is feasible for any withdrawal at any time between Citrea acceptance and payout confirmation, and is repeatable across every deposit/withdrawal and every operator whose xonly_pk is public (all operator keys are public by protocol design).

### Recommendation
Bind the OP_RETURN attribution cryptographically to the claiming operator, e.g. require the operator to sign a commitment (or include a MuSig2/Schnorr signature over the payout tx or withdrawal id) that only the true operator can produce, and have `update_finalized_payouts`/`is_kickoff_malicious` verify that signature instead of trusting raw OP_RETURN bytes. Alternatively, require the payout transaction's funding input(s) to be provably controlled/spent by the claiming operator (e.g., tie the operator identity to a specific pre-committed funding path) so that unrelated third-party inputs cannot forge attribution.

### Proof of Concept
```
cargo test -p clementine-core --test deposit_and_withdraw_e2e -- attacker_hijacks_payout_attribution
```
Test plan:
1. Run a single deposit and register a withdrawal on the mock Citrea client, obtaining `withdrawal_utxo`, `withdrawal_params` (including the withdrawer's `SinglePlusAnyoneCanPay` `input_signature`), matching the flow in `run_single_deposit`/`generate_withdrawal_transaction_and_signature`.
2. Before calling `operator.withdraw(...)` or `aggregator.optimistic_payout(...)`, independently construct a transaction (as an attacker with no aggregator/operator access) that: reuses `withdrawal_utxo` + `input_signature` as input, adds an attacker-funded input, pays the exact registered `output_script_pubkey`/`output_amount`, and appends an OP_RETURN containing operator O's real `xonly_public_key` bytes. Broadcast and mine it.
3. Mine to finality and assert, on the verifier DB, `get_payout_info_from_move_txid(move_txid).0 == Some(O_xonly_pk)` (left side of the binding) while separately asserting operator O never called `withdraw`/signed this specific payout tx (right side: true payer is the attacker's funding input, not O) — demonstrating the equality "payer credited == party who funded" is violated.
4. Poll `operator_db.get_handled_payout_kickoff_txid(None, payout_txid)` and confirm O's `PayoutCheckerTask` autonomously processes and eventually reaches a `Reimburse` transaction for this withdrawal despite O never disbursing funds.

### Citations

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

**File:** core/src/verifier.rs (L2312-2328)
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
```

**File:** core/src/rpc/aggregator.rs (L1120-1126)
```rust
            let sighash = opt_payout_txhandler
                .calculate_pubkey_spend_sighash(0, input_signature.sighash_type)?;

            let message = Message::from_digest(sighash.to_byte_array());

            SECP.verify_schnorr(&input_signature.signature, &message, &user_xonly_pk)
                .map_err(|_| Status::internal("Invalid signature for optimistic payout tx. Ensure the signature uses SinglePlusAnyoneCanPay sighash type."))?;
```

**File:** core/src/database/verifier.rs (L168-196)
```rust
    /// Returns the withdrawal indexes and their spending txid for the given
    /// block id.
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
    }
```

**File:** core/src/database/verifier.rs (L282-313)
```rust
    pub async fn get_first_unhandled_payout_by_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        operator_xonly_pk: XOnlyPublicKey,
    ) -> Result<Option<(u32, Txid, BlockHash)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, Option<TxidDB>, Option<BlockHashDB>)>(
            "SELECT w.idx, w.move_to_vault_txid, w.payout_tx_blockhash
             FROM withdrawals w
             WHERE w.payout_txid IS NOT NULL
                AND w.is_payout_handled = FALSE
                AND w.payout_payer_operator_xonly_pk = $1
                ORDER BY w.idx ASC
             LIMIT 1",
        )
        .bind(XOnlyPublicKeyDB(operator_xonly_pk));

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        results
            .map(|(citrea_idx, move_to_vault_txid, payout_tx_blockhash)| {
                Ok((
                    u32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to u32")?,
                    move_to_vault_txid
                        .expect("move_to_vault_txid Must be Some")
                        .0,
                    payout_tx_blockhash
                        .expect("payout_tx_blockhash Must be Some")
                        .0,
                ))
            })
            .transpose()
    }
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
