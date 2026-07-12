import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "crypto-org-chain/ethermint"
# todo: the name of the repository
REPO_NAME = "ethermint"
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
    "ante/cache/antecache.go",
    "ante/cosmos/authz.go",
    "ante/cosmos/eip712.go",
    "ante/cosmos/min_gas_price.go",
    "ante/cosmos/reject_msgs.go",
    "ante/eth.go",
    "ante/evm/fee_checker.go",
    "ante/evm/nativefee.go",
    "ante/interfaces/evm.go",
    "ante/interfaces/setup.go",
    "ante/sigverify.go",
    "appmempool/mempoolclient.go",
    "appmempool/preverify.go",
    "crypto/codec/amino.go",
    "crypto/codec/codec.go",
    "crypto/ethsecp256k1/ethsecp256k1.go",
    "crypto/hd/algorithm.go",
    "encoding/codec/codec.go",
    "encoding/config.go",
    "ethereum/eip712/domain.go",
    "ethereum/eip712/eip712.go",
    "ethereum/eip712/eip712_legacy.go",
    "ethereum/eip712/encoding.go",
    "ethereum/eip712/encoding_legacy.go",
    "ethereum/eip712/message.go",
    "ethereum/eip712/types.go",
    "evmd/ante/ante.go",
    "evmd/ante/evm_handler.go",
    "evmd/ante/handler_options.go",
    "evmd/ante/tx_listener.go",
    "evmd/app.go",
    "evmd/executor.go",
    "evmd/export.go",
    "evmd/genesis.go",
    "evmd/mempool.go",
    "evmd/signer.go",
    "evmd/upgrades.go",
    "indexer/kv_indexer.go",
    "internal/origin/origin.go",
    "proto/ethermint/crypto/v1/ethsecp256k1/keys.proto",
    "proto/ethermint/evm/v1/access_tuple.proto",
    "proto/ethermint/evm/v1/chain_config.proto",
    "proto/ethermint/evm/v1/chain_config_v0.proto",
    "proto/ethermint/evm/v1/events.proto",
    "proto/ethermint/evm/v1/genesis.proto",
    "proto/ethermint/evm/v1/log.proto",
    "proto/ethermint/evm/v1/params.proto",
    "proto/ethermint/evm/v1/params_v0.proto",
    "proto/ethermint/evm/v1/params_v4.proto",
    "proto/ethermint/evm/v1/preinstall.proto",
    "proto/ethermint/evm/v1/query.proto",
    "proto/ethermint/evm/v1/set_code_authorization.proto",
    "proto/ethermint/evm/v1/state.proto",
    "proto/ethermint/evm/v1/trace_config.proto",
    "proto/ethermint/evm/v1/trace_config_v0.proto",
    "proto/ethermint/evm/v1/transaction_logs.proto",
    "proto/ethermint/evm/v1/tx.proto",
    "proto/ethermint/evm/v1/tx_result.proto",
    "proto/ethermint/feemarket/v1/events.proto",
    "proto/ethermint/feemarket/v1/feemarket.proto",
    "proto/ethermint/feemarket/v1/genesis.proto",
    "proto/ethermint/feemarket/v1/query.proto",
    "proto/ethermint/feemarket/v1/tx.proto",
    "proto/ethermint/types/v1/account.proto",
    "proto/ethermint/types/v1/dynamic_fee.proto",
    "proto/ethermint/types/v1/indexer.proto",
    "proto/ethermint/types/v1/web3.proto",
    "rpc/apis.go",
    "rpc/backend/account_info.go",
    "rpc/backend/backend.go",
    "rpc/backend/blocks.go",
    "rpc/backend/call_tx.go",
    "rpc/backend/chain_info.go",
    "rpc/backend/filters.go",
    "rpc/backend/node_info.go",
    "rpc/backend/sign_tx.go",
    "rpc/backend/simulate.go",
    "rpc/backend/tracing.go",
    "rpc/backend/tx_info.go",
    "rpc/backend/utils.go",
    "rpc/ethereum/pubsub/pubsub.go",
    "rpc/namespaces/ethereum/debug/api.go",
    "rpc/namespaces/ethereum/debug/trace.go",
    "rpc/namespaces/ethereum/debug/trace_fallback.go",
    "rpc/namespaces/ethereum/debug/utils.go",
    "rpc/namespaces/ethereum/eth/api.go",
    "rpc/namespaces/ethereum/eth/filters/api.go",
    "rpc/namespaces/ethereum/eth/filters/filters.go",
    "rpc/namespaces/ethereum/eth/filters/utils.go",
    "rpc/namespaces/ethereum/net/api.go",
    "rpc/namespaces/ethereum/personal/api.go",
    "rpc/namespaces/ethereum/txpool/api.go",
    "rpc/namespaces/ethereum/web3/api.go",
    "rpc/stream/cond.go",
    "rpc/stream/queue.go",
    "rpc/stream/rpc.go",
    "rpc/stream/stream.go",
    "rpc/types/addrlock.go",
    "rpc/types/block.go",
    "rpc/types/events.go",
    "rpc/types/query_client.go",
    "rpc/types/simulate.go",
    "rpc/types/simulate_errors.go",
    "rpc/types/simulate_tracer.go",
    "rpc/types/types.go",
    "rpc/types/utils.go",
    "rpc/websockets.go",
    "server/config/config.go",
    "server/config/toml.go",
    "server/flags/flags.go",
    "server/indexer_cmd.go",
    "server/indexer_service.go",
    "server/json_rpc.go",
    "server/log_handler.go",
    "server/start.go",
    "server/util.go",
    "types/account.go",
    "types/block.go",
    "types/chain_id.go",
    "types/codec.go",
    "types/coin.go",
    "types/dynamic_fee.go",
    "types/encoding.go",
    "types/errors.go",
    "types/gasmeter.go",
    "types/hdpath.go",
    "types/indexer.go",
    "types/int.go",
    "types/protocol.go",
    "types/validation.go",
    "x/evm/client/cli/query.go",
    "x/evm/client/cli/tx.go",
    "x/evm/client/cli/utils.go",
    "x/evm/genesis.go",
    "x/evm/keeper/abci.go",
    "x/evm/keeper/bloom.go",
    "x/evm/keeper/config.go",
    "x/evm/keeper/gas.go",
    "x/evm/keeper/grpc_query.go",
    "x/evm/keeper/hooks.go",
    "x/evm/keeper/keeper.go",
    "x/evm/keeper/migrations.go",
    "x/evm/keeper/msg_server.go",
    "x/evm/keeper/params.go",
    "x/evm/keeper/set_code_authorizations.go",
    "x/evm/keeper/simulate.go",
    "x/evm/keeper/state_transition.go",
    "x/evm/keeper/statedb.go",
    "x/evm/keeper/utils.go",
    "x/evm/migrations/v0/types/chain_config.go",
    "x/evm/migrations/v0/types/params.go",
    "x/evm/migrations/v0/types/params_legacy.go",
    "x/evm/migrations/v4/migrate.go",
    "x/evm/migrations/v4/types/params.go",
    "x/evm/migrations/v5/migrate.go",
    "x/evm/migrations/v6/migrate.go",
    "x/evm/migrations/v7/migrate.go",
    "x/evm/migrations/v8/migrate.go",
    "x/evm/module.go",
    "x/evm/simulation/decoder.go",
    "x/evm/simulation/genesis.go",
    "x/evm/simulation/operations.go",
    "x/evm/statedb/access_list.go",
    "x/evm/statedb/config.go",
    "x/evm/statedb/interfaces.go",
    "x/evm/statedb/journal.go",
    "x/evm/statedb/native.go",
    "x/evm/statedb/state_object.go",
    "x/evm/statedb/statedb.go",
    "x/evm/statedb/statedb_hooked.go",
    "x/evm/statedb/transient_storage.go",
    "x/evm/types/access_list.go",
    "x/evm/types/access_list_tx.go",
    "x/evm/types/auth_list.go",
    "x/evm/types/chain_config.go",
    "x/evm/types/codec.go",
    "x/evm/types/compiled_contract.go",
    "x/evm/types/dynamic_fee_tx.go",
    "x/evm/types/errors.go",
    "x/evm/types/eth.go",
    "x/evm/types/events.go",
    "x/evm/types/evm_result.go",
    "x/evm/types/genesis.go",
    "x/evm/types/interfaces.go",
    "x/evm/types/key.go",
    "x/evm/types/legacy_tx.go",
    "x/evm/types/logs.go",
    "x/evm/types/msg.go",
    "x/evm/types/params.go",
    "x/evm/types/preinstall.go",
    "x/evm/types/query.go",
    "x/evm/types/response.go",
    "x/evm/types/set_code_tx.go",
    "x/evm/types/storage.go",
    "x/evm/types/tracer.go",
    "x/evm/types/tx.go",
    "x/evm/types/tx_args.go",
    "x/evm/types/tx_data.go",
    "x/evm/types/utils.go",
    "x/feemarket/client/cli/query.go",
    "x/feemarket/genesis.go",
    "x/feemarket/keeper/abci.go",
    "x/feemarket/keeper/eip1559.go",
    "x/feemarket/keeper/grpc_query.go",
    "x/feemarket/keeper/keeper.go",
    "x/feemarket/keeper/migrations.go",
    "x/feemarket/keeper/msg_server.go",
    "x/feemarket/keeper/params.go",
    "x/feemarket/migrations/v4/migrate.go",
    "x/feemarket/migrations/v4/types/params.go",
    "x/feemarket/module.go",
    "x/feemarket/simulation/genesis.go",
    "x/feemarket/types/codec.go",
    "x/feemarket/types/events.go",
    "x/feemarket/types/genesis.go",
    "x/feemarket/types/interfaces.go",
    "x/feemarket/types/keys.go",
    "x/feemarket/types/msg.go",
    "x/feemarket/types/params.go",
]

target_scopes = [
    "Critical. Unauthorized theft, mint, burn bypass, or balance transfer of EVM-denom or Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic",
    "Critical. Valid unprivileged transaction, RPC submission, or block-processing path can halt the chain, corrupt committed state, or cause deterministic validator consensus failure",
    "High. Ethereum transaction, EIP-155/EIP-712/EIP-7702 authorization, nonce, chain-id, or signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation",
    "High. EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted",
    "High. Public JSON-RPC, gRPC, simulation, tracing, receipt/log, or indexer path feeds incorrect consensus-critical data into transaction execution or exposes a reachable route to the impacts above",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one Ethermint production target.

    ```
    target_file format:
    "'File Name: x/evm/keeper/state_transition.go -> Scope: Critical. Unauthorized theft, mint, burn bypass, or balance transfer of EVM-denom or Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact Ethermint target:

    {target_file}

    Project context:
    Ethermint is a Cosmos SDK application/library that executes Ethereum transactions on CometBFT consensus. Security-sensitive areas include EVM tx decoding/signing, ante checks, mempool/preverify/proposal handling, EIP-1559 fee market, EVM stateDB, bank balance bridging, native action hooks, EIP-712 Cosmos signing, EIP-7702 set-code authorization, JSON-RPC transaction submission/simulation/tracing, block/receipt/log/indexer data, genesis and migrations.

    Core invariants:
    * Only valid Ethereum/Cosmos transactions with correct signer, nonce, chain-id, fees, gas, and authorization may commit state.
    * EVM stateDB, bank keeper balances, gas refunds, fee market updates, logs, receipts, and account/code/storage changes must stay deterministic and internally consistent.
    * Public RPC, gRPC, simulation, tracing, and mempool paths must not create a route to invalid committed execution, replay, forged authorization, consensus divergence, or fund loss.
    * Migrations/genesis must preserve balances, params, chain config, nonces, code, storage, fee market state, and EVM compatibility assumptions.

    Rules:
    * Treat `File Name:` as the exact file/module and `Scope:` as the ONLY accepted impact.
    * Assume full repo context is accessible. Do not ask for code or say files are missing.
    * Attacker is unprivileged: external account, contract caller, tx/RPC submitter, mempool participant, block proposer using valid public interfaces, or user controlling tx/call/simulation inputs.
    * Do not rely on malicious validators, governance, privileged keepers, leaked keys, compromised nodes, chain reorgs, dependency compromise, social engineering, local config mistakes, or network-level DoS only.
    * Ignore tests, mocks, docs, generated pb.go, scripts, local tooling, and issues with only informational/low/medium impact.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, fuzz, accounting, replay, consensus, or cross-module questions.
    * Every question must be testable by PoC, unit test, fuzz test, invariant test, differential test, or local integration test.
    * Avoid generic checklist questions and repeated root causes.

    High-value attack surfaces:
    * Tx path: RPC SendRawTransaction/SendTransaction -> tx decoding -> ante/sigverify/fees -> ApplyTransaction/ApplyMessage -> stateDB commit.
    * Authorization/replay: EIP-155, EIP-712, EIP-7702, nonce handling, chain-id/domain separation, authz, and signer/account conversion.
    * Accounting: bank/EVM balance sync, gas/refunds, base fee, priority fee, native fee, selfdestruct, preinstall/native actions, and fee market BeginBlock/EndBlock.
    * Determinism: mempool/proposal handlers, block context/header fields, state snapshots/reverts, migrations, genesis, logs/receipts/bloom, indexer, simulation, tracing.

    Allowed impacts only:
    * Critical unauthorized fund theft/mint/burn bypass/balance transfer.
    * Critical chain halt, committed state corruption, or deterministic consensus failure from a valid unprivileged path.
    * High signature/replay/authorization bypass.
    * High invalid tx commit or user fund/fee mis-accounting.
    * High public RPC/query/simulation route to one of those impacts.

    Each question must include:
    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: fuzz/state-test PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Ethermint exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

Main Focus should be on production Ethermint files from `scope_files`, especially:
- x/evm/keeper, x/evm/statedb, x/evm/types, x/evm/migrations
- x/feemarket/keeper and x/feemarket/types
- ante, evmd/ante, appmempool
- rpc, server JSON-RPC, indexer
- ethereum/eip712, crypto, encoding, types, proto sources
Issues outside those production files are out of scope unless required as direct supporting context.

## Scope Rules
- Audit only production Ethermint code.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, generated pb.go, scripts, configs, local fixtures, vendored libraries, and developer tooling as audited targets.

## Objective
Decide whether the question leads to a real, reachable Ethermint vulnerability.
The attacker must be unprivileged and enter through public tx submission, contract execution, RPC/gRPC query or simulation inputs, mempool/proposal paths, or valid chain data.
The impact must match one allowed Critical/High Ethermint impact below.
Prefer #NoVulnerability unless the path is concrete, local-testable, and bounty-grade.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unauthorized theft, mint, burn bypass, or balance transfer of EVM-denom or Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic.
- Critical. Valid unprivileged transaction, RPC submission, or block-processing path can halt the chain, corrupt committed state, or cause deterministic validator consensus failure.
- High. Ethereum transaction, EIP-155/EIP-712/EIP-7702 authorization, nonce, chain-id, or signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation.
- High. EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted.
- High. Public JSON-RPC, gRPC, simulation, tracing, receipt/log, or indexer path feeds incorrect consensus-critical data into transaction execution or exposes a reachable route to the impacts above.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production Ethermint files/functions.
3. Check the relevant guard: signature, nonce, chain-id, fee/gas, ante, stateDB snapshot/revert/commit, bank balance, fee market, RPC input, simulation, migration, or consensus determinism.
4. Decide whether the questioned invariant can actually break under intended deployment.
5. Prove root cause with file/function/line references.
6. Confirm realistic likelihood and exact scoped impact.
7. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires malicious validators, governance, privileged roles, leaked keys, compromised RPC nodes, host compromise, dependency compromise, chain reorgs, phishing, victim mistakes, or network-level DoS only.
- Only affects tests, docs, configs, scripts, mocks, generated code, local fixtures, vendored libraries, or local deployment choices.
- Impact is only logging, observability, local misconfiguration, non-security correctness, harmless revert, stale read without consensus/security impact, gas optimization, fee estimate inaccuracy, or theoretical risk.
- No concrete Critical/High scoped impact or no realistic exploit path.

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
    Generate a short cross-project analog scan prompt for Ethermint.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

Main Focus should be on production Ethermint files from `scope_files`, especially:
- x/evm/keeper, x/evm/statedb, x/evm/types, x/evm/migrations
- x/feemarket/keeper and x/feemarket/types
- ante, evmd/ante, appmempool
- rpc, server JSON-RPC, indexer
- ethereum/eip712, crypto, encoding, types, proto sources
Issues outside those production files are out of scope unless required as direct supporting context.

## Access Rules (Strict)
- Treat production Ethermint files in the provided scope as accessible context.
- Do not claim missing/inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, generated pb.go, build files, IDE files, configs, resources, local fixtures, vendored libraries, package metadata, or e2e assets as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid Critical/High issues in Ethermint.
Only report an analog if this codebase has its own reachable root cause, triggered by an unprivileged tx/RPC/contract/mempool input, and the impact matches one allowed Ethermint impact below.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unauthorized theft, mint, burn bypass, or balance transfer of EVM-denom or Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic.
- Critical. Valid unprivileged transaction, RPC submission, or block-processing path can halt the chain, corrupt committed state, or cause deterministic validator consensus failure.
- High. Ethereum transaction, EIP-155/EIP-712/EIP-7702 authorization, nonce, chain-id, or signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation.
- High. EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted.
- High. Public JSON-RPC, gRPC, simulation, tracing, receipt/log, or indexer path feeds incorrect consensus-critical data into transaction execution or exposes a reachable route to the impacts above.

## Method
1. Classify vuln type: auth/replay bypass, fee/gas/accounting bug, stateDB commit/revert bug, consensus nondeterminism, invalid tx admission, RPC-to-consensus confusion, migration/genesis corruption, or parser/encoding issue.
2. Map to Ethermint components and exact production files.
3. Prove root cause with exact file/function/module/line references.
4. Confirm concrete scoped impact and realistic likelihood.
5. Explain the attacker-controlled entry path and why Ethermint code is the necessary vulnerable step.
6. Reject if the impact does not match one allowed Critical/High impact above.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires malicious validators, governance, privileged roles, leaked keys, compromised nodes, dependency compromise, Sybil/51% attack, phishing, chain reorgs, or network-level DoS only.
- External dependency behavior is the only cause.
- Test/docs/config/build/generated-only issue.
- Theoretical-only issue with no protocol impact.
- Impact is only local misconfiguration, observability/logging noise, harmless revert, stale read, fee estimate drift, or non-security correctness.
- Impact or likelihood missing.

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
    Generate a strict Ethermint bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

Main Focus should be on production Ethermint files from `scope_files`, especially:
- x/evm/keeper, x/evm/statedb, x/evm/types, x/evm/migrations
- x/feemarket/keeper and x/feemarket/types
- ante, evmd/ante, appmempool
- rpc, server JSON-RPC, indexer
- ethereum/eip712, crypto, encoding, types, proto sources
Issues outside those production files are out of scope unless required as direct supporting context.

## Rules
- Validate only the submitted claim.
- Check SECURITY.md, Researcher.md if present, and the Evmos/Ethermint bounty context for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-validator-only, governance-only, privileged-role-only, leaked-key, host-compromise, dependency-compromise, best-practice, docs/style, config/test-only, generated-code-only, gas-optimization-only, front-run-only, network-level-DoS-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, phishing/social engineering, third-party dapp/oracle compromise, public-mainnet DoS testing, chain reorgs, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged user through public tx/RPC/gRPC/contract/mempool/simulation inputs, unless the claim proves privilege escalation from an unprivileged path.
- The final impact must match an allowed Critical/High Ethermint impact, not just a generic code bug.
- Prefer #NoVulnerability over speculative reports.

## In-Scope Protocol Areas
The claim must affect production in-scope Ethermint code or systems, such as:
- EVM execution: ApplyTransaction, ApplyMessage, stateDB account/code/storage/balance changes, snapshots/reverts, native actions, selfdestruct, logs, receipts, bloom, hooks.
- Authorization: Ethereum tx decoding, EIP-155 chain-id, nonce checks, ethsecp256k1 signatures, EIP-712 Cosmos signing, authz, and EIP-7702 set-code authorization.
- Fees and admission: ante handlers, min gas price, native fee checks, app mempool/preverify, proposal handling, gas refunds, EIP-1559 base fee and feemarket state.
- Public API paths: JSON-RPC, gRPC queries, SendRawTransaction, eth_call, estimateGas, simulate/tracing, block/receipt/log/indexer reconstruction when they feed or prove consensus-impacting behavior.
- State lifecycle: genesis, migrations, params, chain config, account encoding, proto schemas, and deterministic block BeginBlock/EndBlock behavior.

Reject third-party dapps, unlisted public websites, tests, docs, examples, mocks, generated pb.go files, local deployment helpers, vendored libraries, e2e tooling, and issues that only affect local developer tooling unless the claim proves a direct in-scope Critical/High Ethermint security impact.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unauthorized theft, mint, burn bypass, or balance transfer of EVM-denom or Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic.
- Critical. Valid unprivileged transaction, RPC submission, or block-processing path can halt the chain, corrupt committed state, or cause deterministic validator consensus failure.
- High. Ethereum transaction, EIP-155/EIP-712/EIP-7702 authorization, nonce, chain-id, or signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation.
- High. EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted.
- High. Public JSON-RPC, gRPC, simulation, tracing, receipt/log, or indexer path feeds incorrect consensus-critical data into transaction execution or exposes a reachable route to the impacts above.

Informational, Low, Medium, non-security correctness, observability/logging-only, harmless reject/revert, stale read without consensus/state/accounting/security impact, local misconfiguration, and non-demonstrably-exploitable reports are invalid for this validation output.

If the submitted claim does not concretely prove one allowed Critical/High Ethermint impact above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken protocol/security/accounting/authentication/consensus assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete impact that exactly matches one allowed Critical/High impact above, with realistic likelihood.
6. Reproducible safe proof path: runnable PoC, deterministic integration test, invariant/fuzz test, differential test, or exact local manual steps.
7. No obvious rejection reason from SECURITY.md, Researcher.md if present, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user trigger this through a public Ethermint protocol path?
- Does the code actually behave as claimed?
- Is the impact caused by Ethermint production code, not by an external dependency alone?
- Is the fund loss, replay, invalid commit, state corruption, or consensus impact concrete?
- Does the claim avoid malicious validators, governance, privileged roles, leaked keys, node compromise, mainnet DoS, and third-party compromise assumptions?
- Would a bounty triager accept the proof?
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
[Concrete allowed Ethermint impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/fork test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
