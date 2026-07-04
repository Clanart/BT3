### Title
Felt Arithmetic Underflow in `check_proof_facts` Halts OS Execution When Block Number < `STORED_BLOCK_HASH_BUFFER` — (File: `execution/execution_constraints.cairo`)

---

### Summary

`check_proof_facts` in `execution_constraints.cairo` performs the subtraction `current_block_number - STORED_BLOCK_HASH_BUFFER` without first verifying that `current_block_number >= STORED_BLOCK_HASH_BUFFER`. In Cairo, felt arithmetic is modular (mod P ≈ 2²⁵¹). When `current_block_number < 10`, the subtraction wraps around to a value near P, causing the subsequent `assert_nn_le` range-check to fail and crashing the entire OS execution. An unprivileged transaction sender can trigger this by submitting an invoke transaction with non-empty `proof_facts` during the first 10 blocks of any new StarkNet deployment.

---

### Finding Description

**Root cause — `execution_constraints.cairo`, lines 66–68:**

```cairo
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
```

`STORED_BLOCK_HASH_BUFFER = 10` (defined in `constants.cairo` line 65). When `current_block_number` is, say, `5`, the felt subtraction `5 - 10` does not produce `-5`; it produces `P - 5` (≈ 2²⁵¹ − 5), a value far above the range-check bound RC_BOUND = 2¹²⁸.

`assert_nn_le(a, b)` expands to:
1. `assert_nn(a)` — checks `a ∈ [0, RC_BOUND)`
2. `assert_nn(b - a)` — checks `b - a ∈ [0, RC_BOUND)`

With `b = P − 5`, step 2 evaluates `(P − 5) − a` in felt arithmetic. For any small valid block number `a`, this result is still ≈ P − 5, which is ≫ RC_BOUND. The range-check constraint fails, and the OS execution aborts.

**Contrast with the correct guard in `os_utils.cairo`, lines 52–58:**

```cairo
tempvar old_block_number = block_context.block_info_for_execute.block_number -
    STORED_BLOCK_HASH_BUFFER;
let is_old_block_number_non_negative = is_nn(old_block_number);
if (is_old_block_number_non_negative == FALSE) {
    // Not enough blocks in the system - nothing to write.
    return ();
}
```

`os_utils.cairo` performs the identical subtraction but guards it with `is_nn` before proceeding. `check_proof_facts` omits this guard entirely.

**Attacker-controlled entry path:**

`check_proof_facts` is called from `execute_invoke_function_transaction` (`transaction_impls.cairo`, lines 313–318):

```cairo
check_proof_facts(
    proof_facts_size=proof_facts_size,
    proof_facts=proof_facts,
    current_block_number=block_context.block_info_for_execute.block_number,
    virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
);
```

`proof_facts_size` is loaded from the hint `%{ TxProofFacts %}`, which is derived from the transaction's own data. Any unprivileged sender can craft an invoke transaction with `proof_facts_size > 0` (the field is part of the transaction hash, so it is committed to but not restricted to zero). The early-return guard at line 40 (`if (proof_facts_size == 0) { return (); }`) is bypassed whenever the attacker sets a non-zero value.

---

### Impact Explanation

When the OS execution aborts, the prover cannot generate a valid STARK proof for the block. The sequencer must re-sequence the block. If the sequencer's off-chain validation does not independently reject transactions with non-empty `proof_facts` at low block heights, it may repeatedly include the offending transaction, causing every proof attempt to fail. This constitutes a **network halt**: no new blocks can be confirmed until the offending transaction is identified and excluded.

**Matched allowed impact:** High — Network not being able to confirm new transactions (total network shutdown).

---

### Likelihood Explanation

The window is the first 10 blocks (`block_number ∈ {0, …, 9}`) of any new StarkNet deployment — including new L3 app-chains, testnets, or devnets. The `proof_facts` feature (client-side proving / meta-transactions) is a live protocol feature, so a transaction carrying non-empty `proof_facts` is syntactically valid and will pass standard mempool checks. The sequencer's Rust-side blockifier does not enforce the `block_number >= STORED_BLOCK_HASH_BUFFER` constraint for `proof_facts`; that check exists only inside the Cairo OS. Therefore, the sequencer can include the transaction without detecting the problem, and the prover will fail.

---

### Recommendation

Mirror the guard already present in `os_utils.cairo`. Before the subtraction in `check_proof_facts`, verify that `current_block_number >= STORED_BLOCK_HASH_BUFFER`:

```cairo
// Guard: proof facts require a stored block hash, which only exists after
// STORED_BLOCK_HASH_BUFFER blocks.
if (current_block_number < STORED_BLOCK_HASH_BUFFER) {
    // Treat as if proof_facts_size == 0; no valid base block can exist yet.
    with_attr error_message("Proof facts not supported before block {STORED_BLOCK_HASH_BUFFER}.") {
        assert proof_facts_size = 0;
    }
    return ();
}
assert_nn_le(
    os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
);
```

Alternatively, use `is_nn` on the subtraction result before passing it to `assert_nn_le`, exactly as `write_block_number_to_block_hash_mapping` does.

---

### Proof of Concept

1. Deploy a fresh StarkNet network (L3 or testnet); block number starts at 0.
2. In block 5 (any block where `block_number < 10`), submit a valid invoke transaction (version 3) with `proof_facts` set to any non-empty byte sequence (e.g., a minimal `ProofHeader` + `VirtualOsOutputHeader` struct).
3. The sequencer's blockifier executes the transaction successfully (the blockifier does not enforce the block-number guard for `proof_facts`).
4. The prover runs the Cairo OS. Execution reaches `check_proof_facts` with `proof_facts_size > 0` and `current_block_number = 5`.
5. The OS evaluates `5 - 10` in felt arithmetic → `P - 5`.
6. `assert_nn_le(base_block_number, P - 5)` triggers `assert_nn(P - 5 - base_block_number)`. The range-check constraint fails because `P - 5 - base_block_number ≫ 2¹²⁸`.
7. The OS execution aborts; no proof is generated; the block cannot be finalized → **network halt**.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L66-68)
```text
    assert_nn_le(
        os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L52-58)
```text
    tempvar old_block_number = block_context.block_info_for_execute.block_number -
        STORED_BLOCK_HASH_BUFFER;
    let is_old_block_number_non_negative = is_nn(old_block_number);
    if (is_old_block_number_non_negative == FALSE) {
        // Not enough blocks in the system - nothing to write.
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L313-318)
```text
    check_proof_facts(
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        current_block_number=block_context.block_info_for_execute.block_number,
        virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L65-65)
```text
const STORED_BLOCK_HASH_BUFFER = 10;
```
