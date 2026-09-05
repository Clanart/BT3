### Title
`with-ft`/`with-nft` allowances silently become "wildcard-for-all-assets-in-contract" when the token literally named `*` is targeted, letting other fungible/non-fungible assets move past `restrict-assets?`/`as-contract?` post-conditions - (File: `clarity/src/vm/functions/post_conditions.rs`)

### Summary
`check_allowances` in `clarity/src/vm/functions/post_conditions.rs` treats an `AssetIdentifier` whose `asset_name` is the literal string `"*"` as a special "match every fungible/non-fungible asset in this contract" wildcard. `ClarityName`, the type used both for real Clarity identifiers (including token names passed to `define-fungible-token`/`define-non-fungible-token`) and for the wildcard sentinel, explicitly permits a bare `"*"` as a valid name via its regex. Consequently, any allowance written as `(with-ft contract-id "*" amount)` or `(with-nft contract-id "*" ids)` is indistinguishable, at the type level, from an allowance meant for a real asset that a contract happens to have named `*`. The checker's merge logic (lines 598-627 for FT, 629-656 for NFT) always folds the `"*"`-named entry into every other asset's allowed list for that contract, so writing an allowance for the concretely-named asset `*` unintentionally (and silently) grants the same numeric/identifier allowance to every other fungible or non-fungible token defined in that contract.

### Finding Description
`eval_allowance` builds `FtAllowance`/`NftAllowance` values straight from the user-supplied `asset-name` string with no restriction excluding the sentinel value: [1](#0-0) 

`check_allowances` stores all `with-ft` entries, keyed by `AssetIdentifier{contract_identifier, asset_name}`, in `ft_allowances`: [2](#0-1) 

When validating what actually moved, for every FT that was transferred it looks up the exact `AssetIdentifier`, and *always* additionally looks up an `AssetIdentifier` for the same contract with `asset_name` hard-coded to `ClarityName::from_literal("*")`, merging any allowance found there into the accepted list for the *unrelated* asset: [3](#0-2) 

The identical pattern exists for NFTs: [4](#0-3) 

The problem is that `"*"` is not a value reserved exclusively for this internal wildcard sentinel — it is a syntactically legal `ClarityName`, matched by the single-character alternative in the name grammar: [5](#0-4) 

That means a contract can legitimately `define-fungible-token` (or `define-non-fungible-token`) an asset whose name is literally `*`. If a caller writes `(with-ft 'SPXXXX.contract "*" u100)` intending to authorize movement of only that specific asset named `*`, the implementation cannot distinguish "allowance for the real asset `*`" from "wildcard allowance for every FT in the contract." The merge logic at lines 608-613 (and the NFT analog at 637-642) treats it as the latter unconditionally, so the allowance is silently broadened to cover every other fungible (or non-fungible) token the contract defines, none of which were mentioned in the `restrict-assets?`/`as-contract?` allowance list.

This breaks the core equality the post-condition/allowance system is supposed to enforce: *the set of assets a protected body is permitted to move equals exactly the set enumerated in the allowance list*. Here, one allowance entry (for asset `*`) leaks permission to move arbitrarily many un-enumerated assets in the same contract, up to the same numeric amount / same NFT id list, without the caller ever writing an allowance for those other assets.

### Impact Explanation
This is a "post-condition escape": inside `restrict-assets?` or `as-contract?`, a body can move fungible or non-fungible tokens that were never covered by any allowance the caller wrote, as long as the contract exposes an asset literally named `*` and the caller (or a wrapping contract) supplies a `with-ft`/`with-nft` allowance for that specific asset. Any code relying on `restrict-assets?`/`as-contract?` to bound what an inner call can move (e.g. a wallet, aggregator, or DeFi router that sandboxes a call to an unknown/untrusted contract with `(with-ft contract "*" limit)`) can have far more value extracted than the stated limit — the limit ends up applying independently to *every* FT (or every NFT id list) the target contract defines, not just the intended one. This matches the "an asset moving past its post-conditions" Critical-impact category.

### Likelihood Explanation
Exploitation requires:
1. A contract (which can be attacker-controlled, e.g. a token contract a victim script interacts with) to define a fungible or non-fungible asset literally named `*` — permitted by the `ClarityName` grammar and requiring no special privilege.
2. A caller to use `restrict-assets?`/`as-contract?` with a `with-ft`/`with-nft` allowance naming that specific `*` asset.

Given `*` is a valid, if unusual, Clarity identifier, and `restrict-assets?`/`as-contract?` are new asset-safety primitives meant to be used defensively when interacting with arbitrary/untrusted contracts, an attacker who controls the target contract can deliberately name one of its assets `*` to booby-trap any caller who writes an allowance referencing it, silently granting itself unlimited-looking allowances on all its other tokens. This is a configuration/identifier-collision issue exactly analogous to the reported bug class (reward token colliding with base token identity causing balance/allowance conflation).

### Recommendation
**Short term:** Reject `"*"` as a valid `asset-name` argument to `with-ft`/`with-nft` at both the type-checker (`check_allowance_with_ft`/`check_allowance_with_nft` in `clarity/src/vm/analysis/type_checker/v2_1/natives/post_conditions.rs`) and runtime (`eval_allowance` in `clarity/src/vm/functions/post_conditions.rs`) layers, so a real asset cannot be named `*` from a caller's allowance perspective, or introduce a distinct, non-collidable wildcard representation (e.g. an `Option<ClarityName>` / dedicated `Allowance::FtWildcard` variant) instead of overloading `AssetIdentifier.asset_name`.

**Long term:** Audit every place in the codebase that uses a string sentinel value to represent "all"/"wildcard" semantics within a namespace that also accepts arbitrary user-supplied names, and replace such sentinels with type-level distinctions that cannot collide with legitimate user input.

### Proof of Concept
1. Deploy contract `C` that defines two fungible tokens: one named `*` and one named `abc` (both are valid via `CLARITY_NAME_REGEX`), each with an `ft-transfer?`-exposing public function.
2. In a driver contract or transaction, execute:
```clarity
(as-contract?
  ((with-ft 'SP...C "*" u100))
  (contract-call? 'SP...C transfer-abc u100 tx-sender 'SP_ATTACKER)
)
```
Here the caller intends to allow only 100 units of the token named `*` to move, but never explicitly allowed moving `abc`.
3. Per `check_allowances` (`clarity/src/vm/functions/post_conditions.rs:598-627`), when checking the `abc` transfer, the code fails to find an exact-match allowance for `abc`, but succeeds in finding the wildcard-keyed allowance (`asset_name == "*"`) registered for the `*`-named token, and merges it in — so the `abc` transfer of up to 100 units passes with no violation, even though no allowance for `abc` was ever written.

### Citations

**File:** clarity/src/vm/functions/post_conditions.rs (L180-196)
```rust
            let asset_name =
                eval(&rest[1], exec_state, invoke_ctx, context)?.clone_with_cost(exec_state)?;
            let asset_name = asset_name
                .expect_string_ascii()
                .map_err(|_| VmInternalError::Expect("Expected ASCII String.".into()))?;
            let asset_name = match ClarityName::try_from(asset_name) {
                Ok(name) => name,
                Err(_) => {
                    return Err(RuntimeError::BadTokenName(rest[1].to_string()).into());
                }
            };

            let asset = AssetIdentifier {
                contract_identifier,
                asset_name,
            };

```

**File:** clarity/src/vm/functions/post_conditions.rs (L515-525)
```rust
    let mut stx_allowances: Vec<(usize, u128)> = Vec::new();
    // Map assets to a vector of (index in allowances, amount)
    let mut ft_allowances: HashMap<AssetIdentifier, Vec<(usize, u128)>> = HashMap::new();
    // Map assets to a tuple with the first allowance's index and a vector of
    // asset identifiers. We use Vec instead of HashSet because:
    // 1. Most NFT IDs are simple (`uint`s), making Value::eq() very fast
    // 2. Linear search through ≤128 items is cache-friendly and fast
    // 3. Avoids serialization cost during both setup and lookup phases
    // 4. Simpler implementation with lower memory overhead (no cloning or
    //    space used for serialization)
    let mut nft_allowances: HashMap<AssetIdentifier, (usize, Vec<Value>)> = HashMap::new();
```

**File:** clarity/src/vm/functions/post_conditions.rs (L598-627)
```rust
    // Check FT movements
    if let Some(ft_moved) = assets.get_all_fungible_tokens(owner) {
        for (asset, amount_moved) in ft_moved {
            // Build merged allowance list: exact-match entries + wildcard entries for the same contract
            let mut merged: Vec<(usize, u128)> = Vec::new();

            if let Some(allowance_vec) = ft_allowances.get(asset) {
                merged.extend(allowance_vec.iter().cloned());
            }

            if let Some(wildcard_vec) = ft_allowances.get(&AssetIdentifier {
                contract_identifier: asset.contract_identifier.clone(),
                asset_name: ClarityName::from_literal("*"),
            }) {
                merged.extend(wildcard_vec.iter().cloned());
            }

            if merged.is_empty() {
                // No allowance for this asset, any movement is a violation
                record_violation(&mut earliest_violation, MAX_ALLOWANCES as u128);
                continue;
            }

            for (index, allowance) in merged {
                if *amount_moved > allowance {
                    record_violation(&mut earliest_violation, index as u128);
                }
            }
        }
    }
```

**File:** clarity/src/vm/functions/post_conditions.rs (L629-656)
```rust
    // Check NFT movements
    if let Some(nft_moved) = assets.get_all_nonfungible_tokens(owner) {
        for (asset, ids_moved) in nft_moved {
            let mut merged: Vec<(usize, &Vec<Value>)> = Vec::new();
            if let Some((index, allowance_vec)) = nft_allowances.get(asset) {
                merged.push((*index, allowance_vec));
            }

            if let Some((index, allowance_vec)) = nft_allowances.get(&AssetIdentifier {
                contract_identifier: asset.contract_identifier.clone(),
                asset_name: ClarityName::from_literal("*"),
            }) {
                merged.push((*index, allowance_vec));
            }

            if merged.is_empty() {
                // No allowance for this asset, any movement is a violation
                record_violation(&mut earliest_violation, MAX_ALLOWANCES as u128);
                continue;
            }

            for (index, allowance_vec) in merged {
                if ids_moved.iter().any(|id| !allowance_vec.contains(id)) {
                    record_violation(&mut earliest_violation, index as u128);
                }
            }
        }
    }
```

**File:** clarity-types/src/representations.rs (L51-56)
```rust
    pub static ref CLARITY_NAME_REGEX_STRING: String =
        "^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$".into();
    pub static ref CLARITY_NAME_REGEX: Regex =
    {
        Regex::new(CLARITY_NAME_REGEX_STRING.as_str()).unwrap()
    };
```
