### Title
DL update fee is naively divided among all singletons, forcing users to overpay or risk mempool rejection - ([File: chia/wallet/wallet_rpc_api.py])

### Summary
`dl_update_multiple` in the Wallet RPC API splits a single caller-supplied fee equally across all Data Layer singletons being updated, mirroring the reported bug class (a single total amount being naively divided among multiple independent spends with different actual requirements) rather than letting the RPC caller specify the fee needed per singleton spend.

### Finding Description
`WalletRpcApi.dl_update_multiple` takes one `request.fee` value and divides it by integer division across the number of `launcher_root_pairs`, then calls `wallet.create_update_state_spend` once per pair with that uniform `fee_per_launcher`: [1](#0-0) 

Each `create_update_state_spend` call produces an independent coin spend (independent singleton lineage) that is later aggregated into one spend bundle for mempool submission. Because integer division (`//`) is used, any remainder is silently dropped, and any individual singleton's spend can end up under-funded relative to what's actually needed to keep the whole bundle above the mempool's minimum fee-per-cost when the bundle is competing for space, and there is no way for the caller to specify a fee per singleton — only a single flat aggregate fee that gets divided uniformly regardless of how many updates are being coalesced. The code even carries a `TODO` acknowledging this method should "natively support spending many and attaching one fee" instead of splitting it, confirming this is a known-incomplete design rather than an intentional per-spend fee model.

### Impact Explanation
This impacts DataLayer clients calling the wallet RPC to batch-update multiple singleton roots in one transaction. Because the fee is split blindly with no ability to weight it per singleton, and integer division can drop mojos, some spends may end up effectively fee-less within an aggregated bundle. In an ordinary uncongested mempool this simply causes silent underpayment; under mempool congestion or fee-rate-sensitive conditions, this can cause the whole batched update transaction to be rejected for insufficient fee-per-cost even though the caller supplied what they believed was an adequate total fee, since there's no way to weight or concentrate the fee where it's needed.

### Likelihood Explanation
Any local RPC caller (DataLayer client) using `dl_update_multiple` to batch update more than one singleton is affected; likelihood of the transaction-processing failure is dependent on mempool congestion and the number of pairs, which is a normal operating condition of the DataLayer batch-update path.

### Recommendation
Follow the fix suggested by the TODO already in the code: attach a single fee to the aggregated bundle (e.g., by adding it to one spend and linking singleton spends via announcements/assertions) instead of dividing it across N spends, and/or allow the caller to specify a fee per `launcher_root_pair` so unequal per-chain/per-singleton costs can be expressed explicitly, consistent with the recommendation in the referenced report.

### Proof of Concept
1. Call the `dl_update_multiple` RPC with `updates.launcher_root_pairs` containing N launcher/root pairs and a `fee` value that is not evenly divisible by N (or that is calibrated to be minimally sufficient for the whole bundle).
2. Observe `fee_per_launcher = uint64(request.fee // len(request.updates.launcher_root_pairs))` truncates the remainder, so the sum of `fee_per_launcher * N` is less than `request.fee`, and each individual spend gets an equal share regardless of its actual priority/urgency, matching the reported "user pays based on maximum requirement, cannot allocate per-target" bug class. [2](#0-1)

### Citations

**File:** chia/wallet/wallet_rpc_api.py (L3191-3218)
```python
    async def dl_update_multiple(
        self,
        request: DLUpdateMultiple,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> DLUpdateMultipleResponse:
        """Update multiple singletons with new merkle roots"""
        if self.service.wallet_state_manager is None:
            raise RuntimeError("not initialized")

        wallet = await self.service.wallet_state_manager.get_dl_wallet()
        async with self.service.wallet_state_manager.lock:
            # TODO: This method should optionally link the singletons with announcements.
            #       Otherwise spends are vulnerable to signature subtraction.
            # TODO: This method should natively support spending many and attaching one fee
            fee_per_launcher = uint64(request.fee // len(request.updates.launcher_root_pairs))
            for launcher_root_pair in request.updates.launcher_root_pairs:
                await wallet.create_update_state_spend(
                    launcher_root_pair.launcher_id,
                    launcher_root_pair.new_root,
                    action_scope,
                    fee=fee_per_launcher,
                    extra_conditions=extra_conditions,
                )

            # tx_endpoint will take care of default values here
            return DLUpdateMultipleResponse(unsigned_transactions=[], transactions=[])

```
