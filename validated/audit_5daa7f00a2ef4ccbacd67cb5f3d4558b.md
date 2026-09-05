### Title
`restrict-assets?`/`as-contract?` fungible-token allowance collides with the internal "all FTs in contract" wildcard sentinel, letting a token literally named `*` unlock unrestricted fungible-token transfers - (File: clarity/src/vm/functions/post_conditions.rs)

### Summary
`check_allowances` in [1](#0-0)  stores fungible-token allowances in a `HashMap<AssetIdentifier, Vec<(usize, u128)>>` keyed by the real `AssetIdentifier`. To support an "allow any FT in this contract" allowance, the checker treats an `AssetIdentifier` whose `asset_name` equals the literal string `"*"` as a wildcard sentinel and merges it into every fungible-asset check for that contract: [2](#0-1) . Nothing distinguishes this internal sentinel from a legitimate, user-chosen asset name. `AllowanceWithFt` builds its `AssetIdentifier.asset_name` directly from a `ClarityName` supplied by the contract author via `define-fungible-token`, without banning `"*"`, as seen in the argument handling at [3](#0-2) . `ClarityName`'s grammar permits bare operator-style identifiers (e.g. `+`, `-`, `*`, `/`), so `*` is a syntactically valid fungible-token name.

### Finding Description
The equality this code is supposed to preserve is: *an allowance declared for asset X should authorize movement of exactly X (and only X) up to the stated amount*. This equality breaks whenever a contract defines a fungible token whose `asset-name` is exactly `"*"`.

When a caller writes `(allowance-with-ft contract "*" amount)` intending to authorize only the specific token literally named `"*"`, `eval_allowance` stores it as `FtAllowance { asset: AssetIdentifier { contract_identifier, asset_name: "*" }, amount }`. `check_allowances` inserts this into `ft_allowances` keyed on `AssetIdentifier{contract, asset_name: "*"}` — the exact same key the checker uses internally to represent "all FTs in this contract are allowed" (built at [4](#0-3) ). Consequently, for every *other* fungible token `T ≠ "*"` moved out of the same contract during the `restrict-assets?`/`as-contract?` scope, the lookup at line 608 (`ft_allowances.get(&AssetIdentifier{contract, asset_name: "*"})`) succeeds and merges the narrow, single-token allowance into the check for `T`, authorizing movement of `T` up to the amount the caller only meant to authorize for the token `"*"`.

The caller's intended narrow equality ("only token `*` may move") silently becomes "any FT in this contract may move (up to that amount)" — an asset (`T`) moving past what its declared allowance actually covers.

### Impact Explanation
This breaks the "asset moving past its post-conditions" invariant for the `restrict-assets?`/`as-contract?` safety mechanism: a contract that composes with an untrusted callee inside a restricted scope, and that (directly or via a dependency) issues an FT-specific allowance for a token literally named `"*"`, ends up authorizing the untrusted callee to move *any* fungible token held by/for that contract identifier, not just the one it named. This is a cross-account impact: the party harmed is whoever wrote the `restrict-assets?` block expecting a narrow, single-asset guarantee, and the beneficiary is the untrusted contract invoked within that scope.

### Likelihood Explanation
Exploitation requires: (1) a fungible-token contract whose asset actually is named `"*"` (or an attacker persuading/tricking a legitimate contract's allowance list into referencing `"*"`), and (2) a `restrict-assets?`/`as-contract?` caller writing an allowance for that specific token. This is a narrow but concrete precondition — not requiring miner/signer/admin privileges, and reachable purely through ordinary Clarity contract deployment and calls. Likelihood is Low-to-Medium given the naming coincidence needed, but the resulting break is a genuine equality violation in production logic, directly analogous to the Caddy report's un-escaped-sentinel-collision bug class.

### Recommendation
Do not use a value from the same domain as real asset names (a bare `"*"` `ClarityName`) as the internal "all FTs in this contract" sentinel. Represent the "all FTs in contract" allowance with a distinct enum variant/flag instead of encoding it as an `AssetIdentifier` with `asset_name == "*"`, so it can never collide with a real, attacker- or user-defined asset name. Update `check_allowances` in [5](#0-4)  and the corresponding NFT wildcard path to match on this new variant instead of a literal string comparison.

### Proof of Concept
1. Deploy contract `C` that defines a fungible token literally named `*` (`(define-fungible-token * u1000000)`) alongside a second, valuable fungible token `gold`.
2. Contract `Guard` calls into `C` (or a dependent of it) inside `(as-contract? (lambda () ...) (allowance-with-ft 'C "*" u1))` intending to permit only up to 1 unit of the token named `"*"` to move.
3. Inside that restricted scope, `C` (or code it calls) transfers a large amount of `gold` out of `Guard`'s holdings.
4. In `check_allowances`, the lookup `ft_allowances.get(&AssetIdentifier{contract_identifier: C, asset_name: "*"})` at [4](#0-3)  matches the caller's narrow allowance entry (which is keyed identically because its `asset_name` is the literal `"*"`), so the `gold` transfer is treated as within the merged allowance and passes, even though `Guard` only intended to authorize 1 unit of the token actually named `*`.

### Citations

**File:** clarity/src/vm/functions/post_conditions.rs (L180-195)
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

**File:** clarity/src/vm/functions/post_conditions.rs (L501-530)
```rust
fn check_allowances(
    owner: &PrincipalData,
    allowances: Vec<Allowance>,
    assets: &AssetMap,
    epoch: StacksEpochId,
) -> Result<Option<u128>, VmExecutionError> {
    let mut earliest_violation: Option<u128> = None;
    let record_violation = |earliest: &mut Option<u128>, candidate: u128| {
        if earliest.is_none_or(|current| candidate < current) {
            *earliest = Some(candidate);
        }
    };

    // Elements are (index in allowances, amount)
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
    // Elements are (index in allowances, amount)
    let mut stacking_allowances: Vec<(usize, u128)> = Vec::new();
    // Index of the first `with-pox` allowance, if any.
    let mut pox_allowance: Option<usize> = None;

```

**File:** clarity/src/vm/functions/post_conditions.rs (L598-656)
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
