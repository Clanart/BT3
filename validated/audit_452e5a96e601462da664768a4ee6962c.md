Confirmed: `with-ft` and `with-nft` take a user-supplied `(string-ascii 128)` token name with no restriction against the literal value `"*"`, which is exactly what `check_allowances` uses internally as a "match all assets in this contract" wildcard sentinel when merging allowance lists.

### Title
Post-condition wildcard sentinel collision lets a crafted asset name bypass `restrict-assets?`/`as-contract?` allowances - (File: clarity/src/vm/functions/post_conditions.rs)

### Summary
`check_allowances` builds per-asset lookup keys and, for every FT/NFT actually moved, additionally probes the allowance map under a synthetic key with `asset_name: ClarityName::from_literal("*")` to detect a "wildcard" allowance for the whole contract [1](#0-0) . This "*" value is not a distinguished internal token — the type checker for `with-ft`/`with-nft` only requires a `(string-ascii 128)` value for the asset-name argument, so an ordinary Clarity author can legally pass the literal string `"*"` as a real asset name [2](#0-1) .

### Finding Description
`restrict-assets?`/`as-contract?` allowances are parsed into `AssetIdentifier { contract_identifier, asset_name }` keys with no reservation of the `"*"` name for any special engine-only allowance form [3](#0-2) . When enforcing allowances, `check_allowances` does two lookups per moved FT (and NFT): an exact-match lookup on the real asset identifier, and a second lookup using a synthetically constructed key that reuses the *same* asset's `contract_identifier` but hardcodes `asset_name` to `"*"` [4](#0-3) . Because there is no separate namespace/tag distinguishing "the wildcard sentinel" from "a real asset legitimately named `*`", any allowance a caller writes as `(with-ft contract-id "*" amount)` is indistinguishable from an intended "cover every FT in `contract-id`" allowance — the engine cannot tell whether the author meant to restrict movement of a token literally named `*`, or unintentionally created a blanket allowance for the whole contract. If the target contract mints/transfers multiple distinct fungible or non-fungible token types, a single `(with-ft contract-id "*" N)` allowance silently authorizes movement of *any* of those other asset types up to `N`, even though the author declared no allowance for them.

This breaks the equality the allowance mechanism exists to enforce: "assets moved under `restrict-assets?`/`as-contract?` must not exceed the specific allowances declared for those specific assets." An asset with **zero** declared allowance is treated as within-bounds because its movement is masked by an unrelated `"*"`-named allowance, i.e. it moves past its post-conditions.

### Impact Explanation
This is a within-runtime asset/post-condition-escape bug, not a network-level fork: any Clarity code protected by `restrict-assets?`/`as-contract?` and relying on the completeness of its declared FT/NFT allowances can have those allowances silently expanded to every fungible/non-fungible asset in a given contract merely because that contract also happens to define (or an attacker crafts a contract to define) an asset literally named `"*"`. Since `restrict-assets?`/`as-contract?` are the exact mechanism SIP-introduced post-conditions rely on to bound "as-contract" or "trusted-call" asset exposure, this is a genuine "asset moving past its post-conditions" condition as scoped by the rules.

### Likelihood Explanation
Requires only an unprivileged contract deployer to name one of their fungible/non-fungible tokens `"*"` (a legal `(string-ascii 128)` value, unrestricted by `check_allowance_with_ft`/`check_allowance_with_nft`) [5](#0-4) , and for some other contract's `restrict-assets?`/`as-contract?` call to declare a `with-ft`/`with-nft` allowance naming that same asset. No signer/miner/admin privilege is needed to create the colliding contract.

### Recommendation
Reserve the wildcard sentinel outside the space of names a user asset can take (e.g. use a dedicated `Allowance` variant/flag instead of overloading `AssetIdentifier.asset_name == "*"`), or reject `"*"` as an asset name in `check_allowance_with_ft`/`check_allowance_with_nft` at parse time, so a real contract-defined asset can never collide with the internal wildcard key used in `check_allowances`.

### Proof of Concept
1. Deploy `token-x.clar` defining two SIP-010 fungible tokens under the same contract: one legitimately named `foo`, and one named literally `*` (a valid `(string-ascii 128)` value).
2. Deploy a "victim" contract that does:
```
(as-contract?
  ((with-ft 'SP...token-x "*" u10))
  (contract-call? 'SP...token-x transfer-foo u1000 tx-sender))
```
intending to only allow the `*`-named token to move up to `u10`.
3. Because `check_allowances` probes `ft_allowances.get(&AssetIdentifier { contract_identifier: token-x, asset_name: "*" })` for *every* FT moved from `token-x` (including `foo`), the `transfer-foo` call moving `1000` `foo` tokens is treated as authorized by the `"*"` allowance instead of being rejected for having no allowance, per [4](#0-3) .

### Citations

**File:** clarity/src/vm/functions/post_conditions.rs (L159-196)
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

```

**File:** clarity/src/vm/functions/post_conditions.rs (L598-626)
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
```

**File:** clarity/src/vm/analysis/type_checker/v2_1/natives/post_conditions.rs (L214-243)
```rust
/// Type check a `with-ft` allowance expression.
/// `(with-ft contract-id:principal token-name:(string-ascii 128) amount:uint)`
fn check_allowance_with_ft(
    checker: &mut TypeChecker,
    args: &[SymbolicExpression],
    context: &TypingContext,
) -> Result<bool, StaticCheckError> {
    check_argument_count(3, args)?;

    checker.type_check_expects(
        args.first()
            .ok_or(StaticCheckErrorKind::CheckerImplementationFailure)?,
        context,
        &TypeSignature::PrincipalType,
    )?;
    checker.type_check_expects(
        args.get(1)
            .ok_or(StaticCheckErrorKind::CheckerImplementationFailure)?,
        context,
        &TypeSignature::STRING_ASCII_128,
    )?;
    checker.type_check_expects(
        args.get(2)
            .ok_or(StaticCheckErrorKind::CheckerImplementationFailure)?,
        context,
        &TypeSignature::UIntType,
    )?;

    Ok(false)
}
```
