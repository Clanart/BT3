### Title
Eve NFT mint lets a minter forge an arbitrary DID owner without approval - ([File: chia/wallet/nft_wallet/nft_wallet.py])

### Summary
This is the closest in-scope analog to CVE-2025-54875's pattern: a field that should only be settable by an authorized/verified party (`new_user_is_admin` in FreshRSS) is instead accepted from the unprivileged actor performing the action, and downstream consumers can be tricked into trusting it as if it were verified. In Chia, the eve spend of an NFT with the ownership layer lets the *minter* — who does not need to hold, sign for, or control the target DID at all — set the NFT's ownership-layer `current_owner` field to any DID they want, with no cryptographic proof of DID approval.

### Finding Description
`NFTWallet.generate_new_nft()` explicitly documents and implements this: the ownership layer's `current_owner` curried parameter is initialized empty at the eve puzzle, and the "real" DID assignment is applied only in the eve *solution* (the `-10` "change owner" magic condition) via `new_owner=did_id`, with a code comment acknowledging the danger: [1](#0-0) 

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

The `-10` condition that actually sets `new_owner` on the eve spend is applied without any signature, announcement, or on-chain proof binding the DID's actual owner/inner-puzzle to this specific mint — the "DID approval" (`get_did_approval_info`) is only invoked when the *minter's own* DID wallet is present locally and chooses to call it; there is no CLVM-level enforcement forcing every claimed `did_id` to correspond to an actual DID announcement in the same spend bundle for the eve case: [2](#0-1) 

Downstream, `WalletStateManager.get_minter_did()` and `nft_wallet.identify()` / `NFTWallet.puzzle_solution_received()` parse this claimed owner DID directly out of the eve solution via `get_new_owner_did()` and treat it as the NFT's `minter_did`/`owner_did` for wallet bookkeeping, RPC responses (`NFTInfo.minter_did`, `owner_did`), and cross-wallet routing decisions (which NFT wallet a coin belongs to, based on matching `did_id`): [3](#0-2) [4](#0-3) 

Because the comment itself says "wallets should always ignore DID value for eve coins," this is a known, documented trust boundary that any code path not following the warning (RPC display, DID-based wallet routing/`identify()`, trade/offer summaries that inspect `owner_did`) can be misled by a value the minter fabricated with no proof of DID control.

### Impact Explanation
An attacker (any local wallet user acting as an NFT minter — no special privilege required) can mint an NFT that falsely claims to be owned/created-by/approved-by an arbitrary DID they do not control, analogous to setting `new_user_is_admin=true` on a registration request that should only be settable by an already-authenticated admin. This is a forged-asset-identity issue: NFT provenance/minter attribution (`minter_did`, `owner_did`) — which marketplaces, offer summaries, and wallet UIs rely on to convey authenticity/ownership — can be spoofed without the claimed DID's cooperation, potentially deceiving a counterparty into accepting an offer or trusting provenance based on a DID that never approved the mint.

### Likelihood Explanation
High likelihood of triggering the underlying behavior — it requires only a standard NFT mint RPC call (`nft_mint_nft` / `generate_new_nft`) supplying an arbitrary `did_id` argument; no signature or on-chain proof from that DID is enforced at the eve spend. The residual risk (whether it causes real financial harm) depends on whether any code path treats the value as verified rather than following the documented warning to ignore eve-coin DID claims; this codebase already has an explicit written warning about this exact hazard, indicating it is a recognized but only partially mitigated trust boundary.

### Recommendation
- Ensure every code path that surfaces `owner_did`/`minter_did` for an eve-state NFT (RPC responses, `identify()`/wallet-routing logic, offer/trade summaries, marketplace-facing APIs) explicitly discards or marks-unverified the claimed DID until a subsequent, non-eve spend re-asserts DID ownership through the standard on-chain approval/announcement flow.
- Audit `WalletStateManager.get_minter_did()`, `NFTWallet.identify()`, and any RPC/CLI surface that reports `minter_did` for eve coins to confirm they are not silently treated as authoritative ownership claims.
- Consider requiring an on-chain DID announcement/proof at the eve spend itself (mirroring `get_did_approval_info`) so that DID assignment can never be claimed without cryptographic participation from the DID owner, closing the gap the code comment currently only documents defensively.

### Proof of Concept
1. Using the wallet RPC, call `nft_mint_nft` (backed by `NFTWallet.generate_new_nft()`) with `did_id` set to any DID launcher id the caller does not own/control.
2. Observe that the mint succeeds: the eve ownership-layer puzzle is created with an empty `current_owner`, and the eve solution's `-10` condition sets `new_owner` to the attacker-chosen `did_id` with no signature or announcement from that DID.
3. Query the resulting NFT via `nft_get_info` / `list_nfts`: `minter_did`/`owner_did` reflect the attacker-chosen DID, exactly as parsed by `get_minter_did()`/`get_new_owner_did()`, even though that DID never participated in or approved the mint.

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L286-360)
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
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L500-512)
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
        else:
            self.log.debug("Creating standard NFT")
            inner_puzzle = p2_inner_puzzle
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
