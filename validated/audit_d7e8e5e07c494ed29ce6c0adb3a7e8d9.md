[1](#0-0)

### Citations

**File:** aptos-move/framework/natives/src/code.rs (L232-240)
```rust
pub struct PublishRequest {
    pub destination: AccountAddress,
    pub bundle: ModuleBundle,
    pub expected_modules: BTreeSet<String>,
    /// Allowed module dependencies. Empty for no restrictions. An empty string in the set
    /// allows all modules from that address.
    pub allowed_deps: Option<BTreeMap<AccountAddress, BTreeSet<String>>>,
    pub check_compat: bool,
}
```
