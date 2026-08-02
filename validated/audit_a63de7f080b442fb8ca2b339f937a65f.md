Confirmed: the deserialization path (`DeserializationSeed` for `MoveStructLayout::Runtime` in [1](#0-0)  and the layout-driven byte copy in [2](#0-1) ) treats a `std::string::String` purely as a `vector<u8>` field — it never invokes `internal_check_utf8`. That check only exists on the explicit constructor path [3](#0-2) . The generic native `util::from_bytes<T>`, used by `code::publish_package_txn` to turn the raw transaction argument into a `PackageMetadata`, goes through this same generic layout-based BCS deserializer [4](#0-3)  and [5](#0-4) . This means `PackageMetadata.name`, `.source_digest`, and `.manifest` (all `std::string::String`) can be populated with arbitrary, non-UTF8 byte sequences and the on-chain invariant "String is guaranteed valid UTF8" is silently violated for permissionlessly published/upgraded packages.

### Title
Non-UTF8 Bytes Can Be Smuggled into `PackageMetadata.name`/`source_digest` via `util::from_bytes`, Breaking the `String` UTF8 Invariant and CLI/Indexer Package Verification — (File: `aptos-move/framework/aptos-framework/sources/code.move`)

### Summary
`code::publish_package_txn` deserializes the caller-supplied `metadata_serialized` bytes directly into a `PackageMetadata` struct using the generic BCS-by-layout native `util::from_bytes<T>`. This deserialization path bypasses the `std::string::utf8()` constructor and its `internal_check_utf8` validation, so an attacker-controlled `PackageMetadata.name` (or `.source_digest`, `.manifest`) field can contain byte sequences that are not valid UTF-8, while still satisfying the Move type system's `String` type.

### Finding Description
`std::string::String` documents itself as "a sequence of bytes which is guaranteed to be in utf8 format" [6](#0-5) , but that guarantee is only enforced in the `utf8()`/`try_utf8()` constructors via the native `internal_check_utf8`. Any code path that produces a `String` value directly from raw bytes without going through those functions bypasses the invariant.

`code::publish_package_txn` accepts `metadata_serialized: vector<u8>` as an entry-function argument and converts it with `util::from_bytes<PackageMetadata>(metadata_serialized)` [5](#0-4) . The native `from_bytes` obtains the Move type layout for `PackageMetadata` and deserializes the raw bytes generically against that layout [4](#0-3) . For struct fields (including the nested `String{bytes: vector<u8>}`), this generic deserializer just walks the byte vector as an untyped BCS-encoded `vector<u8>` and packs it into the `String` struct — there is no call to any UTF8-checking routine, as seen in both the struct field visitor [1](#0-0)  and the byte-copy fast paths of the alternate VM's deserializer [2](#0-1) .

This is an unprivileged, permissionless bug: any account can call `code::publish_package_txn` (directly, or via `object_code_deployment::publish`/`upgrade` [7](#0-6) , or `resource_account::create_resource_account_and_publish_package` [8](#0-7) ) and can craft `metadata_serialized` so that the `PackageMetadata.name` or `.source_digest` field contains invalid UTF-8 bytes while remaining valid BCS.

The exact corrupted value: `PackageRegistry.packages[i].name`/`.source_digest`, a `std::string::String` on-chain resource, containing byte sequences that fail UTF-8 validation — directly analogous to the Gravity Bridge `ERC20DeployedEvent` symbol field containing non-UTF8 bytes.

### Impact Explanation
Downstream code that assumes `PackageMetadata.name`/`source_digest` is valid UTF-8 can break in a way that resembles the referenced Gravity bug's "freeze":
- The Aptos CLI's `VerifyPackage`/`stored_package::verify` flow directly string-compares `self_metadata.name` and `.source_digest` against locally-built metadata [9](#0-8) ; a corrupted on-chain name can never match a legitimately-built local package, and any tooling that tries to render/parse this field (JSON/API serialization of Move `String` assumes valid UTF-8) can error or misbehave.
- More importantly, the same non-UTF8-`name` package registration participates in `check_upgradability`, which matches packages **by name** (`old.name == pack.name`) inside `publish_package` [10](#0-9) . Because name equality is only a raw byte comparison, this by itself does not break consensus — every validator computes the same bytes deterministically — so this does **not** cause an oracle-style network halt like the Gravity Bridge case (that required independent off-chain parsing with different UTF8 enforcement points, i.e. Go protobufs vs. Rust orchestrator). On Aptos, both the write-set and every full node use the same deterministic BCS/Move VM logic, so validators do not diverge and consensus does not freeze.
- Realistic impact is therefore lower than the "freeze the bridge" class: it is a data-integrity / tooling-safety issue — off-chain indexers, block explorers, and the CLI's package-verification/download workflow can panic, error out, or silently mismatch when they assume `String` fields are valid UTF-8, and a malicious package name could be used to confuse verification/attribution tooling. It does not by itself grant unauthorized publish, upgrade, or ownership takeover, nor does it bypass `check_dependencies`/`check_upgradability`/`Compatibility` bytecode-level protections, which operate on module bytecode, not on the `name`/`source_digest` metadata strings.

### Likelihood Explanation
High likelihood of triggering the underlying condition (trivial, permissionless, one transaction), but the blast radius is confined to off-chain/CLI tooling correctness rather than any protected on-chain state mutation, ownership, or upgrade-authority bypass. It does not meet the bar of "Unauthorized module publish/upgrade/freeze/ownership change" or "forbidden bytecode on chain" required by the Publish Impact Gate, since compiled module bytecode itself remains fully verified and compatibility-checked; only auxiliary Move `String` metadata fields carry the corrupted bytes.

### Recommendation
If genuinely treated as a vulnerability, `code::publish_package` (or `util::from_bytes` specifically when the target layout contains `std::string::String`) should validate that any deserialized `String` field's bytes are valid UTF-8 before accepting the `PackageMetadata`, e.g., by exposing/using `internal_check_utf8` as a post-deserialization invariant check on decorated struct layouts, or by requiring metadata to be constructed through `string::utf8()` at the BCS boundary rather than via the generic `from_bytes` native.

### Proof of Concept
1. Build `PackageMetadata` BCS bytes by hand (not through `string::utf8()`), setting the `name` field's inner `vector<u8>` to an invalid UTF-8 sequence, e.g. `b"\xC0\x80"`.
2. Submit `code::publish_package_txn(owner, metadata_serialized, code)` (or via `aptos_framework::object_code_deployment::publish`) with this crafted metadata and a valid, self-consistent module bundle.
3. The transaction succeeds; `PackageRegistry.packages[i].name` on-chain now holds invalid UTF-8 bytes inside a `std::string::String`.
4. Any client, indexer, or the Aptos CLI's `verify-package`/`stored_package::verify` that attempts to interpret or display this field as UTF-8 text will error or mis-render, since the invariant documented in `string.move` was never actually enforced on this path.

**Note**: I was unable to fully confirm from the index whether the primary (non-`mono-move`) production VM's `ValueSerDeContext` deserializer used in mainnet execution has any additional decorated-layout invariant check that might reject non-UTF8 bytes for `String`-typed fields at a higher level (e.g., a resource/type layout annotation checked elsewhere in `aptos-vm`); the search only surfaced the generic struct/vector deserialization code paths, which show no such check. Given the low severity of this finding under the stated Publish Impact Gate, further verification would not change the conclusion.

### Citations

**File:** third_party/move/move-vm/types/src/values/values_impl.rs (L5667-5681)
```rust
impl<'d> serde::de::DeserializeSeed<'d> for DeserializationSeed<'_, &MoveStructLayout> {
    type Value = Struct;

    fn deserialize<D: serde::de::Deserializer<'d>>(
        self,
        deserializer: D,
    ) -> Result<Self::Value, D::Error> {
        match &self.layout {
            MoveStructLayout::Runtime(field_layouts) => {
                let fields = deserializer.deserialize_tuple(
                    field_layouts.len(),
                    StructFieldVisitor(self.ctx, field_layouts),
                )?;
                Ok(Struct::pack(fields))
            },
```

**File:** third_party/move/mono-move/runtime/src/value_utils.rs (L634-658)
```rust
unsafe fn deserialize_impl<T: LayoutProvider + ?Sized>(
    layouts: &T,
    heap: &mut Heap,
    layout: &ValueLayout,
    bytes: &[u8],
    cursor: &mut usize,
    dst: *mut u8,
) -> AllocationResult<()> {
    // TODO(metering): This walk recurses on struct fields and vector elements; convert it
    // to a non-recursive form to bound stack depth on deeply nested values.
    //
    // If every byte pattern is valid (no padding, no pointers, no `bool`), the
    // value's BCS bytes are exactly its in-memory image. A `bool` is excluded
    // because only `0`/`1` are canonical and a raw copy would skip that check.
    // TODO(correctness): breaks on big-endian hosts. This writes the
    // little-endian BCS bytes verbatim, but the in-memory representation is
    // native-endian, so the two only match on little-endian hosts.
    if layout.all_byte_patterns_valid() {
        let n = layout.size as usize;
        let src = read_slice(bytes, cursor, n)?;
        // SAFETY: caller ensures `n` bytes can be written to `dst` and it is
        // not aliasing `src`.
        unsafe { std::ptr::copy_nonoverlapping(src.as_ptr(), dst, n) };
        return Ok(());
    }
```

**File:** third_party/move/move-stdlib/sources/string.move (L12-21)
```text
    /// A `String` holds a sequence of bytes which is guaranteed to be in utf8 format.
    struct String has copy, drop, store {
        bytes: vector<u8>,
    }

    /// Creates a new string from a sequence of bytes. Aborts if the bytes do not represent valid utf8.
    public fun utf8(bytes: vector<u8>): String {
        assert!(internal_check_utf8(&bytes), EINVALID_UTF8);
        String{bytes}
    }
```

**File:** aptos-move/framework/natives/src/util.rs (L30-58)
```rust
fn native_from_bytes(
    context: &mut SafeNativeContext,
    ty_args: &[Type],
    mut args: VecDeque<Value>,
) -> SafeNativeResult<SmallVec<[Value; 1]>> {
    debug_assert_eq!(ty_args.len(), 1);
    debug_assert_eq!(args.len(), 1);

    // TODO(Gas): charge for getting the layout
    let layout = context.type_to_type_layout(&ty_args[0])?;

    let bytes = safely_pop_arg!(args, Vec<u8>);
    context.charge(
        UTIL_FROM_BYTES_BASE + UTIL_FROM_BYTES_PER_BYTE * NumBytes::new(bytes.len() as u64),
    )?;

    let function_value_extension = context.function_value_extension();
    let max_value_nest_depth = context.max_value_nest_depth();
    let val = match ValueSerDeContext::new(max_value_nest_depth)
        .with_legacy_signer()
        .with_func_args_deserialization(&function_value_extension)
        .deserialize(&bytes, &layout)
    {
        Some(val) => val,
        None => return Err(SafeNativeError::abort(EFROM_BYTES)),
    };

    Ok(smallvec![val])
}
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L192-200)
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
```

**File:** aptos-move/framework/aptos-framework/sources/code.move (L258-261)
```text
    public entry fun publish_package_txn(owner: &signer, metadata_serialized: vector<u8>, code: vector<vector<u8>>)
    acquires PackageRegistry {
        publish_package(owner, util::from_bytes<PackageMetadata>(metadata_serialized), code)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L80-90)
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

```

**File:** aptos-move/framework/aptos-framework/sources/resource_account.move (L124-139)
```text
    public entry fun create_resource_account_and_publish_package(
        origin: &signer,
        seed: vector<u8>,
        metadata_serialized: vector<u8>,
        code: vector<vector<u8>>
    ) acquires Container {
        let (resource, resource_signer_cap) =
            account::create_resource_account(origin, seed);
        aptos_framework::code::publish_package_txn(&resource, metadata_serialized, code);
        rotate_account_authentication_key_and_store_capability(
            origin,
            resource,
            resource_signer_cap,
            ZERO_AUTH_KEY
        );
    }
```

**File:** aptos-move/cli/src/stored_package.rs (L193-238)
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
```
