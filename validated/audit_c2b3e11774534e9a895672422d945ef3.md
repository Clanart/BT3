## Title
`aptos move verify-package` never compares on-chain bytecode to the rebuilt module bytes, so attacker-controlled `PackageMetadata` can make malicious bytecode "verify" as matching benign source — (File: `aptos-move/cli/src/stored_package.rs`)

## Summary
`aptos move verify-package` is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0) , but its implementation, `CachedPackageMetadata::verify`, only diff-checks the `PackageMetadata` struct fields (name, deps, module *names/source text*, manifest, upgrade policy, extension, source digest) — it never fetches or hashes the actual on-chain module bytecode [2](#0-1) . Because the Move-side publish flow accepts `metadata` and `code` as two independently supplied byte blobs with no binding beyond module-name matching, an attacker can publish arbitrary malicious bytecode alongside metadata describing innocuous source, and `verify-package` will still report success.

## Finding Description
The publish entry point `code::publish_package_txn`/`publish_package` takes `pack: PackageMetadata` (including `source_digest`, `manifest`, and per-module gzipped `source`) and `code: vector<vector<u8>>` (the actual compiled modules) as separate, publisher-controlled arguments [3](#0-2) . On-chain validation of the relationship between the two is limited to matching module *names*: `validate_publish_request` only checks that each compiled module's name is present in `expected_modules` (derived from `pack.modules[].name`) [4](#0-3) . Nothing checks that `source_digest`, `manifest`, or per-module `source` text actually correspond to the bytecode being committed — those fields are purely informational.

The CLI's `VerifyPackage` command is the tool meant to close that gap for reviewers: it rebuilds the package locally, fetches the on-chain `PackageMetadata` (explicitly with `with_bytecode = false`, i.e. it never downloads the real on-chain module bytes) via `CachedPackageRegistry::create(client, self.account, false)`, and calls `package.verify(&compiled_metadata)` [5](#0-4) . `verify()` only compares `name`, `deps`, `modules` (name/source/source_map/extension — the gzip *source text*, not bytecode), `manifest`, `upgrade_policy`, `extension`, and `source_digest` between the two metadata objects [2](#0-1) . It never retrieves or hashes the actual `.mv` bytecode stored under the account and compares it against what a rebuild of the claimed source would produce.

Since the publisher fully controls both the on-chain `PackageMetadata` (including `source_digest` and per-module `source`) and the separately-published `code` bytes, and the two are never cross-validated on-chain, a publisher can make the metadata perfectly self-consistent with a benign "cover" source tree while shipping unrelated, malicious bytecode under the same module names. `verify-package`, which never inspects the real bytecode, will report `"Successfully verified source of package"`.

## Impact Explanation
This breaks the core code-safety invariant that "verified bytes, package metadata, dependency declarations, and committed module bytes" must agree. Any party that trusts the `verify-package` command's output — auditors approving a mainnet deployment, a DAO/governance reviewer, or downstream automated tooling — can be deceived into believing that the deployed, executing bytecode matches audited/benign source, when in fact arbitrary code (backdoors, hidden privileged functions, drainers) is running under the verified module names. The `aptos move download-package` and `verify-package` docs explicitly promote this as the mechanism "For code review and reproducibility" [6](#0-5) , so the false sense of assurance has direct mainnet relevance for any workflow (dependency resolution via `package_hooks.rs`'s `resolve_custom_dependency`/`maybe_download_package`, which trusts downloaded metadata/source without bytecode verification either [7](#0-6) ) that relies on this "verification" as a safety gate before trusting or building against a package.

## Likelihood Explanation
No privileged role is required. Any account can publish a package via the standard `code::publish_package_txn` path with metadata and bytecode of its own choosing, since the framework never binds `source`/`source_digest`/`manifest` to the actual compiled module bytes — only module names are checked [8](#0-7) . Constructing a `source_digest`/`source` pair that is internally consistent with a fabricated benign source tree is trivial (the digest computation is just `sha256` over the attacker-chosen source files) [9](#0-8) . The only barrier to exploitation is that reviewers must rely on `verify-package` rather than independently disassembling/decompiling on-chain bytecode — a very plausible real-world workflow given the tool's documented purpose.

## Recommendation
`VerifyPackage`/`CachedPackageMetadata::verify` must fetch and compare the actual on-chain module bytecode (as `DownloadPackage` already can, via `with_bytecode = true` and `registry.get_bytecode`) against bytecode freshly compiled from the local source tree, in addition to (not instead of) the metadata-field comparison currently performed. Recompiling the claimed source and byte-for-byte (or normalized) comparing it to the on-chain `.mv` modules is the only way to make the "on-chain bytecode matches a local source tree" claim actually true.

## Proof of Concept
1. Prepare two Move packages with identical module names, e.g. `0xcafe::m`: `real/` containing malicious logic (e.g. a hidden `admin_drain` entry function guarded by an attacker-known secret), and `cover/` containing an innocuous version of `m` with the same public interface.
2. Compile `cover/` to obtain `source_digest_cover`/gzip `source` text, and manually construct a `PackageMetadata` whose `modules[].source`/`source_digest` are taken from `cover/`.
3. Compile `real/` to obtain the actual bytecode blob(s).
4. Submit a `code::publish_package_txn(owner, metadata_from_cover, code_from_real)` transaction — this succeeds because `validate_publish_request` only checks that module name `m` is expected, with no source/digest cross-check [8](#0-7) .
5. Run `aptos move verify-package --account <addr>` against a local copy of `cover/`. Because `VerifyPackage::execute` never downloads or compares bytecode (`CachedPackageRegistry::create(client, self.account, false)`), it only diff-checks metadata fields, all of which match `cover/`'s rebuild — the command prints `"Successfully verified source of package"` [5](#0-4)  even though the on-chain module is actually `real/`'s malicious bytecode.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L12-12)
```markdown
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L157-187)
```text
    /// Publishes a package at the given signer's address. The caller must provide package metadata describing the
    /// package.
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
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1803-1841)
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

**File:** aptos-move/cli/src/package_hooks.rs (L31-57)
```rust
    fn resolve_custom_dependency(
        &self,
        _dep_name: Symbol,
        info: &CustomDepInfo,
    ) -> anyhow::Result<()> {
        block_on(maybe_download_package(info))
    }
}

async fn maybe_download_package(info: &CustomDepInfo) -> anyhow::Result<()> {
    if !info
        .download_to
        .join(CompiledPackageLayout::BuildInfo.path())
        .exists()
    {
        let registry = CachedPackageRegistry::create(
            Client::new(Url::parse(info.node_url.as_str())?),
            load_account_arg(info.package_address.as_str())?,
            false,
        )
        .await?;
        let package = registry.get_package(info.package_name).await?;
        package.save_package_to_disk(info.download_to.as_path())
    } else {
        Ok(())
    }
}
```

**File:** third_party/move/tools/move-package/src/resolution/digest.rs (L12-52)
```rust
pub fn compute_digest(paths: &[PathBuf]) -> Result<PackageDigest> {
    let mut hashed_files = Vec::new();
    let mut hash = |path: &Path| {
        let contents = std::fs::read(path)?;
        hashed_files.push(format!("{:X}", Sha256::digest(&contents)));
        Ok(())
    };
    let mut maybe_hash_file = |path: &Path| -> Result<()> {
        match path.extension() {
            Some(x) if MOVE_EXTENSION == x => hash(path),
            _ if path.ends_with(SourcePackageLayout::Manifest.path()) => hash(path),
            _ => Ok(()),
        }
    };

    for path in paths {
        if path.is_file() {
            maybe_hash_file(path)?;
        } else {
            for entry in walkdir::WalkDir::new(path)
                .follow_links(true)
                .into_iter()
                .filter_map(|e| e.ok())
            {
                if entry.file_type().is_file() {
                    maybe_hash_file(entry.path())?
                }
            }
        }
    }

    // Sort the hashed files to ensure that the order of files is always stable
    hashed_files.sort();

    let mut hasher = Sha256::new();
    for file_hash in hashed_files.into_iter() {
        hasher.update(file_hash.as_bytes());
    }

    Ok(PackageDigest::from(format!("{:X}", hasher.finalize())))
}
```
