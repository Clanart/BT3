### Title
Minters can curry an arbitrary DID/organization ID into a freshly-minted NFT eve coin, letting them impersonate ownership/endorsement by another DID without any approval spend - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
The reported OpenQ bug is that a caller can freely choose an `_organization` identifier when minting a bounty, with no on-chain proof that they actually control/represent that organization, allowing impersonation. The same bug class exists in the NFT ownership-layer minting path in this repo: the DID value curried into a brand-new ("eve") NFT coin's ownership layer is attacker-controlled at mint time and carries no cryptographic proof of approval from the claimed DID. Only this wallet's own minting code deliberately zeroes it out; the protocol itself does not enforce that.

### Finding Description
When minting a DID-owned NFT, `NFTWallet.generate_new_nft` builds the ownership-layer inner puzzle for the eve coin via `create_ownership_layer_puzzle(launcher_coin.name(), b"", p2_inner_puzzle, percentage, royalty_puzzle_hash=royalty_puzzle_hash)`, with the code comment explicitly stating: [1](#0-0) 

> "eve coin DID can be set to whatever so we keep it empty. WARNING: wallets should always ignore DID value for eve coins as they can be set to any DID without approval"

This confirms the underlying protocol fact: the initial `current_owner` field curried into `NFT_OWNERSHIP_LAYER` (via `construct_ownership_layer`) is a free-form value chosen by whoever mints the singleton - it is not verified against any actual approval or spend from the claimed DID: [2](#0-1) 

A malicious minter using a different (non-conforming) minting implementation can curry in any DID's `bytes32` id (e.g., a well-known organization's public DID) as `current_owner` for the eve coin, exactly analogous to OpenQ's `_organization` string being freely chosen with no ownership check.

On the receiving side, `NFTWallet.identify` derives `owner_did`/`new_did_id` from the uncurried puzzle of the NFT coin and looks for a locally-held `DIDWallet` whose ID matches, to decide which DID-scoped NFT wallet the NFT belongs to: [3](#0-2) [4](#0-3) 

Because the eve coin's `owner_did` (via `UncurriedNFT.owner_did`, populated straight from the curried `current_owner`) is trusted as the NFT's DID at first sight, any wallet or downstream consumer (e.g. marketplaces, `NFTGetInfo`/`NFTGetNFTs` RPC responses, or auto-organized "DID collection" wallets) that surfaces `owner_did`/`minter_did`/collection membership as an indicator of an organization's endorsement can be spoofed by a minter who never obtained approval from the real DID owner.

### Impact Explanation
This allows a malicious actor to mint NFTs that appear associated with (or "endorsed by") a legitimate DID/organization they do not control - enabling phishing, brand impersonation, and misleading collection attribution, mirroring the OpenQ organization-spoofing impact. It does not, however, grant the attacker actual control over the real DID's coins or ability to transfer/claim assets belonging to the legitimate DID; the impersonation is limited to identity/attribution spoofing at mint time, not asset theft.

### Likelihood Explanation
Likelihood is bounded by client behavior: this repo's own minting code proactively zeros the eve DID and only sets the real owner via a subsequent DID-approved `-10` spend (`get_did_approval_info`), which is the documented mitigation. However, since the protocol itself does not enforce this - it's purely a client-side convention flagged by a `WARNING` comment - any non-compliant minter (custom tooling, competing wallet, malicious script) can trivially curry an arbitrary DID at mint time, and any downstream consumer that reads `owner_did`/collection membership without checking for a subsequent approved DID-set spend would be misled.

### Recommendation
Treat the eve/first-seen NFT's curried DID value as untrusted for attribution purposes across all consumers (wallet UI, RPC `NFTInfo`, marketplaces): only recognize DID ownership once a DID-approved spend (the `-10` condition with matching DID approval/announcement) has actually occurred on-chain, never from the singleton's initial curry parameters alone. Consider surfacing an explicit "unverified/self-declared owner" flag in `NFTInfo`/`NFTGetNFTs` responses when the DID association has not been confirmed via an approved spend, so integrators don't silently trust it as OpenQ integrators trusted the free-form `_organization` string.

### Proof of Concept
1. A malicious party crafts a singleton launcher spend that curries `NFT_OWNERSHIP_LAYER` with `current_owner` set to a well-known organization's DID id (instead of `b""`), using `create_ownership_layer_puzzle`/`construct_ownership_layer` directly rather than going through `NFTWallet.generate_new_nft`'s safe path. [5](#0-4) 
2. The resulting eve NFT coin is broadcast/received by a victim's wallet that runs `NFTWallet.identify`, which reads `uncurried_nft.owner_did` directly from the coin's curry parameters and, if a local `DIDWallet` for that DID id exists, files the NFT as belonging to that organization's collection. [6](#0-5) 
3. No spend from the real DID ever occurred; the association is purely a self-declared, attacker-chosen value - identical in spirit to OpenQ's unchecked `_organization` string in `mintBounty`.

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L286-327)
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
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L328-391)
```python
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

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L180-185)
```python
def construct_ownership_layer(
    current_owner: bytes32 | None,
    transfer_program: Program,
    inner_puzzle: Program,
) -> Program:
    return NFT_OWNERSHIP_LAYER.curry(NFT_OWNERSHIP_LAYER_HASH, current_owner, transfer_program, inner_puzzle)
```

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L188-211)
```python
def create_ownership_layer_puzzle(
    nft_id: bytes32,
    did_id: bytes,
    p2_puzzle: Program,
    percentage: uint16,
    royalty_puzzle_hash: bytes32 | None = None,
) -> Program:
    log.debug(
        "Creating ownership layer puzzle with NFT_ID: %s DID_ID: %s Royalty_Percentage: %d P2_puzzle: %s",
        nft_id.hex(),
        did_id,
        percentage,
        p2_puzzle,
    )
    singleton_struct = Program.to((SINGLETON_TOP_LAYER_MOD_HASH, (nft_id, SINGLETON_LAUNCHER_PUZZLE_HASH)))
    if not royalty_puzzle_hash:
        royalty_puzzle_hash = p2_puzzle.get_tree_hash()
    transfer_program = NFT_TRANSFER_PROGRAM_DEFAULT.curry(singleton_struct, royalty_puzzle_hash, percentage)
    nft_inner_puzzle = p2_puzzle

    nft_ownership_layer_puzzle = construct_ownership_layer(
        bytes32(did_id) if did_id else None, transfer_program, nft_inner_puzzle
    )
    return nft_ownership_layer_puzzle
```
