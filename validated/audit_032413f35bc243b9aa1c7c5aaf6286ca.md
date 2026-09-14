## Title
Unauthenticated Self-Assignment of NFT `minter_did`/Owner DID During Mint (Forged NFT-DID Provenance) - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

## Summary
`NFTWallet.generate_new_nft()` lets any caller mint an NFT with an arbitrary `did_id` that becomes the NFT's recorded `minter_did`/owner DID and the singleton's `-10` "change owner" condition target, without ever obtaining a genuine approval spend from that DID. The intended approval step, `get_did_approval_info()`, is invoked without forwarding the caller-supplied `did_id`, so it silently substitutes the *minting wallet's own* DID (`self.did_id`) instead of validating the target DID. The code even contains a self-documented admission that this value is attacker-controlled and unverified. This is directly analogous to the SysReptor bug class: an unprivileged actor can write a privileged/identity-defining attribute onto an object they control without the authorization check that is supposed to gate it.

## Finding Description
In `generate_new_nft()`, when a caller (via wallet RPC `nft_mint_nft` → `NFTMintNFTRequest.did_id`) supplies a `did_id`, the code builds the eve NFT's ownership layer and later sets that exact `did_id` as the singleton's owner: [1](#0-0) 

The comment explicitly states the eve coin's DID "can be set to whatever" and "without approval" — but this is treated only as a client-display caveat, not fixed at the protocol/wallet layer.

The supposed approval step is: [2](#0-1) 

Note that `get_did_approval_info` is called with only `[launcher_coin.name()]` and `action_scope` — the target `did_id` is **not** passed as the third argument. Inside `get_did_approval_info`: [3](#0-2) 

Because `did_id` defaults to `None`, the function falls back to `did_id = self.did_id` — the NFT wallet's *own bound DID*, not the arbitrary `did_id` requested by the caller. The message-spend/announcement that is supposed to prove DID ownership is therefore created from the caller's own DID (if any), while the value actually written into the NFT's ownership layer (`new_owner=did_id`, `minter_did=bytes32(did_id)`) can be a completely unrelated DID the caller does not own or control.

The RPC entry point passes the request value straight through with no ownership check: [4](#0-3) 

Downstream, receiving wallets use the `-10` condition embedded in the eve coin's parent spend to classify/attribute the NFT to a DID-associated wallet: [5](#0-4) [6](#0-5) 

and the value is persisted/exposed via `NFTCoinInfo.minter_did`, `NFTInfo.owner_did`/`minter_did`, and the `nft_get_nfts` RPC response, all sourced from this unverified field.

## Impact Explanation
Any wallet user (no elevated privilege, no signature or coin-announcement from the impersonated DID) can mint an NFT that:
- Reports `minter_did`/`owner_did` equal to a DID they do not own (e.g., a well-known collection's DID, a marketplace-verified DID, or another user's DID).
- Gets automatically routed/displayed in a recipient's DID-associated NFT wallet bucket matching that forged DID, purely based on the unauthenticated `-10` condition value.

This is a forged asset identity: any client, marketplace, or wallet UI that treats `minter_did`/`owner_did` as an indicator of authentic collection/creator association (rather than treating eve-coin DID values as untrusted, as the source comment warns) can be deceived into displaying/trading a forged, seemingly "endorsed" NFT. Because the check that was supposed to enforce genuine DID approval (`get_did_approval_info`) does not receive the caller-supplied `did_id`, there is no code path that actually prevents this — the "protection" against forged eve-coin DID values is only a comment, not an enforcement mechanism.

## Likelihood Explanation
Trivial to trigger: a single call to the standard `nft_mint_nft` wallet RPC (or `NFTWallet.generate_new_nft()` directly) with an arbitrary `did_id` string is sufficient. No cryptographic material from the target DID, no special permissions, and no interaction with the impersonated DID's owner is required.

## Recommendation
- In `generate_new_nft()`, pass the caller-supplied `did_id` explicitly into `get_did_approval_info(..., did_id)` so the approval message spend is genuinely tied to the DID being claimed as owner, and fail (as `get_did_approval_info` already does via its `else: raise ValueError`) when the caller does not control a DID wallet matching that `did_id`.
- Audit all call sites of `get_did_approval_info` for the same missing-parameter pattern (e.g., `set_nft_did`, `set_bulk_nft_did`) to ensure the approval is always obtained from the DID that will end up as `new_owner`, not from an unrelated bound DID.
- Consider hardening downstream consumers (`identify()`, `NFTInfo` exposure) so `owner_did`/`minter_did` are not treated as trusted until confirmed by a subsequent, cryptographically-verified transfer, matching the intent already noted in the source comment.

## Proof of Concept
1. Attacker creates/controls a standalone NFT wallet (no DID, or a DID they own) and calls the wallet RPC `nft_mint_nft` (`NFTMintNFTRequest`) with `did_id` set to the hex ID of a DID they do **not** control (e.g., a publicly known collection DID).
2. `WalletRpcApi.nft_mint_nft()` → `NFTWallet.generate_new_nft()` builds the ownership layer with that `did_id`, and `get_did_approval_info([launcher_coin.name()], action_scope)` is called without forwarding `did_id`, so it resolves approval against `self.did_id` (possibly `None`, or a different DID the attacker legitimately owns) instead of validating the target DID.
3. The eve NFT's singleton is spent with `new_owner=did_id` (attacker's chosen, unrelated DID) and `NFTCoinInfo.minter_did=bytes32(did_id)`.
4. Query `nft_get_nfts`/`get_nft_info`: the returned `NFTInfo.minter_did`/`owner_did` shows the impersonated DID, and any recipient wallet with that DID wallet configured will bucket the NFT under it — all without any spend or signature ever originating from the real DID owner.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L549-561)
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
```

**File:** chia/wallet/wallet_rpc_api.py (L2401-2418)
```python
        if request.did_id is not None:
            if request.did_id == "":
                did_id: bytes | None = b""
            else:
                did_id = decode_puzzle_hash(request.did_id)
        else:
            did_id = request.did_id

        nft_id = await nft_wallet.generate_new_nft(
            metadata,
            action_scope,
            target_puzhash,
            royalty_puzhash,
            request.royalty_percentage,
            did_id,
            request.fee,
            extra_conditions=extra_conditions,
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
