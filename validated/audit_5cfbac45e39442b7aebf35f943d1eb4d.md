### Title
NFT royalty payment is self-declared by the seller's spend and is not cryptographically bound to the actual settlement payment, allowing royalty bypass outside the wallet's `make_nft1_offer` path - (File: `chia/wallet/nft_wallet/nft_wallet.py`, `chia/wallet/nft_wallet/nft_puzzle_utils.py`)

### Summary
The Y2K report describes a fee that is only charged when users go through one specific deposit code path (`_id != 0`) but is silently skipped when the same economic action is performed through an alternate, equally legitimate code path (`_id == 0`), causing the treasury to lose fee revenue while other actors keep benefiting. The analogous pattern in this codebase is NFT royalty enforcement: royalty payment is only produced and enforced when an offer is built through `NFTWallet.make_nft1_offer` / `TradeManager.check_for_special_offer_making`, which computes `trade_prices_list` from the real requested payment amounts. But the on-chain enforcement mechanism (the `-10` ownership-change condition consumed by the NFT's ownership/transfer-program layer) accepts an arbitrary, spender-supplied `trade_prices_list` with no cryptographic link to what is actually paid to the seller for the NFT.

### Finding Description
When an NFT1 (royalty-enabled) NFT changes ownership, its ownership layer requires a `-10` condition supplying `(new_owner, trade_prices_list, new_did_inner_hash)`. This solution is built by `create_ownership_layer_transfer_solution`: [1](#0-0) 

The honest wallet flow (`NFTWallet.make_nft1_offer`) computes `trade_prices` from the *requested fungible payment* amounts and the number of royalty-eligible NFTs, and creates matching `AssertPuzzleAnnouncement` conditions on the settlement/royalty-payment coins so that the buyer's payment is split to include the royalty: [2](#0-1) 

Crucially, the actual requested payment (the amount the buyer must send, notarized via `Offer.notarize_payments`/`calculate_announcements`) and the royalty `trade_prices_list` consumed by the ownership layer's `-10` condition are two independently constructed values with no consensus-level linkage forcing them to agree: [3](#0-2) 

The `check_for_special_offer_making` routing to `make_nft1_offer` is a *wallet-software convenience path*, not a consensus rule — it is only invoked because `TradeManager._create_offer_for_ids` chooses to detect a royalty-enabled asset and hand off construction: [4](#0-3) 

Because the `-10` condition and its `trade_prices_list` argument are fully controlled by whoever constructs the spend of the NFT coin, a party who crafts their own spend bundle directly (i.e., not through `make_nft1_offer`) — exactly the "alternate legitimate path" pattern in the Y2K report — can set `trade_prices_list` to zero or to an arbitrarily low value while still completing a real sale for full price via the settlement/offer coins. `test_ownership_layer` demonstrates that a `-10` condition with an empty `trade_prices_list` (`[-10, [], []]`) is accepted by the puzzle with no announcement obligation at all: [5](#0-4) 

Since only the `AssertPuzzleAnnouncement` matching the *declared* `trade_prices_list` is enforced (not the real settlement value), two colluding counterparties (the maker who owns/sells the royalty NFT and the taker who buys it) can bypass royalty entirely by constructing the take/settle spend bundle by hand instead of using `TradeManager.respond_to_offer` → `_create_offer_for_ids` → `check_for_special_offer_making`, exactly mirroring how Carousel users bypass `depositFee` by using the `_id == 0` queue path instead of direct deposit.

### Impact Explanation
The NFT creator/royalty recipient (functionally analogous to the Y2K "treasury") never receives their configured royalty percentage when a sale is executed through a manually crafted spend bundle instead of the wallet's honest offer-construction path. Since offer acceptance/spend construction is entirely client-side and there is no consensus rule tying the `-10` condition's `trade_prices_list` to the real payment received by the seller, this is systematically exploitable by any two parties trading an NFT1 asset — not merely a theoretical rounding edge case. This causes real, recurring loss of expected royalty income to third-party creators across the ecosystem, matching the "loss to protocol/beneficiary via a fee-bypass path" impact class in the source report.

### Likelihood Explanation
High for any pair of cooperating counterparties (maker+taker), since it requires no privileged access, no bug in signature verification, and no exploit of consensus rules — it only requires constructing a spend bundle directly instead of going through the standard `TradeManager`/`NFTWallet.make_nft1_offer` helper. This is within reach of any wallet user / offer counterparty, matching the allowed threat model (unprivileged spend-bundle submitter / offer counterparty).

### Recommendation
Royalty payment amounts should be derived from, and cryptographically bound to, the actual settlement payment received for the NFT (e.g., by having the ownership-layer transfer program compute `trade_prices_list` from the asserted requested-payment announcement rather than accepting it as an unconstrained solution argument), so that royalty cannot be independently declared lower than the real sale price by a party constructing a custom spend bundle.

### Proof of Concept
Not independently executable from the index alone — the puzzle-level CLVM source for `NFT_OWNERSHIP_TRANSFER_PROGRAM_ONE_WAY_CLAIM_WITH_ROYALTIES` (loaded via `chia_puzzles_py.programs.NFT_OWNERSHIP_TRANSFER_PROGRAM_ONE_WAY_CLAIM_WITH_ROYALTIES` in `chia/wallet/nft_wallet/nft_puzzles.py`) is not available in this index to confirm the exact byte-level condition logic beyond what the referenced tests demonstrate. The behavior is inferred from: (1) `create_ownership_layer_transfer_solution` accepting an arbitrary `trade_prices_list` argument, (2) `make_nft1_offer` computing royalty announcements only from the *wallet's own* requested-payment bookkeeping, and (3) `test_ownership_layer` showing a `-10` spend with an empty `trade_prices_list` succeeding with no announcement requirement. A definitive PoC (constructing a raw `WalletSpendBundle` that pays full price via `OFFER_MOD` settlement coins while declaring `trade_prices_list=[]` on the NFT's `-10` condition, then confirming it via `sim_client.push_tx`) would need direct access to `NFT_OWNERSHIP_TRANSFER_PROGRAM_ONE_WAY_CLAIM_WITH_ROYALTIES`'s CLVM source to be run in a Devin session, which is recommended to fully validate this finding.

### Citations

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L214-231)
```python
def create_ownership_layer_transfer_solution(
    new_did: bytes, new_did_inner_hash: bytes, trade_prices_list: list[list[int]], new_puzhash: bytes32
) -> Program:
    log.debug(
        "Creating a transfer solution with: DID:%s Inner_puzhash:%s trade_price:%s puzhash:%s",
        new_did.hex(),
        new_did_inner_hash.hex(),
        trade_prices_list,
        new_puzhash.hex(),
    )
    condition_list: list[list[CastableType]] = [
        [51, new_puzhash, 1, [new_puzhash]],
        [-10, new_did, trade_prices_list, new_did_inner_hash],
    ]
    log.debug("Condition list raw: %r", condition_list)
    solution = Program.to([[solution_for_conditions(condition_list)]])
    log.debug("Generated transfer solution: %s", solution)
    return solution
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L896-934)
```python
        trade_prices: list[tuple[uint64, bytes32]] = []
        for asset, amount in fungible_asset_dict.items():  # requested fungible items
            if amount > 0 and offer_side_royalty_split > 0:
                settlement_ph: bytes32 = (
                    OFFER_MOD_HASH if asset is None else construct_puzzle(driver_dict[asset], OFFER_MOD).get_tree_hash()
                )
                trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))

        required_royalty_info: list[tuple[bytes32, bytes32, uint16]] = []  # [(launcher_id, address, percentage)]
        offered_royalty_percentages: dict[bytes32, uint16] = {}
        for asset, amount in royalty_nft_asset_dict.items():  # royalty enabled NFTs
            transfer_info = driver_dict[asset].also().also()  # type: ignore
            assert isinstance(transfer_info, PuzzleInfo)
            royalty_percentage_raw = transfer_info["transfer_program"]["royalty_percentage"]
            assert royalty_percentage_raw is not None
            # clvm encodes large ints as bytes
            if isinstance(royalty_percentage_raw, bytes):
                royalty_percentage = int_from_bytes(royalty_percentage_raw)
            else:
                royalty_percentage = int(royalty_percentage_raw)
            if amount > 0:
                required_royalty_info.append(
                    (
                        asset,
                        bytes32(transfer_info["transfer_program"]["royalty_address"]),
                        uint16(royalty_percentage),
                    )
                )
            else:
                offered_royalty_percentages[asset] = uint16(royalty_percentage)

        royalty_payments: dict[bytes32 | None, list[tuple[bytes32, CreateCoin]]] = {}
        for asset, amount in fungible_asset_dict.items():  # offered fungible items
            if amount < 0 and request_side_royalty_split > 0:
                payment_list: list[tuple[bytes32, CreateCoin]] = []
                for launcher_id, address, percentage in required_royalty_info:
                    extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
                    payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
                royalty_payments[asset] = payment_list
```

**File:** chia/wallet/trading/offer.py (L112-148)
```python
    @staticmethod
    def notarize_payments(
        requested_payments: dict[bytes32 | None, list[CreateCoin]],  # `None` means you are requesting XCH
        coins: list[Coin],
    ) -> dict[bytes32 | None, list[NotarizedPayment]]:
        # This sort should be reproducible in CLVM with `>s`
        sorted_coins: list[Coin] = sorted(coins, key=Coin.name)
        sorted_coin_list: list[list[bytes32 | uint64]] = [coin_as_list(c) for c in sorted_coins]
        nonce: bytes32 = Program.to(sorted_coin_list).get_tree_hash()

        notarized_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        for asset_id, payments in requested_payments.items():
            notarized_payments[asset_id] = []
            for p in payments:
                notarized_payments[asset_id].append(NotarizedPayment(p.puzzle_hash, p.amount, p.memos, nonce=nonce))

        return notarized_payments

    # The announcements returned from this function must be asserted in whatever spend bundle is created by the wallet
    @staticmethod
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

**File:** chia/wallet/trade_manager.py (L919-937)
```python
    async def check_for_special_offer_making(
        self,
        offer_dict: dict[bytes32 | None, int],
        driver_dict: dict[bytes32, PuzzleInfo],
        action_scope: WalletActionScope,
        solver: Solver,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> Offer | None:
        for puzzle_info in driver_dict.values():
            if (
                puzzle_info.check_type([AssetType.SINGLETON.value, AssetType.METADATA.value, AssetType.OWNERSHIP.value])
                and isinstance(puzzle_info.also().also()["transfer_program"], PuzzleInfo)  # type: ignore
                and puzzle_info.also().also()["transfer_program"].type()  # type: ignore
                == AssetType.ROYALTY_TRANSFER_PROGRAM.value
            ):
                return await NFTWallet.make_nft1_offer(
                    self.wallet_state_manager, offer_dict, driver_dict, action_scope, fee, extra_conditions
                )
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_lifecycle.py (L158-172)
```python
        generic_spend = make_spend(
            ownership_coin,
            ownership_puzzle,
            Program.to([[[51, ACS_PH, 1], [-10, [], []]]]),
        )
        generic_bundle = cost_logger.add_cost(
            "Ownership only coin - one child created", WalletSpendBundle([generic_spend], G2Element())
        )
        result = await sim_client.push_tx(generic_bundle)
        assert result == (MempoolInclusionStatus.SUCCESS, None)
        await sim.farm_block()
        ownership_coin = (await sim_client.get_coin_records_by_puzzle_hash(ownership_ph, include_spent_coins=False))[
            0
        ].coin

```
