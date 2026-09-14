### Title
Ambiguous wallet lookup by asset_id lets a differently-restricted CAT wallet (RCAT/CR-CAT) be silently substituted for a plain CAT during offer construction and settlement - (File: chia/wallet/wallet_state_manager.py)

### Summary
`WalletStateManager.get_wallet_for_asset_id()` resolves a wallet purely by matching the CAT tail hash (`asset_id`), ignoring any additional restriction layer ("also" data such as `authorized_providers`/`proofs_checker` for CR-CATs or `hidden_puzzle_hash` for RCATs). This is structurally the same bug class as ALPINE-CVE-2022-30550: multiple local configuration entries (here, multiple wallets: a plain `CATWallet`, an `RCATWallet`, a `CRCATWallet`) that share the same "driver+args" key (CAT type + tail hash) but differ in security-relevant settings (restriction/authorization layer), and the code picks one based on an incomplete match, silently applying the wrong settings.

### Finding Description
`get_wallet_for_asset_id` iterates `self.wallets` and returns the first wallet whose `type()` is in `{CAT, CRCAT, RCAT}` and whose `get_asset_id()` equals the requested `asset_id`: [1](#0-0) 

Unlike `get_wallet_for_puzzle_info`, which uses `MatchPuzzleInfoWallet.match_puzzle_info()` to compare the *entire* `PuzzleInfo` including the `also()` restriction layer: [2](#0-1) 

`get_asset_id()`-based matching only compares the tail hash, not the wallet-specific restriction data. `CRCATWallet.match_puzzle_info` and `RCATWallet.match_puzzle_info` both explicitly check the tail hash *and* the `authorized_providers`/`proofs_checker`, or `hidden_puzzle_hash`, respectively, showing that the codebase itself treats these as security-distinguishing fields that must not be conflated: [3](#0-2) [4](#0-3) 

`get_wallet_for_asset_id` is used by `TradeManager._create_offer_for_ids` when a wallet is not specified as an integer ID but as a raw `asset_id` (e.g. via the wallet RPC `create_offer_for_ids` / `driver_dict`-based offer flows), to resolve which wallet's coins to select and which wallet's `get_puzzle_info` to trust for constructing/settling an offer: [5](#0-4) [6](#0-5) 

If a wallet keychain locally tracks more than one wallet for the same CAT tail (a normal `CATWallet` and a `CRCATWallet`/`RCATWallet` variant for the same asset, which can legitimately coexist since CR-CAT/RCAT coins are convertible to/from a plain CAT sharing the tail — see the conversion path in `CATWallet.identify`), an asset-id-only lookup will nondeterministically return whichever wallet happens to iterate first in the `self.wallets` dict, rather than the one that actually corresponds to the puzzle reveal/coins involved in the specific spend: [7](#0-6) 

### Impact Explanation
When the wrong wallet is returned, `puzzle_driver: PuzzleInfo = await wallet.get_puzzle_info(asset_id)` in `_create_offer_for_ids` will produce a driver descriptor that omits (or mismatches) the CR-CAT `authorized_providers`/`proofs_checker` restriction or the RCAT revocation-layer hidden puzzle hash for coins that actually carry those restrictions: [8](#0-7) 

Because `Offer.calculate_announcements`/`to_valid_spend` construct the outer settlement puzzle from `driver_dict[asset_id]` (via `construct_puzzle`), an incorrect/incomplete driver for a restricted asset can result in the wrong outer puzzle/settlement construction being used for that asset id, and downstream logic that gates CR-CAT flows on `driver_dict` contents (e.g. `TradeManager.check_for_final_modifications` / `check_for_requested_payment_modifications`, which specifically look for the `CAT`→`CR` type chain to require VC authorization) can be silently skipped if the resolved driver reports as a plain CAT instead of CR-CAT: [9](#0-8) [10](#0-9) 

This is a credential-restricted-asset flow correctness/authorization issue: a locally-configured client with multiple CAT-family wallets for the same tail could construct offers whose CR/authorization gating is bypassed because the wrong wallet's driver info is substituted, analogous to the Dovecot bug where ambiguous config matching silently applies the wrong (weaker) authorization settings.

### Likelihood Explanation
This requires a wallet client to simultaneously track more than one CAT-family wallet for the same tail hash (e.g. both a `CATWallet` and a `CRCATWallet`/`RCATWallet` for the same asset), and for an offer/trade flow to reach `_create_offer_for_ids` with a raw `asset_id` key rather than an integer wallet id, which is a supported path used by wallet RPC/offer-driver_dict flows. This is a plausible but non-trivial local wallet-state precondition, not requiring any network attacker; it is reachable purely through normal wallet/RPC usage and CAT/CR-CAT conversion behavior already present in the codebase.

### Recommendation
Make `get_wallet_for_asset_id` (or its CAT-family branch) disambiguate on the full puzzle/driver identity, not just the tail hash — e.g., delegate to `get_wallet_for_puzzle_info` semantics (matching `also()` restriction data) whenever more than one CAT-family wallet exists for a tail, or require callers in `trade_manager.py` to pass enough context (the actual coin/puzzle reveal) to select the correct wallet deterministically instead of relying on dict iteration order.

### Proof of Concept
Not independently executable from the index alone (would require constructing a wallet state with both a `CATWallet` and `CRCATWallet`/`RCATWallet` sharing the same tail hash and driving `TradeManager.create_offer_for_ids` with the raw `asset_id` key to observe which wallet's `get_puzzle_info` is used). This should be validated by a background engineer against the full test suite (e.g., `chia/_tests/wallet/cat_wallet/test_trades.py`, `chia/_tests/wallet/vc_wallet/test_vc_wallet.py`) by instrumenting `get_wallet_for_asset_id` and constructing both wallet types for one tail.

### Citations

**File:** chia/wallet/wallet_state_manager.py (L2021-2026)
```python
    async def get_wallet_for_asset_id(self, asset_id: bytes32) -> WalletProtocol | None:
        for wallet_id, wallet in self.wallets.items():
            if wallet.type() in {WalletType.CAT, WalletType.CRCAT, WalletType.RCAT}:
                assert isinstance(wallet, CATWallet)
                if wallet.get_asset_id() == asset_id:
                    return wallet
```

**File:** chia/wallet/wallet_state_manager.py (L2038-2043)
```python
    async def get_wallet_for_puzzle_info(self, puzzle_driver: PuzzleInfo) -> WalletProtocol | None:
        for wallet in self.wallets.values():
            if isinstance(wallet, MatchPuzzleInfoWallet):
                if await wallet.match_puzzle_info(puzzle_driver):
                    return wallet
        return None
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L804-819)
```python
    async def match_puzzle_info(self, puzzle_driver: PuzzleInfo) -> bool:
        if (
            AssetType(puzzle_driver.type()) == AssetType.CAT
            and puzzle_driver["tail"] == self.info.limitations_program_hash
        ):
            inner_puzzle_driver: PuzzleInfo | None = puzzle_driver.also()
            if inner_puzzle_driver is None:
                raise ValueError("Malformed puzzle driver passed to CRCATWallet.match_puzzle_info")  # pragma: no cover
            return (
                AssetType(inner_puzzle_driver.type()) == AssetType.CR
                and [bytes32(provider) for provider in inner_puzzle_driver["authorized_providers"]]
                == self.info.authorized_providers
                and ProofsChecker.from_program(UnknownPuzzle(known_program=inner_puzzle_driver["proofs_checker"]))
                == self.info.proofs_checker
            )
        return False
```

**File:** chia/wallet/cat_wallet/r_cat_wallet.py (L241-253)
```python
    async def match_puzzle_info(self, puzzle_driver: PuzzleInfo) -> bool:
        if (
            AssetType(puzzle_driver.type()) == AssetType.CAT
            and puzzle_driver["tail"] == self.info.limitations_program_hash
        ):
            inner_puzzle_driver: PuzzleInfo | None = puzzle_driver.also()
            if inner_puzzle_driver is None:
                raise ValueError("Malformed puzzle driver passed to RCATWallet.match_puzzle_info")
            return (
                AssetType(inner_puzzle_driver.type()) == AssetType.REVOCATION_LAYER
                and bytes32(inner_puzzle_driver["hidden_puzzle_hash"]) == self.info.hidden_puzzle_hash
            )
        return False
```

**File:** chia/wallet/trade_manager.py (L523-526)
```python
                    else:
                        asset_id = id
                        wallet = await self.wallet_state_manager.get_wallet_for_asset_id(asset_id)
                        memos = [p2_ph]
```

**File:** chia/wallet/trade_manager.py (L541-543)
```python
                    else:
                        asset_id = id
                        wallet = await self.wallet_state_manager.get_wallet_for_asset_id(asset_id)
```

**File:** chia/wallet/trade_manager.py (L570-583)
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
```

**File:** chia/wallet/trade_manager.py (L1006-1017)
```python
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
```

**File:** chia/wallet/trade_manager.py (L1028-1045)
```python
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
```

**File:** chia/wallet/cat_wallet/cat_wallet.py (L463-522)
```python
        else:
            our_inner_puzzle: Program = wallet_state_manager.main_wallet.puzzle_for_pk(derivation_record.pubkey)
            asset_id: bytes32 = parent_data.tail_program_hash
            cat_puzzle = construct_cat_puzzle(CAT_MOD, asset_id, our_inner_puzzle, CAT_MOD_HASH)
            wallet_type: type[CATWallet] = CATWallet
            crcat = None
            if cat_puzzle.get_tree_hash() != coin_state.coin.puzzle_hash:
                # Check if it is a special type of CAT
                uncurried_puzzle_reveal = UnknownPuzzle(known_program=coin_spend.puzzle_reveal)
                if uncurried_puzzle_reveal.mod != CAT_MOD or uncurried_puzzle_reveal.curried_args is None:
                    return None
                revocation_layer_match = match_revocation_layer(
                    UnknownPuzzle(known_program=uncurried_puzzle_reveal.curried_args[2])
                )
                if revocation_layer_match is not None:
                    wallet_type = RCATWallet
                else:
                    try:
                        next_crcats = CRCAT.get_next_from_coin_spend(coin_spend)

                    except ValueError:
                        return None

                    crcat = next(crc for crc in next_crcats if crc.coin == coin_state.coin)

                    wallet_type = CRCATWallet
            if wallet_type is CRCATWallet:
                assert crcat is not None  # mypy doesn't get the semantics
                # Since CRCAT wallet doesn't have derivation path, every CRCAT will go through this code path
                # Make sure we control the inner puzzle or we control it if it's wrapped in the pending state
                if (
                    await wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(
                        crcat.inner_puzzle_hash
                    )
                    is None
                    and crcat.inner_puzzle_hash
                    != construct_pending_approval_state(
                        hinted_coin.hint,
                        uint64(coin_state.coin.amount),
                    ).get_tree_hash()
                ):
                    wallet_state_manager.log.error(
                        f"Unknown CRCAT inner puzzle, coin ID:{crcat.coin.name().hex()}"
                    )  # pragma: no cover
                    return None  # pragma: no cover

                # Check if we already have a wallet
                for wallet_info in await wallet_state_manager.get_all_wallet_info_entries(wallet_type=WalletType.CRCAT):
                    crcat_info: CRCATInfo = CRCATInfo.from_bytes(bytes.fromhex(wallet_info.data))
                    if crcat_info.limitations_program_hash == asset_id:
                        return WalletIdentifier(wallet_info.id, WalletType(wallet_info.type))

            if wallet_type in {CRCATWallet, RCATWallet}:
                # We didn't find a matching alt-CAT wallet, but maybe we have a matching CAT wallet that we can convert
                for wallet_info in await wallet_state_manager.get_all_wallet_info_entries(wallet_type=WalletType.CAT):
                    cat_info: CATInfo = CATInfo.from_bytes(bytes.fromhex(wallet_info.data))
                    found_cat_wallet = wallet_state_manager.wallets[wallet_info.id]
                    assert isinstance(found_cat_wallet, CATWallet)
                    if cat_info.limitations_program_hash == asset_id:
                        if wallet_type is CRCATWallet:
```
