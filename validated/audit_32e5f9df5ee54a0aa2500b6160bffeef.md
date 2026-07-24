### Title
Verifier Signs Deposit with Non-Finalized `deposit_blockhash`, Enabling Bitcoin Reorg to Permanently Corrupt Kickoff Signatures — (`core/src/verifier.rs`)

---

### Summary

`Verifier::is_deposit_valid` only checks that the deposit transaction is confirmed (has any `blockhash`), but never checks that it has reached `finality_depth` confirmations. Both `deposit_sign` and `deposit_finalize` then call `get_blockhash_of_tx` to obtain the `deposit_blockhash` and feed it into `create_nofn_sighash_stream` / `create_operator_sighash_stream`. The `deposit_blockhash` is the sole entropy source for `get_kickoff_utxos_to_sign`, which selects which kickoff UTXOs are pre-signed for this deposit. If a Bitcoin reorg moves the deposit transaction into a different block between signing and move-tx confirmation, the stored signatures are bound to the wrong kickoff UTXOs, permanently breaking the operator's reimbursement path for that deposit.

---

### Finding Description

**Step 1 — No finality check in `is_deposit_valid`.**

`is_deposit_valid` verifies the deposit outpoint is on-chain and the block height is ≥ `start_height`, but it never checks that the deposit has `finality_depth` confirmations:

```rust
// core/src/verifier.rs  ~line 706-730
let tx_info = self.rpc.get_raw_transaction_info(&deposit_txid, None).await?;
let blockhash = tx_info.blockhash.ok_or_else(|| {
    BridgeError::InvalidDeposit("Deposit transaction is not confirmed".to_string())
})?;
let block_height = self.rpc.get_block_info(&blockhash).await?.height;
let start_height = self.config.protocol_paramset().start_height;
if (block_height as u32) < start_height { ... }
Ok(())   // ← no confirmation-count check
``` [1](#0-0) 

**Step 2 — `deposit_sign` fetches and locks in the blockhash immediately after the single-confirmation check.**

```rust
// core/src/verifier.rs  ~line 876-897
self.is_deposit_valid(&mut deposit_data).await?;   // only 1-conf required
...
let deposit_blockhash = self
    .rpc
    .get_blockhash_of_tx(&deposit_data.get_deposit_outpoint().txid)
    .await?;
// deposit_blockhash is now used for ALL sighash generation
``` [2](#0-1) 

**Step 3 — `deposit_blockhash` is the sole entropy for kickoff UTXO selection.**

`create_nofn_sighash_stream` passes `deposit_blockhash` to `get_kickoff_utxos_to_sign`, which deterministically selects which kickoff UTXOs are pre-signed for this deposit. Every NofN sighash is computed over transactions referencing those specific UTXOs:

```rust
// core/src/builder/sighash.rs  ~line 222-226
let utxo_idxs = get_kickoff_utxos_to_sign(
    config.protocol_paramset(),
    *op_xonly_pk,
    deposit_blockhash,          // ← wrong if reorg occurs
    deposit_data.get_deposit_outpoint(),
);
``` [3](#0-2) 

The same pattern is repeated in `deposit_finalize` and in `Operator::deposit_sign`: [4](#0-3) [5](#0-4) 

**Step 4 — The aggregator also fetches the blockhash without a finality check.**

```rust
// core/src/rpc/aggregator.rs  ~line 1603-1607
let deposit_blockhash = self
    .rpc
    .get_blockhash_of_tx(&deposit_data.get_deposit_outpoint().txid)
    .await
    .map_to_status()?;
``` [6](#0-5) 

**Step 5 — The move tx is independent of `deposit_blockhash`.**

The move tx spends the deposit UTXO by `txid:vout`. Bitcoin txids are content-addressed and do not change when a transaction is re-mined in a different block. Therefore, after a reorg that moves the deposit tx from block A to block B:
- The move tx remains valid and can be confirmed.
- The stored kickoff signatures remain bound to `blockhash_A`-derived UTXOs.
- The correct UTXOs (derived from `blockhash_B`) were never signed.

**Step 6 — Signatures are persisted before the move tx is confirmed.**

`deposit_finalize` stores all NofN and operator signatures in the database before the move tx is broadcast. Once the move tx is confirmed, the deposit is finalized with the wrong kickoff signatures permanently in the DB. [7](#0-6) 

---

### Impact Explanation

The `deposit_blockhash` selects which kickoff UTXOs are pre-signed for a deposit. If the wrong blockhash is used, the stored signatures authorize kickoff transactions for the wrong UTXOs. After the move tx is confirmed:

- The operator cannot initiate a kickoff for this deposit (signatures are for wrong UTXOs).
- The operator cannot obtain reimbursement from the bridge vault for any withdrawal they paid out against this deposit.
- The operator's payout funds (up to `bridge_amount` = 10 BTC per deposit) are permanently unrecoverable through the normal reimbursement path.
- User funds in the vault are safe, but the operator's reimbursement outputs are permanently locked.

This matches the allowed impact gate: **permanent lock of reimbursement outputs and bridge-controlled UTXOs**.

---

### Likelihood Explanation

- The `new_deposit` RPC can be called immediately after the deposit tx receives its first confirmation; no caller-side finality gate exists in production code.
- Bitcoin reorgs of 1–2 blocks occur naturally during normal network operation, especially on testnet4 (which Clementine targets).
- The signing pipeline takes non-trivial time (MuSig2 nonce aggregation + signature aggregation across N verifiers), widening the window during which a reorg can occur.
- The test harness explicitly mines `DEFAULT_FINALITY_DEPTH + 1` blocks before calling `new_deposit` (see `run_multiple_deposits`), confirming the developers are aware finality matters — but this guard is absent from production validation. [8](#0-7) 

---

### Recommendation

Add a finality-depth confirmation check inside `is_deposit_valid` before accepting the deposit for signing:

```rust
// After confirming the tx is on-chain, add:
let confirmations = self
    .rpc
    .confirmation_blocks(&deposit_txid)
    .await
    .wrap_err("Failed to get deposit confirmation count")?;
let required = self.config.protocol_paramset().finality_depth;
if confirmations < required {
    return Err(BridgeError::InvalidDeposit(format!(
        "Deposit has only {confirmations} confirmations, need {required}"
    )));
}
```

Apply the same guard in `Operator::deposit_sign` (which currently calls `get_blockhash_of_tx` with no validity check at all) and in the aggregator's `new_deposit` before initiating the signing pipeline. [9](#0-8) [10](#0-9) 

---

### Proof of Concept

1. User broadcasts deposit tx; it is mined into block A (1 confirmation).
2. Aggregator calls `new_deposit` immediately. `is_deposit_valid` passes (tx is confirmed). All verifiers and the operator call `get_blockhash_of_tx`, obtaining `blockhash_A`. Kickoff UTXOs `[i, j, k]` are selected and signed. Signatures are stored in the DB.
3. A natural 1-block reorg occurs. The deposit tx is re-mined into block B (`blockhash_B ≠ blockhash_A`). The correct kickoff UTXOs for this deposit are now `[x, y, z]`.
4. The move tx (which spends the deposit UTXO by txid, unchanged) is broadcast and confirmed. The deposit is finalized. The DB holds signatures for UTXOs `[i, j, k]`.
5. The operator pays out a withdrawal against this deposit and attempts to initiate a kickoff using the stored signatures. The kickoff transaction references UTXOs `[i, j, k]`, but the bridge's covenant requires UTXOs `[x, y, z]` for `blockhash_B`. The kickoff is invalid and cannot be broadcast.
6. The operator's payout funds are unrecoverable through the reimbursement path. [1](#0-0) [11](#0-10) [12](#0-11)

### Citations

**File:** core/src/verifier.rs (L541-731)
```rust
    async fn is_deposit_valid(&self, deposit_data: &mut DepositData) -> Result<(), BridgeError> {
        // check if security council is the same as in our config
        if deposit_data.security_council != self.config.security_council {
            let reason = format!(
                "Security council in deposit is not the same as in the config, expected {:?}, got {:?}",
                self.config.security_council,
                deposit_data.security_council
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        // check if extra watchtowers (non verifier watchtowers) are not greater than the maximum allowed
        if deposit_data.actors.watchtowers.len() > MAX_EXTRA_WATCHTOWERS {
            let reason = format!(
                "Number of extra watchtowers in deposit is greater than the maximum allowed, expected at most {}, got {}",
                MAX_EXTRA_WATCHTOWERS,
                deposit_data.actors.watchtowers.len()
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        // check if total watchtowers are not greater than the maximum allowed
        if deposit_data.get_num_watchtowers() > MAX_NUMBER_OF_WATCHTOWERS {
            let reason = format!(
                "Number of watchtowers in deposit is greater than the maximum allowed, expected at most {}, got {}",
                MAX_NUMBER_OF_WATCHTOWERS,
                deposit_data.get_num_watchtowers()
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all verifiers are unique
        if !deposit_data.are_all_verifiers_unique() {
            let reason = format!(
                "Verifiers in deposit are not unique: {:?}",
                deposit_data.actors.verifiers
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all watchtowers are unique
        if !deposit_data.are_all_watchtowers_unique() {
            let reason = format!(
                "Watchtowers in deposit are not unique: {:?}",
                deposit_data.actors.watchtowers
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all operators are unique
        if !deposit_data.are_all_operators_unique() {
            let reason = format!(
                "Operators in deposit are not unique: {:?}",
                deposit_data.actors.operators
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        let operators_in_deposit_data = deposit_data.get_operators();
        // check if all operators that still have collateral are in the deposit
        let operators_in_db = self.db.get_operators(None).await?;
        for (xonly_pk, reimburse_addr, collateral_funding_outpoint) in operators_in_db.iter() {
            let operator_data = OperatorData {
                xonly_pk: *xonly_pk,
                collateral_funding_outpoint: *collateral_funding_outpoint,
                reimburse_addr: reimburse_addr.clone(),
            };
            let kickoff_winternitz_pks = self
                .db
                .get_operator_kickoff_winternitz_public_keys(None, *xonly_pk)
                .await?;
            let kickoff_wpks = KickoffWinternitzKeys::new(
                kickoff_winternitz_pks,
                self.config.protocol_paramset().num_kickoffs_per_round,
                self.config.protocol_paramset().num_round_txs,
            )?;
            let is_collateral_usable = self
                .rpc
                .collateral_check(
                    &operator_data,
                    &kickoff_wpks,
                    self.config.protocol_paramset(),
                )
                .await?;
            // if operator is not in deposit but its collateral is still on chain, return false
            if !operators_in_deposit_data.contains(xonly_pk) && is_collateral_usable {
                let reason = format!(
                    "Operator {xonly_pk:?} is is still in protocol but not in the deposit data from aggregator",
                );
                tracing::error!("{reason}");
                return Err(BridgeError::InvalidDeposit(reason));
            }
            // if operator is in deposit, but the collateral is not usable, return false
            if operators_in_deposit_data.contains(xonly_pk) && !is_collateral_usable {
                let reason = format!(
                    "Operator {xonly_pk:?} is in the deposit data from aggregator but its collateral is spent, operator cannot fulfill withdrawals anymore",
                );
                tracing::error!("{reason}");
                return Err(BridgeError::InvalidDeposit(reason));
            }
        }
        // check if there are any operators in the deposit that are not in the DB.
        for operator_xonly_pk in operators_in_deposit_data {
            if !operators_in_db
                .iter()
                .any(|(xonly_pk, _, _)| xonly_pk == &operator_xonly_pk)
            {
                let reason = format!(
                    "Operator {operator_xonly_pk:?} is in the deposit data from aggregator but not in the verifier's DB, cannot sign deposit"
                );
                tracing::error!("{reason}");
                return Err(BridgeError::InvalidDeposit(reason));
            }
        }
        // check if deposit script in deposit_outpoint is valid
        let deposit_scripts: Vec<ScriptBuf> = deposit_data
            .get_deposit_scripts(self.config.protocol_paramset())?
            .into_iter()
            .map(|s| s.to_script_buf())
            .collect();
        // what the deposit scriptpubkey is in the deposit_outpoint should be according to the deposit data
        let expected_scriptpubkey = create_taproot_address(
            &deposit_scripts,
            None,
            self.config.protocol_paramset().network,
        )
        .0
        .script_pubkey();
        let deposit_outpoint = deposit_data.get_deposit_outpoint();
        let deposit_txid = deposit_outpoint.txid;
        let deposit_tx = self
            .rpc
            .get_tx_of_txid(&deposit_txid)
            .await
            .wrap_err("Deposit tx could not be found on chain")?;
        let deposit_txout_in_chain = deposit_tx
            .output
            .get(deposit_outpoint.vout as usize)
            .ok_or(eyre::eyre!(
                "Deposit vout not found in tx {}, vout: {}",
                deposit_txid,
                deposit_outpoint.vout
            ))?;
        if deposit_txout_in_chain.value != self.config.protocol_paramset().bridge_amount {
            let reason = format!(
                "Deposit amount is not correct, expected {}, got {}",
                self.config.protocol_paramset().bridge_amount,
                deposit_txout_in_chain.value
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        if deposit_txout_in_chain.script_pubkey != expected_scriptpubkey {
            let reason = format!(
                "Deposit script pubkey in deposit outpoint does not match the deposit data, expected {:?}, got {:?}",
                expected_scriptpubkey,
                deposit_txout_in_chain.script_pubkey
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        // check if deposit outpoint is included in a block with height >= start_height
        let tx_info = self
            .rpc
            .get_raw_transaction_info(&deposit_txid, None)
            .await
            .wrap_err("Failed to get deposit transaction info")?;
        let blockhash = tx_info.blockhash.ok_or_else(|| {
            BridgeError::InvalidDeposit("Deposit transaction is not confirmed".to_string())
        })?;
        let block_height = self
            .rpc
            .get_block_info(&blockhash)
            .await
            .wrap_err(format!(
                "Failed to get block info for deposit tx block hash: {blockhash}",
            ))?
            .height;
        let start_height = self.config.protocol_paramset().start_height;
        if (block_height as u32) < start_height {
            let reason = format!(
                "Deposit transaction is included in a block with height {block_height} which is less than start_height {start_height}",
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        Ok(())
```

**File:** core/src/verifier.rs (L876-897)
```rust
        self.is_deposit_valid(&mut deposit_data).await?;

        // set deposit data to db before starting to sign, ensures that if the deposit data already exists in db, it matches the one
        // given by the aggregator currently. We do not want to sign 2 different deposits for same deposit_outpoint
        self.db
            .insert_deposit_data_if_not_exists(
                None,
                &mut deposit_data,
                self.config.protocol_paramset(),
            )
            .await?;

        let verifier = self.clone();
        let (partial_sig_tx, partial_sig_rx) = mpsc::channel(constants::DEFAULT_CHANNEL_SIZE);
        let verifier_index = deposit_data.get_verifier_index(&self.signer.public_key)?;
        let verifiers_public_keys = deposit_data.get_verifiers();
        let monitor_sender = partial_sig_tx.clone();

        let deposit_blockhash = self
            .rpc
            .get_blockhash_of_tx(&deposit_data.get_deposit_outpoint().txid)
            .await?;
```

**File:** core/src/verifier.rs (L982-1008)
```rust
    pub async fn deposit_finalize(
        &self,
        deposit_data: &mut DepositData,
        session_id: u128,
        mut sig_receiver: mpsc::Receiver<Signature>,
        mut agg_nonce_receiver: mpsc::Receiver<AggregatedNonce>,
        mut operator_sig_receiver: mpsc::Receiver<Signature>,
    ) -> Result<(PartialSignature, PartialSignature), BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(deposit_data).await?;

        let mut tweak_cache = TweakCache::default();
        let deposit_blockhash = self
            .rpc
            .get_blockhash_of_tx(&deposit_data.get_deposit_outpoint().txid)
            .await?;

        let mut sighash_stream = pin!(create_nofn_sighash_stream(
            self.db.clone(),
            self.config.clone(),
            deposit_data.clone(),
            deposit_blockhash,
            true,
        ));
```

**File:** core/src/builder/sighash.rs (L206-230)
```rust
pub fn create_nofn_sighash_stream(
    db: Database,
    config: BridgeConfig,
    deposit_data: DepositData,
    deposit_blockhash: bitcoin::BlockHash,
    yield_kickoff_txid: bool,
) -> impl Stream<Item = Result<(TapSighash, SignatureInfo), BridgeError>> {
    try_stream! {
        let paramset = config.protocol_paramset();

        let operators = deposit_data.get_operators();

        for (operator_idx, op_xonly_pk) in
            operators.iter().enumerate()
        {

            let utxo_idxs = get_kickoff_utxos_to_sign(
                config.protocol_paramset(),
                *op_xonly_pk,
                deposit_blockhash,
                deposit_data.get_deposit_outpoint(),
            );
            // need to create new TxHandlerDbData for each operator
            let mut tx_db_data = ReimburseDbCache::new_for_deposit(db.clone(), *op_xonly_pk, deposit_data.get_deposit_outpoint(), config.protocol_paramset(), None);

```

**File:** core/src/operator.rs (L433-456)
```rust
    pub async fn deposit_sign(
        &self,
        mut deposit_data: DepositData,
    ) -> Result<mpsc::Receiver<Result<schnorr::Signature, BridgeError>>, BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        let mut tweak_cache = TweakCache::default();
        let (sig_tx, sig_rx) = mpsc::channel(constants::DEFAULT_CHANNEL_SIZE);
        let monitor_err_sender = sig_tx.clone();

        let deposit_blockhash = self
            .rpc
            .get_blockhash_of_tx(&deposit_data.get_deposit_outpoint().txid)
            .await?;

        let mut sighash_stream = Box::pin(create_operator_sighash_stream(
            self.db.clone(),
            self.signer.xonly_public_key,
            self.config.clone(),
            deposit_data,
            deposit_blockhash,
        ));
```

**File:** core/src/rpc/aggregator.rs (L1603-1607)
```rust
            let deposit_blockhash = self
                .rpc
                .get_blockhash_of_tx(&deposit_data.get_deposit_outpoint().txid)
                .await
                .map_to_status()?;
```

**File:** core/src/test/common/mod.rs (L348-349)
```rust
        rpc.mine_blocks_while_synced(DEFAULT_FINALITY_DEPTH + 1, actors, None)
            .await?;
```

**File:** crates/clementine-config/src/protocol.rs (L118-122)
```rust
    /// Amount of depth a block should have from the current head to be considered finalized
    /// Also means finality depth, how many confirmations are needed for a block to be considered finalized
    /// The chain tip has 1 confirmation. Minimum value should be 1.
    pub finality_depth: u32,
    /// start height to sync the chain from, i.e. the height bridge was deployed
```
