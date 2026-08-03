## Finding

Verified: `verify_module_metadata_for_module_publishing` in `types/src/vm/module_metadata.rs` is the runtime gate invoked whenever a module (raw bytecode, not Move source) is published on-chain. It does **not** perform the same validation that the Move compiler's extended checks (`aptos-move/framework/src/extended_checks.rs::check_and_record_resource_group_members`) do. [1](#0-0) 

`is_valid_resource_group_member` only checks that the *locally defined* struct named in the attribute key exists and has the `key` ability — it never inspects the `StructTag` returned by `attr.get_resource_group_member()`, i.e. it never checks that the referenced container module/struct exists, that it is actually marked `#[resource_group]`, or that the scope (`global`/`address`/`module_`) is satisfied: [2](#0-1) 

By contrast, the compile-time checker in `extended_checks.rs` does this cross-module validation (resolving the target module/struct, confirming it is a group with `self.get_resource_group(&container)`, and checking `scope.are_equal_envs`) — but this code path only runs during Move-source compilation (`run_extended_checks`), not during the on-chain publish flow that gates raw bytecode. [3](#0-2) 

`check_metadata_format` (gated behind `features.are_resource_groups_enabled()`) only validates that the metadata blob is well-formed BCS/known keys — it does not perform any attribute-semantic validation, so the “ordering” between it and `is_valid_resource_group_member` is not itself the source of the gap; the actual gap is that `is_valid_resource_group_member` never validates the referenced group at all. [4](#0-3) 

I also found `extract_resource_group_metadata_from_module` / `extract_resource_group_metadata` in `aptos-move/aptos-vm/src/verifier/resource_groups.rs`, which just extracts the `(struct → StructTag)` map from metadata without any existence/scope validation either. [5](#0-4) 

## What I could not fully confirm
I was unable to trace, within the tool budget, the exact runtime code path in `aptos-move/aptos-vm/src/data_cache.rs` that resolves a resource's group container `StructTag` into the actual storage key used for `move_to`/`borrow_global` at execution time. This is the piece needed to determine the concrete on-chain consequence of a forged `resource_group_member` attribute — whether it causes a runtime abort when the VM tries to resolve the (nonexistent or unrelated) group, or whether it actually succeeds and colocates the attacker's resource data into another group's storage blob at the depositor's own address. Given the index size limits and the remaining iteration budget, I could not pull the full relevant section of `data_cache.rs` (29 matches for `resource_group` in that file, but the read call did not return the surrounding logic).

## Assessment against decision standard
The gap in `is_valid_resource_group_member` — accepting an attacker-forged/unrelated group `StructTag` — is real and verifiable in the on-chain verifier code, and it is reachable via an unprivileged raw-bytecode module publish (bypassing the Move compiler's `extended_checks.rs`, which is not part of the on-chain verification path). This satisfies "unprivileged input changes what code can be published" for the *validation* step. However, I cannot confirm without further investigation (into `data_cache.rs` and the loader's group-resolution logic) whether this metadata mismatch actually corrupts another module's resource-group storage/layout at runtime, or is inert / caught elsewhere (e.g., as a runtime abort or as a compatibility-check rejection on subsequent upgrade). Given this residual uncertainty on concrete on-chain impact, I recommend this be escalated for deeper tracing into `data_cache.rs`'s resource-group storage-key resolution rather than declaring a conclusive Critical finding at this time.

### Citations

**File:** types/src/vm/module_metadata.rs (L253-283)
```rust
fn check_metadata_format(module: &CompiledModule) -> Result<(), MalformedError> {
    let mut exist = false;
    let mut compilation_key_exist = false;
    for data in module.metadata.iter() {
        if data.key == *APTOS_METADATA_KEY || data.key == *APTOS_METADATA_KEY_V1 {
            if exist {
                return Err(MalformedError::DuplicateKey);
            }
            exist = true;

            if data.key == *APTOS_METADATA_KEY {
                bcs::from_bytes::<RuntimeModuleMetadata>(&data.value)
                    .map_err(|e| MalformedError::DeserializedError(data.key.clone(), e))?;
            } else if data.key == *APTOS_METADATA_KEY_V1 {
                bcs::from_bytes::<RuntimeModuleMetadataV1>(&data.value)
                    .map_err(|e| MalformedError::DeserializedError(data.key.clone(), e))?;
            }
        } else if data.key == *COMPILATION_METADATA_KEY {
            if compilation_key_exist {
                return Err(MalformedError::DuplicateKey);
            }
            compilation_key_exist = true;
            bcs::from_bytes::<CompilationMetadata>(&data.value)
                .map_err(|e| MalformedError::DeserializedError(data.key.clone(), e))?;
        } else {
            return Err(MalformedError::UnknownKey(data.key.clone()));
        }
    }

    Ok(())
}
```

**File:** types/src/vm/module_metadata.rs (L423-439)
```rust
pub fn is_valid_resource_group_member(
    structs: &BTreeMap<&IdentStr, (&StructHandle, &StructDefinition)>,
    struct_: &str,
) -> Result<(), AttributeValidationError> {
    if let Ok(ident_struct) = Identifier::new(struct_) {
        if let Some((struct_handle, _struct_def)) = structs.get(ident_struct.as_ident_str()) {
            if struct_handle.abilities.has_ability(Ability::Key) {
                return Ok(());
            }
        }
    }

    Err(AttributeValidationError {
        key: struct_.to_string(),
        attribute: KnownAttributeKind::ViewFunction as u8,
    })
}
```

**File:** types/src/vm/module_metadata.rs (L494-516)
```rust
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

**File:** aptos-move/framework/src/extended_checks.rs (L400-453)
```rust
                let (module_name, container_name) =
                    if let AttributeValue::Name(_, Some(module), name) = value {
                        (module, name)
                    } else {
                        self.env.error(
                            &struct_.get_loc(),
                            "resource_group_member lacks 'group' parameter",
                        );
                        continue;
                    };

                let module = if let Some(module) = self.env.find_module(module_name) {
                    module
                } else {
                    self.env
                        .error(&struct_.get_loc(), "unable to find resource_group module");
                    continue;
                };

                let container = if let Some(container) = module.find_struct(*container_name) {
                    container
                } else {
                    self.env
                        .error(&struct_.get_loc(), "unable to find resource_group struct");
                    continue;
                };

                if let Some(scope) = self.get_resource_group(&container) {
                    if !scope.are_equal_envs(struct_, &container) {
                        self.env
                            .error(&struct_.get_loc(), "resource_group scope mismatch");
                        continue;
                    }

                    self.output
                        .entry(module_id.clone())
                        .or_default()
                        .struct_attributes
                        .entry(
                            self.env
                                .symbol_pool()
                                .string(struct_.get_name())
                                .to_string(),
                        )
                        .or_default()
                        .push(KnownAttribute::resource_group_member(
                            container.get_full_name_with_address(),
                        ));
                } else {
                    self.env.error(
                        &struct_.get_loc(),
                        "container is not a resource_group_container",
                    );
                }
```

**File:** aptos-move/aptos-vm/src/verifier/resource_groups.rs (L216-246)
```rust
pub(crate) fn extract_resource_group_metadata(
    metadata: &RuntimeModuleMetadataV1,
) -> VMResult<(
    BTreeMap<String, ResourceGroupScope>,
    BTreeMap<String, StructTag>,
)> {
    let mut groups = BTreeMap::new();
    let mut members = BTreeMap::new();
    for (struct_, attrs) in &metadata.struct_attributes {
        for attr in attrs {
            if attr.is_resource_group() {
                let group = attr
                    .get_resource_group()
                    .ok_or_else(|| metadata_validation_error("Invalid resource_group attribute"))?;
                let old = groups.insert(struct_.clone(), group);
                if old.is_some() {
                    metadata_validation_err("Found duplicate resource_group attribute")?;
                }
            } else if attr.is_resource_group_member() {
                let member = attr.get_resource_group_member().ok_or_else(|| {
                    metadata_validation_error("Invalid resource_group_member attribute")
                })?;
                let old = members.insert(struct_.clone(), member);
                if old.is_some() {
                    metadata_validation_err("Found duplicate resource_group_member attribute")?;
                }
            }
        }
    }
    Ok((groups, members))
}
```
