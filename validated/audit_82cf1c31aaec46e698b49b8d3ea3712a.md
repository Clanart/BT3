### Title
DID recovery/backup authority persists across ownership transfer, letting the seller's chosen recovery DIDs keep spend authority over the DID after sale - (File: `chia/wallet/did_wallet/did_wallet.py`)

### Summary
`transfer_did()` — the function used to sell/hand over a Chia DID singleton to a new owner — reuses the existing `backup_ids` / `num_of_backup_ids_needed` (the DID's recovery/backup authority list) when constructing the new owner's inner puzzle, unless the caller explicitly clears them first. This is the direct structural analog of the Footium bug: an authority granted by the *previous* controller (here, a set of "backup DIDs" empowered to recover/spend the singleton) survives a change of the asset's controlling key and is not automatically revoked at transfer time.

### Finding Description
`DIDWallet.transfer_did()` builds the new DID inner puzzle hash by currying in the seller's existing recovery configuration: [1](#0-0) 

`backup_ids = self.did_info.backup_ids` and `backup_required = self.did_info.num_of_backup_ids_needed` are taken verbatim from the DID's current state and curried into `new_did_puzhash` for the buyer's coin — there is no forced reset of the recovery/backup list on transfer. `reset_recovery_list()` only nils out the recovery hash when `backup_ids` is already empty, so if the seller left backups configured, they are propagated onto the new owner's puzzle: [2](#0-1) 

The `get_innerpuz_for_new_innerhash()` helper (used for the analogous "load info for new owner" path) documents this behavior explicitly in its own comment: the recovery list is intentionally kept, and it's up to the seller to clean it before selling: [3](#0-2) 

DID recovery (the `-10`/backdoor "recovery mode" spend path of `DID_INNERPUZ`) is meant to let the DIDs listed in `backup_ids` co-sign a spend that recovers/moves the singleton to a new p2 puzzle when the current owner's key is lost. Because the CLI/RPC layer for `DidTransferDidCMD` only offers a `reset_recovery` flag that defaults to `False` (`chia/cmds/wallet.py:862-864`), and `DIDTransferDID` no longer requires resetting recovery at the RPC boundary at all (`with_recovery_info` must always be `True`, per `chia/wallet/wallet_request_types.py:1697-1699`), an inattentive seller can transfer a DID while leaving the old backup DID set installed, live, and curried into the puzzle hash the buyer now owns.

### Impact Explanation
If the seller previously configured backup/recovery DIDs (their own alternate keys, or DIDs controlled by a partner they trusted at the time), those DIDs retain co-signing/recovery authority over the DID singleton after it is sold. The seller can:
- Front-run the transfer or simply leave existing backups in place, then later use the recovery path (a raw, unprivileged spend bundle constructed against the DID's current puzzle — reachable by anyone holding the backup DID's key, independent of any removed convenience RPC) to hijack or move the singleton the buyer believes they now solely control.
- Combine this with NFTs/assets bound to that DID (ownership layer transfers, `set_nft_did`, message-spend announcements) since NFT and CR-CAT authorization flows key off DID identity; a still-authorized backup DID undermines the buyer's exclusive control of everything gated behind that DID.

This is an unsigned/unauthorized-coin-movement class issue: a party other than the current, intended controller retains spend authority over a singleton coin due to state (the recovery list) that is not automatically invalidated on ownership change.

### Likelihood Explanation
Likelihood is contingent on the seller having configured backup DIDs before selling (a real, if not default, DID feature) and not resetting them at transfer time. The RPC/CLI defaults (`reset_recovery: bool = False`) actively encourage the vulnerable state to persist by default, and the DID transfer path never forces a check. Compared to the Footium escrow bug (which requires an active on-chain approval before sale), this requires a pre-existing recovery configuration, making exploitation opportunistic rather than universal, but the vector is fully reachable by an ordinary wallet user acting as seller with no special privilege.

### Recommendation
- Change `transfer_did()` (and `get_innerpuz_for_new_innerhash()`) to require or default to clearing `backup_ids`/`num_of_backup_ids_needed` on transfer unless the new owner explicitly opts in to keep them (invert the current default).
- Surface a strong warning/confirmation in `DidTransferDidCMD` when recovery is not reset, and consider making buyer-side wallets detect and flag non-empty recovery lists on newly-received DIDs before treating the asset as fully "theirs."
- At minimum, document and enforce that marketplaces/offer flows for DIDs verify `recovery_list_hash` is nil (or a buyer-approved set) before settling a DID sale, mirroring the Footium recommendation to gate/limit who can hold standing authority over the asset.

### Proof of Concept
1. Owner A creates a DID with `backup_ids = [B]`, `num_of_backup_ids_needed = 1` (a normal, supported DID recovery configuration) via `create_new_did_wallet`.
2. Owner A sells/transfers the DID to Owner C using `did_transfer_did` / `DidTransferDidCMD` with default `reset_recovery=False` (or any path that does not explicitly clear the recovery list) — see `chia/wallet/did_wallet/did_wallet.py:805-836` and `chia/cmds/wallet.py:850-883`.
3. The resulting DID coin owned by C still curries in recovery authority for DID `B` (`chia/wallet/did_wallet/did_wallet.py:825-832`), because `backup_ids`/`reset_recovery_list()` were not cleared.
4. Owner A (who controls B) constructs a raw spend bundle invoking the DID's recovery/backdoor solution path against the DID's current coin, using B's signature, and submits it directly to the mempool — no wallet RPC gating is required since this is a CLVM-level puzzle capability, not merely an RPC feature. This lets A recover/redirect the DID coin despite C being the new nominal owner, exactly analogous to the previous-owner "front-running with pre-approved authority" scenario described in the Footium report.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L820-832)
```python
        coin = await self.get_coin()
        backup_ids = []
        backup_required = uint64(0)
        backup_ids = self.did_info.backup_ids
        backup_required = self.did_info.num_of_backup_ids_needed
        new_did_puzhash = did_wallet_puzzles.get_inner_puzhash_by_p2(
            p2_puzhash=new_puzhash,
            recovery_list=backup_ids,
            num_of_backup_ids_needed=backup_required,
            launcher_id=self.did_info.origin_coin.name(),
            metadata=did_wallet_puzzles.metadata_to_program(json.loads(self.did_info.metadata)),
            recovery_list_hash=self.reset_recovery_list(),
        )
```

**File:** chia/wallet/did_wallet/did_wallet.py (L976-992)
```python
    async def get_innerpuz_for_new_innerhash(self, pubkey: G1Element) -> Program:
        """
        Get the inner puzzle for a new owner
        :param pubkey: Pubkey
        :return: Inner puzzle
        """
        # Note: the recovery list will be kept.
        # In a selling case, the seller should clean the recovery list then transfer to the new owner.
        assert self.did_info.origin_coin is not None
        return did_wallet_puzzles.create_innerpuz(
            p2_puzzle_or_hash=puzzle_for_pk(pubkey),
            recovery_list=self.did_info.backup_ids,
            num_of_backup_ids_needed=uint64(self.did_info.num_of_backup_ids_needed),
            launcher_id=self.did_info.origin_coin.name(),
            metadata=did_wallet_puzzles.metadata_to_program(json.loads(self.did_info.metadata)),
            recovery_list_hash=self.reset_recovery_list(),
        )
```

**File:** chia/wallet/did_wallet/did_wallet.py (L1017-1032)
```python
    def reset_recovery_list(self) -> Program | None:
        if self.did_info.current_inner is None:
            return None

        uncurried_args = uncurry_innerpuz(self.did_info.current_inner)
        if uncurried_args is None:
            return None

        _, og_recovery_list_hash, _, _, _ = uncurried_args
        if self.did_info.num_of_backup_ids_needed == 0 and not did_recovery_is_nil(og_recovery_list_hash):
            return None

        if len(self.did_info.backup_ids) > 0:
            return None

        return og_recovery_list_hash
```
