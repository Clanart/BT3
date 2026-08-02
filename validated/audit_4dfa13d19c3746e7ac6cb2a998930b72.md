[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-164)
```text
    public fun publish_package(owner: &signer, pack: PackageMetadata, code: vector<vector<u8>>) acquires PackageRegistry {
        // Disallow incompatible upgrade mode. Governance can decide later if this should be reconsidered.
        assert!(
            pack.upgrade_policy.policy > upgrade_policy_arbitrary().policy,
            error::invalid_argument(EINCOMPATIBLE_POLICY_DISABLED),
        );
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L233-254)
```text
    public fun freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) acquires PackageRegistry {
        let code_object_addr = code_object.object_address();
        assert!(exists<PackageRegistry>(code_object_addr), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));
        assert!(
            object::is_owner(code_object, signer::address_of(publisher)),
            error::permission_denied(ENOT_PACKAGE_OWNER)
        );

        let registry = borrow_global_mut<PackageRegistry>(code_object_addr);
        registry.packages.for_each_mut(|pack| {
            let package: &mut PackageMetadata = pack;
            package.upgrade_policy = upgrade_policy_immutable();
        });

        // We unfortunately have to make a copy of each package to avoid borrow checker issues as check_dependencies
        // needs to borrow PackageRegistry from the dependency packages.
        // This would increase the amount of gas used, but this is a rare operation and it's rare to have many packages
        // in a single code object.
        registry.packages.for_each(|pack| {
            check_dependencies(code_object_addr, &pack);
        });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L297-346)
```text
    /// Check that the upgrade policies of all packages are equal or higher quality than this package. Also
    /// compute the list of module dependencies which are allowed by the package metadata. The later
    /// is passed on to the native layer to verify that bytecode dependencies are actually what is pretended here.
    fun check_dependencies(publish_address: address, pack: &PackageMetadata): vector<AllowedDep>
    acquires PackageRegistry {
        let allowed_module_deps = vector::empty();
        let deps = &pack.deps;
        deps.for_each_ref(|dep| {
            let dep: &PackageDep = dep;
            assert!(exists<PackageRegistry>(dep.account), error::not_found(EPACKAGE_DEP_MISSING));
            if (is_policy_exempted_address(dep.account)) {
                // Allow all modules from this address, by using "" as a wildcard in the AllowedDep
                let account: address = dep.account;
                let module_name = string::utf8(b"");
                vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
            } else {
                let registry = borrow_global<PackageRegistry>(dep.account);
                let found = vector::any(&registry.packages, |dep_pack| {
                    let dep_pack: &PackageMetadata = dep_pack;
                    if (dep_pack.name == dep.package_name) {
                        // Check policy
                        assert!(
                            dep_pack.upgrade_policy.policy >= pack.upgrade_policy.policy,
                            error::invalid_argument(EDEP_WEAKER_POLICY)
                        );
                        if (dep_pack.upgrade_policy == upgrade_policy_arbitrary()) {
                            assert!(
                                dep.account == publish_address,
                                error::invalid_argument(EDEP_ARBITRARY_NOT_SAME_ADDRESS)
                            )
                        };
                        // Add allowed deps
                        let account = dep.account;
                        let k = 0;
                        let r = vector::length(&dep_pack.modules);
                        while (k < r) {
                            let module_name = vector::borrow(&dep_pack.modules, k).name;
                            vector::push_back(&mut allowed_module_deps, AllowedDep { account, module_name });
                            k += 1;
                        };
                        true
                    } else {
                        false
                    }
                });
                assert!(found, error::not_found(EPACKAGE_DEP_MISSING));
            };
        });
        allowed_module_deps
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L113-139)
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

    /// Make an existing upgradable package immutable. Once this is called, the package cannot be made upgradable again.
    /// Each `code_object` should only have one package, as one package is deployed per object in this module.
    /// Requires the `publisher` to be the owner of the `code_object`.
    public entry fun freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) {
        code::freeze_code_object(publisher, code_object);
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1602-1687)
```rust
        let PublishRequest {
            destination,
            bundle,
            expected_modules,
            allowed_deps,
            check_compat: _,
        } = maybe_publish_request.expect("Publish request exists");

        let modules = self.deserialize_module_bundle(&bundle)?;
        let modules: &Vec<CompiledModule> =
            traversal_context.referenced_module_bundles.alloc(modules);

        // Whether loading modules lazily, or as a full transitive closure, we always need to meter
        // 1) old versions of modules because they are used for compatibility checks, and 2) new
        // modules.
        if self.gas_feature_version() >= RELEASE_V1_10 {
            // Charge old versions of existing modules, in case of upgrades.
            for module in modules.iter() {
                let addr = module.self_addr();
                let name = module.self_name();

                if !traversal_context.visit_if_not_special_address(addr, name) {
                    continue;
                }

                let size_if_old_module_exists = module_storage
                    .unmetered_get_module_size(addr, name)?
                    .map(|v| v as u64);
                if let Some(old_size) = size_if_old_module_exists {
                    gas_meter
                        .charge_dependency(
                            DependencyKind::Existing,
                            addr,
                            name,
                            NumBytes::new(old_size),
                        )
                        .map_err(|err| {
                            err.finish(Location::Module(ModuleId::new(*addr, name.to_owned())))
                        })?;
                }
            }

            // Charge all modules in the bundle that is about to be published.
            for (module, blob) in modules.iter().zip(bundle.iter()) {
                let addr = module.self_addr();
                let name = module.self_name();
                gas_meter
                    .charge_dependency(
                        DependencyKind::New,
                        addr,
                        name,
                        NumBytes::new(blob.code().len() as u64),
                    )
                    .map_err(|err| err.finish(Location::Undefined))?;

                // In case of lazy loading: add all modules in a bundle as visited to avoid double
                // charging during module initialization.
                if self.features().is_lazy_loading_enabled() {
                    traversal_context.visit_if_not_special_address(addr, name);
                }
            }

            // Charge all immediate dependencies of a published package.
            self.charge_package_dependencies(
                module_storage,
                gas_meter,
                traversal_context,
                modules,
            )?;
        }

        for (module, blob) in modules.iter().zip(bundle.iter()) {
            // TODO(Gas): Make budget configurable.
            let budget = 2048 + blob.code().len() as u64 * 20;
            move_binary_format::check_complexity::check_module_complexity(module, budget)
                .map_err(|err| err.finish(Location::Undefined))?;
        }

        self.validate_publish_request(
            module_storage,
            traversal_context,
            gas_meter,
            modules,
            expected_modules,
            allowed_deps,
        )?;
```

**File:** aptos-move/cli/src/stored_package.rs (L193-241)
```rust
    pub fn verify(&self, package_metadata: &PackageMetadata) -> anyhow::Result<()> {
        let self_metadata = self.metadata;

        if self_metadata.name != package_metadata.name {
            bail!(
                "Package name doesn't match {} : {}",
                package_metadata.name,
                self_metadata.name
            )
        } else if self_metadata.deps != package_metadata.deps {
            bail!(
                "Dependencies don't match {:?} : {:?}",
                package_metadata.deps,
                self_metadata.deps
            )
        } else if self_metadata.modules != package_metadata.modules {
            bail!(
                "Modules don't match {:?} : {:?}",
                package_metadata.modules,
                self_metadata.modules
            )
        } else if self_metadata.manifest != package_metadata.manifest {
            bail!(
                "Manifest doesn't match {:?} : {:?}",
                package_metadata.manifest,
                self_metadata.manifest
            )
        } else if self_metadata.upgrade_policy != package_metadata.upgrade_policy {
            bail!(
                "Upgrade policy doesn't match {:?} : {:?}",
                package_metadata.upgrade_policy,
                self_metadata.upgrade_policy
            )
        } else if self_metadata.extension != package_metadata.extension {
            bail!(
                "Extensions doesn't match {:?} : {:?}",
                package_metadata.extension,
                self_metadata.extension
            )
        } else if self_metadata.source_digest != package_metadata.source_digest {
            bail!(
                "Source digests doesn't match {:?} : {:?}",
                package_metadata.source_digest,
                self_metadata.source_digest
            )
        }

        Ok(())
    }
```
