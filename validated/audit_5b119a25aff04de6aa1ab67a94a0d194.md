### Title
Zero Operators Causes `get_num_required_nofn_sigs` to Return Zero, Bypassing All Signature Count Guards and Permanently Locking Bridged BTC — (`File: core/src/builder/sighash.rs`, `core/src/rpc/verifier.rs`)

### Summary

`get_num_required_nofn_sigs` multiplies by `deposit_data.get_num_operators()` as its outermost factor. When zero operators are registered in the system, this function returns `0`, causing every downstream signature-count guard to pass trivially (`0 < 0` is `false`). A deposit can then be finalized through the full aggregator → verifier pipeline with no operator signatures, moving BTC into the vault with no operator able to ever initiate a kickoff or withdrawal, permanently locking the bridged funds.

### Finding Description

**Root cause — `get_num_required_nofn_sigs` collapses to zero:**

```rust
// core/src/builder/sighash.rs  lines 50-55
pub fn get_num_required_nofn_sigs(&self, deposit_data: &DepositData) -> usize {
    deposit_data.get_num_operators()          // ← 0 when no operators registered
        * self.protocol_paramset().num_round_txs
        * self.protocol_paramset().num_signed_kickoffs
        * self.get_num_required_nofn_sigs_per_kickoff(deposit_data)
}
```

`get_num_required_nofn_sigs_per_kickoff` always returns `≥ 7` (it is `7 + 4*num_verifiers + assert_txs*2`), so the per-kickoff count is never zero. The collapse is entirely caused by the `num_operators` multiplier.

**Guard bypass in the verifier RPC handler:**

```rust
// core/src/rpc/verifier.rs  lines 468-510
let num_required_nofn_sigs = verifier.config.get_num_required_nofn_sigs(&deposit_data);
// num_required_nofn_sigs == 0 → the while loop body never executes
// nonce_idx stays at 0
if nonce_idx < num_required_nofn_sigs {   // 0 < 0 → false → guard skipped
    return Err(Status::invalid_argument(...));
}
```

The same collapse propagates to the operator-signature guard:

```rust
// core/src/rpc/verifier.rs  lines 530-570
let num_required_op_sigs = verifier.config.get_num_required_operator_sigs(&deposit_data);
let num_operators = deposit_data.get_num_operators();          // 0
let num_required_total_op_sigs = num_required_op_sigs * num_operators;  // 0
// ...
if total_op_sig_count < num_required_total_op_sigs {  // 0 < 0 → false → guard skipped
    return Err(Status::invalid_argument(...));
}
```

**Aggregator side — nonce request also collapses:**

```rust
// core/src/rpc/aggregator.rs  lines 1496-1497
let num_required_sigs = self.config.get_num_required_nofn_sigs(&deposit_data);
let num_required_nonces = num_required_sigs as u32 + 2;  // 0 + 2 = 2
```

Only the move-tx and emergency-stop nonces are requested; all kickoff-related nonces and signatures are silently skipped.

**Deposit data construction — zero operators is a reachable state:**

```rust
// core/src/rpc/aggregator.rs  lines 1464-1473
let deposit_data = DepositData {
    deposit: deposit_info.clone(),
    nofn_xonly_pk: None,
    actors: Actors {
        verifiers: self.fetch_verifier_keys().await?,
        watchtowers: vec![],
        operators: self.fetch_operator_keys().await?,  // empty Vec when no operators in DB
    },
    security_council: self.config.security_council.clone(),
};
```

**Verifier validation — no minimum-operator check:**

`is_deposit_valid` in `core/src/verifier.rs` (lines 541–731) checks security council, watchtower caps, uniqueness of actors, and that every operator in the deposit is in the DB and vice versa. When both the deposit and the DB have zero operators, all of these checks pass. There is no guard of the form `if operators.is_empty() { return Err(...) }`.

**End-to-end exploit path:**

1. System is in a state with zero registered operators (e.g., initial bootstrap, or all operators have exited and their collateral is spent).
2. Any user (or attacker front-running a legitimate deposit) calls `new_deposit` on the aggregator gRPC endpoint.
3. Aggregator builds `deposit_data` with `operators: []`.
4. `get_num_required_nofn_sigs` returns `0`; `num_required_nonces = 2`.
5. Verifiers sign only the move-tx and emergency-stop; all kickoff-tree signatures are skipped.
6. The move-to-vault transaction is broadcast; BTC enters the vault locked under the N-of-N verifier key.
7. No operator exists to call kickoff → no withdrawal path → BTC is permanently locked.

### Impact Explanation

Permanent, irrecoverable lock of all BTC deposited while the system has zero operators. The vault is sealed under the N-of-N verifier key with no operator-controlled kickoff UTXOs ever created. The security-council replacement-deposit path requires a valid prior move-tx, which exists, but the replacement deposit would face the same zero-operator problem unless operators are added first. This is a direct, material loss of bridged BTC for depositors.

### Likelihood Explanation

The zero-operator state is reachable without any privileged action:
- During initial system deployment before any operator calls `set_operator`.
- After all operators' collateral is spent/slashed and they are removed from the active set.

Any user who calls `new_deposit` during this window triggers the permanent lock. No special keys or credentials are required.

### Recommendation

Add an explicit minimum-operator guard in two places:

1. **In `is_deposit_valid` (`core/src/verifier.rs`):** Reject any deposit whose `actors.operators` list is empty.
2. **In the aggregator's `new_deposit` (`core/src/rpc/aggregator.rs`):** Before constructing `deposit_data`, assert that `fetch_operator_keys()` returns at least one key, and return an error otherwise.

Optionally, add a non-zero assertion inside `get_num_required_nofn_sigs` itself so that a zero result is treated as a programming error rather than a valid count.

### Proof of Concept

**Precondition:** Aggregator and verifier nodes are running; no operator has called `set_operator`, so `db.get_operators()` returns `[]`.

**Step 1:** User sends a valid Bitcoin deposit UTXO of `bridge_amount` to the expected taproot address (computed from the N-of-N verifier key, which is non-zero).

**Step 2:** User calls `new_deposit` on the aggregator gRPC with the deposit outpoint.

**Step 3 (aggregator):** `fetch_operator_keys()` returns `[]`. `deposit_data.get_num_operators() == 0`. `get_num_required_nofn_sigs` returns `0`. `num_required_nonces = 2`.

**Step 4 (verifier RPC handler):** `num_required_nofn_sigs == 0`. The nonce-collection loop body never executes. `nonce_idx == 0`. Guard `0 < 0` is `false` → passes. `num_required_total_op_sigs == 0`. Guard `0 < 0` is `false` → passes.

**Step 5:** Verifiers sign the move-to-vault transaction. Aggregator broadcasts it. BTC is moved to the vault.

**Step 6:** No operator exists. No kickoff transaction can ever be created. BTC is permanently locked.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/src/builder/sighash.rs (L50-55)
```rust
    pub fn get_num_required_nofn_sigs(&self, deposit_data: &DepositData) -> usize {
        deposit_data.get_num_operators()
            * self.protocol_paramset().num_round_txs
            * self.protocol_paramset().num_signed_kickoffs
            * self.get_num_required_nofn_sigs_per_kickoff(deposit_data)
    }
```

**File:** core/src/rpc/verifier.rs (L468-510)
```rust
            let num_required_nofn_sigs = verifier.config.get_num_required_nofn_sigs(&deposit_data);
            tracing::debug!(
                "Needed nofn sigs for deposit {:?}: {}",
                deposit_data,
                num_required_nofn_sigs
            );
            let mut nonce_idx = 0;
            while let Some(sig) =
                parser::verifier::parse_next_deposit_finalize_param_schnorr_sig(&mut in_stream)
                    .await
                    .wrap_err_with(|| {
                        format!(
                            "While waiting for the {}th signature out of {}",
                            nonce_idx + 1,
                            num_required_nofn_sigs
                        )
                    })
                    .map_to_status()?
            {
                tracing::trace!(
                    "Received full nofn sig {} in deposit_finalize()",
                    nonce_idx + 1
                );
                sig_tx
                    .send(sig)
                    .await
                    .map_err(error::output_stream_ended_prematurely)?;
                tracing::debug!(
                    "Sent full nofn sig {} to src/verifier in deposit_finalize()",
                    nonce_idx + 1
                );
                nonce_idx += 1;
                if nonce_idx == num_required_nofn_sigs {
                    break;
                }
            }
            if nonce_idx < num_required_nofn_sigs {
                let err_msg = format!(
                    "Insufficient N-of-N signatures received: got {nonce_idx}, expected {num_required_nofn_sigs}",
                );
                tracing::error!("{err_msg}");
                return Err(Status::invalid_argument(err_msg));
            }
```

**File:** core/src/rpc/verifier.rs (L530-570)
```rust
            let num_required_op_sigs = verifier
                .config
                .get_num_required_operator_sigs(&deposit_data);
            let num_operators = deposit_data.get_num_operators();
            let num_required_total_op_sigs = num_required_op_sigs * num_operators;
            let mut total_op_sig_count = 0;
            for _ in 0..num_operators {
                let mut op_sig_count = 0;

                while let Some(operator_sig) =
                    parser::verifier::parse_next_deposit_finalize_param_schnorr_sig(&mut in_stream)
                        .await?
                {
                    tracing::trace!(
                        "Received full operator sig {} in deposit_finalize()",
                        op_sig_count + 1
                    );
                    operator_sig_tx
                        .send(operator_sig)
                        .await
                        .map_err(error::output_stream_ended_prematurely)?;
                    tracing::trace!(
                        "Sent full operator sig {} to src/verifier in deposit_finalize()",
                        op_sig_count + 1
                    );

                    op_sig_count += 1;
                    total_op_sig_count += 1;
                    if op_sig_count == num_required_op_sigs {
                        break;
                    }
                }
            }

            if total_op_sig_count < num_required_total_op_sigs {
                let err_msg = format!(
                    "Insufficient operator signatures received: got {total_op_sig_count}, expected {num_required_total_op_sigs}",
                );
                tracing::error!("{err_msg}");
                return Err(Status::invalid_argument(err_msg));
            }
```

**File:** core/src/rpc/aggregator.rs (L1464-1473)
```rust
            let deposit_data = DepositData {
                deposit: deposit_info.clone(),
                nofn_xonly_pk: None,
                actors: Actors {
                    verifiers: self.fetch_verifier_keys().await?,
                    watchtowers: vec![],
                    operators: self.fetch_operator_keys().await?,
                },
                security_council: self.config.security_council.clone(),
            };
```

**File:** core/src/rpc/aggregator.rs (L1496-1497)
```rust
            let num_required_sigs = self.config.get_num_required_nofn_sigs(&deposit_data);
            let num_required_nonces = num_required_sigs as u32 + 2; // ask for +2 for the final movetx signature + emergency stop signature, but don't send it on deposit_sign stage
```

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
