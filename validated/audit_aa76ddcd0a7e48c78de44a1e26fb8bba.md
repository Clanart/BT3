### Title
Deposit Finalized Without BitVM Setup Data Disables Fraud-Proof Challenge Capability — (`core/src/verifier.rs`)

### Summary

The verifier's `deposit_sign` and `deposit_finalize` functions call `is_deposit_valid`, which checks that operators are registered in the DB but does **not** verify that per-deposit BitVM setup data (`bitvm_setups`, `operator_challenge_ack_hashes`, `operator_bitvm_winternitz_public_keys`) has been stored via `set_operator_keys`. A deposit can therefore be fully finalized — the `MoveToVault` transaction broadcast and BTC locked in the vault — while the verifier permanently lacks the data required to challenge fraudulent kickoffs for that deposit.

### Finding Description

The deposit lifecycle in Clementine requires two distinct setup phases before a deposit is finalized:

1. **`set_operator`** (setup phase) — registers the operator, stores kickoff Winternitz keys and unspent-kickoff signatures.
2. **`set_operator_keys`** (per-deposit phase) — stores per-deposit BitVM assert-tx addresses, disprove root hash, latest-blockhash root hash (`bitvm_setups`), challenge-ACK hashes, and BitVM Winternitz public keys.

The aggregator is expected to call `set_operator_keys` on every verifier before calling `deposit_sign`. However, the verifier enforces only the first prerequisite. `is_deposit_valid` checks that operators are present in the `operators` table (populated by `set_operator`), but never queries `bitvm_setups` or `operator_challenge_ack_hashes`: [1](#0-0) 

Both `deposit_sign` and `deposit_finalize` call `is_deposit_valid` and nothing else guards the BitVM-setup prerequisite: [2](#0-1) [3](#0-2) 

`set_operator_keys` and `deposit_sign` are independent gRPC endpoints: [4](#0-3) 

Because they are separate calls, a malicious or buggy aggregator can invoke `deposit_sign` → `deposit_finalize` without ever calling `set_operator_keys`. The verifier will produce valid partial signatures, the aggregator will assemble the final `MoveToVault` transaction, and BTC will be locked in the vault — all without the verifier possessing the data it needs to later challenge kickoffs.

When the operator subsequently broadcasts a kickoff and the verifier attempts to handle it, `ReimburseDbCache::get_bitvm_setup` returns `None` and the code returns `TxError::BitvmSetupNotFound`: [5](#0-4) 

The `bitvm_setups` table schema confirms the data is keyed per `(xonly_pk, deposit_id)` and cannot be reconstructed from on-chain data alone: [6](#0-5) 

### Impact Explanation

Once the `MoveToVault` transaction is confirmed, the BTC is locked in the N-of-N vault. The only way to release it legitimately is through the reimbursement path, which requires the verifier to be able to challenge fraudulent kickoffs. Without `bitvm_setups`:

- The verifier cannot verify that a kickoff's assert-tx addresses match the expected ones.
- The verifier cannot construct the disprove transaction.
- A fraudulent operator can broadcast a kickoff claiming reimbursement for a withdrawal that never occurred, and no verifier with missing setup data can challenge it.
- The operator drains the vault UTXO corresponding to that deposit.

This constitutes a permanent loss of bridged BTC for the affected deposit.

### Likelihood Explanation

The aggregator is expected to call `set_operator_keys` before `deposit_sign`. In the normal aggregator flow (`collect_and_distribute_keys` → nonce generation → signing), this ordering is maintained and a failure in `set_operator_keys` aborts the whole deposit. However:

- The verifier exposes `DepositSign` and `DepositFinalize` as independent gRPC endpoints with no server-side enforcement of the prerequisite.
- A compromised, buggy, or race-conditioned aggregator can reach `deposit_sign` without completing `set_operator_keys` on all verifiers.
- Because the verifier is the security layer (the protocol assumes at least one honest verifier), it must independently enforce all prerequisites rather than trusting the aggregator's call ordering.

### Recommendation

Add a check inside `is_deposit_valid` (or at the top of `deposit_finalize`, where the commitment is irrevocable) that queries `bitvm_setups` for every operator in the deposit and returns `BridgeError::InvalidDeposit` if any entry is absent:

```rust
// In is_deposit_valid, after the operator-in-DB check:
for operator_xonly_pk in deposit_data.get_operators() {
    if self.db
        .get_bitvm_setup(None, operator_xonly_pk, deposit_data.get_deposit_outpoint())
        .await?
        .is_none()
    {
        return Err(BridgeError::InvalidDeposit(format!(
            "BitVM setup not found for operator {operator_xonly_pk:?}, \
             set_operator_keys must be called before deposit_finalize"
        )));
    }
}
```

The same guard should cover `operator_challenge_ack_hashes` and `operator_bitvm_winternitz_public_keys` for completeness. [7](#0-6) 

### Proof of Concept

```
1. Register operator via set_operator (collateral on-chain, kickoff WPKs stored).
2. Call aggregator.new_deposit() but intercept/drop the set_operator_keys RPC
   call to all verifiers (e.g., by using a proxy that silently drops that
   specific endpoint).
3. Allow deposit_sign and deposit_finalize to proceed normally.
   → is_deposit_valid passes (operator is in DB from step 1).
   → Verifiers produce partial signatures.
   → Aggregator assembles MoveToVault tx and broadcasts it.
   → BTC is now locked in vault; deposit_id recorded in DB.
4. Confirm: db.get_bitvm_setup(operator_pk, deposit_outpoint) returns None
   on every verifier.
5. Operator broadcasts a fraudulent KickoffTx (no real withdrawal occurred).
6. Verifier's handle_kickoff calls create_kickoff_txhandler →
   ReimburseDbCache::get_bitvm_assert_hash → get_bitvm_setup → None
   → TxError::BitvmSetupNotFound.
   Verifier cannot construct or broadcast the challenge/disprove transaction.
7. Challenge window expires; operator claims reimbursement from vault.
   Bridged BTC is stolen.
```

### Citations

**File:** core/src/verifier.rs (L541-658)
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
```

**File:** core/src/verifier.rs (L866-876)
```rust
    pub async fn deposit_sign(
        &self,
        mut deposit_data: DepositData,
        session_id: u128,
        mut agg_nonce_rx: mpsc::Receiver<AggregatedNonce>,
    ) -> Result<mpsc::Receiver<Result<PartialSignature, BridgeError>>, BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(&mut deposit_data).await?;
```

**File:** core/src/verifier.rs (L982-994)
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
```

**File:** core/src/verifier.rs (L1715-1759)
```rust
    pub async fn set_operator_keys(
        &self,
        mut deposit_data: DepositData,
        keys: OperatorKeys,
        operator_xonly_pk: XOnlyPublicKey,
    ) -> Result<(), BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(&mut deposit_data).await?;

        self.db
            .insert_deposit_data_if_not_exists(
                Some(&mut dbtx),
                &mut deposit_data,
                self.config.protocol_paramset(),
            )
            .await?;

        let hashes: Vec<[u8; 20]> = keys
            .challenge_ack_digests
            .into_iter()
            .map(|x| {
                x.hash.try_into().map_err(|e: Vec<u8>| {
                    eyre::eyre!("Invalid hash length, expected 20 bytes, got {}", e.len())
                })
            })
            .collect::<Result<Vec<[u8; 20]>, eyre::Report>>()?;

        if hashes.len() != self.config.get_num_challenge_ack_hashes(&deposit_data) {
            return Err(eyre::eyre!(
                "Invalid number of challenge ack hashes received from operator {:?}: got: {} expected: {}",
                operator_xonly_pk,
                hashes.len(),
                self.config.get_num_challenge_ack_hashes(&deposit_data)
            ).into());
        }

        let operator_data = self
            .db
            .get_operator(Some(&mut dbtx), operator_xonly_pk)
            .await?
            .ok_or(BridgeError::OperatorNotFound(operator_xonly_pk))?;
```

**File:** core/src/rpc/clementine.proto (L596-617)
```text
  // Sets the operator's winternitz keys and challenge ACK hashes and saves them
  // into the db.
  //
  // Used by aggregator inside new_deposit to let all verifiers know all other
  // operators' deposit information
  rpc SetOperatorKeys(OperatorKeysWithDeposit) returns (Empty) {}

  // Generates nonces for a deposit.
  //
  // # Returns
  //
  // Nonce metadata followed by nonces.
  //
  // Used by aggregator inside new_deposit
  rpc NonceGen(NonceGenRequest) returns (stream NonceGenResponse) {}

  // Signs deposit with given aggNonces and verifier's secNonce using
  // nonce_id.
  //
  // Used by aggregator inside new_deposit
  rpc DepositSign(stream VerifierDepositSignParams)
      returns (stream PartialSig) {}
```

**File:** core/src/builder/transaction/creator.rs (L247-264)
```rust
    async fn get_bitvm_setup(&mut self, deposit_outpoint: OutPoint) -> Result<(), BridgeError> {
        let (assert_addr, bitvm_hash, latest_blockhash_root_hash) = self
            .db
            .get_bitvm_setup(
                self.dbtx.as_deref_mut(),
                self.operator_xonly_pk,
                deposit_outpoint,
            )
            .await
            .wrap_err("Failed to get bitvm setup in ReimburseDbCache::get_bitvm_setup")?
            .ok_or(TxError::BitvmSetupNotFound(
                self.operator_xonly_pk,
                deposit_outpoint.txid,
            ))?;
        self.bitvm_assert_addr = Some(assert_addr);
        self.bitvm_disprove_root_hash = Some(bitvm_hash);
        self.latest_blockhash_root_hash = Some(latest_blockhash_root_hash);
        Ok(())
```

**File:** core/src/database/schema.sql (L77-86)
```sql
create table if not exists bitvm_setups (
    xonly_pk text not null,
    deposit_id int not null,
    assert_tx_addrs bytea [] not null,
    root_hash bytea not null check (length(root_hash) = 32),
    latest_blockhash_root_hash bytea not null check (length(latest_blockhash_root_hash) = 32),
    --public_input_wots bytea[] not null,
    created_at timestamp not null default now(),
    primary key (xonly_pk, deposit_id)
);
```
