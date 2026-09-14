### Title
DIDWallet.transfer_did() preserves the old recovery/backup ID list, letting a previously-authorized backup DID reclaim a sold/transferred DID coin - ([File: chia/wallet/did_wallet/did_wallet.py])

### Summary
Chia DID singletons support a "backup/recovery" mechanism: a `recovery_list` (`backup_ids`) of other DIDs that are curried into the DID's inner puzzle and are authorized to co-sign a recovery spend that reassigns control of the DID coin, independent of the current owner's key. `DIDWallet.transfer_did()`, the code path used to hand a DID (and by extension any NFT that names it as DID owner) to a new owner, does not clear this list unless the caller explicitly resets it first — this mirrors the Burner.sol pattern of migrating to a new privileged reference (MINTR/new owner) while leaving the old privileged entity's authorization (allowance/backup DID) intact.

### Finding Description
`transfer_did` builds the new DID inner puzzle by re-using `self.did_info.backup_ids` and `self.did_info.num_of_backup_ids_needed` verbatim, and only substitutes a "reset" version through `self.reset_recovery_list()` if certain existing internal conditions are already met (empty backup list or `num_of_backup_ids_needed == 0` with recovery hash already nil): [1](#0-0) 

`reset_recovery_list()` explicitly only nulls the list if the backup list is *already empty*: it does **not** proactively clear a non-empty `backup_ids` set as part of `transfer_did`: [2](#0-1) 

The code comment on `get_innerpuz_for_new_innerhash` makes this explicit and is the developers' own acknowledgement of the pattern: "the recovery list will be kept. In a selling case, the seller should clean the recovery list then transfer to the new owner": [3](#0-2) 

Because the recovery backup IDs (analogous to the "approved MINTR" in the Burner.sol report) are baked into the new DID puzzle hash by `transfer_did` and are not cleared by default, any DID(s) still listed as `backup_ids` retain the on-chain-enforced authority to trigger a recovery spend of the transferred DID coin after the sale/transfer — exactly the "stale privileged approval left on a changed reference" bug class described in the report, just expressed in Chia's coin/puzzle model instead of an ERC20 `approve()`.

### Impact Explanation
If a seller lists themselves (or an entity they control) as a backup/recovery DID before selling a DID (and any linked NFTs using that DID as owner) and does not manually clear the recovery list before calling `transfer_did`, the seller retains the ability to execute a DID recovery spend after the sale and redirect the DID singleton's ownership away from the buyer — an unauthorized coin-ownership takeover of an asset the buyer believes they now fully control. This satisfies "concrete unsigned or unauthorized coin movement" since the recovery mechanism does not require the new owner's signature, only the still-authorized backup DID's cooperation.

### Likelihood Explanation
This requires that at time of transfer, `backup_ids` is non-empty (i.e., the DID actually has a recovery/backup mechanism configured) and that the transferring party (or wallet UI/RPC flow) does not proactively reset the recovery list before calling `transfer_did`. Since `transfer_did` performs no automatic reset and the burden is explicitly placed on the caller ("the seller should clean the recovery list then transfer"), any wallet, CLI, or RPC caller that transfers a DID without first calling a reset/backup-list-clearing operation will reproduce this exposure. This is directly reachable by a normal wallet user/DID owner performing a standard sale/transfer action, with no privileged or attacker-controlled network state required.

### Recommendation
`transfer_did` should not silently preserve a non-empty recovery/backup list across ownership transfer. Either:
1. Force `backup_ids = []` and `num_of_backup_ids_needed = 0` by default in `transfer_did` unless the caller explicitly opts to keep the same recovery set, or
2. Require an explicit, separate recovery-list-clearing spend as a mandatory precondition enforced by the wallet RPC/CLI before allowing `transfer_did` to proceed, with a clear warning/error if `backup_ids` is non-empty.

This mirrors the recommended fix pattern from the report (revoke old authority before/at the point the "owner" reference changes) applied to Chia's DID recovery mechanism.

### Proof of Concept
1. Owner A creates a DID with `backup_ids = [DID_X]` (a backup DID under A's own control) and `num_of_backup_ids_needed = 1`.
2. Owner A sells/transfers the DID to Owner B via `transfer_did()`, which curries the new inner puzzle with the same `backup_ids = [DID_X]` since neither `backup_ids` nor `num_of_backup_ids_needed` are cleared: [4](#0-3) 
3. After confirmation, Owner B believes the DID (and any NFTs it owns) fully belongs to them.
4. Owner A, using `DID_X` (which A still controls), initiates a DID recovery spend against the DID coin now owned by B. Because `DID_X` remains in the on-chain curried `recovery_list_hash`, the recovery spend is valid per the DID inner puzzle's own logic, and Owner A can redirect the DID coin's ownership away from B without B's signature or consent.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L820-836)
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
        p2_solution = self.standard_wallet.make_solution(
            primaries=[CreateCoin(new_did_puzhash, uint64(coin.amount), [new_puzhash])],
            conditions=(*extra_conditions, CreateCoinAnnouncement(coin.name())),
        )
```

**File:** chia/wallet/did_wallet/did_wallet.py (L982-992)
```python
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
