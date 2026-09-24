## Analysis: Sensitive-Data Disclosure Analog in Protobuf's ProtoJSON Parser

### Title
Sensitive field values embedded verbatim in `ParseError`/`EnumStringValueParseError` messages during ProtoJSON parsing - (File: `python/google/protobuf/json_format.py`)

### Summary
The Infinispan advisory's failed invariant is: *a sensitive value that is legitimately processed by the application ends up embedded verbatim in an exception/error message, which is a plaintext-disclosure surface distinct from the value's intended use.* The Python ProtoJSON parser (`json_format.Parse`/`ParseDict`, a supported public parsing API) has the same failed invariant: when a JSON-supplied field value fails a type/shape/enum check, the raw un-redacted value is interpolated directly into the raised `ParseError`/`EnumStringValueParseError` message text, with no length cap or redaction.

### Finding Description
`json_format.py`'s `_ConvertFieldValuePair` builds messages like `'repeated field {0} must be in [] which is {1} at {2}'.format(name, value, path)` directly from the attacker-supplied JSON `value` [1](#0-0) . Similarly, `_ConvertValueMessage` raises `'Value {0} has unexpected type {1} at {2}'.format(value, type(value), path)` [2](#0-1) , and `_ConvertScalarFieldValue` raises `'Invalid enum value {0} for enum type {1}'.format(value, field.enum_type.full_name)` when an enum string/number does not match [3](#0-2) .

These raw, un-truncated values are then further wrapped and re-raised by the field-pair handler, which explicitly re-embeds the caught exception text into a new `ParseError`: `raise ParseError('Failed to parse {0} field: {1}.'.format(name, e)) from e` for `ValueError`/`TypeError`/inner `ParseError` [4](#0-3) . No part of this chain redacts, truncates, or otherwise sanitizes the field's content before it is placed into the exception message.

The consuming-application exposure assumption: server-side or CLI code commonly does `try: json_format.Parse(body, msg) except ParseError as e: log.error(str(e))` or returns `str(e)` in an HTTP 400 response for debuggability — this is the same class of "error path that unintentionally echoes a legitimate value" that the Infinispan CVE describes (a decoded secret ending up in a command-not-found error string). Any JSON field carrying sensitive data (API keys, tokens, passwords, PII) that happens to be malformed relative to the schema (wrong JSON shape for a repeated/map field, wrong type for a `Value`/`Struct` field, or an unrecognized enum string) will have its raw content mirrored into the exception text and, transitively, into logs or error responses.

### Impact Explanation
This matches CWE-209 exactly: a value not intended for display (a client-submitted credential/secret misrepresented in the request JSON) is placed into an error string that downstream code often persists to logs or returns to callers for debugging. Impact is confidentiality-only (matches the CVE's `C:H/I:N/A:N` profile) — no corruption or availability impact, just disclosure of the value that failed validation. Since the field's own descriptor/type is not consulted for sensitivity, this is systemic across every scalar/enum/repeated/map field validated in `_ConvertFieldValuePair`, `_ConvertScalarFieldValue`, and `_ConvertValueMessage`.

### Likelihood Explanation
Likelihood is Medium: it requires (1) an application to feed externally-influenced JSON (attacker or legitimate client input) into `json_format.Parse`/`ParseDict`, (2) the erroring field to carry sensitive content, and (3) the caller to log or surface the exception's `str()`. All three preconditions are common in typical microservice/API gateway deployments that use ProtoJSON as their wire format and log parse failures for diagnostics — a very standard pattern, unlike more contrived RCE-style analogs.

### Recommendation
Truncate/redact interpolated `value` in all `ParseError`/`EnumStringValueParseError`/`ValueError` messages raised from `_ConvertFieldValuePair`, `_ConvertScalarFieldValue`, and `_ConvertValueMessage` in `json_format.py` (e.g., cap length similar to the `%.1024r` guard already used in `type_checkers.py`, or omit the raw value entirely and only report the field path/type mismatch). Document that `str(ParseError)` may contain untrusted/sensitive input and should not be logged or surfaced without sanitization by callers.

### Proof of Concept
```python
from google.protobuf import json_format, struct_pb2

msg = struct_pb2.Struct()
# Attacker/legitimate client submits a nested value with a "password"-carrying
# blob in the wrong JSON shape (e.g. int instead of the expected string/list/dict).
text = '{"fields": {"password": {"my_secret_value": 12345}}}'
try:
    json_format.Parse(text, msg)
except json_format.ParseError as e:
    print(str(e))  # message embeds the raw offending value verbatim
```
Tracing this through `_ConvertValueMessage` shows the raised message is built as `'Value {0} has unexpected type {1} at {2}'.format(value, type(value), path)` [2](#0-1) , i.e. `value` (which could be or contain sensitive submitted data) is placed into the exception text with no redaction, matching the same class of unintended plaintext disclosure described in the Infinispan CVE.

### Citations

**File:** python/google/protobuf/json_format.py (L708-713)
```python
          if not isinstance(value, _LIST_LIKE):
            raise ParseError(
                'repeated field {0} must be in [] which is {1} at {2}'.format(
                    name, value, path
                )
            )
```

**File:** python/google/protobuf/json_format.py (L754-768)
```python
      except ParseError as e:
        if field and field.containing_oneof is None:
          raise ParseError(
              'Failed to parse {0} field: {1}.'.format(name, e)
          ) from e
        else:
          raise ParseError(str(e)) from e
      except ValueError as e:
        raise ParseError(
            'Failed to parse {0} field: {1}.'.format(name, e)
        ) from e
      except TypeError as e:
        raise ParseError(
            'Failed to parse {0} field: {1}.'.format(name, e)
        ) from e
```

**File:** python/google/protobuf/json_format.py (L828-833)
```python
    else:
      raise ParseError(
          'Value {0} has unexpected type {1} at {2}'.format(
              value, type(value), path
          )
      )
```

**File:** python/google/protobuf/json_format.py (L1088-1106)
```python
      if enum_value is None:
        try:
          number = int(value)
          enum_value = field.enum_type.values_by_number.get(number, None)
        except ValueError as e:
          # Since parsing to integer failed and lookup in values_by_name didn't
          # find this name, we have an enum string value which is unknown.
          raise EnumStringValueParseError(
              'Invalid enum value {0} for enum type {1}'.format(
                  value, field.enum_type.full_name
              )
          ) from e
        if enum_value is None:
          if field.enum_type.is_closed:
            raise ParseError(
                'Invalid enum value {0} for enum type {1}'.format(
                    value, field.enum_type.full_name
                )
            )
```
