### Title
Hint-Supplied Block Hash Inputs Are Unconstrained by Cairo Assertions, Allowing a Malicious Prover to Commit a Forged Block Hash — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo`)

---

### Summary

The `get_block_hashes` function in `block_hash.cairo` computes the canonical `new_block_hash` using `header_commitments` (covering `transaction_commitment`, `event_commitment`, `receipt_commitment`, `state_diff_commitment`, `packed_lengths`) and `gas_prices_hash` that are supplied entirely by the prover via hints, with **no Cairo-level assertions** binding them to the actual transactions, events, or state diff executed in the block. A companion flaw in `os_utils.cairo` similarly allows the `old_block_hash` written into the block-hash-contract storage (and later returned by the `get_block_hash` syscall) to be an arbitrary prover-chosen value. Both omissions are explicitly acknowledged by TODO comments in the code.

---

### Finding Description

**Root cause 1 — `block_hash.cairo`, `get_block_hashes`**

```cairo
// Currently, the header commitments and gas prices are not computed by the OS.
// TODO(Yoni, 1/1/2027): compute the header commitments and gas prices.
local header_commitments: BlockHeaderCommitments*;
local gas_prices_hash;

%{ GetBlockHashes %}          // prover-supplied witness; no Cairo assertion follows

let block_hash = calculate_block_hash(
    block_info=block_info,
    header_commitments=header_commitments,
    gas_prices_hash=gas_prices_hash,
    ...
);

%{ CheckBlockHashConsistency %}   // hint only — not a Cairo constraint
```

`header_commitments` and `gas_prices_hash` are `local` variables filled exclusively by the `GetBlockHashes` hint. `calculate_block_hash` then hashes them into `new_block_hash` without any `assert` that they match the transactions or events actually executed. `CheckBlockHashConsistency` is Python hint code; it produces no Cairo constraint and is invisible to the STARK verifier.

**Root cause 2 — `os_utils.cairo`, `write_block_number_to_block_hash_mapping`**

```cairo
// Currently, the block hash mapping is not enforced by the OS.
// TODO(Yoni, 1/1/2026): output this hash.
local old_block_hash;
%{ GetOldBlockNumberAndHash %}    // prover-supplied; no Cairo assertion

assert [storage_ptr] = DictAccess(
    key=old_block_number, prev_value=0, new_value=old_block_hash
);
```

`old_block_hash` is written into the `BLOCK_HASH_CONTRACT_ADDRESS` storage (contract `0x1`) with no Cairo constraint tying it to any previously committed block hash. Every contract that calls the `get_block_hash` syscall reads from this storage.

The `get_block_os_output_header` caller in `os_utils.cairo` also documents that `prev_block_hash` is guessed and unverified:

> "NOTE: both the previous block hash and previous state root are guessed, and the OS does not verify their consistency."

---

### Impact Explanation

**Forged `new_block_hash` → Unintended chain split (High)**

The `new_block_hash` produced by `get_block_hashes` is serialized into the OS output header and ultimately posted to L1 as the authoritative hash of the block. Because the inputs to `calculate_block_hash` are unconstrained, a malicious prover can produce a STARK proof that is cryptographically valid yet encodes a `new_block_hash` that does not correspond to the actual transactions in the block. L1 verifies the proof but cannot distinguish a correct hash from a forged one. Subsequent blocks embed this forged hash as `previous_block_hash`, permanently diverging the on-chain block-hash chain from the true execution history — an unintended chain split.

**Forged `old_block_hash` in storage → Direct loss of funds (Critical)**

Any contract that calls `get_block_hash(block_number)` receives the value written by `write_block_number_to_block_hash_mapping`. Because `old_block_hash` is unconstrained, a malicious prover can write an arbitrary value. Contracts that use historical block hashes as a source of entropy (e.g., lottery, random-number-based NFT minting) or as a commitment anchor (e.g., cross-chain bridges, time-locked vaults) will operate on a forged value, enabling the prover to predict or manipulate outcomes and drain funds.

---

### Likelihood Explanation

The vulnerability requires the prover to supply crafted hint values. In StarkNet's current architecture the prover is operated by StarkWare (a trusted party), which reduces near-term likelihood. However:

- The OS Cairo code is the protocol's source of truth; the absence of Cairo constraints means the protocol's correctness guarantee is entirely dependent on prover honesty rather than cryptographic enforcement.
- StarkNet's roadmap includes decentralized proving; once proving is permissionless, any prover can exploit these gaps without any additional privilege.
- The TODO comments (`1/1/2026`, `1/1/2027`) confirm these are known, unresolved gaps — not intentional design choices — increasing the window of exposure.

The analog to the external report is direct: just as `LightClient.force` allowed finalization with as few as 10 signers by bypassing the two-thirds threshold, the OS here allows block hash commitment with zero verified inputs by bypassing the requirement to compute commitments from actual block contents.

---

### Recommendation

1. **Short term**: Add Cairo `assert` statements in `get_block_hashes` that recompute `transaction_commitment`, `event_commitment`, `receipt_commitment`, `state_diff_commitment`, and `gas_prices_hash` from the actual executed transactions and events, and assert equality against the hint-supplied values before passing them to `calculate_block_hash`. Remove reliance on `CheckBlockHashConsistency` as a security control.

2. **Short term**: In `write_block_number_to_block_hash_mapping`, derive `old_block_hash` from the OS output of the corresponding prior block (already present in the OS output chain) and assert equality, rather than accepting it as an unconstrained hint.

3. **Long term**: Resolve both TODO items (`TODO(Yoni, 1/1/2026)` and `TODO(Yoni, 1/1/2027)`) before any decentralized proving is enabled, as the current design makes protocol correctness contingent on prover honesty rather than cryptographic proof.

---

### Proof of Concept

1. A prover constructs a block containing a set of legitimate transactions `T`.
2. Instead of computing `transaction_commitment = hash(T)`, the prover supplies `header_commitments.transaction_commitment = hash(T')` where `T'` is a different (e.g., empty or attacker-chosen) transaction set.
3. The prover also supplies `gas_prices_hash = 0` and arbitrary values for the remaining commitment fields.
4. `calculate_block_hash` hashes these forged inputs and produces `new_block_hash_forged`.
5. The prover generates a valid STARK proof — the proof is sound because no Cairo `assert` checks the commitment values.
6. L1 verifies the proof and records `new_block_hash_forged` as the canonical hash of the block.
7. The next block's `previous_block_hash` is `new_block_hash_forged`, permanently breaking the block-hash chain.
8. Simultaneously, the prover writes an arbitrary `old_block_hash` into the block-hash-contract storage; any contract calling `get_block_hash` in that or subsequent blocks receives the forged value, enabling fund manipulation in contracts that depend on it. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L63-75)
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
    let storage_ptr = storage_ptr + DictAccess.SIZE;
    %{ WriteOldBlockToStorage %}
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
