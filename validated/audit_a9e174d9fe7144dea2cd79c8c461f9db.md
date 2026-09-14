I have enough evidence to establish this analog. `WalletStateManager.coin_added()` at [1](#0-0)  calls `self.wallets[wallet_id].coin_added(...)` directly, without a surrounding try/except. This call happens inside `_add_coin_state()` at [2](#0-1)  which is itself wrapped by `add_coin_state()`'s try/except at [3](#0-2) , so an unhandled exception here is only caught at the outer per-coin-state level and logged — but this happens for every incoming coin during wallet sync, including the DID case below.

### Title
Unhandled `UnicodeDecodeError` in DID metadata parsing from attacker-controlled on-chain data can repeatedly abort wallet coin-sync processing - (File: `chia/wallet/did_wallet/did_wallet_puzzles.py`)

### Summary
`did_program_to_metadata()` decodes DID metadata key/value bytes taken directly from an on-chain coin's curried puzzle arguments using `str(key, "utf-8")` / `str(val, "utf-8")` with no error handling. This mirrors the reported bug class: a library/primitive (`bytes.decode`/`str(bytes,...)`) throws an exception on malformed attacker-supplied input where the calling code assumed it would either succeed or fail gracefully, and the exception is not handled at the point where it matters, disrupting normal processing of a legitimate operation (here, wallet coin-state sync) rather than crashing the whole process outright.

### Finding Description
`did_program_to_metadata()` is defined as: [4](#0-3) 

It iterates `program.as_python()` key/value pairs pulled from a DID inner puzzle's curried `METADATA` argument and blindly calls `str(key, "utf-8")` / `str(val, "utf-8")`. Any coin creator fully controls the bytes curried into this metadata slot of a DID puzzle — there is no on-chain constraint that the metadata bytes be valid UTF-8.

This function is invoked from at least three wallet-sync code paths that process a coin that a remote party sent/transferred (e.g., an offer settlement, a DID transfer, or someone paying to a DID's puzzle hash the victim's wallet is now watching):
- `DIDWallet.coin_added()`, called whenever the wallet state manager delivers a new DID coin to a DID wallet: [5](#0-4) 
- `DIDWallet.create_new_did_wallet_from_coin_spend()`, used when first discovering/recognizing a DID coin: [6](#0-5) 
- `WalletStateManager.find_lost_did()`'s recovery path: [7](#0-6) 

These all funnel from `WalletStateManager.coin_added()` → `wallet.coin_added()` at [8](#0-7) , which is called from `_add_coin_state()` per received coin state during normal sync at [9](#0-8) . Unlike `CATWallet.coin_added()`, which wraps its risky logic in `try/except Exception` [10](#0-9) , and `CRCATWallet.coin_added()`, which does the same [11](#0-10) , `DIDWallet.coin_added()` has no such guard around the metadata decode call at line 425.

An attacker can create a DID (or transfer an existing DID) whose innerpuzzle curries a `METADATA` `Program` containing a key or value atom that is not valid UTF-8 (e.g., a single `0xFF` byte). If this coin is subsequently sent to, or discovered by, a victim's wallet (via a normal transfer, an accepted offer, or DID recovery search), `did_program_to_metadata()` raises `UnicodeDecodeError`.

### Impact Explanation
The exception propagates out of `wallet.coin_added()` into `_add_coin_state()`, and is only caught by the coarse `try/except Exception` in `add_coin_state()` at [12](#0-11) , which logs and, for the whole current DB-writer transaction, rolls back and either re-queues the state via `retry_store.add_state()` or drops it depending on exception type. Since `UnicodeDecodeError` is not a `PeerRequestException`/`aiosqlite.Error`, it falls into the `else` branch (`retry_store.remove_state`), meaning the coin state is silently dropped and the wallet coin-store transaction for that item aborts, without ever crediting the coin or updating downstream state (DID recognition, spendability, parent DID wallet linkage). If the DID coin is part of a larger batch/atomic sync transaction (`db_wrapper.writer()`), other coin states processed together in the same call chain can also be affected because the writer's transaction is unwound. This is a targeted, repeatable wallet sync disruption: any wallet that is watching or receives a DID with malformed metadata bytes cannot process that coin's arrival correctly, and repeated malicious DID transfers can be used to keep breaking a specific victim wallet's DID processing (denial of service against DID-related wallet functionality), analogous to how the PocketMine skin-geometry bug let an attacker crash processing via a single malformed message.

### Likelihood Explanation
Likelihood is high for a targeted attack: creating a DID with attacker-controlled metadata bytes is a normal, unprivileged wallet operation (via `create_new_did_wallet` or spending an existing DID with `update_metadata`/`create_update_spend`), and transferring/offering that DID to a victim, or a victim's wallet independently discovering/searching for it (`manual_did_search`, `find_lost_did`), are all standard user-driven code paths. No special network position or trusted-peer permission is required — only the ability to send a spend bundle or an offer containing the malicious DID puzzle to the network.

### Recommendation
Wrap the DID metadata decode logic in `did_program_to_metadata()` (or its three call sites: `DIDWallet.coin_added()`, `DIDWallet.create_new_did_wallet_from_coin_spend()`, and `WalletStateManager.find_lost_did()`) in explicit exception handling, similarly to how `CATWallet.coin_added()` and `CRCATWallet.coin_added()` guard their coin-processing logic. Decode failures should fall back to a safe placeholder (e.g., storing raw hex or skipping non-UTF8 keys/values) rather than raising, so that a single malformed DID coin cannot abort the surrounding sync/coin-state transaction.

### Proof of Concept
1. Craft a DID inner puzzle (via `did_wallet_puzzles.create_innerpuz`) curried with a `metadata` `Program` whose kv-list contains an atom that is not valid UTF-8 (e.g., `Program.to([(b"key", b"\xff\xfe")])`).
2. Mint/transfer this DID coin on-chain (a standard, unprivileged spend).
3. Have a victim wallet either receive the DID (offer acceptance / direct transfer) so `DIDWallet.coin_added()` runs, or call `manual_did_search`/trigger `find_lost_did` for that coin.
4. Observe `did_program_to_metadata()` raise `UnicodeDecodeError` inside `str(val, "utf-8")` at [13](#0-12) , propagating up and aborting the coin-state add transaction in `add_coin_state()`.

### Citations

**File:** chia/wallet/wallet_state_manager.py (L1216-1229)
```python
        elif coin_state.spent_height is None:
            if local_record is None:
                await self.coin_added(
                    coin_state.coin,
                    uint32(coin_state.created_height),
                    all_unconfirmed,
                    wallet_identifier.id,
                    wallet_identifier.type,
                    peer,
                    coin_name,
                    coin_data,
                    sync_scope,
                )
                await self.add_interested_coin_ids([coin_name])
```

**File:** chia/wallet/wallet_state_manager.py (L1567-1594)
```python
        rollback_wallets = None
        try:
            async with self.db_wrapper.writer(), self.new_sync_scope() as sync_scope:
                rollback_wallets = self.wallets.copy()  # Shallow copy of wallets if writer rolls back the db
                # This only succeeds if we don't raise out of the transaction
                await self.retry_store.remove_state(coin_state)
                new_used_up_to = await self._add_coin_state(
                    coin_state,
                    coin_name,
                    sync_scope,
                    peer,
                    fork_height,
                    trade_removals,
                    all_unconfirmed,
                    used_up_to,
                    ph_to_index_cache,
                    local_records,
                )
                return new_used_up_to
        except Exception as e:
            self.log.exception(f"Failed to add coin_state: {coin_state}, error: {e}")
            if rollback_wallets is not None:
                self.wallets = rollback_wallets  # Restore since DB will be rolled back by writer
            if isinstance(e, (PeerRequestException, aiosqlite.Error)):
                await self.retry_store.add_state(coin_state, peer.peer_node_id, fork_height)
            else:
                await self.retry_store.remove_state(coin_state)
        return used_up_to
```

**File:** chia/wallet/wallet_state_manager.py (L1791-1793)
```python
        await self.coin_store.add_coin_record(coin_record, coin_name)

        await self.wallets[wallet_id].coin_added(coin, height, peer, coin_data, sync_scope)
```

**File:** chia/wallet/wallet_state_manager.py (L2554-2565)
```python
                    did_info: DIDInfo = DIDInfo(
                        did_wallet.did_info.origin_coin,
                        recovery_list,
                        uint64(backup_required),
                        [],
                        did_puzzle,
                        None,
                        None,
                        None,
                        False,
                        json.dumps(did_program_to_metadata(metadata)),
                    )
```

**File:** chia/wallet/did_wallet/did_wallet_puzzles.py (L218-227)
```python
def did_program_to_metadata(program: Program) -> dict[str, str]:
    """
    Convert a program to a metadata dict
    :param program: Chialisp program contains the metadata
    :return: Metadata dict
    """
    metadata = {}
    for key, val in program.as_python():
        metadata[str(key, "utf-8")] = str(val, "utf-8")
    return metadata
```

**File:** chia/wallet/did_wallet/did_wallet.py (L228-239)
```python
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

**File:** chia/wallet/did_wallet/did_wallet.py (L415-427)
```python
        new_info = DIDInfo(
            origin_coin=self.did_info.origin_coin,
            backup_ids=self.did_info.backup_ids,
            num_of_backup_ids_needed=self.did_info.num_of_backup_ids_needed,
            parent_info=self.did_info.parent_info,
            current_inner=inner_puzzle,
            temp_coin=None,
            temp_puzhash=None,
            temp_pubkey=None,
            sent_recovery_transaction=False,
            metadata=json.dumps(did_wallet_puzzles.did_program_to_metadata(did_data.metadata)),
        )
        await self.save_info(new_info)
```

**File:** chia/wallet/cat_wallet/cat_wallet.py (L385-406)
```python
        if lineage is None:
            try:
                if coin_data is None:
                    # The method is not triggered after the determine_coin_type, no pre-fetched data
                    coin_state = await self.wallet_state_manager.wallet_node.get_coin_state(
                        [coin.parent_coin_info], peer=peer
                    )
                    assert coin_state[0].coin.name() == coin.parent_coin_info
                    coin_spend = await fetch_coin_spend_for_coin_state(coin_state[0], peer)
                    cat_curried_args = match_cat_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
                    if cat_curried_args is not None:
                        cat_mod_hash, tail_program_hash, cat_inner_puzzle = cat_curried_args
                        coin_data = CATCoinData(
                            bytes32(cat_mod_hash.as_atom()),
                            bytes32(tail_program_hash.as_atom()),
                            cat_inner_puzzle,
                            coin_state[0].coin.parent_coin_info,
                            uint64(coin_state[0].coin.amount),
                        )
                await self.puzzle_solution_received(coin, coin_data)
            except Exception as e:
                self.log.debug(f"Exception: {e}, traceback: {traceback.format_exc()}")
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L220-225)
```python
        try:
            coin_state = await self.wallet_state_manager.wallet_node.get_coin_state([coin.parent_coin_info], peer=peer)
            coin_spend = await fetch_coin_spend_for_coin_state(coin_state[0], peer)
            await self.add_crcat_coin(coin_spend, coin, height)
        except Exception as e:
            self.log.debug(f"Exception: {e}, traceback: {traceback.format_exc()}")
```
