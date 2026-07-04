### Title
Unconstrained Block Hash Written to Mapping Allows Malicious Prover to Inject Arbitrary Historical Block Hashes - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo`)

---

### Summary

The StarkNet OS writes a historical block hash to the block hash mapping contract (address `0x1`) using a value that is entirely hint-provided and carries **zero Cairo proof constraints**. Any contract calling the `get_block_hash()` syscall receives this unconstrained value. A malicious prover can inject an arbitrary felt as the block hash for any historical block and produce a valid proof, breaking the soundness guarantee of the OS for all contracts that rely on `get_block_hash()`.

---

### Finding Description

In `write_block_number_to_block_hash_mapping` (`os_utils.cairo`), the OS writes the hash of block `current_block_number - STORED_BLOCK_HASH_BUFFER` (i.e., 10 blocks ago) into the dedicated block hash contract at address `BLOCK_HASH_CONTRACT_ADDRESS = 0x1`. The value written is:

```cairo
local old_block_hash;
%{ GetOldBlockNumberAndHash %}

assert [storage_ptr] = DictAccess(key=old_block_number, prev_value=0, new_value=old_block_hash);
```

`old_block_hash` is declared as a `local` variable and populated exclusively by the hint `GetOldBlockNumberAndHash`. There is **no Cairo assertion** that constrains `old_block_hash` to equal the actual Poseidon hash of the referenced block. The code itself acknowledges this:

> `// Currently, the block hash mapping is not enforced by the OS.`
> `// TODO(Yoni, 1/1/2026): output this hash.`

When a contract later calls `get_block_hash(block_number)`, `execute_get_block_hash` in `syscall_impls.cairo` reads `block_hash` from `response.block_hash` (also hint-provided) and calls `read_block_hash_from_storage`:

```cairo
let block_hash = response.block_hash;
read_block_hash_from_storage(block_number=request_block_number, expected_block_hash=block_hash);
```

`read_block_hash_from_storage` asserts a dict read:

```cairo
assert [storage_ptr] = DictAccess(
    key=block_number, prev_value=expected_block_hash, new_value=expected_block_hash
);
```

The Cairo dict squashing mechanism enforces that the `new_value` written in `write_block_number_to_block_hash_mapping` matches the `prev_value` read in `read_block_hash_from_storage`. This means `response.block_hash` must equal `old_block_hash`. However, **both values are hint-provided**. A malicious prover sets both to the same arbitrary felt `X`, the dict squashing is satisfied, and the proof verifies — yet `X` is not the actual block hash.

The `OsOutputHeader` struct comment in `output.cairo` further confirms:

> `// Currently, the block hash is not enforced by the OS.`

And `get_block_hashes` in `block_hash.cairo` uses only a hint for consistency checking (`%{ CheckBlockHashConsistency %}`), which is not a proof constraint.

---

### Impact Explanation

**Direct loss of funds (Critical).**

Any on-chain contract that uses `get_block_hash()` as a source of unpredictability or as a commitment anchor — e.g., lottery contracts, commit-reveal schemes, VRF-based protocols, or cross-chain bridges that verify StarkNet block hashes — receives a prover-controlled value. A malicious prover can:

1. Pre-select a target block hash value that causes a victim contract to release funds (e.g., a lottery that pays out when `block_hash % N == 0`).
2. Set `old_block_hash` to that value in the hint.
3. Produce a valid OS proof with the manipulated block hash mapping.
4. Submit the proof to L1; the L1 verifier accepts it because the proof is arithmetically valid.
5. The victim contract's `get_block_hash()` call returns the attacker-chosen value, triggering the payout.

---

### Likelihood Explanation

The StarkNet security model is explicitly designed so that even a malicious sequencer/prover cannot steal funds — the OS proof is the trust anchor. This is not a "trusted operator" assumption; it is the core soundness property of the ZK rollup. The missing constraint is a known TODO (`1/1/2026`) that has not yet been implemented, meaning the vulnerability is present in the current production OS program. Any entity that can submit a valid OS proof (i.e., the sequencer) can exploit this.

---

### Recommendation

Enforce the block hash value within the Cairo proof by including `old_block_hash` in the OS output and verifying it against the committed output of the previous OS execution on L1. Concretely:

1. Output `(old_block_number, old_block_hash)` as part of the OS output header so the L1 contract can cross-check it against the previously committed `new_block_hash` for that block number.
2. Remove the `// Currently, the block hash mapping is not enforced by the OS.` bypass and replace the hint-only population of `old_block_hash` with a Cairo-constrained derivation tied to the OS output chain.

---

### Proof of Concept

**Step 1 — Write phase (per-block pre-processing):**

`write_block_number_to_block_hash_mapping` is called unconditionally for every block with `block_number >= STORED_BLOCK_HASH_BUFFER`. [1](#0-0) 

`old_block_hash` is a `local` felt filled only by the hint `GetOldBlockNumberAndHash`. No `assert` constrains it to any computed value. [2](#0-1) 

**Step 2 — Read phase (syscall):**

When a contract calls `get_block_hash`, `execute_get_block_hash` reads `block_hash` from the hint-provided `response.block_hash` and passes it to `read_block_hash_from_storage` as `expected_block_hash`. [3](#0-2) 

`read_block_hash_from_storage` asserts a dict read with `prev_value=expected_block_hash`. Dict squashing enforces write/read consistency, but since both the write value and the read value are hint-provided, a malicious prover sets both to the same arbitrary `X`. [4](#0-3) 

**Step 3 — Block hash computation is also unconstrained:**

`get_block_hashes` in `block_hash.cairo` populates `previous_block_hash`, `header_commitments`, `gas_prices_hash`, and `starknet_version` all via a single hint `GetBlockHashes`, with only a hint-based consistency check (`CheckBlockHashConsistency`) — not a Cairo assertion. [5](#0-4) 

The `OsOutputHeader` comment confirms the OS does not enforce the block hash: [6](#0-5) 

**Result:** A malicious prover submits a proof where `old_block_hash = ATTACKER_VALUE`. The proof is valid. All contracts calling `get_block_hash(old_block_number)` receive `ATTACKER_VALUE`, enabling fund theft from any contract that uses historical block hashes for security-critical logic.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L64-73)
```text
    // Currently, the block hash mapping is not enforced by the OS.
    // TODO(Yoni, 1/1/2026): output this hash.
    local old_block_hash;
    %{ GetOldBlockNumberAndHash %}

    // Update mapping.
    assert state_entry.class_hash = 0;
    assert state_entry.nonce = 0;
    tempvar storage_ptr = state_entry.storage_ptr;
    assert [storage_ptr] = DictAccess(key=old_block_number, prev_value=0, new_value=old_block_hash);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L690-717)
```text
func read_block_hash_from_storage{contract_state_changes: DictAccess*}(
    block_number: felt, expected_block_hash: felt
) {
    // Fetch the block hash contract state.
    tempvar state_entry: StateEntry*;
    // Fetch a state_entry in this hint. Validate it in the update that comes next.
    %{ GetBlockHashMapping %}

    // Read from storage.
    tempvar storage_ptr = state_entry.storage_ptr;
    assert [storage_ptr] = DictAccess(
        key=block_number, prev_value=expected_block_hash, new_value=expected_block_hash
    );
    let storage_ptr = storage_ptr + DictAccess.SIZE;

    // Update the state.
    dict_update{dict_ptr=contract_state_changes}(
        key=BLOCK_HASH_CONTRACT_ADDRESS,
        prev_value=cast(state_entry, felt),
        new_value=cast(
            new StateEntry(
                class_hash=state_entry.class_hash, storage_ptr=storage_ptr, nonce=state_entry.nonce
            ),
            felt,
        ),
    );

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L760-766)
```text
    let response = cast(syscall_ptr, GetBlockHashResponse*);
    // Advance syscall pointer to the next syscall.
    let syscall_ptr = syscall_ptr + GetBlockHashResponse.SIZE;

    let block_hash = response.block_hash;

    read_block_hash_from_storage(block_number=request_block_number, expected_block_hash=block_hash);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L58-81)
```text
    alloc_locals;
    local previous_block_hash;
    // Currently, the header commitments and gas prices are not computed by the OS.
    // TODO(Yoni, 1/1/2027): compute the header commitments and gas prices.
    local header_commitments: BlockHeaderCommitments*;
    local gas_prices_hash;
    // TODO(Yoni): move to global context, and consider enforcing a specific version for the
    // non-virtual OS.
    local starknet_version;

    %{ GetBlockHashes %}

    let block_hash = calculate_block_hash(
        block_info=block_info,
        header_commitments=header_commitments,
        gas_prices_hash=gas_prices_hash,
        state_root=state_root,
        previous_block_hash=previous_block_hash,
        starknet_version=starknet_version,
    );

    %{ CheckBlockHashConsistency %}

    return (previous_block_hash=previous_block_hash, new_block_hash=block_hash);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L38-40)
```text
    prev_block_hash: felt,
    // Currently, the block hash is not enforced by the OS.
    new_block_hash: felt,
```
