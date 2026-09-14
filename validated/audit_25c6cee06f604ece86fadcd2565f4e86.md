### Title
Malicious DID owner can retain recovery/backup-ID control after transferring DID ownership - (File: chia/wallet/did_wallet/did_wallet.py)

### Summary
`DIDWallet.transfer_did()` carries over the current owner's `backup_ids` (recovery DID list) and `num_of_backup_ids_needed` into the new DID puzzle given to the recipient, unless the transferring owner explicitly clears them beforehand. This mirrors the LSP6 finding: a permission/authority set by the *previous* controller (here, the DID recovery list) is not owner-scoped and silently persists across an ownership transfer, letting the old owner (or parties they nominated as backup IDs) retain latent control over the asset after handing it to a new owner.

### Finding Description
When a DID is transferred, `transfer_did()` builds the new inner puzzle hash using the *current* `did_info.backup_ids` / `num_of_backup_ids_needed`, and only nils the recovery list hash via `reset_recovery_list()` if very specific preconditions hold (empty `backup_ids` and non-nil/zero-required combination already matching origin state): [1](#0-0) 

```
coin = await self.get_coin()
backup_ids = self.did_info.backup_ids
backup_required = self.did_info.num_of_backup_ids_needed
new_did_puzhash = did_wallet_puzzles.get_inner_puzhash_by_p2(
    p2_puzhash=new_puzhash,
    recovery_list=backup_ids,
    num_of_backup_ids_needed=backup_required,
    ...
    recovery_list_hash=self.reset_recovery_list(),
)
``` [2](#0-1) 

`reset_recovery_list()` only returns a nil hash to clear the list under narrow conditions, and otherwise preserves whatever backup ID configuration the outgoing owner had set: [3](#0-2) 

This is explicitly acknowledged as intentional carry-over behavior in `get_innerpuz_for_new_innerhash`: [4](#0-3) 

```
# Note: the recovery list will be kept.
# In a selling case, the seller should clean the recovery list then transfer to the new owner.
```

The `backup_ids` field is a controller-specific "permission" curried into the DID's inner puzzle (`DID_INNERPUZ_MOD`), analogous to LSP6's universal permission keys: it is not tied to who currently holds `p2_puzzle` ownership, and the smart-contract-level puzzle has no mechanism to invalidate/rotate it on transfer other than the wallet software optionally zeroing it out before the spend. `DIDInfo.backup_ids` / `num_of_backup_ids_needed` in `did_info.py` are simply carried state, not automatically reset on any transfer path: [5](#0-4) 

Additionally, `wallet_request_types.py`'s `DIDTransferDID` request now hard-codes that recovery info must always accompany the transfer (`with_recovery_info` cannot be disabled), meaning the RPC path cannot be used to force stripping recovery permissions at transfer time; that must be done as a separate `set_recovery_list` action prior to transfer, entirely reliant on the outgoing owner's honesty: [6](#0-5) 

### Impact Explanation
A seller/prior owner can configure `backup_ids` (recovery DIDs) and `num_of_backup_ids_needed` to addresses they control, then transfer the DID to a buyer without clearing the list. The wallet code does not force clearing on transfer; it only clears under narrow existing-state conditions in `reset_recovery_list()`. The buyer receives a DID whose on-chain puzzle still contains a recovery mechanism controlled by the seller, allowing the seller to later initiate a DID "recovery" spend and seize/redirect control of the DID (and any NFTs/assets bound to that DID identity) away from the new owner — a rug-pull vector functionally identical to the residual "universal permission" issue described in the LSP6 report. There is no smart-contract-level way for the new owner to trustlessly verify recovery list state was cleared before accepting the transfer.

### Likelihood Explanation
Likelihood is conditional and requires an unwary or trusting recipient (similar to the original finding's "unfounded trust" caveat) — the same rationale the LUKSO judge used to cap this at Medium. DID transfers/sales are a real, supported wallet flow (`transfer_did`, `DIDTransferDID` RPC/CLI command), and a malicious seller has direct incentive and capability to set a self-controlled recovery list before selling. The comment in `get_innerpuz_for_new_innerhash` acknowledges the team is aware sellers are expected to manually clean the list, but nothing enforces this at the protocol or wallet level.

### Recommendation
- Make DID recovery/backup permissions transfer-scoped: force `transfer_did()` to always nil out `backup_ids`/`num_of_backup_ids_needed` (equivalent to requiring an explicit reset) unless the new owner explicitly opts to keep them, rather than defaulting to preserving prior state.
- Surface the current recovery-list configuration prominently to the receiving party (e.g., in `did_get_info` / offer summaries) before accepting a DID via an offer or manual transfer, so recipients can trustlessly verify no residual recovery permissions exist.
- Consider a puzzle-level solution analogous to the salt/nonce mitigation proposed in the source report — binding the recovery list commitment to a per-transfer nonce so that old recovery configurations can't be replayed for future recovery attempts without new-owner consent.

### Proof of Concept
1. Owner A creates a DID and sets `backup_ids = [DID_B_controlled_by_A]`, `num_of_backup_ids_needed = 1` via the DID recovery configuration flow.
2. Owner A transfers the DID to buyer B using `transfer_did()` (`chia/wallet/did_wallet/did_wallet.py:805-880`), without calling any action that clears the recovery list (the RPC `DIDTransferDID` request cannot disable `with_recovery_info`, and `reset_recovery_list()` does not clear a non-empty `backup_ids`).
3. The new DID inner puzzle delivered to B still curries in `backup_ids=[DID_B_controlled_by_A]`.
4. After B takes ownership and uses the DID (e.g., binds NFTs, uses it for authentication), A initiates a DID recovery spend using their still-valid backup DID, potentially regaining or contesting control of the DID puzzle — without B's consent and without any on-chain flag indicating this risk existed.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L805-837)
```python
    async def transfer_did(
        self,
        new_puzhash: bytes32,
        fee: uint64,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        """
        Transfer the current DID to another owner
        :param new_puzhash: New owner's p2_puzzle
        :param fee: Transaction fee
        :return: Spend bundle
        """
        assert self.did_info.current_inner is not None
        assert self.did_info.origin_coin is not None
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
        innersol = Program.to([2, p2_solution, [], [], [], self.did_info.backup_ids])
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

**File:** chia/wallet/did_wallet/did_info.py (L24-37)
```python
@streamable
@dataclass(frozen=True)
class DIDInfo(Streamable):
    origin_coin: Coin | None  # Coin ID of this coin is our DID
    backup_ids: list[bytes32]
    num_of_backup_ids_needed: uint64
    parent_info: list[tuple[bytes32, LineageProof | None]]  # {coin.name(): LineageProof}
    current_inner: Program | None  # represents a Program as bytes
    temp_coin: Coin | None  # partially recovered wallet uses these to hold info
    temp_puzhash: bytes32 | None
    temp_pubkey: bytes | None
    sent_recovery_transaction: bool
    metadata: str  # JSON of the user defined metadata

```

**File:** chia/wallet/wallet_request_types.py (L1690-1700)
```python
@streamable
@dataclass(frozen=True, kw_only=True)
class DIDTransferDID(TransactionEndpointRequest):
    wallet_id: uint32
    inner_address: str
    with_recovery_info: bool = True

    def __post_init__(self) -> None:
        if self.with_recovery_info is False:
            raise ValueError("Recovery related options are no longer supported. `with_recovery` must always be true.")
        return super().__post_init__()
```
