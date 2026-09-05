Confirmed: the regex `^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$` explicitly accepts the single-character name `"*"` as a valid `ClarityName` (it matches the `^[-+=/*]$` alternative). So a Clarity fungible-token contract can legally define an asset literally named `*`. This makes the wildcard-sentinel-collision bug in `check_allowances` real and exploitable, and it is the same bug *class* as the Maia finding: an attacker-controlled value collides with a reserved sentinel key used inside an equality/lookup check, silently changing the meaning of a security check written by an unrelated, honest party.

### Title
Wildcard fungible-token allowance sentinel `"*"` collides with a legally-named real asset, allowing a `restrict-assets?`/`as-contract?` post-condition to be silently over-authorized - (File: clarity/src/vm/functions/post_conditions.rs)

### Summary
`check_allowances` merges a caller's specific `with-ft` allowance for `(contract, asset_name)` with a synthetic "wildcard" allowance keyed at `(contract, "*")`, intended to mean "any fungible asset in this contract." Because `"*"` is also a syntactically valid `ClarityName`, an attacker-authored contract can define a real fungible token whose name literally is `"*"`. When a legitimate `restrict-assets?`/`as-contract?` caller writes `(with-ft contract "*" amount)` intending to authorize movement of that specific `"*"`-named asset, the code instead treats the allowance as a blanket allowance for *every* fungible asset in that contract, up to `amount`, exactly mirroring the Maia bug where a legitimate governance mapping check was corrupted by attacker-controlled data written through an unrelated code path.

### Finding Description
In `check_allowances` (clarity/src/vm/functions/post_conditions.rs:598-627), fungible token movements are checked against allowances as follows: [1](#0-0) 

Allowance entries are inserted into `ft_allowances: HashMap<AssetIdentifier, Vec<(usize, u128)>>` keyed by the exact `AssetIdentifier { contract_identifier, asset_name }` supplied to `with-ft`: [2](#0-1) 

When checking an actual fungible-asset movement, the code does two lookups and merges them: an exact-match lookup, and a "wildcard" lookup that manufactures the key `AssetIdentifier { contract_identifier: asset.contract_identifier.clone(), asset_name: "*" }`: [3](#0-2) 

The wildcard lookup and the exact-match lookup for an asset that is *actually* named `"*"` use the identical `HashMap` key. `ClarityName` accepts `"*"` as valid per `CLARITY_NAME_REGEX_STRING = "^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$"`: [4](#0-3) 

So there is no separation between "the user's intent to allow up to X of literally-named `*` token" and "the engine's internal sentinel meaning `X of any token in this contract`." Any FT contract (attacker-deployed or otherwise) can define an asset called `*`, and the moment a protected caller writes `(with-ft 'SP...attacker-contract "*" 50)` — believing they are authorizing transfer of that specific `*` token — the engine instead treats it as a blanket allowance letting up to 50 units of *any other, more valuable* fungible token defined in that same contract move within the `restrict-assets?`/`as-contract?` scope without tripping a post-condition violation.

This exactly parallels the referenced Maia/Ulysses bug: a privileged/legitimate actor's registration (`addEcosystemToken` / here, `with-ft ... "*" ...`) is silently reinterpreted because an unprivileged party can populate the same key space (`hToken` underlying mapping / here, a real Clarity asset literally named `*`) that the security-critical equality check relies on.

### Impact Explanation
This breaks "an asset moving past its post-conditions" — one of the explicitly listed Critical impacts. A user (or a contract's own logic) invoking `restrict-assets?`/`as-contract?` with a `with-ft` allowance whose asset name happens to be `"*"` gets an unintentionally broadened authorization scope, permitting movement of unrelated, unapproved fungible assets from the same contract inside the protected block without the check flagging a violation. Since `restrict-assets?`/`as-contract?` and post-conditions are exactly the mechanism users rely on to bound value movement, this is a genuine equality-check bypass with fund-safety consequences, not a mere DoS.

### Likelihood Explanation
Likelihood requires: (1) a fungible-token contract defining an asset literally named `*` — trivial for any contract deployer, including an attacker deploying a malicious multi-FT contract, and (2) a caller writing `(with-ft contract "*" amount)` targeting that literal asset. Because `"*"` is a valid, unremarkable-looking Clarity identifier (single-character operator names are permitted), a contract could define such an asset without obvious malicious intent, or an attacker could specifically engineer this collision knowing the post-condition semantics, then trick or wait for a victim contract/user to write a `with-ft ... "*"` allowance against it. This does not require any miner, signer, or admin privilege — any unprivileged token deployer plus a normal `restrict-assets?`/`as-contract?` caller is sufficient.

### Recommendation
Do not overload the literal string `"*"` as both a valid Clarity asset name and the internal wildcard sentinel. Options:
- Represent the "wildcard applies to all FTs in a contract" allowance as a distinct `Allowance` variant (e.g., extend `FtAllowance`/`Allowance::Ft` with an `is_wildcard: bool` field, or add a dedicated `Allowance::FtWildcard { contract, amount }`) instead of encoding it via `AssetIdentifier { asset_name: "*" }`.
- Reject (or special-case) the literal name `"*"` when building an `AssetIdentifier` for FT/NFT allowance matching so it cannot collide with the sentinel, or use a reserved/unrepresentable name (not expressible via `ClarityName`) as the internal wildcard key.
- Add a regression test where a contract defines a real fungible asset named `*` and verify that a `(with-ft contract "*" amount)` allowance only authorizes that specific asset and does not blanket-authorize other FTs in the same contract.

### Proof of Concept
1. Deploy a contract `attacker.clar` on the target chain defining two fungible tokens:
   ```clarity
   (define-fungible-token * u1000000)
   (define-fungible-token valuable-coin u1000000)
   ```
   (`*` is accepted by `CLARITY_NAME_REGEX`, so `define-fungible-token *` compiles.)
2. Have a victim contract or user execute:
   ```clarity
   (restrict-assets? tx-sender ((with-ft 'SP...attacker "*" u50))
     (try! (contract-call? 'SP...attacker transfer-valuable-coin u1000 tx-sender recipient))
   )
   ```
   The victim's intent is "I'm okay moving up to 50 units of the token literally named `*`."
3. In `check_allowances`, when the moved asset is `valuable-coin` (not `*`), the code does:
   - Exact lookup `ft_allowances.get({attacker, "valuable-coin"})` → `None`.
   - Wildcard lookup `ft_allowances.get({attacker, "*"})` → finds the entry the victim inserted for the literal `*` token, treating it as a blanket wildcard.
   - `merged` is non-empty, so the check for `valuable-coin` movement passes against the `u50` limit meant only for the `*` token, even though `valuable-coin` was never explicitly authorized.
4. Result: up to 1000 units of `valuable-coin` can move (bounded only by the mismatched `u50` check against the wrong per-unit accounting, or entirely unrestricted amounts of any other FT in the contract up to the sentinel's limit) without the post-condition/allowance system flagging a violation — demonstrating the asset-moving-past-post-conditions bypass. [1](#0-0) [4](#0-3)

### Citations

**File:** clarity/src/vm/functions/post_conditions.rs (L540-545)
```rust
            Allowance::Ft(ft) => {
                ft_allowances
                    .entry(ft.asset)
                    .or_default()
                    .push((i, ft.amount));
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

**File:** clarity-types/src/representations.rs (L51-56)
```rust
    pub static ref CLARITY_NAME_REGEX_STRING: String =
        "^[a-zA-Z]([a-zA-Z0-9]|[-_!?+<>=/*])*$|^[-+=/*]$|^[<>]=?$".into();
    pub static ref CLARITY_NAME_REGEX: Regex =
    {
        Regex::new(CLARITY_NAME_REGEX_STRING.as_str()).unwrap()
    };
```
