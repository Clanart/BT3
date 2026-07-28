import json
import os

MAX_REPO = 25
SOURCE_REPO = 'pushchain/push-chain-evm'
REPO_NAME = 'push-chain-evm'
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
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
    'ante/ante.go',
    'ante/cosmos.go',
    'ante/cosmos/authz.go',
    'ante/cosmos/eip712.go',
    'ante/cosmos/min_gas_price.go',
    'ante/cosmos/reject_msgs.go',
    'ante/evm.go',
    'ante/evm/01_setup_ctx.go',
    'ante/evm/02_mempool_fee.go',
    'ante/evm/03_global_fee.go',
    'ante/evm/04_validate.go',
    'ante/evm/05_signature_verification.go',
    'ante/evm/06_account_verification.go',
    'ante/evm/07_can_transfer.go',
    'ante/evm/08_gas_consume.go',
    'ante/evm/09_increment_sequence.go',
    'ante/evm/10_gas_wanted.go',
    'ante/evm/11_emit_event.go',
    'ante/evm/fee_checker.go',
    'ante/evm/mono_decorator.go',
    'ante/evm/utils.go',
    'ante/interfaces/cosmos.go',
    'ante/interfaces/evm.go',
    'ante/sigverify.go',
    'ante/tx_listener.go',
    'ante/types/block.go',
    'ante/types/dynamic_fee.go',
    'config/chain_id.go',
    'config/config.go',
    'config/constants.go',
    'config/evmd_config.go',
    'config/server_app_options.go',
    'crypto/codec/amino.go',
    'crypto/codec/codec.go',
    'crypto/ethsecp256k1/ethsecp256k1.go',
    'crypto/hd/algorithm.go',
    'crypto/hd/hdpath.go',
    'crypto/keyring/options.go',
    'crypto/secp256r1/verify.go',
    'eips/eips.go',
    'encoding/address/address_codec.go',
    'encoding/codec/codec.go',
    'encoding/config.go',
    'ethereum/eip712/codec.go',
    'ethereum/eip712/domain.go',
    'ethereum/eip712/eip712.go',
    'ethereum/eip712/eip712_legacy.go',
    'ethereum/eip712/encoding.go',
    'ethereum/eip712/encoding_legacy.go',
    'ethereum/eip712/message.go',
    'ethereum/eip712/preprocess.go',
    'ethereum/eip712/types.go',
    'evmd/app.go',
    'evmd/export.go',
    'evmd/genesis.go',
    'evmd/interfaces.go',
    'evmd/mempool.go',
    'evmd/upgrades.go',
    'ibc/errors.go',
    'ibc/interfaces.go',
    'ibc/module.go',
    'ibc/utils.go',
    'mempool/blockchain.go',
    'mempool/checktx/check_tx.go',
    'mempool/errors.go',
    'mempool/interface.go',
    'mempool/iterator.go',
    'mempool/mempool.go',
    'mempool/miner/ordering.go',
    'mempool/signer.go',
    'mempool/txpool/errors.go',
    'mempool/txpool/legacypool/legacypool.go',
    'mempool/txpool/legacypool/list.go',
    'mempool/txpool/legacypool/noncer.go',
    'mempool/txpool/legacypool/reset_production.go',
    'mempool/txpool/reserver.go',
    'mempool/txpool/subpool.go',
    'mempool/txpool/txpool.go',
    'mempool/txpool/validation.go',
    'precompiles/bank/bank.go',
    'precompiles/bank/query.go',
    'precompiles/bank/types.go',
    'precompiles/bech32/bech32.go',
    'precompiles/bech32/methods.go',
    'precompiles/callbacks/abi.go',
    'precompiles/callbacks/callbacks.go',
    'precompiles/common/abi.go',
    'precompiles/common/balance_handler.go',
    'precompiles/common/errors.go',
    'precompiles/common/interfaces.go',
    'precompiles/common/precompile.go',
    'precompiles/common/revert.go',
    'precompiles/common/types.go',
    'precompiles/common/utils.go',
    'precompiles/distribution/distribution.go',
    'precompiles/distribution/errors.go',
    'precompiles/distribution/events.go',
    'precompiles/distribution/query.go',
    'precompiles/distribution/tx.go',
    'precompiles/distribution/types.go',
    'precompiles/erc20/approve.go',
    'precompiles/erc20/bank_msg_server_wrapper.go',
    'precompiles/erc20/erc20.go',
    'precompiles/erc20/errors.go',
    'precompiles/erc20/events.go',
    'precompiles/erc20/interfaces.go',
    'precompiles/erc20/query.go',
    'precompiles/erc20/tx.go',
    'precompiles/erc20/types.go',
    'precompiles/gov/errors.go',
    'precompiles/gov/events.go',
    'precompiles/gov/gov.go',
    'precompiles/gov/query.go',
    'precompiles/gov/tx.go',
    'precompiles/gov/types.go',
    'precompiles/ics20/errors.go',
    'precompiles/ics20/events.go',
    'precompiles/ics20/ics20.go',
    'precompiles/ics20/query.go',
    'precompiles/ics20/tx.go',
    'precompiles/ics20/types.go',
    'precompiles/p256/p256.go',
    'precompiles/slashing/events.go',
    'precompiles/slashing/query.go',
    'precompiles/slashing/slashing.go',
    'precompiles/slashing/tx.go',
    'precompiles/slashing/types.go',
    'precompiles/staking/errors.go',
    'precompiles/staking/events.go',
    'precompiles/staking/query.go',
    'precompiles/staking/staking.go',
    'precompiles/staking/tx.go',
    'precompiles/staking/types.go',
    'precompiles/types/defaults.go',
    'precompiles/types/static_precompiles.go',
    'precompiles/werc20/events.go',
    'precompiles/werc20/interfaces.go',
    'precompiles/werc20/tx.go',
    'precompiles/werc20/werc20.go',
    'rpc/apis.go',
    'rpc/backend/account_info.go',
    'rpc/backend/backend.go',
    'rpc/backend/blocks.go',
    'rpc/backend/call_tx.go',
    'rpc/backend/chain_info.go',
    'rpc/backend/comet.go',
    'rpc/backend/comet_to_eth.go',
    'rpc/backend/filters.go',
    'rpc/backend/headers.go',
    'rpc/backend/node_info.go',
    'rpc/backend/sign_tx.go',
    'rpc/backend/tracing.go',
    'rpc/backend/tx_info.go',
    'rpc/backend/tx_pool.go',
    'rpc/backend/utils.go',
    'rpc/ethereum/pubsub/pubsub.go',
    'rpc/namespaces/ethereum/debug/api.go',
    'rpc/namespaces/ethereum/debug/trace.go',
    'rpc/namespaces/ethereum/debug/trace_fallback.go',
    'rpc/namespaces/ethereum/debug/utils.go',
    'rpc/namespaces/ethereum/eth/api.go',
    'rpc/namespaces/ethereum/eth/filters/api.go',
    'rpc/namespaces/ethereum/eth/filters/filters.go',
    'rpc/namespaces/ethereum/eth/filters/utils.go',
    'rpc/namespaces/ethereum/miner/api.go',
    'rpc/namespaces/ethereum/miner/unsupported.go',
    'rpc/namespaces/ethereum/net/api.go',
    'rpc/namespaces/ethereum/personal/api.go',
    'rpc/namespaces/ethereum/txpool/api.go',
    'rpc/namespaces/ethereum/web3/api.go',
    'rpc/stream/cond.go',
    'rpc/stream/queue.go',
    'rpc/stream/rpc.go',
    'rpc/stream/stream.go',
    'rpc/types/addrlock.go',
    'rpc/types/block.go',
    'rpc/types/errors.go',
    'rpc/types/events.go',
    'rpc/types/protocol.go',
    'rpc/types/query_client.go',
    'rpc/types/types.go',
    'rpc/types/utils.go',
    'rpc/websockets.go',
    'server/config/config.go',
    'server/config/migration/migration.go',
    'server/config/opendb.go',
    'server/config/opendb_rocksdb.go',
    'server/config/toml.go',
    'server/flags/flags.go',
    'server/indexer_cmd.go',
    'server/indexer_service.go',
    'server/json_rpc.go',
    'server/log_handler.go',
    'server/start.go',
    'server/types/indexer.go',
    'server/util.go',
    'utils/eth/eth.go',
    'utils/int.go',
    'utils/power.go',
    'utils/utils.go',
    'utils/validation.go',
    'x/erc20/genesis.go',
    'x/erc20/ibc_middleware.go',
    'x/erc20/keeper/allowance.go',
    'x/erc20/keeper/dynamic_precompiles.go',
    'x/erc20/keeper/evm.go',
    'x/erc20/keeper/grpc_query.go',
    'x/erc20/keeper/ibc_callbacks.go',
    'x/erc20/keeper/keeper.go',
    'x/erc20/keeper/mint.go',
    'x/erc20/keeper/msg_server.go',
    'x/erc20/keeper/params.go',
    'x/erc20/keeper/precompiles.go',
    'x/erc20/keeper/proposals.go',
    'x/erc20/keeper/token_pairs.go',
    'x/erc20/keeper/util.go',
    'x/erc20/module.go',
    'x/erc20/types/allowance.go',
    'x/erc20/types/codec.go',
    'x/erc20/types/constants.go',
    'x/erc20/types/errors.go',
    'x/erc20/types/events.go',
    'x/erc20/types/evm.go',
    'x/erc20/types/genesis.go',
    'x/erc20/types/interfaces.go',
    'x/erc20/types/keys.go',
    'x/erc20/types/msg.go',
    'x/erc20/types/params.go',
    'x/erc20/types/proposal.go',
    'x/erc20/types/token_pair.go',
    'x/erc20/types/utils.go',
    'x/erc20/v2/ibc_middleware.go',
    'x/feemarket/genesis.go',
    'x/feemarket/keeper/abci.go',
    'x/feemarket/keeper/eip1559.go',
    'x/feemarket/keeper/grpc_query.go',
    'x/feemarket/keeper/keeper.go',
    'x/feemarket/keeper/msg_server.go',
    'x/feemarket/keeper/params.go',
    'x/feemarket/module.go',
    'x/feemarket/types/codec.go',
    'x/feemarket/types/events.go',
    'x/feemarket/types/genesis.go',
    'x/feemarket/types/keys.go',
    'x/feemarket/types/msg.go',
    'x/feemarket/types/params.go',
    'x/feemarket/types/utils.go',
    'x/ibc/callbacks/keeper/keeper.go',
    'x/ibc/callbacks/types/errors.go',
    'x/ibc/callbacks/types/expected_keepers.go',
    'x/ibc/callbacks/types/keys.go',
    'x/ibc/callbacks/types/marshal.go',
    'x/ibc/transfer/ibc_module.go',
    'x/ibc/transfer/keeper/keeper.go',
    'x/ibc/transfer/keeper/msg_server.go',
    'x/ibc/transfer/module.go',
    'x/ibc/transfer/types/channels.go',
    'x/ibc/transfer/types/interfaces.go',
    'x/ibc/transfer/v2/ibc_module.go',
    'x/vm/ante/ctx.go',
    'x/vm/genesis.go',
    'x/vm/keeper/abci.go',
    'x/vm/keeper/block_proposer.go',
    'x/vm/keeper/call_evm.go',
    'x/vm/keeper/coin_info.go',
    'x/vm/keeper/config.go',
    'x/vm/keeper/fees.go',
    'x/vm/keeper/gas.go',
    'x/vm/keeper/grpc_query.go',
    'x/vm/keeper/hooks.go',
    'x/vm/keeper/keeper.go',
    'x/vm/keeper/migrator.go',
    'x/vm/keeper/msg_server.go',
    'x/vm/keeper/params.go',
    'x/vm/keeper/precompiles.go',
    'x/vm/keeper/preinstalls.go',
    'x/vm/keeper/state_transition.go',
    'x/vm/keeper/statedb.go',
    'x/vm/keeper/static_precompiles.go',
    'x/vm/keeper/utils.go',
    'x/vm/migrations/v2/migrate_params.go',
    'x/vm/module.go',
    'x/vm/statedb/access_list.go',
    'x/vm/statedb/config.go',
    'x/vm/statedb/interfaces.go',
    'x/vm/statedb/journal.go',
    'x/vm/statedb/state_object.go',
    'x/vm/statedb/statedb.go',
    'x/vm/statedb/transient_storage.go',
    'x/vm/store/snapshotkv/store.go',
    'x/vm/store/snapshotmulti/store.go',
    'x/vm/store/types/store.go',
    'x/vm/types/activators.go',
    'x/vm/types/call.go',
    'x/vm/types/chain_config.go',
    'x/vm/types/codec.go',
    'x/vm/types/compiled_contract.go',
    'x/vm/types/config.go',
    'x/vm/types/configurator.go',
    'x/vm/types/denom.go',
    'x/vm/types/denom_config.go',
    'x/vm/types/errors.go',
    'x/vm/types/eth.go',
    'x/vm/types/events.go',
    'x/vm/types/gasmeter.go',
    'x/vm/types/genesis.go',
    'x/vm/types/interfaces.go',
    'x/vm/types/key.go',
    'x/vm/types/logs.go',
    'x/vm/types/msg.go',
    'x/vm/types/opcodes_hooks.go',
    'x/vm/types/params.go',
    'x/vm/types/permissions.go',
    'x/vm/types/precompiles.go',
    'x/vm/types/preinstall.go',
    'x/vm/types/query.go',
    'x/vm/types/scaling.go',
    'x/vm/types/storage.go',
    'x/vm/types/tracer.go',
    'x/vm/types/tx.go',
    'x/vm/types/tx_args.go',
    'x/vm/types/tx_types.go',
    'x/vm/types/utils.go',
    'x/vm/wrappers/bank.go',
    'x/vm/wrappers/feemarket.go',
]

target_scopes = [
    'Critical. An unprivileged attacker can mint, burn, duplicate, or resurrect spendable user value by breaking native/EVM/ERC20/IBC accounting, rollback, or supply invariants in x/vm, x/erc20, ante, mempool, or precompile flows.',
    'Critical. An unprivileged attacker can permanently freeze user funds, escrowed assets, contract balances, or transferable value in a way that would require a coordinated upgrade or hard fork to recover.',
    'Critical. An unprivileged attacker can steal, redirect, or irreversibly extract native balances, EVM balances, contract-held funds, or precompile-controlled assets through reachable state-transition, keeper, or precompile logic.',
    'Critical. An unprivileged attacker can break ERC20 or token-pair invariants and cause unauthorized issuance, redemption, duplication, or loss of backing between Cosmos balances and EVM-visible assets.',
    'Critical. An unprivileged attacker can break ICS20 or IBC transfer accounting and steal, duplicate, strand, or permanently desynchronize escrowed assets or cross-chain token representations.',
    'Critical. An unprivileged attacker can trigger chain halt, non-determinism, consensus fork, or AppHash divergence using an ordinary transaction, contract call, ante path, block hook, or public module entrypoint.',
    'Critical. An unprivileged attacker can exploit public EVM execution, nested-call, revert, journal, snapshot, or precompile state handling to reuse balances, bypass burn/escrow semantics, or commit inconsistent state across execution contexts.',
]

CLEMENTINE_ALLOWED_IMPACT_SCOPE = (
    '## Cosmos EVM Allowed Impact Gate\n'
    'Only accept repository-relevant impacts:\n'
    '- Critical unauthorized minting, burning, duplication, resurrection, or irreversible accounting corruption of spendable user value across native balances, EVM balances, ERC20 representations, IBC escrows, or precompile-mediated assets.\n'
    '- Critical permanent freezing, locking, theft, or unauthorized extraction of user funds, contract balances, escrowed assets, staking/distribution value, or token-pair-backed balances.\n'
    '- Critical chain halt, consensus fork, non-determinism, or AppHash divergence that an unprivileged user can trigger through ordinary transaction, contract, precompile, hook, or block-processing flow.\n'
    'Out of scope: anything below Critical severity, including isolated auth bypasses without critical impact, non-critical unauthorized state mutation, deterministic single-node crashes, fee-only issues, tests, mocks, fixtures, scripts, docs-only issues, local tooling, README/config-only concerns, generated files, dependency-only behavior, x/precisebank, privileged keys, malicious validators/relayers/peers/nodes/admins, governance-only assumptions, honest external chain behavior, and style issues.'
)

CLEMENTINE_AUDIT_PIVOTS = (
    '## Smart Audit Pivots\n'
    '- VM state path: x/vm keeper, statedb, journal, snapshot, nested-call, refund, and revert handling must keep balances, storage, logs, and code state consistent across recursive execution.\n'
    '- Asset-representation path: x/erc20, bank, ICS20, staking, distribution, slashing, gov, and werc20 flows must preserve 1:1 accounting between native coins, ERC20 views, escrows, and precompile-visible balances.\n'
    '- Admission path: ante, mempool, txpool, fee market, signature, chain-id, nonce, gas, authz, and permission checks must reject invalid state transitions before execution.\n'
    '- Public surface path: JSON-RPC, filters, tracing, personal APIs, block/receipt derivation, callbacks, and module msg/query entrypoints must not grant extra authority or unbounded work to an unprivileged caller.'
)


def question_generator(target_file: str) -> str:
    """
    Generate security questions for one Cosmos EVM target.
    """

    prompt = f"""
    Generate Cosmos EVM security questions for this exact target file:

    {target_file}

    Project lens:
    Cosmos EVM adds Ethereum execution, JSON-RPC, precompiles, ERC20/IBC integration, fee market, and Cosmos module access to a Cosmos SDK chain. Focus on x/vm execution, ante, mempool, precompiles, x/erc20, x/ibc transfer, feemarket, and JSON-RPC trust boundaries.

    Impact gate:
    {CLEMENTINE_ALLOWED_IMPACT_SCOPE}

    {CLEMENTINE_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file and `Scope:` as the only impact.
    * Assume repo context is accessible; do not ask for code.
    * The attacker is strictly unprivileged. Do not rely on validator, relayer, peer, node, governance, admin, operator, signer, or infrastructure control unless scoped code lets an ordinary external user reach the same effect.
    * Never base a question on a malicious peer or node. Reject privileged-key compromise, deployment mistakes, and off-repo infra failures unless scoped code fails to authenticate, bind, or validate them.
    * Exclude tests, mocks, fixtures, scripts, docs, local tooling, generated files, x/precisebank, config-only issues, fee-only issues, style, and dependency-only behavior.
    * Generate 16 to 22 high-signal questions with non-overlapping root causes.
    * Name the exact corrupted value: native balance, ERC20 balance, allowance, IBC escrow amount, total supply, nonce, gas/accounting field, auth decision, AppHash-relevant state, code hash, storage slot, or receipt/log result.
    * Every question must be testable with a Go unit, integration, state-transition, property, or fuzz-style test.
    * Focus only on Critical outcomes. Do not generate questions whose best-case impact is merely High, Medium, Low, or informational.

    Each question must include target symbol, attacker-controlled input, required state, call path, broken invariant, corrupted value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled INPUT under REQUIRED_STATE reach CALL_PATH and violate VM_OR_ACCOUNTING_INVARIANT, corrupting EXACT_VALUE_AT_RISK with scoped impact SCOPE_IMPACT? Proof idea: write a Go test that drives ENTRYPOINT through the vulnerable state transition and asserts EXPECTED_SAFETY_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Cosmos EVM exploit-question validation prompt.
    """
    return f"""# COSMOS EVM QUESTION REVIEW

## Exploit Question
{question}

## Scope Rules
- Audit only Cosmos EVM production code in this repository.
- Ignore tests, mocks, fixtures, scripts, generated artifacts, local tooling, README/config-only issues, and x/precisebank.
- Do not ask for repo contents or claim files are missing.

## Objective
    Decide whether the question leads to a real Cosmos EVM vulnerability. The attacker must be unprivileged and must enter through ordinary transaction, contract, precompile, module, mempool, ante, or JSON-RPC flows available in scoped code.

    Reject claims needing validator, relayer, peer, node, governance, admin, or leaked-key control. Reject malicious external chain behavior unless scoped validation fails. Prefer #NoVulnerability unless the path proves a Critical unauthorized mint/burn, theft, permanent lock, or consensus/liveness break allowed below.

## Required Impacts
{CLEMENTINE_ALLOWED_IMPACT_SCOPE}

{CLEMENTINE_AUDIT_PIVOTS}

## Method
1. Trace the unprivileged entrypoint.
2. Map it to exact scoped files and functions.
3. Follow the full path through validation, execution, keeper state writes, precompile/module effects, and final balance, supply, or consensus-relevant state.
4. Identify the exact corrupted value and who loses funds, liveness, or determinism.
5. Reject if existing guards preserve the invariant or if impact is immaterial.

## Reject Immediately
- Malicious validator, relayer, peer, node, governance, or admin assumptions without a scoped code bypass.
- Honest IBC counterparty, RPC client, or external chain behavior unless scoped binding/validation is missing.
- Any issue whose strongest supported impact is below Critical.
- View-only mismatches, harmless decoding differences, fee-only issues, logs, style, dependency-only behavior, tests, mocks, fixtures, scripts, generated files, docs-only issues, or x/precisebank findings.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a cross-project analog scan prompt for Cosmos EVM issues.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Search Cosmos EVM execution, ante, mempool, precompile, ERC20, ICS20, IBC transfer, fee market, and JSON-RPC code for a native analog with concrete Critical in-scope impact.

## Required Impacts
{CLEMENTINE_ALLOWED_IMPACT_SCOPE}

{CLEMENTINE_AUDIT_PIVOTS}

Report only if this repository has its own reachable root cause, unprivileged trigger, broken invariant, exact corrupted value, and matching Critical target scope or allowed impact. Reject privileged assumptions, malicious peer/node/relayer setups, external-system-only issues, dependency-only behavior, lower-severity issues, and anything outside the production surface.

## Work Plan
1. Classify the external bug into one Cosmos EVM invariant.
2. Map it to exact scoped files/functions.
3. Trace attacker input through production validation, execution, and state updates.
4. Identify the wrong balance, allowance, supply value, escrow amount, auth decision, consensus-relevant state, or RPC work unit.
5. Reject if existing guards preserve the invariant or the impact is not Critical.

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
    Generate a strict Cosmos EVM validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim against Cosmos EVM production code in this repository.
- Do not invent a stronger claim, change target scope, or upgrade severity without evidence.
- A valid issue must be triggered by an unprivileged external attacker using only capabilities exposed by scoped code.
- Privileged keys, malicious deployment, malicious validators/relayers/peers/nodes, and off-repo infra control are out unless scoped code fails to authenticate, bind, or validate them.
- Reject any claim that needs the attacker to already hold governance, admin, signer, database, or infrastructure privileges.
- Reject tests, mocks, fixtures, scripts, local tooling, docs-only issues, generated-file issues, x/precisebank, fee-only issues, style issues, and dependency-only bugs.
- Reject any issue whose strongest supported impact is below Critical.
- The final impact must match one `target_scopes` item or allowed impact below and identify the exact corrupted value.

## Required Impacts
{CLEMENTINE_ALLOWED_IMPACT_SCOPE}

{CLEMENTINE_AUDIT_PIVOTS}

## Required Checks
1. Exact file/function references in scoped code.
2. Clear broken Cosmos EVM invariant tied to funds, supply, consensus, liveness, or deterministic execution.
3. Reachable exploit path: preconditions -> attacker input -> production call path -> bad value.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: native balance, ERC20 balance, allowance, total supply, IBC escrow amount, nonce, gas/accounting field, auth decision, AppHash-relevant state, code hash, storage slot, or receipt/log output.
6. Reproducible proof path: Go unit, integration, state-transition, property, or fuzz-style test.

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
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
