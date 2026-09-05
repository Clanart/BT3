No vulnerability found for this question.

The Lybra bug is a smart-contract design flaw where an ERC20 `allowance()` granted to a specific vault contract is implicitly treated as a public "you may use my tokens as a liquidation provider" consent for *any* third-party caller, letting a frontrunner insert themselves as the fee-collecting keeper. This is fundamentally a token-approval/authorization-semantics issue that lives entirely inside a single smart contract's business logic.

I searched the transaction/post-condition/auth code paths that this scan is scoped to — `check_transaction_postconditions` in [1](#0-0) , the Clarity `check_allowances`/`restrict-assets?`/`as-contract?` mechanism in [2](#0-1) , and the PoX `check-caller-allowed`/`allow-contract-caller` delegation pattern in [3](#0-2)  and [4](#0-3) . None of these exhibit the analogous flaw:

- Stacks post-conditions are explicit, transaction-scoped assertions attached and signed by the transaction's own signer, not an implicit standing approval that any other unrelated actor can invoke on the approver's behalf.
- The Clarity `restrict-assets?`/`as-contract?` allowance list is declared inline in the same execution and checked against actual asset movement in the same transaction — there is no separate "approve once, anyone can spend later" pattern analogous to ERC20 allowance reuse.
- The PoX `allowance-contract-callers` map is an explicit, opt-in delegation (`allow-contract-caller`) requiring the delegating principal's own signature to set up, not an implicit side effect of an unrelated action (like granting ERC20 allowance to a vault).

There is no unprivileged-sender path in this repo's auth/post-condition/mempool code where a signature or approval intended for one purpose is silently repurposed by a third party to move assets, charge fees, or bypass post-conditions, matching the required Critical/High impact categories.

### Citations

**File:** crates/stacks-transactions/src/lib.rs (L149-155)
```rust
pub fn check_transaction_postconditions(
    post_conditions: &[TransactionPostCondition],
    post_condition_mode: &TransactionPostConditionMode,
    origin_principal: &PrincipalData,
    asset_map: &AssetMap,
    epoch_id: StacksEpochId,
) -> Result<Option<String>, SerializationError> {
```

**File:** clarity/src/vm/functions/post_conditions.rs (L497-512)
```rust
/// Check the allowances against the asset map. If any assets moved without a
/// corresponding allowance return a `Some` with an index of the violated
/// allowance, or 128 if an asset with no allowance caused the violation. If all
/// allowances are satisfied, return `Ok(None)`.
fn check_allowances(
    owner: &PrincipalData,
    allowances: Vec<Allowance>,
    assets: &AssetMap,
    epoch: StacksEpochId,
) -> Result<Option<u128>, VmExecutionError> {
    let mut earliest_violation: Option<u128> = None;
    let record_violation = |earliest: &mut Option<u128>, candidate: u128| {
        if earliest.is_none_or(|current| candidate < current) {
            *earliest = Some(candidate);
        }
    };
```

**File:** stackslib/src/chainstate/stacks/boot/pox-2.clar (L246-259)
```text
(define-read-only (check-caller-allowed)
    (or (is-eq tx-sender contract-caller)
        (let ((caller-allowed
                 ;; if not in the caller map, return false
                 (unwrap! (map-get? allowance-contract-callers
                                    { sender: tx-sender, contract-caller: contract-caller })
                          false))
               (expires-at
                 ;; if until-burn-ht not set, then return true (because no expiry)
                 (unwrap! (get until-burn-ht caller-allowed) true)))
          ;; is the caller allowance expired?
          (if (>= burn-block-height expires-at)
              false
              true))))
```

**File:** stackslib/src/chainstate/stacks/boot/pox-3.clar (L561-571)
```text
;; Give a contract-caller authorization to call stacking methods
;;  normally, stacking methods may only be invoked by _direct_ transactions
;;   (i.e., the tx-sender issues a direct contract-call to the stacking methods)
;;  by issuing an allowance, the tx-sender may call through the allowed contract
(define-public (allow-contract-caller (caller principal) (until-burn-ht (optional uint)))
  (begin
    (asserts! (is-eq tx-sender contract-caller)
              (err ERR_STACKING_PERMISSION_DENIED))
    (ok (map-set allowance-contract-callers
               { sender: tx-sender, contract-caller: caller }
               { until-burn-ht: until-burn-ht }))))
```
