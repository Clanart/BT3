### Title
Fee Computation Felt-Overflow via Attacker-Controlled `max_price_per_unit` Enables Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked felt arithmetic over attacker-supplied `max_price_per_unit` values. Because `pack_resource_bounds` only enforces `assert_nn` (i.e., value ∈ [0, (PRIME−1)/2]) with no upper-bound cap, an unprivileged transaction sender can craft resource bounds whose product-sum wraps to exactly 0 modulo PRIME. `charge_fee` then short-circuits on `max_fee == 0` and charges nothing, allowing the transaction to execute for free inside a valid OS proof.

---

### Finding Description

`pack_resource_bounds` (called during transaction-hash computation) applies only a lower-bound check on `max_price_per_unit`:

```cairo
// transaction_hash/transaction_hash.cairo  L103-L108
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);   // ← only ≥ 0, no upper bound
    ...
}
```

This means `max_price_per_unit` is constrained to `[0, (PRIME−1)/2]` — values up to ≈ 2^250.

Later, `compute_max_possible_fee` reuses those same field elements with no re-validation:

```cairo
// transaction_impls.cairo  L87-L101
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

All arithmetic is modulo PRIME. The result can wrap to 0.

`charge_fee` then short-circuits on that zero result:

```cairo
// transaction_impls.cairo  L121-L125
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();   // ← no fee charged, no actual-fee assertion executed
}
```

The OS proof is valid with zero fee charged.

---

### Impact Explanation

An attacker who can submit a crafted V3 invoke transaction (an unprivileged role) causes the OS to produce a valid STARK proof in which the fee transfer is entirely skipped. Because the proof is valid, the L1 verifier accepts it. The sequencer receives zero compensation for executing the transaction. With free execution, the attacker can flood the network with arbitrarily many transactions at zero cost, exhausting sequencer resources and preventing legitimate transactions from being confirmed — a total network shutdown. Additionally, free execution of arbitrary contract calls can be chained with other contract-level logic to drain funds held in contracts the attacker interacts with.

**Matched impact**: High — Network not being able to confirm new transactions (total network shutdown).

---

### Likelihood Explanation

The exploit requires only arithmetic knowledge of PRIME and the ability to submit a signed V3 transaction. No privileged access, leaked keys, or third-party compromise is needed. The crafted values pass all existing OS range checks. The computation to find a valid overflow tuple is trivial (see PoC below). Likelihood is **High**.

---

### Recommendation

Add an explicit upper-bound range check on `max_price_per_unit` inside `pack_resource_bounds`, matching the 128-bit packing width implied by the encoding:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This ensures each term in `compute_max_possible_fee` is at most `(2^64 − 1) × (2^128 − 1) < 2^192 ≪ PRIME`, making three-term overflow impossible. Alternatively, add the same bound check at the start of `compute_max_possible_fee` before performing the arithmetic.

---

### Proof of Concept

**Goal**: find `(A, B, C, D, F, G)` satisfying all OS range checks such that `A*B + C*(D+tip) + F*G ≡ 0 (mod PRIME)`.

Let `P = PRIME = 2^251 + 17·2^192 + 1`.

Choose:
- `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = (P−1)/2`
- `l2_gas_bounds.max_amount = 1`, `l2_gas_bounds.max_price_per_unit = (P−1)/2`, `tip = 0`
- `l1_data_gas_bounds.max_amount = 1`, `l1_data_gas_bounds.max_price_per_unit = 1`

**Range-check verification**:
- `max_amount = 1 ≤ 2^64 − 1` ✓ (`assert_nn_le`)
- `max_price_per_unit = (P−1)/2 ≤ (P−1)/2` ✓ (`assert_nn`)
- `tip = 0 ≤ 2^64 − 1` ✓ (`assert_nn_le`)

**Fee computation**:
```
1 · (P−1)/2  +  1 · ((P−1)/2 + 0)  +  1 · 1
= (P−1)/2 + (P−1)/2 + 1
= P − 1 + 1
= P
≡ 0  (mod P)
```

`compute_max_possible_fee` returns `0`. `charge_fee` hits the `if (max_fee == 0) { return (); }` branch. No fee is deducted. The OS proof is valid. The transaction executes for free. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-101)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L119-125)
```text

    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L110-144)
```text
func hash_fee_fields{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    tip: felt, resource_bounds: ResourceBounds*, n_resource_bounds: felt
) -> felt {
    alloc_locals;

    let (local data_to_hash: felt*) = alloc();
    assert data_to_hash[0] = tip;
    assert_nn_le(tip, 2 ** 64 - 1);

    static_assert L1_GAS_INDEX == 0;
    static_assert L2_GAS_INDEX == 1;
    static_assert L1_DATA_GAS_INDEX == 2;

    with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
        assert n_resource_bounds = 3;
    }

    // L1 gas.
    let l1_gas_bounds = resource_bounds[L1_GAS_INDEX];
    assert l1_gas_bounds.resource = L1_GAS;
    assert data_to_hash[1] = pack_resource_bounds(l1_gas_bounds);

    // L2 gas.
    let l2_gas_bounds = resource_bounds[L2_GAS_INDEX];
    assert l2_gas_bounds.resource = L2_GAS;
    assert data_to_hash[2] = pack_resource_bounds(l2_gas_bounds);

    // L1 data gas.
    let l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];
    assert l1_data_gas_bounds.resource = L1_DATA_GAS;
    assert data_to_hash[3] = pack_resource_bounds(l1_data_gas_bounds);

    let (hash) = poseidon_hash_many(n=n_resource_bounds + 1, elements=data_to_hash);
    return hash;
}
```
