Confirmed: the `CLARITY_NAME_REGEX_STRING` regex `^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$` in `clarity-types/src/representations.rs:52` explicitly allows the bare, single-character name `"*"` as a valid `ClarityName` (it matches the `^[-+=/*]$` alternative). This makes a real, developer-defined fungible/non-fungible token name of literally `"*"` a legal `define-fungible-token`/`define-non-fungible-token` identifier. `check_allowances` in `clarity/src/vm/functions/post_conditions.rs:608-613` and `637-642` uses that exact literal string as its internal wildcard sentinel to mean "any asset name in this contract," with no separate flag distinguishing "the user wrote a literal asset named `*`" from "the user wrote the wildcard."

### Title
`restrict-assets?`/`as-contract?` wildcard sentinel `"*"` collides with a legitimately named asset, letting unrelated fungible/non-fungible tokens move past declared allowances - (File: clarity/src/vm/functions/post_conditions.rs)

### Summary
`check_allowances` in `clarity/src/vm/functions/post_conditions.rs` implements the SIP-040-style asset-guard primitives `restrict-assets?` / `as-contract?`. A caller declares per-asset allowances with `with-ft`/`with-nft`, each keyed by an `AssetIdentifier{contract_identifier, asset_name}`. To support "allow any asset in this contract," the implementation reserves the literal string `"*"` as a sentinel `asset_name` and, for every asset actually moved, additionally looks it up under `AssetIdentifier{contract, asset_name: "*"}` [1](#0-0) . Because `ClarityName`'s validation regex permits the single character `"*"` as a legal token name [2](#0-1) , a token contract can legitimately define a fungible or non-fungible asset whose real name is `"*"`. An allowance the author intends to apply narrowly to that one specific token (`(with-ft contract-id "*" 100)`) is indistinguishable, at the `AssetIdentifier` level, from the wildcard-all-assets allowance, and is stored in the very same `ft_allowances`/`nft_allowances` map slot used by the wildcard lookup [3](#0-2) .

### Finding Description
This is a direct analog of the Spartan `Pool.mintSynth` bug class: a caller-supplied identifier that is supposed to scope authorization to one specific asset is instead accepted at face value and silently reinterpreted by the checker as authorization for a broader set of assets, breaking the equality between "what the allowance author intended to authorize" and "what actually gets authorized."

Concretely:
- `eval_allowance` for `NativeFunctions::AllowanceWithFt`/`AllowanceWithNft` builds an `AssetIdentifier` directly from the user-supplied `asset_name` string with no reservation or rejection of the sentinel value `"*"` [4](#0-3) [5](#0-4) .
- `check_allowances` indexes all declared allowances by this `AssetIdentifier`, so an allowance for the real asset literally named `"*"` lands in the exact same hashmap bucket that the wildcard-lookup logic queries for every other asset under that contract [6](#0-5) .
- When checking actual FT/NFT movement, the code always probes both the exact-match key and the synthetic `"*"`-named key for the same `contract_identifier` [7](#0-6) [8](#0-7) .

Result: a token-contract author (or an attacker deploying a malicious multi-asset SIP-010/SIP-009 implementation) can define an ordinary-looking asset whose name is `"*"`. Any Clarity contract that uses `restrict-assets?`/`as-contract?` with `(with-ft <that-contract> "*" <small-amount>)`, intending to bound just that one asset, unknowingly also authorizes unlimited movement (up to the same amount, or effectively unbounded since the attacker controls which other asset names exist and their amounts) of every *other* fungible or non-fungible asset defined under that same `contract_identifier` — assets the allowance author never intended to expose and may not even know exist.

### Impact Explanation
This breaks the "asset moving past its post-conditions" guarantee that `restrict-assets?`/`as-contract?` exists to enforce — the Critical impact category listed as in-scope. A contract that relies on `as-contract?` to safely interact with a caller-specified or lower-trust token contract (a common DeFi pattern, directly mirroring the multi-pool/multi-synth trust model in the reference report) can have assets it never authorized siphoned out from under its guard, because the checker cannot distinguish "wildcard" from "a real asset literally named `*`" — both collapse to the same lookup key.

### Likelihood Explanation
Exploitation requires only that an attacker control a token contract (trivial — anyone can deploy a SIP-010/SIP-009-style contract with `define-fungible-token *` or `define-non-fungible-token *`, since `"*"` passes `CLARITY_NAME_REGEX`), and that some victim contract calls `restrict-assets?`/`as-contract?` with a `with-ft`/`with-nft` allowance naming that asset by its literal `"*"` name (e.g., a generic helper that names allowances after tokenomics conventions, or a contract that queries a token's `get-token-name`-style value and happens to encounter `"*"`). This is a specification/implementation ambiguity rather than a rare edge case, since nothing in the type checker (`clarity/src/vm/analysis/type_checker/v2_1/natives/post_conditions.rs`) rejects `"*"` as an asset name for `with-ft`/`with-nft`.

### Recommendation
Reserve the wildcard sentinel out-of-band from the `ClarityName` value space used for real asset names — e.g., represent "all assets in this contract" as a distinct `Allowance` variant (as `Allowance::All` already does for "all assets everywhere") rather than as a magic `ClarityName` literal that collides with the legal namespace of user-defined asset names. Alternatively, reject `"*"` as a valid `asset-name` argument to `with-ft`/`with-nft` at the type-checker level (`clarity/src/vm/analysis/type_checker/v2_1/natives/post_conditions.rs`), forcing wildcard intent to be expressed only through a dedicated syntax.

### Proof of Concept
1. Deploy a token contract `evil-token.clar` that defines two fungible tokens under the same `contract_identifier`: `(define-fungible-token * u1000000)` (the "decoy") and `(define-fungible-token real-payout u1000000)` (the asset the attacker actually wants to drain).
2. Get a victim contract that uses `(as-contract? ((with-ft 'SP...evil-token "*" u100)) (contract-call? 'SP...evil-token transfer ...))`, intending to cap movement of the decoy `*`-named token to 100 units.
3. From within the guarded body (or via a callback the victim contract triggers on `evil-token`), transfer an arbitrary amount of `real-payout` out of the guarded principal.
4. `check_allowances` computes, for the `real-payout` asset moved, `ft_allowances.get(asset)` → no exact entry, then `ft_allowances.get(&AssetIdentifier{contract, asset_name:"*"})` → finds the victim's `100`-unit allowance meant only for the decoy `*` token, and permits the `real-payout` movement up to that limit instead of rejecting it as "no allowance for this asset" [9](#0-8) .

### Citations

**File:** clarity/src/vm/functions/post_conditions.rs (L180-203)
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

            let amount =
                eval(&rest[2], exec_state, invoke_ctx, context)?.clone_with_cost(exec_state)?;
            let amount = amount
                .expect_u128()
                .map_err(|_| VmInternalError::Expect("Expected u128".into()))?;

            Ok(Allowance::Ft(FtAllowance { asset, amount }))
```

**File:** clarity/src/vm/functions/post_conditions.rs (L226-249)
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

            let asset_id_list =
                eval(&rest[2], exec_state, invoke_ctx, context)?.clone_with_cost(exec_state)?;
            let asset_ids = asset_id_list
                .expect_list()
                .map_err(|_| VmInternalError::Expect("Expected list".into()))?;

            Ok(Allowance::Nft(NftAllowance { asset, asset_ids }))
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

**File:** clarity/src/vm/functions/post_conditions.rs (L637-642)
```rust
            if let Some((index, allowance_vec)) = nft_allowances.get(&AssetIdentifier {
                contract_identifier: asset.contract_identifier.clone(),
                asset_name: ClarityName::from_literal("*"),
            }) {
                merged.push((*index, allowance_vec));
            }
```

**File:** clarity-types/src/representations.rs (L51-52)
```rust
    pub static ref CLARITY_NAME_REGEX_STRING: String =
        "^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$".into();
```
