### Title
Unverified Hint-Supplied `old_block_hash` and `gas_prices_hash` Written to Block Hash Mapping and Block Hash Output Without Cairo Enforcement — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo`)

---

### Summary

The StarkNet OS uses hint-supplied, unverified values for `old_block_hash`, `gas_prices_hash`, and `header_commitments` in two critical paths: the block hash mapping contract (which contracts read via `get_block_hash`) and the block hash committed to the OS output. No Cairo-level constraint enforces correctness of these values. This is the direct analog of the `slot0` manipulation vulnerability: just as `slot0` returns an unverified instantaneous price that a sequencer/MEV actor can manipulate, the OS here accepts sequencer-supplied block hash components without proof-system enforcement, allowing a sequencer to inject arbitrary values that pass proof verification.

---

### Finding Description

**Root cause 1 — `get_block_hashes` in `block_hash.cairo`:**

```cairo
func get_block_hashes{poseidon_ptr: PoseidonBuiltin*}(block_info: BlockInfo*, state_root: felt) -> (
    previous_block_hash: felt, new_block_hash: felt
) {
    alloc_locals;
    local previous_block_hash;
    // Currently, the header commitments and gas prices are not computed by the OS.
    // TODO(Yoni, 1/1/2027): compute the header commitments and gas prices.
    local header_commitments: BlockHeaderCommitments*;
    local gas_prices_hash;
    local starknet_version;

    %{ GetBlockHashes %}   // <-- hint supplies all four locals; no Cairo assertion follows

    let block_hash = calculate_block_hash(
        block_info=block_info,
        header_commitments=header_commitments,
        gas_prices_hash=gas_prices_hash,
        ...
    );
    %{ CheckBlockHashConsistency %}   // <-- consistency check is also a hint, not a Cairo assert
    ...
}
```

`header_commitments` (containing `transaction_commitment`, `event_commitment`, `receipt_commitment`, `state_diff_commitment`, `packed_lengths`) and `gas_prices_hash` are loaded exclusively from the `%{ GetBlockHashes %}` hint. There is no subsequent `assert` or range-check in Cairo code that binds these locals to any computed value. `%{ CheckBlockHashConsistency %}` is itself a hint — it runs Python-side and produces no Cairo constraint. The result is that `calculate_block_hash` ingests fully sequencer-controlled inputs.

**Root cause 2 — `write_block_number_to_block_hash_mapping` in `os_utils.cairo`:**

```cairo
// Currently, the block hash mapping is not enforced by the OS.
// TODO(Yoni, 1/1/2026): output this hash.
local old_block_hash;
%{ GetOldBlockNumberAndHash %}   // <-- hint supplies old_block_hash; no Cairo assertion follows

assert [storage_ptr] = DictAccess(key=old_block_number, prev_value=0, new_value=old_block_hash);
```

`old_block_hash` is loaded from a hint and written directly into the block hash mapping contract's storage dictionary. No Cairo assertion ties `old_block_hash` to any cryptographic commitment of the actual block at `old_block_number`. This storage entry is then committed into the global state root via `state_update`, making the manipulated value part of the proven state.

**Root cause 3 — `OsOutputHeader` in `output.cairo`:**

```cairo
struct OsOutputHeader {
    ...
    // Currently, the block hash is not enforced by the OS.
    new_block_hash: felt,
    ...
}
```

The `new_block_hash` field in the serialized OS output is explicitly documented as unenforced. Combined with root cause 1, the block hash written to L1 is entirely sequencer-controlled.

**Call chain:**

```
os.cairo::execute_blocks
  → os_utils.cairo::pre_process_block
      → os_utils.cairo::write_block_number_to_block_hash_mapping   [root cause 2]
  → os_utils.cairo::get_block_os_output_header
      → block_hash.cairo::get_block_hashes                          [root cause 1]
          → block_hash.cairo::calculate_block_hash
```

---

### Impact Explanation

**Direct loss of funds (Critical).**

The `get_block_hash` syscall is a standard StarkNet primitive that contracts use for commit-reveal schemes, on-chain randomness, and cross-contract verification. The value returned by `get_block_hash(N)` is exactly the value written by `write_block_number_to_block_hash_mapping` into the block hash contract storage. Because `old_block_hash` is hint-supplied and unconstrained by Cairo, a sequencer can write any value — including a value chosen to break a specific protocol's security invariant — and the STARK proof will remain valid.

Concrete attack scenario:
- A DeFi protocol uses `get_block_hash(N)` as a source of randomness or as a commit-reveal anchor.
- The sequencer, knowing the protocol's logic, supplies `old_block_hash = H_adversarial` via the hint.
- The OS writes `H_adversarial` into the block hash mapping contract and commits it to the state root.
- The proof verifies successfully on L1.
- The protocol reads `H_adversarial` via `get_block_hash`, and the adversarial value triggers a favorable outcome (e.g., winning a lottery, bypassing a time-lock, replaying a commitment).
- Funds are drained.

Additionally, `gas_prices_hash` and `header_commitments` being unverified means the block hash committed to L1 does not cryptographically bind the actual gas prices or transaction/event commitments of the block. This breaks the integrity guarantee of the block hash as a tamper-evident summary of block contents.

---

### Likelihood Explanation

The sequencer is the entity that provides all hints to the Cairo VM during proof generation. In the StarkNet protocol, the OS is the mechanism that is supposed to constrain the sequencer — the proof system's purpose is to ensure correctness even against a sequencer that deviates from the protocol. Because the OS performs no Cairo-level assertion on `old_block_hash`, `gas_prices_hash`, or `header_commitments`, any sequencer can supply arbitrary values for these fields with zero additional effort. The TODOs in the code (`TODO(Yoni, 1/1/2026)`, `TODO(Yoni, 1/1/2027)`) confirm this is a known, unresolved gap in enforcement. Likelihood is high for any sequencer motivated to exploit protocols that rely on `get_block_hash`.

---

### Recommendation

1. **Compute `old_block_hash` inside Cairo**: Derive it from the committed state of the previous block rather than accepting it from a hint. At minimum, add a Cairo `assert` that binds `old_block_hash` to a value that has been cryptographically committed in a prior OS output.

2. **Compute `gas_prices_hash` and `header_commitments` inside Cairo**: These should be derived from the actual transaction execution results (fees paid, events emitted, state diff size) rather than from hints. The `TODO(Yoni, 1/1/2027)` acknowledges this; it should be treated as a security-critical fix, not a future improvement.

3. **Replace `%{ CheckBlockHashConsistency %}` with a Cairo `assert`**: A hint-based consistency check provides no proof-system guarantee. The check must be a Cairo constraint.

---

### Proof of Concept

```
1. Sequencer identifies a target protocol P that uses get_block_hash(N) for security.
2. Sequencer computes H_adversarial = the block hash value that causes P to release funds.
3. During proof generation for block N + STORED_BLOCK_HASH_BUFFER, the sequencer
   provides H_adversarial via the %{ GetOldBlockNumberAndHash } hint.
4. os_utils.cairo::write_block_number_to_block_hash_mapping writes:
     DictAccess(key=N, prev_value=0, new_value=H_adversarial)
   into the block hash contract storage — no Cairo assertion rejects this.
5. state_update commits this storage entry into the global state root.
6. The STARK proof is generated and verified on L1 successfully.
7. Protocol P calls get_block_hash(N) and receives H_adversarial.
8. P's security invariant is broken; funds are transferred to the attacker.
```

**Affected files and lines:**

- `block_hash.cairo` lines 59–77: `get_block_hashes` — hint-supplied `gas_prices_hash`, `header_commitments`, `previous_block_hash` with no Cairo assertion. [1](#0-0) 

- `os_utils.cairo` lines 64–73: `write_block_number_to_block_hash_mapping` — hint-supplied `old_block_hash` written to state with no Cairo assertion. [2](#0-1) 

- `output.cairo` lines 39–40: `OsOutputHeader.new_block_hash` — explicitly documented as unenforced. [3](#0-2) 

- `os_utils.cairo` lines 126–134: `get_block_os_output_header` — comment confirms previous block hash and state root are guessed and unverified by the OS. [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L59-77)
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
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L126-134)
```text
    // Calculate the block hash based on the block info and state root.
    // NOTE: both the previous block hash and previous state root are guessed, and the OS
    // does not verify their consistency (unlike the new hash and root).
    // The consumer of the OS output should verify both.
    // TODO(Yoni): verify the consistency of the previous block hash and state root, and remove the
    // state roots from the OS output header.
    let (prev_block_hash, new_block_hash) = get_block_hashes{poseidon_ptr=poseidon_ptr}(
        block_info=block_context.block_info_for_execute, state_root=state_update_output.final_root
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L39-40)
```text
    // Currently, the block hash is not enforced by the OS.
    new_block_hash: felt,
```
