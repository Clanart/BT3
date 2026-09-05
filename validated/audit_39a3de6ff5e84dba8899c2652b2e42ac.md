### Title
Wildcard `with-ft` allowance in `restrict-assets?`/`as-contract?` is applied per-asset instead of in aggregate, letting assets move past their declared allowance - (File: `clarity/src/vm/functions/post_conditions.rs`)

### Summary
The reported bug is a Multipool deposit-slippage issue: a check that validates two amounts (`amount0Min`/`amount1Min`) *independently* can be satisfied even when the combined result is bad for the user, because an attacker can manipulate multiple "tiers" simultaneously so each independent check passes while the aggregate outcome is not what the user intended. The Stacks analog lives in Clarity's `restrict-assets?` / `as-contract?` allowance system: a wildcard fungible-token allowance (`with-ft <contract> "*" <amount>`) is meant to bound how much value can leave the protected principal for *any* fungible token defined in a contract, but `check_allowances` re-applies the same allowance amount independently to every distinct fungible-token asset actually moved, instead of capping the sum across them.

### Finding Description
`check_allowances` in `clarity/src/vm/functions/post_conditions.rs` builds a per-asset "merged" list of allowances (exact match + wildcard `"*"` for the same contract) and then, for the FT case, checks each moved asset independently: [1](#0-0) 

For every distinct `(asset, amount_moved)` pair returned by `assets.get_all_fungible_tokens(owner)`, the code re-looks-up the *same* wildcard entry (`asset_name = "*"`) from `ft_allowances` and checks `amount_moved > allowance` for that asset alone. There is no accumulator that sums `amount_moved` across the different fungible-token asset identifiers that share the wildcard allowance.

Concretely: if a caller writes
```
(with-ft 'SP...my-token-contract "*" u100)
```
intending to authorize "up to 100 units of value may leave this principal via any fungible token defined in `my-token-contract`", and the contract inside `restrict-assets?`/`as-contract?` defines multiple `define-fungible-token`s (e.g. `token-a`, `token-b`, `token-c`), then a protected body that moves 100 of `token-a`, 100 of `token-b`, and 100 of `token-c` will pass the check three times independently (100 ≤ 100 each), even though 300 total units of value left the owner. This is structurally identical to the Multipool bug: two (or more) independent "reserve"/limit checks are individually satisfied while the attacker drives the *combined* effect far beyond what a single limit was meant to bound — just as Bob moves feeTier1 and feeTier2 in opposite directions so each pool's price check passes while the aggregate LP-share outcome is manipulated.

The STX case in the same function is explicitly guarded against this exact class of bug — it computes a `total_stx_change` (movement + burn) and checks it against the allowance in addition to the individual checks: [2](#0-1) 

No equivalent aggregate/summed check exists for the FT wildcard case, confirming the inconsistency is a gap rather than an intended per-asset semantic.

### Impact Explanation
This breaks the equality the allowance mechanism exists to enforce: "assets moved by the protected principal ≤ declared allowance." A `with-ft ... "*" ...` wildcard entry is the mechanism a caller uses to bound total value exposure when delegating execution (via `as-contract?`) or protecting a principal (via `restrict-assets?`) across a call whose exact token movements it cannot fully predict. Because the check is applied per distinct fungible-token identifier rather than summed, a multi-token contract lets the protected body move an unbounded multiple of the declared cap (bounded only by the number of distinct fungible tokens the target contract defines), directly matching the "asset moving past its post-conditions" impact category — asset movement escapes the bound the caller explicitly authorized.

### Likelihood Explanation
Exploitation requires only: (1) a caller using the wildcard `with-ft` allowance form (rather than exact-asset allowances) inside `restrict-assets?`/`as-contract?`, and (2) the called contract defining more than one fungible token. Both are ordinary, unprivileged conditions — no miner/signer/admin access or another account's key is needed, and the attacker only needs to control (or induce a call into) a multi-token contract that the protected body interacts with. Given that `with-ft "*"` is documented specifically to reduce the burden of enumerating every asset in a contract, its use is expected to be common wherever an author wants coarse-grained protection.

### Recommendation
Sum `amount_moved` across all fungible-token assets covered by the same wildcard allowance entry (grouped by allowance index) before comparing against the allowance amount, mirroring the combined-check pattern already used for STX movement + burn in the same function (lines 678-700). Exact-match (non-wildcard) `with-ft` entries can remain per-asset since they name a single asset explicitly, but any allowance keyed by `asset_name = "*"` must be checked against the cumulative amount moved across every asset it matches.

### Proof of Concept
1. Deploy a contract `multi-token.clar` defining three fungible tokens: `token-a`, `token-b`, `token-c`, each with a `transfer` entrypoint that moves tokens out of `tx-sender`.
2. From a principal `P`, execute:
```clarity
(as-contract?
  ((with-ft 'SP...multi-token "*" u100))
  (contract-call? 'SP...multi-token transfer-a u100 tx-sender 'ATTACKER)
  (contract-call? 'SP...multi-token transfer-b u100 tx-sender 'ATTACKER)
  (contract-call? 'SP...multi-token transfer-c u100 tx-sender 'ATTACKER))
```
3. In `check_allowances` (`clarity/src/vm/functions/post_conditions.rs:598-626`), each of `token-a`, `token-b`, `token-c` is checked independently against the same `u100` wildcard allowance; each passes (`100 ≤ 100`), so `Ok(None)` is returned and the body's rollback context is committed.
4. Result: 300 total fungible-token units left the `as-contract?` principal even though the caller declared an allowance of only 100, demonstrating the asset movement escaping the declared post-condition/allowance bound.

### Citations

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

**File:** clarity/src/vm/functions/post_conditions.rs (L678-700)
```rust
    // Check combined STX movements and burns. In epochs that don't support the combined check,
    // this happens after all other checks to ensure that we only need to reach this rejectable
    // error if there are no other errors already reached.
    let total_stx_change = amount_moved
        .unwrap_or(0)
        .checked_add(amount_burned.unwrap_or(0))
        .ok_or(VmInternalError::Expect(
            "STX movement and burn overflowed u128".into(),
        ))?;
    if total_stx_change > 0 {
        for (index, allowance) in &stx_allowances {
            if total_stx_change > *allowance {
                if epoch.handles_with_stx_combined_check() {
                    record_violation(&mut earliest_violation, *index as u128);
                    break;
                } else if earliest_violation.is_none() {
                    return Err(VmExecutionError::Internal(VmInternalError::Expect(
                        "Total STX movement and burn exceeds allowance".into(),
                    )));
                }
            }
        }
    }
```
