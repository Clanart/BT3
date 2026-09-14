Based on my research, I found a concrete analog to the "MAX_DELEGATES" DoS-by-poisoned-list pattern in the DID wallet's recovery-list handling.

### Title
Unbounded attacker-controlled `backup_ids` recovery list can be embedded in a DID coin transferred to a victim, permanently bloating every future `transfer_did` spend and risking CLVM-cost DoS with no recovery path - ([File: chia/wallet/did_wallet/did_wallet.py])

### Summary
When a DID is transferred to a wallet via `create_new_did_wallet_from_coin_spend`, the receiving wallet parses `recovery_list` directly out of the inbound coin spend's solution with no size limit, and stores it verbatim in `DIDInfo.backup_ids`. That list is then unconditionally re-embedded, unbounded, into every subsequent `transfer_did()` spend solution the new owner creates. Because the wallet RPC endpoint that historically allowed clearing/reducing a DID's recovery list (`did_update_recovery_ids`) has been removed, a victim who receives (buys/is gifted) a DID whose `backup_ids` list was maliciously inflated by the sender has no way to shrink it, permanently increasing the cost of every future spend of that coin — directly analogous to the reported `MAX_DELEGATES`/gas-exhaustion griefing pattern, but here unbounded and with no built-in mitigation constant at all.

### Finding Description
`create_new_did_wallet_from_coin_spend` reconstructs the recovering wallet's `DIDInfo` straight from an inbound `CoinSpend`'s solution: [1](#0-0) 

Specifically, the `recovery_list` is decoded from `inner_solution.rest().rest().rest().rest().rest()` with no length check whatsoever, and stored as `backup_ids` in `DIDInfo`: [2](#0-1) 

`DIDInfo.backup_ids` itself is a plain unbounded `list[bytes32]` field with no size constraint enforced anywhere in the streamable model: [3](#0-2) 

Once this poisoned `backup_ids` list is persisted, `transfer_did()` — the standard "move my DID to a new owner" path any wallet user calls — unconditionally serializes the *entire* `backup_ids` list into the inner solution of every future spend of that coin: [4](#0-3) 

The same unbounded list is also re-curried/hashed on every call to `get_did_innerpuz`, `get_innerpuz_for_new_innerhash`, and `inner_puzzle_for_did_puzzle`, which are used elsewhere in the DID spend/recovery-computation code paths: [5](#0-4) 

Critically, `reset_recovery_list()` — the only mechanism that could drop the recovery-list requirement — explicitly refuses to reset if `len(self.did_info.backup_ids) > 0`, i.e., it cannot clear an already-populated poisoned list: [6](#0-5) 

And the wallet RPC endpoints that historically let a user update/replace their DID's recovery list (`did_update_recovery_ids`, `did_recovery_spend`, `did_get_recovery_list`, etc.) have been removed from this codebase:

(see CHANGELOG entry documenting removal of these RPCs — grep result on `CHANGELOG.md:307`)

The comment in `get_innerpuz_for_new_innerhash` even acknowledges the risk but places the burden entirely on trust in the sender rather than any enforced limit: "In a selling case, the seller should clean the recovery list then transfer to the new owner" — with no code enforcing this.

### Impact Explanation
An unprivileged counterparty in a DID trade/gift/transfer can craft a DID inner-solution reveal containing an arbitrarily large `recovery_list` (thousands of `bytes32` entries) before sending the DID coin to a victim. The victim's wallet ingests this list without bound and bakes it into `DIDInfo.backup_ids`. From then on:
- Every `transfer_did()` spend the victim creates grows linearly with the poisoned list size (both in generator bytes and CLVM execution cost via `shatree_atom_list`/`Program.to(recovery_list).get_tree_hash()`).
- With a sufficiently large list, the resulting spend bundle cost can exceed `max_tx_clvm_cost` (half of `MAX_BLOCK_COST_CLVM`), causing mempool rejection.
- Since there is no user-facing way to shrink or clear `backup_ids` once set (the relevant RPCs were removed, and `reset_recovery_list()` refuses to help when the list is non-empty), the victim's DID coin can become **permanently unspendable/untransferable** — a direct match to the "permanent freezing" and "griefing" impact categories in the source report.

### Likelihood Explanation
This requires only a single malicious counterparty willing to send/sell a specially-crafted DID to a victim — no special privileges, no network-layer or consensus-layer access needed, and no cost to the attacker beyond constructing an oversized solution once at DID-creation/transfer time. Any wallet that accepts DID transfers from arbitrary counterparties (which is the normal design intent of DIDs/offers) is exposed.

### Recommendation
1. Enforce a maximum size on `backup_ids`/`recovery_list` both when parsing an inbound DID transfer in `create_new_did_wallet_from_coin_spend` and `find_lost_did`, and reject/truncate lists beyond a sane bound (analogous to `MAX_DELEGATES`).
2. Provide (or restore) a supported wallet-side mechanism to clear/replace an inherited `backup_ids` list independent of `reset_recovery_list()`'s current "only if empty" restriction, so a victim can recover from a poisoned transfer.
3. Consider excluding `backup_ids` reveal entirely from routine `transfer_did` spends when `num_of_backup_ids_needed == 0`, since the recovery list should only need to be revealed during an actual recovery spend, not on every ownership transfer.

### Proof of Concept
1. Attacker creates a DID and, via the legacy (but still-parsed) `recovery_list_hash` reveal path, crafts an inner solution whose `recovery_list` element (position `inner_solution.rest().rest().rest().rest().rest()`) contains e.g. 5,000+ `bytes32` entries, consistent with a committed `recovery_list_hash`.
2. Attacker transfers/sells this DID coin to a victim wallet (e.g., via a normal offer or spend).
3. Victim's wallet calls `create_new_did_wallet_from_coin_spend`, which decodes and stores the 5,000-entry list into `DIDInfo.backup_ids` with no size check (`chia/wallet/did_wallet/did_wallet.py:216-230`).
4. Victim later calls `transfer_did()` to move the coin (e.g., to sell it or send it elsewhere); the full 5,000-entry list is serialized into `innersol` (`chia/wallet/did_wallet/did_wallet.py:837`), inflating the spend's CLVM cost.
5. With a large enough list, the resulting `SpendBundle` cost exceeds `max_tx_clvm_cost`, and the mempool rejects it; the victim has no supported way to shrink `backup_ids` (RPCs removed, `reset_recovery_list()` refuses when list is non-empty), permanently freezing the DID coin.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L210-239)
```python
        args = did_wallet_puzzles.uncurry_innerpuz(inner_puzzle)
        if args is None:
            raise ValueError("Cannot uncurry the DID puzzle.")
        _, recovery_list_hash, num_verification, _, metadata = args
        full_solution: Program = Program.from_bytes(bytes(coin_spend.solution))
        inner_solution: Program = full_solution.rest().rest().first()
        recovery_list: list[bytes32] = []
        backup_required: int = num_verification.as_int()
        if not did_recovery_is_nil(recovery_list_hash):
            self.log.warning(f"DID {launch_coin.name().hex()} has a recovery list hash which has been deprecated.")
            try:
                for did in inner_solution.rest().rest().rest().rest().rest().as_python():
                    recovery_list.append(bytes32(did[0]))
            except Exception:
                self.log.warning(
                    f"DID {launch_coin.name().hex()} has a recovery list hash but missing a reveal,"
                    " you may need to reset the recovery info."
                )
        self.did_info = DIDInfo(
            origin_coin=launch_coin,
            backup_ids=recovery_list,
            num_of_backup_ids_needed=uint64(backup_required),
            parent_info=[],
            current_inner=inner_puzzle,
            temp_coin=None,
            temp_puzhash=None,
            temp_pubkey=None,
            sent_recovery_transaction=False,
            metadata=json.dumps(did_wallet_puzzles.did_program_to_metadata(metadata)),
        )
```

**File:** chia/wallet/did_wallet/did_wallet.py (L820-837)
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
        innersol = Program.to([2, p2_solution, [], [], [], self.did_info.backup_ids])
```

**File:** chia/wallet/did_wallet/did_wallet.py (L965-1015)
```python
        return did_wallet_puzzles.create_innerpuz(
            p2_puzzle_or_hash=await action_scope.get_puzzle(
                self.wallet_state_manager, override_reuse_puzhash_with=override_reuse_puzhash_with
            ),
            recovery_list=self.did_info.backup_ids,
            num_of_backup_ids_needed=self.did_info.num_of_backup_ids_needed,
            launcher_id=launcher_id,
            metadata=did_wallet_puzzles.metadata_to_program(json.loads(self.did_info.metadata)),
            recovery_list_hash=self.reset_recovery_list(),
        )

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

    async def inner_puzzle_for_did_puzzle(self, did_hash: bytes32) -> Program:
        record = await self.wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(did_hash)
        assert self.did_info.origin_coin is not None
        assert self.did_info.current_inner is not None
        uncurried_args = uncurry_innerpuz(self.did_info.current_inner)
        assert uncurried_args is not None
        p2_puzzle, _, _, _, _ = uncurried_args
        if record is None:
            record = await self.wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(
                p2_puzzle.get_tree_hash()
            )
        if record is None:
            raise RuntimeError(f"Could not find derived inner puzzle for did_hash {did_hash}")
        inner_puzzle: Program = did_wallet_puzzles.create_innerpuz(
            p2_puzzle_or_hash=puzzle_for_pk(record.pubkey),
            recovery_list=self.did_info.backup_ids,
            num_of_backup_ids_needed=self.did_info.num_of_backup_ids_needed,
            launcher_id=self.did_info.origin_coin.name(),
            metadata=did_wallet_puzzles.metadata_to_program(json.loads(self.did_info.metadata)),
            recovery_list_hash=self.reset_recovery_list(),
        )
        return inner_puzzle
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

**File:** chia/wallet/did_wallet/did_info.py (L24-36)
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
