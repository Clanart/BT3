### Title
Unsigned aggregation of multiple Data Layer singleton spends in `dl_update_multiple` allows signature-subtraction across roots - ([File: chia/wallet/wallet_rpc_api.py])

### Summary
`dl_update_multiple` in `chia/wallet/wallet_rpc_api.py` builds one aggregated spend bundle from N independent Data Layer singleton root-update spends (one per launcher/root pair) without linking them together via coin/puzzle announcements, mirroring the Atomic Green pattern where a single aggregated authorization was reusable/separable across many independent positions.

### Finding Description
`dl_update_multiple` (`chia/wallet/wallet_rpc_api.py:3191-3217`) iterates over `request.updates.launcher_root_pairs` and calls `wallet.create_update_state_spend(...)` for each launcher/new-root pair, accumulating the resulting spends/signatures into a single transaction bundle via the shared `action_scope`. The code contains an explicit acknowledged gap:
```
# TODO: This method should optionally link the singletons with announcements.
#       Otherwise spends are vulnerable to signature subtraction.
``` [1](#0-0) 

Because each per-singleton spend is independently valid CLVM-wise (each DL singleton spend is self-contained and only needs its own `AGG_SIG_ME`-style condition satisfied for its own coin, per the standard singleton/owner-signature model used elsewhere in the codebase, e.g. `pkm_pairs_for_conditions_dict` binding signatures to `coin.name()` [2](#0-1) ), nothing in `dl_update_multiple` ties the N spends together with `AssertCoinAnnouncement`/`AssertPuzzleAnnouncement` conditions. As a result, when the wallet aggregates the BLS signatures for all N spends into one `G2Element` (the same aggregation pattern used throughout the wallet, e.g. `WalletTool.sign_transaction` at `chia/simulator/wallet_tools.py:170-202` [3](#0-2) ), a party observing this spend bundle in the mempool can, in principle, subtract individual coin spends and their corresponding partial signature contribution (BLS signatures are linear/aggregable, so removing a spend’s condition and its matching signature component from the aggregate is possible) and resubmit a modified bundle containing only a subset of the singleton updates, still validated correctly by consensus, without ever needing the private key. This is conceptually the same failure mode as the Atomic Green incident: one authorization intended to cover a batch of independent state-changing actions (LP burns / DL root updates) is not cryptographically bound as an atomic set, so it can be decomposed and replayed piecemeal across the independent state objects (LP positions / DL singletons) it was meant to jointly authorize.

### Impact Explanation
An attacker (any unprivileged mempool observer / peer, not necessarily malicious-node/network-layer as excluded by the rules — this is reachable from any spend-bundle submitter or mempool watcher) could take a legitimately signed `dl_update_multiple` bundle intended to atomically update roots for several Data Layer singletons and strip out a subset of the intended updates before/while it propagates, causing only a partial application of the intended multi-singleton update. Depending on how the requester relied on "all-or-nothing" semantics (e.g., coordinated multi-store root commitments, or fee-sharing across launchers as also flagged by the adjacent TODO), this can lead to inconsistent Data Layer root state across singletons that were supposed to be updated together — a coin-set/state divergence from what the legitimate signer authorized, and potential fee-desync since `fee_per_launcher` is split evenly assuming all updates land together.

### Likelihood Explanation
Medium: the vulnerability requires knowledge of BLS signature aggregation properties and specific crafting to subtract a sub-signature and its associated data cleanly, but the code path is a documented, acknowledged gap (`TODO` comment written into the shipped code) reachable by any wallet RPC caller invoking `dl_update_multiple`, and the resulting malformed/subset bundle is a normal, unprivileged mempool submission requiring no special node/peer/network access.

### Recommendation
Link all per-launcher singleton spends generated in `dl_update_multiple` with mutual `CreateCoinAnnouncement`/`AssertCoinAnnouncement` (or `AssertPuzzleAnnouncement`) conditions so that no subset of the spends can be validated without the full announcement graph being present, as already hinted by the existing TODO. Additionally, avoid splitting `fee` evenly across launchers in a way that assumes atomic inclusion, or attach the fee via an announcement-bound spend so a partial submission fails fee-sufficiency checks too.

### Proof of Concept
Not independently reproducible from the indexed code alone (would require constructing and signing a real `DLUpdateMultiple` RPC call across ≥2 launcher IDs, capturing the resulting `WalletSpendBundle`, and demonstrating BLS signature subtraction/removal of one coin spend + its message contribution while keeping the remaining aggregate signature valid for the remaining spends). This requires runtime execution (wallet RPC + full node simulator) that is not available through static code review; the `TODO` at `chia/wallet/wallet_rpc_api.py:3203-3204` [4](#0-3)  confirms the vulnerability class is acknowledged in the code itself, but I could not execute or verify the exploit end-to-end within the available tools.

### Citations

**File:** chia/wallet/wallet_rpc_api.py (L3202-3214)
```python
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
```

**File:** chia/consensus/condition_tools.py (L131-158)
```python
def pkm_pairs_for_conditions_dict(
    conditions_dict: dict[ConditionOpcode, list[ConditionWithArgs]],
    coin: Coin,
    additional_data: bytes,
) -> list[tuple[G1Element, bytes]]:
    ret: list[tuple[G1Element, bytes]] = []

    data = agg_sig_additional_data(additional_data)

    for cwa in conditions_dict.get(ConditionOpcode.AGG_SIG_UNSAFE, []):
        validate_cwa(cwa)
        for disallowed in data.values():
            if cwa.vars[1].endswith(disallowed):
                raise ConsensusError(Err.INVALID_CONDITION)
        ret.append((G1Element.from_bytes(cwa.vars[0]), cwa.vars[1]))

    for opcode in [
        ConditionOpcode.AGG_SIG_PARENT,
        ConditionOpcode.AGG_SIG_PUZZLE,
        ConditionOpcode.AGG_SIG_AMOUNT,
        ConditionOpcode.AGG_SIG_PUZZLE_AMOUNT,
        ConditionOpcode.AGG_SIG_PARENT_AMOUNT,
        ConditionOpcode.AGG_SIG_PARENT_PUZZLE,
        ConditionOpcode.AGG_SIG_ME,
    ]:
        for cwa in conditions_dict.get(opcode, []):
            validate_cwa(cwa)
            ret.append((G1Element.from_bytes(cwa.vars[0]), make_aggsig_final_message(opcode, cwa.vars[1], coin, data)))
```

**File:** chia/simulator/wallet_tools.py (L170-202)
```python
    def sign_transaction(self, coin_spends: list[CoinSpend]) -> SpendBundle:
        signatures = []
        data = agg_sig_additional_data(self.constants.AGG_SIG_ME_ADDITIONAL_DATA)
        agg_sig_opcodes = [
            ConditionOpcode.AGG_SIG_PARENT,
            ConditionOpcode.AGG_SIG_PUZZLE,
            ConditionOpcode.AGG_SIG_AMOUNT,
            ConditionOpcode.AGG_SIG_PUZZLE_AMOUNT,
            ConditionOpcode.AGG_SIG_PARENT_AMOUNT,
            ConditionOpcode.AGG_SIG_PARENT_PUZZLE,
            ConditionOpcode.AGG_SIG_ME,
        ]
        for coin_spend in coin_spends:
            secret_key = self.get_private_key_for_puzzle_hash(coin_spend.coin.puzzle_hash)
            synthetic_secret_key = calculate_synthetic_secret_key(secret_key, DEFAULT_HIDDEN_PUZZLE_HASH)
            conditions_dict = conditions_dict_for_solution(
                coin_spend.puzzle_reveal, coin_spend.solution, self.constants.MAX_BLOCK_COST_CLVM
            )

            for cwa in conditions_dict.get(ConditionOpcode.AGG_SIG_UNSAFE, []):
                msg = cwa.vars[1]
                signature = AugSchemeMPL.sign(synthetic_secret_key, msg)
                signatures.append(signature)

            for agg_sig_opcode in agg_sig_opcodes:
                for cwa in conditions_dict.get(agg_sig_opcode, []):
                    msg = make_aggsig_final_message(agg_sig_opcode, cwa.vars[1], coin_spend.coin, data)
                    signature = AugSchemeMPL.sign(synthetic_secret_key, msg)
                    signatures.append(signature)

        aggsig = AugSchemeMPL.aggregate(signatures)
        spend_bundle = SpendBundle(coin_spends, aggsig)
        return spend_bundle
```
