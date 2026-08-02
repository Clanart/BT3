[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/code.move (L159-169)
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
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L192-201)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L233-239)
```text
    public fun freeze_code_object(publisher: &signer, code_object: Object<PackageRegistry>) acquires PackageRegistry {
        let code_object_addr = code_object.object_address();
        assert!(exists<PackageRegistry>(code_object_addr), error::not_found(ECODE_OBJECT_DOES_NOT_EXIST));
        assert!(
            object::is_owner(code_object, signer::address_of(publisher)),
            error::permission_denied(ENOT_PACKAGE_OWNER)
        );
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L301-346)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/code.spec.move (L1-15)
```text
spec aptos_framework::code {
    /// <high-level-req>
    /// No.: 1
    /// Requirement: Updating a package should fail if the user is not the owner of it.
    /// Criticality: Critical
    /// Implementation: The publish_package function may only be able to update the package if the signer is the actual
    /// owner of the package.
    /// Enforcement: The Aptos upgrade native functions have been manually audited.
    ///
    /// No.: 2
    /// Requirement: The arbitrary upgrade policy should never be used.
    /// Criticality: Critical
    /// Implementation: There should never be a pass of an arbitrary upgrade policy to the
    /// request_publish native function.
    /// Enforcement: Manually audited that it aborts if package.upgrade_policy.policy == 0.
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L80-104)
```text
    public entry fun publish(
        publisher: &signer,
        metadata_serialized: vector<u8>,
        code: vector<vector<u8>>,
    ) {
        let publisher_address = signer::address_of(publisher);
        let object_seed = object_seed(publisher_address);
        let constructor_ref = &object::create_named_object(publisher, object_seed);
        let code_signer = &constructor_ref.generate_signer();
        code::publish_package_txn(code_signer, metadata_serialized, code);

        event::emit(Publish { object_address: signer::address_of(code_signer), });

        move_to(code_signer, ManagingRefs {
            extend_ref: constructor_ref.generate_extend_ref(),
        });
    }

    inline fun object_seed(publisher: address): vector<u8> {
        let sequence_number = account::get_sequence_number(publisher) + 1;
        let seeds = vector[];
        seeds.append(bcs::to_bytes(&OBJECT_CODE_DEPLOYMENT_DOMAIN_SEPARATOR));
        seeds.append(bcs::to_bytes(&sequence_number));
        seeds
    }
```

**File:** aptos-move/e2e-move-tests/src/tests/object_code_deployment.rs (L220-242)
```rust
#[test]
fn object_code_deployment_upgrade_fail_when_not_owner() {
    let mut context = TestContext::new(None, None);
    let acc = context.account.clone();

    // Install the initial version with compat requirements
    assert_success!(context.execute_object_code_action(
        &acc,
        "object_code_deployment.data/pack_initial",
        ObjectCodeAction::Deploy,
    ));

    // We should not be able to upgrade with a different account.
    let different_account = context
        .harness
        .new_account_at(AccountAddress::from_hex_literal("0xbeef").unwrap());
    let status = context.execute_object_code_action(
        &different_account,
        "object_code_deployment.data/pack_upgrade_compat",
        ObjectCodeAction::Upgrade,
    );
    context.assert_feature_flag_error(status, ENOT_CODE_OBJECT_OWNER);
}
```

**File:** aptos-move/e2e-move-tests/src/tests/object_code_deployment.rs (L347-365)
```rust
#[test]
fn freeze_code_object_fail_when_not_owner() {
    let mut context = TestContext::new(None, None);
    let acc = context.account.clone();

    assert_success!(context.execute_object_code_action(
        &acc,
        "object_code_deployment.data/pack_initial",
        ObjectCodeAction::Deploy,
    ));

    let different_account = context
        .harness
        .new_account_at(AccountAddress::from_hex_literal("0xbeef").unwrap());
    let status =
        context.execute_object_code_action(&different_account, "", ObjectCodeAction::Freeze);

    context.assert_feature_flag_error(status, ENOT_PACKAGE_OWNER);
}
```

**File:** aptos-move/cli/src/stored_package.rs (L193-242)
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
}
```

**File:** aptos-move/cli/src/commands.rs (L2104-2139)
```rust
    async fn execute(self) -> CliTypedResult<&'static str> {
        // First build the package locally to get the package metadata
        let build_options = BuildOptions {
            install_dir: self.move_options.output_dir.clone(),
            bytecode_version: fix_bytecode_version(
                self.move_options.bytecode_version,
                self.move_options.language_version,
            ),
            ..self.included_artifacts.build_options(&self.move_options)?
        };
        let w = self.env.writer();
        let pack = BuiltPackage::build_to(&w, self.move_options.get_package_path()?, build_options)
            .map_err(|e| CliError::MoveCompilationError(format!("{:#}", e)))?;
        let compiled_metadata = pack.extract_metadata()?;

        // Now pull the compiled package
        let client = self.rest_options.client(&self.profile_options)?;
        let registry = CachedPackageRegistry::create(client, self.account, false).await?;
        let package = registry
            .get_package(pack.name())
            .await
            .map_err(|s| CliError::CommandArgumentError(s.to_string()))?;

        // We can't check the arbitrary, because it could change on us
        if package.upgrade_policy() == UpgradePolicy::arbitrary() {
            return Err(CliError::CommandArgumentError(
                "A package with upgrade policy `arbitrary` cannot be downloaded \
                since it is not safe to depend on such packages."
                    .to_owned(),
            ));
        }

        // Verify that the source digest matches
        package.verify(&compiled_metadata)?;

        Ok("Successfully verified source of package")
```
