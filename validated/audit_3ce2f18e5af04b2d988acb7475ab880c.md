This confirms the analog: the NFT ownership layer's minter/owner field is a well-known, explicitly-flagged case where "owner" identity is attacker-controlled at mint time without any cryptographic proof, mirroring the "owner not initialized in `initializer()`" Solidity bug class where anyone can claim/assign ownership. The `minter_did` value is read directly off-chain from the eve spend's `-10` condition and stored/displayed as trusted provenance data (`NFTInfo.minter_did`, CLI `print_nft_info`), even though the code comment itself admits this value is forgeable.

### Title
NFT minter/creator identity can be forged at mint time due to unauthenticated owner field in the ownership layer - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
When minting a "provenant" (DID-linked) NFT, the eve singleton coin's ownership layer `current_owner` field is set via a `-10` (change-owner) condition inside the same spend bundle that creates the NFT. This condition is not authenticated by the claimed DID in any way at the eve-spend layer — a minter can put any DID's identity into this field for their own coin spend, and downstream code (`get_minter_did`, `NFTCoinInfo.minter_did`, `NFTInfo.minter_did`) treats this value as the trusted "creator/minter" identity, without verifying that the referenced DID ever authorized or announced the mint.

### Finding Description
`NFTWallet.generate_new_nft` explicitly documents this gap: [1](#0-0) 

The code deliberately curries an empty owner (`b""`) into the eve ownership-layer puzzle and instead relies on a subsequent `-10` condition in the eve spend's solution to set the "real" owner/minter DID: [2](#0-1) 

That `-10` magic condition is interpreted purely from the puzzle solution with no signature or announcement proof tying it to the named DID's actual authorization — it's just an argument the spend bundle author supplies: [3](#0-2) 

`WalletStateManager.get_minter_did` reads this unauthenticated field straight from the eve coin spend and treats it as authoritative minter identity: [4](#0-3) 

This value then flows unchanged into persisted wallet state (`NFTCoinInfo.minter_did`) and into the user-facing `NFTInfo.minter_did` field surfaced by RPC/CLI (`get_nft_info_from_puzzle`, `print_nft_info`): [5](#0-4) [6](#0-5) 

The one place this is explicitly called out is inside `generate_new_nft`'s own comment ("WARNING: wallets should always ignore DID value for eve coins as they can be set to any DID without approval"), which is a workaround/mitigation acknowledgment rather than a fix at the puzzle level — the ownership-layer CLVM puzzle itself does not require any proof that the DID referenced in the `-10` condition consented to being named as owner/minter.

### Impact Explanation
Because the minter/creator DID is essentially a self-declared, unauthenticated field baked into the spend that any unprivileged minter fully controls, an attacker can forge the on-chain "minter_did"/provenance of an NFT to impersonate a reputable creator's DID. This directly matches the "forged asset identity" impact class: a counterparty inspecting `NFTInfo.minter_did` (via CLI, RPC, or a marketplace/offer flow built on this data) has no cryptographic guarantee that the named DID actually created or endorsed the NFT, which can be used to deceive offer counterparties into over-valuing or accepting a forged-provenance NFT in trade.

### Likelihood Explanation
This requires no privileged access — any wallet user minting an NFT with `did_id` set can trigger this path (`generate_new_nft` is exposed via the wallet RPC `nft_mint_nft` endpoint), so it is trivially reachable by a single unprivileged wallet action/spend bundle.

### Recommendation
Do not treat the eve-spend `-10` owner condition as authoritative "minter" provenance without independent verification (e.g., requiring the named DID's own coin to co-sign/announce the mint transaction, or clearly separating "self-declared minter hint" from any RPC/CLI field name that implies a verified/authoritative identity). At minimum, the RPC/CLI surface (`NFTInfo.minter_did`, `print_nft_info`) should document that this value is unauthenticated and cosmetic only, and any downstream consumer (offer/marketplace tooling) must independently verify DID-of-creation claims rather than trusting `minter_did`.

### Proof of Concept
1. Attacker mints an NFT via the wallet RPC `nft_mint_nft` with `did_id` set to a well-known/reputable DID that the attacker does not control (this is only enforced client-side by the CLI wallet flow; a hand-crafted spend bundle can set the `-10` condition's DID argument to any value, since `create_ownership_layer_puzzle`/`generate_new_nft` curry an empty owner and rely purely on the solution-level condition).
2. The resulting eve coin spend is pushed to the mempool and confirmed like any normal NFT mint.
3. Any node/wallet computing `get_minter_did` on this NFT (chia/wallet/wallet_state_manager.py:1074-1112) will report the forged DID as `minter_did`, which is displayed as "Minter DID" via `chia/cmds/wallet_funcs.py:1391-1402` and returned via NFT RPC info, with no cryptographic indication that this is unverified data.

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

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L99-123)
```python
    nft_info = NFTInfo(
        encode_puzzle_hash(uncurried_nft.singleton_launcher_id, prefix=AddressType.NFT.hrp(config=config)),
        uncurried_nft.singleton_launcher_id,
        nft_coin_info.coin.name(),
        nft_coin_info.latest_height,
        uncurried_nft.owner_did,
        uncurried_nft.trade_price_percentage,
        uncurried_nft.royalty_address,
        data_uris,
        uncurried_nft.data_hash.as_python(),
        meta_uris,
        uncurried_nft.meta_hash.as_python(),
        license_uris,
        uncurried_nft.license_hash.as_python(),
        uint64(uncurried_nft.edition_total.as_int()),
        uint64(uncurried_nft.edition_number.as_int()),
        uncurried_nft.metadata_updater_hash.as_python(),
        disassemble(uncurried_nft.metadata),
        nft_coin_info.mint_height,
        uncurried_nft.supports_did,
        uncurried_nft.p2_puzzle.get_tree_hash(),
        nft_coin_info.pending_transaction,
        nft_coin_info.minter_did,
        off_chain_metadata=off_chain_metadata,
    )
```

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L286-297)
```python
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

**File:** chia/cmds/wallet_funcs.py (L1391-1402)
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
```
