[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** third_party/move/move-model/src/metadata.rs (L59-72)
```rust
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CompilationMetadata {
    /// A flag indicating whether, at time of creation, the compilation
    /// result was considered as unstable. Unstable code may have restrictions
    /// for deployment on production networks. This flag is true if either the
    /// compiler or language versions are unstable.
    pub unstable: bool,
    /// The version of the compiler, as a string. See
    /// `CompilationVersion::from_str` for supported version strings.
    pub compiler_version: String,
    /// The version of the language, as a string. See
    /// `LanguageVersion::from_str` for supported version strings.
    pub language_version: String,
}
```

**File:** third_party/move/move-model/src/metadata.rs (L74-81)
```rust
impl CompilationMetadata {
    pub fn new(compiler_version: CompilerVersion, language_version: LanguageVersion) -> Self {
        Self {
            compiler_version: compiler_version.to_string(),
            language_version: language_version.to_string(),
            unstable: compiler_version.unstable() || language_version.unstable(),
        }
    }
```

**File:** third_party/move/move-model/src/metadata.rs (L154-162)
```rust
impl CompilerVersion {
    /// Return true if this is a stable compiler version. A non-stable version
    /// should not be allowed on production networks.
    pub fn unstable(self) -> bool {
        match self {
            CompilerVersion::V2_0 => false,
            CompilerVersion::V2_1 => true,
        }
    }
```

**File:** third_party/move/move-model/src/metadata.rs (L272-281)
```rust
impl LanguageVersion {
    /// Whether the language version is unstable. An unstable version
    /// should not be allowed on production networks.
    pub const fn unstable(self) -> bool {
        use LanguageVersion::*;
        match self {
            V2_0 | V2_1 | V2_2 | V2_3 | V2_4 => false,
            V2_5 => true,
        }
    }
```

**File:** third_party/move/move-model/src/metadata.rs (L306-317)
```rust
    /// If the bytecode version is not specified, infer it from the language version. For
    /// debugging purposes, respects the MOVE_BYTECODE_VERSION env var as an override.
    pub fn infer_bytecode_version(&self, version: Option<u32>) -> u32 {
        env::get_bytecode_version_from_env(version).unwrap_or(match self {
            LanguageVersion::V2_0
            | LanguageVersion::V2_1
            | LanguageVersion::V2_2
            | LanguageVersion::V2_3
            | LanguageVersion::V2_4 => VERSION_DEFAULT,
            LanguageVersion::V2_5 => VERSION_DEFAULT_LANG_V2_5,
        })
    }
```
