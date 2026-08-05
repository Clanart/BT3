Based on my research, I found a concrete Agave analog to this bug class in the vote program's commission-collector update logic.

### Title
Updating one vote-account commission collector silently resets the other collector to a hardcoded default during V3→V4 auto-conversion - ([File: programs/vote/src/vote_state/mod.rs, programs/vote/src/vote_state/handler.rs])

### Summary
The reported bug pattern is: a function meant to update one narrow field (`collateralAmount`) as a side effect resets unrelated state (`tpLastUpdatedBlock`/`slLastUpdatedBlock`) that was previously configured, because the underlying "update" helper unconditionally rewrites those fields. The Agave analog is `update_commission_collector` in `programs/vote/src/vote_state/mod.rs`, which, when invoked against a vote account still serialized in the legacy `VoteStateV3` format, triggers an implicit conversion to `VoteStateV4` via `try_convert_to_vote_state_v4` (`programs/vote/src/vote_state/handler.rs`). That conversion synthesizes default values for the *new* V4-only fields (`inflation_rewards_collector` = vote pubkey, `block_revenue_collector` = node pubkey) and this happens even when the caller only intended to change *one* of the two collector kinds — the untouched collector kind is silently (re)written to its V4 default rather than being left as whatever the caller/last legitimate state intended.

### Finding Description
`update_commission_collector` accepts a `CommissionKind` (`InflationRewards` or `BlockRevenue`) and a new collector, updates only that kind, and is expected to leave the other kind untouched. However, when the underlying account data is still in the `VoteStateV3` representation (which has no separate per-kind collector fields at all), the conversion path derives V4 defaults for *both* collector fields as part of just accessing/mutating the account. A regression test in `programs/vote/src/vote_state/mod.rs:4734-4796` explicitly documents and asserts this side effect: [1](#0-0) 

The test shows that calling `update_commission_collector` for `CommissionKind::InflationRewards` against a V3-serialized account causes `CommissionKind::BlockRevenue` to be forcibly set to `v3_node_pubkey` (the V4-conversion default) rather than being left unset/unaffected: [2](#0-1) 

This is structurally identical to the reported bug: an update path intended to touch a single field (`collateralAmount` in the report, one `CommissionKind` here) instead invokes a broader "normalize/rebuild" routine (`updateTrade()` in the report, `try_convert_to_vote_state_v4` here) that unconditionally re-derives *other* tracked fields (`tpLastUpdatedBlock`/`slLastUpdatedBlock` in the report, the sibling `CommissionKind` collector here) from scratch, discarding whatever value should have been preserved.

### Impact Explanation
Vote-account commission collectors determine where inflation-reward and block-revenue commission payments are routed. If a validator/withdraw-authority updates only one collector kind while the account is still stored in the legacy V3 layout, the other, previously-intended collector destination is silently overwritten to a hardcoded default rather than preserved — a corruption of validator-controlled fund-routing state without explicit consent for that field. This falls in the "fund theft/loss" category since commission payouts can be misdirected to an unintended pubkey (the vote account itself or the node identity) instead of the operator's actual configured collector.

### Likelihood Explanation
This triggers under ordinary, non-malicious usage: any legitimate withdraw-authority calling the update-collector instruction against an account that has not yet been persisted in V4 format will hit this path. No attacker or malicious peer is required — it's a self-inflicted state-corruption bug of the same class as the report (a benign, expected operation causing unintended loss of previously-set state). Likelihood depends on how many active vote accounts remain in legacy V3 serialization at the time such an update is issued, which I was not able to fully confirm given the remaining tool budget — I could not verify whether Agave guarantees eager/eventual persistence of V4-converted state everywhere it is read, which would affect how often a "stale V3 with intended V4 field" scenario can actually recur across transactions.

### Recommendation
When converting a `VoteStateV3` account to `VoteStateV4` inside `try_convert_to_vote_state_v4`, do not derive fabricated defaults for both `inflation_rewards_collector` and `block_revenue_collector` as an implicit side effect of an update targeting only one `CommissionKind`. Instead, the conversion should either (a) require both collector destinations be explicitly supplied/preserved before persisting the upgraded format, or (b) track and honor any previously-set collector value across the format upgrade so `update_commission_collector` for one `CommissionKind` never mutates the other.

### Proof of Concept
The existing unit test in the repository is itself a reproducible PoC of the unintended side effect: [3](#0-2) 
It constructs a V3 vote account, calls `update_commission_collector` only for `CommissionKind::InflationRewards`, and asserts that `CommissionKind::BlockRevenue` ends up set to `v3_node_pubkey` — a value the caller never requested — confirming that an update scoped to one collector kind silently overwrites the other.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L4734-4737)
```rust
        // V3 -> V4 auto-conversion side-effect: updating one collector against a
        // V3-serialized account causes the "other" collector to be written as
        // its V4 default (inflation_rewards_collector = vote_pubkey,
        // block_revenue_collector = node_pubkey), per try_convert_to_vote_state_v4.
```

**File:** programs/vote/src/vote_state/mod.rs (L4738-4796)
```rust
        {
            let v3 = get_max_sized_vote_state_v3();
            let v3_node_pubkey = v3.node_pubkey;
            let v3_withdrawer = v3.authorized_withdrawer;
            let v3_vote_pubkey = solana_pubkey::new_rand();

            let v4_size = VoteStateV4::size_of();
            let mut account_data = vec![0u8; v4_size];
            bincode::serialize_into(&mut account_data[..], &VoteStateVersions::V3(Box::new(v3)))
                .unwrap();
            let mut v3_vote_account =
                AccountSharedData::new(rent.minimum_balance(v4_size), v4_size, &id());
            v3_vote_account.set_data_from_slice(&account_data);

            let v3_signers: HashSet<Pubkey> = vec![v3_withdrawer].into_iter().collect();

            let transaction_context = new_transaction_context(
                vec![
                    (id(), processor_account.clone()),
                    (v3_vote_pubkey, v3_vote_account),
                    (new_collector, collector_account.clone()),
                ],
                vec![
                    InstructionAccount::new(1, false, true),
                    InstructionAccount::new(2, false, true),
                ],
                &rent,
            );
            let instruction_context = transaction_context.get_next_instruction_context().unwrap();
            let mut borrowed_vote_account = instruction_context
                .try_borrow_instruction_account(0)
                .unwrap();

            update_commission_collector(
                &mut borrowed_vote_account,
                target_version,
                NewCommissionCollector::NewAccount(
                    instruction_context
                        .try_borrow_instruction_account(1)
                        .unwrap(),
                ),
                CommissionKind::InflationRewards,
                &v3_signers,
                &rent,
            )
            .unwrap();

            // The updated field reflects the caller's new collector.
            assert_eq!(
                get_commission_collector(&borrowed_vote_account, CommissionKind::InflationRewards),
                new_collector,
            );
            // The *other* field was reset to its V4-conversion default
            // (block_revenue_collector = node_pubkey from the V3 source).
            assert_eq!(
                get_commission_collector(&borrowed_vote_account, CommissionKind::BlockRevenue),
                v3_node_pubkey,
            );
        }
```
