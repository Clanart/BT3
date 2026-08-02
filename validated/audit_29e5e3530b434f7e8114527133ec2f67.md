The strongest local analog I found is not in the Move framework's publish/upgrade entry points (those are heavily audited and already have this exact model with high-level requirements in `code.spec.move`), but in the Aptos CLI's `verify-package` command, whose entire purpose is to establish the "on-chain bytecode ↔ package metadata ↔ local source" correspondence demanded by the Publish Pivots — and which fails to actually check bytecode at all.

### Title
`aptos move verify-package` never compares on-chain module bytecode, only textual metadata, silently defeating its advertised bytecode-integrity guarantee - (File: aptos-move/cli/src/stored_package.rs)

### Summary
`aptos move verify-package` is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0) . In its implementation, `VerifyPackage::execute` fetches the on-chain `PackageRegistry` with `with_bytecode = false` and then calls `CachedPackageMetadata::verify()` [2](#0-1) . `verify()` only compares `PackageMetadata` fields — `name`, `deps`, `modules` (which holds zipped *source text* and *source map*, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` — and never touches the actual compiled `.mv` bytecode stored as separate account-module resources [3](#0-2) .

### Finding Description
On-chain, `code::publish_package_txn` accepts `metadata_serialized` (package/module metadata, including each module's zipped source text) and `code` (the actual compiled bytecode bundle) as two independent, uncorrelated transaction parameters [4](#0-3) . Nothing in `publish_package`/`request_publish_with_allowed_deps` enforces that the bytecode in `code` was actually compiled from the source text embedded in `metadata.modules[i].source` — the only cross-check performed is that module *names* match `expected_modules` [5](#0-4) . This is a well-known, intentional trust gap that `aptos move verify-package` is supposed to close for downstream consumers: it is meant to recompile the claimed source locally and diff it against what is really running on-chain.

However, `CachedPackageRegistry::create` is invoked with `with_bytecode = false` in `VerifyPackage::execute`, so the actual bytecode is never even downloaded [6](#0-5) . `CachedPackageRegistry` does support fetching bytecode via `get_bytecode()` when `with_bytecode = true` [7](#0-6) , and it separately calls `client.get_account_module(addr, &module.name)` to obtain the real `.mv` bytes, but this path is unused by `verify-package`. The `verify()` function itself has no parameter for compiled bytecode and no logic path that would ever compare it, even if it were passed in [3](#0-2) .

The net effect: a package owner can publish a `PackageMetadata` whose `modules[i].source` (and matching `source_digest`) describes innocuous, reviewable Move source code, while submitting a completely different, malicious `code` bundle (e.g., containing a backdoored function, a hidden capability leak, or logic diverging from the audited source) in the same publish transaction. Anyone who runs `aptos move verify-package --account <addr>` — exactly the tool the docs recommend for "code review and reproducibility" [8](#0-7)  — will see `Ok("Successfully verified source of package")` [9](#0-8)  even though the running bytecode has no relationship to the reviewed source.

### Impact Explanation
This breaks the specific "verified bytes vs. committed module bytes" invariant the Publish Pivots call out. It undermines a security-critical trust workflow: reviewers, auditors, or automated tooling that gate deployment/dependency decisions on `verify-package` succeeding will falsely conclude that on-chain code matches audited source, when in fact arbitrary bytecode (including malicious logic, backdoors, or code that violates the upgrade/compatibility assumptions reviewers relied on) can be running at that address. Because `verify-package` is presented as the canonical mechanism for "reproducibility" and code review of already-deployed, permissionlessly-published packages (including object-code and resource-account deployments, which share the same `PackageMetadata`/`PackageRegistry` format), this is a supply-chain-level integrity failure with direct mainnet relevance.

### Likelihood Explanation
This requires no privileged access or race condition — any account can publish a package with mismatched metadata/bytecode today (nothing on-chain prevents it), and any user relying on the CLI's `verify-package` will be silently misled. The bug is deterministic and always reproducible; it doesn't depend on adversarial timing.

### Recommendation
Change `VerifyPackage::execute` to call `CachedPackageRegistry::create(client, self.account, true)` and extend `CachedPackageMetadata::verify` (or add a separate verification step) to recompile the local package's bytecode and byte-for-byte compare each module against `registry.get_bytecode(module_name)`, failing verification on any mismatch — not just relying on metadata/source-digest equality.

### Proof of Concept
1. Compile package `Foo` locally with an innocuous `sources/foo.move`; note its `source_digest`.
2. Craft a `PackageMetadata` with correct `name`, `deps`, `manifest`, `modules[0].source` = zipped innocuous source, and correctly matching `source_digest`.
3. Separately compile a *different*, malicious `foo.move` (e.g., one that drains a resource or grants extra capabilities) to produce bytecode `code_malicious`, keeping the module name identical (`Foo`) so `expected_modules` checking passes.
4. Submit `code::publish_package_txn(owner, metadata_serialized_from_step_2, code_malicious_from_step_3)`. This succeeds on-chain because there is no cross-check between metadata source and bytecode [10](#0-9) .
5. A third party runs `aptos move verify-package --account <owner>`, building the *innocuous* source locally and comparing to the registry. Because `with_bytecode=false` and `verify()` never inspects bytecode, the command prints `"Successfully verified source of package"` [11](#0-10) , despite `code_malicious` actually executing on-chain.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L5-12)
```markdown
## Picking a publication pattern

Two patterns are supported side by side; pick based on how you want the package's address to behave:

- **Account publishing** (`publish` / `deploy`): the modules live at the **signer's account address**. Simplest pattern. Upgrades happen by re-publishing from the same account. The signer owns upgrade authority and can't transfer it.
- **Code-object publishing** (`deploy-object` / `upgrade-object`): the modules live at a **separate, derived object address**. Upgrade authority is held in a code object and can be transferred. Use this when modules need an address independent of any single account.

For code review and reproducibility, [`verify-package`](#aptos-move-verify-package) checks that on-chain bytecode matches a local source tree.
```

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-76)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```
```

**File:** aptos-move/cli/src/commands.rs (L2104-2140)
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
    }
```

**File:** aptos-move/cli/src/stored_package.rs (L40-67)
```rust
impl CachedPackageRegistry {
    /// Creates a new registry.
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L256-261)
```text
    /// Same as `publish_package` but as an entry function which can be called as a transaction. Because
    /// of current restrictions for txn parameters, the metadata needs to be passed in serialized form.
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
    }
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
