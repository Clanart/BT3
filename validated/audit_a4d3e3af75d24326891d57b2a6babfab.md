## Title
Forged NFT royalty metadata in offer `driver_dict` causes silent fund redirection during offer acceptance — ([File: chia/wallet/nft_wallet/nft_wallet.py])

## Summary
The Biswap report shows a migrator trusting a **caller-supplied, unverified contract** ("pair") as the source of truth for token balances, letting the attacker dictate how much of a real asset gets moved and where. The structurally equivalent trust bug in this codebase is `NFTWallet.make_nft1_offer`, which computes royalty payments (real fungible-asset value transferred out) using `driver_dict` entries that are **not validated against the actual on-chain NFT puzzle** when the caller doesn't own that NFT — exactly the scenario that occurs when a wallet accepts (`respond_to_offer`) an untrusted offer crafted by a counterparty.

## Finding Description
`TradeManager.respond_to_offer` passes the counterparty-supplied `offer.driver_dict` straight into `_create_offer_for_ids(..., offer.driver_dict, ...)`. [1](#0-0) 

Before any ownership-based validation of `driver_dict` happens (that validation is a later loop keyed on wallets the *caller* actually owns), `_create_offer_for_ids` calls `check_for_special_offer_making`, which detects a royalty-enabled NFT pattern in `driver_dict` and dispatches straight into `NFTWallet.make_nft1_offer` using the raw, unvalidated `driver_dict`: [2](#0-1) 

Inside `make_nft1_offer`, the royalty address and percentage that determine how much of the *taker's real, offered value* gets peeled off and sent as a royalty payment are read directly out of `driver_dict[asset]` — data supplied by the offer's other party — with no check that it matches the real, immutable transfer-program puzzle actually curried into the on-chain NFT: [3](#0-2) 

That untrusted `(launcher_id, address, percentage)` tuple is then used to build a real `CreateCoin` payment sent from the wallet's own coins: [4](#0-3) 

For an asset ID that the responding wallet does not own, no wallet in `_create_offer_for_ids`'s later validation loop ever calls `get_puzzle_info` for it and compares it to `driver_dict[asset_id]`, because that comparison is only performed for wallets the local party controls (`if asset_id in driver_dict and driver_dict[asset_id] != puzzle_driver`), which is unreachable for an asset the responder is *receiving* rather than spending: [5](#0-4) 

This is the same trust class as the Biswap bug: a peer-supplied, unauthenticated data structure (`FakePair`/`driver_dict`) is treated as an oracle for how much value should move and to whom, instead of being derived from the actual immutable on-chain program.

## Impact Explanation
A malicious offer-maker can embed a `driver_dict` entry for a royalty-enabled NFT with an inflated `royalty_percentage` (up to `MAX_ROYALTY_BASIS_POINTS`) and an attacker-controlled `royalty_address`. When an unwitting taker calls `respond_to_offer` on this offer (e.g. to buy the NFT for an agreed fungible-asset price), `make_nft1_offer` computes and creates an *additional* real payment coin from the taker's own coins to the attacker's address, beyond what the taker believes they agreed to pay. This is a spend-triggered, unauthorized diversion of the taker's real assets to an address chosen entirely by the counterparty — concrete coin-movement theft, not merely a UI display bug, since the extra `CreateCoin` becomes part of the taker's own signed spend bundle.

## Likelihood Explanation
Any wallet user accepting an offer file from an untrusted counterparty (a normal, expected use case for Chia offers, which are commonly shared out-of-band) is exposed. No special network position, plotting, or protocol-level privilege is required — only crafting an offer file with a forged `driver_dict`/`PuzzleInfo` for the NFT asset.

## Recommendation
Before using `driver_dict[asset]`'s royalty fields for assets not owned by the local wallet, validate the driver against the actual on-chain state of that asset (e.g., verify the launcher/NFT puzzle can be reconstructed and matches a real, tracked or independently-fetched coin's puzzle hash before trusting `royalty_address`/`royalty_percentage`), or clearly warn/require explicit user confirmation of royalty terms extracted from untrusted offer data prior to signing.

## Proof of Concept
1. Attacker crafts an `Offer` requesting fungible CAT/XCH payment in exchange for an NFT, embedding `driver_dict[nft_asset_id]` with `also().also()["transfer_program"]["royalty_percentage"]` set high and `["royalty_address"]` set to the attacker's own puzzle hash — this does not need to match the real NFT's actual transfer program.
2. Victim receives the offer file and calls `TradeManager.respond_to_offer(offer, ...)`.
3. `_create_offer_for_ids` → `check_for_special_offer_making` → `NFTWallet.make_nft1_offer` runs using the attacker's `driver_dict` unchecked, computing `required_royalty_info` and generating a real payment `CreateCoin(address, extra_royalty_amount, [address])` from the victim's own coins to the attacker's address, in addition to the agreed offer price. [6](#0-5)

### Citations

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

**File:** chia/wallet/trade_manager.py (L856-868)
```python
        # We need to sandbox the transactions here because we're going to make our own
        async with self.wallet_state_manager.new_action_scope(
            action_scope.config.tx_config, push=False
        ) as inner_action_scope:
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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L904-934)
```python
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
