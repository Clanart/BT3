### No vulnerability found for this question.

The target function `is_new_tenure` is a trivial match-based boolean predicate on `TenureChangeCause` that returns `true` only for `BlockFound` [1](#0-0) . It has no relationship to authorization structures, signatures, sighashes, or multisig key sets — it does not read or write any auth fields, `signer` hashes, `signatures_required`, or the sighash. It is invoked only from `stackslib/src/chainstate/stacks/db/transactions.rs` to decide log messaging around `TenureChange` transaction processing, not to authenticate or authorize signers [2](#0-1) .

The premise of the question — that reordering auth fields changes the authorized key set without changing the sighash, and that this divergence is reachable through `is_new_tenure` — has no code path support. `is_new_tenure`/`TenureChangeCause` do not participate in `verify_origin`, `verify`, `next_signature`, multisig field counting, or any sighash computation in this codebase area. There is no equality being broken here, and no attacker-controlled input to this function influences authentication logic.

### Citations

**File:** stacks-codec/src/transaction.rs (L162-173)
```rust
    /// Does this tenure change cause represent the start of a new tenure?
    pub fn is_new_tenure(&self) -> bool {
        match self {
            Self::BlockFound => true,
            Self::Extended => false,
            Self::ExtendedRuntime => false,
            Self::ExtendedReadCount => false,
            Self::ExtendedReadLength => false,
            Self::ExtendedWriteCount => false,
            Self::ExtendedWriteLength => false,
        }
    }
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L1420-1435)
```rust
            TransactionPayload::TenureChange(ref payload) => {
                // post-conditions are not allowed for this variant, since they're non-sensical.
                // Their presence in this variant makes the transaction invalid.
                if !tx.post_conditions.is_empty() {
                    let msg = "Invalid Stacks transaction: TenureChange transactions do not support post-conditions".to_string();
                    info!("{msg}");

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }

                if !payload.cause.is_new_tenure() {
                    debug!(
                        "TenureChange {:?} extends existing block tenure (confirms {} blocks)",
                        &payload.cause, &payload.previous_tenure_blocks
                    );
                }
```
