Based on my research, I found a strong structural analog to the ShashFactory issue in Agave's "simple vote transaction" classifier, which determines transaction treatment purely by call-shape probing rather than an authoritative check that the instruction is actually a vote instruction.

### Title
Vote-transaction classification relies on structural probing instead of authoritative instruction validation - ([File: perf/src/sigverify.rs])

### Summary
Several privilege- and accounting-relevant code paths decide whether a transaction is a "simple vote transaction" using a purely structural probe (signature count ≤ 2, legacy message, exactly one instruction, program id equals the vote program) instead of actually decoding the instruction payload to confirm it is a genuine vote instruction. This is the same anti-pattern flagged in the external report: classifying an entity by superficial "shape" probes rather than an authoritative discriminant, which silently accepts anything matching the shape as if it had the expected (trusted) behavior.

### Finding Description
The classifier `is_simple_vote_transaction_view` only checks structural properties of the transaction and never inspects whether the single instruction actually deserializes into a valid `VoteInstruction`: [1](#0-0) 

The same shape-only logic is reused for `is_simple_vote_transaction`/`is_simple_vote_transaction_impl` in the runtime-transaction crate to set `is_simple_vote_transaction` metadata used throughout the runtime: [2](#0-1) [3](#0-2) 

Notably, the codebase already contains the *correct* pattern elsewhere — `is_valid_vote_only_transaction` actually deserializes the instruction data as a `VoteInstruction` and requires `is_single_vote_state_update()` before treating the transaction as a real vote: [4](#0-3) 

But this authoritative check is *not* what is used to set the `is_simple_vote_transaction` flag consumed by sigverify's `reject_non_vote` gate and by the prioritization-fee cache. In `sigverify.rs`, the flag directly controls whether a packet with `reject_non_vote` set is allowed to bypass the non-vote rejection path: [5](#0-4) 

And in the runtime, the flag is used to exempt the transaction from prioritization-fee tracking entirely: [6](#0-5) 

An attacker fully controls all the fields being probed: they can sign a legacy message with ≤2 signatures, containing exactly one instruction whose `program_id` is the vote program, but whose instruction data is arbitrary garbage rather than a real `VoteInstruction`. The probe classifies this as `is_simple_vote_transaction = true` even though it is not a real vote and will fail during actual vote-program execution. This is structurally identical to the reported bug class: the checker infers "version"/"kind" from a shallow signature match (call probe) rather than validating the actual payload, so a crafted input with different real behavior (a non-vote instruction masquerading as a vote) is misclassified and inherits treatment reserved for the trusted category.

### Impact Explanation
Because the classification is used to grant vote-transaction treatment (bypassing `reject_non_vote` filtering intended to gate the TPU vote-only path, and exclusion from prioritization-fee/cost accounting that assumes vote transactions carry no meaningful priority fee), an attacker can inject non-vote-shaped-as-vote transactions into paths intended only for genuine consensus votes without needing any real vote content. This weakens the intended separation between vote traffic and general traffic and can be used to smuggle spam through vote-only ingestion paths and to skew fee/cost accounting that assumes "simple vote" transactions are cheap/fixed-cost, all without any privileged or malicious-validator assumption — it only requires an ordinary keypair able to sign a transaction.

### Likelihood Explanation
The crafting requirements (single instruction, vote program id, ≤2 signatures, legacy message, arbitrary data) are trivial to construct and require no special access; every field is client-controlled. The mismatch between the "correct" authoritative check (`is_valid_vote_only_transaction`, which decodes the actual `VoteInstruction`) already existing in the codebase and the shape-only check used by the sigverify/runtime metadata path shows this is a real inconsistency rather than a hypothetical one.

### Recommendation
Replace or augment the structural-shape probe (`is_simple_vote_transaction_view` / `is_simple_vote_transaction_impl`) with an authoritative check that actually deserializes the instruction data as a `VoteInstruction` (mirroring `is_valid_vote_only_transaction` in `vote/src/vote_parser.rs`) before granting "simple vote" treatment in sigverify's `reject_non_vote` gate and in the prioritization-fee/cost-accounting exclusion paths. This avoids relying on call-shape probing that can be satisfied by adversarial inputs with unrelated real behavior — directly analogous to whitelisting/validating actual gauge behavior instead of inferring version from probe calls.

### Proof of Concept
Conceptual PoC (not run, derived from local test helpers that already exercise this exact code path):
1. Build a legacy `Transaction` with exactly one instruction whose `program_id` is `solana_sdk_ids::vote::id()` and whose instruction data is arbitrary bytes (not a valid `bincode`-encoded `VoteInstruction`), signed by an attacker-controlled keypair (≤2 required signatures) — this mirrors the harness used in the repo's own test `test_is_simple_vote_transaction_paths`, which constructs `Instruction::new_with_bytes(solana_sdk_ids::vote::ID, &[], Vec::new())` and asserts it is classified as a simple vote: [7](#0-6) 
2. Submit the packet; `verify_packet`/`is_simple_vote_transaction_view` classifies it as `is_simple_vote_tx = true` purely from shape, without decoding the instruction, so `reject_non_vote` filtering does not discard it: [5](#0-4) 
3. Downstream, `RuntimeTransaction` metadata marks `is_simple_vote_transaction = true`, causing the transaction to be excluded from prioritization-fee cache accounting even though it is not a genuine vote: [6](#0-5) 

Note: I was not able to fully trace, within available time/tooling, the exact downstream cost-model arithmetic (`cost-model/src/cost_model.rs`) to quantify precisely how much cost underestimation this enables, so the magnitude of the fee/cost-accounting impact is not fully verified — this should be confirmed with a live/local build before treating the accounting-impact claim as conclusive. The sigverify `reject_non_vote` bypass and prioritization-fee exclusion, however, are directly supported by the cited code.

### Citations

**File:** perf/src/sigverify.rs (L20-63)
```rust
fn verify_packet(packet: &mut PacketRefMut, reject_non_vote: bool, enable_tx_v1: bool) -> bool {
    // If this packet was already marked as discard, drop it
    if packet.meta().discard() {
        return false;
    }

    let Some(data) = packet.data(..) else {
        return false;
    };

    let (is_simple_vote_tx, verified) = {
        let Ok(view) = SanitizedTransactionView::try_new_sanitized(data, &sanitize_config()) else {
            return false;
        };

        if !enable_tx_v1 && matches!(view.version(), TransactionVersion::V1) {
            return false;
        }

        let is_simple_vote_tx = is_simple_vote_transaction_view(&view);
        if reject_non_vote && !is_simple_vote_tx {
            (is_simple_vote_tx, false)
        } else {
            let signatures = view.signatures();
            if signatures.is_empty() {
                (is_simple_vote_tx, false)
            } else {
                let message = view.message_data();
                let static_account_keys = view.static_account_keys();
                let verified = signatures
                    .iter()
                    .zip(static_account_keys.iter())
                    .all(|(signature, pubkey)| signature.verify(pubkey.as_ref(), message));
                (is_simple_vote_tx, verified)
            }
        }
    };

    if is_simple_vote_tx {
        packet.meta_mut().flags |= PacketFlags::SIMPLE_VOTE_TX;
    }

    verified
}
```

**File:** perf/src/sigverify.rs (L76-106)
```rust
fn is_simple_vote_transaction_view<D: TransactionData>(view: &SanitizedTransactionView<D>) -> bool {
    // vote could have 1 or 2 sigs; zero sig has already been excluded by sanitization.
    if view.num_signatures() > 2 {
        return false;
    }

    // simple vote should only be legacy message
    if !matches!(view.version(), TransactionVersion::Legacy) {
        return false;
    }

    // skip if has more than 1 instruction
    if view.num_instructions() != 1 {
        return false;
    }

    let mut instructions = view.instructions_iter();
    let Some(instruction) = instructions.next() else {
        return false;
    };
    if instructions.next().is_some() {
        return false;
    }

    let program_id_index = usize::from(instruction.program_id_index);
    let Some(program_id) = view.static_account_keys().get(program_id_index) else {
        return false;
    };

    *program_id == solana_sdk_ids::vote::id()
}
```

**File:** runtime-transaction/src/runtime_transaction/transaction_view.rs (L34-44)
```rust
fn is_simple_vote_transaction<D: TransactionData>(
    transaction: &SanitizedTransactionView<D>,
) -> bool {
    let signatures = transaction.signatures();
    let is_legacy_message = matches!(transaction.version(), TransactionVersion::Legacy);
    let instruction_programs = transaction
        .program_instructions_iter()
        .map(|(program_id, _ix)| program_id);

    is_simple_vote_transaction_impl(signatures, is_legacy_message, instruction_programs)
}
```

**File:** runtime-transaction/src/runtime_transaction/transaction_view.rs (L68-121)
```rust
fn from_sanitized_transaction_view<D>(
    transaction: &SanitizedTransactionView<D>,
    message_hash: MessageHash,
    is_simple_vote_tx: Option<bool>,
) -> Result<CachedTransactionMeta>
where
    D: TransactionData,
{
    let message_hash = match message_hash {
        MessageHash::Precomputed(hash) => hash,
        MessageHash::Compute => VersionedMessage::hash_raw_message(transaction.message_data()),
    };
    let is_simple_vote_tx =
        is_simple_vote_tx.unwrap_or_else(|| is_simple_vote_transaction(transaction));

    let InstructionMeta {
        precompile_signature_details,
        instruction_data_len,
    } = InstructionMeta::try_new(transaction.program_instructions_iter())?;

    let signature_details = TransactionSignatureDetails::new(
        u64::from(transaction.num_required_signatures()),
        precompile_signature_details.num_secp256k1_instruction_signatures,
        precompile_signature_details.num_ed25519_instruction_signatures,
        precompile_signature_details.num_secp256r1_instruction_signatures,
    );
    let versioned_transaction_config =
        if let Some(transaction_config_view) = transaction.transaction_config() {
            // NOTE: only txv1 has `transaction_config_view`, which must have been validated for
            // SanitizedTransactionView.
            VersionedTransactionConfiguration::V1(TransactionConfiguration {
                priority_fee_lamports: transaction_config_view.priority_fee_lamports().unwrap_or(0),
                compute_unit_limit: transaction_config_view.compute_unit_limit().unwrap_or(0),
                loaded_accounts_data_size_limit: transaction_config_view
                    .loaded_accounts_data_size_limit()
                    .unwrap_or(0),
                updated_heap_bytes: transaction_config_view
                    .requested_heap_size()
                    .unwrap_or(HEAP_LENGTH as u32),
            })
        } else {
            VersionedTransactionConfiguration::LegacyAndV0(
                ComputeBudgetInstructionDetails::try_from(transaction.program_instructions_iter())?,
            )
        };

    Ok(CachedTransactionMeta {
        message_hash,
        is_simple_vote_transaction: is_simple_vote_tx,
        signature_details,
        versioned_transaction_config,
        instruction_data_len,
    })
}
```

**File:** vote/src/vote_parser.rs (L10-33)
```rust
/// Check if a transaction is a valid vote-only transaction.
/// A valid vote-only transaction must:
/// 1. Have exactly one instruction
/// 2. That instruction must be to the vote program
/// 3. That instruction must be a single vote state update (UpdateVoteState, TowerSync, etc.)
pub fn is_valid_vote_only_transaction(tx: &impl SVMTransaction) -> bool {
    let mut instructions = tx.program_instructions_iter();

    let Some((program_id, instruction)) = instructions.next() else {
        return false;
    };

    if instructions.next().is_some() {
        return false;
    }

    if !solana_sdk_ids::vote::check_id(program_id) {
        return false;
    }

    limited_deserialize::<VoteInstruction>(instruction.data, solana_packet::PACKET_DATA_SIZE as u64)
        .map(|ix| ix.is_single_vote_state_update())
        .unwrap_or(false)
}
```

**File:** runtime/src/prioritization_fee_cache.rs (L210-221)
```rust
    pub fn update<'a, Tx: TransactionWithMeta + 'a>(
        &self,
        bank: &Bank,
        txs: impl Iterator<Item = &'a Tx>,
    ) {
        let (_, send_updates_us) = measure_us!({
            for sanitized_transaction in txs {
                // Vote transactions are not prioritized, therefore they are excluded from
                // updating fee_cache.
                if sanitized_transaction.is_simple_vote_transaction() {
                    continue;
                }
```

**File:** core/src/completed_data_sets_service.rs (L418-434)
```rust
    #[test]
    fn test_is_simple_vote_transaction_paths() {
        let vote_instruction =
            Instruction::new_with_bytes(solana_sdk_ids::vote::ID, &[], Vec::new());
        let non_vote_instruction =
            Instruction::new_with_bytes(Pubkey::new_unique(), &[], Vec::new());

        assert!(is_simple_vote_transaction(&legacy_transaction(
            vote_instruction
        )));
        assert!(!is_simple_vote_transaction(&legacy_transaction(
            non_vote_instruction
        )));
        assert!(!is_simple_vote_transaction(&versioned_v0_transaction(
            Instruction::new_with_bytes(solana_sdk_ids::vote::ID, &[], Vec::new()),
        )));
    }
```
