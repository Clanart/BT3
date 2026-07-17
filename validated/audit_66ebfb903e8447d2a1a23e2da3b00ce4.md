### Title
`max_receipt_size` Cap Not Re-Enforced After `output_data_receivers` Mutation Allows Oversized Receipts Into Chain State - (File: `runtime/runtime/src/verifier.rs`)

---

### Summary

`max_receipt_size` is defined in `LimitConfig` and enforced in `validate_receipt()` only at initial receipt creation (`ValidateReceiptMode::NewReceipt`). However, when a contract executes `promise_return`, the runtime appends `output_data_receivers` to an already-validated receipt, growing it beyond `max_receipt_size`. The re-validation of this mutated receipt uses `ValidateReceiptMode::ExistingReceipt`, which explicitly skips the size check. The oversized receipt is then committed to chain state and forwarded cross-shard, breaking the `ChunkStateWitness` size invariant.

---

### Finding Description

`validate_receipt()` in `runtime/runtime/src/verifier.rs` gates the size check behind a mode guard:

```rust
if mode == ValidateReceiptMode::NewReceipt {
    let receipt_size: u64 = borsh::object_length(receipt)...;
    if receipt_size > limit_config.max_receipt_size {
        return Err(ReceiptValidationError::ReceiptSizeExceeded { ... });
    }
}
``` [1](#0-0) 

`ValidateReceiptMode::ExistingReceipt` is documented to intentionally skip this check:

```
2) There is a bug which allows to create receipts that are above the size limit. Runtime has
   to handle them gracefully until the receipt size limit bug is fixed.
   See https://github.com/near/nearcore/issues/12606 for details.
``` [2](#0-1) 

The attack path is:

1. A user deploys a contract that creates a new receipt C whose serialized size equals exactly `max_receipt_size` (4,194,304 bytes). At creation time, `validate_receipt(..., NewReceipt)` passes.
2. The same contract calls `promise_return(C)` inside a promise chain `[A -then-> B]`. The runtime resolves the return by appending `output_data_receivers` (the data receiver for B) to receipt C's `ActionReceipt`.
3. After this mutation, receipt C's borsh-serialized size exceeds `max_receipt_size`.
4. All subsequent validations of receipt C use `ValidateReceiptMode::ExistingReceipt`, which skips the size check entirely.
5. The oversized receipt is stored in state and forwarded cross-shard.

The same path exists for `value_return`: returning a value of size `max_receipt_size` causes the runtime to wrap it in a `DataReceipt` that exceeds the limit. [3](#0-2) 

Both `process_incoming_receipts` and `process_delayed_receipts` call `validate_receipt` with `ExistingReceipt` mode, so the size check is never re-applied after the mutation: [4](#0-3) [5](#0-4) 

A downstream workaround in `try_forward` clamps the reported size to `max_receipt_size` to prevent receipts from getting stuck in the outgoing buffer, but this does not prevent the oversized receipt from entering state or the witness: [6](#0-5) 

---

### Impact Explanation

The `ChunkStateWitness` size invariant is designed to keep the uncompressed witness under ~17 MiB. `max_receipt_size = 4 MiB` is a load-bearing component of that budget. An oversized receipt (up to ~8 MiB: 4 MiB args + 4 MiB `output_data_receivers`) can push the witness over its intended ceiling. Chunk validators independently re-execute chunk application; if their recorded trie proof or receipt set diverges from the chunk producer's due to size-limit disagreement, they will not endorse the chunk, stalling finality for the affected shard. The oversized receipt is also committed to the outgoing receipt Merkle tree and forwarded cross-shard, propagating the invariant violation.

---

### Likelihood Explanation

Any unprivileged user who can deploy a contract (no special role required) can trigger this by crafting a function call that creates a near-maximum-size receipt and then calls `promise_return`. The technique is demonstrated in the existing test suite, confirming it is reachable in production. The `max_receipt_size` of 4 MiB is large enough that the required gas fits within a single chunk's gas limit. [7](#0-6) 

---

### Recommendation

After the runtime appends `output_data_receivers` (or wraps a return value in a `DataReceipt`), re-run `validate_receipt(..., ValidateReceiptMode::NewReceipt)` on the mutated receipt before storing or forwarding it. If the post-mutation size exceeds `max_receipt_size`, the originating function call should fail with `NewReceiptValidationError::ReceiptSizeExceeded`, exactly as it does for directly oversized receipts today. Once this check is in place, `ValidateReceiptMode::ExistingReceipt` can drop its size-check exemption.

---

### Proof of Concept

The nearcore test suite already contains a working proof of concept:

```
test-loop-tests/src/tests/max_receipt_size.rs
  fn test_max_receipt_size_promise_return()   // promise_return path
  fn test_max_receipt_size_value_return()     // value_return path
```

Both tests call `assert_oversized_receipt_occurred()`, which scans the chain store and asserts that a receipt with `borsh_size > max_receipt_size` was committed to state — confirming the invariant is violated in a live test environment. [8](#0-7) [9](#0-8)

### Citations

**File:** runtime/runtime/src/verifier.rs (L533-542)
```rust
    if mode == ValidateReceiptMode::NewReceipt {
        let receipt_size: u64 =
            borsh::object_length(receipt).unwrap().try_into().expect("Can't convert usize to u64");
        if receipt_size > limit_config.max_receipt_size {
            return Err(ReceiptValidationError::ReceiptSizeExceeded {
                size: receipt_size,
                limit: limit_config.max_receipt_size,
            });
        }
    }
```

**File:** runtime/runtime/src/verifier.rs (L573-586)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidateReceiptMode {
    /// Used for validating new receipts that were just created.
    /// More strict than `OldReceipt` mode, which has to handle older receipts.
    NewReceipt,
    /// Used for validating older receipts that were saved in the state/received. Less strict than
    /// NewReceipt validation. Tolerates some receipts that wouldn't pass new validation. It has to
    /// be less strict because:
    /// 1) Older receipts might have been created before new validation rules.
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
}
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L150-191)
```rust
    // User calls a contract method
    // Contract method creates a DAG with two promises: [A -then-> B]
    // When promise A is executed, it creates a third promise - `C` and does a `promise_return`.
    // The DAG changes to: [C ->then-> B]
    // The receipt for promise C is a maximum size receipt.
    // Adding the `output_data_receivers` to C's receipt makes it go over the size limit.
    let base_receipt_template = Receipt::V0(ReceiptV0 {
        predecessor_id: account.clone(),
        receiver_id: account.clone(),
        receipt_id: CryptoHash::default(),
        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: account.clone(),
            signer_public_key: account_signer.public_key().into(),
            gas_price: Balance::ZERO,
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: vec![Action::FunctionCall(Box::new(FunctionCallAction {
                method_name: "noop".into(),
                args: vec![],
                gas: Gas::ZERO,
                deposit: Balance::ZERO,
            }))],
        }),
    });
    let base_receipt_template = action_receipt_v1_to_latest(&base_receipt_template);
    let base_receipt_size = borsh::object_length(&base_receipt_template).unwrap();
    let max_receipt_size = 4_194_304;
    let args_size = max_receipt_size - base_receipt_size;

    // Call the contract
    let large_receipt_tx = SignedTransaction::call(
        102,
        account.clone(),
        account.clone(),
        &account_signer,
        Balance::ZERO,
        "max_receipt_size_promise_return_method1".into(),
        format!("{{\"args_size\": {}}}", args_size).into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(large_receipt_tx, Duration::seconds(5));
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-267)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
/// A[self.return_large_value()] -then-> B[self.mark_test_completed()]
#[test]
fn test_max_receipt_size_value_return() {
    init_test_logger();

    let account = create_account_id("account0");
    let account_signer = create_user_test_signer(&account);
    let mut env = TestLoopBuilder::new()
        .enable_rpc()
        .add_user_account(&account, Balance::from_near(10_000))
        .build();

    // Deploy the test contract
    let deploy_contract_tx = SignedTransaction::deploy_contract(
        101,
        &account,
        near_test_contracts::rs_contract().into(),
        &account_signer,
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(deploy_contract_tx, Duration::seconds(5));

    let max_receipt_size = 4_194_304;

    // Call the contract
    let large_receipt_tx = SignedTransaction::call(
        102,
        account.clone(),
        account.clone(),
        &account_signer,
        Balance::ZERO,
        "max_receipt_size_value_return_method".into(),
        format!("{{\"value_size\": {}}}", max_receipt_size).into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(large_receipt_tx, Duration::seconds(5));

    // Make sure that the last promise in the DAG was called
    let assert_test_completed = SignedTransaction::call(
        103,
        account.clone(),
        account,
        &account_signer,
        Balance::ZERO,
        "assert_test_completed".into(),
        "".into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(assert_test_completed, Duration::seconds(5));

    assert_oversized_receipt_occurred(&env.validator());
}
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L422-429)
```rust
fn receipt_is_oversized(receipt: &Receipt, max_receipt_size: u64) -> bool {
    let receipt_size: u64 = borsh::object_length(receipt).unwrap().try_into().unwrap();
    if receipt_size > max_receipt_size {
        tracing::info!(%receipt_size, %max_receipt_size, "found receipt above max size");
        return true;
    }
    false
}
```

**File:** runtime/runtime/src/lib.rs (L2444-2455)
```rust
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                &receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(|e| {
                StorageError::StorageInconsistentState(format!(
                    "Delayed receipt {:?} in the state is invalid: {}",
                    receipt, e
                ))
            })?;
```

**File:** runtime/runtime/src/lib.rs (L2512-2518)
```rust
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(RuntimeError::ReceiptValidationError)?;
```

**File:** runtime/runtime/src/congestion_control.rs (L413-427)
```rust
        // There is a bug which allows to create receipts that are above the size limit. Receipts
        // above the size limit might not fit under the maximum outgoing size limit. Let's pretend
        // that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
        // See https://github.com/near/nearcore/issues/12606
        let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
        if size > max_receipt_size {
            tracing::debug!(
                target: "runtime",
                receipt_id=?receipt.receipt_id(),
                size,
                max_receipt_size,
                "try_forward observed a receipt with size exceeding the size limit",
            );
            size = max_receipt_size;
        }
```
