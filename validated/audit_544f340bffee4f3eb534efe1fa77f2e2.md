### Title
FT/NFT post-condition wildcard collides with a literally-named `*` asset, letting an allowed asset bypass its stated post-condition amount - (File: `clarity/src/vm/functions/post_conditions.rs`)

### Summary
`CLARITY_NAME_REGEX_STRING` permits `*` as a standalone valid `ClarityName` (`"^[-+=/*]$"` branch), so a contract author can legally `define-fungible-token *` (or a NFT named `*`). [1](#0-0)  `check_allowances` in `restrict-assets?`/`as-contract?` uses the literal asset name `"*"` as a sentinel meaning "any asset in this contract" and merges it into every other asset's allowance lookup for the same contract. [2](#0-1) [3](#0-2) 

### Finding Description
`AllowanceWithFt`/`AllowanceWithNft` allow the caller to name any specific `(contract, asset-name)` pair, with no restriction preventing `asset-name` from being the string `"*"`. [4](#0-3)  Later, when validating what actually moved, `check_allowances` builds a `merged` list per moved asset by looking up (a) the exact `AssetIdentifier` and (b) an `AssetIdentifier` with the same contract but `asset_name = "*"`, treating the latter as a wildcard covering the contract's other tokens:
```
if let Some(wildcard_vec) = ft_allowances.get(&AssetIdentifier {
    contract_identifier: asset.contract_identifier.clone(),
    asset_name: ClarityName::from_literal("*"),
}) {
    merged.extend(wildcard_vec.iter().cloned());
}
``` [5](#0-4) 
The same pattern exists for NFTs. [6](#0-5) 

Because the code never distinguishes "the user's declared allowance happens to be for a token that is really named `*`" from "the user meant a wildcard for everything in the contract," a contract that defines an actual fungible or non-fungible token whose Clarity name is the single character `*` breaks the intended equality between the declared post-condition and what is allowed to move:

- A user writes `(restrict-assets? tx-sender ((with-ft 'SP...contract "*" u10)) ...)` intending to permit spending up to `u10` of the specific token literally named `*`.
- Inside `check-allowances`, this allowance is stored under the key `{contract, asset_name: "*"}` in `ft_allowances`.
- For every *other* fungible token defined in that same contract (e.g. `usda`, `wrapped-btc`), the wildcard lookup at line 608–613 finds this same entry and merges it in as if it were a blanket allowance, permitting up to `u10` movement of any/every FT in that contract — even ones the user never listed and never intended to authorize — silently expanding the post-condition's scope.
- Conversely, and more critically, a malicious contract can define a token named `*` specifically to be picked up by any wildcard-style allowance a user is not even aware exists for that contract, since `ft_allowances` is keyed purely from post-condition data, not from the tokens moved: the wildcard branch is exercised any time *any* allowance entry named `*` exists for that contract, independent of what asset actually triggered it.

This breaks the "asset moving past its post-conditions" equality: `check_transaction_postconditions`/`check_allowances` are supposed to guarantee that only assets and amounts explicitly covered by the sender's declared allowances can move; the `"*"`-name collision allows an asset that was not individually covered (or covered only for a different, unintended token) to pass the check.

### Impact Explanation
This falls under "an asset moving past its post-conditions" (Critical impact per the rules): a Clarity contract call protected by `restrict-assets?`/`as-contract?` can move a fungible or non-fungible asset that the caller's allowance list did not intend to cover, because the reserved wildcard sentinel `"*"` collides with a legitimately nameable asset. An unprivileged contract author/attacker only needs to name a token `*` in a contract that a victim interacts with using `restrict-assets?`/`as-contract?` guards; no miner, signer, or admin privilege is required.

### Likelihood Explanation
Likelihood is moderate: it requires (1) a contract to define an asset literally named `*` (fully within an unprivileged contract deployer's control, since `"*"` is grammatically valid per `CLARITY_NAME_REGEX_STRING`), and (2) a caller to use `with-ft`/`with-nft` allowances against that contract via `restrict-assets?`/`as-contract?`. Given SIP-040 post-conditions and `restrict-assets?`/`as-contract?` are newly introduced mechanisms in this codebase (per the module doc comment referencing epoch gating for these checks) [7](#0-6) , and wallets/users will naturally write per-asset allowances, this is a realistically reachable condition without needing any privileged actor.

### Recommendation
Reserve the `"*"` `ClarityName` so it cannot be used as an actual asset name (validate in `define-fungible-token`/`define-non-fungible-token` and/or in `AssetIdentifier` construction), or use a dedicated non-`ClarityName` sentinel type/flag for "wildcard" allowances instead of overloading the `asset_name` field with a value that is also a legal token name. Additionally, only apply the wildcard branch when the allowance was explicitly constructed as a wildcard allowance (e.g., a distinct `Allowance::FtWildcard` variant) rather than by re-using the literal string `"*"` as a magic value indistinguishable from a real asset name.

### Proof of Concept
1. Deploy a contract `evil.clar` that defines two fungible tokens in the same contract: `(define-fungible-token * u1000000)` and `(define-fungible-token usda u1000000)`, with a public function that mints/transfers `usda` on behalf of `tx-sender`.
2. Victim wallet calls this contract wrapped in:
   `(restrict-assets? tx-sender ((with-ft 'SP...evil "*" u10)) (contract-call? 'SP...evil transfer-usda u100000 tx-sender recipient))`
   intending only to authorize up to `u10` of the (assumed harmless/unused) `*` token.
3. During evaluation, `check_allowances` records the allowance under key `{evil, asset_name:"*"}`. [8](#0-7) 
4. When checking the `usda` transfer that actually occurred, the FT-movement loop looks up `{evil, asset_name:"*"}` as a wildcard and merges its `u10` allowance in as coverage for `usda`. [9](#0-8)  If the amount transferred is `<= u10` (or, in a variant of this attack, the wildcard-covered token could instead have a large allowance intentionally set to cover many low-value tokens but inadvertently authorize a high-value one), the transfer succeeds despite the caller only having intended to authorize movement of the specific `*`-named token, not `usda`. The post-condition check silently passes, and a transfer of an asset never explicitly authorized by name is accepted.

**Note on completeness:** I was not able to execute this scenario in a live VM to observe the concrete pass/fail behavior at runtime (no execution environment available here); the analysis is based on static reading of `check_allowances`, `eval_allowance`, and the `ClarityName` grammar. It would be worth having a Devin session run the Clarity test suite (`clarity/src/vm/tests/post_conditions.rs`) with an added test defining an asset literally named `*` to confirm the bypass concretely.

### Citations

**File:** clarity-types/src/representations.rs (L51-52)
```rust
    pub static ref CLARITY_NAME_REGEX_STRING: String =
        "^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$".into();
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

**File:** clarity/src/vm/functions/post_conditions.rs (L540-545)
```rust
            Allowance::Ft(ft) => {
                ft_allowances
                    .entry(ft.asset)
                    .or_default()
                    .push((i, ft.amount));
            }
```

**File:** clarity/src/vm/functions/post_conditions.rs (L598-625)
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
```

**File:** clarity/src/vm/functions/post_conditions.rs (L629-642)
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
```

**File:** crates/stacks-transactions/src/lib.rs (L29-34)
```rust
//! variants and modes not yet activated in the current epoch, before execution;
//! [`check_transaction_postconditions`] compares the declared post-conditions
//! against the [`AssetMap`] of what actually moved, after execution. The latter
//! runs in every epoch, so it does not subsume the former. Together they need
//! only the codec post-condition types, an [`AssetMap`], the origin principal
//! and the epoch.
```
