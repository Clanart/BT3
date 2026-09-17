import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 23
# todo: the path from https://github.com/sei-protocol/sei-chain
SOURCE_REPO = "sei-protocol/sei-chain"
# todo: the name of the repository
REPO_NAME = "sei-chain"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    # =================================================================================
    # Dual-address model: Sei <-> EVM association, cast addresses, receive gating
    # =================================================================================
    "x/evm/keeper/address.go",
    "x/evm/keeper/keeper.go",
    "x/evm/types/message_associate.go",
    "x/evm/types/message_associate_contract_address.go",
    "x/evm/types/ethtx/associate_tx.go",
    "utils/helpers/address.go",
    "utils/helpers/associate.go",
    "evmrpc/association.go",
    "evmrpc/rpcutils/sig.go",

    # =================================================================================
    # EVM ante pipeline: routing, preprocess/sender recovery, nonce, fee, gas metering
    # =================================================================================
    "x/evm/ante/router.go",
    "x/evm/ante/no_cosmos_fields.go",
    "x/evm/ante/preprocess.go",
    "x/evm/ante/basic.go",
    "x/evm/ante/sig.go",
    "x/evm/ante/fee.go",
    "x/evm/ante/gas.go",
    "x/evm/keeper/ante.go",
    "x/evm/derived/derived.go",
    "app/ante.go",
    "app/ante/evm_checktx.go",
    "app/ante/evm_delivertx.go",
    "app/ante/cosmos_checktx.go",
    "app/ante/cosmos_delivertx.go",
    "app/antedecorators/gas.go",
    "app/antedecorators/priority.go",
    "app/antedecorators/authz_nested_message.go",
    "sei-cosmos/x/auth/ante/ante.go",
    "sei-cosmos/x/auth/ante/basic.go",
    "sei-cosmos/x/auth/ante/fee.go",
    "sei-cosmos/x/auth/ante/setup.go",
    "sei-cosmos/x/auth/ante/sigverify.go",
    "sei-cosmos/x/auth/ante/validator_tx_fee.go",

    # =================================================================================
    # Ethereum transaction decoding, typed-tx validation and EIP-7702 authorizations
    # =================================================================================
    "x/evm/types/message_evm_transaction.go",
    "x/evm/types/ethtx/txdata.go",
    "x/evm/types/ethtx/legacy_tx.go",
    "x/evm/types/ethtx/access_list_tx.go",
    "x/evm/types/ethtx/access_list.go",
    "x/evm/types/ethtx/dynamic_fee_tx.go",
    "x/evm/types/ethtx/set_code_tx.go",
    "x/evm/types/ethtx/blob_tx.go",
    "x/evm/types/ethtx/auth_list.go",
    "x/evm/types/ethtx/validations.go",
    "x/evm/types/ethtx/semantic_validation.go",
    "x/evm/types/ethtx/int.go",
    "x/evm/types/ethtx/utils.go",

    # =================================================================================
    # StateDB bridge: usei/wei balance conversion, journal, snapshot/revert, refunds
    # =================================================================================
    "x/evm/state/statedb.go",
    "x/evm/state/state.go",
    "x/evm/state/balance.go",
    "x/evm/state/transfer.go",
    "x/evm/state/journal.go",
    "x/evm/state/nonce.go",
    "x/evm/state/code.go",
    "x/evm/state/log.go",
    "x/evm/state/refund.go",
    "x/evm/state/accesslist.go",
    "x/evm/state/check.go",
    "x/evm/state/keys.go",
    "x/evm/state/utils.go",
    "x/evm/keeper/balance.go",
    "x/evm/keeper/nonce.go",
    "x/evm/keeper/code.go",
    "x/evm/keeper/state.go",

    # =================================================================================
    # EVM execution, fee/base-fee accounting, deferred EndBlock work and receipts
    # =================================================================================
    "x/evm/keeper/evm.go",
    "x/evm/keeper/msg_server.go",
    "x/evm/keeper/tx.go",
    "x/evm/keeper/fee.go",
    "x/evm/keeper/coinbase.go",
    "x/evm/keeper/deferred.go",
    "x/evm/keeper/receipt.go",
    "x/evm/keeper/log.go",
    "x/evm/keeper/abci.go",
    "x/evm/keeper/block_hash.go",
    "x/evm/keeper/storage_cleanup.go",
    "x/evm/keeper/params.go",
    "x/evm/keeper/view.go",
    "x/evm/keeper/grpc_query.go",
    "x/evm/types/params.go",
    "x/evm/types/keys.go",
    "x/evm/types/config.go",
    "x/evm/types/constants.go",
    "x/evm/types/logs.go",
    "x/evm/module.go",
    "x/evm/gov.go",
    "x/evm/handler.go",
    "app/receipt.go",
    "sync/gas.go",
    "types/settlement.go",

    # =================================================================================
    # Cosmos precompiles: caller authority, payment handling, gas and ABI decoding
    # =================================================================================
    "precompiles/setup.go",
    "precompiles/common/precompiles.go",
    "precompiles/common/authorization.go",
    "precompiles/common/decode_cost.go",
    "precompiles/common/evm_events.go",
    "precompiles/utils/types.go",
    "precompiles/utils/expected_keepers.go",
    "precompiles/bank/bank.go",
    "precompiles/staking/staking.go",
    "precompiles/distribution/distribution.go",
    "precompiles/gov/gov.go",
    "precompiles/gov/handler.go",
    "precompiles/wasmd/wasmd.go",
    "precompiles/pointer/pointer.go",
    "precompiles/pointerview/pointerview.go",
    "precompiles/addr/addr.go",
    "precompiles/auth/auth.go",
    "precompiles/authz/authz.go",
    "precompiles/json/json.go",
    "precompiles/oracle/oracle.go",
    "precompiles/mint/mint.go",
    "precompiles/slashing/slashing.go",
    "precompiles/evidence/evidence.go",
    "precompiles/ibc/ibc.go",
    "precompiles/solo/solo.go",
    "precompiles/p256/p256.go",
    "precompiles/p256/verifier.go",
    "precompiles/params/params.go",
    "precompiles/upgrade/upgrade.go",
    "x/evm/keeper/precompile.go",

    # =================================================================================
    # CW <-> EVM pointer contracts and the CosmWasm bridge into the EVM
    # =================================================================================
    "x/evm/keeper/pointer.go",
    "x/evm/keeper/pointer_upgrade.go",
    "x/evm/types/message_register_pointer.go",
    "x/evm/types/message_internal_evm_call.go",
    "x/evm/types/message_internal_evm_delegate_call.go",
    "x/evm/types/message_send.go",
    "x/evm/types/whitelist.go",
    "x/evm/client/wasm/query.go",
    "x/evm/client/wasm/encoder.go",
    "wasmbinding/message_plugin.go",
    "wasmbinding/query_plugin.go",
    "wasmbinding/queries.go",
    "wasmbinding/encoder.go",
    "wasmbinding/wasm.go",
    "wasmbinding/bindings/msg.go",

    # =================================================================================
    # Native token authority: bank sends, tokenfactory denoms, mint and epochs
    # =================================================================================
    "sei-cosmos/x/bank/keeper/keeper.go",
    "sei-cosmos/x/bank/keeper/send.go",
    "sei-cosmos/x/bank/keeper/view.go",
    "sei-cosmos/x/bank/keeper/deferred_cache.go",
    "sei-cosmos/x/bank/keeper/msg_server.go",
    "x/tokenfactory/keeper/msg_server.go",
    "x/tokenfactory/keeper/createdenom.go",
    "x/tokenfactory/keeper/admins.go",
    "x/tokenfactory/keeper/bankactions.go",
    "x/tokenfactory/keeper/creators.go",
    "x/tokenfactory/keeper/keeper.go",
    "x/tokenfactory/types/msgs.go",
    "x/tokenfactory/types/denoms.go",
    "x/tokenfactory/types/authority_metadata.go",
    "x/tokenfactory/client/wasm/encoder.go",
    "x/mint/keeper/keeper.go",
    "x/mint/keeper/hooks.go",
    "x/mint/types/minter.go",
    "x/epoch/keeper/abci.go",
    "x/epoch/keeper/epoch.go",

    # =================================================================================
    # Oracle vote admission and tally reachable from a submitted message
    # =================================================================================
    "x/oracle/abci.go",
    "x/oracle/tally.go",
    "x/oracle/keeper/msg_server.go",
    "x/oracle/keeper/ballot.go",
    "x/oracle/keeper/slash.go",
    "x/oracle/keeper/vote_target.go",
    "x/oracle/keeper/keeper.go",
    "x/oracle/types/msgs.go",
    "x/oracle/types/ballot.go",
    "x/oracle/types/vote.go",
    "x/oracle/types/denom.go",

    # =================================================================================
    # Block lifecycle, OCC parallel execution and store layering (determinism)
    # =================================================================================
    "app/abci.go",
    "app/app.go",
    "app/prioritizer.go",
    "app/upgrades.go",
    "app/upgrades/fork_manager.go",
    "app/legacyabci/check_tx.go",
    "app/legacyabci/deliver_tx.go",
    "app/legacyabci/begin_block.go",
    "app/legacyabci/end_block.go",
    "app/legacyabci/recovery.go",
    "sei-cosmos/baseapp/abci.go",
    "sei-cosmos/baseapp/baseapp.go",
    "sei-cosmos/baseapp/state.go",
    "sei-cosmos/baseapp/recovery.go",
    "sei-cosmos/baseapp/msg_service_router.go",
    "sei-cosmos/store/multiversion/store.go",
    "sei-cosmos/store/multiversion/mvkv.go",
    "sei-cosmos/store/multiversion/data_structures.go",
    "sei-cosmos/store/multiversion/memiterator.go",
    "sei-cosmos/store/multiversion/mergeiterator.go",
    "sei-cosmos/store/multiversion/trackediterator.go",
    "sei-cosmos/store/cachekv/store.go",
    "sei-cosmos/store/cachekv/mergeiterator.go",
    "sei-cosmos/store/cachekv/memiterator.go",
    "store/whitelist/kv/store.go",
    "store/whitelist/multi/store.go",
    "store/whitelist/cachemulti/store.go",
    "utils/prioritized_txs.go",
    "utils/metadata.go",
    "utils/panic.go",

    # =================================================================================
    # Public EVM JSON-RPC surface: handlers, encoders, filters, tracers, limiters
    # =================================================================================
    "evmrpc/server.go",
    "evmrpc/endpoints.go",
    "evmrpc/block.go",
    "evmrpc/tx.go",
    "evmrpc/state.go",
    "evmrpc/filter.go",
    "evmrpc/simulate.go",
    "evmrpc/send.go",
    "evmrpc/subscribe.go",
    "evmrpc/tracers.go",
    "evmrpc/trace_baker.go",
    "evmrpc/trace_profile.go",
    "evmrpc/trace_tx_decoder.go",
    "evmrpc/block_trace_profiled.go",
    "evmrpc/txpool.go",
    "evmrpc/info.go",
    "evmrpc/utils.go",
    "evmrpc/bloom.go",
    "evmrpc/ethbloom/bloom.go",
    "evmrpc/query_builder.go",
    "evmrpc/notifier.go",
    "evmrpc/context.go",
    "evmrpc/worker_pool.go",
    "evmrpc/watermark_manager.go",
    "evmrpc/rpcstack.go",
    "evmrpc/request_limiter.go",
    "evmrpc/rate_limit_middleware.go",
    "evmrpc/jwt_handler.go",
    "evmrpc/sei_legacy.go",
    "evmrpc/sei_legacy_http.go",
    "ratelimiter/gate.go",
    "ratelimiter/inflight.go",
    "ratelimiter/method_bucket.go",
    "ratelimiter/method_parser.go",
    "ratelimiter/conn_limit.go",
    "ratelimiter/registry.go",
]


target_scopes = [
    "Critical. An unprivileged account permanently freezes or steals funds through the Sei/EVM dual-address model, because SetAddressMapping, GetSeiAddress, GetEVMAddress, GetSeiAddressOrDefault, GetEVMAddressOrDefault or CanAddressReceive resolves the wrong side of the pair: an attacker front-runs or overrides a victim's association with MsgAssociate, AssociateContractAddress or a first signed tx, or makes value land on the cast-address view of an account whose owner can only spend the associated view, leaving funds with no on-chain remediation path.",
    "Critical. Balance is created or destroyed crossing the 6-decimal usei / 18-decimal wei boundary, because AddBalance, SubBalance, SetBalance, GetBalance and the transfer helpers in x/evm/state, the wei-remainder accounting, and the bank-keeper writes they drive round, truncate, or double-apply a value, so an ordinary EVM transfer, precompile payment, or reverted call leaves total usei supply or an account balance higher or lower than conserved value allows.",
    "Critical. A Cosmos precompile acts with an authority the EVM caller does not hold, because caller derivation and payment handling in precompiles/common (Execute, RunAndCalculateGas, ValidateNonPayable, HandlePaymentUsei, HandlePaymentUseiWei, GetSeiAddressFromArg) or an individual precompile lets an attacker reach bank send, staking delegate/undelegate/redelegate, distribution withdraw, gov vote, authz grant, or wasmd execute under a victim's Sei address - via DELEGATECALL/CALLCODE context, a contract caller, a spoofed address argument, or value forwarded without being charged.",
    "Critical. The CW<->EVM pointer bridge is used to move tokens the attacker does not own, because pointer registration and resolution (SetERC20NativePointer, SetERC20CW20Pointer, SetERC721CW721Pointer, SetERC1155CW1155Pointer, their WithVersion and Get/Delete forms, pointer_upgrade) or the wasm entry points MsgInternalEVMCall / MsgInternalEVMDelegateCall and the delegate-call whitelist let an attacker register or shadow a pointer for a denom or CW contract they do not control, or make a pointer forward a transfer with the pointee's authority rather than their own.",
    "Critical. Fee and gas accounting lets an attacker execute for free or drain collected fees, because EVMFeeCheckDecorator, CalculatePriority, the gas purchase and refund path, the EVM<->Sei gas normalizer, per-tx coinbase collection and the EndBlock surplus sweep, or AdjustDynamicBaseFeePerGas / GetCurrBaseFeePerGas mis-price a transaction: refunded more than charged, surplus credited twice or to the attacker, or a tx admitted below the enforced base fee and still executed.",
    "Critical. A failed, reverted, or ante-rejected transaction leaves committed state, because Snapshot/RevertToSnapshot, the journal entries in x/evm/state, the cached-store write-through, the deferred-info transient store, or the failed-receipt and bloom aggregation in EndBlock commit a balance, nonce, code, or storage change the EVM rolled back - so an attacker crafts a transaction that reverts yet keeps the value it moved, or writes a receipt that reports an outcome execution never produced.",
    "High. A crafted transaction any account can submit makes honest nodes diverge into a permanent chain split, because parallel execution loses a conflict: readset validation and estimate handling in sei-cosmos/store/multiversion (mvkv, data_structures, the iterators), the deferred bank cache, the whitelisted-store layering in store/whitelist, map or pointer iteration order, precompile-version gating by block height, or an upgrade/fork boundary in app/upgrades causes two validators executing the same block to reach different state or receipts.",
    "High. A single transaction or message halts or stalls at least a third of validators, because a deterministic panic or an unrecovered error reached from DeliverTx, a precompile, the wasm bridge, EndBlock (storage_cleanup, deferred aggregation, epoch hooks, oracle tally), or the recovery middleware crashes every node executing it - or its cost makes block production exceed 2.5 seconds - and the transaction re-enters every proposed block so the network cannot recover on its own.",
    "High. A crafted JSON-RPC or gRPC request crashes a default-configuration RPC node, because an eth_*, debug_*, sei_* or subscription handler in evmrpc panics on attacker-controlled parameters: block/receipt encoding in EncodeTmBlock or the other header encoders, filter range and bloom handling, tracer config validation in validateTraceTracer, trace decoding, state overrides in simulate, the worker pool and watermark manager, or the request limiter, rate-limit middleware and sei-legacy gate mishandling a body, batch, or subscription.",
    "Critical/High blind spot. An unprivileged sender, contract deployer, CosmWasm caller, precompile caller, pointer user, or public-RPC client abuses an assumption Sei never wrote down: a value validated in CheckTx and trusted as validated in DeliverTx, an address association or pointer re-read after the check that authorized it, a rule enforced for one EVM tx type but not its EIP-2930/1559/7702 twin or its MsgInternalEVMCall equivalent, a synthetic CW->EVM or EVM->CW receipt path that skips a check the direct path performs, state carried across transaction, block, upgrade, or cache boundaries proven safe only inside one of them, or an error path that commits partial state - yielding fund loss, permanent freezing, a chain split, or node crashes.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one sei-chain target.

    ```
    target_file format:
    "'File Name: x/evm/state/balance.go -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact sei-chain target:

    {target_file}

    Project focus:
    sei-chain is the Sei L1: a Cosmos SDK / Tendermint chain with a native EVM. Focus only on what an ordinary account reaches: EVM transactions and the EVM ante pipeline, Sei<->EVM address association and cast addresses, the usei/wei StateDB bridge, fee and EIP-1559 base-fee accounting, deferred EndBlock work and receipts, Cosmos precompiles, CW<->EVM pointer contracts and the wasm bridge, bank/tokenfactory token authority, oracle vote admission, OCC parallel execution and store layering, and the public EVM JSON-RPC surface.

    Rules:
    * Treat `File Name:` as the exact file/package.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Go symbols (function, method, struct, field, constant) when possible.
    * Attacker is unprivileged only: any key holder who funds an account and submits signed EVM or Cosmos transactions of any type, deploys and calls contracts, calls precompiles from a contract, instantiates and executes CosmWasm contracts, registers pointers, creates tokenfactory denoms, or sends public JSON-RPC/gRPC requests. They sign only for their own keys.
    * Attacker is NOT a validator, proposer, governance voter, node operator, host or DB owner, or holder of another user's key. Never assume a malicious peer, malicious node, malicious validator, p2p/gossip/statesync/consensus-message attacker, leaked key, compromised host, non-default configuration, 51% or economic-governance attack, or social engineering.
    * Out of scope, never ask about: p2p and peer handling, statesync, block propagation, Tendermint consensus message handling, network-level DoS, the `giga/` package and FlatKV storage, oracle price feeds supplied by third parties, Sybil and centralization risks, CLI flags, metrics, dependencies.
    * Ignore test files, mocks, benchmarks, docs, generated code (`*.pb.go`, `*.pb.gw.go`), and TOML/config-only findings.
    * Every question must describe a real transaction, contract call, wasm message, or RPC request an attacker actually submits through a valid entrypoint. No generic unbounded-allocation, memory-growth, cache-size, or resource-exhaustion speculation; no "what if the input is huge" without a concrete submitted payload and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target direct loss or permanent freezing of funds, supply inflation or destruction, unauthorized transfers via precompiles or pointers, fee or refund abuse, state divergence causing a permanent chain split, validator halt, or a crash of default-configuration RPC nodes.
    * Every question must be testable by a Go unit test, an x/evm keeper or StateDB test, a precompile test, an ante-handler test, an OCC/multiversion store test, or an evmrpc handler test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization is exact: an account or denom is debited only when the caller proves control of it, for the exact address the EVM or Cosmos layer resolved.
    * Value is conserved: usei in equals usei out across the wei boundary, precompile payments, refunds, and reverts; no path mints or burns outside the mint module.
    * Reverts are total: an ante failure or EVM revert leaves no committed balance, nonce, code, storage, or receipt change beyond what the protocol defines.
    * Determinism holds: every honest validator executing the same block reaches the same state, receipts, and gas, regardless of parallel-execution scheduling, cache state, tracing, or iteration order.
    * Pricing is honest: CheckTx admission price, intrinsic gas, gas normalization, and base fee match what DeliverTx and the fee sweep enforce.
    * Liveness holds: no submittable payload makes validators panic, stall a block past 2.5 seconds, or crash a default-configuration RPC node.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete transaction, contract call, wasm message, or RPC request: type, fields, arguments);
    3. preconditions (accounts, balance, contracts, denoms, and associations the attacker owns);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: Go unit/keeper/StateDB/precompile/ante/OCC/evmrpc test PARAMETERS and assert AUTHORIZATION_EXACTNESS, VALUE_CONSERVATION, TOTAL_REVERT, DETERMINISM, HONEST_PRICING, or LIVENESS.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused sei-chain exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any key holder who funds an account, submits signed EVM or Cosmos transactions of any type, deploys and calls contracts, calls precompiles, instantiates and executes CosmWasm contracts, registers pointers, creates tokenfactory denoms, or sends public JSON-RPC/gRPC requests. No validator, proposer, governance, operator, host, DB, or foreign-key access.
- Reject malicious-peer, malicious-node, malicious-validator, p2p/gossip/statesync/consensus-message, network-DoS, leaked-key, host-level, 51%/economic-governance, Sybil, centralization, and misconfiguration-only paths.
- Reject `giga/` and FlatKV storage, third-party oracle data quality, CLI, metrics, dependency-only, and test/mock/docs/generated/config-only findings.
- Reject generic unbounded-allocation or resource-growth claims with no concrete submitted payload and no broken invariant.
- Sei pays Critical, High and Medium. Focus on real chain impact: direct loss of funds, permanent freezing of funds with no on-chain remediation, unauthorized transfer/mint/burn, unintended permanent chain split, halt of >=1/3 of validators, crash of default-configuration RPC nodes, deterministic unintended contract execution, or block production delay beyond 2.5 seconds.

## Validate
- Trace the exact reachable path from the attacker's transaction, contract call, wasm message, or RPC request into the affected function.
- Check whether ante validation (router, preprocess, sig, basic, fee, gas), precompile caller and payment checks, pointer registration rules, OCC conflict detection, panic recovery, or the RPC request/rate limiters already stop it.
- Confirm the path is reachable on current mainnet chain config, default `app.toml`, and the active upgrade/fork height.
- Accept only concrete fund loss or freezing, supply divergence, unauthorized transfer, chain split, validator halt, RPC-node crash, or a lasting inability to process valid transactions.
- Require exact file/function support and a reproducible Go unit, keeper/StateDB, precompile, ante, OCC, or evmrpc PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (direct loss or permanent freezing of funds >= $5k), High (chain split requiring hard fork, halt of >=1/3 validators, RPC-node crash), or Medium (loss or freezing < $5k, deterministic unintended contract execution, block delay > 2.5s)]

### Likelihood Explanation
[Preconditions, accounts and balance needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Go unit/keeper/StateDB/precompile/ante/OCC/evmrpc test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for sei-chain.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unprivileged transaction sender, contract deployer, precompile caller, CosmWasm user, pointer user, tokenfactory denom creator, or public-RPC client can reach: EVM transactions and the EVM ante pipeline, Sei<->EVM address association, the usei/wei StateDB bridge, fee and base-fee accounting, deferred EndBlock work and receipts, Cosmos precompiles, CW<->EVM pointers and the wasm bridge, bank/tokenfactory authority, oracle vote admission, OCC parallel execution and store layering, or the public EVM JSON-RPC surface.
- Reject malicious-peer, malicious-node, malicious-validator, p2p/statesync/consensus-message, network-DoS, leaked-key, operator-only, governance/51%, Sybil, `giga/` and FlatKV, third-party oracle data, CLI, mocked-only paths, dependency-only bugs, and no-impact analogs.
- Medium, High and Critical only; no low, informational, or resource-only analogs.

## Validate
- Map the bug class to the strongest reachable sei-chain path from a single submitted transaction, contract call, wasm message, or RPC request.
- Prove root cause with exact file/function support.
- Accept only concrete fund loss or permanent freezing, supply inflation or destruction, unauthorized transfer via precompile or pointer, fee or refund abuse, permanent chain split, validator halt, block delay beyond 2.5 seconds, or a crash of default-configuration RPC nodes.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for sei-chain security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Sei pays Critical, High and Medium; reject low, informational, best-practice, and resource-only reports.
- Reject malicious-peer, malicious-node, malicious-validator, p2p/gossip/statesync/consensus-message, network-level DoS, 51%/economic-governance, Sybil, centralization, lack-of-liquidity, stablecoin-depeg, SSL/cert, CLI, metrics, dependency-only, docs/style, generated-file, and test/mock/config-only issues.
- Reject `giga/` package findings and FlatKV storage findings; both are excluded from the program.
- Reject if the exploit needs validator, proposer, governance, operator, host, database, or privileged-address access, another user's key, victim social engineering, a non-default configuration, or anything outside what an unprivileged account holder can put in a transaction, contract call, wasm message, or public RPC/gRPC request.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged transaction sender, contract deployer, precompile caller, CosmWasm user, pointer user, tokenfactory denom creator, or public-RPC client, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - direct loss of funds of USD $5,000 or more (unauthorized transfer, minting, or burning), or permanent freezing of USD $5,000 or more with no on-chain remediation path; High - crash or halt of >=1/3 of validators without direct network access to them, unintended permanent chain split requiring a hard fork, or crash of default-configuration RPC nodes without direct network access; Medium - direct loss or permanent freezing under USD $5,000, deterministic unintended smart contract execution with no funds at risk, crash of default-configuration RPC nodes via direct unauthenticated RPC/gRPC access, crash or halt of 10-33% of validators via crafted non-brute-force messages, a single proposer freezing blocks for >=10 minutes, or block production delay exceeding 2.5 seconds from crafted transactions.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken authorization, value-conservation, total-revert, determinism, pricing, or liveness invariant.
3. Reachable exploit path: preconditions (attacker-owned accounts, balance, contracts, denoms, associations) -> submitted transaction, contract call, wasm message, or RPC request -> trigger -> bad result.
4. Existing ante validation, precompile caller and payment checks, pointer registration rules, OCC conflict detection, panic recovery, and RPC request/rate limiters reviewed and shown insufficient.
5. Concrete in-scope Critical/High/Medium impact with realistic likelihood, and a funds figure where the tier depends on one.
6. Reproducible proof path: Go unit PoC, keeper/StateDB test, precompile test, ante test, OCC/multiversion test, evmrpc handler test, or exact steps against a local network.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary account holder trigger this with a transaction, contract call, wasm message, or public RPC request, without validator, governance, operator, host, or foreign-key access?
- Does the code actually behave as claimed under current mainnet chain config, default app.toml, and the active upgrade height?
- Is the impact caused by this code, not by a malicious peer, validator, or dependency?
- Is the loss, freezing, split, halt, or crash concrete rather than hypothetical?
- Would an Immunefi triager on the Sei program accept the proof-of-concept?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and Sei bounty impact category]

## Likelihood Explanation
[Attacker capability, accounts and balance required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Go unit/keeper/StateDB/precompile/ante/OCC/evmrpc test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
