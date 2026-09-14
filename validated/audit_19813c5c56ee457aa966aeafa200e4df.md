### Title
Wallet `split_coins` RPC Spends Coins From an Unverified Wallet Context (Missing Coin-to-Wallet Ownership Check) - ([File: chia/wallet/fungibility_manager.py])

### Summary
The wallet RPC `split_coins` endpoint accepts a `wallet_id` (the "context") and a `target_coin_id`, but `FungibilityManager.split_coins()` resolves the coin purely by its global ID via `coin_store.get_coin_record(target_coin_id)` without ever verifying that the fetched `WalletCoinRecord.wallet_id` matches the `wallet` object selected from `request.wallet_id`. This is structurally the same bug class as the Gitea advisory: an object is looked up by a global ID while the authorization/consistency check is performed against a caller-supplied, unrelated context, and the two are never cross-checked.

### Finding Description
The RPC entry point in `chia/wallet/wallet_rpc_api.py` resolves the wallet object solely from `request.wallet_id`: [1](#0-0) 

That `wallet` object (which can be a plain XCH `Wallet` or any `CATWallet` for an arbitrary asset, chosen only via `get_fungible_wallet(request.wallet_id)`) is then passed into `FungibilityManager.split_coins`, which fetches the target coin by ID from the global coin store, with **no filter on `wallet_id`** and **no check that `optional_coin.wallet_id == wallet.id()`**: [2](#0-1) 

Contrast this with the sibling method `combine_coins` in the same class, which does correctly scope its coin lookups with `wallet_id=wallet.id()`: [3](#0-2) 

Because `split_coins` skips this scoping, a caller can supply a `wallet_id` for wallet A (e.g., a CATWallet for asset X) together with a `target_coin_id` that actually belongs to wallet B (e.g., a different CATWallet for asset Y, or the plain XCH wallet, or another user-owned wallet under the same key). The mismatched `Coin` object is then handed directly to `wallet.generate_signed_transaction(..., coins={coin}, ...)`, i.e., wallet A's spend-construction logic (which curries in wallet A's own asset/tail identity and inner-puzzle assumptions) is used to build a spend for a coin that structurally belongs to a completely different asset/wallet context.

### Impact Explanation
This breaks the invariant that a wallet's spend-bundle construction logic (asset id, CAT tail hash, inner puzzle layer) matches the coin actually being spent. Supplying a coin from an unrelated wallet (e.g., a coin belonging to CAT-asset-Y) to CAT-asset-X's `generate_signed_transaction` risks constructing a coin spend/reveal under the wrong asset context — including cases where the constructed puzzle reveal or CAT layer parameters do not correspond to the coin actually being consumed, which is the mechanism class the analog rules flag as "forged asset identity" / "unauthorized coin movement" risk. At minimum it causes silent wallet-side coin/wallet accounting corruption (a coin credited to the wrong wallet's ledger, transaction records with mismatched `wallet_id`), and in the CAT case it opens the door to constructing malformed or asset-mismatched spends that could be pushed to the mempool, since there is no runtime assertion tying `target_coin_id`'s actual owning wallet to `request.wallet_id` before spend construction.

### Likelihood Explanation
Any local wallet RPC caller (the trust boundary explicitly permitted by this scan's scope) can trigger this by simply calling `split_coins` with a `wallet_id` for one wallet and a `target_coin_id` belonging to a coin tracked under a different wallet in the same key/daemon — no special privileges beyond normal wallet-RPC access are required, and the coin ID is discoverable via the ordinary `get_coin_records` endpoints. This makes exploitation straightforward and reliably reproducible.

### Recommendation
In `FungibilityManager.split_coins`, after fetching `optional_coin`, assert that `optional_coin.wallet_id == wallet.id()` (mirroring the scoping already done in `combine_coins`), and raise a `ValueError` if the coin does not belong to the wallet identified by `request.wallet_id`. Add a regression test analogous to `test_combine_coins` that attempts to split a coin belonging to a different wallet than the one specified and asserts that the RPC rejects it.

### Proof of Concept
1. Create two wallets under the same wallet key/daemon: wallet 1 = standard XCH wallet, wallet 2 = a CAT wallet for asset X (or a second CAT wallet for asset Y).
2. Note a `coin_id` belonging to wallet 1 (or wallet-2-of-a-different-asset) via `get_coin_records`.
3. Call the `split_coins` RPC with `wallet_id=2` (the CAT wallet) and `target_coin_id=<coin belonging to wallet 1 or a different CAT asset>`.
4. Observe that `FungibilityManager.split_coins` (`chia/wallet/fungibility_manager.py:47`) fetches the coin with no ownership check against `wallet_id=2`, and proceeds to call `CATWallet.generate_signed_transaction` (asset X context) with a coin that does not belong to that asset/wallet — compare against `combine_coins`, which correctly filters `get_coin_records(wallet_id=wallet.id(), ...)` and would reject the same cross-wallet coin id.

### Citations

**File:** chia/wallet/wallet_rpc_api.py (L1304-1318)
```python
    async def split_coins(
        self, request: SplitCoins, action_scope: WalletActionScope, extra_conditions: tuple[Condition, ...] = tuple()
    ) -> SplitCoinsResponse:
        await self.service.wallet_state_manager.fungibility_manager.split_coins(
            action_scope=action_scope,
            wallet=self.service.wallet_state_manager.fungibility_manager.get_fungible_wallet(request.wallet_id),
            target_coin_id=request.target_coin_id,
            amount_per_coin=request.amount_per_coin,
            number_of_coins=request.number_of_coins,
            fee=request.fee,
            extra_conditions=extra_conditions,
        )

        # tx_endpoint will take care to fill this out
        return SplitCoinsResponse(unsigned_transactions=[], transactions=[])
```

**File:** chia/wallet/fungibility_manager.py (L36-56)
```python
    async def split_coins(
        self,
        *,
        action_scope: WalletActionScope,
        wallet: Wallet | CATWallet,
        target_coin_id: bytes32,
        amount_per_coin: uint64,
        number_of_coins: uint16,
        fee: uint64,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        optional_coin = await self.coin_store.get_coin_record(target_coin_id)
        if optional_coin is None:
            raise ValueError(f"Could not find coin with ID {target_coin_id}")
        else:
            coin = optional_coin.coin

        total_amount = amount_per_coin * number_of_coins

        if coin.amount < total_amount:
            raise ValueError(f"Coin amount: {coin.amount} is less than the total amount of the split: {total_amount}.")
```

**File:** chia/wallet/fungibility_manager.py (L100-111)
```python
        # First get the coin IDs specified
        if target_coin_ids is not None:
            target_records = (
                await self.coin_store.get_coin_records(
                    wallet_id=wallet.id(),
                    coin_id_filter=HashFilter(target_coin_ids, mode=uint8(FilterMode.include.value)),
                )
            ).records
            spent_ids = [cr.coin.name() for cr in target_records if cr.spent]
            if spent_ids:
                raise ValueError(f"Cannot combine already-spent coins: {', '.join(c.hex() for c in spent_ids)}")
            coins.extend(cr.coin for cr in target_records)
```
