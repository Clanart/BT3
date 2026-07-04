### Title
Unverified Block Hash Written to Block Hash Mapping Without OS Enforcement — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo`)

---

### Summary

The StarkNet OS writes an old block hash to the block hash mapping contract (address `0x1`) using a value supplied entirely by an unverified hint, with no cryptographic enforcement by the OS proof. The OS itself acknowledges this gap with an explicit TODO comment. Because the proof does not constrain the written hash, a malicious sequencer/prover can inject an arbitrary value into the block hash mapping, corrupting the data returned to all contracts that call the `get_block_hash` syscall.

---

### Finding Description

In `write_block_number_to_block_hash_mapping`, the `old_block_hash` value is populated exclusively by the hint `%{ GetOldBlockNumberAndHash %}` and is written directly to storage with no Cairo-level assertion verifying its correctness:

```cairo
// Currently, the block hash mapping is not enforced by the OS.
// TODO(Yoni, 1/1/2026): output this hash.
local old_block_hash;
%{ GetOldBlockNumberAndHash %}
...
assert [storage_ptr] = DictAccess(key=old_block_number, prev_value=0, new_value=old_block_hash);
``` [1](#0-0) 

In Cairo's execution model, hints are **not part of the proof**. A hint can assign any value to a local variable; only Cairo `assert` statements constrain the proof. Because there is no `assert` that ties `old_block_hash` to any previously committed or cryptographically verified block hash, the proof accepts any value the prover supplies.

A compounding issue exists in `get_block_hashes` in `block_hash.cairo`. The `previous_block_hash`, `header_commitments`, `gas_prices_hash`, and `starknet_version` are all guessed via hint, and the only "check" is another hint (`%{ CheckBlockHashConsistency %}`), which is also not part of the proof:

```cairo
local previous_block_hash;
local header_commitments: BlockHeaderCommitments*;
local gas_prices_hash;
local starknet_version;

%{ GetBlockHashes %}
...
%{ CheckBlockHashConsistency %}
``` [2](#0-1) 

The OS output header comment in `get_block_os_output_header` further confirms the gap:

```cairo
// NOTE: both the previous block hash and previous state root are guessed, and the OS
// does not verify their consistency (unlike the new hash and root).
// The consumer of the OS output should verify both.
``` [3](#0-2) 

The block hash mapping is stored at the reserved contract address `BLOCK_HASH_CONTRACT_ADDRESS = 0x1`, and the buffer is `STORED_BLOCK_HASH_BUFFER = 10`, meaning the hash written for block `N` is the hash of block `N - 10`. [4](#0-3) 

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

The `get_block_hash` syscall (gas cost defined at `GET_BLOCK_HASH_GAS_COST = 10840`) reads from the block hash mapping contract at address `0x1`. Any contract that uses the returned block hash for security-critical logic — including randomness generation, commit-reveal schemes, or cross-contract replay protection — will receive a value that the OS proof does not guarantee is correct. A malicious prover can supply an arbitrary `old_block_hash`, causing those contracts to operate on attacker-controlled data, enabling direct theft of funds from any contract whose security model depends on block hash integrity. [5](#0-4) 

---

### Likelihood Explanation

The StarkNet proof system is designed so that even a malicious sequencer/prover cannot produce an invalid state transition that passes verification. The OS is the enforcement layer. Because the OS explicitly does not enforce the block hash mapping value, any entity that controls proof generation (i.e., the sequencer/prover) can exploit this gap without producing a proof that fails verification. The OS's own TODO comment (`TODO(Yoni, 1/1/2026): output this hash`) confirms this is a known, unresolved gap in enforcement.

---

### Recommendation

1. **Enforce the block hash in the OS proof**: Replace the hint-only pattern with a Cairo `assert` that ties `old_block_hash` to a previously committed and verified value. The OS output should include the block hash mapping writes so that L1 can verify them.
2. **Remove the hint-only `CheckBlockHashConsistency`**: Replace it with a Cairo-level assertion that verifies `previous_block_hash` is consistent with the prior block's committed output.
3. **Complete the TODO**: The comment `TODO(Yoni, 1/1/2026): output this hash` in `write_block_number_to_block_hash_mapping` should be resolved before the deadline, as the current state leaves the block hash mapping entirely unenforced by the proof.

---

### Proof of Concept

1. A malicious prover executes the OS for block `N`.
2. In the `%{ GetOldBlockNumberAndHash %}` hint, the prover supplies `old_block_hash = ATTACKER_CHOSEN_VALUE` for block `N - 10` instead of the real hash.
3. The OS writes `DictAccess(key=N-10, prev_value=0, new_value=ATTACKER_CHOSEN_VALUE)` to the block hash mapping contract storage. [6](#0-5) 
4. No Cairo `assert` in `write_block_number_to_block_hash_mapping` checks that `old_block_hash` equals the actual hash of block `N - 10`. The proof is valid.
5. Any contract calling `get_block_hash(N - 10)` now receives `ATTACKER_CHOSEN_VALUE`.
6. A contract using this value for a commit-reveal lottery or replay-protection nonce is now operating on attacker-controlled data, enabling fund theft.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L127-131)
```text
    // NOTE: both the previous block hash and previous state root are guessed, and the OS
    // does not verify their consistency (unlike the new hash and root).
    // The consumer of the OS output should verify both.
    // TODO(Yoni): verify the consistency of the previous block hash and state root, and remove the
    // state roots from the OS output header.
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L59-79)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L59-65)
```text
const BLOCK_HASH_CONTRACT_ADDRESS = 0x1;
// This contract stores the aliases mapping used for stateful compression.
const ALIAS_CONTRACT_ADDRESS = 0x2;
// Future reserved contract address.
const RESERVED_CONTRACT_ADDRESS = 0x3;
// The block number -> block hash mapping is written for the current block number minus this number.
const STORED_BLOCK_HASH_BUFFER = 10;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L107-107)
```text
const GET_BLOCK_HASH_GAS_COST = 10840;
```
