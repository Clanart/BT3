### Title
`aptos move verify-package` never fetches or compares on-chain bytecode, so it can report success on tampered/backdoored code - ([File: aptos-move/cli/src/stored_package.rs])

### Summary
The Aptos CLI's `verify-package` command is documented as downloading "a package and verif[ying] the bytecode... matches a local compilation of the Move code," but its implementation only compares serialized `PackageMetadata` fields (name, deps, module source text/source-map, manifest, upgrade policy, extension, and a source-digest computed from *source*, not bytecode). It never fetches or diffs the actual on-chain `.mv` bytecode. This is the Aptos-native analog of the external report's core lesson — a function that *looks* like a safety/verification check but omits the one comparison ("is this actually the code that's running") that matters.

### Finding Description
`VerifyPackage::execute` builds the package locally, then creates a `CachedPackageRegistry` with `with_bytecode = false`: [1](#0-0) 

That registry only fetches the on-chain `0x1::code::PackageRegistry` resource (metadata), and explicitly skips retrieving any module bytecode when `with_bytecode` is `false`: [2](#0-1) 

The actual comparison performed, `CachedPackageMetadata::verify`, only checks `name`, `deps`, `modules` (which is `ModuleMetadata` — gzipped *source text*, source map, and extension, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest`: [3](#0-2) 

`source_digest` itself is explicitly documented as a hash of the *source* files, not of the compiled bytecode: [4](#0-3) 

Meanwhile the on-chain module storage (the actual executable bytecode returned by `get_account_module`/fetched via `get_bytecode`) is a completely separate code path only exercised by `DownloadPackage` when `--bytecode` is passed — never by `VerifyPackage`: [5](#0-4) 

Nothing in `code::publish_package` or the native `request_publish`/`request_publish_with_allowed_deps` path enforces that the submitted bytecode bundle was actually produced from the submitted source/metadata — the VM only verifies bytecode well-formedness, address/sender match, compatibility, and module-name/dependency membership, never a source-to-bytecode equivalence: [6](#0-5) [7](#0-6) 

So a publisher can submit honest-looking `source`/`manifest`/`source_digest` metadata while shipping arbitrary, unrelated bytecode in the `code: vector<vector<u8>>` argument of `code::publish_package_txn` / `object_code_deployment::publish` — there is no on-chain link enforced between the two. Anyone (auditor, dependent developer, wallet, marketplace) relying on `aptos move verify-package` to confirm "the deployed code matches this audited source" gets a false positive: the command prints "Successfully verified source of package" while the code actually executing on-chain can be entirely different, malicious bytecode.

### Impact Explanation
This breaks the "verified bytes vs. committed module bytes" invariant that governs trust in publish/upgrade flows. `verify-package` is the tool users are told to use (see `third_party/move/documentation/book/src/cli-deploy.md`) to confirm on-chain code matches source before depending on or interacting with a package. A false "verified" result can lead users/integrators to trust and interact with a module (approve permissions, deposit funds, grant capabilities, treat it as an audited dependency) whose actual bytecode is backdoored — directly enabling drains or privilege abuse downstream, analogous to the original report's "safe method gives false assurance" pattern. Because this affects a standard, widely-recommended verification workflow (not a niche path), and the gap is exploitable by any package publisher with zero special privilege, the potential downstream impact (fund loss / trust bypass for anyone relying on this tool) is high.

### Likelihood Explanation
Low-to-medium likelihood in practice: exploitation requires an attacker to control both the "source" metadata (which they always do, being the publisher) and to convince a victim to run `verify-package` against their package and trust the "Successfully verified" result rather than independently downloading and disassembling on-chain bytecode. Since `verify-package` is explicitly documented/marketed as verifying bytecode, this is a realistic workflow trap rather than a purely theoretical gap.

### Recommendation
1. Make `VerifyPackage::execute` call `CachedPackageRegistry::create(client, self.account, true)` (fetch bytecode), and additionally compare each module's actual on-chain bytecode (`get_bytecode`) against the bytecode produced by locally compiling `pack` (`pack.extract_code()`), byte-for-byte.
2. Fail with a clear error if bytecode is unavailable or mismatched, rather than only checking metadata equality.
3. Update the doc-comment/UX so it's clear metadata-only verification (if ever retained as a fast path) does **not** guarantee runtime behavior matches source, and clearly separate "metadata verified" from "bytecode verified" in the printed result.

### Proof of Concept
1. Compile package `P` from honest source `S`, producing metadata `M` (with `source_digest` computed from `S`) and bytecode `B_honest`.
2. Craft malicious bytecode `B_evil` (e.g., a backdoored version of the same module) that is a valid, compatible `CompiledModule` for the same module name/address (compatibility checks only constrain public function signatures and struct layout, not function bodies) — see compatibility rules: [8](#0-7) 
3. Publish using `code::publish_package_txn(metadata_serialized = M, code = [B_evil])`. The VM only checks sender/address match, compatibility, and module-name/dependency membership — never that `B_evil` matches `M.modules[i].source`: [9](#0-8) 
4. A third party runs `aptos move verify-package --account <addr>` using the honest source `S`. Locally rebuilding `S` reproduces `M` (same `source_digest`, `manifest`, `modules` source text), so `package.verify(&compiled_metadata)` succeeds and prints "Successfully verified source of package" — even though the actually running bytecode is `B_evil`, not derived from `S`.

Note: I was not able to fully trace whether any additional API-server-side or indexer-side bytecode/source cross-check exists outside the CLI (index coverage may not include every relevant file); if such a check exists elsewhere it would mitigate this specific CLI-level gap, but no such check was found in the reachable `code.move`, native publish, or `stored_package.rs` code paths.

### Citations

**File:** aptos-move/cli/src/commands.rs (L2058-2064)
```rust
        if self.bytecode {
            for module in package.module_names() {
                if let Some(bytecode) = registry.get_bytecode(module).await? {
                    package.save_bytecode_to_disk(package_path.as_path(), module, bytecode)?
                }
            }
        };
```

**File:** aptos-move/cli/src/commands.rs (L2119-2137)
```rust
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
```

**File:** aptos-move/cli/src/stored_package.rs (L42-67)
```rust
    pub async fn create(
        client: Client,
        addr: AccountAddress,
        with_bytecode: bool,
    ) -> anyhow::Result<Self> {
        // Need to use a different type to deserialize JSON
        let inner = client
            .get_account_resource_bcs::<PackageRegistry>(addr, "0x1::code::PackageRegistry")
            .await?
            .into_inner();
        let mut bytecode = BTreeMap::new();
        if with_bytecode {
            for pack in &inner.packages {
                for module in &pack.modules {
                    let bytes = client
                        .get_account_module(addr, &module.name)
                        .await?
                        .into_inner()
                        .bytecode
                        .0;
                    bytecode.insert(module.name.clone(), bytes);
                }
            }
        }
        Ok(Self { inner, bytecode })
    }
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L36-38)
```text
        /// The source digest of the sources in the package. This is constructed by first building the
        /// sha256 of each individual source, than sorting them alphabetically, and sha256 them again.
        source_digest: String,
```

**File:** third_party/move/move-vm/runtime/src/storage/publishing.rs (L144-196)
```rust
        for module_bytes in module_bundle {
            let compiled_module =
                CompiledModule::deserialize_with_config(&module_bytes, deserializer_config)
                    .map(Arc::new)
                    .map_err(|err| {
                        err.append_message_with_separator(
                            '\n',
                            "[VM] module deserialization failed".to_string(),
                        )
                        .finish(Location::Undefined)
                    })?;
            let addr = compiled_module.self_addr();
            let name = compiled_module.self_name();

            // Make sure all modules' addresses match the sender. The self address is
            // where the module will actually be published. If we did not check this,
            // the sender could publish a module under anyone's account.
            if addr != sender {
                let msg = format!(
                    "Compiled modules address {} does not match the sender {}",
                    addr, sender
                );
                return Err(verification_error(
                    StatusCode::MODULE_ADDRESS_DOES_NOT_MATCH_SENDER,
                    IndexKind::AddressIdentifier,
                    compiled_module.self_handle_idx().0,
                )
                .with_message(msg)
                .finish(Location::Undefined));
            }

            // All modules can be republished, as long as the new module is compatible
            // with the old module.
            if compatibility.need_check_compat() {
                // INVARIANT:
                //   Old module must be metered at the caller side.
                if let Some(old_module_ref) =
                    existing_module_storage.unmetered_get_deserialized_module(addr, name)?
                {
                    if !is_framework_for_option_enabled
                        && is_enum_option_enabled
                        && old_module_ref.self_id().is_option()
                        && old_module_ref.self_id() == compiled_module.self_id()
                    {
                        // skip check for option module during publishing
                    } else {
                        let old_module = old_module_ref.as_ref();
                        compatibility
                            .check(old_module, &compiled_module)
                            .map_err(|e| e.finish(Location::Undefined))?;
                    }
                }
            }
```

**File:** aptos-move/framework/natives/src/code.rs (L284-360)
```rust
fn native_request_publish(
    context: &mut SafeNativeContext,
    _ty_args: &[Type],
    mut args: VecDeque<Value>,
) -> SafeNativeResult<SmallVec<[Value; 1]>> {
    debug_assert!(matches!(args.len(), 4 | 5));
    let with_allowed_deps = args.len() == 5;

    context.charge(CODE_REQUEST_PUBLISH_BASE)?;

    let policy = safely_pop_arg!(args, u8);
    let mut code = vec![];
    for module in safely_pop_arg!(args, Vec<Value>) {
        let module_code = module.value_as::<Vec<u8>>()?;

        context.charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(module_code.len() as u64))?;
        code.push(module_code);
    }

    let allowed_deps = if with_allowed_deps {
        let mut allowed_deps: BTreeMap<AccountAddress, BTreeSet<String>> = BTreeMap::new();

        for dep in safely_pop_arg!(args, Vec<Value>) {
            let (account, module_name) = unpack_allowed_dep(dep)?;

            let entry = allowed_deps.entry(account);

            if let Entry::Vacant(_) = &entry {
                // TODO: Is the 32 here supposed to indicate the length of an account address in bytes?
                context.charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(32))?;
            }

            context
                .charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(module_name.len() as u64))?;
            entry.or_default().insert(module_name);
        }

        Some(allowed_deps)
    } else {
        None
    };

    let mut expected_modules = BTreeSet::new();
    for name in safely_pop_arg!(args, Vec<Value>) {
        let str = get_move_string(name)?;

        // TODO(Gas): fine tune the gas formula
        context.charge(CODE_REQUEST_PUBLISH_PER_BYTE * NumBytes::new(str.len() as u64))?;
        expected_modules.insert(str);
    }

    let destination = safely_pop_arg!(args, AccountAddress);

    // Add own modules to allowed deps
    let allowed_deps = allowed_deps.map(|mut allowed| {
        allowed
            .entry(destination)
            .or_default()
            .extend(expected_modules.clone());
        allowed
    });

    let code_context = context.extensions_mut().get_mut::<NativeCodeContext>();
    if code_context.requested_module_bundle.is_some() || !code_context.enabled {
        // Can't request second time or if publish requests are not allowed.
        return Err(SafeNativeError::abort(EALREADY_REQUESTED));
    }
    code_context.requested_module_bundle = Some(PublishRequest {
        destination,
        bundle: ModuleBundle::new(code),
        expected_modules,
        allowed_deps,
        check_compat: policy != ARBITRARY_POLICY,
    });

    Ok(smallvec![])
}
```

**File:** third_party/move/documentation/book/src/modules-and-packages.md (L623-637)
```markdown
### Compatibility rules

When using `compatible` upgrade policy, a module package can be upgraded. However, updates to existing modules already
published previously need to be compatible and follow the rules below:

- All existing structs' fields cannot be updated. This means no new fields can be added and existing fields cannot be
  modified.
- All public and entry functions cannot change their signature (argument types, type arguments, return types). However,
  argument names can change.
- `public(friend)` functions are treated as private and thus their signature can arbitrarily change. This is safe as
  only modules in the same package can call friend functions anyway, and they need to be updated if the signature changes.
- [Enum type upgrade compatibility rules](./structs-and-enums.md#enum-type-upgrade-compatibility).
- Existing abilities on a struct/enum type cannot be removed (but abilities can be added).

When updating your modules, if you see an incompatible error, make sure to check the above rules and fix any violations.
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1804-1843)
```rust
    /// Validate a publish request.
    fn validate_publish_request(
        &self,
        module_storage: &impl AptosModuleStorage,
        traversal_context: &mut TraversalContext,
        gas_meter: &mut impl GasMeter,
        modules: &[CompiledModule],
        mut expected_modules: BTreeSet<String>,
        allowed_deps: Option<BTreeMap<AccountAddress, BTreeSet<String>>>,
    ) -> VMResult<()> {
        self.reject_unstable_bytecode(modules)?;
        self.reject_legacy_module_bytecode(modules)?;
        native_validation::validate_module_natives(modules)?;

        for m in modules {
            if !expected_modules.remove(m.self_id().name().as_str()) {
                return Err(Self::metadata_validation_error(&format!(
                    "unregistered module: '{}'",
                    m.self_id().name()
                )));
            }
            if let Some(allowed) = &allowed_deps {
                for dep in m.immediate_dependencies() {
                    if !allowed
                        .get(dep.address())
                        .map(|modules| {
                            modules.contains("") || modules.contains(dep.name().as_str())
                        })
                        .unwrap_or(false)
                    {
                        return Err(Self::metadata_validation_error(&format!(
                            "unregistered dependency: '{}'",
                            dep
                        )));
                    }
                }
            }
            verify_module_metadata_for_module_publishing(m, self.features())
                .map_err(|err| Self::metadata_validation_error(&err.to_string()))?;
        }
```
