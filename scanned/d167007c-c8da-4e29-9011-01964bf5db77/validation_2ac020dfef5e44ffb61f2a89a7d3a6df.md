## Title
Precompile "signature count" byte silently drives both real CPU cost and cost-model billing, and can be inflated far beyond the real number of ECDSA/Ed25519 checks actually performed — leading to unmetered CPU-cost amplification analogous to unreimbursed L1 data fees - (File: `runtime-transaction/src/signature_details.rs`, `precompiles/src/secp256k1.rs`, `precompiles/src/ed25519.rs`)

### Summary
The Sherlock report's core defect is that a fee/cost accounting model reimburses only one narrow, cheap-to-compute cost component (L2 execution gas) while ignoring a second, dominant, and independently-scaling real cost component (L1 data-publishing fee), letting the true resource consumption diverge sharply from what is billed. In Agave, the exact analog exists in how precompile ("native program") signature-verification cost is billed for block-cost/scheduler purposes: the entire cost model treats the number of secp256k1/ed25519/secp256r1 "signatures" purely as `data[0]` — a single attacker-controlled byte read out of each precompile instruction's own payload — and multiplies that count by a fixed per-signature cost constant. Nothing ties that billed count back to how much data is actually hashed/recovered by the corresponding `verify()` routine for that instruction, because each of the `count` signature-offset entries can point its `message_data_offset`/`message_data_size` fields at arbitrary (and reusable) byte ranges inside *any* instruction in the same transaction, including large ones.

### Finding Description
The cost model bills precompile signature cost strictly from a self-reported counter, not from work actually verified: [1](#0-0) 

```
fn get_num_signatures_in_instruction(instruction: &SVMInstruction) -> u64 {
    u64::from(instruction.data.first().copied().unwrap_or(0))
}
```

This value is summed per-program and cached into `TransactionSignatureDetails`: [2](#0-1) 

and consumed directly by `CostModel::get_signature_cost`, which multiplies the raw declared counts by fixed per-unit constants (`SECP256K1_VERIFY_COST`, `ED25519_VERIFY_STRICT_COST`, `SECP256R1_VERIFY_COST`): [3](#0-2) 

The billing model implicitly assumes each unit of `count` corresponds to one fixed-cost signature check of fixed-size data (~64 bytes). But the actual precompile `verify()` implementations let each of the `count` entries specify an **independent, attacker-chosen `message_data_offset`/`message_data_size`** referencing *any* instruction's data in the transaction — not just a fixed-size signature blob: [4](#0-3) 

```
let message = get_data_slice(
    data,
    instruction_datas,
    offsets.message_instruction_index,
    offsets.message_data_offset,
    offsets.message_data_size as usize,
)?;
...
publickey.verify_strict(message, &signature)...
``` [5](#0-4) 

```
let message_slice = get_data_slice(
    instruction_datas,
    offsets.message_instruction_index,
    offsets.message_data_offset,
    offsets.message_data_size as usize,
)?;
let message_hash: [u8; 32] = solana_keccak_hasher::hash(message_slice).to_bytes();
let pubkey = libsecp256k1::recover(...)...
```

So a single precompile instruction with `count = 255` (max `u8`) can declare 255 separate signature-offset entries, each pointing `message_instruction_index`/`message_data_offset`/`message_data_size` at the *same* large instruction payload elsewhere in the transaction (up to the packet size limit). Each entry then forces the validator to perform a full keccak-256 hash over that (large) slice plus a real secp256k1 EC point recovery (or Ed25519 `verify_strict`) — i.e., up to 255 independent, computationally-expensive crypto operations and up to 255 re-hashes of the same multi-hundred-byte buffer, driven from *one* instruction. Every validator that receives the transaction (via TPU/QUIC ingestion, forwarding, or banking-stage/scheduler `sigverify`) performs this real CPU work as part of ordinary transaction verification, which happens irrespective of the transaction ultimately landing in a block.

The cost model faithfully bills `count * SECP256K1_VERIFY_COST` (or the ed25519/secp256r1 equivalent) for block-inclusion/scheduling purposes, so on paper the "fee" scales with `count`. But — exactly like the L1 data-fee report — the byte that determines the *charged* cost (`data[0]`) is decoupled from the actual dominant driver of real CPU time, which is the size and number of independently-recovered/hashed message slices, not a flat per-signature constant. An attacker can therefore submit transactions (via TPU/QUIC, unprivileged, no special role required) that are cheap in terms of network bandwidth (fits in one packet, ~1232 bytes) yet force disproportionate real CPU consumption during signature verification across every validator on the cluster, well before any bank-level fee deduction or cost-tracker admission decision is even reached, since the crypto work happens as part of parsing/verifying the transaction. This is a non-RPC remote resource-exhaustion vector, unprivileged and reachable purely by broadcasting packets, exactly matching the report's underlying invariant: "the fee model reimburses (bills) a narrow proxy metric while the true, dominant cost component scales independently and can be driven arbitrarily high relative to what's charged."

### Impact Explanation
This falls under "non-RPC remote exhaustion/crash" for unprivileged Agave issues in transactions/QUIC/TPU and gossip processing. An attacker who can send transaction packets to a validator's TPU/QUIC ingestion path (no special stake or trust needed) can craft transactions that force disproportionately expensive precompile CPU work (multiple large-buffer hashes plus EC recoveries) relative to their apparent "signature count" cost and packet size, degrading validator throughput cluster-wide during signature verification, independent of whether the transactions are ultimately included in a block or pay any fee.

### Likelihood Explanation
Constructing such a transaction requires no special privileges: any client can build a legacy or v0 transaction containing a single secp256k1/ed25519/secp256r1 instruction with `count` set to the maximum representable by a `u8` (255) and offsets crafted to reuse the same large-instruction data slice repeatedly. The `verify()` routines place no additional cap on how many times the same bytes can be referenced across offset entries, and the cost model has no visibility into (or bound on) the referenced-data reuse pattern — it only sees the declared `count`.

### Recommendation
Decouple/rebound real precompile verification cost from the raw self-reported `count` byte: either (a) bound and charge cost proportional to the actual bytes hashed/recovered across all offset entries (sum of `message_data_size` per entry, not just instruction count), (b) disallow overlapping/duplicate message-data references across offset entries within a single precompile instruction, or (c) cap the total keccak/EC-recovery work performed per transaction independent of the declared `count`, and gate this cost before performing expensive verification work in the sigverify/ingestion path rather than after.

### Proof of Concept
1. Construct a transaction with a single `secp256k1_program` instruction whose `data[0] = 255` (max signature count).
2. Populate 255 `SecpSignatureOffsets` entries in the instruction data, each with `signature_instruction_index`, `eth_address_instruction_index`, and `message_instruction_index` pointing to a second, large instruction in the same transaction (e.g., a memo-style instruction filled with ~900 bytes of dummy data), with `message_data_size` set to that instruction's full length.
3. Broadcast the transaction (does not need to be a valid signature per entry to force the recovery/hash attempt — the hash/recovery is attempted before failure is confirmed) over TPU/QUIC to a validator.
4. Observe that `secp256k1::verify()` performs up to 255 `keccak256` hashes over the large slice plus 255 `libsecp256k1::recover` calls (or ed25519 `verify_strict` calls for the ed25519 precompile) per transaction — real, expensive CPU work — while `CostModel::get_signature_cost` only reflects `255 * SECP256K1_VERIFY_COST`, a constant per-signature charge that does not scale with the actual (attacker-controlled) size of hashed data or with the fact that the same data buffer is reused across all 255 entries. [6](#0-5) [7](#0-6)

### Citations

**File:** runtime-transaction/src/signature_details.rs (L29-53)
```rust
impl PrecompileSignatureDetailsBuilder {
    pub fn process_instruction(&mut self, program_id: &Pubkey, instruction: &SVMInstruction) {
        let program_id_index = instruction.program_id_index;
        match self.filter.is_signature(program_id_index, program_id) {
            ProgramIdStatus::NotSignature => {}
            ProgramIdStatus::Secp256k1 => {
                self.value.num_secp256k1_instruction_signatures = self
                    .value
                    .num_secp256k1_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
            ProgramIdStatus::Ed25519 => {
                self.value.num_ed25519_instruction_signatures = self
                    .value
                    .num_ed25519_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
            ProgramIdStatus::Secp256r1 => {
                self.value.num_secp256r1_instruction_signatures = self
                    .value
                    .num_secp256r1_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
        }
    }
```

**File:** runtime-transaction/src/signature_details.rs (L60-74)
```rust
/// Get transaction signature details.
pub fn get_precompile_signature_details<'a>(
    instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)>,
) -> PrecompileSignatureDetails {
    let mut builder = PrecompileSignatureDetailsBuilder::default();
    for (program_id, instruction) in instructions {
        builder.process_instruction(program_id, &instruction);
    }
    builder.build()
}

#[inline]
fn get_num_signatures_in_instruction(instruction: &SVMInstruction) -> u64 {
    u64::from(instruction.data.first().copied().unwrap_or(0))
}
```

**File:** cost-model/src/cost_model.rs (L129-151)
```rust
    /// Returns signature details and the total signature cost
    fn get_signature_cost(transaction: &impl TransactionMeta) -> u64 {
        let signatures_count_detail = transaction.signature_details();

        signatures_count_detail
            .num_transaction_signatures()
            .saturating_mul(SIGNATURE_COST)
            .saturating_add(
                signatures_count_detail
                    .num_secp256k1_instruction_signatures()
                    .saturating_mul(SECP256K1_VERIFY_COST),
            )
            .saturating_add(
                signatures_count_detail
                    .num_ed25519_instruction_signatures()
                    .saturating_mul(ED25519_VERIFY_STRICT_COST),
            )
            .saturating_add(
                signatures_count_detail
                    .num_secp256r1_instruction_signatures()
                    .saturating_mul(SECP256R1_VERIFY_COST),
            )
    }
```

**File:** precompiles/src/ed25519.rs (L66-76)
```rust
        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;
        publickey
            .verify_strict(message, &signature)
            .map_err(|_| PrecompileError::InvalidSignature)?;
```

**File:** precompiles/src/secp256k1.rs (L44-102)
```rust
    for i in 0..count {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(1);
        let end = start.saturating_add(SIGNATURE_OFFSETS_SERIALIZED_SIZE);

        let offsets: SecpSignatureOffsets = bincode::deserialize(&data[start..end])
            .map_err(|_| PrecompileError::InvalidSignature)?;

        // Parse out signature
        let signature_index = offsets.signature_instruction_index as usize;
        if signature_index >= instruction_datas.len() {
            return Err(PrecompileError::InvalidInstructionDataSize);
        }
        let signature_instruction = instruction_datas[signature_index];
        let sig_start = offsets.signature_offset as usize;
        let sig_end = sig_start.saturating_add(SIGNATURE_SERIALIZED_SIZE);
        if sig_end >= signature_instruction.len() {
            return Err(PrecompileError::InvalidSignature);
        }

        let signature = libsecp256k1::Signature::parse_standard_slice(
            &signature_instruction[sig_start..sig_end],
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;

        let recovery_id = libsecp256k1::RecoveryId::parse(signature_instruction[sig_end])
            .map_err(|_| PrecompileError::InvalidRecoveryId)?;

        // Parse out pubkey
        let eth_address_slice = get_data_slice(
            instruction_datas,
            offsets.eth_address_instruction_index,
            offsets.eth_address_offset,
            HASHED_PUBKEY_SERIALIZED_SIZE,
        )?;

        // Parse out message
        let message_slice = get_data_slice(
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

        let message_hash: [u8; 32] = solana_keccak_hasher::hash(message_slice).to_bytes();
        let pubkey = libsecp256k1::recover(
            &libsecp256k1::Message::parse_slice(&message_hash).unwrap(),
            &signature,
            &recovery_id,
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;
        let eth_address = eth_address_from_pubkey(&pubkey.serialize()[1..].try_into().unwrap());

        if eth_address_slice != eth_address {
            return Err(PrecompileError::InvalidSignature);
        }
    }
    Ok(())
```
