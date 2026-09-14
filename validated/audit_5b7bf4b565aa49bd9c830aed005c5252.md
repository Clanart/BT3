### Title
Unvalidated `Offer.driver_dict` Allows Forged Asset Identity in Wallet Trade Acceptance - ([File: chia/wallet/trade_manager.py])

### Summary
`Offer` is a `Streamable` whose `driver_dict: dict[bytes32, PuzzleInfo]` field is attacker-controlled wire data supplied by the offer-maker/counterparty (deserialized directly via `Offer.from_bytes`/`from_bech32`, not derived from re-inspecting the actual `CoinSpend.puzzle_reveal`s in the bundle). This is directly analogous to the reported laravel-mediable issue: a client-supplied "type" declaration (there: MIME type; here: `PuzzleInfo`/asset-type metadata) is trusted for downstream logic instead of being verified against the real content (there: file bytes; here: the coin's actual puzzle reveal).

### Finding Description
`Offer.__post_init__` only checks that every requested-payment asset id has *some* entry in `driver_dict`; it never validates that a `driver_dict` entry actually matches the real puzzle/asset identity present in `self._bundle.coin_spends` [1](#0-0) .

When a taker calls `respond_to_offer`, the untrusted `offer.driver_dict` is passed straight through as the trusted `driver_dict` argument to `_create_offer_for_ids` [2](#0-1) . Inside `_create_offer_for_ids`, the code only cross-checks `driver_dict[asset_id]` against the wallet's own `get_puzzle_info(asset_id)` **when a local wallet for that asset already exists** (`wallet is not None`) [3](#0-2) . For an asset the taker does not yet have a wallet for (the common case when accepting an offer for a new/unknown CAT, NFT, or other asset), this verification branch is skipped entirely, so the attacker-declared `PuzzleInfo` (asset "type", CAT tail hash, ownership/singleton metadata, etc.) is accepted unchecked.

That unverified `driver_dict` entry is then used as ground truth to:
- construct the settlement puzzle hash used to build/verify announcements (`Offer.calculate_announcements` → `construct_puzzle(driver_dict[asset_id], OFFER_MOD)`) [4](#0-3) ,
- construct completion spends in `to_valid_spend` [5](#0-4) ,
- and — most significantly — to create a brand-new local wallet via `maybe_create_wallets_for_offer`, which calls `wsm.create_wallet_for_puzzle_info(offer.driver_dict[key])` keyed purely by the attacker's claimed driver info with no cross-check against the real on-chain puzzle for that coin [6](#0-5) .

This mirrors the CVE pattern exactly: a piece of attacker-supplied metadata describing "what this content/asset is" is trusted instead of the content itself, enabling type confusion. Because the driver-info dictionary also feeds `GetOfferSummary`/wallet UI (`OfferSummary.infos`) which is populated from `driver_dict`, a maker can misrepresent the CAT tail / asset type shown to the taker for the *requested* side of an offer, in addition to poisoning what asset record gets created for the *offered* side once accepted.

### Impact Explanation
If the actual settlement coin's real puzzle hash (computed from the genuine `match_puzzle()` result over `coin_spend.puzzle_reveal`) doesn't match what `driver_dict` claims, CLVM/consensus-level puzzle-hash checks in `to_valid_spend`/mempool admission would typically catch a *complete* mismatch when submitting the completion spend — this bounds worst-case direct fund loss. However, the forged asset identity still corrupts local wallet state and user-facing displays: a taker's wallet can be made to instantiate/label an incoming asset under attacker-chosen `PuzzleInfo` (wrong CAT tail / asset "brand", wrong ownership metadata), and offer summaries can misrepresent requested-asset identity before acceptance. This is a forged-asset-identity condition reachable purely from an untrusted, attacker-crafted offer file/bech32 string processed by `take_offer`/`get_offer_summary`, with no signature or on-chain state required to trigger the mismatched-trust window.

### Likelihood Explanation
High reachability: any wallet user who receives an offer (a `.offer` file or bech32 string) from an untrusted counterparty and calls `get_offer_summary` or `take_offer` runs this path. Constructing a raw `Offer` with a `driver_dict` entry that doesn't correspond to the real coin spends requires only crafting/serializing the `Streamable` fields directly (bypassing the normal `create_offer_for_ids`/`from_spend_bundle` construction helpers that do use `match_puzzle`), which is straightforward for anyone who understands the wire format.

### Recommendation
In `Offer.__post_init__` (or in `check_offer_validity`/`respond_to_offer` before using `driver_dict`), re-derive each asset's `PuzzleInfo` by running `match_puzzle()` over the corresponding `CoinSpend.puzzle_reveal` in `self._bundle`/`get_offered_coins()`, and reject the offer (or overwrite the field) if the supplied `driver_dict[asset_id]` does not exactly equal the driver recovered from the real puzzle. Apply the same "recomputed-not-trusted" rule in `maybe_create_wallets_for_offer` before calling `create_wallet_for_puzzle_info`.

### Proof of Concept
1. Construct two coin spends for a genuine settlement (`OFFER_MOD`) coin whose actual puzzle reveal, when uncurried via `match_puzzle`, is plain XCH/settlement (no CAT layer) or is a CAT with tail `T_real`.
2. Serialize an `Offer` object directly (bypassing `_create_offer_for_ids`/`from_spend_bundle`) with a `driver_dict` entry claiming `{"type": "CAT", "tail": "0x" + T_fake.hex()}` for that asset id (or a different NFT/ownership metadata than what actually exists).
3. Encode via `Offer.compress()`/`to_bech32()` and hand the resulting offer string to a victim.
4. Call `check_offer_validity`/`get_offer_summary` on the victim's wallet: the returned `OfferSummary.infos` will report the attacker-chosen `PuzzleInfo`, not the real one, because no verification step recomputes it from `coin_spend.puzzle_reveal`.
5. If the victim calls `take_offer`, and the asset id is not one they already track (`get_wallet_for_asset_id` returns `None`), `_create_offer_for_ids` skips the `driver_dict` vs. `wallet.get_puzzle_info` cross-check entirely, and `maybe_create_wallets_for_offer` creates a local wallet using the forged `PuzzleInfo`.

Note: I was not able to fully trace every downstream consumer of the newly created wallet (e.g., whether later legitimate CAT-identification sync logic in `wallet_state_manager.determine_coin_type`/`CATWallet.identify` would eventually correct or re-validate the forged tail on subsequent sync), so I cannot claim with certainty that this always results in **persistent** forged identity rather than a transient display/UX corruption corrected on next sync. This uncertainty should be resolved by a deeper trace of `create_wallet_for_puzzle_info` and its interaction with `determine_coin_type` during a background Devin session, since the wiki/Ask context here does not include full file contents for `wallet_state_manager.create_wallet_for_puzzle_info`.

### Citations

**File:** chia/wallet/trading/offer.py (L132-148)
```python
    def calculate_announcements(
        notarized_payments: dict[bytes32 | None, list[NotarizedPayment]],
        driver_dict: dict[bytes32, PuzzleInfo],
    ) -> list[AssertPuzzleAnnouncement]:
        announcements: list[AssertPuzzleAnnouncement] = []
        for asset_id, payments in notarized_payments.items():
            if asset_id is not None:
                if asset_id not in driver_dict:
                    raise ValueError("Cannot calculate announcements without driver of requested item")
                settlement_ph: bytes32 = construct_puzzle(driver_dict[asset_id], OFFER_MOD).get_tree_hash()
            else:
                settlement_ph = OFFER_MOD_HASH

            msg: bytes32 = Program.to((payments[0].nonce, [p.as_condition_args() for p in payments])).get_tree_hash()
            announcements.append(AssertPuzzleAnnouncement(asserted_ph=settlement_ph, asserted_msg=msg))

        return announcements
```

**File:** chia/wallet/trading/offer.py (L150-160)
```python
    def __post_init__(self) -> None:
        # Verify that there are no duplicate payments
        for payments in self.requested_payments.values():
            payment_programs: list[bytes32] = [p.name() for p in payments]
            if len(set(payment_programs)) != len(payment_programs):
                raise ValueError("Bundle has duplicate requested payments")

        # Verify we have a type for every kind of asset
        for asset_id in self.requested_payments:
            if asset_id is not None and asset_id not in self.driver_dict:
                raise ValueError("Offer does not have enough driver information about the requested payments")
```

**File:** chia/wallet/trading/offer.py (L565-595)
```python
                    solution: Program = solve_puzzle(
                        self.driver_dict[asset_id],
                        Solver(
                            {
                                "coin": "0x"
                                + coin.parent_coin_info.hex()
                                + coin.puzzle_hash.hex()
                                + uint64(coin.amount).stream_to_bytes().hex(),
                                "parent_spend": "0x" + bytes(coin_to_spend_dict[coin]).hex(),
                                "siblings": siblings,
                                "sibling_spends": sibling_spends,
                                "sibling_puzzles": sibling_puzzles,
                                "sibling_solutions": sibling_solutions,
                                **solver.info,
                            }
                        ),
                        OFFER_MOD,
                        Program.to(coin_to_solution_dict[coin]),
                    )
                else:
                    solution = Program.to(coin_to_solution_dict[coin])

                completion_spends.append(
                    make_spend(
                        coin,
                        construct_puzzle(self.driver_dict[asset_id], OFFER_MOD) if asset_id else OFFER_MOD,
                        solution,
                    )
                )

        return WalletSpendBundle.aggregate([WalletSpendBundle(completion_spends, G2Element()), self._bundle])
```

**File:** chia/wallet/trade_manager.py (L570-585)
```python
                if asset_id is not None and wallet is not None:  # if this asset is not XCH
                    if callable(getattr(wallet, "get_puzzle_info", None)):
                        assert isinstance(wallet, (CATWallet, DataLayerWallet, NFTWallet))
                        puzzle_driver: PuzzleInfo = await wallet.get_puzzle_info(asset_id)
                        if asset_id in driver_dict and driver_dict[asset_id] != puzzle_driver:
                            # ignore the case if we're an nft transferring the did owner
                            if self.check_for_owner_change_in_drivers(puzzle_driver, driver_dict[asset_id]):
                                driver_dict[asset_id] = puzzle_driver
                            else:
                                raise ValueError(
                                    f"driver_dict specified {driver_dict[asset_id]}, was expecting {puzzle_driver}"
                                )
                        else:
                            driver_dict[asset_id] = puzzle_driver
                    else:
                        raise ValueError(f"Wallet for asset id {asset_id} is not properly integrated with TradeManager")
```

**File:** chia/wallet/trade_manager.py (L670-678)
```python
    async def maybe_create_wallets_for_offer(self, offer: Offer) -> None:
        for key in offer.arbitrage():
            wsm = self.wallet_state_manager
            if key is None:
                continue
            # ATTENTION: new_wallets
            exists = await wsm.get_wallet_for_puzzle_info(offer.driver_dict[key])
            if exists is None:
                await wsm.create_wallet_for_puzzle_info(offer.driver_dict[key])
```

**File:** chia/wallet/trade_manager.py (L860-868)
```python
            result = await self._create_offer_for_ids(
                take_offer_dict,
                inner_action_scope,
                offer.driver_dict,
                solver,
                fee=fee,
                extra_conditions=extra_conditions,
                taking=True,
            )
```
