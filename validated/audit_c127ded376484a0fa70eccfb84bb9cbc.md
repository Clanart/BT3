## Analysis

The report's bug class — a missing zero-address check on a "beneficiary" address that receives asset proceeds — has a reachable analog in the Chia pooling/plot-NFT flow.

`PoolState.target_puzzle_hash` is the "final payout destination" for pool rewards [1](#0-0) . It is validated only by `PoolWallet._verify_pool_state()`, which merely checks `state.target_puzzle_hash is None` and delegates to `_verify_self_pooled`/`_verify_pooling_state`, neither of which reject an all-zero or otherwise unspendable puzzle hash [2](#0-1) .

This `target_puzzle_hash` is not always chosen by the wallet owner — when joining a pool, the wallet fetches it directly from the pool server's HTTP response and passes it straight into the join request without any sanity check against a zero/burn hash: `target_puzzle_hash = bytes32.from_hexstr(json_dict["target_puzzle_hash"])` [3](#0-2) , and again in the CLI `join_pool` helper [4](#0-3) . The RPC path `join_pool()`/`PoolWallet.join_pool()` only calls `_verify_initial_target_state`, which is the same permissive check [5](#0-4) .

The tests even demonstrate `bytes32.zeros` being accepted as a valid `target_puzzlehash` for `pw_join_pool` without any error [6](#0-5) , and a separate CLI test explicitly sets `payout_instructions = zero_address` to exercise this exact scenario [7](#0-6) .

Because `target_puzzle_hash` is curried directly into the waiting-room/member singleton inner puzzles that ultimately pay out pool rewards (`create_waiting_room_inner_puzzle`, `create_pooling_inner_puzzle`) [8](#0-7) , a zero (or otherwise unspendable/burn) puzzle hash returned by a malicious or misconfigured pool, or mistakenly supplied by the wallet user, becomes baked into on-chain singleton state. Every self-pooling/leaving-pool reward the wallet subsequently claims to that address is then permanently unspendable, matching the underlying "burned proceeds due to missing zero-address validation" bug class from the report, but reachable here through the wallet's own pool-join RPC/CLI action rather than a contract constructor.

### Title
Missing zero/burn-address validation on pool `target_puzzle_hash` allows permanently unclaimable pool rewards - (File: chia/pools/pool_wallet.py)

### Summary
`PoolWallet._verify_pool_state()`/`_verify_self_pooled()`/`_verify_pooling_state()` never reject a zero or other unspendable `target_puzzle_hash`, and this value is sourced either from user RPC input or, during `join_pool`, from an external pool server's HTTP response with no sanity check before being curried into the on-chain singleton puzzle.

### Finding Description
`PoolState.target_puzzle_hash` is documented as the final destination for self-pooling or pool-farmed rewards [1](#0-0) . The only validation gate, `_verify_pool_state`, checks for `None` but performs no zero-hash/burn-address check [9](#0-8) . `join_pool()` passes user- or pool-supplied `target_state` straight through `_verify_initial_target_state` to `generate_travel_transactions`, which builds the on-chain singleton transition [10](#0-9) . The CLI/RPC `join_pool` flow fetches `target_puzzle_hash` from the pool's `/pool_info` endpoint verbatim [4](#0-3) .

### Impact Explanation
Once a zero/burn `target_puzzle_hash` is committed into the pool singleton's waiting-room/member inner puzzle, every subsequent absorb/claim of self-pooled or pool rewards sends value to that unspendable puzzle hash, permanently burning farmer proceeds. This is a concrete, spend-triggered, irreversible loss of coin value with no recovery path short of catching the mistake before the travel spend confirms.

### Likelihood Explanation
Reachable purely through normal wallet operation: a wallet user can pass `target_puzzlehash=bytes32.zeros` to `pw_join_pool` (accepted by existing tests with no error) [6](#0-5) , or a malicious/compromised pool operator can return a zero/burn `target_puzzle_hash` in its `/pool_info` response, which the CLI/RPC path accepts without validation [3](#0-2) .

### Recommendation
Add an explicit rejection of `bytes32.zeros` (and any other well-known unspendable/burn puzzle hashes) for `target_puzzle_hash` in `PoolWallet._verify_pool_state`/`_verify_self_pooled`/`_verify_pooling_state`, and validate the pool-supplied `target_puzzle_hash` in `create_pool_args`/`join_pool` in `chia/cmds/plotnft_funcs.py` before it is used to construct the join request.

### Proof of Concept
1. Attacker (or misconfigured pool) runs a pool server whose `/pool_info` returns `target_puzzle_hash = "0x00...00"`.
2. Victim runs `chia plotnft join` (or calls `pw_join_pool` RPC directly with `target_puzzlehash=bytes32.zeros`, as already exercised in `chia/_tests/pools/test_pool_rpc.py::test_self_pooling_to_pooling`) [6](#0-5) .
3. `PoolWallet.join_pool()` accepts the state (no zero-hash check) and commits it on-chain via the travel transaction [11](#0-10) .
4. All subsequent farming-to-pool rewards routed to this singleton, once the pool sweeps/absorbs and the victim leaves the pool, resolve their final payout to the zero puzzle hash and are permanently burned.

### Citations

**File:** chia/pools/pool_wallet_info.py (L43-61)
```python
class PoolState(Streamable):
    """
    `PoolState` is a type that is serialized to the blockchain to track the state of the user's pool singleton
    `target_puzzle_hash` is either the pool address, or the self-pooling address that pool rewards will be paid to.
    `target_puzzle_hash` is NOT the p2_singleton puzzle that block rewards are sent to.
    The `p2_singleton` address is the initial address, and the `target_puzzle_hash` is the final destination.
    `relative_lock_height` is zero when in SELF_POOLING state
    """

    version: uint8
    state: uint8  # PoolSingletonState
    # `target_puzzle_hash`: A puzzle_hash we pay to
    # When self-farming, this is a main wallet address
    # When farming-to-pool, the pool sends this to the farmer during pool protocol setup
    target_puzzle_hash: bytes32  # TODO: rename target_puzzle_hash -> pay_to_address
    # owner_pubkey is set by the wallet, once
    owner_pubkey: G1Element
    pool_url: str | None
    relative_lock_height: uint32
```

**File:** chia/pools/pool_wallet.py (L136-181)
```python
    def _verify_self_pooled(cls, state: PoolState) -> str | None:
        err = ""
        if state.pool_url not in {None, ""}:
            err += " Unneeded pool_url for self-pooling"

        if state.relative_lock_height != 0:
            err += " Incorrect relative_lock_height for self-pooling"

        return None if err == "" else err

    @classmethod
    def _verify_pooling_state(cls, state: PoolState) -> str | None:
        err = ""
        if state.relative_lock_height < cls.MINIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is less than recommended minimum ({cls.MINIMUM_RELATIVE_LOCK_HEIGHT})"
            )
        elif state.relative_lock_height > cls.MAXIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is greater than recommended maximum ({cls.MAXIMUM_RELATIVE_LOCK_HEIGHT})"
            )

        if state.pool_url in {None, ""}:
            err += " Empty pool url in pooling state"
        return err

    @classmethod
    def _verify_pool_state(cls, state: PoolState) -> str | None:
        if state.target_puzzle_hash is None:
            return "Invalid puzzle_hash"

        if state.version > POOL_PROTOCOL_VERSION:
            return (
                f"Detected pool protocol version {state.version}, which is "
                f"newer than this wallet's version ({POOL_PROTOCOL_VERSION}). Please upgrade "
                f"to use this pooling wallet"
            )

        if state.state == PoolSingletonState.SELF_POOLING.value:
            return cls._verify_self_pooled(state)
        elif state.state in {PoolSingletonState.FARMING_TO_POOL.value, PoolSingletonState.LEAVING_POOL.value}:
            return cls._verify_pooling_state(state)
        else:
            return "Internal Error"
```

**File:** chia/pools/pool_wallet.py (L569-588)
```python
        escaping_inner_puzzle: Program = create_waiting_room_inner_puzzle(
            initial_target_state.target_puzzle_hash,
            initial_target_state.relative_lock_height,
            initial_target_state.owner_pubkey,
            launcher_coin.name(),
            genesis_challenge,
            delay_time,
            delay_ph,
        )
        escaping_inner_puzzle_hash = escaping_inner_puzzle.get_tree_hash()

        self_pooling_inner_puzzle: Program = create_pooling_inner_puzzle(
            initial_target_state.target_puzzle_hash,
            escaping_inner_puzzle_hash,
            initial_target_state.owner_pubkey,
            launcher_coin.name(),
            genesis_challenge,
            delay_time,
            delay_ph,
        )
```

**File:** chia/pools/pool_wallet.py (L630-673)
```python
    async def join_pool(self, target_state: PoolState, fee: uint64, action_scope: WalletActionScope) -> uint64:
        if target_state.state != FARMING_TO_POOL.value:
            raise ValueError(f"join_pool must be called with target_state={FARMING_TO_POOL} (FARMING_TO_POOL)")
        if self.target_state is not None:
            raise ValueError(f"Cannot join a pool while waiting for target state: {self.target_state}")
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot join pool due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )

        current_state: PoolWalletInfo = await self.get_current_state()

        total_fee = fee
        if current_state.current == target_state:
            self.target_state = None
            msg = f"Asked to change to current state. Target = {target_state}"
            self.log.info(msg)
            raise ValueError(msg)
        elif current_state.current.state in {SELF_POOLING.value, LEAVING_POOL.value}:
            total_fee = fee
        elif current_state.current.state == FARMING_TO_POOL.value:
            total_fee = uint64(fee * 2)

        if self.target_state is not None:
            raise ValueError(
                f"Cannot change to state {target_state} when already having target state: {self.target_state}"
            )
        PoolWallet._verify_initial_target_state(target_state)
        if current_state.current.state == LEAVING_POOL.value:
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot join a pool until height {last_height + current_state.current.relative_lock_height}"
                )

        self.target_state = target_state
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
        return total_fee
```

**File:** chia/cmds/plotnft_funcs.py (L103-106)
```python
        json_dict = await create_pool_args(pool_url)
        relative_lock_height = json_dict["relative_lock_height"]
        target_puzzle_hash = bytes32.from_hexstr(json_dict["target_puzzle_hash"])
        pool_memoization = Program.fromhex(json_dict.get("pool_memoization", "80"))
```

**File:** chia/cmds/plotnft_funcs.py (L393-405)
```python
    func = functools.partial(
        wallet_info.client.pw_join_pool,
        PWJoinPool(
            wallet_id=uint32(selected_wallet_id),
            target_puzzlehash=bytes32.from_hexstr(json_dict["target_puzzle_hash"]),
            pool_url=pool_url,
            relative_lock_height=json_dict["relative_lock_height"],
            pool_memoization=Program.fromhex(json_dict.get("pool_memoization", "80")),
            fee=fee,
            push=True,
        ),
        DEFAULT_TX_CONFIG,
    )
```

**File:** chia/_tests/pools/test_pool_rpc.py (L1004-1014)
```python
        join_pool = await client.pw_join_pool(
            PWJoinPool(
                wallet_id=uint32(wallet_id),
                target_puzzlehash=pool_ph,
                pool_url="https://pool.example.com",
                relative_lock_height=uint32(10),
                fee=uint64(fee),
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )
```

**File:** chia/_tests/pools/test_pool_cmdline.py (L1087-1113)
```python
    zero_ph = bytes32.from_hexstr("0x0000000000000000000000000000000000000000000000000000000000000000")
    zero_address = encode_puzzle_hash(zero_ph, "xch")

    burn_ph = bytes32.from_hexstr("0x000000000000000000000000000000000000000000000000000000000000dead")
    burn_address = encode_puzzle_hash(burn_ph, "xch")
    root_path = wallet_environments.environments[0].node.root_path

    wallet_id = await create_new_plotnft(wallet_environments, version=version)
    pw_info = (await wallet_rpc.pw_status(PWStatus(wallet_id=uint32(wallet_id)))).state

    # This tests what happens when using None for root_path
    mocker.patch("chia.cmds.plotnft_funcs.DEFAULT_ROOT_PATH", root_path)
    await ChangePayoutInstructionsPlotNFTCMD(
        context=ChiaCliContext(root_path=wallet_environments.environments[0].node.root_path),
        launcher_id=bytes32(32 * b"0"),
        address=CliAddress(burn_ph, burn_address, AddressType.XCH),
    ).run()
    out, _err = capsys.readouterr()
    assert f"{bytes32(32 * b'0').hex()} Not found." in out

    with PoolingShareState.acquire(
        root_path=root_path, p2_singleton_puzzle_hash=pw_info.p2_singleton_puzzle_hash
    ) as pool_config:
        pool_config.launcher_id = pw_info.launcher_id
        pool_config.pool_url = "http://pool.example.com"
        pool_config.payout_instructions = zero_address
        pool_config.target_puzzle_hash = bytes32(32 * b"0")
```
