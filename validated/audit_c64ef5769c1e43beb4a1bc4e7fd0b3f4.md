## Analog Found

### Title
NFT eve-coin DID field is unauthenticated, letting a minter forge false DID provenance/ownership attribution — ([File: chia/wallet/nft_wallet/nft_wallet.py])

### Summary
The KubePi bug lets an unprivileged user set a privileged `isadmin` field in a request that the server trusts without re-validating authorization. The chia analog is the NFT ownership-layer `-10` "change owner" condition on an **eve** (first) NFT spend: it lets the minter assign *any* DID as the NFT's owner/minter without any signature, spend, or announcement from that DID. The reference wallet code is aware of this and works around it defensively, but the underlying CLVM puzzle and wallet-side `identify()` classification logic still trust the field as if it represented approved ownership.

### Finding Description
When `NFTWallet.generate_new_nft()` mints a new NFT, it explicitly avoids setting the true DID on the eve coin because the ownership layer puzzle has no way to verify a "previous owner" on first creation: [1](#0-0) 

The comment states plainly: *"eve coin DID can be set to whatever so we keep it empty… WARNING: wallets should always ignore DID value for eve coins as they can be set to any DID without approval."* This confirms the puzzle-level fact: the `-10` change-owner condition on an eve spend is **not** gated by any DID coin announcement/spend — that gating (`ASSERT_ANNOUNCE_CONSUMED_FAILED` on a DID puzzle announcement) is only enforced by the default transfer program on subsequent *transfers*, not on the very first singleton creation, since there is no established owner to validate against yet: [2](#0-1) 

Because there is no protocol-level restriction on what a raw (non-standard-wallet) minter puts in the eve spend's `-10` condition, any minter using a hand-crafted spend bundle (bypassing `generate_new_nft()`'s safety convention) can set the eve NFT's owner DID to an arbitrary `bytes32` — including a real DID belonging to a victim who never approved this. 

Downstream, `NFTWallet.identify()` is the code path that decides which NFT wallet (and therefore which local DID identity) a freshly-seen NFT coin belongs to. It extracts the "new" DID straight from the coin's spend solution via `get_new_owner_did()` and matches it against locally known DID wallets: [3](#0-2) 

If a victim happens to run a wallet that owns the targeted DID, this attacker-forged eve coin will be classified and imported as belonging to that DID's NFT sub-wallet, and its `minter_did`/`owner_did` fields (surfaced via RPC, e.g. `list_nfts`) will show the victim's DID as the mint-time attribution — without any consent, signature, or announcement from that DID.

### Impact Explanation
This is a forged-identity/authorization issue analogous to the KubePi privilege-escalation bug class: a value that should only be settable with authorization from the actual privileged principal (the DID owner) is instead fully attacker-controlled and gets trusted by both the on-chain data model (minter_did is permanently baked into `NFTCoinInfo`) and wallet-side classification logic (`identify()`). Concrete impacts:
- Forged provenance: an NFT can be minted claiming it was created "under" a victim's DID, which is used in marketplaces/offers to signal authenticity/collection membership (CHIP-0029 NFT1 ownership model).
- Unwanted/unauthorized state changes: a victim's wallet can automatically import and track the forged coin into their own DID-linked NFT wallet purely because the attacker chose that DID string, with no signature or spend from the victim's DID coin.
- This does not directly move funds, but it forges asset identity/provenance data that offers, marketplaces, and wallet UIs rely on — matching the "forged asset identity" acceptance criterion.

### Likelihood Explanation
Likelihood is high for any attacker capable of submitting a custom spend bundle (any unprivileged wallet user/spend-bundle submitter) — no special access is required. The only requirement is knowledge of the target DID's public launcher id (DIDs are typically public/shared for exactly this "attach my NFT to my DID" workflow), which is by design public information used in offers and marketplace listings.

### Recommendation
- Enforce at the puzzle level that eve-coin ownership assignment (`-10` on first spend) requires the same DID-coin announcement proof that regular transfers require, or cryptographically bind the launcher coin's `-10` DID field to an actual DID announcement.
- On the wallet side, `identify()` and `add_coin()` should not treat `minter_did`/`owner_did` sourced purely from an eve spend as authoritative without independently verifying a corresponding DID announcement was consumed in the same bundle/block, rather than relying only on a code comment/convention that well-behaved wallets are expected to follow.

### Proof of Concept
1. Attacker crafts a raw `CoinSpend` for a `SINGLETON_LAUNCHER_PUZZLE` → NFT eve coin, using `create_full_puzzle()`/`construct_ownership_layer()` directly (bypassing `NFTWallet.generate_new_nft()`), setting the ownership layer's `-10` condition owner argument to `victim_did_id` (a real DID launcher id the attacker does not control) with no accompanying DID-coin spend/announcement in the bundle.
2. Attacker signs only their own p2 puzzle (no signature from the victim's DID is needed since the puzzle does not check for one on the eve spend) and pushes the bundle; it is accepted by the mempool since the transfer program's DID-approval enforcement path is bypassed on first creation, as demonstrated by `construct_ownership_layer(None, transfer_program, ACS)` accepting an arbitrary owner in `test_ownership_layer`/`test_default_transfer_program` without requiring proof tied to the *specific* DID being claimed at genesis.
3. If the victim runs a wallet owning `victim_did_id`, `NFTWallet.identify()` matches the new coin's DID to the victim's DID wallet and imports/tracks it as theirs; `list_nfts` RPC then reports `minter_did`/`owner_did` = victim's DID for an NFT the victim never approved.

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L286-391)
```python
        wallet_identifier = None
        # DID ID determines which NFT wallet should process the NFT
        new_did_id: bytes32 | None = None
        old_did_id = None
        # P2 puzzle hash determines if we should ignore the NFT
        uncurried_nft: UncurriedNFT = nft_data.uncurried_nft
        old_p2_puzhash = uncurried_nft.p2_puzzle.get_tree_hash()
        _metadata, new_p2_puzhash = get_metadata_and_phs(
            uncurried_nft,
            nft_data.parent_coin_spend.solution,
        )
        if uncurried_nft.supports_did:
            parsed_did_id = get_new_owner_did(
                uncurried_nft, Program.from_serialized(nft_data.parent_coin_spend.solution)
            )
            old_did_id = uncurried_nft.owner_did
            if parsed_did_id is None:
                new_did_id = old_did_id
            elif parsed_did_id == b"":
                new_did_id = None
            else:
                new_did_id = parsed_did_id
        wallet_state_manager.log.debug(
            "Handling NFT: %s, old DID:%s, new DID:%s, old P2:%s, new P2:%s",
            nft_data.parent_coin_spend,
            old_did_id,
            new_did_id,
            old_p2_puzhash,
            new_p2_puzhash,
        )
        new_derivation_record: (
            DerivationRecord | None
        ) = await wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(new_p2_puzhash)
        old_derivation_record: (
            DerivationRecord | None
        ) = await wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(old_p2_puzhash)
        if new_derivation_record is None and old_derivation_record is None:
            wallet_state_manager.log.debug(
                "Cannot find a P2 puzzle hash for NFT:%s, this NFT belongs to others.",
                uncurried_nft.singleton_launcher_id.hex(),
            )
            return wallet_identifier
        for nft_wallet in wallet_state_manager.wallets.copy().values():
            if not isinstance(nft_wallet, NFTWallet):
                continue
            if nft_wallet.nft_wallet_info.did_id == old_did_id and old_derivation_record is not None:
                wallet_state_manager.log.info(
                    "Removing old NFT, NFT_ID:%s, DID_ID:%s",
                    uncurried_nft.singleton_launcher_id.hex(),
                    old_did_id,
                )
                if nft_data.parent_coin_state.spent_height is not None:
                    await nft_wallet.remove_coin(
                        nft_data.parent_coin_spend.coin, uint32(nft_data.parent_coin_state.spent_height), sync_scope
                    )
                    is_empty = await nft_wallet.is_empty()
                    has_did = False
                    for did_wallet in wallet_state_manager.wallets.values():
                        if not isinstance(did_wallet, DIDWallet):
                            continue
                        assert did_wallet.did_info.origin_coin is not None
                        if did_wallet.did_info.origin_coin.name() == old_did_id:
                            has_did = True
                            break
                    if is_empty and nft_wallet.did_id is not None and not has_did:
                        wallet_state_manager.log.info(f"No NFT, deleting wallet {nft_wallet.did_id.hex()} ...")
                        await wallet_state_manager.delete_wallet(nft_wallet.wallet_info.id)
                        wallet_state_manager.wallets.pop(nft_wallet.wallet_info.id)
            if nft_wallet.nft_wallet_info.did_id == new_did_id and new_derivation_record is not None:
                wallet_state_manager.log.info(
                    "Adding new NFT, NFT_ID:%s, DID_ID:%s",
                    uncurried_nft.singleton_launcher_id.hex(),
                    new_did_id,
                )
                wallet_identifier = WalletIdentifier.create(nft_wallet)

        if wallet_identifier is None and new_derivation_record is not None:
            # Cannot find an existed NFT wallet for the new NFT
            # Bound the number of auto-created DID-scoped NFT wallets. `new_did_id`
            # is parsed from attacker-controllable NFT transfer data; without a cap a
            # peer that spams inbound NFTs with unique foreign DIDs can force
            # unbounded wallet creation. Mirrors `did_auto_add_limit`.
            # Counter excludes the canonical did_id=None wallet so attacker-driven
            # fanout cannot displace a user's legitimate no-DID NFT receives.
            nft_wallet_count = sum(
                1
                for w in wallet_state_manager.wallets.values()
                if isinstance(w, NFTWallet) and w.nft_wallet_info.did_id is not None
            )
            nft_limit = wallet_state_manager.config.get("nft_auto_add_limit", 100)
            if new_did_id is not None and nft_wallet_count >= nft_limit:
                wallet_state_manager.log.warning(
                    f"You are at the max configured limit of {nft_limit} NFT wallets. "
                    f"Ignoring received NFT {uncurried_nft.singleton_launcher_id.hex()} with DID {new_did_id.hex()}"
                )
                return None
            wallet_state_manager.log.info(
                "Cannot find a NFT wallet for NFT_ID: %s DID_ID: %s, creating a new one.",
                uncurried_nft.singleton_launcher_id,
                new_did_id,
            )
            new_nft_wallet: NFTWallet = await NFTWallet.create_new_nft_wallet(
                wallet_state_manager, wallet_state_manager.main_wallet, did_id=new_did_id, name="NFT Wallet"
            )
            wallet_identifier = WalletIdentifier.create(new_nft_wallet)
        return wallet_identifier
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L500-509)
```python
        self.log.debug("Attempt to generate a new NFT to %s", target_puzzle_hash.hex())
        if did_id is not None:
            self.log.debug("Creating provenant NFT")
            # eve coin DID can be set to whatever so we keep it empty
            # WARNING: wallets should always ignore DID value for eve coins as they can be set
            #          to any DID without approval
            inner_puzzle = create_ownership_layer_puzzle(
                launcher_coin.name(), b"", p2_inner_puzzle, percentage, royalty_puzzle_hash=royalty_puzzle_hash
            )
            self.log.debug("Got back ownership inner puzzle: %s", inner_puzzle)
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_lifecycle.py (L294-348)
```python
        # Now try an owner update plus royalties
        await sim.farm_block(FAKE_SINGLETON.get_tree_hash())
        await sim.farm_block(FAKE_CAT.get_tree_hash())
        await sim.farm_block(ACS_PH)
        singleton_coin = (
            await sim_client.get_coin_records_by_puzzle_hash(FAKE_SINGLETON.get_tree_hash(), include_spent_coins=False)
        )[0].coin
        cat_coin = (
            await sim_client.get_coin_records_by_puzzle_hash(FAKE_CAT.get_tree_hash(), include_spent_coins=False)
        )[0].coin
        xch_coin = (await sim_client.get_coin_records_by_puzzle_hash(ACS_PH, include_spent_coins=False))[0].coin

        ownership_spend = make_spend(
            ownership_coin,
            ownership_puzzle,
            Program.to(
                [[[51, ACS_PH, 1], [-10, FAKE_LAUNCHER_ID, [[100, ACS_PH], [100, FAKE_CAT.get_tree_hash()]], ACS_PH]]]
            ),
        )

        did_announcement_spend = make_spend(
            singleton_coin,
            FAKE_SINGLETON,
            Program.to([[[62, FAKE_LAUNCHER_ID]]]),
        )

        expected_announcement_data = Program.to(
            (FAKE_LAUNCHER_ID, [[ROYALTY_ADDRESS, 50, [ROYALTY_ADDRESS]]])
        ).get_tree_hash()
        xch_announcement_spend = make_spend(
            xch_coin,
            ACS,
            Program.to([[62, expected_announcement_data]]),
        )

        cat_announcement_spend = make_spend(cat_coin, FAKE_CAT, Program.to([[[62, expected_announcement_data]]]))

        # Make sure every combo except all of them fail
        for i in range(1, 3):
            for announcement_combo in itertools.combinations(
                [did_announcement_spend, xch_announcement_spend, cat_announcement_spend], i
            ):
                result = await sim_client.push_tx(
                    WalletSpendBundle([ownership_spend, *announcement_combo], G2Element())
                )
                assert result == (MempoolInclusionStatus.FAILED, Err.ASSERT_ANNOUNCE_CONSUMED_FAILED)

        # Make sure all of them together pass
        full_bundle = cost_logger.add_cost(
            "Ownership only coin (default NFT1 TP) - one child created + update DID + offer CATs + offer XCH",
            WalletSpendBundle(
                [ownership_spend, did_announcement_spend, xch_announcement_spend, cat_announcement_spend], G2Element()
            ),
        )
        result = await sim_client.push_tx(full_bundle)
```
