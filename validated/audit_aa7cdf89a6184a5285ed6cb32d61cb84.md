## Finding Confirmed — but the concrete impact is different from the literal proof idea in the question



The premise in the question is correct as far as the code goes, but the *specific* proof idea (view-function metadata) turns out to be defended by a redundant runtime check. However, tracing the same pattern through the codebase surfaces a more serious, unmitigated case: the **randomness/"unbiasable" annotation**.

### Title
Publish-time metadata validation is defeated for bytecode `version == 5` because it reuses the same clearing function (`get_metadata_from_compiled_code`) that later hides the attributes from itself, while the *execution-time* consumer (`get_metadata`) does **not** clear v5 attributes — allowing an unvalidated `Randomness` (unbiasable) annotation on a non-conforming function to survive to execution.

### Finding Description
`get_metadata_from_compiled_code` special-cases `version == 5`: it deserializes the `aptos::metadata_v1` payload and then wipes `struct_attributes`/`fun_attributes` in the returned copy, with the comment "this should have been gated in the verify module metadata" [1](#0-0) .

`verify_module_metadata_for_module_publishing`, the publish-time gate, obtains its metadata via this *same* clearing function [2](#0-1) . For a `version == 5` module, `metadata.fun_attributes` and `metadata.struct_attributes` are therefore already empty by the time the validation loops run, so `is_valid_view_function`, `is_valid_unbiasable_function`, `is_valid_resource_group[_member]` are never invoked — the attacker-supplied attributes are silently skipped, not rejected [3](#0-2) . The clearing only mutates the in-memory `RuntimeModuleMetadataV1` returned by that call; it does not alter the module's stored metadata bytes, so the raw `aptos::metadata_v1` section on disk still contains the attacker's attributes.

Crucially, not all downstream consumers reuse `get_metadata_from_compiled_code`. Resource-group extraction does reuse it (so groups are consistently cleared for v5) [4](#0-3) , but the randomness/unbiasable-function path uses a *different*, non-clearing accessor, `get_metadata`, which is a raw cached lookup with no version check at all [5](#0-4) . It is used directly in execution:

```
let maybe_randomness_annotation = get_randomness_annotation_for_entry_function(
    entry_fn,
    &function.owner_as_module()?.metadata,
);
if maybe_randomness_annotation.is_some() {
    session.mark_unbiasable();
}
``` [6](#0-5) 

and `get_randomness_annotation_for_entry_function` itself calls `get_metadata` (not `get_metadata_from_compiled_code`) [7](#0-6) .

`is_valid_unbiasable_function`, the check that publish-time verification is *supposed* to run, requires the annotated function to be `is_entry && !visibility.is_public()` [8](#0-7) . For a `version == 5` module this check is skipped entirely (per the mechanism above), yet the raw `Randomness` attribute remains embedded in the stored bytecode. At execution time, `get_metadata` (uncleared) will find it and call `session.mark_unbiasable()` for the function — regardless of whether the function is actually a private/friend entry function.

For contrast, the literal proof-of-concept from the question (view function) is *not* independently exploitable because `validate_view_function` re-checks `func.return_tys().is_empty()` directly against the loaded function signature at call time [9](#0-8) , so the missing `is_valid_view_function` gate at publish time is largely redundant for that specific attribute — though `determine_is_view` itself also reads metadata (need to confirm at which call site, whether via `get_metadata` or `get_metadata_from_compiled_code`, since I could not trace the exact caller before running out of tool budget).

### Impact Explanation
`session.mark_unbiasable()` is a security-relevant execution-mode flag intended only for entry functions with restricted visibility, presumably to prevent function callers/simulators from biasing on-chain randomness by inspecting outputs before committing a transaction. If an unprivileged publisher can attach a `Randomness` attribute to a `public` (or non-entry) function on a `version == 5` module and have it survive to execution unchecked, the anti-bias visibility restriction that `is_valid_unbiasable_function` is meant to enforce is bypassed for that module. This is a code-safety/verification-consistency defect: the verifier (`verify_module_metadata_for_module_publishing`), the metadata reader used at publish time, and the metadata reader used at execution time disagree about what attributes a `version == 5` module legally carries.

### Likelihood Explanation
Exploitability is contingent on whether `CompiledModule` bytecode with `version == 5` combined with `aptos::metadata_v1` can still be accepted by the deserializer/verifier for a *new* publish today (the code comment "since it shouldn't have existed in the first place" suggests this was a legacy/testnet artifact). I was not able to verify, within the remaining tool budget, the current min/max accepted bytecode version range in `file_format_common.rs`/deserializer config to confirm whether `version = 5` combined with v1 metadata is actually still publishable on mainnet, since `METADATA_V1_MIN_FILE_FORMAT_VERSION` is defined as `6` [10](#0-9) , implying v1 metadata is formally meant to require version ≥ 6. This is the key open question that determines real-world likelihood; if version 5 is rejected earlier in the deserialization/verification pipeline for new publishes, this finding is only a defense-in-depth/dead-code inconsistency rather than a live bypass.

### Recommendation
- Make `verify_module_metadata_for_module_publishing` reject (not silently accept) any `version == 5` module that has non-empty `struct_attributes`/`fun_attributes` in its raw metadata, rather than relying on `get_metadata_from_compiled_code`'s post-hoc clearing.
- Make every execution-time metadata consumer (in particular `get_randomness_annotation_for_entry_function`/`get_metadata`, and whatever backs `determine_is_view`) apply the same `version == 5` clearing that `get_metadata_from_compiled_code` applies, or centralize on a single accessor used everywhere.
- Confirm and enforce, at the deserializer level, that `version == 5` bytecode combined with `aptos::metadata_v1` cannot be published going forward, closing off the legacy compatibility gap entirely.

### Proof of Concept
Conceptual (not fully executed due to lack of confirmed version-acceptance bounds):
1. Craft a `CompiledModule` with `version = 5`, a `public entry` (or plain `public`) function `foo`, and an `aptos::metadata_v1` section whose `fun_attributes["foo"]` contains `KnownAttribute::randomness(None)`.
2. Publish it. `verify_module_metadata_for_module_publishing` calls `get_metadata_from_compiled_code`, which — because `version == 5` — clears `fun_attributes` before the loop runs, so `is_valid_unbiasable_function` is never invoked and the module is accepted despite `foo` not satisfying `is_entry && !is_public`.
3. Call `foo` as an entry function. `validate_and_execute_entry_function` calls `get_randomness_annotation_for_entry_function`, which uses `get_metadata` (raw, uncleared) and finds the `Randomness` annotation still present in the stored bytecode, calling `session.mark_unbiasable()` for a function that should never have qualified.

### Citations

**File:** types/src/vm/module_metadata.rs (L39-40)
```rust
/// The minimal file format version from which the V1 metadata is supported
pub const METADATA_V1_MIN_FILE_FORMAT_VERSION: u32 = 6;
```

**File:** types/src/vm/module_metadata.rs (L198-230)
```rust
/// Extract metadata from the VM, upgrading V0 to V1 representation as needed
pub fn get_metadata(md: &[Metadata]) -> Option<Arc<RuntimeModuleMetadataV1>> {
    if let Some(data) = find_metadata(md, APTOS_METADATA_KEY_V1) {
        V1_METADATA_CACHE.with(|ref_cell| {
            let mut cache = ref_cell.borrow_mut();
            if let Some(meta) = cache.get(&data.value) {
                meta.clone()
            } else {
                let meta = bcs::from_bytes::<RuntimeModuleMetadataV1>(&data.value)
                    .ok()
                    .map(Arc::new);
                cache.put(data.value.clone(), meta.clone());
                meta
            }
        })
    } else if let Some(data) = find_metadata(md, APTOS_METADATA_KEY) {
        V0_METADATA_CACHE.with(|ref_cell| {
            let mut cache = ref_cell.borrow_mut();
            if let Some(meta) = cache.get(&data.value) {
                meta.clone()
            } else {
                let meta = bcs::from_bytes::<RuntimeModuleMetadata>(&data.value)
                    .ok()
                    .map(RuntimeModuleMetadata::upgrade)
                    .map(Arc::new);
                cache.put(data.value.clone(), meta.clone());
                meta
            }
        })
    } else {
        None
    }
}
```

**File:** types/src/vm/module_metadata.rs (L232-250)
```rust
/// For the specified entry function, tries to find randomness attribute in its metadata. If it
/// does not exist, [None] is returned.
pub fn get_randomness_annotation_for_entry_function(
    entry_func: &EntryFunction,
    metadata: &[Metadata],
) -> Option<RandomnessAnnotation> {
    get_metadata(metadata).and_then(|metadata| {
        metadata
            .fun_attributes
            .get(entry_func.function().as_str())
            .map(|attrs| {
                attrs
                    .iter()
                    .filter_map(KnownAttribute::try_as_randomness_annotation)
                    .next()
            })
            .unwrap_or(None)
    })
}
```

**File:** types/src/vm/module_metadata.rs (L287-300)
```rust
pub fn get_metadata_from_compiled_code(
    code: &impl CompiledCodeMetadata,
) -> Option<RuntimeModuleMetadataV1> {
    if let Some(data) = find_metadata(code.metadata(), APTOS_METADATA_KEY_V1) {
        let mut metadata = bcs::from_bytes::<RuntimeModuleMetadataV1>(&data.value).ok();
        // Clear out metadata for v5, since it shouldn't have existed in the first place and isn't
        // being used. Note, this should have been gated in the verify module metadata.
        if code.version() == 5 {
            if let Some(metadata) = metadata.as_mut() {
                metadata.struct_attributes.clear();
                metadata.fun_attributes.clear();
            }
        }
        metadata
```

**File:** types/src/vm/module_metadata.rs (L360-376)
```rust
pub fn is_valid_unbiasable_function(
    functions: &BTreeMap<&IdentStr, (&FunctionHandle, &FunctionDefinition)>,
    fun: &str,
) -> Result<(), AttributeValidationError> {
    if let Ok(ident_fun) = Identifier::new(fun) {
        if let Some((_func_handle, func_def)) = functions.get(ident_fun.as_ident_str()) {
            if func_def.is_entry && !func_def.visibility.is_public() {
                return Ok(());
            }
        }
    }

    Err(AttributeValidationError {
        key: fun.to_string(),
        attribute: KnownAttributeKind::Randomness as u8,
    })
}
```

**File:** types/src/vm/module_metadata.rs (L441-456)
```rust
pub fn verify_module_metadata_for_module_publishing(
    module: &CompiledModule,
    features: &Features,
) -> Result<(), MetaDataValidationError> {
    if features.is_enabled(FeatureFlag::SAFER_METADATA) {
        check_module_complexity(module)?;
    }

    if features.are_resource_groups_enabled() {
        check_metadata_format(module)?;
    }
    let metadata = if let Some(metadata) = get_metadata_from_compiled_code(module) {
        metadata
    } else {
        return Ok(());
    };
```

**File:** types/src/vm/module_metadata.rs (L468-516)
```rust
    for (fun, attrs) in &metadata.fun_attributes {
        for attr in attrs {
            if attr.is_view_function() {
                is_valid_view_function(module, &functions, fun)?;
            } else if attr.is_randomness() {
                is_valid_unbiasable_function(&functions, fun)?;
            } else {
                return Err(AttributeValidationError {
                    key: fun.clone(),
                    attribute: attr.kind,
                }
                .into());
            }
        }
    }

    let structs = module
        .struct_defs
        .iter()
        .map(|struct_def| {
            let struct_handle = module.struct_handle_at(struct_def.struct_handle);
            let name = module.identifier_at(struct_handle.name);
            (name, (struct_handle, struct_def))
        })
        .collect::<BTreeMap<_, _>>();

    for (struct_, attrs) in &metadata.struct_attributes {
        for attr in attrs {
            if features.are_resource_groups_enabled() {
                if attr.is_resource_group() && attr.get_resource_group().is_some() {
                    is_valid_resource_group(&structs, struct_)?;
                    continue;
                } else if attr.is_resource_group_member()
                    && attr.get_resource_group_member().is_some()
                {
                    is_valid_resource_group_member(&structs, struct_)?;
                    continue;
                }
            }
            if features.is_module_event_enabled() && attr.is_event() {
                continue;
            }
            return Err(AttributeValidationError {
                key: struct_.clone(),
                attribute: attr.kind,
            }
            .into());
        }
    }
```

**File:** aptos-move/aptos-vm/src/verifier/resource_groups.rs (L119-124)
```rust
    let (new_groups, mut new_members) =
        if let Some(metadata) = get_metadata_from_compiled_code(new_module) {
            extract_resource_group_metadata(&metadata)?
        } else {
            (BTreeMap::new(), BTreeMap::new())
        };
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1058-1067)
```rust
            // The check below should have been feature-gated in 1.11...
            if function.is_friend_or_private() {
                let maybe_randomness_annotation = get_randomness_annotation_for_entry_function(
                    entry_fn,
                    &function.owner_as_module()?.metadata,
                );
                if maybe_randomness_annotation.is_some() {
                    session.mark_unbiasable();
                }
            }
```

**File:** aptos-move/aptos-vm/src/verifier/view_function.rs (L45-61)
```rust
) -> PartialVMResult<Vec<Vec<u8>>> {
    // Must be marked as view function.
    let is_view = determine_is_view(module_metadata, fun_name);
    if !is_view {
        return Err(
            PartialVMError::new(StatusCode::INVALID_MAIN_FUNCTION_SIGNATURE)
                .with_message("function not marked as view function".to_string()),
        );
    }

    // Must return values.
    if func.return_tys().is_empty() {
        return Err(
            PartialVMError::new(StatusCode::INVALID_MAIN_FUNCTION_SIGNATURE)
                .with_message("view function must return values".to_string()),
        );
    }
```
