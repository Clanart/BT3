### Title
Unchecked Field Arithmetic in `compute_max_possible_fee` Allows Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function performs arithmetic on user-controlled `ResourceBounds` felt values without enforcing that they are within the valid Starknet spec ranges (u64 for `max_amount`, u128 for `max_price_per_unit`). An unprivileged transaction sender can craft resource bounds whose products overflow the STARK field prime and sum to 0 mod P, causing `charge_fee` to skip fee charging entirely. The transaction still executes (validate + execute entry points run), but no ERC20 transfer is made to the sequencer.

---

### Finding Description

`compute_max_possible_fee` computes the following expression over Cairo's prime field (P ≈ 2²⁵¹ + 17·2¹⁹² + 1): [1](#0-0) 

```
l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
+ l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
+ l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit
```

All operands are felt values loaded from user-controlled `ResourceBounds` structs via the hint `%{ LoadCommonTxFields %}`: [2](#0-1) 

The OS applies **no range checks** on `max_amount` or `max_price_per_unit` anywhere in the Cairo code — only `signature_len` and `calldata_size` are range-checked. Because Cairo arithmetic is modular (mod P), an attacker can choose values such that the entire sum equals 0 mod P. A concrete example:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `1` |
| `l1_gas_bounds.max_price_per_unit` | `P − 1` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `0` |
| `l1_data_gas_bounds.max_price_per_unit` | `0` |

Result: `1·(P−1) + 1·(1+0) + 0·0 = P ≡ 0 (mod P)`.

When `compute_max_possible_fee` returns 0, `charge_fee` immediately returns without executing the ERC20 transfer: [3](#0-2) 

The `assert_nn_le(calldata.amount.low, max_fee)` guard is never reached. The transaction's validate and execute entry points run normally, but no fee is deducted.

The same function is also used in the `execute_declare_transaction` bootstrap path: [4](#0-3) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions.**

An attacker who can execute transactions at zero cost can submit an unbounded volume of computationally expensive transactions (e.g., heavy `__execute__` logic, deep call trees). Because the OS proof is valid (all Cairo constraints are satisfied), the L1 verifier accepts the state transition. The sequencer is forced to process these transactions without receiving fees, and the resulting resource exhaustion can prevent legitimate transactions from being confirmed, constituting a total network shutdown scenario.

---

### Likelihood Explanation

The attack requires only:
1. Computing felt values that satisfy `A·B + C·(D+E) + F·G ≡ 0 (mod P)` — trivial arithmetic.
2. Signing the transaction (the resource bounds are committed to in the transaction hash, so the user signs them intentionally).
3. Submitting the transaction through the normal RPC interface.

No privileged role, leaked key, malicious operator, or external dependency is required. Any unprivileged transaction sender can execute this attack.

---

### Recommendation

Add explicit range checks on `ResourceBounds` fields before they are used in arithmetic. In `get_account_tx_common_fields` or at the start of `compute_max_possible_fee`, enforce:

- `max_amount < 2^64` (u64 bound) using `assert_nn_le`
- `max_price_per_unit < 2^128` (u128 bound) using a Uint256-style decomposition check

This mirrors the existing pattern used for `signature_len` and `calldata_size`: [5](#0-4) 

Applying the same discipline to resource bounds fields closes the overflow path.

---

### Proof of Concept

1. Attacker constructs a version-3 invoke transaction with:
   - `l1_gas_bounds = {max_amount: 1, max_price_per_unit: P−1}`
   - `l2_gas_bounds = {max_amount: 1, max_price_per_unit: 1}`, `tip = 0`
   - `l1_data_gas_bounds = {max_amount: 0, max_price_per_unit: 0}`
2. Attacker signs the transaction hash (which commits to these resource bounds via `compute_invoke_transaction_hash`).
3. Transaction is submitted and included in a block by the sequencer.
4. The OS executes `compute_max_possible_fee`:
   - `1·(P−1) + 1·(1+0) + 0·0 = P ≡ 0 (mod P)`
5. `charge_fee` sees `max_fee == 0` and returns immediately — no ERC20 transfer occurs.
6. The validate and execute entry points run normally.
7. A valid STARK proof is generated; the L1 verifier accepts the state transition.
8. The attacker's transaction is finalized on-chain with zero fee paid.

**Analog to the GVault report:** Just as GVault's `_totalAssets()` uses `balanceOf` (an externally manipulable value) instead of a tracked internal state variable — allowing an attacker to force `freeFunds` to a value that makes `convertToShares` return 0 — the StarkNet OS's `compute_max_possible_fee` uses raw felt arithmetic on user-controlled `ResourceBounds` without range validation, allowing an attacker to force the fee computation to return 0 and bypass fee charging entirely.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-102)
```text
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    tempvar resource_bounds: ResourceBounds* = tx_info.resource_bounds_start;
    let n_resource_bounds = (tx_info.resource_bounds_end - resource_bounds) / ResourceBounds.SIZE;

    // Only V3 transactions with all resource bounds are supported.
    assert tx_info.version = 3;
    assert n_resource_bounds = 3;

    tempvar l1_gas_bounds: ResourceBounds = resource_bounds[L1_GAS_INDEX];
    tempvar l2_gas_bounds: ResourceBounds = resource_bounds[L2_GAS_INDEX];
    tempvar l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];

    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L120-125)
```text
    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L174-182)
```text
    local resource_bounds: ResourceBounds*;
    local tip;
    local paymaster_data_length;
    local paymaster_data: felt*;
    local nonce_data_availability_mode;
    local fee_data_availability_mode;
    local nonce;
    %{ LoadCommonTxFields %}
    %{ LoadTxNonceAccount %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L218-218)
```text
    assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L764-776)
```text
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
    }
```
