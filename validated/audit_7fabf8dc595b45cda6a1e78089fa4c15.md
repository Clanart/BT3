This confirms `StateValueMetadata` is a purely accounting structure — it contains only `slot_deposit`, `bytes_deposit`, and `creation_time_usecs` fields used for storage-fee gas refund bookkeeping ( [1](#0-0) ). There is no `owner` or any ownership-related field encoded in it whatsoever.

## No vulnerability found for this question.

**Reasoning:**

1. **`StateValueMetadata` contains no ownership data.** The struct is defined purely as `{ slot_deposit, bytes_deposit, creation_time_usecs }` [1](#0-0) , with mutators limited to `set_slot_deposit`/`set_bytes_deposit` [2](#0-1) . There is no "owner" or "code object" field it could possibly "disagree" with — the premise that `metadata_mut()` could "encode a different owner-adjacent field" doesn't correspond to any real field in this type.

2. **Object-code ownership is tracked entirely in Move resource state, not in `StateValueMetadata`.** Ownership of a code object is determined by `ManagingRefs`/`ExtendRef` and the `object::is_owner` check on the `Object<PackageRegistry>` [3](#0-2) , and package identity/policy lives in `PackageRegistry`/`PackageMetadata` written by `code::publish_package` [4](#0-3) . These are Move-level resource writes verified and executed independently of the low-level storage-fee metadata annotation. Mutating `slot_deposit`/`bytes_deposit`/`creation_time_usecs` on a module's `WriteOp` cannot alter what `ManagingRefs` or `PackageRegistry` say, because those are separate BCS-serialized resource values written through entirely separate code paths in `object_code_deployment.move` and `code.move`.

3. **The storage-fee iteration is deliberately unified and type-agnostic by design.** `write_op_info_iter_mut` on `ModuleWriteSet` [5](#0-4)  and on `VMChangeSet` [6](#0-5)  both yield a `WriteOpInfo` whose `metadata_mut` is used exclusively by `charge_refund_write_op` in `space_pricing.rs` to set `slot_deposit`/refund the `total_deposit` [7](#0-6) . This code applies identically to module and resource writes and only touches deposit accounting fields — it has no code path that reads or writes anything resembling "ownership."

4. **The premise conflates two unrelated concerns.** The `slot_deposit`/`bytes_deposit`/`creation_time_usecs` metadata governs *gas refund accounting for storage slots* and is set from `StateValueMetadata::placeholder(&current_time)` at write-op-conversion time in `WriteOpConverter::new` [8](#0-7) , then finalized by the gas meter. This has zero bearing on module verification, bytecode compatibility, or code-object ownership, all of which are enforced through Move-level checks (`object::is_owner`, `check_upgradability`, backward-compatibility checks) that are entirely separate from the storage-fee metadata mutation path.

The question's proof idea (asserting the module `WriteOp`'s metadata "disagrees with" `ManagingRefs`/`PackageRegistry`) is not meaningful because the two data structures encode fundamentally unrelated information — there is no invariant that ties deposit/creation-time metadata to code ownership, so no "desync" of the kind described can exist. This does not meet the decision standard of demonstrating that unprivileged input changes what code can be published, upgraded, frozen, verified, or executed.

### Citations

**File:** types/src/state_store/state_value.rs (L46-51)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
struct StateValueMetadataInner {
    slot_deposit: u64,
    bytes_deposit: u64,
    creation_time_usecs: u64,
}
```

**File:** types/src/state_store/state_value.rs (L152-158)
```rust
    pub fn set_slot_deposit(&mut self, amount: u64) {
        self.expect_upgraded().slot_deposit = amount;
    }

    pub fn set_bytes_deposit(&mut self, amount: u64) {
        self.expect_upgraded().bytes_deposit = amount;
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L113-133)
```text
    public entry fun upgrade(
        publisher: &signer,
        metadata_serialized: vector<u8>,
        code: vector<vector<u8>>,
        code_object: Object<PackageRegistry>,
    ) {
        let publisher_address = signer::address_of(publisher);
        assert!(
            object::is_owner(code_object, publisher_address),
            error::permission_denied(ENOT_CODE_OBJECT_OWNER),
        );

        let code_object_address = code_object.object_address();
        assert!(exists<ManagingRefs>(code_object_address), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));

        let extend_ref = &borrow_global<ManagingRefs>(code_object_address).extend_ref;
        let code_signer = &extend_ref.generate_signer_for_extending();
        code::publish_package_txn(code_signer, metadata_serialized, code);

        event::emit(Upgrade { object_address: signer::address_of(code_signer), });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-231)
```text
    public fun publish_package(owner: &signer, pack: PackageMetadata, code: vector<vector<u8>>) acquires PackageRegistry {
        // Disallow incompatible upgrade mode. Governance can decide later if this should be reconsidered.
        assert!(
            pack.upgrade_policy.policy > upgrade_policy_arbitrary().policy,
            error::invalid_argument(EINCOMPATIBLE_POLICY_DISABLED),
        );

        let addr = signer::address_of(owner);
        if (!exists<PackageRegistry>(addr)) {
            move_to(owner, PackageRegistry { packages: vector::empty() })
        };

        // Checks for valid dependencies to other packages
        let allowed_deps = check_dependencies(addr, &pack);

        // Check package against conflicts
        // To avoid prover compiler error on spec
        // the package need to be an immutable variable
        let module_names = get_module_names(&pack);

        // Record, per module in this package, the object's transitive root owner at (re)publish, so
        // lazy self-init can detect a later transfer of the object or an ancestor since that module
        // was published (see `init::internal_maybe_initialize`). Objects only; feature-gated.
        if (features::is_lazy_module_initialization_enabled() && object::is_object(addr)) {
            let owner = object::address_to_object<object::ObjectCore>(addr).root_owner();
            module_names.for_each_ref(|name| {
                init::record_deploy_owner(addr, *name.bytes(), owner);
            });
        };
        let package_immutable = &borrow_global<PackageRegistry>(addr).packages;
        let len = package_immutable.length();
        let index = len;
        let upgrade_number = 0;
        package_immutable.enumerate_ref(|i, old| {
            let old: &PackageMetadata = old;
            if (old.name == pack.name) {
                upgrade_number = old.upgrade_number + 1;
                check_upgradability(old, &pack, &module_names);
                index = i;
            } else {
                check_coexistence(old, &module_names)
            };
        });

        // Assign the upgrade counter.
        pack.upgrade_number = upgrade_number;

        let packages = &mut borrow_global_mut<PackageRegistry>(addr).packages;
        // Update registry
        let policy = pack.upgrade_policy;
        if (index < len) {
            pack.modules.for_each_ref(|m| {
                let m: &ModuleMetadata = m;
                init::reset_initialized(addr, *m.name.bytes());
            });
            *packages.borrow_mut(index) = pack
        } else {
            packages.push_back(pack)
        };

        event::emit(PublishPackage {
            code_address: addr,
            is_upgrade: upgrade_number > 0
        });

        // Request publish
        if (features::code_dependency_check_enabled())
            request_publish_with_allowed_deps(addr, module_names, allowed_deps, code, policy.policy)
        else
        // The new `request_publish_with_allowed_deps` has not yet rolled out, so call downwards
        // compatible code.
            request_publish(addr, module_names, code, policy.policy)
    }
```

**File:** aptos-move/aptos-vm-types/src/module_write_set.rs (L100-123)
```rust
    pub fn write_op_info_iter_mut<'a>(
        &'a mut self,
        module_storage: &'a impl ModuleStorage,
    ) -> impl Iterator<Item = PartialVMResult<WriteOpInfo<'a>>> {
        self.writes.iter_mut().map(move |(key, write)| {
            // The unmetered access to module size is fine because:
            //
            // INVARIANT:
            //   If there is a write to the module at key K, it means the module at K has been read
            //   (in order to perform backward-compatibility checks) if it existed.
            //   If module at K previously did not exist, the read of previous size returns None.
            //   Because module with key K has been read, it must have been loaded and metered.
            let prev_size = module_storage
                .unmetered_get_module_size(write.module_address(), write.module_name())
                .map_err(|e| e.to_partial())?
                .unwrap_or(0) as u64;
            Ok(WriteOpInfo {
                key,
                op_size: write.write_op().write_op_size(),
                prev_size,
                metadata_mut: write.write_op_mut().metadata_mut(),
            })
        })
    }
```

**File:** aptos-move/aptos-vm-types/src/change_set.rs (L733-758)
```rust
    fn write_op_info_iter_mut<'a>(
        &'a mut self,
        executor_view: &'a dyn ExecutorView,
        _module_storage: &'a impl AptosModuleStorage,
        fix_prev_materialized_size: bool,
    ) -> impl Iterator<Item = PartialVMResult<WriteOpInfo<'a>>> {
        self.resource_write_set
            .iter_mut()
            .filter(|(_, op)| {
                // Legacy aggregator V1 deltas excluded. They were never part of
                // the write set iterator.
                !op.is_aggregator_v1_delta()
            })
            .map(move |(key, op)| {
                Ok(WriteOpInfo {
                    key,
                    op_size: op.materialized_size(),
                    prev_size: op.prev_materialized_size(
                        key,
                        executor_view,
                        fix_prev_materialized_size,
                    )?,
                    metadata_mut: op.metadata_mut(),
                })
            })
    }
```

**File:** aptos-move/aptos-vm-types/src/storage/space_pricing.rs (L117-151)
```rust
    fn charge_refund_write_op_v1(
        params: &TransactionGasParameters,
        op: WriteOpInfo,
    ) -> ChargeAndRefund {
        use WriteOpSize::*;

        match op.op_size {
            Creation { write_len } => {
                let slot_fee = params.legacy_storage_fee_per_state_slot_create * NumSlots::new(1);
                let bytes_fee = Self::discounted_write_op_size_for_v1(params, op.key, write_len)
                    * params.legacy_storage_fee_per_excess_state_byte;

                if !op.metadata_mut.is_none() {
                    op.metadata_mut.set_slot_deposit(slot_fee.into())
                }

                ChargeAndRefund {
                    charge: slot_fee + bytes_fee,
                    refund: 0.into(),
                }
            },
            Modification { write_len } => {
                let bytes_fee = Self::discounted_write_op_size_for_v1(params, op.key, write_len)
                    * params.legacy_storage_fee_per_excess_state_byte;

                ChargeAndRefund {
                    charge: bytes_fee,
                    refund: 0.into(),
                }
            },
            Deletion => ChargeAndRefund {
                charge: 0.into(),
                refund: op.metadata_mut.total_deposit().into(),
            },
        }
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/write_op_converter.rs (L36-55)
```rust
impl<'r> WriteOpConverter<'r> {
    pub(crate) fn new(
        remote: &'r dyn AptosMoveResolver,
        is_storage_slot_metadata_enabled: bool,
    ) -> Self {
        let mut new_slot_metadata: Option<StateValueMetadata> = None;
        if is_storage_slot_metadata_enabled {
            if let Some(current_time) = CurrentTimeMicroseconds::fetch_config(remote).ok().flatten()
            {
                // The deposit on the metadata is a placeholder (0), it will be updated later when
                // storage fee is charged.
                new_slot_metadata = Some(StateValueMetadata::placeholder(&current_time));
            }
        }

        Self {
            remote,
            new_slot_metadata,
        }
    }
```
