Confirmed: the regex `CLARITY_NAME_REGEX_STRING = "^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$"` explicitly allows the single-character name `"*"` as a valid `ClarityName` (matched by the `^[-+=/*]$` alternative) [1](#0-0) . This means a contract can legitimately `define-fungible-token *` or `define-non-fungible-token *` with the literal name `*`, and this collides with the internal sentinel value the wildcard-allowance logic uses.

### Title
Wildcard sentinel `"*"` in `restrict-assets?`/`as-contract?` allowances collides with a legitimately named on-chain asset, letting asset movement escape its post-condition limit - (File: clarity/src/vm/functions/post_conditions.rs)

### Summary
`check_allowances` treats an `AssetIdentifier` whose `asset_name` equals the literal string `"*"` as a special "wildcard" allowance that matches *every* fungible/non-fungible asset defined in that contract [2](#0-1) [3](#0-2) . However, `"*"` is also a syntactically valid `ClarityName` per `CLARITY_NAME_REGEX_STRING` [4](#0-3) , so a contract can define a real fungible/non-fungible token literally named `*`. A `with-ft`/`with-nft` allowance that a user writes intending to restrict only that specific token (`(with-ft .token "*" u100)`) is indexed in `ft_allowances`/`nft_allowances` keyed by `AssetIdentifier{contract, "*"}` — the exact same key the code uses as its "any asset in this contract" wildcard sentinel. As a result the allowance is silently broadened from "this one asset named `*`, limit 100" to "every fungible/non-fungible asset in this contract, limit 100 each," permitting other, unrelated tokens in the same contract to move without a corresponding user-specified allowance.

### Finding Description
`eval_allowance` for `AllowanceWithFt`/`AllowanceWithNft` accepts any ASCII string as `asset_name`, including `"*"`, converting it straight into a `ClarityName` and building an `AssetIdentifier` from it with no reservation of `"*"` as a disallowed/sentinel-only value [5](#0-4) . Downstream, `check_allowances` builds `ft_allowances`/`nft_allowances` maps keyed by the exact `AssetIdentifier` (contract + asset_name) supplied by the user [6](#0-5) , then during the per-asset check phase it *also* looks up `AssetIdentifier{contract, ClarityName::from_literal("*")}` as an implicit "wildcard covers this contract" entry and merges it in alongside any exact-name match [7](#0-6) [8](#0-7) . Because both the exact-name lookup and the wildcard lookup use identical keys when the real asset happens to be named `"*"`, there is no way to distinguish "allowance for the specific token `*`" from "allowance for all tokens in this contract" — they are the same map entry.

The equality broken: a post-condition/allowance amount that a user attaches to one specific named asset ends up being applied to (and thus permitting movement of) every other asset in the same contract, i.e. `assets actually restricted != assets user intended to restrict`.

### Impact Explanation
This breaks the "asset moving past its post-conditions" guarantee that `restrict-assets?`/`as-contract?` (and, by extension, any code relying on the wildcard semantics) is supposed to enforce: a contract call wrapped in `restrict-assets? tx-sender ((with-ft .vault "*" u10)) ...` intended to cap transfers of the literally-named token `*` to 10 units can, if `.vault` also defines other fungible tokens, allow those other tokens to move up to the same "10-unit" ceiling per allowance entry — i.e., assets that were never covered by any allowance in the user's intent are treated as covered. This falls under the "asset moving past its post-conditions" Critical-severity category defined in the rules, since the sender explicitly scoped protection to one asset and the runtime permits movement of unrelated assets under that scope.

### Likelihood Explanation
Exploitability requires only that a contract (which the attacker can freely author, e.g. a "vault" contract that a victim calls via `restrict-assets?`) define an asset literally named `*`, which the naming grammar permits. Any victim who writes an allowance list intending an exact match against an asset that happens to be named `*` (or who is tricked/social-engineered into using `"*"` believing it addresses a specific token) is affected. This is a straightforward on-chain contract deployment plus a normal `restrict-assets?`/`as-contract?` call by a victim — no privileged access, miner cooperation, or race condition needed, so likelihood is moderate-to-high wherever a token is named `*` and a user tries to write an exact-match allowance for it.

### Recommendation
Reserve `"*"` at the allowance-parsing layer (`eval_allowance`, `AllowanceWithFt`/`AllowanceWithNft` cases) so it can never be used to construct a real, exact-match `AssetIdentifier` — either reject `"*"` outright as an asset name at token-definition time, or represent the wildcard as a distinct `Allowance` variant/sentinel type that cannot collide with a real `ClarityName`, rather than overloading the literal string value used for both real names and the "match all assets in contract" semantics.

### Proof of Concept
1. Deploy contract `.vault` that defines two fungible tokens: `(define-fungible-token * )` and `(define-fungible-token payout)`, minting both to some principal.
2. Victim calls, via another contract or directly:
```
(restrict-assets? tx-sender ((with-ft .vault "*" u10))
  (try! (ft-transfer? payout u1000000 tx-sender attacker))
)
```
3. In `check_allowances`, the FT-movement check for asset `payout` looks up `ft_allowances.get(&AssetIdentifier{contract: .vault, asset_name: "payout"})` (no exact entry) and then `ft_allowances.get(&AssetIdentifier{contract: .vault, asset_name: "*"})`, which *does* exist because the user's `with-ft "*" u10"` allowance was stored under that exact key [2](#0-1) . The transfer of `payout` is checked against the `u10`-limited wildcard-matching allowance the user believed applied only to the token named `*`, illustrating that an allowance scoped by the user to one asset silently governs a different asset because of the key collision.

(Note: I was not able to execute this scenario in a live node/test harness within this investigation; the analysis is based on static code reading of `clarity/src/vm/functions/post_conditions.rs` and `clarity-types/src/representations.rs`. A concrete Rust/Clarity unit test replicating the steps above should be added to `clarity/src/vm/tests/post_conditions.rs` to confirm runtime behavior.)

### Citations

**File:** clarity-types/src/representations.rs (L51-70)
```rust
    pub static ref CLARITY_NAME_REGEX_STRING: String =
        "^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$".into();
    pub static ref CLARITY_NAME_REGEX: Regex =
    {
        Regex::new(CLARITY_NAME_REGEX_STRING.as_str()).unwrap()
    };
    pub static ref CONTRACT_NAME_REGEX: Regex =
    {
        Regex::new(format!("^{}$|^__transient$", CONTRACT_NAME_REGEX_STRING.as_str()).as_str())
            .unwrap()
    };
}

guarded_string!(
    ClarityName,
    CLARITY_NAME_REGEX,
    MAX_STRING_LEN,
    ClarityTypeError,
    ClarityTypeError::InvalidClarityName
);
```

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

**File:** clarity/src/vm/functions/post_conditions.rs (L540-551)
```rust
            Allowance::Ft(ft) => {
                ft_allowances
                    .entry(ft.asset)
                    .or_default()
                    .push((i, ft.amount));
            }
            Allowance::Nft(nft) => {
                let (_, vec) = nft_allowances
                    .entry(nft.asset)
                    .or_insert_with(|| (i, Vec::new()));
                vec.extend(nft.asset_ids);
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
