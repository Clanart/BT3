### Title
Offer `driver_dict` type-tag confusion causes unhandled exceptions and spend/offer-processing crashes - ([File: chia/wallet/trade_manager.py])

### Summary
The Suricata CVE is a "protocol-change type confusion": a stream/type discriminator changes mid-processing but the parser keeps using assumptions tied to the old type, causing a crash. The Chia analog is `PuzzleInfo`/`driver_dict` handling in the offer subsystem: code branches on a self-declared `type`/`also()` chain (`AssetType`) rather than on the real, validated puzzle structure, and for a class of legitimate "requested" (not locally-owned) offer legs that chain is never cross-checked against an actual puzzle reveal before being trusted.

### Finding Description
`PuzzleInfo` is a thin wrapper over a caller-supplied dict whose only structural guarantee is that a `"type"` key exists at each nesting level [1](#0-0) . Field access (`__getitem__`) does a raw dict lookup and raises `KeyError` for any missing field [2](#0-1) , and `check_type()` only verifies that the sequence of `"type"` tags matches — it does not verify that any other field required by the corresponding outer-puzzle driver (`"metadata"`, `"updater_hash"`, `"transfer_program"`, etc.) is present or well-formed [3](#0-2) .

`TradeManager._create_offer_for_ids` accepts a caller-supplied `driver_dict: dict[bytes32, PuzzleInfo]` directly (e.g. from the `create_offer_for_ids` RPC / `CreateOfferForIDs`). For "requested" legs (`amount > 0`) it never validates or overwrites the caller's `PuzzleInfo` against the wallet's own understanding of the asset [4](#0-3) . Validation against `wallet.get_puzzle_info(asset_id)` only happens for "offered" legs, and even then only when a local wallet for that `asset_id` exists (`wallet is not None`) [5](#0-4) . When no local wallet exists for a requested asset (the common case for e.g. a maker requesting an NFT/DID/CR-CAT they don't own), the attacker-controlled `PuzzleInfo` is used completely unchecked.

That unchecked `PuzzleInfo` then drives type-dispatch logic in multiple places purely from its declared `type`/`also()` chain:
- `TradeManager.check_for_special_offer_making` dispatches to `NFTWallet.make_nft1_offer` or `DataLayerWallet.make_update_offer` based on `check_type([...])` [6](#0-5) .
- `TradeManager.check_for_final_modifications`/`check_for_requested_payment_modifications` dispatch to DataLayer or VC/CR-CAT authorization flows the same way [7](#0-6) .
- `Offer.calculate_announcements` and `Offer._get_offered_coins` call `construct_puzzle`/`get_inner_puzzle` which index into the `PuzzleInfo` (`constructor["metadata"]`, `constructor["updater_hash"]`, `constructor["transfer_program"]`, etc.) assuming the fields implied by the declared type actually exist [8](#0-7) [9](#0-8) .

Because the declared `type` tag is trusted to select a code path/field layout while the underlying data was never checked to actually be shaped like that type (no corresponding puzzle reveal to validate against for unowned "requested" legs), a crafted `driver_dict` entry that advertises one type (e.g. `singleton → metadata` with `ACS_MU_PH` updater, or `singleton → metadata → ownership` with a `royalty transfer program`) but omits/mistypes the fields that the matching driver code unconditionally expects, causes downstream driver code to raise unhandled `KeyError`/`AttributeError`/`ValueError`/`AssertionError` deep inside offer construction.

### Impact Explanation
This is reachable by any local wallet RPC caller creating an offer, and by extension by any offer counterparty whose crafted offer causes the same driver-dispatch code to run (`get_dl_offer_summary`, `check_for_final_modifications`) when a victim inspects or accepts an incoming offer file. An unhandled exception in these paths halts offer creation/acceptance processing for that call — a spend/offer-triggered processing halt analogous to the Suricata crash-on-malformed-stream bug class, though scoped to the wallet-side trade/offer pipeline rather than a network parser.

### Likelihood Explanation
Likelihood is high for the crash variant: constructing a `PuzzleInfo` with a valid `type` chain but missing/incorrect leaf fields is trivial from the RPC surface (`create_offer_for_ids`) or from a hand-crafted offer file, and no additional puzzle-reveal validation exists for the affected code paths.

### Recommendation
Validate `PuzzleInfo` structure (required fields per declared type, including nested `also()` layers) at ingestion time — both when accepted from RPC (`driver_dict` parameter) and when parsing an untrusted offer file — and wrap driver-dispatch logic (`check_for_special_offer_making`, `check_for_final_modifications`, `get_dl_offer_summary`, `Offer.calculate_announcements`) so malformed/attacker-controlled `PuzzleInfo` objects fail with a controlled `ValueError` rather than an uncaught exception. For "requested" offer legs without a backing local wallet, cross-check the supplied driver info against the actual settlement puzzle hash that will be produced, rather than trusting the declared type chain outright.

### Proof of Concept
1. Call `create_offer_for_ids` (via wallet RPC `CreateOfferForIDs`) requesting an asset_id for which the caller's wallet has no local wallet instance (e.g., an NFT/DID asset never seen before), and supply a `driver_dict` entry such as:
```json
{
  "type": "singleton",
  "launcher_id": "0x..",
  "launcher_ph": "0x..",
  "also": {
    "type": "metadata",
    "metadata": "()",
    "also": { "type": "ownership", "owner": "()", "transfer_program": {"type": "royalty transfer program"} }
  }
}
```
omitting fields that `TransferProgramPuzzle`/`OwnershipOuterPuzzle`/`NFTWallet.make_nft1_offer` expect (e.g. `royalty_address`, `royalty_percentage`, `launcher_id` inside `transfer_program`).
2. Because this is a "requested" leg with no matching local wallet, `_create_offer_for_ids` skips validating this `PuzzleInfo` against a real puzzle [4](#0-3) , and it flows unchanged into `check_for_special_offer_making`, whose `check_type` gate passes on the declared tags [10](#0-9) , invoking `NFTWallet.make_nft1_offer`, which unconditionally accesses `transfer_info["transfer_program"]["royalty_percentage"]`/`["royalty_address"]` [11](#0-10) .
3. The missing keys cause an uncaught `KeyError`, aborting the `create_offer_for_ids` RPC call and the surrounding wallet action-scope processing for that request.

### Citations

**File:** chia/wallet/puzzle_drivers.py (L21-49)
```python
class PuzzleInfo:
    """
    There are two 'magic' keys in a PuzzleInfo object:
      - 'type' must be an included key (for easy lookup of drivers)
      - 'also' gets its own method as it's the supported way to do recursion of PuzzleInfos
    """

    info: dict[str, Any]

    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info
        self.__post_init__()

    def __post_init__(self) -> None:
        if "type" not in self.info:
            raise ValueError("A type is required to initialize a puzzle driver")

    def __getitem__(self, item: str) -> Any:
        value = self.info[item]
        return decode_info_value(PuzzleInfo, value)

    def __eq__(self, other: object) -> bool:
        for key, value in self.info.items():
            try:
                if self[key] != other[key]:  # type: ignore
                    return False
            except Exception:
                return False
        return True
```

**File:** chia/wallet/puzzle_drivers.py (L66-79)
```python
    def check_type(self, types: list[str]) -> bool:
        if types == []:
            if self.also() is None:
                return True
            else:
                return False
        elif self.type() == types[0]:
            types.pop(0)
            if self.also():
                return self.also().check_type(types)  # type: ignore
            else:
                return self.check_type(types)
        else:
            return False
```

**File:** chia/wallet/trade_manager.py (L502-527)
```python
            for id, amount in offer_dict.items():
                asset_id: bytes32 | None = None
                # asset_id can either be none if asset is XCH or
                # bytes32 if another asset (e.g. NFT, CAT)
                if amount > 0:
                    # this is what we are receiving in the trade
                    memos: list[bytes] = []
                    p2_ph = await action_scope.get_puzzle_hash(self.wallet_state_manager)
                    if isinstance(id, int):
                        wallet_id = uint32(id)
                        wallet = self.wallet_state_manager.wallets.get(wallet_id)
                        assert isinstance(wallet, (Wallet, CATWallet))
                        if wallet.type() != WalletType.STANDARD_WALLET:
                            if callable(getattr(wallet, "get_asset_id", None)):  # ATTENTION: new wallets
                                assert isinstance(wallet, CATWallet)
                                asset_id = wallet.get_asset_id()
                                memos = [p2_ph]
                            else:
                                raise ValueError(
                                    f"Cannot request assets from wallet id {wallet.id()} without more information"
                                )
                    else:
                        asset_id = id
                        wallet = await self.wallet_state_manager.get_wallet_for_asset_id(asset_id)
                        memos = [p2_ph]
                    requested_payments[asset_id] = [CreateCoin(p2_ph, uint64(amount), memos)]
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

**File:** chia/wallet/trade_manager.py (L919-956)
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
            elif (
                puzzle_info.check_type(
                    [
                        AssetType.SINGLETON.value,
                        AssetType.METADATA.value,
                    ]
                )
                and puzzle_info.also()["updater_hash"] == ACS_MU_PH  # type: ignore
            ):
                return await DataLayerWallet.make_update_offer(
                    self.wallet_state_manager,
                    offer_dict,
                    driver_dict,
                    solver,
                    action_scope,
                    fee,
                    extra_conditions,
                )
        return None
```

**File:** chia/wallet/trade_manager.py (L992-1071)
```python
    async def check_for_final_modifications(
        self, offer: Offer, solver: Solver, action_scope: WalletActionScope
    ) -> tuple[Offer, Solver]:
        for puzzle_info in offer.driver_dict.values():
            if (
                puzzle_info.check_type(
                    [
                        AssetType.SINGLETON.value,
                        AssetType.METADATA.value,
                    ]
                )
                and puzzle_info.also()["updater_hash"] == ACS_MU_PH  # type: ignore
            ):
                return (await DataLayerWallet.finish_graftroot_solutions(offer, solver), Solver({}))
            elif puzzle_info.check_type(
                [
                    AssetType.CAT.value,
                    AssetType.CR.value,
                ]
            ):
                # get VC wallet
                for _, wallet in self.wallet_state_manager.wallets.items():
                    if WalletType(wallet.type()) == WalletType.VC:
                        assert isinstance(wallet, VCWallet)
                        return await wallet.add_vc_authorization(offer, solver, action_scope)
                raise ValueError("No VCs to approve CR-CATs with")  # pragma: no cover

        return offer, Solver({})

    async def check_for_requested_payment_modifications(
        self,
        requested_payments: dict[bytes32 | None, list[CreateCoin]],
        driver_dict: dict[bytes32, PuzzleInfo],
        taking: bool,
    ) -> dict[bytes32 | None, list[CreateCoin]]:
        # This function exclusively deals with CR-CATs for now
        if not taking:
            for asset_id, puzzle_info in driver_dict.items():
                if puzzle_info.check_type(
                    [
                        AssetType.CAT.value,
                        AssetType.CR.value,
                    ]
                ):
                    vc = await (
                        await self.wallet_state_manager.get_or_create_vc_wallet()
                    ).get_vc_with_provider_in_and_proofs(
                        puzzle_info["also"]["authorized_providers"],
                        ProofsChecker.from_program(
                            UnknownPuzzle(known_program=puzzle_info["also"]["proofs_checker"])
                        ).flags,
                    )
                    if vc is None:
                        raise ValueError("Cannot request CR-CATs that you cannot approve with a VC")  # pragma: no cover

            return {
                asset_id: (
                    [
                        dataclasses.replace(
                            payment,
                            puzzle_hash=construct_pending_approval_state(
                                payment.puzzle_hash, payment.amount
                            ).get_tree_hash(),
                        )
                        for payment in payments
                    ]
                    if asset_id is not None
                    and driver_dict[asset_id].check_type(
                        [
                            AssetType.CAT.value,
                            AssetType.CR.value,
                        ]
                    )
                    else payments
                )
                for asset_id, payments in requested_payments.items()
            }
        else:
            return requested_payments

```

**File:** chia/wallet/trading/offer.py (L130-148)
```python
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

**File:** chia/wallet/nft_wallet/metadata_outer_puzzle.py (L56-63)
```python
    def asset_id(self, constructor: PuzzleInfo) -> bytes32 | None:
        return bytes32(constructor["updater_hash"])

    def construct(self, constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_puzzle = self._construct(also, inner_puzzle)
        return puzzle_for_metadata_layer(constructor["metadata"], constructor["updater_hash"], inner_puzzle)
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L906-923)
```python
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
```
