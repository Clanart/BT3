### Title
FT/NFT allowance wildcard collision with a literally-named `"*"` asset lets `restrict-assets?`/`as-contract?` post-conditions be bypassed - (File: clarity/src/vm/functions/post_conditions.rs)

### Summary
`check_allowances` in `clarity/src/vm/functions/post_conditions.rs` implements the Clarity-level asset-post-condition mechanism used by `restrict-assets?` and `as-contract?`. For fungible and non-fungible tokens, it treats an `AssetIdentifier` whose `asset_name` is the literal string `"*"` as a special "match all FTs/NFTs in this contract" wildcard entry, and merges it into the allowance list for every other asset name in that same contract. [1](#0-0) [2](#0-1) 

### Finding Description
`eval_allowance` builds an `Allowance::Ft`/`Allowance::Nft` from whatever `ClarityName` a contract passes for the token/asset name argument to `with-ft`/`with-nft`, with no restriction against the value `"*"`: [3](#0-2) [4](#0-3) 

Later, `check_allowances` looks up allowances by exact asset match, and *additionally* looks up an allowance keyed by the same `contract_identifier` but with `asset_name` hard-coded to the literal `"*"`, merging both sets and treating either as satisfying the check for the moved asset: [5](#0-4) [6](#0-5) 

Because `ClarityName` permits operator-like single-character tokens (including `*`) as valid identifiers, a contract deploying a `define-fungible-token`/`define-non-fungible-token` named literally `*` is plausible. If a caller's `restrict-assets?`/`as-contract?` allowance list authorizes movement of that literally-named `*` token/asset via `with-ft`/`with-nft`, `check_allowances` will index it into the *same* wildcard bucket used to authorize movement of **every other FT/NFT defined in that contract**, not just the one the author intended to permit. This breaks the equality the mechanism is supposed to enforce: "assets moved == assets explicitly allowed by name." An allowance meant to scope access to a single, specifically named asset instead silently expands to cover all fungible/non-fungible assets in the target contract, letting body code inside `restrict-assets?`/`as-contract?` move or burn other tokens that were never listed, without triggering a violation/rollback.

This is the closest structural analog to the Balancer bug: both are "constraint meant to bound an asset's movement is silently satisfied by an unrelated over-broad match," and in both cases the value that escapes the intended scope is attacker/deployer controlled (the "*"-named asset here, the imbalanced pool there).

### Impact Explanation
If exploitable, this allows an asset (STX substitute here is FT/NFT) to move past the post-conditions the caller believed were in force, inside a `restrict-assets?`/`as-contract?` block, which the report's rules classify as Critical ("an asset moving past its post-conditions"). The scope is limited to the specific contract that defines a token/asset literally named `*` combined with a caller who explicitly (and, presumably, intentionally, believing it only covers that one token) writes a `with-ft`/`with-nft` allowance for that name — no privileged party or off-chain assumption is required beyond ordinary Clarity code deployment and invocation.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires (1) a contract that defines a fungible or non-fungible token whose `asset_name` is exactly `"*"` — an unusual but syntactically valid choice under Clarity's identifier grammar which permits certain single-character "operator" names — and (2) a caller who names that literal asset in a `with-ft`/`with-nft` allowance without realizing the implementation reserves that string as an internal wildcard sentinel. This is plausible as either an intentional attack (a malicious contract author deliberately names a token `*` to trap `restrict-assets?`/`as-contract?` callers) or a foot-gun triggered by coincidence.

### Recommendation
Do not overload a valid, user-namespaceable `ClarityName` value (`"*"`) as an internal sentinel for "match all assets in this contract." Represent the "any FT/NFT in this contract" allowance as a distinct `Allowance` variant (e.g., a boolean/flag stored alongside the per-asset map) rather than as a synthesized `AssetIdentifier` keyed on the literal string `"*"`, so that a real on-chain asset literally named `*` can never collide with, or be conflated with, the wildcard semantics.

### Proof of Concept
Conceptual sequence (cannot be executed here, but derivable directly from the code above):
1. Contract `C` defines two fungible tokens: `token-a` (the one a caller intends to permit) and, separately, a token literally named `*` (e.g., `(define-fungible-token * u1000000)`), controlled/deployed by an adversarial or unaware author.
2. A victim contract calls:
   `(as-contract? ((with-ft 'C * u10)) (contract-call? 'C mint-and-drain token-a tx-sender))`
   believing this only authorizes moving up to `u10` of the asset named `*`.
3. Inside `check_allowances`, the FT movement for `token-a` is looked up: no exact-match allowance exists for `token-a`, but the wildcard lookup for `AssetIdentifier{contract_identifier: C, asset_name: "*"}` succeeds because the caller's `with-ft` entry for the literal `*` asset is present under that same key.
4. `merged` is non-empty, so the check against `token-a`'s movement uses the `*` allowance's amount (`u10`) instead of rejecting the movement outright — and if the amount happens to satisfy that bound (or the author picked a large bound for the `*` token believing it applied only to that specific asset), `token-a` moves without ever having its own allowance declared, i.e., past the caller's intended post-condition scope. [1](#0-0)

### Citations

**File:** clarity/src/vm/functions/post_conditions.rs (L159-204)
```rust
        NativeFunctions::AllowanceWithFt => {
            if rest.len() != 3 {
                return Err(RuntimeCheckErrorKind::IncorrectArgumentCount(3, rest.len()).into());
            }

            let contract_value =
                eval(&rest[0], exec_state, invoke_ctx, context)?.clone_with_cost(exec_state)?;
            let contract = contract_value
                .clone()
                .expect_principal()
                .map_err(|_| VmInternalError::Expect("Expected principal".into()))?;
            let contract_identifier = match contract {
                PrincipalData::Standard(_) => {
                    return Err(RuntimeCheckErrorKind::ExpectedContractPrincipalValue(
                        contract_value.to_error_string(),
                    )
                    .into());
                }
                PrincipalData::Contract(c) => c,
            };

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

            let amount =
                eval(&rest[2], exec_state, invoke_ctx, context)?.clone_with_cost(exec_state)?;
            let amount = amount
                .expect_u128()
                .map_err(|_| VmInternalError::Expect("Expected u128".into()))?;

            Ok(Allowance::Ft(FtAllowance { asset, amount }))
        }
```

**File:** clarity/src/vm/functions/post_conditions.rs (L205-250)
```rust
        NativeFunctions::AllowanceWithNft => {
            if rest.len() != 3 {
                return Err(RuntimeCheckErrorKind::IncorrectArgumentCount(3, rest.len()).into());
            }

            let contract_value =
                eval(&rest[0], exec_state, invoke_ctx, context)?.clone_with_cost(exec_state)?;
            let contract = contract_value
                .clone()
                .expect_principal()
                .map_err(|_| VmInternalError::Expect("Expected principal".into()))?;
            let contract_identifier = match contract {
                PrincipalData::Standard(_) => {
                    return Err(RuntimeCheckErrorKind::ExpectedContractPrincipalValue(
                        contract_value.to_error_string(),
                    )
                    .into());
                }
                PrincipalData::Contract(c) => c,
            };

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

            let asset_id_list =
                eval(&rest[2], exec_state, invoke_ctx, context)?.clone_with_cost(exec_state)?;
            let asset_ids = asset_id_list
                .expect_list()
                .map_err(|_| VmInternalError::Expect("Expected list".into()))?;

            Ok(Allowance::Nft(NftAllowance { asset, asset_ids }))
        }
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
