### Title
`is_deployed` Missing Pending-State Check in `convert_thin_state_diff` Causes Wrong `replaced_classes`/`deployed_contracts` Classification in RPC Simulation and Tracing — (`File: crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

`convert_thin_state_diff` re-derives the `replaced_classes` / `deployed_contracts` split for an induced state diff by calling `is_deployed`. `is_deployed` always resolves the contract's prior existence against **committed storage only** (using `BlockId::HashOrNumber`). When the caller is `simulate_transactions`, `trace_transaction`, or `trace_block_transactions` operating on a **pending block**, a contract that was deployed in the pending block (not yet committed) is invisible to `is_deployed`. As a result, a class-replacement of a pending-deployed contract is misclassified as a fresh deployment in the returned `state_diff`, producing an authoritative-looking wrong RPC value.

---

### Finding Description

`convert_thin_state_diff` iterates over `thin_state_diff.deployed_contracts` and calls `is_deployed` to decide whether each entry is a new deployment or a class replacement:

```rust
// crates/apollo_rpc/src/v0_8/api/api_impl.rs  L1693-L1709
let prev_block_number = match block_id {
    BlockId::Tag(Tag::Pending) => Some(block_number),   // latest accepted block
    _ => block_number.prev(),
};
for (&address, &class_hash) in thin_state_diff.deployed_contracts.iter() {
    if let Some(prev_block_number) = prev_block_number {
        if self.is_deployed(prev_block_number, address).await? {
            replaced_classes.push((address, class_hash));
        }
    }
}
``` [1](#0-0) 

`is_deployed` unconditionally constructs a **non-pending** `BlockId`:

```rust
// L1677-L1684
async fn is_deployed(&self, block_number: BlockNumber, contract_address: ContractAddress)
    -> RpcResult<bool>
{
    let block_id = BlockId::HashOrNumber(BlockHashOrNumber::Number(block_number));
    Ok(self.maybe_get_class_hash_at(block_id, contract_address).await?.is_some())
}
``` [2](#0-1) 

`maybe_get_class_hash_at` only attaches pending deployed/replaced data when `block_id` is `Tag::Pending`:

```rust
// L1656-L1663
let maybe_pending_deployed_contracts_and_replaced_classes =
    if let BlockId::Tag(Tag::Pending) = block_id {
        // ... reads pending state diff
    } else {
        None   // <-- always None when called from is_deployed
    };
``` [3](#0-2) 

`convert_thin_state_diff` is invoked with `block_id = BlockId::Tag(Tag::Pending)` from three RPC handlers:

- `simulate_transactions` (line 1127–1133)
- `trace_transaction` (line 1290–1292)
- `trace_block_transactions` (line 1414–1420) [4](#0-3) [5](#0-4) [6](#0-5) 

For all three, the execution engine is given the full pending state (including pending-deployed contracts) via `client_pending_data_to_execution_pending_data`. The `ExecutionStateReader` correctly resolves `get_class_hash_at` against pending deployed/replaced contracts:

```rust
// crates/apollo_rpc_execution/src/state_reader.rs  L74-L85
fn get_class_hash_at(&self, contract_address: ContractAddress) -> StateResult<ClassHash> {
    Ok(execution_utils::get_class_hash_at(
        ...,
        self.maybe_pending_data.as_ref().map(|pending_data| {
            (&pending_data.deployed_contracts, &pending_data.replaced_classes)
        }),
        contract_address,
    )...)
}
``` [7](#0-6) 

So execution succeeds and the `induced_state_diff` correctly records the class-hash change in `deployed_contracts`. But when `convert_thin_state_diff` post-processes that diff, `is_deployed` queries only committed storage and returns `false` for any contract that exists only in the pending block. The entry is left in `deployed_contracts` instead of being moved to `replaced_classes`.

By contrast, `get_state_update` for a pending block bypasses `convert_thin_state_diff` entirely and returns the raw `ClientStateDiff` from the feeder, which already carries the correct `replaced_classes` field:

```rust
// L487-L492
if let BlockId::Tag(Tag::Pending) = block_id {
    let state_update = read_pending_data(...).await?.state_update;
    return Ok(StateUpdate::PendingStateUpdate(PendingStateUpdate {
        old_root: state_update.old_root,
        state_diff: state_update.state_diff.into(),   // already split correctly
    }));
}
``` [8](#0-7) 

This is the direct analog of the external report: `get_state_update` (like `claim()`) handles the special case correctly; `simulate_transactions` / `trace_transaction` / `trace_block_transactions` (like `claim_many()`) are missing the equivalent check.

---

### Impact Explanation

`simulate_transactions`, `trace_transaction`, and `trace_block_transactions` return a `state_diff` that misclassifies a class replacement of a pending-deployed contract as a fresh deployment. The `replaced_classes` list is empty when it should contain the entry, and `deployed_contracts` contains an entry that should not be there. This is an authoritative-looking wrong value from three RPC endpoints, matching the **High** impact: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

---

### Likelihood Explanation

The trigger requires:
1. A pending block that contains a transaction deploying contract `A` with class hash `X`.
2. A caller invoking `simulate_transactions` (or `trace_transaction` / `trace_block_transactions`) against `BlockId::Tag(Tag::Pending)` with a transaction that replaces the class of `A` to `Y`.

Both conditions are routine during contract-upgrade testing and automated tooling. Any unprivileged user can reach this path via the public JSON-RPC API.

---

### Recommendation

`is_deployed` must also consult the pending state when the caller is operating on a pending block. The simplest fix is to pass the pending deployed/replaced contracts into `is_deployed` (or inline the check) so that it mirrors the logic already present in `maybe_get_class_hash_at`:

```rust
async fn is_deployed_at(
    &self,
    block_number: BlockNumber,
    contract_address: ContractAddress,
    pending_deployed: Option<&(Vec<DeployedContract>, Vec<ReplacedClass>)>,
) -> RpcResult<bool> {
    // First check pending state if provided
    if let Some((deployed, replaced)) = pending_deployed {
        if replaced.iter().any(|r| r.address == contract_address)
            || deployed.iter().any(|d| d.address == contract_address)
        {
            return Ok(true);
        }
    }
    let block_id = BlockId::HashOrNumber(BlockHashOrNumber::Number(block_number));
    Ok(self.maybe_get_class_hash_at(block_id, contract_address).await?.is_some())
}
```

Alternatively, refactor `convert_thin_state_diff` to accept an optional `PendingData` reference and pass it through to the deployment check, consistent with how `ExecutionStateReader::get_class_hash_at` already handles the pending overlay.

---

### Proof of Concept

1. Pending block contains Tx₁ that deploys contract `0xABC` with class hash `0x111`.
2. Caller invokes `starknet_simulateTransactions` with `block_id = "pending"` and a transaction that calls `replace_class(0x222)` on contract `0xABC`.
3. Execution succeeds: `ExecutionStateReader::get_class_hash_at(0xABC)` finds `0x111` in the pending deployed contracts.
4. `induced_state_diff.deployed_contracts = {0xABC: 0x222}`.
5. `convert_thin_state_diff` calls `is_deployed(latest_block_number, 0xABC)` → queries committed storage → `None` → returns `false`.
6. `0xABC` remains in `deployed_contracts`; `replaced_classes` is empty.
7. RPC response shows `deployed_contracts: [{address: 0xABC, class_hash: 0x222}]` and `replaced_classes: []` — the opposite of the correct answer.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L487-492)
```rust
        if let BlockId::Tag(Tag::Pending) = block_id {
            let state_update = read_pending_data(&self.pending_data, &txn).await?.state_update;
            return Ok(StateUpdate::PendingStateUpdate(PendingStateUpdate {
                old_root: state_update.old_root,
                state_diff: state_update.state_diff.into(),
            }));
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1127-1133)
```rust
            let state_diff = self
                .convert_thin_state_diff(
                    simulation_output.induced_state_diff,
                    block_id,
                    block_number,
                )
                .await?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1285-1293)
```rust
        let block_id = if is_pending {
            BlockId::Tag(Tag::Pending)
        } else {
            BlockId::HashOrNumber(BlockHashOrNumber::Number(block_number))
        };
        let state_diff = self
            .convert_thin_state_diff(simulation_result.induced_state_diff, block_id, block_number)
            .await?;
        Ok((simulation_result.transaction_trace, state_diff).into())
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1414-1420)
```rust
            let state_diff = self
                .convert_thin_state_diff(
                    simulation_output.induced_state_diff,
                    block_id,
                    block_number,
                )
                .await?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1656-1663)
```rust
        let maybe_pending_deployed_contracts_and_replaced_classes =
            if let BlockId::Tag(Tag::Pending) = block_id {
                let pending_state_diff =
                    read_pending_data(&self.pending_data, &txn).await?.state_update.state_diff;
                Some((pending_state_diff.deployed_contracts, pending_state_diff.replaced_classes))
            } else {
                None
            };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1677-1684)
```rust
    async fn is_deployed(
        &self,
        block_number: BlockNumber,
        contract_address: ContractAddress,
    ) -> RpcResult<bool> {
        let block_id = BlockId::HashOrNumber(BlockHashOrNumber::Number(block_number));
        Ok(self.maybe_get_class_hash_at(block_id, contract_address).await?.is_some())
    }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1693-1709)
```rust
        let prev_block_number = match block_id {
            BlockId::Tag(Tag::Pending) => Some(block_number),
            _ => block_number.prev(),
        };
        let mut replaced_classes = vec![];
        for (&address, &class_hash) in thin_state_diff.deployed_contracts.iter() {
            // Check if the class was replaced.
            if let Some(prev_block_number) = prev_block_number {
                if self.is_deployed(prev_block_number, address).await? {
                    replaced_classes.push((address, class_hash));
                }
            }
        }
        replaced_classes.iter().for_each(|(address, _)| {
            thin_state_diff.deployed_contracts.swap_remove(address);
        });
        Ok(ThinStateDiff::from(thin_state_diff, replaced_classes))
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L74-85)
```rust
    fn get_class_hash_at(&self, contract_address: ContractAddress) -> StateResult<ClassHash> {
        Ok(execution_utils::get_class_hash_at(
            &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
            self.state_number,
            self.maybe_pending_data.as_ref().map(|pending_data| {
                (&pending_data.deployed_contracts, &pending_data.replaced_classes)
            }),
            contract_address,
        )
        .map_err(storage_err_to_state_err)?
        .unwrap_or_default())
    }
```
