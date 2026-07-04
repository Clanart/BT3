### Title
Unverified Hint-Supplied Inputs to Block Hash Computation Allow Sequencer to Commit Arbitrary Block Hashes — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo`)

---

### Summary

In `get_block_hashes`, the values `previous_block_hash`, `header_commitments`, `gas_prices_hash`, and `starknet_version` are all supplied exclusively by the sequencer via the `%{ GetBlockHashes %}` hint. No Cairo-level assertion validates any of these values before they are fed into `calculate_block_hash`. The `%{ CheckBlockHashConsistency %}` call is also a hint — it has zero effect on proof validity. As a result, a malicious sequencer can supply arbitrary inputs, causing the OS to produce and commit a provably-valid proof containing a fabricated block hash, breaking the hash chain that links blocks together.

---

### Finding Description

`get_block_hashes` in `block_hash.cairo` is responsible for computing both the previous and new block hashes for each block processed by the OS:

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

    %{ GetBlockHashes %}   // <-- ALL four locals set here, no Cairo assertion follows

    let block_hash = calculate_block_hash(
        block_info=block_info,
        header_commitments=header_commitments,
        gas_prices_hash=gas_prices_hash,
        state_root=state_root,
        previous_block_hash=previous_block_hash,
        starknet_version=starknet_version,
    );

    %{ CheckBlockHashConsistency %}  // <-- hint only, not a proof constraint

    return (previous_block_hash=previous_block_hash, new_block_hash=block_hash);
}
``` [1](#0-0) 

The four hint-supplied locals — `previous_block_hash`, `header_commitments`, `gas_prices_hash`, and `starknet_version` — are passed directly into `calculate_block_hash` with no intervening `assert` or `tempvar` constraint. In Cairo, a `local` variable set only inside a hint block carries no proof obligation; the prover can assign it any field element. The `%{ CheckBlockHashConsistency %}` call is a Python hint, not a Cairo assertion, so it is invisible to the STARK verifier. [2](#0-1) 

The `new_block_hash` produced by this function is written into `OsOutputHeader` and serialized to the proof output: [3](#0-2) 

The `os_utils.cairo` comment explicitly acknowledges that `prev_block_hash` is unverified by the OS and defers responsibility to the consumer:

> "NOTE: both the previous block hash and previous state root are guessed, and the OS does not verify their consistency (unlike the new hash and root). The consumer of the OS output should verify both." [4](#0-3) 

A parallel issue exists in `write_block_number_to_block_hash_mapping`, where `old_block_hash` is also hint-supplied with no Cairo assertion, and the comment states "Currently, the block hash mapping is not enforced by the OS": [5](#0-4) 

---

### Impact Explanation

**High — Unintended chain split (network partition).**

The block hash chain is the cryptographic spine of StarkNet's state continuity. Each block's hash commits to `previous_block_hash`, forming an unbroken chain. Because the OS does not enforce `previous_block_hash` with a Cairo constraint, a malicious sequencer can:

1. Supply an arbitrary `previous_block_hash` for block `N`.
2. The OS computes `new_block_hash(N)` using this fabricated predecessor.
3. The resulting STARK proof is fully valid — the verifier sees a correct execution of `calculate_block_hash` over the supplied inputs.
4. The L1 contract accepts the proof and records `new_block_hash(N)` as canonical.
5. Honest full nodes, computing the hash from the actual chain, arrive at a different value.
6. The L1-accepted chain and the honest P2P network diverge — a chain split.

Additionally, because `gas_prices_hash` and `header_commitments` (which encode transaction count, event count, state diff length, and L1 DA mode) are also unverified, the sequencer can commit proofs where these fields are inconsistent with the actual block contents, further corrupting the canonical record.

---

### Likelihood Explanation

The sequencer is the sole provider of hints to the Cairo VM during proving. No external party needs to be compromised. Any sequencer operator (a role that is not fully decentralized in the current StarkNet architecture) can exploit this by simply providing crafted hint values. The exploit requires no special cryptographic capability — only control over the hint execution environment, which the sequencer already has by design.

---

### Recommendation

Replace the hint-only pattern with Cairo-enforced constraints. For each input to `calculate_block_hash` that is currently hint-supplied, add a verifiable derivation or an explicit `assert` against a committed value:

1. **`previous_block_hash`**: Assert it equals the `new_block_hash` output from the previous block's OS execution (already available in the multi-block loop in `os.cairo`).
2. **`gas_prices_hash`**: Derive it inside Cairo from the `BlockInfo` or a committed gas price structure rather than guessing it via hint.
3. **`header_commitments`**: Compute transaction, event, receipt, and state-diff commitments inside the OS (as the TODO on line 61 acknowledges) and assert the computed values match.
4. **`starknet_version`**: Commit it in `OsGlobalContext` and assert equality rather than guessing per-block.

Remove reliance on `%{ CheckBlockHashConsistency %}` as a security control — it is not a proof constraint.

---

### Proof of Concept

1. Sequencer prepares block `N` with a valid set of transactions.
2. During proving, the hint handler for `GetBlockHashes` returns:
   - `previous_block_hash = 0xDEADBEEF` (arbitrary, not the real hash of block `N-1`)
   - `gas_prices_hash = 0` (zeroed, not reflecting actual gas prices)
   - `header_commitments` with zeroed fields
   - `starknet_version = 0`
3. The Cairo VM executes `calculate_block_hash` over these inputs and produces `new_block_hash(N)`.
4. The STARK proof is generated. The verifier checks only that `calculate_block_hash` was computed correctly given its inputs — it does not check that `previous_block_hash` equals the actual hash of block `N-1`.
5. The proof is submitted to L1. The L1 verifier accepts it.
6. L1 records `new_block_hash(N)` as canonical, but this hash was computed from a fabricated `previous_block_hash`.
7. Honest nodes, computing `new_block_hash(N)` from the real chain, get a different value.
8. The network is now split: L1 and honest nodes disagree on the canonical block hash of block `N`. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L19-50)
```text
func calculate_block_hash{poseidon_ptr: PoseidonBuiltin*}(
    block_info: BlockInfo*,
    header_commitments: BlockHeaderCommitments*,
    gas_prices_hash: felt,
    state_root: felt,
    previous_block_hash: felt,
    starknet_version: felt,
) -> felt {
    static_assert BlockInfo.SIZE == 3;
    static_assert BlockHeaderCommitments.SIZE == 5;

    let hash_state = hash_init();
    with hash_state {
        hash_update_single(BLOCK_HASH_VERSION);
        hash_update_single(block_info.block_number);
        hash_update_single(state_root);
        hash_update_single(block_info.sequencer_address);
        hash_update_single(block_info.block_timestamp);
        hash_update_single(header_commitments.packed_lengths);
        hash_update_single(header_commitments.state_diff_commitment);
        hash_update_single(header_commitments.transaction_commitment);
        hash_update_single(header_commitments.event_commitment);
        hash_update_single(header_commitments.receipt_commitment);
        hash_update_single(gas_prices_hash);
        hash_update_single(starknet_version);
        hash_update_single(0);
        hash_update_single(previous_block_hash);
    }

    let block_hash = hash_finalize(hash_state=hash_state);
    return block_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L55-82)
```text
func get_block_hashes{poseidon_ptr: PoseidonBuiltin*}(block_info: BlockInfo*, state_root: felt) -> (
    previous_block_hash: felt, new_block_hash: felt
) {
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L34-49)
```text
struct OsOutputHeader {
    state_update_output: CommitmentUpdate*,
    prev_block_number: felt,
    new_block_number: felt,
    prev_block_hash: felt,
    // Currently, the block hash is not enforced by the OS.
    new_block_hash: felt,
    // The hash of the OS program, if the aggregator was used. Zero if the OS was used directly.
    os_program_hash: felt,
    starknet_os_config_hash: felt,
    // Indicates whether to use KZG commitment scheme instead of adding the data-availability to
    // the transaction data.
    use_kzg_da: felt,
    // Indicates whether previous state values are included in the state update information.
    full_output: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L62-75)
```text
    %{ GetBlockHashMapping %}

    // Currently, the block hash mapping is not enforced by the OS.
    // TODO(Yoni, 1/1/2026): output this hash.
    local old_block_hash;
    %{ GetOldBlockNumberAndHash %}

    // Update mapping.
    assert state_entry.class_hash = 0;
    assert state_entry.nonce = 0;
    tempvar storage_ptr = state_entry.storage_ptr;
    assert [storage_ptr] = DictAccess(key=old_block_number, prev_value=0, new_value=old_block_hash);
    let storage_ptr = storage_ptr + DictAccess.SIZE;
    %{ WriteOldBlockToStorage %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L121-150)
```text
func get_block_os_output_header{poseidon_ptr: PoseidonBuiltin*}(
    block_context: BlockContext*,
    state_update_output: CommitmentUpdate*,
    os_global_context: OsGlobalContext*,
) -> OsOutputHeader* {
    // Calculate the block hash based on the block info and state root.
    // NOTE: both the previous block hash and previous state root are guessed, and the OS
    // does not verify their consistency (unlike the new hash and root).
    // The consumer of the OS output should verify both.
    // TODO(Yoni): verify the consistency of the previous block hash and state root, and remove the
    // state roots from the OS output header.
    let (prev_block_hash, new_block_hash) = get_block_hashes{poseidon_ptr=poseidon_ptr}(
        block_info=block_context.block_info_for_execute, state_root=state_update_output.final_root
    );

    // All blocks inside of a multi block should be off-chain and therefore
    // should not be compressed.
    tempvar os_output_header = new OsOutputHeader(
        state_update_output=state_update_output,
        prev_block_number=block_context.block_info_for_execute.block_number - 1,
        new_block_number=block_context.block_info_for_execute.block_number,
        prev_block_hash=prev_block_hash,
        new_block_hash=new_block_hash,
        os_program_hash=0,
        starknet_os_config_hash=os_global_context.starknet_os_config_hash,
        use_kzg_da=FALSE,
        full_output=TRUE,
    );
    return os_output_header;
}
```
