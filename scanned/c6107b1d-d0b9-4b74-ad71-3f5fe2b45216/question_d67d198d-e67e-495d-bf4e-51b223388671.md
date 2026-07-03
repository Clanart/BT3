[File: 'crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo (L1-125)
```text
from starkware.cairo.common.bool import FALSE, TRUE
from starkware.cairo.common.builtin_poseidon.poseidon import poseidon_hash_many
from starkware.cairo.common.cairo_builtins import EcOpBuiltin, PoseidonBuiltin
from starkware.cairo.common.dict_access import DictAccess
from starkware.starknet.core.os.block_context import BlockContext, OsGlobalContext
from starkware.starknet.core.os.block_hash import get_block_hashes
from starkware.starknet.core.os.output import MessageToL1Header, OsOutput, OsOutputHeader
from starkware.starknet.core.os.state.commitment import CommitmentUpdate
from starkware.starknet.core.os.virtual_os_output import (
    VIRTUAL_OS_OUTPUT_VERSION,
    VirtualOsOutputHeader,
)

// Hashes each L2-to-L1 message separately and writes the hash to the output.
func output_message_to_l1_hashes{output_ptr: felt*, poseidon_ptr: PoseidonBuiltin*}(
    messages_ptr_start: felt*, messages_ptr_end: felt*
) {
    if (messages_ptr_start == messages_ptr_end) {
        return ();
    }

    // Read the message header.
    let message_header = cast(messages_ptr_start, MessageToL1Header*);

    // Hash the message (header + payload).
    let message_size = MessageToL1Header.SIZE + message_header.payload_size;
    let (message_hash) = poseidon_hash_many(n=message_size, elements=messages_ptr_start);

    // Store the hash and advance output_ptr.
    assert output_ptr[0] = message_hash;
    let output_ptr = &output_ptr[1];

    return output_message_to_l1_hashes(
        messages_ptr_start=&messages_ptr_start[message_size], messages_ptr_end=messages_ptr_end
    );
}

// Does nothing for the virtual OS.
func pre_process_block{
    range_check_ptr,
    poseidon_ptr: PoseidonBuiltin*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
}(block_context: BlockContext*) {
    return ();
}

// Returns the OS output header for the virtual OS.
// Note that unlike the Starknet sequencer OS, the virtual OS expects the block info to be of
// the **previous** block.
func get_block_os_output_header{poseidon_ptr: PoseidonBuiltin*}(
    block_context: BlockContext*,
    state_update_output: CommitmentUpdate*,
    os_global_context: OsGlobalContext*,
) -> OsOutputHeader* {
    // Calculate the previous block hash based on the block info and the **initial** state root.
    let (_prev_prev_block_hash, prev_block_hash) = get_block_hashes{poseidon_ptr=poseidon_ptr}(
        block_info=block_context.block_info_for_execute, state_root=state_update_output.initial_root
    );

    tempvar os_output_header = new OsOutputHeader(
        state_update_output=state_update_output,
        prev_block_number=block_context.block_info_for_execute.block_number,
        new_block_number=0,
        prev_block_hash=prev_block_hash,
        new_block_hash=0,
        os_program_hash=0,
        starknet_os_config_hash=os_global_context.starknet_os_config_hash,
        use_kzg_da=FALSE,
        full_output=TRUE,
    );
    return os_output_header;
}

// Processes OS outputs for the virtual OS.
// Outputs the virtual OS header and the messages to L1.
func process_os_output{
    output_ptr: felt*, range_check_ptr, ec_op_ptr: EcOpBuiltin*, poseidon_ptr: PoseidonBuiltin*
}(n_blocks: felt, os_outputs: OsOutput*, n_public_keys: felt, public_keys: felt*) {
    alloc_locals;
    assert n_public_keys = 0;

    // Part of the VIRTUAL_SNOS0 version contract. Changes must trigger a version bump.
    assert n_blocks = 1;
    let os_output = os_outputs[0];

    let header = os_output.header;

    // Hash each message to L1 separately and write hashes to output.
    // We'll write hashes starting after the header (which we'll write later).
    let output_header_placeholder = cast(output_ptr, VirtualOsOutputHeader*);
    let output_ptr = output_ptr + VirtualOsOutputHeader.SIZE;
    let messages_to_l1_hashes_ptr_start: felt* = output_ptr;

    output_message_to_l1_hashes(
        messages_ptr_start=os_output.initial_carried_outputs.messages_to_l1,
        messages_ptr_end=os_output.final_carried_outputs.messages_to_l1,
    );

    // Calculate the number of messages from the pointer difference.
    let n_l2_to_l1_messages = output_ptr - messages_to_l1_hashes_ptr_start;

    // Create the virtual OS output header with count of messages.
    assert [output_header_placeholder] = VirtualOsOutputHeader(
        output_version=VIRTUAL_OS_OUTPUT_VERSION,
        base_block_number=header.prev_block_number,
        base_block_hash=header.prev_block_hash,
        starknet_os_config_hash=header.starknet_os_config_hash,
        n_l2_to_l1_messages=n_l2_to_l1_messages,
    );

    return ();
}

// Returns whether aliases should be allocated for state updates.
// In virtual OS mode, aliases should not be allocated.
func should_allocate_aliases() -> felt {
    return FALSE;
}

// Returns a function pointer to execute_deprecated_syscalls.
// In virtual OS mode, deprecated syscalls are not supported, so we return 0.
func get_execute_deprecated_syscalls_ptr() -> (res: felt*) {
    return (res=cast(0, felt*));
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/virtual_os_output.cairo (L1-67)
```text
// Marker indicating SNOS (StarkNet OS) proof facts variant.
const VIRTUAL_SNOS = 'VIRTUAL_SNOS';

// Marker indicating proof facts format version 0.
const PROOF_VERSION = 'PROOF0';

// The version of the virtual OS output.
//
// === VIRTUAL_SNOS0 Version Contract ===
//
// This version string is a commitment to the exact behavior and output format of the virtual OS.
// Any change to the following guarantees MUST be accompanied by a version bump.
//
// 1. Output format:
//    The output is a flat array of felts with the following layout:
//      [output_version, base_block_number, base_block_hash, starknet_os_config_hash,
//       n_l2_to_l1_messages, message_hash_0, message_hash_1, ...]
//    - output_version: the VIRTUAL_OS_OUTPUT_VERSION constant.
//    - base_block_number / base_block_hash: the block this run is based on. The hash is
//      computed (proven) by the OS from the block info and the initial state root.
//    - starknet_os_config_hash: Poseidon hash of the Starknet OS config.
//    - n_l2_to_l1_messages: count of L2-to-L1 message hashes that follow.
//    - Each message hash is Poseidon([from_address, to_address, payload_size, ...payload]).
//    No state diff, data availability, or state roots are included.
//
// 2. Single block, single transaction:
//    - Exactly 1 block is processed (asserted in process_os_output).
//    - Exactly 1 transaction per block, which must be INVOKE_FUNCTION (asserted in
//      execute_transactions_inner).
//
// 3. Blocked syscalls:
//    The following syscalls are NOT available in virtual OS mode and will cause a Cairo error:
//      - Deploy
//      - GetBlockHash
//      - ReplaceClass
//      - Keccak
//      - MetaTxV0
//
// 4. Cairo 1 only:
//    Only Sierra (Cairo 1) contracts are supported. Deprecated (Cairo 0) entry points are
//    unreachable.
//
// 5. No proof facts:
//    The virtual OS does not support recursive proof facts (proof_facts_size must be 0).
//
// 6. Block info semantics:
//     get_execution_info returns the **base (previous) block** info.
//
// Changes to ANY of the above MUST trigger a version bump.
const VIRTUAL_OS_OUTPUT_VERSION = 'VIRTUAL_SNOS0';

// The header of the proof facts, preceding the virtual OS output.
struct ProofHeader {
    proof_version: felt,
    proof_variant: felt,
    program_hash: felt,
}

// The header of the virtual OS output.
struct VirtualOsOutputHeader {
    output_version: felt,
    // The block number and hash that this run is based on.
    base_block_number: felt,
    base_block_hash: felt,
    starknet_os_config_hash: felt,
    n_l2_to_l1_messages: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L1-82)
```text
from starkware.cairo.common.cairo_builtins import PoseidonBuiltin
from starkware.cairo.common.hash_state_poseidon import hash_finalize, hash_init, hash_update_single
from starkware.starknet.common.new_syscalls import BlockInfo

// The latest block hash version.
const BLOCK_HASH_VERSION = 'STARKNET_BLOCK_HASH1';

struct BlockHeaderCommitments {
    transaction_commitment: felt,
    event_commitment: felt,
    receipt_commitment: felt,
    state_diff_commitment: felt,
    // Packed encoding of transaction count, event count, state diff length, and L1 data
    // availability mode.
    packed_lengths: felt,
}

// Calculates the block hash given the top level components.
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

// Calculates the new block hash given the block info and the state root.
// Guesses the rest of the block hash components to complete the hash calculation.
// Returns the previous block hash and the new block hash.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transactions_inner__virtual.cairo (L1-33)
```text
// Virtual OS version of execute_transactions_inner.cairo

from starkware.cairo.common.dict_access import DictAccess
from starkware.starknet.core.os.block_context import BlockContext
from starkware.starknet.core.os.builtins import BuiltinPointers
from starkware.starknet.core.os.execution.transaction_impls import (
    execute_invoke_function_transaction,
)
from starkware.starknet.core.os.output import OsCarriedOutputs

// In virtual OS mode, we only support a single INVOKE_FUNCTION transaction.
func execute_transactions_inner{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, n_txs) {
    // Part of the VIRTUAL_SNOS0 version contract. Changes must trigger a version bump.
    with_attr error_message(
