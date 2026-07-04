### Title
`STORAGE_READ_GAS_COST` and `STORAGE_WRITE_GAS_COST` Fail to Account for Patricia Tree Updates and Dict Squash, Enabling Underpriced Computational Resource Consumption — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo`)

---

### Summary

The gas constants `STORAGE_READ_GAS_COST = 18070` and `STORAGE_WRITE_GAS_COST = 44970` are explicitly acknowledged by a developer TODO comment to **not** include the cost of Patricia Merkle tree updates and dict squash operations. An unprivileged transaction sender can submit invoke transactions that perform many `storage_read`/`storage_write` syscalls to unique storage slots, paying only the flat per-syscall L2 gas cost while causing the sequencer/prover to perform significantly more uncompensated block-level computational work — directly analogous to the minievm intrinsic gas omission.

---

### Finding Description

In `constants.cairo`, the storage syscall gas costs are defined with an explicit developer acknowledgment of the gap:

```cairo
// TODO(Yoni, 1/1/2026): take into account Patricia updates and dict squash.
const STORAGE_READ_GAS_COST = 18070;
const STORAGE_WRITE_GAS_COST = 44970;
``` [1](#0-0) 

These constants are used in `reduce_syscall_gas_and_write_response_header` / `reduce_syscall_base_gas` to deduct L2 gas from the user's budget for each storage syscall. The deduction is a flat per-call cost that does **not** include:

1. **Patricia Merkle tree traversal/update**: Each unique storage slot accessed requires an O(log n) traversal of StarkNet's 251-level Patricia tree during state commitment. Each level involves a Pedersen hash (`PEDERSEN_GAS_COST = 4050`). For a unique slot, this is up to ~251 × 4050 ≈ 1,017,050 gas worth of work — roughly **56× the charged `STORAGE_READ_GAS_COST`** — that is borne entirely by the sequencer/prover.

2. **Dict squash**: At the end of block processing, all storage accesses across all transactions are squashed (deduplicated and sorted). This is an O(n log n) block-level operation for n unique slots. Its cost is not attributed to any transaction's gas budget.

The `get_initial_user_gas_bound` function derives the user's gas budget solely from the `L2_GAS_INDEX` resource bound:

```cairo
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
``` [2](#0-1) 

The initial gas is set directly from this bound and decremented by `STORAGE_READ_GAS_COST` per read:

```cairo
let initial_user_gas_bound = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
let remaining_gas = initial_user_gas_bound;
``` [3](#0-2) 

There is no mechanism to charge the Patricia/dict squash overhead back to the transaction sender. The sequencer/prover absorbs this cost unconditionally.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

An attacker who fills blocks with transactions that each perform the maximum number of `storage_read` syscalls to distinct storage slots forces the sequencer/prover to perform Patricia tree traversals and dict squash work that is ~56× (or more) the L2 gas charged per read. Because the block-level dict squash is O(n log n) in the number of unique slots, the uncompensated work grows super-linearly as the attacker maximizes unique slot accesses within the L2 gas limit. This can overwhelm the prover's capacity to generate proofs within the block time, halting new block production.

---

### Likelihood Explanation

**High.** Any unprivileged transaction sender can trigger this by:
- Deploying a contract that reads from many distinct storage slots (e.g., iterating over a large range of keys)
- Submitting invoke transactions calling this function, paying only the flat `STORAGE_READ_GAS_COST` per read
- No privileged access, leaked keys, or external dependency compromise is required

The attack is cheap relative to the damage: the attacker pays L2 gas at the rate of 18,070 per read while causing ~56× more uncompensated prover work per unique slot.

---

### Recommendation

Resolve the acknowledged TODO by updating `STORAGE_READ_GAS_COST` and `STORAGE_WRITE_GAS_COST` to include an amortized per-access cost for Patricia tree traversal and dict squash. This can be modeled as a per-unique-slot surcharge proportional to the tree depth (251 levels × `PEDERSEN_GAS_COST`) plus an amortized dict squash cost, similar to how EIP-2930 charges upfront for cold storage access in Ethereum.

---

### Proof of Concept

1. Attacker deploys a Cairo 1 contract with an `__execute__` function that calls `storage_read` on N distinct storage keys (e.g., keys 0 through N−1).
2. Attacker submits an invoke transaction with `L2_GAS` resource bound set to `N × STORAGE_READ_GAS_COST` (18,070 × N).
3. The OS charges exactly `N × 18,070` L2 gas from the user's budget via `reduce_syscall_base_gas`.
4. At block commit, the OS performs Patricia tree updates for each of the N unique slots (each requiring up to 251 Pedersen hashes ≈ 1,017,050 gas of work per slot) and a dict squash over all N entries — none of which is charged to the attacker.
5. With N = 10,000 unique reads (costing the attacker ~180,700,000 L2 gas, within `EXECUTE_MAX_SIERRA_GAS = 1,100,000,000`), the prover must perform ~10,170,500,000 gas-equivalent units of Patricia work — a **~56× amplification** — without compensation.
6. Repeated across multiple transactions per block, this halts block production. [1](#0-0) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L89-92)
```text
const DEFAULT_INITIAL_GAS_COST = 10000000000;
const VALIDATE_MAX_SIERRA_GAS = 100000000;
const EXECUTE_MAX_SIERRA_GAS = 1100000000;
const DEFAULT_INITIAL_GAS_COST_NO_L2 = VALIDATE_MAX_SIERRA_GAS + EXECUTE_MAX_SIERRA_GAS;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L112-114)
```text
// TODO(Yoni, 1/1/2026): take into account Patricia updates and dict squash.
const STORAGE_READ_GAS_COST = 18070;
const STORAGE_WRITE_GAS_COST = 44970;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L322-323)
```text
    let initial_user_gas_bound = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
    let remaining_gas = initial_user_gas_bound;
```
