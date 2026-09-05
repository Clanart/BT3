### Title
Wildcard `with-ft`/`with-nft` allowance applies the same fixed cap independently per distinct asset instead of as a shared budget, letting a callee define many token identifiers to move value far past what `restrict-assets?`/`as-contract?` protection was meant to bound - (File: clarity/src/vm/functions/post_conditions.rs)

### Summary
`restrict-assets?` and `as-contract?` let a contract author bound the value that can leave `asset-owner` while executing a body, e.g. before calling into an unknown/untrusted contract [1](#0-0) . The `with-ft contract-id "*" amount` allowance is documented to apply "to all FTs defined in `contract-id`" so an author doesn't have to enumerate every token name a callee might define [2](#0-1) . However, the enforcement in `check_allowances` treats the wildcard `amount` as a per-distinct-asset cap, re-applied independently to each different `AssetIdentifier` that moved, rather than as a single shared budget across all of them. A callee that defines/mints many distinct fungible tokens can therefore each stay under the wildcard threshold individually while the aggregate value moved from the protected owner vastly exceeds the amount the author intended to risk.

### Finding Description
`check_allowances` in `clarity/src/vm/functions/post_conditions.rs` iterates over every distinct asset identifier the owner moved via `assets.get_all_fungible_tokens(owner)`, which returns one entry per `AssetIdentifier` [3](#0-2) . For each asset, it merges the exact-name allowance entries with the wildcard (`asset_name == "*"`) entries for the same contract, and independently checks `amount_moved > allowance` for that single asset: [4](#0-3) 

This means a `with-ft .callee "*" u100` allowance does not bound "at most 100 units of value can leave the owner via `.callee`'s tokens" — it bounds "at most 100 units of *each distinct token type* defined by `.callee` can leave the owner." If `.callee` is untrusted code (deployable by any unprivileged sender) that defines `N` distinct fungible tokens and transfers up to `u100` of each from the protected owner, every individual check passes (`100 > 100` is false), yet the owner's real aggregate outflow is `100 * N`, unbounded by the number of tokens the callee chooses to define. The same per-asset (not aggregated) enforcement applies to the NFT wildcard path as well, since `nft_allowances` is keyed by `AssetIdentifier` in the same way [5](#0-4) .

The violated equality is: the post-condition/allowance mechanism is supposed to guarantee "assets moved by the protected principal are bounded by the allowances declared before the protected body executes." Because the cap is a single hardcoded value applied independently per asset identifier (directly analogous to `MultiFlowPump`'s single `LOG_MAX_INCREASE`/`LOG_MAX_DECREASE` applied uniformly across all tokens in a pool), a callee that controls how many distinct assets it defines can make the real aggregate outflow diverge arbitrarily from what a single `with-ft ... "*" amount` allowance was meant to express.

### Impact Explanation
This lets code invoked from inside `as-contract?`/`restrict-assets?` (which is precisely the mechanism meant to safely interact with less-trusted code) move funds past the protection the calling contract's author put in place, mapping to the "an asset moving past its post-conditions" Critical impact category. Any unprivileged party who can get their contract called from inside such a protected block (a very common pattern for interacting with third-party token/DEX contracts) can multiply their effective allowance by the number of distinct fungible/non-fungible asset types they define.

### Likelihood Explanation
The wildcard allowance feature exists specifically to make it convenient to interact with contracts whose exact token names are not known/trusted in advance — i.e., the exact scenario where the caller does not control what assets the callee defines. This makes the divergence between "per-asset cap" and "the author's intended aggregate cap" readily reachable in normal usage of `with-ft "*"`/`with-nft "*"`, not a contrived edge case. However, whether this is a "bug" versus a documented/intended semantic (the docs say the allowance "applies to all FTs," which is technically true of the per-asset re-application) is ambiguous from the available documentation and tests, which only exercise single-asset scenarios with the wildcard [6](#0-5) . I was not able to find any design note or changelog clarifying whether the "per-asset, non-cumulative" semantics for the wildcard was a deliberate choice versus an oversight of the risk model.

### Recommendation
Clarify the intended semantics of the wildcard allowance, and if a shared/aggregate budget across multiple distinct assets is intended, sum `amount_moved` across all assets matched by a given wildcard allowance entry before comparing to `amount`, rather than checking each asset independently against the same threshold. If per-asset semantics are intentional, the documentation in `clarity/src/vm/docs/mod.rs` should be updated to explicitly warn that the wildcard amount is a per-token-type cap, not a total cap, so contract authors do not mistakenly rely on it to bound total exposure to an unknown number of token types defined by a callee.

### Proof of Concept
Conceptual PoC (requires a Devin session with the full test/build environment to confirm, since this repo's index does not let me execute Clarity code):
1. Define a malicious contract `.evil` with two distinct fungible tokens, `token-a` and `token-b`, each transferable from the caller.
2. Have a victim contract call `(as-contract? ((with-ft .evil "*" u100)) (contract-call? .evil "drain" ...))`, intending to risk at most `u100` total.
3. `.evil`'s `drain` function transfers `u100` of `token-a` and `u100` of `token-b` from `tx-sender`/`current-contract`.
4. Per `check_allowances` (`clarity/src/vm/functions/post_conditions.rs:598-626`), both transfers pass individually (`100 > 100` is false for each), so the call succeeds with `(ok ...)`, even though the victim's aggregate outflow is `u200`, double the intended cap. Extending `.evil` to define `N` tokens scales the bypass to `100 * N`.

### Citations

**File:** clarity/src/vm/docs/mod.rs (L2934-2942)
```rust
const ALLOWANCE_WITH_STX: SpecialAPI = SpecialAPI {
    input_type: "uint",
    snippet: "with-stx ${1:amount}",
    output_type: "Allowance",
    signature: "(with-stx amount)",
    description: "Adds an outflow allowance for `amount` uSTX from the
`asset-owner` of the enclosing `restrict-assets?` or `as-contract?`
expression. `with-stx` is not allowed outside of `restrict-assets?` or
`as-contract?` contexts.",
```

**File:** clarity/src/vm/docs/mod.rs (L2961-2967)
```rust
    description: r#"Adds an outflow allowance for `amount` of the fungible
token defined in `contract-id` with name `token-name` from the `asset-owner`
of the enclosing `restrict-assets?` or `as-contract?` expression.  `with-ft` is
not allowed outside of `restrict-assets?` or `as-contract?` contexts. Note that
`token-name` should match the name used in the `define-fungible-token` call in
the contract. When `"*"` is used for the token name, the allowance applies to
**all** FTs defined in `contract-id`."#,
```

**File:** clarity-types/src/effects/asset_map.rs (L398-404)
```rust
    /// Returns all fungible token transfers by `principal`.
    pub fn get_all_fungible_tokens(
        &self,
        principal: &PrincipalData,
    ) -> Option<&HashMap<AssetIdentifier, u128>> {
        self.token_map.get(principal)
    }
```

**File:** clarity/src/vm/functions/post_conditions.rs (L514-529)
```rust
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

**File:** clarity/src/vm/tests/post_conditions.rs (L1109-1211)
```rust
#[test]
fn test_restrict_assets_with_ft_wildcard_exceeds() {
    let snippet = r#"
(define-fungible-token stackaroo)
(ft-mint? stackaroo u200 tx-sender)
(let ((recipient 'SP000000000000000000002Q6VF78))
  (restrict-assets? tx-sender ((with-ft current-contract "*" u10))
    (try! (ft-transfer? stackaroo u50 tx-sender recipient))
  )
)"#;
    let expected = Value::error(Value::UInt(0)).unwrap();
    assert_eq!(expected, execute(snippet).unwrap().unwrap());
}

#[test]
fn test_restrict_assets_with_ft_wildcard_other_allowances() {
    let snippet = r#"
(define-fungible-token stackaroo)
(ft-mint? stackaroo u200 tx-sender)
(let ((recipient 'SP000000000000000000002Q6VF78))
  (restrict-assets? tx-sender
    (
      (with-stx u200)
      (with-ft .other "*" u100) ;; other contract, same token name
      (with-ft current-contract "other" u100) ;; same contract, different token name
      (with-nft .token "*" (list 123))
    )
    (try! (ft-transfer? stackaroo u50 tx-sender recipient))
  )
)"#;
    let expected = Value::error(Value::UInt(MAX_ALLOWANCES as u128)).unwrap();
    assert_eq!(expected, execute(snippet).unwrap().unwrap());
}

#[test]
fn test_restrict_assets_with_ft_wildcard_multiple_allowances_both_low() {
    let snippet = r#"
(define-fungible-token stackaroo)
(ft-mint? stackaroo u200 tx-sender)
(let ((recipient 'SP000000000000000000002Q6VF78))
  (restrict-assets? tx-sender ((with-ft current-contract "*" u30) (with-ft current-contract "*" u20))
    (try! (ft-transfer? stackaroo u40 tx-sender recipient))
  )
)"#;
    let expected = Value::error(Value::UInt(0)).unwrap();
    assert_eq!(expected, execute(snippet).unwrap().unwrap());
}

#[test]
fn test_restrict_assets_with_ft_wildcard_multiple_allowances_both_ok() {
    let snippet = r#"
(define-fungible-token stackaroo)
(ft-mint? stackaroo u200 tx-sender)
(let ((recipient 'SP000000000000000000002Q6VF78))
  (restrict-assets? tx-sender ((with-ft current-contract "*" u300) (with-ft current-contract "*" u200))
    (try! (ft-transfer? stackaroo u40 tx-sender recipient))
  )
)"#;
    let expected = Value::okay_true();
    assert_eq!(expected, execute(snippet).unwrap().unwrap());
}

#[test]
fn test_restrict_assets_with_ft_wildcard_multiple_allowances_one_low() {
    let snippet = r#"
(define-fungible-token stackaroo)
(ft-mint? stackaroo u200 tx-sender)
(let ((recipient 'SP000000000000000000002Q6VF78))
  (restrict-assets? tx-sender ((with-ft current-contract "*" u100) (with-ft current-contract "*" u20))
    (try! (ft-transfer? stackaroo u40 tx-sender recipient))
  )
)"#;
    let expected = Value::error(Value::UInt(1)).unwrap();
    assert_eq!(expected, execute(snippet).unwrap().unwrap());
}

#[test]
fn test_restrict_assets_with_ft_wildcard_multiple_allowances_low1() {
    let snippet = r#"
(define-fungible-token stackaroo)
(ft-mint? stackaroo u200 tx-sender)
(let ((recipient 'SP000000000000000000002Q6VF78))
  (restrict-assets? tx-sender ((with-ft current-contract "*" u20) (with-ft current-contract "stackaroo" u20))
    (try! (ft-transfer? stackaroo u40 tx-sender recipient))
  )
)"#;
    let expected = Value::error(Value::UInt(0)).unwrap();
    assert_eq!(expected, execute(snippet).unwrap().unwrap());
}

#[test]
fn test_restrict_assets_with_ft_wildcard_multiple_allowances_low2() {
    let snippet = r#"
(define-fungible-token stackaroo)
(ft-mint? stackaroo u200 tx-sender)
(let ((recipient 'SP000000000000000000002Q6VF78))
  (restrict-assets? tx-sender ((with-ft current-contract "stackaroo" u20) (with-ft current-contract "*" u20))
    (try! (ft-transfer? stackaroo u40 tx-sender recipient))
  )
)"#;
    let expected = Value::error(Value::UInt(0)).unwrap();
    assert_eq!(expected, execute(snippet).unwrap().unwrap());
}
```
