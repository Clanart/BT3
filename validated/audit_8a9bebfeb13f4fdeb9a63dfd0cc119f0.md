### Title
NFT eve-coin minter DID can be forged by any unprivileged minter, corrupting `minter_did`/collection attribution used by wallets and RPC consumers - (`File: chia/wallet/nft_wallet/nft_wallet.py`, `chia/wallet/wallet_state_manager.py`)

### Summary
Analogous to CVE-2015-8688 (Gajim accepting an unauthenticated/unauthorized roster-push stanza and using it to alter trusted identity state), the Chia NFT minting path lets the party who spends the singleton **eve** coin set the ownership-layer "current owner/DID" field to *any* value, with no verification that the claimed DID actually approved or participated in the mint. Downstream wallet code (`WalletStateManager.get_minter_did()`) reads that unauthenticated value out of the eve spend's solution and persists it as the NFT's `minter_did`, which is then surfaced through RPC/UI (`NFTInfo.minter_did`, `NFTCoinInfo.minter_did`) as if it were an authenticated attribution of "which DID collection minted this NFT."

### Finding Description
When an NFT with DID support is minted, the eve-coin's ownership layer is deliberately created with an **empty** DID (`create_ownership_layer_puzzle(launcher_coin.name(), b"", ...)`), and the code explicitly documents that this value cannot be trusted: [1](#0-0) 

```
if did_id is not None:
    self.log.debug("Creating provenant NFT")
    # eve coin DID can be set to whatever so we keep it empty
    # WARNING: wallets should always ignore DID value for eve coins as they can be set
    #          to any DID without approval
    inner_puzzle = create_ownership_layer_puzzle(
        launcher_coin.name(), b"", p2_inner_puzzle, percentage, royalty_puzzle_hash=royalty_puzzle_hash
    )
```

The subsequent (real) transfer spend of the eve coin is where the `-10` "change owner" magic condition sets the actual DID value into the ownership layer, e.g. via `set_nft_did`/`generate_signed_transaction`, and that is the value that ships on-chain in the eve coin's solution. Because a minter fully controls both the eve coin puzzle and its solution (it is their own coin, signed by their own key), they can put **any** `bytes32` (any DID's identity) into the `-10` change-owner condition. Nothing in the singleton, CAT, or DID puzzle stack requires that the referenced DID coin sign, announce, or otherwise approve this claim at eve-spend time — the DID puzzle's actual authorization gate (`AssertPuzzleAnnouncement`/`AssertCoinAnnouncement` from the real DID coin) is only enforced when the ownership layer is later spent to *change* to a DID in normal transfer flows, not for the attacker's own initial claim embedded in the eve spend solution that other wallets subsequently read back out.

`WalletStateManager.get_minter_did()` retrieves exactly this unauthenticated field: [2](#0-1) 

It fetches the eve coin's spend, uncurries it, and calls `get_new_owner_did(eve_uncurried_nft, ...)` — a helper that just reads the `-10` condition's argument out of the (attacker-authored) eve solution: [3](#0-2) 

```
def get_new_owner_did(unft: UncurriedNFT, solution: Program) -> Literal[b""] | bytes32 | None:
    conditions = unft.p2_puzzle.run(unft.get_innermost_solution(solution))
    new_did_id: Literal[b""] | bytes32 | None = None
    for condition in conditions.as_iter():
        if condition.first().as_int() == -10:
            ...
            new_did_id = bytes32(atom)
    return new_did_id
```

This value is then stored verbatim as `minter_did` on `NFTCoinInfo`/`NFTInfo` (`chia/wallet/nft_wallet/nft_info.py` lines 84, 109) and exposed through NFT RPC responses and CLI tooling (`chia/cmds/wallet_funcs.py`), with no signature check, DID-coin coin-spend proof, or announcement assertion tying the claimed DID to the actual mint. Any user can therefore mint an NFT that falsely claims to have been minted "by" a well-known/valuable DID (e.g., a verified artist or brand collection identity), because the field is populated purely from attacker-supplied solution data rather than a cryptographically verified relationship — the same class of bug as Gajim trusting an unauthenticated roster-push stanza to assert identity/relationship state.

### Impact Explanation
`minter_did` is used as a provenance/collection-attribution signal surfaced to end users and integrators (wallet UI, RPC, `NFTGetInfo`). Marketplaces, offer UIs, or automated tooling that treat `minter_did` as authenticated collection provenance (a common real-world pattern for NFT collection verification) can be misled into treating forged NFTs as genuinely part of a trusted collection. This enables spoofed asset identity/provenance fraud that can facilitate offer counterpart-deception (a buyer believing they are purchasing a "verified collection" NFT when they are not), which maps to the "forged asset identity" acceptance criterion. Impact is Medium: it does not directly move funds or forge fungible-asset identity (CAT/XCH), but it forges a persisted identity/provenance attribute that is exposed to and reasonably relied upon by wallet users and RPC consumers, mirroring the roster-spoofing/identity-forgery nature of the original CVE.

### Likelihood Explanation
Any unprivileged user minting an NFT through the standard `generate_new_nft` flow can trivially set `did_id` to an arbitrary DID launcher id they do not own or control (this is a single-transaction, self-authorized mint using only their own coins/signature). No cooperation from the impersonated DID is required, and the resulting `minter_did` deception silently propagates through wallet sync (`get_minter_did`) and RPC to any consumer that reads the field. Likelihood is High for the forgery step itself; overall severity is bounded by how much downstream tooling trusts the field without independent verification (consistent with the code's own comment acknowledging it is unauthenticated).

### Recommendation
- Treat `minter_did`/eve-coin DID claims as strictly untrusted display metadata; clearly label it in all RPC/API responses as "self-reported, unverified" rather than a "minter DID."
- Where provenance verification is required, require an actual DID-signed approval/announcement (as already used for real ownership transfers) that ties the claimed DID coin to the specific launcher id at mint time, and only populate `minter_did` when such proof exists.
- Audit and update any RPC/CLI/UI surface (`chia/cmds/wallet_funcs.py`, NFT info displays) that currently presents `minter_did` as an authoritative collection/minter identity to add an explicit "unverified" disclaimer or remove the field from trust-sensitive flows.

### Proof of Concept
1. Attacker owns a standard wallet with `did_id=None` NFT wallet capability (no need to control the DID `did_id` they will claim).
2. Attacker calls the NFT mint flow (`NFTWallet.generate_new_nft`) supplying `did_id = <victim's well-known DID launcher id>`. The code path builds the eve coin with empty owner (per lines 501-509) then, in the follow-up eve spend (`generate_signed_transaction`/inner solution), inserts a `-10` change-owner condition carrying `did_id = <victim DID>` — signed only by the attacker's own key, with no participation from the victim DID coin.
3. Push the resulting spend bundle to the mempool/full node; it is accepted because the CLVM ownership layer only requires the *minter's* p2 puzzle signature, not the referenced DID's.
4. Any wallet or RPC client that later calls `get_minter_did()`/`NFTGetInfo` on this NFT will report `minter_did == <victim DID>`, i.e., a forged provenance claim persisted and surfaced as if verified.

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
