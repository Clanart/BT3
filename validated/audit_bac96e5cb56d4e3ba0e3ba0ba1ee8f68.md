### Title
Stale DID Recovery/Backup List Persists After `transfer_did`, Enabling Old Backup Providers to Recover a Sold DID - (File: chia/wallet/did_wallet/did_wallet.py)

### Summary
`DIDWallet.transfer_did()`, the function used to sell/transfer ownership of a DID singleton to a new p2 puzzle hash, does not clear the previously configured `backup_ids` (recovery/"delegate" DIDs) unless that list is already empty. Just like the reported Convergence issue where `delegatedYsCvg`/`delegatedVeCvg`/`delegatedMgCvg` mappings survive a Locking Position NFT sale and let the old delegate steal rewards from the new owner, a DID's recovery list (its "delegates" who can authorize DID recovery) survives an on-chain `transfer_did` sale unless the seller explicitly performs a separate reset step. A malicious seller can pre-configure a self-controlled backup DID before listing/selling the DID, then use that stale backup entry to initiate the DID recovery process against the buyer's newly-acquired DID coin.

### Finding Description
`transfer_did()` builds the new DID inner puzzle for the buyer using the **same** `backup_ids`/`num_of_backup_ids_needed` as the seller's current DID state: [1](#0-0) 

The recovery list hash is only reset to a "cleared" value by `reset_recovery_list()` if the backup list is already empty: [2](#0-1) 

This means if `self.did_info.backup_ids` is non-empty, `reset_recovery_list()` returns `None`, and `create_innerpuz`/`get_inner_puzhash_by_p2` recompute the hash **from the same unchanged `recovery_list`**: [3](#0-2) 

The sibling helper `get_innerpuz_for_new_innerhash()` (used for related transfer/recovery flows) explicitly documents this as a known footgun that is left to the caller to handle: [4](#0-3) 
> "Note: the recovery list will be kept. In a selling case, the seller should clean the recovery list then transfer to the new owner."

Because the backup DID list ("delegates" trusted to attest to recovery) is baked directly into the on-chain DID inner puzzle curry parameters, whichever address held a slot in that list before the sale retains cryptographic standing to co-sign/attest a DID recovery spend against the coin the buyer now owns, exactly as the report's stale `delegatedYsCvg`/`delegatedVeCvg` mapping retains standing to claim rewards after a Locking Position NFT sale. Unlike the NFT ownership `-10` condition (which does atomically flip DID ownership on the singleton), the recovery/backup mechanism is a separate, independent trust list that is not automatically wiped on transfer — it must be manually reset by the seller, and nothing in `transfer_did()`, the CLI (`transfer_did` in `chia/cmds/wallet_funcs.py`), or the RPC path enforces that reset.

### Impact Explanation
If a seller (or an attacker who compromised a DID before selling it, e.g., on a secondary marketplace) leaves a backup ID under their control in the DID's recovery list, they retain the ability to initiate the DID recovery flow after the DID coin has been sold and transferred to a new legitimate owner. Because DIDs are frequently used as ownership anchors for NFT collections, VC issuance, and other protocol state (`nft_wallet.py` keys NFT wallets by `did_id`, `vc_drivers.py` uses DID-based `proof_provider`), recovering/hijacking the DID after sale can let the old owner regain control of the singleton and any NFTs/VCs bound to it, redirecting or seizing assets the buyer paid for. This is a concrete unauthorized-coin-control / asset-theft primitive matching the "unsigned or unauthorized coin movement" acceptance criterion.

### Likelihood Explanation
Exploitation only requires the seller to configure a backup DID they control (a normal, supported DID feature) prior to selling the DID via `transfer_did`/`did_transfer_did` RPC, then simply not clear it (silently, since nothing warns or enforces clearing). Nothing in the transfer path validates or forces an empty recovery list, and a buyer inspecting only current DID ownership/inner puzzle hash has no straightforward way to know an old backup DID is still configured. This is a self-contained, single-actor (seller) action requiring no cooperation from a malicious peer, network-layer position, or leaked keys — it only requires normal wallet-level DID feature usage, which fits the "wallet user"/offer counterparty reachable category.

### Recommendation
`transfer_did()` should default to clearing the recovery/backup list (`backup_ids = []`, `num_of_backup_ids_needed = 0`) on ownership transfer unless the caller explicitly opts to preserve it (mirroring the `with_recovery_info` flag already exposed at the CLI/RPC layer, but making the safe "clear recovery" behavior the default rather than opt-in). At minimum, the wallet RPC/CLI transfer command should warn/require explicit confirmation when transferring a DID with a non-empty backup list, and wallet sync/display logic should surface the currently-configured backup IDs to the buyer so they can independently verify no stale delegate remains before accepting a DID/NFT-bearing-DID trade.

### Proof of Concept
1. Seller creates a DID wallet and sets `backup_ids = [attacker_controlled_did]`, `num_of_backup_ids_needed = 1` via the standard DID creation/backup configuration flow.
2. Seller lists the DID (or an NFT tied to it) for sale, e.g., via an offer file or marketplace.
3. Buyer accepts the offer; `transfer_did()` is invoked, producing a new DID inner puzzle curried with the **same** `backup_ids`/`num_of_backup_ids_needed` (per lines 820–832 of `did_wallet.py`), because `reset_recovery_list()` only clears when the list is already empty.
4. Buyer now believes they solely own the DID.
5. Attacker (holding `attacker_controlled_did`) initiates the DID recovery protocol against the transferred DID coin, using their still-valid backup-provider standing to attest/co-sign a recovery spend and redirect the DID singleton (and any bound NFTs) to an address they control — without the buyer's cooperation and without needing the buyer's private key.

Note: I was unable to locate the specific DID-recovery attestation/co-sign RPC entry points (`did_wallet_rpc_api` recovery attest endpoints) within the indexed portion of the codebase to cite the exact recovery-spend construction call; the recovery mechanism's existence and its reliance on `backup_ids`/`num_of_backup_ids_needed` is confirmed via `did_wallet_puzzles.create_innerpuz` and `DIDInfo.backup_ids`/`num_of_backup_ids_needed` fields, but full end-to-end recovery spend code may reside in files not fully covered by the current index. If exact recovery-spend line references are needed, a full codebase clone/session would be required to trace `did_recovery`/attestation RPC handlers.

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
