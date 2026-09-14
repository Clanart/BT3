### Title
NFT Mint "Minter DID" / Ownership Spoofing via Unapproved DID Assignment on Eve Coins - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
When minting an NFT with `did_id` set, `NFTWallet.generate_new_nft` constructs the *eve* singleton coin's ownership layer with an **empty** current-owner value and lets the DID field be set purely from the caller-supplied `did_id` argument, without any cryptographic approval from that DID's actual owner. The code contains an explicit warning acknowledging this: the DID on the eve coin "can be set to whatever" and "can be set to any DID without approval." This value is subsequently persisted as `minter_did` and surfaced to RPC/CLI consumers (`nft_get_info`, `nft_get_nfts`) as though it were an authenticated attribution — mirroring the Craft CMS `authorId` mass-assignment bug where an unprivileged creator could stamp an entry with an arbitrary victim's identity.

### Finding Description
`generate_new_nft` in [1](#0-0)  builds the eve coin's ownership-layer inner puzzle with the current owner set to `b""` (empty) rather than the requested `did_id`, precisely because the DID value at mint time cannot be cryptographically verified to belong to the caller — the comment states plainly that "wallets should always ignore DID value for eve coins as they can be set to any DID without approval."

Despite this, the same function immediately stores the caller-supplied `did_id` as `minter_did` on the resulting `NFTCoinInfo` and passes it as `new_owner` into the subsequent spend at [2](#0-1) . This `minter_did` is persisted to the wallet's NFT store, [3](#0-2) , and returned via RPC endpoints such as `nft_get_nfts`/`list_nfts` and `nft_get_info`, which report it back to end users as `NFTInfo.minter_did` — i.e., "who minted this NFT and on behalf of which DID."

On the sync side, `WalletStateManager.get_minter_did` in [4](#0-3)  derives the same value purely from the `-10` magic condition in the eve coin's solution — a value the minter fully controls and can set to any bytes32, including a real, unrelated victim's DID id, with no signature or announcement proving that the victim's DID authorized this attribution (unlike `set_nft_did`/`get_did_approval_info`, which does require an actual DID-coin spend/announcement for post-mint ownership transfers, see [5](#0-4) ).

This is a mass-assignment style trust issue: a field (`did_id`/`minter_did`) that should require authorization from the referenced identity is instead accepted at face value from the unprivileged minting party and surfaced as authenticated provenance data, exactly analogous to Craft CMS accepting an unauthorized `authorId` parameter and displaying it as the true author.

### Impact Explanation
Any wallet user minting an NFT can attribute the mint to an arbitrary victim DID (e.g., a well-known creator, brand, or admin-controlled DID) by simply supplying that DID's id in the `did_id` parameter of `nft_mint_nft`. Because the code path explicitly documents that this DID is unverified at mint time, wallets, marketplaces, or explorers that trust `minter_did`/eve-coin DID values without independently re-deriving/ignoring them (as the in-repo comment warns some might) could display forged provenance, letting an attacker impersonate a trusted minter/brand for NFTs, potentially deceiving counterparties in trades/offers about the true origin of an asset.

### Likelihood Explanation
High likelihood of misuse: this only requires a standard "Create Entries"-equivalent action — calling the public `nft_mint_nft`/`generate_new_nft` wallet RPC with an attacker-chosen `did_id`, something any local wallet RPC caller with minting capability can already do. No special privileges, signatures, or victim DID cooperation are required to produce a coin whose `minter_did` field names the victim.

### Recommendation
- Do not persist or surface the caller-supplied `did_id`/`minter_did` value as authenticated provenance unless it is cryptographically tied to an actual spend/announcement from that DID (as already done for post-mint `set_nft_did`/`get_did_approval_info`).
- Ensure all downstream consumers (`nft_get_info`, `nft_get_nfts`, CLI display code in `chia/cmds/wallet_funcs.py`) either omit `minter_did` for eve coins lacking DID-approval proof or clearly flag it as unverified, consistent with the existing in-code warning.
- Consider requiring the DID's own coin spend/announcement (similar to `get_did_approval_info`) before recording any `minter_did`/ownership attribution at mint time, closing the gap between the documented risk and actual enforcement.

### Proof of Concept
1. As any wallet user with a standard XCH wallet, call the `nft_mint_nft` RPC with `did_id` set to a victim's known DID id (no ownership of that DID is required).
2. Observe in `generate_new_nft` that the ownership layer is created with an empty current owner (line 506-508) yet `minter_did` is recorded as the victim's DID (line 560) and passed as `new_owner` to the spend (line 568).
3. Query `nft_get_info`/`nft_get_nfts` for the minted NFT — the response reports `minter_did` as the victim's DID, even though the victim never approved or signed anything, reproducing the "authorship spoofing via mass assignment" pattern from the Craft CMS advisory.

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L436-463)
```python
    async def get_did_approval_info(
        self,
        nft_ids: list[bytes32],
        action_scope: WalletActionScope,
        did_id: bytes32 | None = None,
    ) -> bytes32:
        """Get DID spend with announcement created we need to transfer NFT with did with current inner hash of DID

        We also store `did_id` and then iterate to find the did wallet as we'd otherwise have to subscribe to
        any changes to DID wallet and storing wallet_id is not guaranteed to be consistent on wallet crash/reset.
        """
        if did_id is None:
            did_id = self.did_id
        did_inner_hash: bytes32
        for _, wallet in self.wallet_state_manager.wallets.items():
            self.log.debug("Checking wallet type %s", wallet.type())
            if wallet.type() == WalletType.DECENTRALIZED_ID:
                self.log.debug("Found a DID wallet, checking did: %r == %r", wallet.get_my_DID(), did_id)
                if bytes32.fromhex(wallet.get_my_DID()) == did_id:
                    self.log.debug("Creating announcement from DID for nft_ids: %s", nft_ids)
                    await wallet.create_message_spend(
                        action_scope, extra_conditions=(CreatePuzzleAnnouncement(id) for id in nft_ids)
                    )
                    did_inner_hash = wallet.did_info.current_inner.get_tree_hash()
                    break
        else:
            raise ValueError(f"Missing DID Wallet for did_id: {did_id}")
        return did_inner_hash
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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L549-571)
```python
        # Create inner solution for eve spend
        did_inner_hash = b""
        if did_id is not None:
            if did_id != b"":
                did_inner_hash = await self.get_did_approval_info([launcher_coin.name()], action_scope)
        nft_coin = NFTCoinInfo(
            nft_id=launcher_coin.name(),
            coin=eve_coin,
            lineage_proof=LineageProof(parent_name=launcher_coin.parent_coin_info, amount=uint64(launcher_coin.amount)),
            full_puzzle=eve_fullpuz,
            mint_height=uint32(0),
            minter_did=bytes32(did_id) if did_id is not None and did_id != b"" else None,
        )
        # Don't set fee, it is covered in the tx_record
        await self.generate_signed_transaction(
            [uint64(eve_coin.amount)],
            [target_puzzle_hash],
            action_scope,
            nft_coin=nft_coin,
            new_owner=did_id,
            new_did_inner_hash=did_inner_hash,
            memos=[[target_puzzle_hash]],
        )
```

**File:** chia/wallet/wallet_nft_store.py (L112-138)
```python
    async def save_nft(self, wallet_id: uint32, did_id: bytes32 | None, nft_coin_info: NFTCoinInfo) -> None:
        async with self.db_wrapper.writer_maybe_transaction() as conn:
            columns = (
                "nft_id, nft_coin_id, wallet_id, did_id, coin, lineage_proof, mint_height, status, full_puzzle, "
                "minter_did, removed_height, latest_height"
            )
            await conn.execute(
                f"INSERT or REPLACE INTO users_nfts ({columns}) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    nft_coin_info.nft_id.hex(),
                    nft_coin_info.coin.name().hex(),
                    int(wallet_id),
                    did_id.hex() if did_id else None,
                    json.dumps(nft_coin_info.coin.to_json_dict()),
                    (
                        json.dumps(nft_coin_info.lineage_proof.to_json_dict())
                        if nft_coin_info.lineage_proof is not None
                        else None
                    ),
                    int(nft_coin_info.mint_height),
                    IN_TRANSACTION_STATUS if nft_coin_info.pending_transaction else DEFAULT_STATUS,
                    bytes(nft_coin_info.full_puzzle),
                    None if nft_coin_info.minter_did is None else nft_coin_info.minter_did.hex(),
                    None,
                    int(nft_coin_info.latest_height),
                ),
            )
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
