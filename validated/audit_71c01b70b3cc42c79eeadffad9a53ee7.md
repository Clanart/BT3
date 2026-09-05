No vulnerability found for this question.

**Reasoning:**

`special_stx_transfer` delegates to `stx_transfer_consolidated`, which enforces `Some(from) != invoke_ctx.sender.as_ref()` before any balance mutation — i.e., the `from` principal recorded via `exec_state.global_context.log_stx_transfer(from, amount)` (which feeds the `AssetMap`) is always exactly the current Clarity execution sender (tx-sender or `as-contract` principal), never an arbitrary principal supplied by the nested call. [1](#0-0) 

The committed `AssetMap` records this transfer keyed by that verified `from` principal via `add_stx_transfer` [2](#0-1) , and `check_transaction_postconditions` in Deny mode requires every principal appearing in the `AssetMap`'s STX/fungible/asset table to be covered by an explicit post-condition, or the transaction fails with "was moved by ... but not checked" [3](#0-2) .

So the two sides of the claimed-broken equality — "principal debited by `stx_transfer_consolidated`" and "principal attributed in the `AssetMap` checked against post-conditions" — are identical by construction: the `from` used for the balance debit is the same value passed to `log_stx_transfer`, which is the same key iterated by `check_transaction_postconditions`. A nested contract-call cannot substitute a different principal's funds into `special_stx_transfer` because Clarity's sender-check (`SENDER_IS_NOT_TX_SENDER`) blocks any `from` that isn't the actual invoking principal (tx-sender or the contract itself under `as-contract`). This is exercised by the existing Deny-mode coverage tests, including `test_check_postconditions_originator_mode_coverage` which explicitly asserts Deny mode still requires all moved principals to be covered [4](#0-3) , and the broader STX post-condition test suite covering allow/deny/originator modes [5](#0-4) .

No code path allows a nested contract-call to move STX attributed to a principal other than the actual debited sender, so the invariant "every committed movement == a movement its post-conditions permit" holds under Deny mode.

### Citations

**File:** clarity/src/vm/functions/assets.rs (L139-165)
```rust
    if Some(from) != invoke_ctx.sender.as_ref() {
        return clarity_ecode!(StxErrorCodes::SENDER_IS_NOT_TX_SENDER);
    }

    // loading from/to principals and balances
    exec_state.add_memory(TypeSignature::PrincipalType.size()?.into())?;
    exec_state.add_memory(TypeSignature::PrincipalType.size()?.into())?;
    // loading from's locked amount and height
    // TODO: this does not count the inner stacks block header load, but arguably,
    // this could be optimized away, so it shouldn't penalize the caller.
    exec_state.add_memory(STXBalance::unlocked_and_v1_size as u64)?;
    exec_state.add_memory(STXBalance::unlocked_and_v1_size as u64)?;

    let mut sender_snapshot = exec_state
        .global_context
        .database
        .get_stx_balance_snapshot(from)?;
    if !sender_snapshot.can_transfer(amount)? {
        return clarity_ecode!(StxErrorCodes::NOT_ENOUGH_BALANCE);
    }

    sender_snapshot.transfer_to(to, amount)?;

    exec_state.global_context.log_stx_transfer(from, amount)?;
    exec_state.register_stx_transfer_event(from.clone(), to.clone(), amount, memo.clone())?;
    Ok(Value::okay_true())
}
```

**File:** clarity-types/src/effects/asset_map.rs (L160-169)
```rust
    /// Records an STX transfer by `principal`.
    pub fn add_stx_transfer(
        &mut self,
        principal: &PrincipalData,
        amount: u128,
    ) -> Result<(), AssetMapError> {
        let next_amount = next_amount(self.stx_map.get(principal), amount)?;
        self.stx_map.insert(principal.clone(), next_amount);
        Ok(())
    }
```

**File:** crates/stacks-transactions/src/lib.rs (L328-385)
```rust
    // make sure every asset transferred is covered by a postcondition, if the current mode
    // requires it.
    let asset_map_copy = (*asset_map).clone();
    let mut all_assets_sent = asset_map_copy.to_table();
    for (principal, mut assets) in all_assets_sent.drain() {
        if !enforce_unchecked_assets_for_principal(&principal) {
            continue;
        }
        for (asset_identifier, asset_entry) in assets.drain() {
            match asset_entry {
                AssetMapEntry::Asset(values) => {
                    // this is a NFT
                    if let Some(checked_nft_asset_map) = checked_nonfungible_assets.get(&principal)
                    {
                        if let Some(nfts) = checked_nft_asset_map.get(&asset_identifier) {
                            // each value must be covered
                            for v in values {
                                if !nfts.contains(&v.clone().try_into()?) {
                                    let reason = format!(
                                        "Post-condition check failure: Non-fungible asset {asset_identifier} value {v:?} was moved by {principal} but not checked"
                                    );
                                    return Ok(Some(reason));
                                }
                            }
                        } else {
                            // no values covered
                            let reason = format!(
                                "Post-condition check failure: Non-fungible asset {asset_identifier} was moved by {principal} but not checked"
                            );
                            return Ok(Some(reason));
                        }
                    } else {
                        // no NFT for this principal
                        let reason = format!(
                            "Post-condition check failure: No checks for non-fungible asset {asset_identifier} moved by {principal}"
                        );
                        return Ok(Some(reason));
                    }
                }
                _ => {
                    // This is STX or a fungible token
                    if let Some(checked_ft_asset_ids) = checked_fungible_assets.get(&principal) {
                        if !checked_ft_asset_ids.contains(&asset_identifier) {
                            let reason = format!(
                                "Post-condition check failure: Fungible asset {asset_identifier} was moved by {principal} but not checked"
                            );
                            return Ok(Some(reason));
                        }
                    } else {
                        let reason = format!(
                            "Post-condition check failure: Fungible asset {asset_identifier} was moved by {principal} but not checked"
                        );
                        return Ok(Some(reason));
                    }
                }
            }
        }
    }
```

**File:** crates/stacks-transactions/src/tests.rs (L2286-2295)
```rust
        // sanity check: deny mode still requires all principals to be covered
        (
            false,
            vec![TransactionPostCondition::STX(
                PostConditionPrincipal::Origin,
                FungibleConditionCode::SentEq,
                50,
            )],
            TransactionPostConditionMode::Deny,
        ),
```

**File:** crates/stacks-transactions/src/tests.rs (L3087-3123)
```rust
#[test]
fn test_check_postconditions_stx() {
    let privk = StacksPrivateKey::from_hex(
        "6d430bb91222408e7706c9001cfaeb91b08c2be6d5ac95779ab52c6b431950e001",
    )
    .unwrap();
    let auth = TransactionAuth::from_p2pkh(&privk).unwrap();
    let addr = auth.origin().address_testnet();
    let origin = addr.to_account_principal();
    let _recv_addr = StacksAddress::new(1, Hash160([0xff; 20])).unwrap();

    // stx-transfer for 123 microstx
    let mut stx_asset_map = AssetMap::new();
    stx_asset_map.add_stx_transfer(&origin, 123).unwrap();

    // stx-burn for 123 microstx
    let mut stx_burn_asset_map = AssetMap::new();
    stx_burn_asset_map.add_stx_burn(&origin, 123).unwrap();

    // stx-transfer and stx-burn for a total of 123 microstx
    let mut stx_transfer_burn_asset_map = AssetMap::new();
    stx_transfer_burn_asset_map
        .add_stx_transfer(&origin, 100)
        .unwrap();
    stx_transfer_burn_asset_map
        .add_stx_burn(&origin, 23)
        .unwrap();

    let tests = vec![
        // no post-conditions in allow mode
        (
            true,
            vec![],
            TransactionPostConditionMode::Allow,
            origin.clone(),
        ), // should pass
        // post-conditions on origin in allow mode
```
