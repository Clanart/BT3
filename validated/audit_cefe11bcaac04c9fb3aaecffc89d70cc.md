### Title
Forged NFT owner/minter DID via unauthenticated `-10` ownership condition on eve/ownership-layer spends - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
The NFT ownership layer accepts an arbitrary DID as the `owner`/`minter` value on any spend that changes ownership (the `-10` "change owner" magic condition), with no protocol-level requirement that the referenced DID coin actually authorized the association. The chia wallet code itself documents this as dangerous, explicitly warning that "eve coin DID can be set to whatever" and that "wallets should always ignore DID value for eve coins as they can be set to any DID without approval." [1](#0-0)  Despite this warning, downstream code (`get_minter_did`, `recurry_nft_puzzle`/`get_new_owner_did`, `UncurriedNFT.owner_did`) reads and surfaces this attacker-controlled value as authoritative "Owner DID"/"Minter DID" metadata via the wallet RPC and CLI, without any cryptographic verification that the claimed DID approved the mint/transfer.

### Finding Description
When an NFT singleton is created or its ownership layer is re-curried, the new "current owner" DID is taken directly from the `-10` condition emitted by the spend's inner solution: [2](#0-1)  This value is later extracted by uncurrying the resulting puzzle's curried arguments with no further validation: [3](#0-2) 

The only mechanism that is supposed to tie a DID value to the actual DID coin is the wallet-level convention of asserting a coin announcement from that DID's coin (`get_did_approval_info` / `AssertCoinAnnouncement`) — a convention followed by `NFTWallet.generate_new_nft`, but not enforced by the ownership-layer puzzle itself. Any unprivileged spend-bundle submitter who mints or transfers an NFT can construct their own eve/ownership-layer spend, setting the `-10` condition's target to an arbitrary, unrelated DID id (e.g. a well-known artist's or protocol's DID) without ever spending or controlling that DID coin. The comment in `generate_new_nft` acknowledges exactly this: the wallet deliberately keeps the DID empty on the eve coin because "eve coin DID can be set to whatever" and "wallets should always ignore DID value for eve coins as they can be set to any DID without approval." [4](#0-3) 

However, the wallet state manager's own `get_minter_did`, used when *syncing* NFTs seen on-chain (not just self-minted ones), trusts this same unauthenticated field and returns it as the canonical "minter DID" for any NFT: [5](#0-4)  This value is persisted (`WalletNftStore.save_nft`) [6](#0-5)  and surfaced through `NFTInfo.minter_did`/`owner_did` in RPC responses and CLI display (`print_nft_info`): [7](#0-6)  and [8](#0-7) 

### Impact Explanation
This allows an unprivileged NFT minter/spend-bundle submitter to forge the on-chain provenance/identity of an NFT — the exact analog of the POAP incident, where the minting system was exploited to fraudulently issue badges that appeared to be authorized by legitimate parties (XCOPY, Polygonal Mind). Here, any minter can make an NFT falsely appear to be created by/associated with an arbitrary, unrelated DID (e.g. a known collection creator), which wallets, marketplaces, and users would display and trust as "Owner DID"/"Minter DID" without any cryptographic tie to that DID actually approving the association. This is a forged-asset-identity issue impacting trust in NFT provenance across the ecosystem, rated Medium given it does not directly cause coin/fund loss but does enable spoofed collection/creator attribution that can facilitate fraud (e.g., selling forged "official" NFTs).

### Likelihood Explanation
High likelihood of exploitation: no privileged access is required. Any user who mints an NFT (a single, standard, permissionless action) can directly set the ownership layer's DID value to any bytes32 they choose in their own solution, since the ownership-layer puzzle and the `-10` condition processing performs no signature or announcement check binding the DID value to the actual DID coin. The vulnerability is implicitly acknowledged by the developers' own defensive comment in `generate_new_nft`, but that mitigation only protects the wallet's own self-minting code path — it does not protect NFTs synced from arbitrary chain data via `get_minter_did`/`manual_nft_search`.

### Recommendation
- Treat `owner_did`/`minter_did` as unverified/self-reported metadata everywhere it is surfaced (RPC responses, CLI output, GUI), and clearly label it as such, or
- When populating `minter_did`/`owner_did` from chain data in `get_minter_did`/`recurry_nft_puzzle`, additionally verify that the corresponding DID coin was spent within the same transaction/block and actually created a matching `CREATE_COIN_ANNOUNCEMENT`/authorization that the NFT spend asserted, rejecting or flagging DID associations lacking this proof.
- Update `manual_nft_search` and any other minter/owner-DID resolution paths consistently with the same warning already present in `generate_new_nft`.

### Proof of Concept
1. Mint any NFT normally (permissionless — the `nft_mint_nft` RPC or equivalent CLI command). 
2. Construct the eve-coin (or any ownership-transitioning) spend's inner solution to include a `-10` condition with `new_owner` set to a well-known DID id you do not control, e.g. a popular collection's DID, following exactly the pattern read by `get_new_owner_did`. [9](#0-8) 
3. Submit the spend bundle; because the ownership layer does not require any proof from the referenced DID's coin, this spend is accepted by consensus.
4. Any observer syncing this NFT (via `manual_nft_search`/`get_minter_did`) will report the NFT's `minter_did`/`owner_did` as the forged, well-known DID, even though that DID never authorized the mint. [10](#0-9)

### Citations

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

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L263-297)
```python
def recurry_nft_puzzle(unft: UncurriedNFT, solution: Program, new_inner_puzzle: Program) -> Program:
    log.debug("Generating NFT puzzle with ownership support: %s", disassemble(solution))
    conditions = unft.p2_puzzle.run(unft.get_innermost_solution(solution))
    new_did_id = unft.owner_did
    new_puzhash = None
    for condition in conditions.as_iter():
        if condition.first().as_int() == -10:
            # this is the change owner magic condition
            atom = condition.at("rf").atom
            if atom is None or atom == b"":
                new_did_id = None
            else:
                new_did_id = bytes32(atom)
        elif condition.first().as_int() == 51:
            new_puzhash = condition.at("rf").atom
    # assert new_puzhash and new_did_id
    log.debug(f"Found NFT puzzle details: {new_did_id!r} {new_puzhash!r}")
    assert unft.transfer_program
    new_ownership_puzzle = construct_ownership_layer(new_did_id, unft.transfer_program, new_inner_puzzle)

    return new_ownership_puzzle


def get_new_owner_did(unft: UncurriedNFT, solution: Program) -> Literal[b""] | bytes32 | None:
    conditions = unft.p2_puzzle.run(unft.get_innermost_solution(solution))
    new_did_id: Literal[b""] | bytes32 | None = None
    for condition in conditions.as_iter():
        if condition.first().as_int() == -10:
            # this is the change owner magic condition
            atom = condition.at("rf").as_atom()
            if atom == b"":
                new_did_id = b""
            else:
                new_did_id = bytes32(atom)
    return new_did_id
```

**File:** chia/wallet/nft_wallet/uncurry_nft.py (L148-163)
```python
            mod, ol_args = inner_puzzle.uncurry()
            supports_did = False
            if mod == NFT_OWNERSHIP_LAYER:
                supports_did = True
                log.debug("Parsing ownership layer")
                _, current_did_p, transfer_program, p2_puzzle = ol_args.as_iter()
                _, transfer_program_args = transfer_program.uncurry()
                _, royalty_address_p, royalty_percentage_p = transfer_program_args.as_iter()
                royalty_percentage = uint16(royalty_percentage_p.as_int())
                royalty_address = bytes32(royalty_address_p.as_atom())
                current_did_atom = current_did_p.as_atom()
                if current_did_atom == b"":
                    # For unassigned NFT, set owner DID to None
                    current_did = None
                else:
                    current_did = bytes32(current_did_atom)
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

**File:** chia/wallet/wallet_state_manager.py (L2387-2400)
```python
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

**File:** chia/cmds/wallet_funcs.py (L1391-1404)
```python
def print_nft_info(nft: NFTInfo, *, config: dict[str, Any]) -> None:
    indent: str = "   "
    owner_did = None if nft.owner_did is None else encode_puzzle_hash(nft.owner_did, AddressType.DID.hrp(config))
    minter_did = None if nft.minter_did is None else encode_puzzle_hash(nft.minter_did, AddressType.DID.hrp(config))
    print()
    print(f"{'NFT identifier:'.ljust(26)} {encode_puzzle_hash(nft.launcher_id, AddressType.NFT.hrp(config))}")
    print(f"{'Launcher coin ID:'.ljust(26)} {nft.launcher_id}")
    print(f"{'Launcher puzhash:'.ljust(26)} {nft.launcher_puzhash}")
    print(f"{'Current NFT coin ID:'.ljust(26)} {nft.nft_coin_id}")
    print(f"{'On-chain data/info:'.ljust(26)} {nft.chain_info}")
    print(f"{'Owner DID:'.ljust(26)} {owner_did}")
    print(f"{'Minter DID:'.ljust(26)} {minter_did}")
    print(f"{'Royalty percentage:'.ljust(26)} {nft.royalty_percentage}")
    print(f"{'Royalty puzhash:'.ljust(26)} {nft.royalty_puzzle_hash}")
```

**File:** chia/wallet/nft_wallet/nft_info.py (L84-91)
```python
    minter_did: bytes32 | None = None
    """DID of the NFT minter"""

    launcher_puzhash: bytes32 = SINGLETON_LAUNCHER_PUZZLE_HASH
    """Puzzle hash of the singleton launcher in hex"""

    off_chain_metadata: str | None = None
    """Serialized off-chain metadata"""
```
