## Analog Found: Unauthenticated minter-DID attribution trusted from the NFT eve/launch spend

### Title
Forged NFT minter DID accepted without DID-authorization proof - (File: `chia/wallet/wallet_state_manager.py`)

### Summary
`WalletStateManager.get_minter_did()` derives the "minter DID" of an NFT directly from the DID field embedded in the eve (first) spend of the NFT singleton, without requiring any proof that the referenced DID actually authorized/approved the mint. This mirrors the reported Solidity bug class: a one-time "initializer" state (here, the eve/genesis state of a singleton) can be set to an arbitrary value by the entity performing the initializing spend, and downstream code treats that self-declared value as trustworthy provenance/ownership data.

### Finding Description
When an NFT singleton is created, its first ("eve") coin curries in an ownership layer whose `current_owner` field can be set to any DID id chosen by the minter, with no signature or on-chain announcement proving that DID actually approved the mint. This is explicitly acknowledged in the wallet's own minting code: [1](#0-0) 

The comment states: *"eve coin DID can be set to whatever so we keep it empty ... wallets should always ignore DID value for eve coins as they can be set to any DID without approval."* The wallet's own minting path (`generate_new_nft`) follows this rule and keeps the eve DID empty until the actual DID-approval spend (`get_did_approval_info`, which requires the real DID wallet to create an announcement spend) sets it.

However, `WalletStateManager.get_minter_did()` — used to populate `NFTCoinInfo.minter_did` / `NFTInfo.minter_did` when the wallet discovers or manually searches for an NFT — reads the DID value straight out of the eve spend's solution via `get_new_owner_did()`, without verifying that a corresponding DID coin ever created the required approval announcement: [2](#0-1) 

This is called from `manual_nft_search`: [3](#0-2) 

Because the ownership-layer `current_owner` field of the eve coin is attacker-controlled at mint time (any address, i.e. any unprivileged wallet user, can mint an NFT and curry in an arbitrary DID id in the ownership layer as long as they are willing to have it displayed as unapproved until a real transfer happens), and `get_minter_did` performs no verification that the claimed DID ever signed/announced approval, the recorded "minter DID" for the NFT can be forged to point at any victim DID.

### Impact Explanation
`minter_did` is persisted (`chia/wallet/wallet_nft_store.py`) and surfaced through `NFTInfo.minter_did` and CLI/RPC output (`chia/cmds/wallet_funcs.py`), which wallets, marketplaces, and users rely on to attribute NFT provenance/creator identity. An attacker can mint an NFT that falsely claims to originate from a well-known or victim DID (a forged asset/creator identity), without ever needing that DID's cooperation, key, or on-chain approval spend. This can be used to spoof provenance for scams (e.g., claiming a collectible NFT was minted by a reputable creator's DID) that downstream tooling displays as authoritative.

This is a data-integrity / identity-forgery issue rather than one that lets an attacker steal or move someone else's coins; it does not affect the actual DID ownership layer used for transfers (that still requires a real DID approval spend), so it does not enable coin theft. It matches the "forged asset identity" category in the sense that the *reported minter attribution* of an asset can be forged, but the actual custody/ownership of the NFT is unaffected.

### Likelihood Explanation
High likelihood of triggering: any single unprivileged wallet user can mint an NFT (one spend bundle) and set the ownership-layer `current_owner` to an arbitrary DID id — no cooperation from the target DID or any additional authorization is required, exactly as documented in the code's own warning comment. The only work required by an attacker is calling the standard NFT-minting flow with a chosen `did_id` and not performing/needing the real DID approval spend for the initial value to be attributed by other wallets' `get_minter_did`.

### Recommendation
`get_minter_did()` should not trust the DID value taken directly from the eve spend's `-10` (change-owner) condition. It should instead verify that the claimed DID actually performed a `create_message_spend`/announcement authorizing the mint (the same mechanism `get_did_approval_info` uses for normal transfers), for example by requiring an on-chain coin announcement from the claimed DID's singleton coincident with the eve spend, mirroring the invariant already documented and enforced in `NFTWallet.generate_new_nft`. If such proof cannot be found, `minter_did` should be treated as unknown/`None` rather than the self-declared value.

### Proof of Concept
1. Attacker (or any unprivileged wallet) calls the standard NFT wallet mint path but crafts the eve spend's inner solution directly (bypassing `generate_new_nft`'s intentional "keep DID empty" precaution) so that the `-10` change-owner condition names `victim_did` as `current_owner`.
2. The NFT singleton is minted and confirmed on chain with `ownership_layer.current_owner == victim_did`, without any spend from `victim_did`'s DID coin and without any announcement/approval.
3. Any wallet later calling `manual_nft_search` / sync logic invokes `WalletStateManager.get_minter_did()` on this NFT's launcher coin; `get_new_owner_did()` reads `victim_did` from the eve spend's solution and returns it as `minter_did`.
4. `NFTCoinInfo`/`NFTInfo.minter_did` is populated with `victim_did` and surfaced via RPC/CLI (`chia/cmds/wallet_funcs.py`) as the NFT's attributed minter/creator, despite `victim_did` never having approved or been involved in the mint.

Note: I could not fully trace every consumer of `NFTInfo.minter_did` (e.g., third-party marketplace or GUI code outside this repo) to assess the full scope of downstream trust in this field; the analysis here is limited to what is visible in this in-scope wallet codebase.

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L501-509)
```python
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

**File:** chia/wallet/wallet_state_manager.py (L1074-1112)
```python
    async def get_minter_did(self, launcher_coin: Coin, peer: WSChiaConnection) -> bytes32 | None:
        # Get minter DID
        eve_coin = (await self.wallet_node.fetch_children(launcher_coin.name(), peer=peer))[0]
        eve_coin_spend = await fetch_coin_spend_for_coin_state(eve_coin, peer)
        eve_full_puzzle: Program = Program.from_bytes(bytes(eve_coin_spend.puzzle_reveal))
        eve_uncurried_nft: UncurriedNFT | None = UncurriedNFT.uncurry(*eve_full_puzzle.uncurry())
        if eve_uncurried_nft is None:
            raise ValueError("Couldn't get minter DID for NFT")
        if not eve_uncurried_nft.supports_did:
            return None
        minter_did = get_new_owner_did(eve_uncurried_nft, Program.from_serialized(eve_coin_spend.solution))
        if minter_did == b"":
            minter_did = None
        if minter_did is None:
            # Check if the NFT is a bulk minting
            launcher_parent: list[CoinState] = await self.wallet_node.get_coin_state(
                [launcher_coin.parent_coin_info], peer=peer
            )
            assert len(launcher_parent) == 1 and launcher_parent[0].spent_height is not None
            # NFTs minted out of coinbase coins would not have minter DIDs
            if self.constants.GENESIS_CHALLENGE[:16] in bytes(
                launcher_parent[0].coin.parent_coin_info
            ) or self.constants.GENESIS_CHALLENGE[16:] in bytes(launcher_parent[0].coin.parent_coin_info):
                return None
            did_coin: list[CoinState] = await self.wallet_node.get_coin_state(
                [launcher_parent[0].coin.parent_coin_info], peer=peer
            )
            assert len(did_coin) == 1 and did_coin[0].spent_height is not None
            did_spend = await fetch_coin_spend_for_coin_state(did_coin[0], peer)
            uncurried = UnknownPuzzle(known_program=did_spend.puzzle_reveal)
            did_curried_args = (
                match_did_puzzle(uncurried.mod, Program.to(uncurried.curried_args))
                if uncurried.mod is not None and uncurried.curried_args is not None
                else None
            )
            if did_curried_args is not None:
                assert uncurried.curried_args is not None
                minter_did = bytes32(uncurried.curried_args[0].at("rf").as_atom())
        return minter_did
```

**File:** chia/wallet/wallet_state_manager.py (L2381-2402)
```python
        # Get launcher coin
        launcher_coin: list[CoinState] = await self.wallet_node.get_coin_state(
            [uncurried_nft.singleton_launcher_id], peer=peer
        )
        if len(launcher_coin) < 1 or launcher_coin[0].spent_height is None:
            raise ValueError(f"Launcher coin record 0x{uncurried_nft.singleton_launcher_id.hex()} not found")
        minter_did = await self.get_minter_did(launcher_coin[0].coin, peer)

        return ManualNFTSearchResults(
            nft_info=await nft_puzzle_utils.get_nft_info_from_puzzle(
                NFTCoinInfo(
                    uncurried_nft.singleton_launcher_id,
                    coin_state.coin,
                    None,
                    full_puzzle,
                    uint32(launcher_coin[0].spent_height),
                    minter_did,
                    uint32(coin_state.created_height) if coin_state.created_height else uint32(0),
                ),
                self.config,
            ),
            next_p2_puzzle_hash=p2_puzzle_hash,
```
