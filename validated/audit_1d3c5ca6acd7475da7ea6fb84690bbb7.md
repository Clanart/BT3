I found a reachable analog: `WalletSigner.gather_signing_info` calls `conditions_dict_for_solution`, which runs a coin's puzzle/solution and then calls `parse_sexp_to_conditions` → `parse_sexp_to_condition` on the CLVM output. That function does `ConditionOpcode(op)` on the raw single-byte opcode extracted from the puzzle's output [1](#0-0) . `ConditionOpcode` is a `bytes, enum.Enum` subclass, and constructing it with a byte value that isn't one of the enumerated members raises an unhandled `ValueError` [2](#0-1) .

The caller `conditions_for_solution` only catches `Program.EvalError`, not `ValueError`: [3](#0-2) 

`conditions_dict_for_solution` (which wraps it) also has no broader exception handling [4](#0-3) . This is invoked directly from `WalletSigner.gather_signing_info`, on every `Spend`'s `puzzle_reveal`/`solution` pair, when preparing signing instructions for an unsigned transaction or an offer: [5](#0-4) 

This is reachable via `gather_signing_info_for_trades` (processing an untrusted `Offer._bundle` from an offer counterparty) and `gather_signing_info_for_bundles`/`gather_signing_info_for_txs`, which are used by wallet RPC signing flows. An attacker who crafts a coin spend/offer whose puzzle output emits a condition with a single-byte opcode outside the known `ConditionOpcode` enum values (any byte not in the set {1,43-52,60-67,70-76,80-87,90}) will cause an unhandled `ValueError` to propagate out of `parse_sexp_to_condition`, uncaught by `conditions_for_solution`/`conditions_dict_for_solution`, crashing the signing/offer-parsing call path in the wallet.

This is the closest reachable analog to CVE-2024-0208's "improper handling of missing/malformed values causing a parser crash" pattern: a value that fails to map to a known code path is not gracefully rejected but raises an unhandled exception. However note the elsewhere-used entrypoint `parse_conditions_non_consensus` (wallet condition parsing used for JSON/offer display) explicitly wraps per-condition parsing in `try/except Exception` and falls back to `UnknownCondition`, showing the codebase's intended defensive pattern is missing specifically in `condition_tools.py`'s `parse_sexp_to_condition`/`conditions_for_solution` path: [6](#0-5) 

Given the strict validation rules (require concrete unsigned/unauthorized coin movement, supply inflation, offer theft, coin-set divergence, invalid spend/block acceptance, reward redirection, or a spend-triggered transaction-processing halt), this bug's actual impact is a crash/exception when the wallet processes an attacker-supplied offer or spend during signing — a **spend/offer-triggered processing halt** in the wallet's signing pipeline (denial of service against the local wallet process), not a consensus-affecting bug (mempool's real condition parsing happens in the Rust `chia_rs` layer, not this Python path, so consensus/mempool admission is unaffected).

### Title
Unhandled ValueError on Unrecognized Condition Opcode Crashes Wallet Signing/Offer Processing - (File: chia/consensus/condition_tools.py)

### Summary
`parse_sexp_to_condition` constructs a `ConditionOpcode` enum member directly from an untrusted single-byte value produced by running a coin's puzzle with its solution. If the byte doesn't match a known opcode, `ConditionOpcode(op)` raises `ValueError`, which is not caught anywhere in the call chain (`parse_sexp_to_conditions` → `conditions_for_solution` → `conditions_dict_for_solution`), unlike the codebase's other condition-parsing entrypoint (`parse_conditions_non_consensus`) which defensively catches all exceptions.

### Finding Description
`parse_sexp_to_condition` extracts the opcode atom from a CLVM-produced condition and calls `ConditionOpcode(op)` without validating that `op` is a known opcode value first [1](#0-0) . `ConditionOpcode` only defines specific single-byte members [2](#0-1) ; any other single-byte value raises `ValueError`. The wrapping function `conditions_for_solution` only handles `Program.EvalError`, letting `ValueError` propagate [3](#0-2) , and `conditions_dict_for_solution` adds no additional handling [4](#0-3) .

`WalletSigner.gather_signing_info` calls `conditions_dict_for_solution` on every spend's puzzle reveal/solution when building signing instructions [5](#0-4) , which is reached from `gather_signing_info_for_trades` (offers) and `gather_signing_info_for_bundles`/`gather_signing_info_for_txs` (transaction records), i.e. paths driven by an offer counterparty's or wallet user's spend data.

### Impact Explanation
A crafted puzzle/solution (e.g., embedded in an offer file, or a coin spend submitted for local signing) whose CLVM execution emits a condition with an opcode byte outside the recognized set will raise an unhandled `ValueError` deep in the wallet signing pipeline. This halts processing of that transaction/offer signing request, denying service to the wallet's signing/offer-acceptance functionality for the affected call.

### Likelihood Explanation
Reachable by any offer counterparty or spend-bundle-for-signing submitter without special privileges — the only requirement is providing a puzzle whose evaluated conditions include a non-standard opcode byte, which is trivial to construct with any custom CLVM puzzle.

### Recommendation
Wrap the `ConditionOpcode(op)` lookup in `parse_sexp_to_condition` with exception handling (or use a safe `.get`-style lookup) that raises/propagates a `ConsensusError` (consistent with the rest of the function) instead of an unhandled `ValueError`, or catch broader exceptions in `conditions_for_solution`/`conditions_dict_for_solution` the same way `parse_conditions_non_consensus` already does.

### Proof of Concept
Construct a coin whose puzzle, when run with any solution, outputs a condition list containing `(op ...)` where `op` is a single byte not in `{1,43,44,45,46,47,48,49,50,51,52,60,61,62,63,64,65,66,67,70,71,72,73,74,75,76,80,81,82,83,84,85,86,87,90}` (e.g., opcode byte `99`). Package this as a `CoinSpend`/`Spend` and pass it through `WalletSigner.gather_signing_info` (or embed it in an `Offer` and call `gather_signing_info_for_trades`); the call raises an unhandled `ValueError: 99 is not a valid ConditionOpcode` instead of a graceful error, aborting the signing operation for the batch.

### Citations

**File:** chia/consensus/condition_tools.py (L19-45)
```python
def parse_sexp_to_condition(sexp: Program) -> ConditionWithArgs:
    """
    Takes a ChiaLisp sexp and returns a ConditionWithArgs.
    Raises an ConsensusError if it fails.
    """
    first = sexp.pair
    if first is None:
        raise ConsensusError(Err.INVALID_CONDITION, ["first is None"])
    op = first[0].atom
    if op is None or len(op) != 1:
        raise ConsensusError(Err.INVALID_CONDITION, ["invalid op"])

    # since the ConditionWithArgs only has atoms as the args, we can't parse
    # hints and memos with this function. We just exit the loop if we encounter
    # a pair instead of an atom
    vars: list[bytes] = []
    for arg in Program(first[1]).as_iter():
        a = arg.atom
        if a is None:
            break
        vars.append(a)
        # no condition (currently) has more than 3 arguments. Additional
        # arguments are allowed but ignored
        if len(vars) > 3:
            break

    return ConditionWithArgs(ConditionOpcode(op), vars)
```

**File:** chia/consensus/condition_tools.py (L163-169)
```python
def conditions_dict_for_solution(
    puzzle_reveal: Program | SerializedProgram, solution: Program | SerializedProgram, max_cost: int
) -> dict[ConditionOpcode, list[ConditionWithArgs]]:
    conditions_dict: dict[ConditionOpcode, list[ConditionWithArgs]] = {}
    for cvp in conditions_for_solution(puzzle_reveal, solution, max_cost):
        conditions_dict.setdefault(cvp.opcode, list()).append(cvp)
    return conditions_dict
```

**File:** chia/consensus/condition_tools.py (L172-181)
```python
def conditions_for_solution(
    puzzle_reveal: Program | SerializedProgram, solution: Program | SerializedProgram, max_cost: int
) -> list[ConditionWithArgs]:
    # get the standard script for a puzzle hash and feed in the solution
    try:
        _cost, r = run_with_cost(puzzle_reveal, max_cost, solution)
        return parse_sexp_to_conditions(r)
    except Program.EvalError as e:
        raise ConsensusError(Err.SEXP_ERROR, [str(e)]) from e

```

**File:** chia/types/condition_opcodes.py (L7-73)
```python
class ConditionOpcode(bytes, enum.Enum):
    # AGG_SIG is ascii "1"

    # the conditions below require bls12-381 signatures

    AGG_SIG_PARENT = bytes([43])
    AGG_SIG_PUZZLE = bytes([44])
    AGG_SIG_AMOUNT = bytes([45])
    AGG_SIG_PUZZLE_AMOUNT = bytes([46])
    AGG_SIG_PARENT_AMOUNT = bytes([47])
    AGG_SIG_PARENT_PUZZLE = bytes([48])
    AGG_SIG_UNSAFE = bytes([49])
    AGG_SIG_ME = bytes([50])

    # the conditions below reserve coin amounts and have to be accounted for in output totals

    CREATE_COIN = bytes([51])
    RESERVE_FEE = bytes([52])

    # the conditions below deal with announcements, for inter-coin communication

    CREATE_COIN_ANNOUNCEMENT = bytes([60])
    ASSERT_COIN_ANNOUNCEMENT = bytes([61])
    CREATE_PUZZLE_ANNOUNCEMENT = bytes([62])
    ASSERT_PUZZLE_ANNOUNCEMENT = bytes([63])
    ASSERT_CONCURRENT_SPEND = bytes([64])
    ASSERT_CONCURRENT_PUZZLE = bytes([65])

    # new message conditions in softfork introduced in Chia 2.3

    SEND_MESSAGE = bytes([66])
    RECEIVE_MESSAGE = bytes([67])

    # the conditions below let coins inquire about themselves

    ASSERT_MY_COIN_ID = bytes([70])
    ASSERT_MY_PARENT_ID = bytes([71])
    ASSERT_MY_PUZZLEHASH = bytes([72])
    ASSERT_MY_AMOUNT = bytes([73])
    ASSERT_MY_BIRTH_SECONDS = bytes([74])
    ASSERT_MY_BIRTH_HEIGHT = bytes([75])
    ASSERT_EPHEMERAL = bytes([76])

    # the conditions below ensure that we're "far enough" in the future

    # wall-clock time
    ASSERT_SECONDS_RELATIVE = bytes([80])
    ASSERT_SECONDS_ABSOLUTE = bytes([81])

    # block index
    ASSERT_HEIGHT_RELATIVE = bytes([82])
    ASSERT_HEIGHT_ABSOLUTE = bytes([83])

    # wall-clock time
    ASSERT_BEFORE_SECONDS_RELATIVE = bytes([84])
    ASSERT_BEFORE_SECONDS_ABSOLUTE = bytes([85])

    # block index
    ASSERT_BEFORE_HEIGHT_RELATIVE = bytes([86])
    ASSERT_BEFORE_HEIGHT_ABSOLUTE = bytes([87])

    # to be activated with the 2.0 hard fork.
    # the first parameter is always the cost of the condition
    SOFTFORK = bytes([90])

    # A condition that is always true and always ignore all arguments
    REMARK = bytes([1])
```

**File:** chia/wallet/wallet_signer.py (L94-117)
```python
    async def gather_signing_info(self, spends: list[Spend]) -> SigningInstructions:
        pks: list[bytes] = []
        signing_targets: list[SigningTarget] = []
        for spend in spends:
            coin_spend = spend.as_coin_spend()
            # Get AGG_SIG conditions
            conditions_dict = conditions_dict_for_solution(
                coin_spend.puzzle_reveal,
                coin_spend.solution,
                self.max_block_cost_clvm,
            )
            # Create signature
            for pk, msg in pkm_pairs_for_conditions_dict(
                conditions_dict, coin_spend.coin, self.agg_sig_me_additional_data
            ):
                pk_bytes = bytes(pk)
                pks.append(pk_bytes)
                fingerprint: bytes = pk.get_fingerprint().to_bytes(4, "big")
                signing_targets.append(SigningTarget(fingerprint, msg, std_hash(pk_bytes + msg)))

        return SigningInstructions(
            await self.key_hints_for_pubkeys(pks),
            signing_targets,
        )
```

**File:** chia/wallet/conditions.py (L1371-1386)
```python
def parse_conditions_non_consensus(
    conditions: Iterable[Program],
    abstractions: bool = True,  # Use abstractions like *Announcement or Timelock instead of specific condition class
) -> list[Condition]:
    driver_dictionary: dict[bytes, type[Condition]] = (
        CONDITION_DRIVERS_W_ABSTRACTIONS if abstractions else CONDITION_DRIVERS
    )
    final_condition_list: list[Condition] = []
    for condition in conditions:
        try:
            final_condition_list.append(driver_dictionary[condition.at("f").as_atom()].from_program(condition))
        except Exception:
            final_condition_list.append(UnknownCondition.from_program(condition))

    return final_condition_list

```
