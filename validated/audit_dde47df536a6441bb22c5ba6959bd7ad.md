### Title
Previous DID owner can retain a hidden "recovery" backdoor across ownership transfer, allowing them to steal control of the DID after sale - ([File: chia/wallet/did_wallet/did_wallet.py])

### Summary
Chia's `DIDWallet.transfer_did` and related inner-puzzle construction helpers do not automatically clear the DID's `backup_ids` (recovery list) when a DID is transferred to a new owner. Because Chia DIDs support a "recovery" mode where a threshold of `backup_ids` can attest to and take over a DID coin, a seller who has set `backup_ids` (e.g., to their own alternate identity) before selling the DID can carry that recovery capability forward into the buyer's coin, letting them later reclaim/steal the DID (and any NFTs/assets gated behind DID ownership) — the exact "approval persists after ownership change" bug class described in the external report.

### Finding Description
`DIDWallet.transfer_did` constructs the new owner's inner puzzle using `self.did_info.backup_ids` and `self.did_info.num_of_backup_ids_needed` taken from the *current* (seller's) DID info, and only substitutes a cleared/hashed recovery list when `reset_recovery_list()` decides to: [1](#0-0) 

`reset_recovery_list()` explicitly refuses to clear the list whenever `backup_ids` is non-empty, returning `None` (which causes the raw `backup_ids` to be curried into the new owner's puzzle) instead of wiping it: [2](#0-1) 

The same pattern occurs in `get_innerpuz_for_new_innerhash`, whose own comment acknowledges the exact hazard: the recovery list is preserved across ownership transfer, and it is the seller's responsibility to manually clear it before selling: [3](#0-2) 

Because the recovery list (a set of "backup" DIDs that can jointly attest to reclaim/recreate ownership of the DID coin) is preserved by default, a previous owner who included their own alternate DID (or a colluding DID) as a backup id before selling retains the ability to initiate a DID recovery spend against the buyer's coin after the sale — without needing the buyer's cooperation or signature — exactly mirroring the reported bug class where a prior owner "approves" themselves before transferring ownership and exploits that approval afterward.

### Impact Explanation
If exploited, a buyer of a DID (and any NFTs, marketplace listings, or protocol roles gated by DID ownership, e.g., NFT ownership-layer transfers, Data Layer admin DIDs, or VC issuance authority tied to the DID) can have that DID coin recovered/hijacked by the previous owner via the recovery mechanism, resulting in unauthorized transfer of the singleton coin and control over any assets or privileges tied to that DID. This is a concrete unauthorized coin movement/ownership hijack, consistent with Medium severity.

### Likelihood Explanation
Exploitation requires the previous owner to have deliberately set `backup_ids` to their own control before selling and for the buyer/new-owner tooling to not notice or clear the recovery list post-transfer — the wallet code does not enforce clearing it, and the in-code comment confirms this is left to manual seller diligence ("the seller should clean the recovery list then transfer to the new owner"). Because this is opt-in behavior by the seller rather than an automatic safeguard, likelihood depends on buyer/marketplace tooling verifying `backup_ids` before purchase, similar to the original ERC20/721 approval scenario.

### Recommendation
When transferring a DID to a new owner (`transfer_did`, `get_innerpuz_for_new_innerhash`), default to clearing `backup_ids`/`num_of_backup_ids_needed` (i.e., force `reset_recovery_list` to null the list) unless the new owner explicitly opts to retain the existing recovery configuration. At minimum, wallet RPCs/CLI should surface a clear warning and require explicit confirmation when transferring a DID with a non-empty recovery list, and marketplace/offer tooling should treat a non-empty `backup_ids` as a red flag before accepting a DID-based purchase.

### Proof of Concept
1. Seller creates a DID with `backup_ids = [seller_alt_did]`, `num_of_backup_ids_needed = 1`.
2. Seller calls `transfer_did(new_puzhash=buyer_p2_puzzle, ...)`. Since `backup_ids` is non-empty, `reset_recovery_list()` returns `None`, so `get_inner_puzhash_by_p2` curries in the original `backup_ids` unchanged: [4](#0-3) 
3. Buyer receives the DID coin, unaware that `seller_alt_did` is still a valid recovery backup.
4. Seller (via `seller_alt_did`) later creates a recovery attestment/message spend (per `create_recovery_message_puzzle`/`create_spend_for_message` in `did_wallet_puzzles.py`) to recover the DID coin, reclaiming ownership without the buyer's signature.

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
