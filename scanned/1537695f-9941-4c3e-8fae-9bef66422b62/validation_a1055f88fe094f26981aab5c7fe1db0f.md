## Title
`secp256r1` precompile missing from `BUILTIN_INSTRUCTION_COSTS`, causing default compute-unit misclassification - (File: `builtins-default-costs/src/lib.rs`)

## Summary
`agave-precompiles` treats `secp256k1_program`, `ed25519_program`, and `secp256r1_program` as the three canonical precompiles [1](#0-0) , and `reserved-account-keys` likewise reserves all three [2](#0-1) . However, `solana-builtins-default-costs`, which is used to classify program IDs as "builtin" for the purpose of computing a transaction's *default* compute-unit limit, only lists `secp256k1_program` and `ed25519_program` — `secp256r1_program` is absent from both `MIGRATING_BUILTINS_COSTS` and `NON_MIGRATING_BUILTINS_COSTS` [3](#0-2) . This is structurally the same defect as the ZKsync report: a secondary "is this a known privileged program" table wasn't kept in sync when a newer precompile was added, so lookups against it silently misclassify the newer entry.

## Finding Description
`get_builtin_migration_feature_index()` and the derived `MAYBE_BUILTIN_KEY` table are built exclusively from `BUILTIN_INSTRUCTION_COSTS` (the union of `MIGRATING_BUILTINS_COSTS` and `NON_MIGRATING_BUILTINS_COSTS`) [4](#0-3) . Since `secp256r1_program::id()` is not a key in that map, `get_builtin_migration_feature_index(&secp256r1_program::id())` always returns `BuiltinMigrationFeatureIndex::NotBuiltin` [5](#0-4) , independent of the `MAYBE_BUILTIN_KEY` fast-path.

`BuiltinProgramsFilter::check_program_kind`, used by `ComputeBudgetInstructionDetails::try_from` to compute the *implicit* per-transaction compute-unit limit when the transaction doesn't request one explicitly, therefore classifies `secp256r1_program` instructions as `ProgramKind::NotBuiltin` rather than `ProgramKind::Builtin` [6](#0-5) .

In `calculate_default_compute_unit_limit`, instructions classified as `Builtin`/`MigratingBuiltin-not-yet-migrated` accumulate into `num_non_migratable_builtin_instructions`, each contributing `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` to the default limit, while instructions classified `NotBuiltin` accumulate into `num_non_builtin_instructions`, each contributing the much larger `DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT` [7](#0-6) . Because `secp256r1_program` is misrouted into the `NotBuiltin` bucket, every top-level `secp256r1_program::verify` instruction in a transaction without an explicit `ComputeBudgetInstruction::SetComputeUnitLimit` inflates the transaction's computed default compute-unit budget by the full BPF-program default instead of the small, fixed builtin allocation — even though the actual verify cost is fixed and known (it is a precompile, executed outside the BPF VM at a small fixed cost).

This is the direct analog of the ZKsync `CURRENT_MAX_PRECOMPILE_ADDRESS` bug: a newly introduced precompile (`secp256r1`, added after `secp256k1`/`ed25519`) was not added to the auxiliary classification table (`BUILTIN_INSTRUCTION_COSTS`) that a separate code path relies on to decide "is this address privileged/known", producing a wrong classification result for an unprivileged, ordinary caller (anyone who submits a transaction containing a `secp256r1_program` instruction).

## Impact Explanation
An unprivileged user constructing an ordinary transaction that calls `secp256r1_program` (e.g. for a `Secp256r1SigVerify` instruction) without setting an explicit compute-unit limit gets a default compute-unit reservation computed as if that instruction were an arbitrary BPF instruction (`DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT`), rather than the intended small fixed builtin allocation (`MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT`). Because this default limit feeds directly into transaction cost accounting used for block-packing/scheduling, an attacker can cheaply construct transactions consisting solely (or mostly) of `secp256r1_program` verify instructions to make the runtime believe each instruction "occupies" a much larger slice of compute-unit budget than it actually needs. This lets a remote, unprivileged sender skew per-transaction/block compute-unit accounting relative to real execution cost, which can be leveraged to reserve disproportionate block compute space cheaply — a resource-exhaustion vector against block packing/throughput driven entirely by ordinary transaction submission (no malicious peer/validator assumption required).

## Likelihood Explanation
The condition is deterministic and always triggered, since `secp256r1_program::id()` is a fixed, publicly known program ID and it is unconditionally absent from `NON_MIGRATING_BUILTINS_COSTS`/`MIGRATING_BUILTINS_COSTS` [3](#0-2) . Any unprivileged actor sending a legacy-style transaction that omits `SetComputeUnitLimit` and calls the `secp256r1` precompile reliably hits the mis-costed path; no race condition or special network state is needed.

## Recommendation
Add `secp256r1_program::id()` to `NON_MIGRATING_BUILTINS_COSTS` (or `MIGRATING_BUILTINS_COSTS` if a Core BPF migration is planned) in `builtins-default-costs/src/lib.rs`, mirroring its treatment in `agave-precompiles` and `reserved-account-keys`. Note the crate's own comments flag `BUILTIN_INSTRUCTION_COSTS` as consensus-sensitive ("DO NOT ADD MORE ENTRIES TO THIS MAP" / "modifying it can modify consensus"), so this change would need to go through the same feature-gated process used for other builtin-cost changes rather than a direct edit. More generally, the derived lookup tables (`MAYBE_BUILTIN_KEY`, `BUILTIN_INSTRUCTION_COSTS`) should be generated from (or asserted against) the single canonical precompile/builtin list in `agave-precompiles`/`solana-builtins` at compile time to prevent future additions from silently diverging.

## Proof of Concept
1. Build a transaction whose only instruction is a `secp256r1_program` "verify" instruction, with no `ComputeBudgetInstruction::SetComputeUnitLimit` instruction present.
2. Call `ComputeBudgetInstructionDetails::try_from(...)` on the instruction iterator, as done by the runtime path [8](#0-7) .
3. Observe that `BuiltinProgramsFilter::get_program_kind` returns `ProgramKind::NotBuiltin` for the `secp256r1_program` ID (because `get_builtin_migration_feature_index` cannot find it in `BUILTIN_INSTRUCTION_COSTS`) [9](#0-8) .
4. Observe that `calculate_default_compute_unit_limit` therefore adds `DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT` (BPF-program default) instead of `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` (builtin default) for this instruction [7](#0-6) , confirming the misclassification versus the correctly-classified `secp256k1_program`/`ed25519_program` cases, which are present in `NON_MIGRATING_BUILTINS_COSTS` [10](#0-9) .

### Citations

**File:** precompiles/src/lib.rs (L54-72)
```rust
static PRECOMPILES: LazyLock<Vec<Precompile>> = LazyLock::new(|| {
    vec![
        Precompile::new(
            solana_sdk_ids::secp256k1_program::id(),
            None, // always enabled
            secp256k1::verify,
        ),
        Precompile::new(
            solana_sdk_ids::ed25519_program::id(),
            None, // always enabled
            ed25519::verify,
        ),
        Precompile::new(
            solana_sdk_ids::secp256r1_program::id(),
            None, // always enabled
            secp256r1::verify,
        ),
    ]
});
```

**File:** reserved-account-keys/src/lib.rs (L145-158)
```rust
            ReservedAccount::new_active(address_lookup_table::id()),
            ReservedAccount::new_active(bpf_loader::id()),
            ReservedAccount::new_active(bpf_loader_deprecated::id()),
            ReservedAccount::new_active(bpf_loader_upgradeable::id()),
            ReservedAccount::new_active(compute_budget::id()),
            ReservedAccount::new_active(config::id()),
            ReservedAccount::new_active(ed25519_program::id()),
            ReservedAccount::new_active(feature::id()),
            // "Loader V4" must remain a reserved account key, since it cannot
            // be removed without breaking consensus.
            // We will use this address eventually.
            ReservedAccount::new_active(loader_v4::id()),
            ReservedAccount::new_active(secp256k1_program::id()),
            ReservedAccount::new_active(secp256r1_program::id()),
```

**File:** builtins-default-costs/src/lib.rs (L70-132)
```rust
static BUILTIN_INSTRUCTION_COSTS: std::sync::LazyLock<AHashMap<Pubkey, BuiltinCost>> =
    std::sync::LazyLock::new(|| {
        MIGRATING_BUILTINS_COSTS
            .iter()
            .chain(NON_MIGRATING_BUILTINS_COSTS.iter())
            .cloned()
            .collect()
    });
// DO NOT ADD MORE ENTRIES TO THIS MAP

/// DEVELOPER WARNING: please do not add new entry into MIGRATING_BUILTINS_COSTS or
/// NON_MIGRATING_BUILTINS_COSTS, do so will modify BUILTIN_INSTRUCTION_COSTS therefore
/// cause consensus failure. However, when a builtin started being migrated to core bpf,
/// it MUST be moved from NON_MIGRATING_BUILTINS_COSTS to MIGRATING_BUILTINS_COSTS, then
/// correctly furnishing `core_bpf_migration_feature`.
///
#[cfg(test)]
const TOTAL_COUNT_BUILTINS: usize = 9;
#[cfg(test)]
static_assertions::const_assert_eq!(
    MIGRATING_BUILTINS_COSTS.len() + NON_MIGRATING_BUILTINS_COSTS.len(),
    TOTAL_COUNT_BUILTINS
);

pub const MIGRATING_BUILTINS_COSTS: &[(Pubkey, BuiltinCost)] = &[
    // The Vote program is NOT migrating to on-chain BPF.
    // However, SIMD-0387 states that the Vote program will be removed from
    // builtin program cost modeling, so we use the same mechanism to evict
    // it from the list.
    (
        vote::id(),
        BuiltinCost::Migrating(MigratingBuiltinCost {
            core_bpf_migration_feature: bls_pubkey_management_in_vote_account::id(),
            position: 0,
        }),
    ),
];

const NON_MIGRATING_BUILTINS_COSTS: &[(Pubkey, BuiltinCost)] = &[
    (system_program::id(), BuiltinCost::NotMigrating),
    (compute_budget::id(), BuiltinCost::NotMigrating),
    (bpf_loader_upgradeable::id(), BuiltinCost::NotMigrating),
    (bpf_loader_deprecated::id(), BuiltinCost::NotMigrating),
    (bpf_loader::id(), BuiltinCost::NotMigrating),
    // We're going to need a feature gate to "fake migrate" Loader V4 to BPF,
    // whenever we deploy the program on-chain. The builtin shouldn't have been
    // added here without a feature gate.
    (loader_v4::id(), BuiltinCost::NotMigrating),
    (secp256k1_program::id(), BuiltinCost::NotMigrating),
    (ed25519_program::id(), BuiltinCost::NotMigrating),
];

/// A table of 256 booleans indicates whether the first `u8` of a Pubkey exists in
/// BUILTIN_INSTRUCTION_COSTS. If the value is true, the Pubkey might be a builtin key;
/// if false, it cannot be a builtin key. This table allows for quick filtering of
/// builtin program IDs without the need for hashing.
pub static MAYBE_BUILTIN_KEY: std::sync::LazyLock<[bool; 256]> = std::sync::LazyLock::new(|| {
    let mut temp_table: [bool; 256] = [false; 256];
    BUILTIN_INSTRUCTION_COSTS
        .keys()
        .for_each(|key| temp_table[key.as_ref()[0] as usize] = true);
    temp_table
});
```

**File:** builtins-default-costs/src/lib.rs (L140-150)
```rust
pub fn get_builtin_migration_feature_index(program_id: &Pubkey) -> BuiltinMigrationFeatureIndex {
    BUILTIN_INSTRUCTION_COSTS.get(program_id).map_or(
        BuiltinMigrationFeatureIndex::NotBuiltin,
        |builtin_cost| {
            builtin_cost.position().map_or(
                BuiltinMigrationFeatureIndex::BuiltinNoMigrationFeature,
                BuiltinMigrationFeatureIndex::BuiltinWithMigrationFeature,
            )
        },
    )
}
```

**File:** compute-budget-instruction/src/builtin_programs_filter.rs (L37-60)
```rust
    pub(crate) fn get_program_kind(&mut self, index: usize, program_id: &Pubkey) -> ProgramKind {
        *self
            .program_kind
            .get_mut(index)
            .expect("program id index is sanitized")
            .get_or_insert_with(|| Self::check_program_kind(program_id))
    }

    #[inline]
    fn check_program_kind(program_id: &Pubkey) -> ProgramKind {
        if !MAYBE_BUILTIN_KEY[program_id.as_ref()[0] as usize] {
            return ProgramKind::NotBuiltin;
        }

        match get_builtin_migration_feature_index(program_id) {
            BuiltinMigrationFeatureIndex::NotBuiltin => ProgramKind::NotBuiltin,
            BuiltinMigrationFeatureIndex::BuiltinNoMigrationFeature => ProgramKind::Builtin,
            BuiltinMigrationFeatureIndex::BuiltinWithMigrationFeature(
                core_bpf_migration_feature_index,
            ) => ProgramKind::MigratingBuiltin {
                core_bpf_migration_feature_index,
            },
        }
    }
```

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L53-96)
```rust
impl ComputeBudgetInstructionDetails {
    pub fn try_from<'a>(
        instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)> + Clone,
    ) -> Result<Self> {
        let mut filter = ComputeBudgetProgramIdFilter::new();
        let mut compute_budget_instruction_details = ComputeBudgetInstructionDetails::default();

        for (i, (program_id, instruction)) in instructions.clone().enumerate() {
            if filter.is_compute_budget_program(instruction.program_id_index as usize, program_id) {
                compute_budget_instruction_details.process_instruction(i as u8, &instruction)?;
            } else {
                compute_budget_instruction_details.num_non_compute_budget_instructions += 1;
            }
        }

        if compute_budget_instruction_details
            .requested_compute_unit_limit
            .is_none()
        {
            let mut filter = BuiltinProgramsFilter::new();
            // reiterate to collect builtin details
            for (program_id, instruction) in instructions {
                match filter.get_program_kind(instruction.program_id_index as usize, program_id) {
                    ProgramKind::Builtin => {
                        compute_budget_instruction_details
                            .num_non_migratable_builtin_instructions += 1;
                    }
                    ProgramKind::NotBuiltin => {
                        compute_budget_instruction_details.num_non_builtin_instructions += 1;
                    }
                    ProgramKind::MigratingBuiltin {
                        core_bpf_migration_feature_index,
                    } => {
                        *compute_budget_instruction_details
                            .migrating_builtin_feature_counters
                            .migrating_builtin
                            .get_mut(core_bpf_migration_feature_index)
                            .expect(
                                "migrating feature index within range of MIGRATION_FEATURE_IDS",
                            ) += 1;
                    }
                }
            }
        }
```

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L196-219)
```rust
    fn calculate_default_compute_unit_limit(&self, feature_set: &FeatureSet) -> u32 {
        // evaluate if any builtin has migrated with feature_set
        let (num_migrated, num_not_migrated) = self
            .migrating_builtin_feature_counters
            .migrating_builtin
            .iter()
            .enumerate()
            .fold((0, 0), |(migrated, not_migrated), (index, count)| {
                if count.0 > 0 && feature_set.is_active(get_migration_feature_id(index)) {
                    (migrated + count.0, not_migrated)
                } else {
                    (migrated, not_migrated + count.0)
                }
            });

        u32::from(self.num_non_migratable_builtin_instructions.0)
            .saturating_add(u32::from(num_not_migrated))
            .saturating_mul(MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT)
            .saturating_add(
                u32::from(self.num_non_builtin_instructions.0)
                    .saturating_add(u32::from(num_migrated))
                    .saturating_mul(DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT),
            )
    }
```
