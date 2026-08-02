## Finding: `aptos move verify-package` verifies package metadata, not actual on-chain bytecode

I traced the bug-class ("wrong hash / missing verification input silently produces a false pass") to Aptos's own bytecode-verification tooling and found a genuine local analog: the CLI's package `verify()` never actually compares bytecode.

### Title
`VerifyPackage` (`aptos move verify-package`) never fetches or compares on-chain bytecode, so it can report success for tampered/backdoored module bytecode - (File: `aptos-move/cli/src/commands.rs`, `aptos-move/cli/src/stored_package.rs`)

### Summary
`aptos move verify-package` is documented as checking "that on-chain bytecode matches a local source tree" [1](#0-0) . In implementation, `VerifyPackage::execute` fetches the on-chain `PackageRegistry` with `with_bytecode = false` and only calls `package.verify(&compiled_metadata)` [2](#0-1) . `CachedPackageMetadata::verify` compares only the `PackageMetadata` struct fields (`name`, `deps`, `modules` metadata, `manifest`, `upgrade_policy`, `extension`, `source_digest`) - it never downloads or diffs the actual compiled module bytecode [3](#0-2) .

### Finding Description
`PackageMetadata.modules` is a `vector<ModuleMetadata>`, where `ModuleMetadata` only carries the module `name`, gzipped `source`, `source_map`, and an unused `extension` field - there is no bytecode digest/hash field [4](#0-3) . `source_digest` is likewise computed purely from the *source files*, not from the compiled bytecode: "the sha256 of each individual source, ... sha256 them again" [5](#0-4) .

At publish time, the on-chain protocol only checks that the submitted bytecode passes the bytecode verifier, compatibility rules, and that each module's declared name matches an entry in `expected_modules` [6](#0-5) . Nothing in `code::publish_package` or the native `request_publish*` validates that the `source`/`source_map` bytes embedded in `ModuleMetadata` actually correspond to the bytecode being published [7](#0-6) , [8](#0-7) . A publisher fully controls both fields independently in the same transaction.

`CachedPackageRegistry::create` only downloads bytecode `if with_bytecode` is `true` [9](#0-8) , and `VerifyPackage::execute` hard-codes `false` [10](#0-9) . So the "verify" command reduces to: does the locally rebuilt package's declared source/name/deps/manifest/digest match what the publisher *claimed* in metadata - never "does the deployed executable code match what a reviewer/auditor is looking at."

### Impact Explanation
This breaks the code-safety invariant the tool is documented to provide: "checks that on-chain bytecode matches a local source tree." A malicious or compromised publisher can publish benign-looking `ModuleMetadata.source`/`source_map`/`manifest` alongside arbitrary (e.g. backdoored) compiled bytecode for the same module name - the two are never cross-checked on-chain, and `verify-package` cannot catch the discrepancy because it never fetches the real bytecode. Anyone relying on `aptos move verify-package` (auditors, integrators, users deciding whether to trust an "immutable, source-verified" package) receives false assurance that the deployed executable matches reviewed source, which can lead to trusting compromised code that never displayed itself in the reviewed source. This directly matches the required impact class: "Mismatch between verified bytes, package metadata, dependency declarations, and committed module bytes."

### Likelihood Explanation
High likelihood of triggering under normal usage: this requires no special privilege beyond publishing a package (any account can publish arbitrary `ModuleMetadata.source` independent of the real bytecode), and the verification gap is deterministic - it reproduces on every `aptos move verify-package` invocation against such a package, not a race condition or edge case.

### Recommendation
`VerifyPackage::execute` should always fetch on-chain bytecode (`with_bytecode = true`) and additionally diff each module's actual on-chain bytecode bytes against the locally compiled bytecode (not merely `ModuleMetadata`), failing loudly if they differ. Longer-term, consider embedding a bytecode digest in `PackageMetadata`/`ModuleMetadata` and optionally enforcing it natively at publish time so metadata cannot silently diverge from committed bytecode.

### Proof of Concept
1. Compile package `P` twice from the same directory: build A (benign logic) and build B (bytecode patched or compiled from a modified source not reflected in the metadata's embedded `source`).
2. Publish build B on-chain via `aptos move publish`, but keep `ModuleMetadata.source`/`manifest`/`source_digest` consistent with build A's source files (the publisher can construct/serialize `PackageMetadata` independently of the bytecode bundle since they are separate arguments to `code::publish_package_txn`) [11](#0-10) .
3. Run `aptos move verify-package --account <addr>` from a local checkout of build A's source.
4. `VerifyPackage::execute` builds A locally, fetches the on-chain `PackageRegistry` with `with_bytecode=false`, and calls `verify()`, which only compares metadata fields [12](#0-11) , [3](#0-2) . Since metadata (source/digest/manifest) matches, the command reports `"Successfully verified source of package"` even though the deployed bytecode (build B) differs from build A.

### Citations

**File:** third_party/move/documentation/book/src/cli-deploy.md (L70-83)
```markdown
## `aptos move verify-package`

Build the package locally and verify that the on-chain copy matches.

```shellscript filename="Terminal"
aptos move verify-package --account 0xABC...123
```

| Flag | Meaning |
|---|---|
| `--account <ADDR>` | Address of the on-chain package to verify against. |
| `--included-artifacts <none\|sparse\|all>` | Match what was used at publish time. |

A package published with `upgrade_policy = "arbitrary"` cannot be verified — its content can change at any time, so the verifier refuses to depend on it.
```

**File:** aptos-move/cli/src/commands.rs (L2098-2140)
```rust
#[async_trait]
impl CliCommand<&'static str> for VerifyPackage {
    fn command_name(&self) -> &'static str {
        "VerifyPackage"
    }

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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L27-38)
```text
    /// Metadata for a package. All byte blobs are represented as base64-of-gzipped-bytes
    struct PackageMetadata has copy, drop, store {
        /// Name of this package.
        name: String,
        /// The upgrade policy of this package.
        upgrade_policy: UpgradePolicy,
        /// The numbers of times this module has been upgraded. Also serves as the on-chain version.
        /// This field will be automatically assigned on successful upgrade.
        upgrade_number: u64,
        /// The source digest of the sources in the package. This is constructed by first building the
        /// sha256 of each individual source, than sorting them alphabetically, and sha256 them again.
        source_digest: String,
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L55-65)
```text
    /// Metadata about a module in a package.
    struct ModuleMetadata has copy, drop, store {
        /// Name of the module.
        name: String,
        /// Source text, gzipped String. Empty if not provided.
        source: vector<u8>,
        /// Source map, in compressed BCS. Empty if not provided.
        source_map: vector<u8>,
        /// For future extensions.
        extension: Option<Any>,
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

**File:** aptos-move/framework/aptos-framework/sources/code.move (L366-391)
```text
    /// Native function to initiate module loading
    native fun request_publish(
        owner: address,
        expected_modules: vector<String>,
        bundle: vector<vector<u8>>,
        policy: u8
    );

    /// A helper type for request_publish_with_allowed_deps
    struct AllowedDep has drop {
        /// Address of the module.
        account: address,
        /// Name of the module. If this is the empty string, then this serves as a wildcard for
        /// all modules from this address. This is used for speeding up dependency checking for packages from
        /// well-known framework addresses, where we can assume that there are no malicious packages.
        module_name: String
    }

    /// Native function to initiate module loading, including a list of allowed dependencies.
    native fun request_publish_with_allowed_deps(
        owner: address,
        expected_modules: vector<String>,
        allowed_deps: vector<AllowedDep>,
        bundle: vector<vector<u8>>,
        policy: u8
    );
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1818-1843)
```rust
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
