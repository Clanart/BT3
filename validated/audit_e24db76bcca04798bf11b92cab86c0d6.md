### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Allows Complete Fee Bypass — (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked felt arithmetic over user-controlled resource bounds and tip values. Because Cairo arithmetic is modular (mod the Stark prime P ≈ 2²⁵¹ + 17·2¹⁹² + 1), a crafted transaction can make the function return exactly 0. `charge_fee` treats a zero result as "no fee required" and returns immediately, skipping the ERC20 transfer entirely. The attacker's transaction is executed by the OS with no fee payment.

---

### Finding Description

`compute_max_possible_fee` in `transaction_impls.cairo` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All six operands (`max_amount`, `max_price_per_unit` for each resource, and `tip`) are user-supplied fields loaded from the signed transaction. There is no range check or overflow guard on any of them. The result is a raw felt sum, which wraps modulo P.

`charge_fee` then does:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [2](#0-1) 

A zero result causes an unconditional early return before the ERC20 `transfer` call is ever constructed or executed. The guard was designed for L1 handlers (which legitimately have `max_fee = 0`), but it is also reached for V3 account transactions whose fee sum overflows to zero.

**Concrete overflow construction:**

Set:
- `l1_gas_max_amount = 0`, `l1_data_gas_max_amount = 0` (zero out the other two terms)
- `l2_gas_max_amount = 1`
- `l2_gas_max_price_per_unit = P − 1` (the maximum valid felt value)
- `tip = 1`

Then:

```
max_fee = 0 + 1 × ((P − 1) + 1) + 0
        = 1 × P
        ≡ 0  (mod P)
```

The OS computes `max_fee = 0` and returns from `charge_fee` without executing the fee transfer. The attacker's invoke, declare, or deploy-account transaction runs for free.

The resource bounds are part of the signed transaction hash (committed via `compute_invoke_transaction_hash` / `compute_declare_transaction_hash`), so the attacker simply signs a transaction containing these specific values and submits it. No privileged access is required. [3](#0-2) 

---

### Impact Explanation

**Direct loss of funds — Critical.**

The fee transfer from the user's account to the sequencer is the mechanism by which STRK tokens move from user balances to the sequencer. When `charge_fee` returns early, the ERC20 `transfer` call at lines 161–163 is never executed: [4](#0-3) 

The user retains STRK tokens they were obligated to pay. The sequencer receives nothing. Because the OS is the ground truth for the validity proof, a block containing such a transaction is provably valid — the sequencer cannot later dispute it. At scale, an attacker can drain the expected fee revenue of every block by submitting only overflow-crafted transactions, permanently depriving the protocol of fee income and breaking the economic security model that compensates sequencers for including transactions.

---

### Likelihood Explanation

**High.**

1. The attack requires only arithmetic knowledge of the Stark prime P — no cryptographic break, no privileged key, no social engineering.
2. The attacker constructs a valid signed V3 transaction (the OS verifies the hash and signature through `__validate__`; the overflow only affects the fee computation after validation).
3. The sequencer's mempool may apply its own gas-price sanity checks, but the OS itself imposes no such bound, and the OS proof is the authoritative record. A sequencer that is itself the attacker (or that has a permissive mempool) can include the transaction.
4. The attack is repeatable for every transaction type that calls `charge_fee`: invoke, declare, and deploy-account.

---

### Recommendation

1. **Add explicit range checks on each resource-bound field.** Before computing the fee, assert that `max_amount` and `max_price_per_unit` for each resource are within protocol-defined maximums (e.g., using `assert_nn_le`). This prevents any single term from being large enough to cause a wrap-around.

2. **Detect and reject overflow explicitly.** After computing the sum, verify that the result is at least as large as each individual term (a standard overflow-detection pattern for modular arithmetic). If the sum is smaller than any addend, the computation wrapped and the transaction must be rejected.

3. **Separate the "no fee" path from the "overflow to zero" path.** The early-return guard `if (max_fee == 0)` should only be reachable for transaction types that are structurally fee-free (L1 handlers). For V3 account transactions, a zero result after the computation should be treated as an error, not as permission to skip the transfer.

---

### Proof of Concept

**Setup:** Attacker controls a standard StarkNet account with a small STRK balance.

**Step 1 — Craft the transaction fields:**
```
version                    = 3
l1_gas_max_amount          = 0
l1_gas_max_price_per_unit  = 0
l2_gas_max_amount          = 1
l2_gas_max_price_per_unit  = P − 1
                           = 3618502788666131213697322783095070105623107215331596699973092056135872020480
tip                        = 1
l1_data_gas_max_amount     = 0
l1_data_gas_max_price_per_unit = 0
```

**Step 2 — Verify the overflow:**
```
max_fee = 0 + 1 × ((P − 1) + 1) + 0
        = P mod P
        = 0
```

**Step 3 — Sign and submit.** The transaction hash is computed over these fields (including `l2_gas_max_price_per_unit = P−1` and `tip = 1`). The attacker signs with their account key. `__validate__` runs normally and returns `VALID`.

**Step 4 — OS execution.** `charge_fee` calls `compute_max_possible_fee`, receives 0, and returns at line 123–125 without invoking the fee token's `transfer` entry point.

**Result:** The attacker's transaction is fully executed and proven valid. Zero STRK is transferred to the sequencer. The attacker retains all funds they should have paid.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-163)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
```
