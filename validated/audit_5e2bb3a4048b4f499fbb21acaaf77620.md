## Title
Stale DID recovery-DID permissions survive `transfer_did()`, letting a previous DID owner reclaim a sold/transferred DID - (File: `chia/wallet/did_wallet/did_wallet.py`)

### Summary
`DIDWallet.transfer_did()` moves a DID coin to a new p2 puzzle hash (new owner) but reuses the old `DIDInfo.backup_ids` / `num_of_backup_ids_needed` recovery configuration when constructing the new inner puzzle. This is the same bug class as the Open Dollar finding: a permission set (there, `handlerCan`; here, the DID's on-chain recovery/backup-DID list) is tied to a persistent object (the safeHandler / the DID singleton) rather than being reset on ownership transfer, so stale permissions granted by the *previous* owner remain valid against the object after it changes hands, without the new owner necessarily being aware.

### Finding Description
`transfer_did()` builds the new DID inner puzzle hash for the incoming owner while carrying forward the existing recovery configuration: [1](#0-0) 

The recovery list (`backup_ids`) and required signer count (`num_of_backup_ids_needed`) are curried directly into the DID's inner puzzle via `did_wallet_puzzles.get_inner_puzhash_by_p2()` / `create_innerpuz()`: [2](#0-1) 

The code is explicit that this is a known, un-enforced risk — it is documented in a comment rather than fixed: [3](#0-2) 
"Note: the recovery list will be kept. In a selling case, the seller should clean the recovery list then transfer to the new owner."

Because the recovery/backup DID identities and the required threshold are curried directly into the on-chain puzzle (not stored server-side), whoever controls the previously-designated backup DIDs can still satisfy the DID puzzle's recovery branch after the DID singleton has been sold/transferred to a new owner — exactly analogous to `handlerCan[safeHandler][X]` continuing to authorize address `X` after `ODSafeManager.transferSAFEOwnership()` moves the safe to a new owner. In both cases the vulnerable object (safeHandler / DID inner puzzle) is reused across the ownership transition while a permission list tied to it is not reset, and the new owner has no visibility into or built-in ability to detect this unless they manually inspect the recovery configuration.

### Impact Explanation
If a DID owner (or DID marketplace seller) transfers/sells a DID via `transfer_did()` without separately resetting the recovery list (there is no automatic reset — resetting requires the seller to proactively clear `backup_ids`/`num_of_backup_ids_needed` before transfer, and `reset_recovery_list()` only nils it out under specific already-empty conditions), the previous owner (through DIDs they control as "backup" identities) retains the on-chain ability to run a DID recovery spend that reassigns control of the DID coin — and by extension any NFTs/assets whose wallet identification and DID-linked ownership state depend on that DID — to a puzzle hash of their choosing. This is unauthorized/forged reclamation of a transferred asset's identity, which a naive buyer/new owner would not detect, matching the "forged asset identity" / "unauthorized coin movement" impact bar.

### Likelihood Explanation
Reachable purely through standard wallet actions: any DID owner who sets up backup/recovery DIDs and later sells/transfers the DID via `transfer_did()` (a normal wallet operation, also exposed via RPC `did_transfer_did` and CLI `did transfer`) is affected unless they manually clear the recovery list first — which the code comment shows is not enforced or defaulted safely. This does not require a malicious peer/node; it only requires the previous (potentially malicious) owner acting through standard, unprivileged wallet/RPC flows plus normal on-chain spend submission. I was not able to fully confirm the exact recovery spend construction/threshold-signing chialisp logic in this pass (the DID inner puzzle CLVM source was not resolved via search), so I cannot state with certainty whether a single self-registered backup DID by the seller would be sufficient to unilaterally reclaim the DID, or whether other coin-level checks (e.g., requiring the current owner's own signature in addition) mitigate real-world exploitability. This uncertainty affects severity but not the existence of the stale-permission bug itself.

### Recommendation
- In `transfer_did()`, require/force clearing the recovery configuration (`backup_ids = []`, `num_of_backup_ids_needed = 0`) by default when transferring the DID to a puzzle hash outside the current wallet's derivation, unless the caller explicitly opts to preserve it.
- Surface a clear warning/confirmation in the RPC (`did_transfer_did`) and CLI (`did transfer`, note the existing `--reset_recovery` flag defaults to *not* resetting) so that sellers/transferrers are not silently exposed.
- Consider making "reset recovery" the default behavior of `did transfer`/`did_transfer_did` rather than an opt-in flag, since the current default preserves stale permissions.

### Proof of Concept
Conceptual reproduction based on the code paths above (concrete on-chain recovery-spend construction could not be fully traced in this pass):
1. Owner A creates a DID and configures backup DID(s) `[X]` with `num_of_backup_ids_needed = 1` as part of `DIDInfo.backup_ids`.
2. Owner A calls `transfer_did(new_puzhash=B, ...)` to sell/transfer the DID to Owner B. As shown in `did_wallet.py:820-832`, `backup_ids`/`num_of_backup_ids_needed` are reused unchanged when computing `new_did_puzhash`, and the `reset_recovery` CLI flag is off by default (`chia/cmds/wallet.py:862-864`), so no reset occurs.
3. Owner B now controls the DID's p2 puzzle hash but is unaware that DID `X` (controlled by Owner A) remains embedded in the puzzle's on-chain recovery configuration.
4. Owner A, using DID `X`, constructs and submits a recovery spend bundle directly against the DID coin (bypassing wallet UI/RPC, which is not required since mempool/full-node validation only checks puzzle/solution validity), potentially regaining control of the DID coin and any dependent NFT/DID-linked assets without Owner B's consent.

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
