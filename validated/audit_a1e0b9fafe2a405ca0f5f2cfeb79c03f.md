## Title
DID ownership transfer preserves the old owner's recovery list, letting old backup DIDs seize control of the DID after transfer - (File: `chia/wallet/did_wallet/did_wallet.py`)

### Summary
Chia's DID (Decentralized Identifier) singleton has a recovery mechanism (`backup_ids` / "recovery list") that lets a configured set of external DIDs re-key ("recover") the DID coin to a new owner puzzle hash, requiring `num_of_backup_ids_needed` attestations rather than the current owner's key signature. When a DID is transferred to a new owner via `DIDWallet.transfer_did()`, this recovery list is **not cleared** — it is carried over verbatim into the new inner puzzle. This is the same bug class as the reported "token allowances stay in effect on proxy ownership transfer" issue: an ownership-transfer path leaves a previously-granted authorization (there, ERC20 approvals; here, the DID recovery/backup-DID authorization) intact and exercisable by the previous owner (or whoever controls the previously configured backup DIDs) without the new owner's knowledge or consent.

### Finding Description
`DIDWallet.transfer_did()` builds the new DID inner puzzle hash while explicitly reusing the current `backup_ids` and `num_of_backup_ids_needed`: [1](#0-0) 

The related helper `get_innerpuz_for_new_innerhash()` even documents this behavior in a comment, acknowledging that sellers are expected to manually clean the recovery list before transferring, but nothing enforces this: [2](#0-1) 

The DID inner puzzle is curried with `p2_puzzle_or_hash`, `backup_ids_hash`, and `num_of_backup_ids_needed`: [3](#0-2) 

The `backup_ids` list represents a set of *other* DIDs previously designated by the (old) owner as trusted recovery parties. If `num_of_backup_ids_needed > 0`, those backup DIDs — which the old owner controls or colludes with — can execute the DID's recovery spend path (an alternate spend branch that does not require the current p2 puzzle's signature, only attestations from the backup DIDs) to redirect the DID singleton to a puzzle hash of their choosing. Because `transfer_did()` never resets `backup_ids`/`num_of_backup_ids_needed`, this recovery authorization silently carries over to the new owner's DID coin, exactly mirroring the proxy bug: the "approval" (recovery authorization) is bound to the singleton's puzzle content, not reset on ownership transfer, and the new owner has no visibility into who is listed unless they separately inspect on-chain history.

### Impact Explanation
If a DID is sold/transferred without the seller (old owner) clearing the recovery list, the old owner (or any party colluding with the previously-configured backup DIDs) can later trigger the recovery spend path to redirect the DID coin — and, by extension, hijack DID-gated assets such as NFTs bound to that DID (`owner_did`) and any protocol relying on the DID address as an ownership/access-control anchor — to a puzzle hash under the old owner's control, without needing the new owner's private key. This is an unauthorized coin/identity redirection enabling theft of DID-controlled assets, matching the "unsigned/unauthorized coin movement" and "forged asset identity" impact classes.

### Likelihood Explanation
This is entirely reachable by any wallet user who transfers or purchases a DID (e.g., via `transfer_did()`/offers) without realizing (or without tooling enforcing) that the recovery list must be independently reset. Since resetting is a manual, undocumented-in-flow step (only a code comment references it) and no wallet-level warning or automatic reset exists in `transfer_did()`, real-world buyers of a DID (common in NFT/DID marketplaces) are likely unaware of latent backup-DID authorizations from the previous owner.

### Recommendation
`transfer_did()` (and any other DID ownership-transfer path) should require explicit clearing of `backup_ids`/`num_of_backup_ids_needed` (i.e., set `num_of_backup_ids_needed = 0` and empty the recovery list) by default, or at minimum surface a mandatory, prominent warning/parameter forcing the caller to acknowledge and reset stale recovery authorizations before completing a transfer, so new owners are not silently exposed to recovery by parties trusted by the previous owner.

### Proof of Concept
1. Owner A creates a DID with `backup_ids = [DID_X, DID_Y]`, `num_of_backup_ids_needed = 1` (recovery enabled), where A also controls `DID_X`.
2. Owner A sells/transfers the DID to Owner B via `DIDWallet.transfer_did(new_puzhash=B_ph, ...)`. Per [4](#0-3) , the new DID inner puzzle still curries in `backup_ids = [DID_X, DID_Y]` and `num_of_backup_ids_needed = 1`.
3. Owner B is unaware the recovery list persisted and begins using the DID (e.g., attaching NFTs to it).
4. Owner A, using `DID_X`, initiates the DID recovery spend flow (attestation + recovery announcement) to redirect the DID singleton's p2 puzzle hash to an address A controls, effectively reclaiming the DID and any assets gated by its ownership, without B's signature ever being required.

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

**File:** chia/wallet/did_wallet/did_wallet_puzzles.py (L37-63)
```python
def create_innerpuz(
    p2_puzzle_or_hash: Program | bytes32,
    recovery_list: list[bytes32],
    num_of_backup_ids_needed: uint64,
    launcher_id: bytes32,
    metadata: Program = Program.to([]),
    recovery_list_hash: Program | None = None,
) -> Program:
    """
    Create DID inner puzzle
    :param p2_puzzle_or_hash: Standard P2 puzzle or hash
    :param recovery_list: A list of DIDs used for the recovery
    :param num_of_backup_ids_needed: Need how many DIDs for the recovery
    :param launcher_id: ID of the launch coin
    :param metadata: DID customized metadata
    :param recovery_list_hash: Recovery list hash
    :return: DID inner puzzle
    Note: Receiving a standard P2 puzzle hash wouldn't calculate a valid puzzle, but
    that can be useful if calling `.get_tree_hash_precalc()` on it.
    """
    backup_ids_hash: Program | bytes32 = Program.to(recovery_list).get_tree_hash()
    if recovery_list_hash is not None:
        backup_ids_hash = recovery_list_hash
    singleton_struct = Program.to((SINGLETON_TOP_LAYER_MOD_HASH, (launcher_id, SINGLETON_LAUNCHER_PUZZLE_HASH)))
    return DID_INNERPUZ_MOD.curry(
        p2_puzzle_or_hash, backup_ids_hash, num_of_backup_ids_needed, singleton_struct, metadata
    )
```
